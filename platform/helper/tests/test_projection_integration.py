from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID, uuid4

import pytest

from course_helper.domain.projection import ProjectionCommand, ProjectionReceipt
from course_helper.projection_bundle import PublishedProjectionBundleResolver
from course_helper.projection_events import ProjectionEvidenceStore
from course_helper.projection_host import (
    HostExecutablePolicy,
    InstalledHostTransportFactory,
    ProjectionHostError,
    ProjectionHostSupervisor,
)
from test_projection_bundle import _published_projection


REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_ROOT = REPO_ROOT / ".tools" / "projection-integration" / "install"
HOST_PATH = (
    INSTALL_ROOT / "projection-host" / "CourseStudio.ProjectionHost.exe"
)
HOST_RUN_ROOT = Path(tempfile.gettempdir()) / "CourseStudio.ProjectionHost"


def _command(
    name: str,
    *,
    session_id: UUID | None,
    generation: int,
    payload: dict[str, object] | None = None,
) -> ProjectionCommand:
    return ProjectionCommand.model_validate(
        {
            "schemaVersion": 1,
            "commandId": str(uuid4()),
            "command": name,
            "sessionId": None if session_id is None else str(session_id),
            "expectedGeneration": generation,
            "payload": payload or {},
        }
    )


def _supervisor(
    *,
    policy: HostExecutablePolicy,
    database_path: Path,
    artifact_root: Path,
    evidence_root: Path,
) -> ProjectionHostSupervisor:
    return ProjectionHostSupervisor(
        transport_factory=InstalledHostTransportFactory(
            policy,
            runtime_root=REPO_ROOT / ".tools" / "dotnet",
        ),
        bundle_resolver=PublishedProjectionBundleResolver(
            database_path=database_path,
            artifact_root=artifact_root,
        ),
        evidence_store=ProjectionEvidenceStore(evidence_root),
        command_timeout_seconds=120,
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _receipt_digest(receipt: ProjectionReceipt) -> str:
    payload = receipt.model_dump(mode="json", by_alias=True)
    payload["commandId"] = "00000000-0000-0000-0000-000000000000"
    payload["sessionId"] = None
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_receipt(payload: dict[str, object]) -> None:
    target = REPO_ROOT / "platform" / "windows" / "evidence" / "projection-integration.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


@pytest.mark.projection_integration
def test_real_helper_host_webview_pipeline_stays_noncertified(
    tmp_path: Path,
) -> None:
    assert os.environ.get("COURSE_PROJECTION_INTEGRATION_TEST") == "1"
    assert os.name == "nt"
    assert HOST_PATH.is_file()
    run_roots_before = (
        {path.name for path in HOST_RUN_ROOT.iterdir() if path.is_dir()}
        if HOST_RUN_ROOT.is_dir()
        else set()
    )
    host_digest = hashlib.sha256(HOST_PATH.read_bytes()).hexdigest()
    with pytest.raises(ProjectionHostError, match="host_digest_mismatch"):
        HostExecutablePolicy(INSTALL_ROOT, "0" * 64).resolve()
    policy = HostExecutablePolicy(INSTALL_ROOT, host_digest)

    fixture, refs = _published_projection(tmp_path)
    receipts: list[ProjectionReceipt] = []
    first = _supervisor(
        policy=policy,
        database_path=fixture.catalog.path,
        artifact_root=tmp_path / ".artifacts",
        evidence_root=tmp_path / "restart-evidence",
    )
    first_transport = None
    try:
        detected = first.detect_displays(
            _command("detect_displays", session_id=None, generation=0)
        )
        assert detected.accepted and detected.status == "candidate"
        first_transport = first._transport
        assert first_transport is not None and first_transport.is_alive
    finally:
        first.shutdown()
    assert first_transport is not None and not first_transport.is_alive

    supervisor = _supervisor(
        policy=policy,
        database_path=fixture.catalog.path,
        artifact_root=tmp_path / ".artifacts",
        evidence_root=tmp_path / "pipeline-evidence",
    )
    transport = None
    session_id = uuid4()
    try:
        receipts.append(supervisor.detect_displays(
            _command("detect_displays", session_id=None, generation=0)
        ))
        receipts.append(supervisor.open_session(_command(
            "open_projection_session",
            session_id=session_id,
            generation=0,
            payload={
                "courseVersionId": refs["courseVersionId"],
                "slideDeckId": refs["slideDeckId"],
                "runtimeManifestId": refs["runtimeManifestId"],
            },
        )))
        transport = supervisor._transport
        receipts.append(supervisor.assign_windows(_command(
            "assign_projection_window",
            session_id=session_id,
            generation=0,
        )))
        receipts.append(supervisor.enter_fullscreen(_command(
            "enter_projection_fullscreen",
            session_id=session_id,
            generation=1,
        )))
        transport = supervisor._transport
        assert transport is not None and transport.is_alive
        receipts.append(supervisor.close_session(_command(
            "close_projection_session",
            session_id=session_id,
            generation=1,
        )))
    finally:
        supervisor.shutdown()
        fixture.catalog.close()

    expected_receipts = [
        ("candidate", "display_candidate_ready"),
        ("candidate", "projection_session_opened"),
        ("assigned", "projection_windows_assigned"),
        ("fullscreen", "projection_fullscreen_verified"),
        ("closed", "projection_session_closed"),
    ]
    for index, (receipt, expected) in enumerate(
        zip(receipts, expected_receipts, strict=True)
    ):
        assert (receipt.status, receipt.message) == expected, (
            f"receipt_{index}_{receipt.status}_{receipt.message}"
        )
    assert all(receipt.accepted for receipt in receipts)
    assert transport is not None and not transport.is_alive
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run_roots_after = (
            {path.name for path in HOST_RUN_ROOT.iterdir() if path.is_dir()}
            if HOST_RUN_ROOT.is_dir()
            else set()
        )
        if run_roots_after <= run_roots_before:
            break
        time.sleep(0.05)
    assert run_roots_after <= run_roots_before
    assert not any(receipt.status == "certified" for receipt in receipts)
    web_root = INSTALL_ROOT / "projection-host" / "web"
    _write_receipt(
        {
            "schemaVersion": 1,
            "status": "passed",
            "interactiveLocalSession": True,
            "attendedSession": False,
            "physicalDualScreenCertified": False,
            "releaseSignatureCertified": False,
            "hostExecutableDigest": host_digest,
            "webBundleDigest": _tree_digest(web_root),
            "receiptDigests": [_receipt_digest(receipt) for receipt in receipts],
            "checks": [
                "host_digest_policy",
                "authenticated_helper_transport",
                "published_bundle_streamed",
                "webview_roles_committed_equal_frame",
                "exact_fullscreen_geometry",
                "host_restart_and_job_cleanup",
                "certification_not_requested",
            ],
            "verifiedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
