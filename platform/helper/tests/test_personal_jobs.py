from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.sources import SourceAssetVersion
from course_helper.jobs import (
    JobSpec,
    PersonalCourseCreateJob,
    PersonalCourseStatusJob,
    WorkerRuntimeConfig,
    personal_course_create_request_digest,
)
from course_helper.personal_jobs import run_personal_job


NOW = datetime(2026, 7, 21, 4, 0, tzinfo=timezone.utc)
ACTOR_JSON = {"actorType": "human", "actorId": "personal-user"}


class RecordingSupervisor:
    def __init__(self) -> None:
        self.started: list[tuple[str, ActorRef]] = []

    def start(self, run_id: str, actor: ActorRef) -> None:
        self.started.append((run_id, actor))


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "personal-jobs.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
    )


def _seed_source(config: WorkerRuntimeConfig) -> str:
    source = SourceAssetVersion(
        logical_id="source-personal-job",
        version_id="source-personal-job-v1",
        revision=1,
        content_digest="a" * 64,
        created_at=NOW,
        created_by=ActorRef(actor_type="human", actor_id="personal-user"),
        locator=SourceLocator(root_id="fixture", relative_path="personal.md"),
        display_name="personal.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=12,
        extraction_status="parsed",
    )
    with KnowledgeCatalog.open(config.database_path) as catalog:
        catalog.insert_source(source)
    return source.version_id


def _create_job(source_version_id: str) -> PersonalCourseCreateJob:
    request = {
        "requestId": "personal-request-" + "d" * 32,
        "prompt": "制作个人 AI 工作流课程",
        "sourceVersionIds": [source_version_id],
        "titleHint": None,
        "createdAt": NOW.isoformat(),
    }
    payload = {
        "type": "personal_course_create",
        "operationId": "personal-create-operation",
        "requestDigest": personal_course_create_request_digest(request),
        "actor": ACTOR_JSON,
        "request": request,
    }
    return PersonalCourseCreateJob.model_validate(payload)


def test_personal_create_is_exact_scheduled_and_projects_only_public_fields(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    job = _create_job(_seed_source(config))
    supervisor = RecordingSupervisor()

    outcome = run_personal_job(job, config, supervisor)

    assert outcome.status_code == 202
    assert set(outcome.result) == {"runId", "view"}
    assert set(outcome.result["view"]) == {
        "status",
        "phaseLabel",
        "title",
        "chapterCount",
        "attentionCount",
        "canResume",
        "course",
    }
    assert outcome.result["view"]["status"] == "creating"
    assert supervisor.started[0][0] == outcome.result["runId"]
    assert "source-personal-job-v1" not in json.dumps(outcome.result["view"])


def test_personal_status_is_read_only_and_rejects_extra_create_fields(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    create = _create_job(_seed_source(config))
    supervisor = RecordingSupervisor()
    created = run_personal_job(create, config, supervisor)
    status = PersonalCourseStatusJob.model_validate(
        {
            "type": "personal_course_status",
            "runId": created.result["runId"],
            "actor": ACTOR_JSON,
        }
    )

    projected = run_personal_job(status, config, supervisor)

    assert projected.status_code == 200
    assert projected.result == created.result
    bad = create.model_dump(mode="json", by_alias=True)
    bad["internalId"] = "leak"
    try:
        TypeAdapter(JobSpec).validate_python(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("personal create accepted an extra internal field")
