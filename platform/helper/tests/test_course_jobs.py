from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from course_helper.cards import VOCABULARY_VERSION_ID, publish_card, seed_vocabulary
from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    TagAssignment,
)
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.jobs import (
    BoundedJobRunner,
    CourseComposeJob,
    CourseOutlineConfirmJob,
    HttpActor,
    JobSpec,
    KnowledgeIndexJob,
    WorkerRuntimeConfig,
    _run_course_compose,
    _run_course_outline_confirm,
    _run_knowledge_index,
    course_compose_request_digest,
    course_outline_confirm_request_digest,
    knowledge_index_request_digest,
)
from course_helper.operations import (
    IndexOutboxItem,
    OperationMutationResult,
    OperationRequest,
    run_operation,
)


NOW = datetime(2026, 7, 19, 6, 0, tzinfo=timezone.utc)
SESSION = "session-" + "c" * 64
ACTOR_JSON = {"actorType": "human", "actorId": "course-author"}
ACTOR = ActorRef(actor_type="human", actor_id="course-author")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "course-jobs.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
    )


def _seed_published_card_and_outbox(config: WorkerRuntimeConfig) -> str:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        seed_vocabulary(catalog)
        source = SourceAssetVersion(
            logical_id="source-course-logical",
            version_id="source-course-v1",
            revision=1,
            content_digest=_digest("source-course-v1"),
            created_at=NOW,
            created_by=ACTOR,
            locator=SourceLocator(root_id="fixture", relative_path="course.md"),
            display_name="course.md",
            source_kind="markdown",
            media_type="text/markdown",
            byte_size=64,
            extraction_status="parsed",
        )
        catalog.insert_source(source)
        chunk = ExtractedChunk(
            chunk_id="chunk-course-v1",
            source_version_id=source.version_id,
            ordinal=0,
            modality="text",
            language="zh-CN",
            normalized_text="解释可靠的 AI 基础概念",
            content_digest=_digest("chunk-course-v1"),
            locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
        )
        catalog.insert_chunk(chunk)
        candidate = KnowledgeCardVersion(
            logical_id="card-course-logical",
            version_id="card-course-v1",
            revision=1,
            content_digest=_digest("card-course-v1"),
            created_at=NOW,
            created_by=ACTOR,
            main_type_id="concept",
            title="AI 基础概念",
            learning_objective="解释可靠的 AI 基础概念",
            content_ast=(
                CardContentNode(type="paragraph", text="解释可靠的 AI 基础概念"),
            ),
            suggested_minutes=10,
            vocabulary_version_id=VOCABULARY_VERSION_ID,
            tag_assignments=tuple(
                TagAssignment(
                    vocabulary_version_id=VOCABULARY_VERSION_ID,
                    dimension_id=tag_id.split(":", 1)[0],
                    tag_id=tag_id,
                )
                for tag_id in (
                    "topic:ai-foundations",
                    "audience:learner",
                    "difficulty:beginner",
                )
            ),
            chunk_citations=(
                ChunkCitation(chunk_id=chunk.chunk_id, source_version_id=source.version_id),
            ),
            status="review",
        )
        published = publish_card(candidate, catalog)
        request = OperationRequest(
            operation_id="seed-course-index-operation",
            request_digest=_digest("seed-course-index-operation"),
            actor=ACTOR,
            session_id=SESSION,
        )
        outbox_id = "index-outbox-course-v1"
        run_operation(
            catalog,
            request,
            lambda: OperationMutationResult(
                result_refs={},
                item_outcomes=(),
                index_outbox=(
                    IndexOutboxItem(
                        outbox_id=outbox_id,
                        card_version_id=published.version_id,
                        action="upsert",
                    ),
                ),
            ),
            clock=lambda: NOW,
        )
        return outbox_id


def _index_job(outbox_id: str) -> KnowledgeIndexJob:
    return KnowledgeIndexJob.model_validate(
        {
            "type": "knowledge_index",
            "expectedOutboxId": outbox_id,
            "operationId": "http-course-index",
            "requestDigest": knowledge_index_request_digest(outbox_id),
            "actor": ACTOR_JSON,
        }
    )


def _compose_job(snapshot_id: str) -> CourseComposeJob:
    payload = {
        "type": "course_compose",
        "requirement": {
            "requirementId": "requirement-course-v1",
            "title": "个人 AI 基础课程",
            "audience": "个人学习者",
            "learningGoals": ["解释可靠的 AI 基础概念"],
            "durationMinutes": 10,
            "requiredTagIds": ["topic:ai-foundations"],
            "excludedTagIds": [],
            "usageScope": "internal",
        },
        "options": {
            "audienceTagId": "audience:learner",
            "difficultyTagId": "difficulty:beginner",
            "indexSnapshotId": snapshot_id,
            "includeCardVersionIds": [],
            "excludeCardVersionIds": [],
            "requireVisualRefs": False,
            "requireDatasetRefs": False,
        },
        "outlineLogicalId": "outline-course-logical",
        "outlineVersionId": "outline-course-v1",
        "outlineRevision": 1,
        "operationId": "http-course-compose",
        "actor": ACTOR_JSON,
    }
    payload["requestDigest"] = course_compose_request_digest(
        requirement=payload["requirement"],
        options=payload["options"],
        outline_logical_id=payload["outlineLogicalId"],
        outline_version_id=payload["outlineVersionId"],
        outline_revision=payload["outlineRevision"],
    )
    return CourseComposeJob.model_validate(payload)


def _confirm_job(compose_result: dict[str, object]) -> CourseOutlineConfirmJob:
    summary = compose_result["confirmationSummary"]
    assert isinstance(summary, dict)
    payload = {
        "type": "course_outline_confirm",
        "confirmationId": "confirmation-course-v1",
        "requirementId": compose_result["requirementId"],
        "outlineVersionId": compose_result["outlineVersionId"],
        "expectedOutlineDigest": compose_result["outlineDigest"],
        "confirmationDigest": summary["confirmationDigest"],
        "courseLogicalId": "course-logical-v1",
        "courseVersionId": "course-confirmed-v1",
        "courseRevision": 1,
        "operationId": "http-course-confirm",
        "actor": ACTOR_JSON,
    }
    payload["requestDigest"] = course_outline_confirm_request_digest(
        confirmation_id=payload["confirmationId"],
        requirement_id=payload["requirementId"],
        outline_version_id=payload["outlineVersionId"],
        expected_outline_digest=payload["expectedOutlineDigest"],
        confirmation_digest=payload["confirmationDigest"],
        course_logical_id=payload["courseLogicalId"],
        course_version_id=payload["courseVersionId"],
        course_revision=payload["courseRevision"],
    )
    return CourseOutlineConfirmJob.model_validate(payload)


def test_course_job_contracts_are_strict_lower_camel_digest_bound_and_path_free() -> None:
    index = _index_job("index-outbox-course-v1")
    compose = _compose_job("index-snapshot-" + "1" * 32)
    confirm = _confirm_job(
        {
            "requirementId": "requirement-course-v1",
            "outlineVersionId": "outline-course-v1",
            "outlineDigest": "2" * 64,
            "confirmationSummary": {"confirmationDigest": "3" * 64},
        }
    )
    adapter = TypeAdapter(JobSpec)
    for job in (index, compose, confirm):
        dumped = job.model_dump(mode="json", by_alias=True)
        assert adapter.validate_python(dumped) == job
        assert "Path" not in str(dumped)
        assert "Url" not in str(dumped)
        assert "Sql" not in str(dumped)

    bad = compose.model_dump(mode="json", by_alias=True)
    bad["requestDigest"] = "f" * 64
    try:
        adapter.validate_python(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("stale compose digest was accepted")


def test_index_compose_confirm_replay_is_atomic_bounded_and_recoverable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    outbox_id = _seed_published_card_and_outbox(config)

    indexed, _ = _run_knowledge_index(_index_job(outbox_id), config, SESSION)
    assert indexed["operationStatus"] == "committed"
    assert indexed["indexState"] == "degraded"
    assert indexed["retrievalMode"] == "fts-degraded"
    assert indexed["consumedOutboxId"] == outbox_id
    snapshot_id = indexed["indexSnapshotId"]
    assert isinstance(snapshot_id, str)

    compose_job = _compose_job(snapshot_id)
    composed, _ = _run_course_compose(compose_job, config, SESSION)
    replayed, _ = _run_course_compose(compose_job, config, SESSION)
    assert replayed == composed
    assert composed["operationStatus"] == "committed"
    assert composed["blockingGaps"] == []
    assert composed["indexSnapshotId"] == snapshot_id
    assert len(composed["outline"]["chapters"]) == 1
    assert "contentAst" not in str(composed)
    assert "normalizedText" not in str(composed)
    assert "relativePath" not in str(composed)

    runner = BoundedJobRunner(config, monotonic=lambda: 1.0)
    recovered = runner._recover_committed_operation(
        compose_job,
        session_id=SESSION,
        ceiling={"timeoutSeconds": 30},
        started_at=NOW,
        started_tick=0.0,
        exit_code=0,
    )
    assert recovered is not None
    assert recovered.status_code == 200
    assert recovered.result == composed
    assert recovered.evidence.checks[0].code == "operation-recovery"

    confirm_job = _confirm_job(composed)
    confirmed, _ = _run_course_outline_confirm(confirm_job, config, SESSION)
    confirm_replay, _ = _run_course_outline_confirm(confirm_job, config, SESSION)
    assert confirm_replay == confirmed
    assert confirmed["operationStatus"] == "committed"
    assert confirmed["courseVersionId"] == "course-confirmed-v1"
    assert confirmed["courseStatus"] == "confirmed"

    with KnowledgeCatalog.open(config.database_path) as catalog:
        assert catalog.get_course_requirement("requirement-course-v1") is not None
        assert catalog.get_course_outline("outline-course-v1") is not None
        stored = catalog.get_course_version("course-confirmed-v1")
        assert stored is not None
        assert stored.payload.status == "confirmed"
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id IN (?, ?, ?)",
            (
                "http-course-index",
                "http-course-compose",
                "http-course-confirm",
            ),
        ).fetchone()[0] == 3


async def _connected() -> bool:
    return False


def test_real_spawn_runs_the_allowlisted_course_composition_slice(tmp_path: Path) -> None:
    config = _config(tmp_path)
    outbox_id = _seed_published_card_and_outbox(config)
    runner = BoundedJobRunner(config)

    indexed = asyncio.run(
        runner.run(_index_job(outbox_id), disconnected=_connected, session_id=SESSION)
    )
    assert indexed.status_code == 200
    composed = asyncio.run(
        runner.run(
            _compose_job(indexed.result["indexSnapshotId"]),
            disconnected=_connected,
            session_id=SESSION,
        )
    )
    assert composed.status_code == 200
    confirmed = asyncio.run(
        runner.run(
            _confirm_job(dict(composed.result)),
            disconnected=_connected,
            session_id=SESSION,
        )
    )
    assert confirmed.status_code == 200
    assert confirmed.result["courseStatus"] == "confirmed"
    serialized = (
        str(indexed.result)
        + str(composed.result)
        + str(confirmed.result)
        + indexed.evidence.model_dump_json()
        + composed.evidence.model_dump_json()
        + confirmed.evidence.model_dump_json()
    )
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
