from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.projection import ProjectionCommand, ProjectionReceipt
from course_helper.jobs import (
    JobSpec,
    ProjectionJob,
    WorkerRuntimeConfig,
    projection_job_command,
    projection_job_timeout_seconds,
)
from course_helper.projection_host import ProjectionHostError


def _job_payloads() -> tuple[dict[str, Any], ...]:
    session_id = str(uuid4())
    return (
        {
            "type": "projection_detect_displays",
            "commandId": str(uuid4()),
            "sessionId": None,
            "expectedGeneration": 0,
            "payload": {},
        },
        {
            "type": "projection_open_session",
            "commandId": str(uuid4()),
            "sessionId": session_id,
            "expectedGeneration": 0,
            "payload": {
                "courseVersionId": "course-v1",
                "slideDeckId": "deck-v1",
                "runtimeManifestId": "runtime-v1",
            },
        },
        {
            "type": "projection_assign_window",
            "commandId": str(uuid4()),
            "sessionId": session_id,
            "expectedGeneration": 0,
            "payload": {"swap": False},
        },
        {
            "type": "projection_enter_fullscreen",
            "commandId": str(uuid4()),
            "sessionId": session_id,
            "expectedGeneration": 1,
            "payload": {},
        },
        {
            "type": "projection_verify_assignment",
            "commandId": str(uuid4()),
            "sessionId": session_id,
            "expectedGeneration": 1,
            "payload": {},
        },
        {
            "type": "projection_close_session",
            "commandId": str(uuid4()),
            "sessionId": session_id,
            "expectedGeneration": 1,
            "payload": {},
        },
    )


def test_six_projection_jobs_build_exact_native_commands_with_bounded_timeouts() -> (
    None
):
    adapter = TypeAdapter(JobSpec)
    expected_commands = (
        "detect_displays",
        "open_projection_session",
        "assign_projection_window",
        "enter_projection_fullscreen",
        "verify_projection_assignment",
        "close_projection_session",
    )

    for payload, expected_command in zip(
        _job_payloads(), expected_commands, strict=True
    ):
        job = adapter.validate_python(payload)
        assert isinstance(job, ProjectionJob)
        command = projection_job_command(job)
        assert command.command == expected_command
        assert str(command.command_id) == payload["commandId"]
        assert command.expected_generation == payload["expectedGeneration"]
        assert (
            command.model_dump(mode="json", by_alias=True)["payload"]
            == payload["payload"]
        )
        assert 0 < projection_job_timeout_seconds(job) <= 120
        if job.type == "projection_verify_assignment":
            assert projection_job_timeout_seconds(job) == 120


@pytest.mark.parametrize(
    "forbidden",
    (
        "url",
        "path",
        "hwnd",
        "token",
        "shell",
        "runtimePath",
        "manifest",
        "course",
        "assetBytes",
    ),
)
@pytest.mark.parametrize("job_index", range(6))
def test_projection_jobs_reject_sensitive_or_browser_authored_extra_fields(
    forbidden: str,
    job_index: int,
) -> None:
    adapter = TypeAdapter(JobSpec)
    payload = _job_payloads()[job_index]
    payload["payload"] = {**payload["payload"], forbidden: "private-value"}

    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


@pytest.mark.parametrize(
    "index,mutation",
    (
        (0, {"sessionId": str(uuid4())}),
        (1, {"sessionId": None}),
        (2, {"payload": {"swap": "true"}}),
        (3, {"expectedGeneration": -1}),
        (4, {"payload": {"witnessCode": "1234"}}),
        (5, {"extra": True}),
    ),
)
def test_projection_job_identity_and_payload_schemas_are_strict(
    index: int,
    mutation: dict[str, Any],
) -> None:
    payload = {**_job_payloads()[index], **mutation}

    with pytest.raises(ValidationError):
        TypeAdapter(JobSpec).validate_python(payload)


class _NeverRunner:
    async def run(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("projection jobs must not spawn a worker")


class _CaptureSupervisor:
    def __init__(self) -> None:
        self.commands: list[ProjectionCommand] = []
        self.method_calls: list[str] = []

    def _receipt(
        self,
        command: ProjectionCommand,
        method_name: str,
    ) -> ProjectionReceipt:
        self.commands.append(command)
        self.method_calls.append(method_name)
        return ProjectionReceipt(
            commandId=command.command_id,
            sessionId=command.session_id,
            command=command.command,
            accepted=True,
            status="candidate",
            generation=command.expected_generation,
            message="projection_command_accepted",
        )

    def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._receipt(command, "detect_displays")

    def open_session(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._receipt(command, "open_session")

    def assign_windows(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._receipt(command, "assign_windows")

    def enter_fullscreen(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._receipt(command, "enter_fullscreen")

    def verify_assignment(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._receipt(command, "verify_assignment")

    def close_session(self, command: ProjectionCommand) -> ProjectionReceipt:
        return self._receipt(command, "close_session")

    def cancel_current(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


def _projection_client(
    tmp_path: Path,
    supervisor: object | None,
) -> tuple[TestClient, dict[str, str]]:
    from course_helper.api import HelperRuntime, create_app
    from course_helper.session import LaunchSession

    database = tmp_path / "knowledge.db"
    with KnowledgeCatalog.open(database):
        pass
    session = LaunchSession.create(allowed_origin="http://127.0.0.1:4173")
    runtime = HelperRuntime(
        config=WorkerRuntimeConfig(
            database_path=str(database),
            app_data_path=str(tmp_path / "app-data"),
            source_roots=(),
        ),
        launch_session=session,
        job_runner=_NeverRunner(),  # type: ignore[arg-type]
        projection_supervisor=supervisor,  # type: ignore[arg-type]
    )
    client = TestClient(create_app(runtime))
    exchanged = client.post(
        "/v1/session/exchange",
        headers={"Origin": session.allowed_origin},
        json={"nonce": session.launch_nonce},
    )
    assert exchanged.status_code == 200
    return client, {
        "Origin": session.allowed_origin,
        "X-Course-Session": exchanged.json()["sessionToken"],
    }


def test_authenticated_jobs_dispatch_once_to_each_matching_supervisor_method(
    tmp_path: Path,
) -> None:
    supervisor = _CaptureSupervisor()
    client, headers = _projection_client(tmp_path, supervisor)

    assert client.post("/v1/jobs", json=_job_payloads()[0]).status_code == 401
    responses = [
        client.post("/v1/jobs", headers=headers, json=payload)
        for payload in _job_payloads()
    ]

    assert [response.status_code for response in responses] == [200] * 6
    assert [command.command for command in supervisor.commands] == [
        "detect_displays",
        "open_projection_session",
        "assign_projection_window",
        "enter_projection_fullscreen",
        "verify_projection_assignment",
        "close_projection_session",
    ]
    assert supervisor.method_calls == [
        "detect_displays",
        "open_session",
        "assign_windows",
        "enter_fullscreen",
        "verify_assignment",
        "close_session",
    ]
    for response in responses:
        value = response.json()
        assert set(value) == {"result", "evidence"}
        assert set(value["result"]) == {"receipt"}
        assert value["evidence"]["kind"] == "runtime"
        assert response.headers["cache-control"] == "no-store"


def test_missing_supervisor_and_private_exception_are_sanitized(tmp_path: Path) -> None:
    client, headers = _projection_client(tmp_path / "missing", None)
    missing = client.post("/v1/jobs", headers=headers, json=_job_payloads()[0])
    assert missing.status_code == 503
    assert missing.json()["result"] == {
        "reasonCode": "projection_unavailable",
        "status": "failed",
    }

    class BrokenSupervisor(_CaptureSupervisor):
        def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
            del command
            raise ProjectionHostError(f"private:{tmp_path}:secret-token")

    broken_client, broken_headers = _projection_client(
        tmp_path / "broken",
        BrokenSupervisor(),
    )
    broken = broken_client.post(
        "/v1/jobs",
        headers=broken_headers,
        json=_job_payloads()[0],
    )
    serialized = broken.text
    assert broken.status_code == 503
    assert broken.json()["result"]["reasonCode"] == "projection_command_failed"
    assert str(tmp_path) not in serialized
    assert "secret-token" not in serialized


def test_replay_conflict_and_stale_generation_return_bounded_outcomes(
    tmp_path: Path,
) -> None:
    class ConflictSupervisor(_CaptureSupervisor):
        def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
            raise ProjectionHostError("command_id_collision")

        def enter_fullscreen(self, command: ProjectionCommand) -> ProjectionReceipt:
            return ProjectionReceipt(
                commandId=command.command_id,
                sessionId=command.session_id,
                command=command.command,
                accepted=False,
                status="invalidated",
                generation=command.expected_generation,
                message="generation_mismatch",
            )

    client, headers = _projection_client(tmp_path, ConflictSupervisor())
    replay = client.post("/v1/jobs", headers=headers, json=_job_payloads()[0])
    stale = client.post("/v1/jobs", headers=headers, json=_job_payloads()[3])

    assert replay.status_code == 409
    assert replay.json()["result"]["reasonCode"] == "command_replay_conflict"
    assert stale.status_code == 409
    assert stale.json()["result"]["receipt"]["message"] == "generation_mismatch"
    assert (
        datetime.fromisoformat(stale.json()["evidence"]["finishedAt"]).tzinfo
        is not None
    )


def test_native_receipt_message_is_allowlisted_before_browser_response(
    tmp_path: Path,
) -> None:
    class PrivateMessageSupervisor(_CaptureSupervisor):
        def enter_fullscreen(self, command: ProjectionCommand) -> ProjectionReceipt:
            return ProjectionReceipt(
                commandId=command.command_id,
                sessionId=command.session_id,
                command=command.command,
                accepted=False,
                status="invalidated",
                generation=command.expected_generation,
                message=f"private:{tmp_path}:secret-token",
            )

    client, headers = _projection_client(tmp_path, PrivateMessageSupervisor())
    response = client.post("/v1/jobs", headers=headers, json=_job_payloads()[3])

    assert response.status_code == 409
    assert response.json()["result"]["receipt"]["message"] == (
        "projection_command_rejected"
    )
    assert str(tmp_path) not in response.text
    assert "secret-token" not in response.text


def test_projection_timeout_cancels_the_current_host_receive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = threading.Event()

    class BlockingSupervisor(_CaptureSupervisor):
        def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
            del command
            assert cancelled.wait(timeout=2)
            raise ProjectionHostError("host_not_running")

        def cancel_current(self) -> None:
            cancelled.set()
            raise RuntimeError(f"private:{tmp_path}:secret-token")

    monkeypatch.setattr(
        "course_helper.api.projection_job_timeout_seconds",
        lambda _job: 0.01,
    )
    client, headers = _projection_client(tmp_path, BlockingSupervisor())

    response = client.post("/v1/jobs", headers=headers, json=_job_payloads()[0])

    assert response.status_code == 504
    assert response.json()["result"] == {
        "reasonCode": "projection_timeout",
        "status": "failed",
    }
    assert cancelled.is_set()
    assert str(tmp_path) not in response.text
    assert "secret-token" not in response.text


def test_timeout_does_not_return_until_the_cancelled_worker_thread_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = threading.Event()
    finished = threading.Event()

    class SlowExitSupervisor(_CaptureSupervisor):
        def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
            assert cancelled.wait(timeout=2)
            time.sleep(2.7)
            finished.set()
            return self._receipt(command, "detect_displays")

        def cancel_current(self) -> None:
            cancelled.set()

    monkeypatch.setattr(
        "course_helper.api.projection_job_timeout_seconds",
        lambda _job: 0.01,
    )
    client, headers = _projection_client(tmp_path, SlowExitSupervisor())

    response = client.post("/v1/jobs", headers=headers, json=_job_payloads()[0])

    assert response.status_code == 504
    assert finished.is_set()


def test_projection_gateway_serializes_concurrent_commands_before_supervisor(
    tmp_path: Path,
) -> None:
    guard = threading.Lock()
    active = 0
    max_active = 0

    class ConcurrentProbeSupervisor(_CaptureSupervisor):
        def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return self._receipt(command, "detect_displays")

    client, headers = _projection_client(tmp_path, ConcurrentProbeSupervisor())
    payloads = (_job_payloads()[0], _job_payloads()[0])
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = tuple(
            executor.map(
                lambda payload: client.post("/v1/jobs", headers=headers, json=payload),
                payloads,
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert max_active == 1


def test_cancelled_request_waits_for_request_bound_host_cancellation() -> None:
    from course_helper.api import _run_projection_job

    started = threading.Event()
    cancel_started = threading.Event()
    cancelled = threading.Event()
    finished = threading.Event()

    class CancelProbeSupervisor(_CaptureSupervisor):
        def detect_displays(self, command: ProjectionCommand) -> ProjectionReceipt:
            del command
            started.set()
            assert cancelled.wait(timeout=2)
            time.sleep(0.4)
            finished.set()
            raise ProjectionHostError("host_not_running")

        def cancel_current(self) -> None:
            cancel_started.set()
            cancelled.set()
            time.sleep(0.15)

    job = TypeAdapter(JobSpec).validate_python(_job_payloads()[0])
    assert isinstance(job, ProjectionJob)

    async def scenario() -> None:
        task = asyncio.create_task(_run_projection_job(job, CancelProbeSupervisor()))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()

        async def repeat_cancellation() -> None:
            assert await asyncio.to_thread(cancel_started.wait, 2)
            for _ in range(5):
                await asyncio.sleep(0.01)
                task.cancel()

        cancellation_storm = asyncio.create_task(repeat_cancellation())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        await cancellation_storm

    asyncio.run(scenario())

    assert cancelled.is_set()
