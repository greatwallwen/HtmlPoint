"""Fixed-loopback command-line entry for the Course Studio helper."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from course_helper.api import HelperRuntime, create_app
from course_helper.catalog import KnowledgeCatalog
from course_helper.jobs import BoundedJobRunner, WorkerRuntimeConfig
from course_helper.session import BrowserLaunchError, LaunchSession


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="course-helper")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--app-data", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--web-origin", required=True)
    parser.add_argument("--port", type=_port, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        web_origin = _exact_loopback_origin(args.web_origin)
        reference_root = args.reference_root.resolve(strict=True)
        if not reference_root.is_dir():
            raise ValueError("reference root must be a directory")
        app_data = args.app_data.resolve()
        app_data.mkdir(parents=True, exist_ok=True)
        database = args.database.resolve()
        with KnowledgeCatalog.open(database):
            pass
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
        )
        app = create_app(runtime)
        launch_session.open_browser(
            web_application_url=web_origin,
            helper_base_url=f"http://127.0.0.1:{args.port}",
            opener=webbrowser.open,
        )
    except BrowserLaunchError:
        print("course-helper: browser launch failed", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError):
        print("course-helper: runtime configuration is invalid", file=sys.stderr)
        return 1

    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


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
