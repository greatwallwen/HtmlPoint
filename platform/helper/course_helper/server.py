"""Fixed-loopback command-line entry for the Course Studio helper."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from course_helper.api import HelperRuntime, create_app
from course_helper.catalog import KnowledgeCatalog
from course_helper.jobs import BoundedJobRunner, WorkerRuntimeConfig
from course_helper.projection_bundle import PublishedProjectionBundleResolver
from course_helper.projection_events import ProjectionEvidenceStore
from course_helper.projection_host import (
    HostExecutablePolicy,
    InstalledHostTransportFactory,
    ProjectionHostSupervisor,
)
from course_helper.session import BrowserLaunchError, LaunchSession
from course_helper.static_web import validate_web_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="course-helper")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--app-data", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--web-origin", required=True)
    parser.add_argument("--web-root", type=Path)
    parser.add_argument("--port", type=_port, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    projection_supervisor: ProjectionHostSupervisor | None = None
    try:
        web_origin = _exact_loopback_origin(args.web_origin)
        helper_origin = f"http://127.0.0.1:{args.port}"
        web_root = None
        if args.web_root is not None:
            if web_origin != helper_origin:
                raise ValueError("product web origin must match helper origin")
            web_root = validate_web_root(args.web_root)
        reference_root = args.reference_root.resolve(strict=True)
        if not reference_root.is_dir():
            raise ValueError("reference root must be a directory")
        app_data = args.app_data.resolve()
        app_data.mkdir(parents=True, exist_ok=True)
        database = args.database.resolve()
        with KnowledgeCatalog.open(database):
            pass
        projection_policy = _projection_policy_from_app_data(app_data)
        if projection_policy is not None:
            projection_supervisor = _create_projection_supervisor(
                policy=projection_policy,
                database_path=database,
                app_data=app_data,
            )
        config = WorkerRuntimeConfig(
            database_path=str(database),
            app_data_path=str(app_data),
            source_roots=(("reference", str(reference_root)),),
        )
        launch_session = LaunchSession.create(allowed_origin=web_origin)
        runtime = HelperRuntime(
            config=config,
            launch_session=launch_session,
            job_runner=BoundedJobRunner(config),
            projection_supervisor=projection_supervisor,
            web_root=web_root,
        )
        app = create_app(runtime)
        launch_session.open_browser(
            web_application_url=web_origin,
            helper_base_url=helper_origin,
            opener=webbrowser.open,
        )
    except BrowserLaunchError:
        _shutdown_projection(projection_supervisor)
        print("course-helper: browser launch failed", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError):
        _shutdown_projection(projection_supervisor)
        print("course-helper: runtime configuration is invalid", file=sys.stderr)
        return 1

    server_failed = False
    shutdown_ok = True
    try:
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port)
        except Exception:
            print("course-helper: local server failed", file=sys.stderr)
            server_failed = True
    finally:
        shutdown_ok = _shutdown_projection(projection_supervisor)
    if not shutdown_ok:
        print("course-helper: projection shutdown failed", file=sys.stderr)
    return 0 if not server_failed and shutdown_ok else 1


def _projection_policy_from_app_data(app_data: Path) -> HostExecutablePolicy | None:
    policy_path = app_data / "projection-host-policy.json"
    executable = app_data / "projection-host" / "CourseStudio.ProjectionHost.exe"
    policy_exists = policy_path.exists()
    executable_exists = executable.exists()
    if not policy_exists and not executable_exists:
        return None
    if policy_exists != executable_exists:
        raise ValueError("projection install is incomplete")
    try:
        value = json.loads(
            _read_projection_policy_bytes(policy_path).decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("projection policy is invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "hostSha256"}
        or value.get("schemaVersion") != 1
        or not isinstance(value.get("hostSha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["hostSha256"]) is None
    ):
        raise ValueError("projection policy is invalid")
    policy = HostExecutablePolicy(app_data, value["hostSha256"])
    policy.resolve()
    return policy


def _read_projection_policy_bytes(policy_path: Path) -> bytes:
    descriptor = -1
    raw_handle: int | None = None
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            handle = kernel32.CreateFileW(
                str(policy_path),
                0x80000000,
                0x00000001,
                None,
                3,
                0x08000000 | 0x00200000,
                None,
            )
            invalid = ctypes.c_void_p(-1).value
            if not handle or int(handle) == invalid:
                raise OSError("projection policy cannot be opened")
            raw_handle = int(handle)
            descriptor = msvcrt.open_osfhandle(
                raw_handle,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            raw_handle = None
        else:
            descriptor = os.open(
                policy_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino, opened.st_size)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > 4096
            or bool(getattr(opened, "st_file_attributes", 0) & 0x400)
        ):
            raise ValueError("projection policy is invalid")
        requested = Path(os.path.abspath(policy_path))
        resolved = requested.resolve(strict=True)
        current = resolved.stat(follow_symlinks=False)
        if (
            os.path.normcase(str(requested)) != os.path.normcase(str(resolved))
            or resolved.is_symlink()
            or _is_reparse(resolved)
            or (current.st_dev, current.st_ino, current.st_size) != identity
        ):
            raise ValueError("projection policy is invalid")
        payload = os.read(descriptor, 4097)
        if len(payload) != opened.st_size or os.read(descriptor, 1):
            raise ValueError("projection policy is invalid")
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size) != identity:
            raise ValueError("projection policy is invalid")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        elif raw_handle is not None and os.name == "nt":
            import ctypes
            from ctypes import wintypes

            close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(raw_handle)


def _create_projection_supervisor(
    *,
    policy: HostExecutablePolicy,
    database_path: Path,
    app_data: Path,
) -> ProjectionHostSupervisor:
    return ProjectionHostSupervisor(
        transport_factory=InstalledHostTransportFactory(policy),
        bundle_resolver=PublishedProjectionBundleResolver(
            database_path=database_path,
            artifact_root=app_data / "artifacts",
        ),
        evidence_store=ProjectionEvidenceStore(_safe_evidence_root(app_data)),
        command_timeout_seconds=120,
    )


def _shutdown_projection(supervisor: object | None) -> bool:
    if supervisor is None:
        return True
    try:
        supervisor.shutdown()
    except Exception:
        return False
    return True


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON property")
        value[key] = item
    return value


def _is_reparse(path: Path) -> bool:
    try:
        return bool(path.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except AttributeError:
        return False


def _safe_evidence_root(app_data: Path) -> Path:
    root = app_data.resolve(strict=True)
    current = root
    for name in ("evidence", "projection"):
        current = current / name
        current.mkdir(exist_ok=True)
        resolved = current.resolve(strict=True)
        if (
            not current.is_dir()
            or current.is_symlink()
            or _is_reparse(current)
            or str(current).casefold() != str(resolved).casefold()
        ):
            raise ValueError("projection evidence root is invalid")
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("projection evidence root is invalid") from error
        current = resolved
    return current


def _port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be from 1 to 65535")
    return parsed


def _exact_loopback_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path
        or parsed.port is None
    ):
        raise ValueError("web origin must be an exact loopback origin")
    return value
