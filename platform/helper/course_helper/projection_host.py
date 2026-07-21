"""Contained native projection Host supervision and authenticated transport."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast
from uuid import UUID

from pydantic import ValidationError

from course_helper.domain.projection import ProjectionCommand, ProjectionReceipt
from course_helper.projection_events import ProjectionEvidenceStore


_HOST_RELATIVE_PATH = Path("projection-host") / "CourseStudio.ProjectionHost.exe"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ALLOWED_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
)
_MAX_ASSETS = 128
_MAX_ASSET_BYTES = 20 * 1024 * 1024
_MAX_BUNDLE_BYTES = 96 * 1024 * 1024
_DEFAULT_CHUNK_BYTES = 36 * 1024
_MAX_BOOTSTRAP_BYTES = 40 * 1024
_GENERAL_LINE_BYTES = 64 * 1024
_ASSET_LINE_BYTES = 72 * 1024
_HANDSHAKE_LINE_BYTES = 4096
_CREATE_SUSPENDED = 0x00000004


class ProjectionHostError(RuntimeError):
    """Stable redacted Helper-side projection failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransportProtocolError(ProjectionHostError):
    """Authenticated framing, ordering, or process-liveness failure."""


@dataclass(frozen=True)
class HostExecutablePolicy:
    install_root: Path
    expected_sha256: str

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.expected_sha256):
            raise ProjectionHostError("host_digest_invalid")

    def _resolve_paths(self) -> tuple[Path, Path]:
        requested_root = Path(os.path.abspath(self.install_root))
        try:
            root = requested_root.resolve(strict=True)
        except OSError as error:
            raise ProjectionHostError("host_install_root_invalid") from error
        if (
            not _same_path(requested_root, root)
            or not root.is_dir()
            or _is_reparse(root)
        ):
            raise ProjectionHostError("host_install_root_invalid")

        candidate = root / _HOST_RELATIVE_PATH
        try:
            executable = candidate.resolve(strict=True)
            executable.relative_to(root)
        except (OSError, ValueError) as error:
            raise ProjectionHostError("host_executable_invalid") from error
        if (
            not _same_path(candidate, executable)
            or not executable.is_file()
            or _is_reparse(executable)
            or executable.suffix.lower() != ".exe"
        ):
            raise ProjectionHostError("host_executable_invalid")
        current = executable.parent
        while current != root:
            if _is_reparse(current) or not current.is_dir():
                raise ProjectionHostError("host_executable_invalid")
            current = current.parent

        return root, executable

    def _verify_digest(self, executable: Path) -> None:
        digest = hashlib.sha256()
        try:
            with executable.open("rb") as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
        except OSError as error:
            raise ProjectionHostError("host_executable_invalid") from error
        if not hmac.compare_digest(digest.hexdigest(), self.expected_sha256):
            raise ProjectionHostError("host_digest_mismatch")

    def resolve(self) -> Path:
        _, executable = self._resolve_paths()
        self._verify_digest(executable)
        return executable

    def acquire(self) -> VerifiedHostExecutableLease:
        if os.name != "nt":
            raise ProjectionHostError("host_platform_unsupported")
        root, executable = self._resolve_paths()
        handles: list[int] = []
        try:
            handles.append(_lock_windows_path(root, directory=True))
            handles.append(_lock_windows_path(executable.parent, directory=True))
            handles.append(_lock_windows_path(executable, directory=False))
            locked_root, locked_executable = self._resolve_paths()
            if not _same_path(root, locked_root) or not _same_path(
                executable, locked_executable
            ):
                raise ProjectionHostError("host_executable_invalid")
            self._verify_digest(locked_executable)
            return VerifiedHostExecutableLease(locked_executable, tuple(handles))
        except Exception:
            for handle in reversed(handles):
                _close_windows_handle(handle)
            raise


class VerifiedHostExecutableLease:
    """Keep install directories and the verified image non-replaceable through launch."""

    def __init__(self, path: Path, handles: tuple[int, ...]) -> None:
        self.path = path
        self._handles = handles
        self._closed = False

    @property
    def is_active(self) -> bool:
        return not self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in reversed(self._handles):
            _close_windows_handle(handle)

    def __enter__(self) -> VerifiedHostExecutableLease:
        if self._closed:
            raise ProjectionHostError("host_executable_lease_closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True)
class HostLaunchSpec:
    argv: tuple[str, ...]
    cwd: Path
    shell: bool
    runtime_root: Path | None = None

    @classmethod
    def for_executable(
        cls,
        executable: Path,
        *,
        runtime_root: Path | None = None,
    ) -> HostLaunchSpec:
        resolved = Path(executable).resolve(strict=True)
        if not resolved.is_file() or resolved.suffix.lower() != ".exe":
            raise ProjectionHostError("host_executable_invalid")
        resolved_runtime: Path | None = None
        if runtime_root is not None:
            requested_runtime = Path(os.path.abspath(runtime_root))
            try:
                resolved_runtime = requested_runtime.resolve(strict=True)
            except OSError as error:
                raise ProjectionHostError("host_runtime_root_invalid") from error
            if (
                not _same_path(requested_runtime, resolved_runtime)
                or not resolved_runtime.is_dir()
                or _is_reparse(resolved_runtime)
            ):
                raise ProjectionHostError("host_runtime_root_invalid")
        return cls(
            argv=(str(resolved),),
            cwd=resolved.parent,
            shell=False,
            runtime_root=resolved_runtime,
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "argumentCount": len(self.argv),
            "shell": self.shell,
            "workingDirectory": "contained-install-root",
        }


class AuthenticatedEnvelopeCodec:
    """Strict LF-JSON envelope with per-direction sequence and HMAC."""

    def __init__(
        self,
        key: bytes,
        *,
        send_direction: str,
        max_line_bytes: int = _ASSET_LINE_BYTES,
    ) -> None:
        if len(key) != 32:
            raise ValueError("transport key must be exactly 32 bytes")
        if send_direction not in {"helper", "host"}:
            raise ValueError("transport direction is invalid")
        if max_line_bytes < 256:
            raise ValueError("transport line ceiling is too small")
        self._key = bytearray(key)
        self._send_direction = send_direction
        self._max_line_bytes = max_line_bytes
        self._send_sequence = 0
        self._receive_sequence = 0

    def encode(self, payload: Mapping[str, Any]) -> bytes:
        body = _canonical_json(dict(payload))
        direction = self._send_direction
        sequence = self._send_sequence
        envelope = {
            "schemaVersion": 1,
            "direction": direction,
            "sequence": sequence,
            "body": _base64url(body),
            "mac": _base64url(_frame_mac(self._key, direction, sequence, body)),
        }
        encoded = _canonical_json(envelope) + b"\n"
        ceiling = (
            self._max_line_bytes
            if payload.get("type") == "asset_chunk"
            else min(self._max_line_bytes, _GENERAL_LINE_BYTES)
        )
        if len(encoded) > ceiling:
            raise TransportProtocolError("transport_message_too_large")
        self._send_sequence += 1
        return encoded

    def decode(
        self,
        line: bytes,
        *,
        expected_direction: str,
    ) -> Mapping[str, Any]:
        if expected_direction not in {"helper", "host"}:
            raise ValueError("expected direction is invalid")
        if (
            not line.endswith(b"\n")
            or b"\n" in line[:-1]
            or b"\r" in line
            or len(line) > self._max_line_bytes
        ):
            raise TransportProtocolError("transport_message_too_large")
        try:
            envelope = _strict_json_object(line[:-1])
            if set(envelope) != {
                "schemaVersion",
                "direction",
                "sequence",
                "body",
                "mac",
            }:
                raise ValueError
            if envelope["schemaVersion"] != 1:
                raise ValueError
            direction = envelope["direction"]
            sequence = envelope["sequence"]
            if type(direction) is not str or type(sequence) is not int:
                raise ValueError
            if direction != expected_direction:
                raise TransportProtocolError("transport_direction_invalid")
            body = _decode_base64url(envelope["body"])
            supplied_mac = _decode_base64url(envelope["mac"])
            expected_mac = _frame_mac(self._key, direction, sequence, body)
            if not hmac.compare_digest(supplied_mac, expected_mac):
                raise TransportProtocolError("transport_authentication_failed")
            if sequence < self._receive_sequence:
                raise TransportProtocolError("transport_replay")
            if sequence != self._receive_sequence:
                raise TransportProtocolError("transport_sequence_invalid")
            payload = _strict_json_object(body)
            ceiling = (
                self._max_line_bytes
                if payload.get("type") == "asset_chunk"
                else min(self._max_line_bytes, _GENERAL_LINE_BYTES)
            )
            if len(line) > ceiling:
                raise TransportProtocolError("transport_message_too_large")
        except TransportProtocolError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise TransportProtocolError("transport_authentication_failed") from None
        self._receive_sequence += 1
        return payload

    def close(self) -> None:
        _zero(self._key)


@dataclass(frozen=True)
class ProjectionAssetSource:
    opaque_id: str
    media_type: str
    byte_size: int
    sha256: str
    open_verified: Callable[[], BinaryIO]


@dataclass(frozen=True)
class ProjectionSessionBundle:
    course_version_id: str
    runtime_manifest_digest: str
    navigation_identity: str
    bootstrap: Mapping[str, Any]
    assets: tuple[ProjectionAssetSource, ...]


class ProjectionHostTransport(Protocol):
    def send(self, payload: Mapping[str, Any]) -> None: ...

    def receive(self, *, timeout_seconds: float) -> Mapping[str, Any]: ...

    def close(self) -> None: ...

    @property
    def is_alive(self) -> bool: ...


class ProjectionHostSupervisor:
    """Serialize commands, verify receipts, and fail closed on Host loss."""

    def __init__(
        self,
        *,
        transport_factory: Callable[[], ProjectionHostTransport],
        bundle_resolver: Callable[[ProjectionCommand], ProjectionSessionBundle],
        evidence_store: ProjectionEvidenceStore,
        clock: Callable[[], Any] | None = None,
        command_timeout_seconds: float = 15.0,
        asset_chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> None:
        if not 1024 <= asset_chunk_bytes <= _DEFAULT_CHUNK_BYTES:
            raise ValueError("asset chunk ceiling is invalid")
        if not 0 < command_timeout_seconds <= 120:
            raise ValueError("command timeout is invalid")
        self._transport_factory = transport_factory
        self._bundle_resolver = bundle_resolver
        self._evidence_store = evidence_store
        self._clock = clock
        self._command_timeout_seconds = command_timeout_seconds
        self._asset_chunk_bytes = asset_chunk_bytes
        self._lock = threading.RLock()
        self._transport_lock = threading.Lock()
        self._transport: ProjectionHostTransport | None = None
        self._receipts: dict[UUID, tuple[str, ProjectionReceipt]] = {}
        self._session_digests: dict[UUID, dict[str, str]] = {}
        self._evidence_sequence = 0
        self._closed = False

    def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="detect_displays")

    def open_session(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="open_projection_session")

    def assign_windows(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="assign_projection_window")

    def enter_fullscreen(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="enter_projection_fullscreen")

    def verify_assignment(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="verify_projection_assignment")

    def close_session(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._execute(command, expected="close_projection_session")

    def shutdown(self) -> None:
        self._closed = True
        self._close_transport()

    def cancel_current(self) -> None:
        """Interrupt an in-flight receive without permanently closing the supervisor."""

        self._close_transport()

    def _execute(self, command: ProjectionCommand, *, expected: str) -> ProjectionReceipt:
        if not isinstance(command, ProjectionCommand) or command.command != expected:
            raise ProjectionHostError("unexpected_command")
        payload = command.model_dump(mode="json", by_alias=True)
        command_bytes = _canonical_json(payload)
        if len(command_bytes) > _MAX_BOOTSTRAP_BYTES:
            raise ProjectionHostError("command_size_exceeded")
        request_digest = hashlib.sha256(command_bytes).hexdigest()
        with self._lock:
            if self._closed:
                raise ProjectionHostError("supervisor_closed")
            previous = self._receipts.get(command.command_id)
            if previous is not None:
                previous_digest, receipt = previous
                if not hmac.compare_digest(previous_digest, request_digest):
                    raise ProjectionHostError("command_id_collision")
                if (
                    command.command != "close_projection_session"
                    and not self._has_live_transport()
                ):
                    self._receipts.pop(command.command_id, None)
                    self._record_failure(command, "host_not_running")
                    raise ProjectionHostError("host_not_running")
                return receipt

            try:
                transport = self._ensure_transport()
                bundle: ProjectionSessionBundle | None = None
                if command.command == "open_projection_session":
                    if command.session_id is None:
                        raise ProjectionHostError("session_identity_missing")
                    bundle = self._bundle_resolver(command)
                    self._validate_bundle(bundle)
                transport.send({"type": "projection_command", "command": payload})
                if bundle is not None:
                    self._expect_asset_ready(transport.receive(
                        timeout_seconds=self._command_timeout_seconds
                    ), command)
                    bundle_digest = self._transfer_bundle(transport, bundle)
                    transport.send(
                        {
                            "type": "projection_bootstrap",
                            "command": payload,
                            "courseVersionId": bundle.course_version_id,
                            "runtimeManifestDigest": bundle.runtime_manifest_digest,
                            "navigationIdentity": bundle.navigation_identity,
                            "bootstrap": dict(bundle.bootstrap),
                        }
                    )
                    self._session_digests[command.session_id] = {
                        "bundle": bundle_digest,
                        "bootstrap": hashlib.sha256(
                            _canonical_json(dict(bundle.bootstrap))
                        ).hexdigest(),
                        "manifest": bundle.runtime_manifest_digest,
                        "navigation": bundle.navigation_identity,
                    }
                    self._record(
                        "asset_bundle_verified",
                        command,
                        status="syncing",
                        code="asset_bundle_verified",
                        digests=self._session_digests[command.session_id],
                    )
                raw_receipt = transport.receive(
                    timeout_seconds=self._command_timeout_seconds
                )
                receipt = self._parse_receipt(raw_receipt, command)
                self._receipts[command.command_id] = (request_digest, receipt)
                if receipt.accepted:
                    self._record_success(command, receipt)
                else:
                    self._record_rejection(command, receipt)
                if command.command == "close_projection_session":
                    self._close_transport()
                return receipt
            except ProjectionHostError as error:
                self._close_transport()
                self._record_failure(command, error.code)
                if command.session_id is not None:
                    self._session_digests.pop(command.session_id, None)
                raise
            except (OSError, RuntimeError, ValidationError, ValueError) as error:
                self._close_transport()
                self._record_failure(command, "host_protocol_invalid")
                if command.session_id is not None:
                    self._session_digests.pop(command.session_id, None)
                raise ProjectionHostError("host_protocol_invalid") from error

    def _ensure_transport(self) -> ProjectionHostTransport:
        with self._transport_lock:
            if self._transport is None:
                self._transport = self._transport_factory()
            if not self._transport.is_alive:
                raise ProjectionHostError("host_not_running")
            return self._transport

    def _expect_asset_ready(
        self,
        message: Mapping[str, Any],
        command: ProjectionCommand,
    ) -> None:
        if set(message) != {"type", "commandId"} or message != {
            "type": "asset_ready",
            "commandId": str(command.command_id),
        }:
            raise ProjectionHostError("asset_ready_invalid")

    def _parse_receipt(
        self,
        message: Mapping[str, Any],
        command: ProjectionCommand,
    ) -> ProjectionReceipt:
        if set(message) != {"type", "receipt"} or message.get("type") != "projection_receipt":
            raise ProjectionHostError("receipt_invalid")
        try:
            receipt = ProjectionReceipt.model_validate(message["receipt"])
        except (KeyError, ValidationError) as error:
            raise ProjectionHostError("receipt_invalid") from error
        if (
            receipt.command_id != command.command_id
            or receipt.session_id != command.session_id
            or receipt.command != command.command
        ):
            raise ProjectionHostError("receipt_identity_mismatch")
        if receipt.accepted:
            expected_generation = command.expected_generation + (
                1 if command.command == "assign_projection_window" else 0
            )
            if receipt.generation != expected_generation:
                raise ProjectionHostError("receipt_identity_mismatch")
        return receipt

    def _validate_bundle(self, bundle: ProjectionSessionBundle) -> None:
        if not isinstance(bundle, ProjectionSessionBundle):
            raise ProjectionHostError("bundle_invalid")
        if (
            not _OPAQUE_ID.fullmatch(bundle.course_version_id)
            or not _DIGEST.fullmatch(bundle.runtime_manifest_digest)
            or not _DIGEST.fullmatch(bundle.navigation_identity)
            or not isinstance(bundle.bootstrap, Mapping)
        ):
            raise ProjectionHostError("bundle_invalid")
        try:
            bootstrap_bytes = _canonical_json(dict(bundle.bootstrap))
        except (TypeError, ValueError) as error:
            raise ProjectionHostError("bundle_invalid") from error
        if len(bootstrap_bytes) > _MAX_BOOTSTRAP_BYTES:
            raise ProjectionHostError("bootstrap_size_exceeded")
        if len(bundle.assets) > _MAX_ASSETS:
            raise ProjectionHostError("asset_count_exceeded")
        total = 0
        identities: set[str] = set()
        for asset in bundle.assets:
            if (
                not _OPAQUE_ID.fullmatch(asset.opaque_id)
                or asset.opaque_id in identities
                or asset.media_type not in _ALLOWED_MEDIA_TYPES
                or type(asset.byte_size) is not int
                or asset.byte_size < 1
                or not _DIGEST.fullmatch(asset.sha256)
                or not callable(asset.open_verified)
            ):
                raise ProjectionHostError("asset_metadata_invalid")
            if asset.byte_size > _MAX_ASSET_BYTES:
                raise ProjectionHostError("asset_size_exceeded")
            identities.add(asset.opaque_id)
            total += asset.byte_size
            if total > _MAX_BUNDLE_BYTES:
                raise ProjectionHostError("asset_bundle_size_exceeded")

    def _transfer_bundle(
        self,
        transport: ProjectionHostTransport,
        bundle: ProjectionSessionBundle,
    ) -> str:
        metadata: list[dict[str, Any]] = []
        for asset in bundle.assets:
            descriptor = {
                "assetId": asset.opaque_id,
                "mediaType": asset.media_type,
                "byteSize": asset.byte_size,
                "sha256": asset.sha256,
            }
            metadata.append(descriptor)
            transport.send({"type": "asset_begin", **descriptor})
            digest = hashlib.sha256()
            offset = 0
            try:
                source = asset.open_verified()
            except (OSError, RuntimeError, ValueError) as error:
                raise ProjectionHostError("asset_open_failed") from error
            with closing(source):
                while block := source.read(self._asset_chunk_bytes):
                    if not isinstance(block, bytes):
                        raise ProjectionHostError("asset_stream_invalid")
                    if len(block) > self._asset_chunk_bytes:
                        raise ProjectionHostError("asset_stream_invalid")
                    offset += len(block)
                    if offset > asset.byte_size:
                        raise ProjectionHostError("asset_size_mismatch")
                    digest.update(block)
                    transport.send(
                        {
                            "type": "asset_chunk",
                            "assetId": asset.opaque_id,
                            "offset": offset - len(block),
                            "data": _base64url(block),
                        }
                    )
            if offset != asset.byte_size:
                raise ProjectionHostError("asset_size_mismatch")
            if not hmac.compare_digest(digest.hexdigest(), asset.sha256):
                raise ProjectionHostError("asset_digest_mismatch")
            transport.send(
                {
                    "type": "asset_commit",
                    "assetId": asset.opaque_id,
                    "byteSize": offset,
                    "sha256": digest.hexdigest(),
                }
            )
        return hashlib.sha256(_canonical_json(metadata)).hexdigest()

    def _record_success(
        self,
        command: ProjectionCommand,
        receipt: ProjectionReceipt,
    ) -> None:
        mapping = {
            "open_projection_session": ("session_opened", "session_opened"),
            "verify_projection_assignment": (
                "assignment_verified",
                "assignment_verified",
            ),
            "close_projection_session": ("session_closed", "session_closed"),
        }
        selected = mapping.get(command.command)
        if selected is None or command.session_id is None:
            return
        event_type, code = selected
        digests = self._session_digests.get(command.session_id, {})
        self._record(
            event_type,
            command,
            status=receipt.status,
            code=code,
            digests=digests,
        )
        if command.command == "close_projection_session":
            self._record(
                "final_summary",
                command,
                status="closed",
                code="session_closed",
                digests=digests,
            )
            self._session_digests.pop(command.session_id, None)

    def _record_failure(self, command: ProjectionCommand, code: str) -> None:
        if command.session_id is None:
            return
        try:
            digests = self._session_digests.get(command.session_id, {})
            self._record(
                "session_invalidated",
                command,
                status="invalidated",
                code="session_invalidated",
                digests=digests,
            )
            self._record(
                "host_failure",
                command,
                status="invalidated",
                code=_safe_code(code),
                digests=digests,
            )
        except (OSError, RuntimeError, ValueError):
            return

    def _record_rejection(
        self,
        command: ProjectionCommand,
        receipt: ProjectionReceipt,
    ) -> None:
        if command.session_id is None:
            return
        digests = self._session_digests.get(command.session_id, {})
        self._record(
            "session_invalidated",
            command,
            status="invalidated",
            code="session_invalidated",
            digests=digests,
        )
        if command.command == "open_projection_session":
            self._session_digests.pop(command.session_id, None)

    def _record(
        self,
        event_type: str,
        command: ProjectionCommand,
        *,
        status: str,
        code: str,
        digests: Mapping[str, str],
    ) -> None:
        if command.session_id is None:
            return
        self._evidence_sequence += 1
        self._evidence_store.record(
            event_type=event_type,
            session_id=str(command.session_id),
            command_id=str(command.command_id),
            generation=command.expected_generation,
            sequence=self._evidence_sequence,
            status=status,
            code=code,
            digests=digests,
        )

    def _close_transport(self) -> None:
        with self._transport_lock:
            transport = self._transport
            self._transport = None
        if transport is not None:
            transport.close()

    def _has_live_transport(self) -> bool:
        with self._transport_lock:
            return self._transport is not None and self._transport.is_alive


class InstalledHostTransportFactory:
    """Resolve the fixed install policy at every launch and start one child."""

    def __init__(
        self,
        policy: HostExecutablePolicy,
        *,
        runtime_root: Path | None = None,
    ) -> None:
        self._policy = policy
        self._runtime_root = runtime_root

    def __call__(self) -> ProjectionHostTransport:
        with self._policy.acquire() as verified:
            return AuthenticatedSubprocessTransport.launch(
                HostLaunchSpec.for_executable(
                    verified.path,
                    runtime_root=self._runtime_root,
                ),
                verified_executable=verified,
            )


class AuthenticatedSubprocessTransport:
    """Windows child transport with an inherited bootstrap pipe and Job Object."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        job: _WindowsJobObject,
        session_key: bytearray,
        runtime_directory: Path,
    ) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ProjectionHostError("host_pipe_invalid")
        self._process = process
        self._job = job
        self._session_key = session_key
        self._runtime_directory = runtime_directory
        self._encoder = AuthenticatedEnvelopeCodec(
            bytes(session_key), send_direction="helper"
        )
        self._decoder = AuthenticatedEnvelopeCodec(
            bytes(session_key), send_direction="helper"
        )
        self._inbound: queue.Queue[Mapping[str, Any] | BaseException | None] = queue.Queue(
            maxsize=256
        )
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self._stderr_tail = bytearray()
        self._reader = threading.Thread(
            target=self._read_stdout,
            name="projection-host-stdout",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr,
            name="projection-host-stderr",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()

    @classmethod
    def launch(
        cls,
        spec: HostLaunchSpec,
        *,
        verified_executable: VerifiedHostExecutableLease | None = None,
    ) -> AuthenticatedSubprocessTransport:
        if os.name != "nt":
            raise ProjectionHostError("host_platform_unsupported")
        if spec.shell or len(spec.argv) != 1:
            raise ProjectionHostError("host_launch_spec_invalid")
        if verified_executable is not None and (
            not verified_executable.is_active
            or not _same_path(Path(spec.argv[0]), verified_executable.path)
        ):
            raise ProjectionHostError("host_executable_lease_invalid")
        import msvcrt

        bootstrap_read_fd, bootstrap_write_fd = os.pipe()
        os.set_inheritable(bootstrap_read_fd, True)
        bootstrap_handle = msvcrt.get_osfhandle(bootstrap_read_fd)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [bootstrap_handle]}
        runtime_parent = Path(tempfile.gettempdir()) / "CourseStudio.ProjectionHost"
        runtime_parent.mkdir(parents=True, exist_ok=True)
        projection_run_root = Path(
            tempfile.mkdtemp(
                prefix=f"run-{secrets.token_hex(16)}-",
                dir=runtime_parent,
            )
        )
        environment = _minimal_host_environment(
            bootstrap_handle,
            spec.runtime_root,
            projection_run_root,
        )
        process: subprocess.Popen[bytes] | None = None
        job: _WindowsJobObject | None = None
        launch_key = bytearray(secrets.token_bytes(32))
        try:
            job = _WindowsJobObject.create()
            process = subprocess.Popen(
                list(spec.argv),
                cwd=str(spec.cwd),
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                close_fds=True,
                startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW | _CREATE_SUSPENDED,
                env=environment,
            )
            os.close(bootstrap_read_fd)
            bootstrap_read_fd = -1
            job.assign(process)
            _resume_suspended_process(process)
            os.write(bootstrap_write_fd, launch_key)
            os.close(bootstrap_write_fd)
            bootstrap_write_fd = -1
            session_key = _perform_helper_handshake(
                process,
                launch_key,
                timeout_seconds=10.0,
            )
            return cls(process, job, session_key, projection_run_root)
        except Exception:
            _cleanup_failed_launch(process, job)
            _cleanup_projection_runtime(projection_run_root)
            raise
        finally:
            for descriptor in (bootstrap_read_fd, bootstrap_write_fd):
                if descriptor >= 0:
                    os.close(descriptor)
            _zero(launch_key)

    def send(self, payload: Mapping[str, Any]) -> None:
        if self._closed or self._process.stdin is None:
            raise TransportProtocolError("transport_closed")
        encoded = self._encoder.encode(payload)
        with self._write_lock:
            try:
                _write_all(self._process.stdin, encoded)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise TransportProtocolError("host_eof") from error

    def receive(self, *, timeout_seconds: float) -> Mapping[str, Any]:
        try:
            value = self._inbound.get(timeout=timeout_seconds)
        except queue.Empty as error:
            raise TransportProtocolError("host_timeout") from error
        if value is None:
            raise TransportProtocolError("host_eof")
        if isinstance(value, BaseException):
            if isinstance(value, ProjectionHostError):
                raise value
            raise TransportProtocolError("host_protocol_invalid") from value
        return value

    @property
    def is_alive(self) -> bool:
        return not self._closed and self._process.poll() is None

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._process.stdin is not None:
                try:
                    self._process.stdin.close()
                except OSError:
                    pass
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._job.close()
                self._process.wait(timeout=5)
            else:
                self._job.close()
            self._encoder.close()
            self._decoder.close()
            _zero(self._session_key)
            _cleanup_projection_runtime(self._runtime_directory)

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            while True:
                line = self._process.stdout.readline(_ASSET_LINE_BYTES + 1)
                if not line:
                    self._put_inbound(None)
                    return
                if len(line) > _ASSET_LINE_BYTES or not line.endswith(b"\n"):
                    raise TransportProtocolError("transport_message_too_large")
                self._put_inbound(
                    self._decoder.decode(line, expected_direction="host")
                )
        except BaseException as error:  # reader thread must hand failure to owner
            self._put_inbound(error)

    def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        while block := self._process.stderr.read(4096):
            self._stderr_tail.extend(block)
            if len(self._stderr_tail) > 16 * 1024:
                del self._stderr_tail[: len(self._stderr_tail) - 16 * 1024]

    def _put_inbound(self, value: Mapping[str, Any] | BaseException | None) -> None:
        try:
            self._inbound.put(value, timeout=1)
        except queue.Full:
            try:
                self._inbound.put_nowait(
                    TransportProtocolError("host_event_queue_exceeded")
                )
            except queue.Full:
                return


class _WindowsJobObject:
    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._closed = False

    @classmethod
    def create(cls) -> _WindowsJobObject:
        if os.name != "nt":
            raise ProjectionHostError("host_platform_unsupported")
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ProjectionHostError("job_object_create_failed")
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            kernel32.CloseHandle(handle)
            raise ProjectionHostError("job_object_policy_failed")
        return cls(cast(int, handle))

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        process_handle = cast(Any, process)._handle
        if not kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise ProjectionHostError("job_object_assign_failed")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle(self._handle)


def _resume_suspended_process(process: subprocess.Popen[bytes]) -> None:
    if os.name != "nt":
        raise ProjectionHostError("host_platform_unsupported")
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(cast(Any, process)._handle)
    if status < 0:
        raise ProjectionHostError("host_resume_failed")


def _cleanup_failed_launch(
    process: subprocess.Popen[bytes] | None,
    job: _WindowsJobObject | None,
) -> None:
    if job is not None:
        job.close()
    if process is None:
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _lock_windows_path(path: Path, *, directory: bool) -> int:
    import ctypes
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
    desired_access = 0x00000080 if directory else 0x80000000
    flags = 0x02000000 if directory else 0x08000000
    handle = kernel32.CreateFileW(
        str(path),
        desired_access,
        0x00000001,
        None,
        3,
        flags,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or cast(int, handle) == invalid:
        raise ProjectionHostError("host_executable_lock_failed")
    return cast(int, handle)


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def _perform_helper_handshake(
    process: subprocess.Popen[bytes],
    launch_key: bytearray,
    *,
    timeout_seconds: float = 10.0,
) -> bytearray:
    if process.stdin is None or process.stdout is None:
        raise ProjectionHostError("host_pipe_invalid")
    line_queue: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)

    def read_hello() -> None:
        try:
            line_queue.put(process.stdout.readline(_HANDSHAKE_LINE_BYTES + 1))
        except BaseException as error:
            line_queue.put(error)

    threading.Thread(
        target=read_hello,
        name="projection-host-handshake",
        daemon=True,
    ).start()
    try:
        hello_value = line_queue.get(timeout=timeout_seconds)
    except queue.Empty as error:
        raise ProjectionHostError("host_handshake_timeout") from error
    if isinstance(hello_value, BaseException):
        raise ProjectionHostError("host_handshake_invalid") from hello_value
    hello_line = hello_value
    if (
        not hello_line.endswith(b"\n")
        or b"\r" in hello_line
        or len(hello_line) > _HANDSHAKE_LINE_BYTES
    ):
        raise ProjectionHostError("host_handshake_invalid")
    try:
        hello = _strict_json_object(hello_line[:-1])
        if set(hello) != {"schemaVersion", "type", "hostNonce", "mac"}:
            raise ValueError
        if hello["schemaVersion"] != 1 or hello["type"] != "host_hello":
            raise ValueError
        host_nonce = _decode_base64url(hello["hostNonce"])
        supplied = _decode_base64url(hello["mac"])
        if len(host_nonce) != 32 or not hmac.compare_digest(
            supplied,
            hmac.digest(bytes(launch_key), b"host_hello\0" + host_nonce, "sha256"),
        ):
            raise ValueError
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectionHostError("host_handshake_invalid") from error

    helper_nonce = secrets.token_bytes(32)
    response = {
        "schemaVersion": 1,
        "type": "helper_hello",
        "helperNonce": _base64url(helper_nonce),
        "mac": _base64url(
            hmac.digest(
                bytes(launch_key),
                b"helper_hello\0" + host_nonce + helper_nonce,
                "sha256",
            )
        ),
    }
    encoded = _canonical_json(response) + b"\n"
    _write_all(process.stdin, encoded)
    process.stdin.flush()
    return bytearray(
        hmac.digest(
            bytes(launch_key),
            b"session\0" + host_nonce + helper_nonce,
            "sha256",
        )
    )


def _minimal_host_environment(
    bootstrap_handle: int,
    runtime_root: Path | None,
    projection_run_root: Path,
) -> dict[str, str]:
    environment = {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key)) is not None
    }
    environment.update(
        {
            "COURSE_PROJECTION_BOOTSTRAP_HANDLE": str(bootstrap_handle),
            "COURSE_PROJECTION_PROTOCOL": "1",
            "COURSE_PROJECTION_RUN_ROOT": str(projection_run_root),
        }
    )
    if runtime_root is not None:
        environment["DOTNET_ROOT"] = str(runtime_root)
    return environment


def _cleanup_projection_runtime(runtime_directory: Path) -> None:
    expected_parent = (
        Path(tempfile.gettempdir()) / "CourseStudio.ProjectionHost"
    ).resolve()
    requested = Path(os.path.abspath(runtime_directory))
    try:
        if requested.parent.resolve() != expected_parent:
            return
    except OSError:
        return
    for attempt in range(50):
        try:
            if not requested.exists():
                return
            if requested.is_symlink() or _is_reparse(requested):
                return
            shutil.rmtree(requested)
            return
        except OSError:
            if attempt == 49:
                return
            time.sleep(0.1)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json_object(payload: bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON property")
            result[key] = value
        return result

    value = json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=no_duplicates,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _frame_mac(
    key: bytes | bytearray,
    direction: str,
    sequence: int,
    body: bytes,
) -> bytes:
    prefix = (
        b"course-projection-v1\0"
        + direction.encode("ascii")
        + b"\0"
        + str(sequence).encode("ascii")
        + b"\0"
    )
    return hmac.digest(key, prefix + body, "sha256")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: object) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("base64url value is invalid")
    padding = "=" * ((4 - len(value) % 4) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _base64url(decoded) != value:
        raise ValueError("base64url value is not canonical")
    return decoded


def _safe_code(value: str) -> str:
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else "host_failure"


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first)) == os.path.normcase(str(second))


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except AttributeError:
        return False


def _zero(value: bytearray) -> None:
    value[:] = b"\0" * len(value)


def _write_all(stream: BinaryIO, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(payload):
        count = stream.write(view[written:])
        if count is None or count <= 0:
            raise BrokenPipeError("pipe write failed")
        written += count


__all__ = [
    "AuthenticatedEnvelopeCodec",
    "AuthenticatedSubprocessTransport",
    "HostExecutablePolicy",
    "HostLaunchSpec",
    "InstalledHostTransportFactory",
    "ProjectionAssetSource",
    "ProjectionHostError",
    "ProjectionHostSupervisor",
    "ProjectionSessionBundle",
    "TransportProtocolError",
]
