from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.domain.personal_course import PersonalCourseRequest
from course_helper.jobs import WorkerRuntimeConfig
from course_helper.personal_runs import create_personal_run
from course_helper.personal_supervisor import PersonalCourseSupervisor


NOW = datetime(2026, 7, 21, 3, 30, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="system", actor_id="supervisor-tests")


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "supervisor.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
    )


def _queued_run(config: WorkerRuntimeConfig):
    request = PersonalCourseRequest(
        request_id="personal-request-" + "c" * 32,
        prompt="个人 AI 课程",
        source_version_ids=("source-supervisor-v1",),
        created_at=NOW,
        requested_by=ACTOR,
    )
    with KnowledgeCatalog.open(config.database_path) as catalog:
        return create_personal_run(
            catalog,
            request,
            source_snapshot_digest=hashlib.sha256(b"snapshot").hexdigest(),
            clock=lambda: NOW,
        )


def test_start_is_nonblocking_and_duplicate_start_uses_one_future(tmp_path: Path) -> None:
    config = _config(tmp_path)
    run = _queued_run(config)
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocking_runner(runtime, run_id: str, actor: ActorRef):
        calls.append(run_id)
        entered.set()
        release.wait(timeout=5)
        return run

    supervisor = PersonalCourseSupervisor(config, resume_fn=blocking_runner)
    try:
        first = supervisor.start(run.run_id, ACTOR)
        assert entered.wait(timeout=2)
        second = supervisor.start(run.run_id, ACTOR)
        assert first is second
        assert calls == [run.run_id]
    finally:
        release.set()
        supervisor.shutdown()


def test_resume_pending_schedules_persisted_nonterminal_runs(tmp_path: Path) -> None:
    config = _config(tmp_path)
    run = _queued_run(config)
    calls: list[str] = []

    def recording_runner(runtime, run_id: str, actor: ActorRef):
        calls.append(run_id)
        return run

    supervisor = PersonalCourseSupervisor(config, resume_fn=recording_runner)
    try:
        futures = supervisor.resume_pending()
        tuple(future.result(timeout=2) for future in futures)
    finally:
        supervisor.shutdown()

    assert calls == [run.run_id]
