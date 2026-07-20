from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from course_helper.domain.projection import ProjectionCommand, ProjectionReceipt
from course_helper.projection_events import ProjectionEvidenceStore
from course_helper.projection_host import (
    AuthenticatedEnvelopeCodec,
    HostExecutablePolicy,
    HostLaunchSpec,
    InstalledHostTransportFactory,
    ProjectionAssetSource,
    ProjectionHostError,
    ProjectionHostSupervisor,
    ProjectionSessionBundle,
    TransportProtocolError,
)
from course_helper import projection_host as projection_host_module


NOW = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
AUTHENTICATED_FRAME_FIXTURE = (
    Path(__file__).parents[2]
    / "contracts"
    / "projection"
    / "v1"
    / "fixtures"
    / "authenticated-helper-frame.txt"
)


def _command(name: str, *, command_id: UUID | None = None) -> ProjectionCommand:
    session_id = None if name == "detect_displays" else uuid4()
    return ProjectionCommand.model_validate(
        {
            "schemaVersion": 1,
            "commandId": str(command_id or uuid4()),
            "command": name,
            "sessionId": None if session_id is None else str(session_id),
            "expectedGeneration": 0,
            "payload": {},
        }
    )


def _receipt(command: ProjectionCommand) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "commandId": str(command.command_id),
        "sessionId": (
            None if command.session_id is None else str(command.session_id)
        ),
        "command": command.command,
        "accepted": True,
        "status": "candidate" if command.command == "detect_displays" else "assigned",
        "generation": command.expected_generation
        + (1 if command.command == "assign_projection_window" else 0),
        "message": "ok",
        "assignments": [],
    }


class FakeTransport:
    def __init__(
        self,
        responder: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        self.responder = responder
        self.sent: list[Mapping[str, Any]] = []
        self.responses: list[Mapping[str, Any]] = []
        self.closed = False
        self.alive = True

    def send(self, payload: Mapping[str, Any]) -> None:
        if self.closed:
            raise TransportProtocolError("transport_closed")
        value = dict(payload)
        self.sent.append(value)
        response = self.responder(value)
        if response:
            self.responses.append(response)

    def receive(self, *, timeout_seconds: float) -> Mapping[str, Any]:
        del timeout_seconds
        if not self.alive:
            raise TransportProtocolError("host_eof")
        if not self.responses:
            raise TransportProtocolError("host_timeout")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True
        self.alive = False

    @property
    def is_alive(self) -> bool:
        return self.alive


def _transport_for_commands(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if payload.get("type") == "projection_command":
        raw = payload["command"]
        command = ProjectionCommand.model_validate(raw)
        if command.command == "open_projection_session":
            return {
                "type": "asset_ready",
                "commandId": str(command.command_id),
            }
        return {"type": "projection_receipt", "receipt": _receipt(command)}
    if payload.get("type") == "projection_bootstrap":
        raw_command = payload["command"]
        command = ProjectionCommand.model_validate(raw_command)
        return {"type": "projection_receipt", "receipt": _receipt(command)}
    return {}


def _bundle(asset_bytes: bytes = b"authentic-image") -> ProjectionSessionBundle:
    digest = hashlib.sha256(asset_bytes).hexdigest()
    asset = ProjectionAssetSource(
        opaque_id="artifact-1",
        media_type="image/png",
        byte_size=len(asset_bytes),
        sha256=digest,
        open_verified=lambda: io.BytesIO(asset_bytes),
    )
    return ProjectionSessionBundle(
        course_version_id="course-version-1",
        runtime_manifest_digest=DIGEST_A,
        navigation_identity=DIGEST_B,
        bootstrap={"schemaVersion": 1, "course": {"id": "course-1"}},
        assets=(asset,),
    )


def _declared_asset(identifier: int, byte_size: int) -> ProjectionAssetSource:
    return ProjectionAssetSource(
        opaque_id=f"artifact-{identifier}",
        media_type="image/png",
        byte_size=byte_size,
        sha256="0" * 64,
        open_verified=lambda: io.BytesIO(b""),
    )


def test_host_executable_policy_requires_exact_contained_regular_digest(
    tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    host_dir = install / "projection-host"
    host_dir.mkdir(parents=True)
    executable = host_dir / "CourseStudio.ProjectionHost.exe"
    executable.write_bytes(b"signed-build-fixture")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()

    resolved = HostExecutablePolicy(install, digest).resolve()

    assert resolved == executable.resolve(strict=True)
    executable.write_bytes(b"changed")
    with pytest.raises(ProjectionHostError, match="host_digest_mismatch"):
        HostExecutablePolicy(install, digest).resolve()
    with pytest.raises(ProjectionHostError, match="host_install_root_invalid"):
        HostExecutablePolicy(tmp_path / "missing", digest).resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows executable sharing contract")
def test_verified_host_lease_blocks_replacement_until_launch_boundary_closes(
    tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    host_dir = install / "projection-host"
    host_dir.mkdir(parents=True)
    executable = host_dir / "CourseStudio.ProjectionHost.exe"
    executable.write_bytes(b"verified-host")
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement-host")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()

    with HostExecutablePolicy(install, digest).acquire() as lease:
        assert lease.path == executable.resolve(strict=True)
        with pytest.raises(OSError):
            os.replace(replacement, executable)

    os.replace(replacement, executable)
    assert executable.read_bytes() == b"replacement-host"


def test_launch_spec_has_fixed_argv_shell_false_and_no_secret_projection(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "CourseStudio.ProjectionHost.exe"
    executable.write_bytes(b"host")
    spec = HostLaunchSpec.for_executable(executable)

    assert spec.argv == (str(executable.resolve(strict=True)),)
    assert spec.shell is False
    assert spec.cwd == executable.parent.resolve(strict=True)
    serialized = json.dumps(spec.safe_summary(), sort_keys=True)
    assert "secret" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert str(executable) not in serialized


def test_authenticated_frames_reject_tamper_replay_wrong_direction_and_oversize() -> None:
    key = bytes(range(32))
    helper = AuthenticatedEnvelopeCodec(key, send_direction="helper", max_line_bytes=1024)
    host = AuthenticatedEnvelopeCodec(key, send_direction="host", max_line_bytes=1024)
    encoded = helper.encode({"type": "detect_displays"})

    assert host.decode(encoded, expected_direction="helper") == {
        "type": "detect_displays"
    }
    with pytest.raises(TransportProtocolError, match="transport_replay"):
        host.decode(encoded, expected_direction="helper")

    tampered = bytearray(helper.encode({"type": "detect_displays"}))
    tampered[-8] ^= 1
    with pytest.raises(TransportProtocolError, match="transport_authentication_failed"):
        AuthenticatedEnvelopeCodec(
            key, send_direction="host", max_line_bytes=1024
        ).decode(bytes(tampered), expected_direction="helper")

    with pytest.raises(TransportProtocolError, match="transport_direction_invalid"):
        AuthenticatedEnvelopeCodec(
            key, send_direction="host", max_line_bytes=1024
        ).decode(helper.encode({"type": "ok"}), expected_direction="host")
    with pytest.raises(TransportProtocolError, match="transport_message_too_large"):
        helper.encode({"type": "large", "value": "x" * 5000})
    with pytest.raises(TransportProtocolError, match="transport_message_too_large"):
        AuthenticatedEnvelopeCodec(
            key, send_direction="host", max_line_bytes=1024
        ).decode(encoded[:-1] + b"\r\n", expected_direction="helper")


def test_authenticated_frame_matches_cross_language_fixture() -> None:
    codec = AuthenticatedEnvelopeCodec(
        bytes(range(32)),
        send_direction="helper",
    )

    assert codec.encode({"type": "detect_displays"}) == (
        AUTHENTICATED_FRAME_FIXTURE.read_bytes()
    )


def test_supervisor_is_serialized_idempotent_and_rejects_command_id_collision(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(_transport_for_commands)
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
        clock=lambda: NOW,
    )
    command = _command("detect_displays")

    first = supervisor.detect_displays(command)
    second = supervisor.detect_displays(command)

    assert first == second
    assert len(transport.sent) == 1
    changed = command.model_copy(update={"payload": {"swap": True}})
    with pytest.raises(ProjectionHostError, match="command_id_collision"):
        supervisor.detect_displays(changed)


def test_supervisor_never_replays_cached_success_after_host_loss(tmp_path: Path) -> None:
    transport = FakeTransport(_transport_for_commands)
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
        clock=lambda: NOW,
    )
    command = _command("detect_displays")
    assert supervisor.detect_displays(command).accepted
    transport.alive = False

    with pytest.raises(ProjectionHostError, match="host_not_running"):
        supervisor.detect_displays(command)

    assert len(transport.sent) == 1


def test_supervisor_rejects_wrong_method_and_mismatched_receipt(tmp_path: Path) -> None:
    command = _command("assign_projection_window")
    transport = FakeTransport(
        lambda _payload: {
            "type": "projection_receipt",
            "receipt": _receipt(_command("detect_displays")),
        }
    )
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
        clock=lambda: NOW,
    )

    with pytest.raises(ProjectionHostError, match="unexpected_command"):
        supervisor.detect_displays(command)
    with pytest.raises(ProjectionHostError, match="receipt_identity_mismatch"):
        supervisor.assign_windows(command)
    assert transport.closed is True


def test_open_session_streams_ordered_verified_assets_before_bootstrap(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(_transport_for_commands)
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: _bundle(b"x" * 100_000),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
        clock=lambda: NOW,
        asset_chunk_bytes=36 * 1024,
    )
    command = _command("open_projection_session")

    receipt = supervisor.open_session(command)

    assert isinstance(receipt, ProjectionReceipt)
    message_types = [str(message["type"]) for message in transport.sent]
    assert message_types == [
        "projection_command",
        "asset_begin",
        "asset_chunk",
        "asset_chunk",
        "asset_chunk",
        "asset_commit",
        "projection_bootstrap",
    ]
    chunks = [message for message in transport.sent if message["type"] == "asset_chunk"]
    assert [chunk["offset"] for chunk in chunks] == [0, 36 * 1024, 72 * 1024]
    assert "path" not in json.dumps(transport.sent, sort_keys=True).lower()
    codec = AuthenticatedEnvelopeCodec(
        b"k" * 32,
        send_direction="helper",
    )
    assert all(len(codec.encode(message)) <= 72 * 1024 for message in transport.sent)


def test_rejected_open_is_idempotent_but_never_recorded_as_session_opened(
    tmp_path: Path,
) -> None:
    def reject_open(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        response = _transport_for_commands(payload)
        if response.get("type") == "projection_receipt":
            receipt = dict(response["receipt"])
            receipt.update(accepted=False, status="invalidated", message="rejected")
            return {"type": "projection_receipt", "receipt": receipt}
        return response

    transport = FakeTransport(reject_open)
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
    )
    command = _command("open_projection_session")

    first = supervisor.open_session(command)
    second = supervisor.open_session(command)

    assert first == second and not first.accepted
    events = {
        json.loads(path.read_text(encoding="utf-8"))["eventType"]
        for path in (tmp_path / "evidence").glob("*.json")
    }
    assert "asset_bundle_verified" in events
    assert "session_invalidated" in events
    assert "session_opened" not in events


@pytest.mark.parametrize(
    ("bundle", "code"),
    [
        (
            ProjectionSessionBundle(
                course_version_id="course-version-1",
                runtime_manifest_digest=DIGEST_A,
                navigation_identity=DIGEST_B,
                bootstrap={},
                assets=tuple(_bundle().assets[0] for _ in range(129)),
            ),
            "asset_count_exceeded",
        ),
        (
            _bundle(b"x" * (20 * 1024 * 1024 + 1)),
            "asset_size_exceeded",
        ),
        (
            ProjectionSessionBundle(
                course_version_id="course-version-1",
                runtime_manifest_digest=DIGEST_A,
                navigation_identity=DIGEST_B,
                bootstrap={},
                assets=tuple(
                    _declared_asset(index, 20 * 1024 * 1024)
                    for index in range(5)
                ),
            ),
            "asset_bundle_size_exceeded",
        ),
    ],
)
def test_asset_ceilings_fail_before_partial_transfer(
    tmp_path: Path,
    bundle: ProjectionSessionBundle,
    code: str,
) -> None:
    transport = FakeTransport(_transport_for_commands)
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: bundle,
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
        clock=lambda: NOW,
    )

    with pytest.raises(ProjectionHostError, match=code):
        supervisor.open_session(_command("open_projection_session"))
    assert all(message["type"] != "asset_begin" for message in transport.sent)
    assert transport.closed is True


def test_digest_mismatch_timeout_eof_and_shutdown_all_close_transport(
    tmp_path: Path,
) -> None:
    cases = ("digest", "host_timeout", "host_eof")
    for case in cases:
        if case == "digest":
            transport = FakeTransport(_transport_for_commands)
            valid = _bundle().assets[0]
            bad = valid.__class__(
                opaque_id=valid.opaque_id,
                media_type=valid.media_type,
                byte_size=valid.byte_size,
                sha256="0" * 64,
                open_verified=valid.open_verified,
            )
            bundle = _bundle().__class__(
                course_version_id="course-version-1",
                runtime_manifest_digest=DIGEST_A,
                navigation_identity=DIGEST_B,
                bootstrap={},
                assets=(bad,),
            )
        else:
            transport = FakeTransport(_transport_for_commands)
            transport.alive = case != "host_eof"
            if case == "host_timeout":
                transport.responder = lambda _payload: {}
            bundle = _bundle()
        supervisor = ProjectionHostSupervisor(
            transport_factory=lambda transport=transport: transport,
            bundle_resolver=lambda _command, bundle=bundle: bundle,
            evidence_store=ProjectionEvidenceStore(tmp_path / case),
            clock=lambda: NOW,
        )

        with pytest.raises(ProjectionHostError):
            supervisor.open_session(_command("open_projection_session"))
        assert transport.closed is True

    clean = FakeTransport(_transport_for_commands)
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: clean,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "clean"),
        clock=lambda: NOW,
    )
    supervisor.detect_displays(_command("detect_displays"))
    supervisor.shutdown()
    assert clean.closed is True


def test_evidence_store_is_allowlisted_bounded_atomic_and_redacted(tmp_path: Path) -> None:
    store = ProjectionEvidenceStore(tmp_path / "evidence", clock=lambda: NOW)
    receipt = store.record(
        event_type="session_opened",
        session_id=str(uuid4()),
        command_id=str(uuid4()),
        generation=2,
        sequence=3,
        status="assigned",
        code="session_opened",
        digests={"manifest": DIGEST_A},
    )

    assert receipt["eventType"] == "session_opened"
    files = list((tmp_path / "evidence").glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert str(tmp_path) not in raw
    assert "token" not in raw.lower()
    assert "codeValue" not in raw
    with pytest.raises(ValueError, match="evidence_event_not_allowed"):
        store.record(
            event_type="frame_committed",
            session_id=str(uuid4()),
            command_id=str(uuid4()),
            generation=2,
            sequence=4,
            status="syncing",
            code="frame",
            digests={},
        )


def test_bootstrap_handshake_proves_both_sides_and_derives_fresh_session_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_key = bytearray(bytes(range(32)))
    host_nonce = bytes(range(32, 64))
    helper_nonce = bytes(range(64, 96))
    import hmac

    host_mac = hmac.digest(bytes(launch_key), b"host_hello\0" + host_nonce, "sha256")
    hello = {
        "schemaVersion": 1,
        "type": "host_hello",
        "hostNonce": projection_host_module._base64url(host_nonce),
        "mac": projection_host_module._base64url(host_mac),
    }

    class FakeProcess:
        stdin = io.BytesIO()
        stdout = io.BytesIO(
            json.dumps(hello, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        )

    monkeypatch.setattr(
        projection_host_module.secrets,
        "token_bytes",
        lambda count: helper_nonce if count == 32 else b"x" * count,
    )
    process = FakeProcess()
    session_key = projection_host_module._perform_helper_handshake(
        process, launch_key
    )

    expected = hmac.digest(
        bytes(launch_key), b"session\0" + host_nonce + helper_nonce, "sha256"
    )
    assert bytes(session_key) == expected
    response = json.loads(process.stdin.getvalue())
    assert response["type"] == "helper_hello"
    supplied = projection_host_module._decode_base64url(response["mac"])
    assert hmac.compare_digest(
        supplied,
        hmac.digest(
            bytes(launch_key),
            b"helper_hello\0" + host_nonce + helper_nonce,
            "sha256",
        ),
    )


def test_cancel_interrupts_receive_and_next_run_can_start_fresh_transport(
    tmp_path: Path,
) -> None:
    class BlockingTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__(lambda _payload: {})
            self.started = threading.Event()
            self.released = threading.Event()

        def receive(self, *, timeout_seconds: float) -> Mapping[str, Any]:
            self.started.set()
            self.released.wait(timeout_seconds)
            raise TransportProtocolError("host_eof" if self.closed else "host_timeout")

        def close(self) -> None:
            super().close()
            self.released.set()

    blocking = BlockingTransport()
    healthy = FakeTransport(_transport_for_commands)
    transports = iter((blocking, healthy))
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: next(transports),
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
        clock=lambda: NOW,
    )
    failures: list[BaseException] = []
    worker = threading.Thread(
        target=lambda: _capture_failure(
            failures,
            lambda: supervisor.detect_displays(_command("detect_displays")),
        )
    )
    worker.start()
    assert blocking.started.wait(1)

    started = time.monotonic()
    supervisor.cancel_current()
    worker.join(1)

    assert not worker.is_alive()
    assert time.monotonic() - started < 1
    assert isinstance(failures[0], ProjectionHostError)
    assert supervisor.detect_displays(_command("detect_displays")).accepted is True


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_job_object_close_terminates_child_without_orphan() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    job = projection_host_module._WindowsJobObject.create()
    try:
        job.assign(process)
        job.close()
        process.wait(timeout=5)
        assert process.poll() is not None
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended launch contract")
def test_suspended_child_is_contained_before_any_child_code_runs(tmp_path: Path) -> None:
    marker = tmp_path / "child-started.txt"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import time; "
                f"Path({str(marker)!r}).write_text('started'); time.sleep(60)"
            ),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW | projection_host_module._CREATE_SUSPENDED,
    )
    job = projection_host_module._WindowsJobObject.create()
    try:
        job.assign(process)
        time.sleep(0.2)
        assert not marker.exists()
        projection_host_module._resume_suspended_process(process)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.read_text(encoding="utf-8") == "started"
        job.close()
        process.wait(timeout=5)
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows failed launch cleanup contract")
def test_failed_preassignment_launch_terminates_suspended_child() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW | projection_host_module._CREATE_SUSPENDED,
    )
    job = projection_host_module._WindowsJobObject.create()
    try:
        projection_host_module._cleanup_failed_launch(process, job)
        process.wait(timeout=5)
        assert process.poll() is not None
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_helper_restart_uses_new_transport_and_no_cached_process_state(
    tmp_path: Path,
) -> None:
    first = FakeTransport(_transport_for_commands)
    first_supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: first,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "first"),
    )
    first_supervisor.detect_displays(_command("detect_displays"))
    first_supervisor.shutdown()
    assert first.closed

    second = FakeTransport(_transport_for_commands)
    second_supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: second,
        bundle_resolver=lambda _command: _bundle(),
        evidence_store=ProjectionEvidenceStore(tmp_path / "second"),
    )
    assert second_supervisor.detect_displays(_command("detect_displays")).accepted
    assert len(second.sent) == 1
    second_supervisor.shutdown()


@pytest.mark.skipif(
    os.environ.get("COURSE_PROJECTION_INTEGRATION_TEST") != "1",
    reason="explicit real Host transport gate",
)
def test_real_host_authenticated_detect_smoke(tmp_path: Path) -> None:
    workspace = Path(__file__).parents[3]
    build_root = (
        workspace
        / "platform"
        / "windows"
        / "src"
        / "CourseStudio.ProjectionHost"
        / "bin"
        / "Debug"
        / "net10.0-windows"
    ).resolve(strict=True)
    install_root = tmp_path / "install"
    host_root = install_root / "projection-host"
    shutil.copytree(build_root, host_root)
    executable = host_root / "CourseStudio.ProjectionHost.exe"
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    runtime_root = (workspace / ".tools" / "dotnet").resolve(strict=True)
    session_temp_root = Path(tempfile.gettempdir()) / "CourseStudio.ProjectionHost"
    before = (
        {child.name for child in session_temp_root.iterdir()}
        if session_temp_root.exists()
        else set()
    )
    transport = InstalledHostTransportFactory(
        HostExecutablePolicy(install_root, digest),
        runtime_root=runtime_root,
    )()
    try:
        command = _command("detect_displays")
        transport.send(
            {
                "type": "projection_command",
                "command": command.model_dump(mode="json", by_alias=True),
            }
        )
        message = transport.receive(timeout_seconds=15)
        receipt = ProjectionReceipt.model_validate(message["receipt"])
        assert message["type"] == "projection_receipt"
        assert receipt.command_id == command.command_id
        assert receipt.command == "detect_displays"
        assert receipt.generation == 0
        assert receipt.status in {"candidate", "undetected"}
    finally:
        transport.close()
    assert not transport.is_alive
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        after = (
            {child.name for child in session_temp_root.iterdir()}
            if session_temp_root.exists()
            else set()
        )
        if not after.difference(before):
            break
        time.sleep(0.05)
    assert not after.difference(before)


@pytest.mark.skipif(
    os.environ.get("COURSE_PROJECTION_INTEGRATION_TEST") != "1",
    reason="explicit real Host transport gate",
)
def test_real_host_supervisor_transfers_bundle_and_closes(tmp_path: Path) -> None:
    workspace = Path(__file__).parents[3]
    executable = (
        workspace
        / "platform"
        / "windows"
        / "src"
        / "CourseStudio.ProjectionHost"
        / "bin"
        / "Debug"
        / "net10.0-windows"
        / "CourseStudio.ProjectionHost.exe"
    ).resolve(strict=True)
    runtime_root = (workspace / ".tools" / "dotnet").resolve(strict=True)
    transport = projection_host_module.AuthenticatedSubprocessTransport.launch(
        HostLaunchSpec.for_executable(executable, runtime_root=runtime_root)
    )
    supervisor = ProjectionHostSupervisor(
        transport_factory=lambda: transport,
        bundle_resolver=lambda _command: _bundle(b"verified-session-asset"),
        evidence_store=ProjectionEvidenceStore(tmp_path / "evidence"),
    )
    detect = supervisor.detect_displays(_command("detect_displays"))
    assert detect.accepted and detect.status == "candidate"
    opened = _command("open_projection_session")
    open_receipt = supervisor.open_session(opened)
    assert open_receipt.accepted and open_receipt.status == "candidate"
    close = _command("close_projection_session").model_copy(
        update={"session_id": opened.session_id}
    )
    close_receipt = supervisor.close_session(close)
    assert close_receipt.accepted and close_receipt.status == "closed"
    assert not transport.is_alive
    assert list((tmp_path / "evidence").glob("*.json"))


def _capture_failure(
    failures: list[BaseException],
    action: Callable[[], object],
) -> None:
    try:
        action()
    except BaseException as error:
        failures.append(error)
