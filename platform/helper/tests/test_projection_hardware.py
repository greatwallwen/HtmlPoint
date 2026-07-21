from __future__ import annotations

from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

import pytest

from course_helper.domain.projection import ProjectionReceipt
from course_helper.projection_host import HostExecutablePolicy, ProjectionHostSupervisor
from test_projection_integration import (
    HOST_PATH,
    HOST_RUN_ROOT,
    INSTALL_ROOT,
    REPO_ROOT,
    _command,
    _published_projection,
    _receipt_digest,
    _supervisor,
    _tree_digest,
)


HARDWARE_RECEIPT = (
    REPO_ROOT
    / "platform"
    / "windows"
    / "evidence"
    / "physical-dual-screen-current.json"
)


def _remove_stale_receipt() -> None:
    if not HARDWARE_RECEIPT.exists():
        return
    if HARDWARE_RECEIPT.is_symlink() or not HARDWARE_RECEIPT.is_file():
        raise OSError("hardware evidence is not a plain file")
    HARDWARE_RECEIPT.unlink()


def _write_hardware_receipt(payload: dict[str, object]) -> None:
    HARDWARE_RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    temporary = HARDWARE_RECEIPT.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, HARDWARE_RECEIPT)


def _runtime_directories() -> set[str]:
    if not HOST_RUN_ROOT.is_dir():
        return set()
    return {path.name for path in HOST_RUN_ROOT.iterdir() if path.is_dir()}


def _attended_prompt(title: str, message: str) -> None:
    message_box = ctypes.WinDLL("user32", use_last_error=True).MessageBoxW
    message_box.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    ]
    message_box.restype = ctypes.c_int
    result = message_box(
        None,
        message,
        title,
        0x00000040 | 0x00010000 | 0x00040000,
    )
    assert result == 1


def _open_fullscreen_and_witness(
    supervisor: ProjectionHostSupervisor,
    refs: dict[str, object],
    receipts: list[ProjectionReceipt],
) -> tuple[UUID, ProjectionReceipt]:
    session_id = uuid4()
    receipts.append(
        supervisor.detect_displays(
            _command("detect_displays", session_id=None, generation=0)
        )
    )
    receipts.append(
        supervisor.open_session(
            _command(
                "open_projection_session",
                session_id=session_id,
                generation=0,
                payload={
                    "courseVersionId": refs["courseVersionId"],
                    "slideDeckId": refs["slideDeckId"],
                    "runtimeManifestId": refs["runtimeManifestId"],
                },
            )
        )
    )
    receipts.append(
        supervisor.assign_windows(
            _command(
                "assign_projection_window",
                session_id=session_id,
                generation=0,
            )
        )
    )
    receipts.append(
        supervisor.enter_fullscreen(
            _command(
                "enter_projection_fullscreen",
                session_id=session_id,
                generation=1,
            )
        )
    )
    certified = supervisor.verify_assignment(
        _command(
            "verify_projection_assignment",
            session_id=session_id,
            generation=1,
        )
    )
    receipts.append(certified)
    return session_id, certified


@pytest.mark.projection_hardware
def test_attended_physical_dual_screen_certification(tmp_path: Path) -> None:
    _remove_stale_receipt()
    assert os.environ.get("COURSE_PROJECTION_HARDWARE_TEST") == "1"
    assert os.name == "nt"
    assert HOST_PATH.is_file()
    run_roots_before = _runtime_directories()
    host_digest = hashlib.sha256(HOST_PATH.read_bytes()).hexdigest()
    policy = HostExecutablePolicy(INSTALL_ROOT, host_digest)
    fixture, refs = _published_projection(tmp_path)
    supervisor = _supervisor(
        policy=policy,
        database_path=fixture.catalog.path,
        artifact_root=tmp_path / ".artifacts",
        evidence_root=tmp_path / "hardware-evidence",
    )
    receipts: list[ProjectionReceipt] = []
    first_transport = None
    second_transport = None
    try:
        first_session, first_certified = _open_fullscreen_and_witness(
            supervisor,
            refs,
            receipts,
        )
        first_transport = supervisor._transport
        assert first_transport is not None and first_transport.is_alive
        assert (
            first_certified.status,
            first_certified.message,
        ) == ("certified", "projection_assignment_certified")

        _attended_prompt(
            "实体双屏认证：验证帧同步",
            "请切换到控制屏（Presenter），点击三角形按钮开始计时。\n"
            "看到计时跳动后再点一次暂停，然后回到本提示并点击“确定”。",
        )
        receipts.append(
            supervisor.verify_assignment(
                _command(
                    "verify_projection_assignment",
                    session_id=first_session,
                    generation=1,
                )
            )
        )

        _attended_prompt(
            "实体双屏认证：验证失效保护",
            "请按 Alt+Tab 切换到任意一个课程窗口，再按 Win+Down 使其离开全屏或最小化。\n"
            "完成后回到本提示并点击“确定”。",
        )
        receipts.append(
            supervisor.verify_assignment(
                _command(
                    "verify_projection_assignment",
                    session_id=first_session,
                    generation=1,
                )
            )
        )
        receipts.append(
            supervisor.close_session(
                _command(
                    "close_projection_session",
                    session_id=first_session,
                    generation=1,
                )
            )
        )
        assert not first_transport.is_alive

        second_session, second_certified = _open_fullscreen_and_witness(
            supervisor,
            refs,
            receipts,
        )
        second_transport = supervisor._transport
        assert second_transport is not None and second_transport.is_alive
        assert (
            second_certified.status,
            second_certified.message,
        ) == ("certified", "projection_assignment_certified")
        receipts.append(
            supervisor.close_session(
                _command(
                    "close_projection_session",
                    session_id=second_session,
                    generation=1,
                )
            )
        )
    finally:
        supervisor.shutdown()
        fixture.catalog.close()

    expected_prefix = [
        ("candidate", "display_candidate_ready"),
        ("candidate", "projection_session_opened"),
        ("assigned", "projection_windows_assigned"),
        ("fullscreen", "projection_fullscreen_verified"),
        ("certified", "projection_assignment_certified"),
        (
            "certified",
            "projection_assignment_certified_after_frame_advance",
        ),
    ]
    assert [
        (receipt.status, receipt.message) for receipt in receipts[:6]
    ] == expected_prefix
    invalidated = receipts[6]
    assert not invalidated.accepted
    assert invalidated.status == "invalidated"
    assert invalidated.message in {
        "window_minimized",
        "window_moved",
    }
    assert [
        (receipt.status, receipt.message) for receipt in receipts[7:]
    ] == [
        ("closed", "projection_session_closed"),
        ("candidate", "display_candidate_ready"),
        ("candidate", "projection_session_opened"),
        ("assigned", "projection_windows_assigned"),
        ("fullscreen", "projection_fullscreen_verified"),
        ("certified", "projection_assignment_certified"),
        ("closed", "projection_session_closed"),
    ]
    assert all(
        receipt.accepted for index, receipt in enumerate(receipts) if index != 6
    )
    first_certified = receipts[4]
    second_certified = receipts[12]
    for certified in (first_certified, second_certified):
        assert len(certified.assignments) == 2
        assert {item.role for item in certified.assignments} == {"stage", "presenter"}
        assert len({item.display_id for item in certified.assignments}) == 2
    assert first_transport is not None and not first_transport.is_alive
    assert second_transport is not None and not second_transport.is_alive

    deadline = time.monotonic() + 5
    run_roots_after = _runtime_directories()
    while time.monotonic() < deadline and not run_roots_after <= run_roots_before:
        time.sleep(0.05)
        run_roots_after = _runtime_directories()
    assert run_roots_after <= run_roots_before

    web_root = INSTALL_ROOT / "projection-host" / "web"
    _write_hardware_receipt(
        {
            "schemaVersion": 1,
            "status": "verified",
            "mode": "attended-personal-device",
            "operatorWitnessed": True,
            "physicalDualScreenCertified": True,
            "releaseSignatureCertified": False,
            "distinctRoleDisplays": True,
            "exactFullscreenGeometry": True,
            "matchingCommittedFrame": True,
            "frameAdvanceDemonstrated": True,
            "invalidationDemonstrated": True,
            "restoredAndRewitnessed": True,
            "orphanProcessCount": 0,
            "hostExecutableDigest": host_digest,
            "webBundleDigest": _tree_digest(web_root),
            "receiptDigests": [_receipt_digest(receipt) for receipt in receipts],
            "checks": [
                "host_digest_policy",
                "authenticated_helper_transport",
                "published_bundle_streamed",
                "distinct_physical_assignments",
                "exact_fullscreen_geometry",
                "webview_roles_committed_equal_frame",
                "attended_codes_accepted",
                "post_witness_frame_advance",
                "window_invalidation_observed",
                "restored_and_rewitnessed",
                "host_job_cleanup",
            ],
            "verifiedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
