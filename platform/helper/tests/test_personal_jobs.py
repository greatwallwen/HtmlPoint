from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from course_helper.cards import create_review_task
from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.composition import canonical_digest
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.personal_course import PersonalCourseRequest
from course_helper.domain.sources import (
    ChunkLocator,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
)
from course_helper.import_pipeline import persist_governed_import
from course_helper.jobs import (
    JobSpec,
    PersonalCourseCreateJob,
    PersonalCourseResolveJob,
    PersonalCourseStatusJob,
    WorkerRuntimeConfig,
    personal_course_create_request_digest,
    personal_course_resolve_request_digest,
)
from course_helper.personal_orchestrator import (
    create_personal_course_run,
    resume_personal_course,
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
    assert outcome.evidence.output_summary == {
        "publicStatus": "creating",
        "attentionCount": 0,
    }
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


def test_recommended_knowledge_resolution_closes_review_and_reaches_ready(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    actor = ActorRef(actor_type="human", actor_id="personal-user")
    source = SourceAssetVersion(
        logical_id="source-personal-resolution",
        version_id="source-personal-resolution-v1",
        revision=1,
        content_digest=hashlib.sha256(b"personal-resolution").hexdigest(),
        created_at=NOW,
        created_by=actor,
        locator=SourceLocator(root_id="fixture", relative_path="resolution.md"),
        display_name="resolution.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=64,
        extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id="chunk-personal-resolution-v1",
        source_version_id=source.version_id,
        ordinal=0,
        modality="text",
        language="zh-CN",
        normalized_text="用真实资料完成个人 AI 工作流课程。",
        content_digest=hashlib.sha256(b"personal-resolution-chunk").hexdigest(),
        locator=ChunkLocator(
            kind="markdown-section",
            ast_path=(1,),
            heading_path=("个人 AI 工作流",),
        ),
        heading="个人 AI 工作流",
    )
    extraction = ExtractionResult(
        source=source,
        chunks=(chunk,),
        evidence=EvidenceObject(
            evidence_id="evidence-personal-resolution-extract",
            kind="extraction",
            subject_version_id=source.version_id,
            status="verified",
            producer="personal-jobs-tests",
            started_at=NOW,
            finished_at=NOW,
        ),
    )
    with KnowledgeCatalog.open(config.database_path) as catalog:
        catalog.insert_source(source)
        with catalog.atomic_write():
            imported = persist_governed_import(catalog, extraction=extraction, actor=actor)
        create_review_task(
            catalog,
            kind="manual-review",
            subject_version_id=imported.candidate_card_version_ids[0],
            created_at=NOW,
            created_by=actor,
        )

    request = PersonalCourseRequest(
        request_id="personal-request-" + "e" * 32,
        prompt="为个人讲师制作 AI 工作流课程",
        source_version_ids=(source.version_id,),
        created_at=NOW,
        requested_by=actor,
    )
    queued = create_personal_course_run(config, request, actor)
    attention = resume_personal_course(config, queued.run_id, actor)
    assert attention.status == "needs_attention"
    assert attention.attention_bundle is not None
    attention_digest = canonical_digest(attention.attention_bundle)
    action = attention.attention_bundle.items[0].recommended_action
    resolve = PersonalCourseResolveJob.model_validate(
        {
            "type": "personal_course_resolve",
            "operationId": "personal-resolution-operation",
            "requestDigest": personal_course_resolve_request_digest(
                run_id=attention.run_id,
                expected_attention_digest=attention_digest,
                action=action,
            ),
            "actor": ACTOR_JSON,
            "runId": attention.run_id,
            "expectedAttentionDigest": attention_digest,
            "action": action,
        }
    )

    resumed = run_personal_job(resolve, config, RecordingSupervisor())
    ready = resume_personal_course(config, attention.run_id, actor)

    assert resumed.status_code == 202
    assert ready.status == "ready"
    with KnowledgeCatalog.open_read_only(config.database_path) as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_task_current WHERE current_status = 'open' "
            "AND task_id IN (SELECT task_id FROM review_tasks WHERE subject_version_id = ?)",
            (imported.candidate_card_version_ids[0],),
        ).fetchone() == (0,)
