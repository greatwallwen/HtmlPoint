from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.personal_course import PersonalCourseRequest
from course_helper.domain.sources import (
    ChunkLocator,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
)
from course_helper.import_pipeline import persist_governed_import
from course_helper.jobs import WorkerRuntimeConfig
from course_helper import personal_orchestrator as orchestrator
from course_helper.personal_orchestrator import (
    VisualCandidate,
    choose_visual,
    create_personal_course_run,
    resume_personal_course,
)


NOW = datetime(2026, 7, 21, 3, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="human", actor_id="personal-author")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "personal-course.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
    )


def _seed_source(config: WorkerRuntimeConfig) -> str:
    source = SourceAssetVersion(
        logical_id="source-personal-workflow",
        version_id="source-personal-workflow-v1",
        revision=1,
        content_digest=_digest("source-personal-workflow-v1"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="workflow.md"),
        display_name="workflow.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=128,
        extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id="chunk-personal-workflow-v1",
        source_version_id=source.version_id,
        ordinal=0,
        modality="text",
        language="zh-CN",
        normalized_text="用真实来源设计、执行并复盘个人 AI 工作流。",
        content_digest=_digest("chunk-personal-workflow-v1"),
        locator=ChunkLocator(
            kind="markdown-section",
            ast_path=(1,),
            heading_path=("个人 AI 工作流实战",),
        ),
        heading="个人 AI 工作流实战",
    )
    extraction = ExtractionResult(
        source=source,
        chunks=(chunk,),
        evidence=EvidenceObject(
            evidence_id="evidence-personal-workflow-extract",
            kind="extraction",
            subject_version_id=source.version_id,
            status="verified",
            producer="personal-orchestrator-tests",
            started_at=NOW,
            finished_at=NOW,
        ),
    )
    with KnowledgeCatalog.open(config.database_path) as catalog:
        catalog.insert_source(source)
        with catalog.atomic_write():
            persist_governed_import(catalog, extraction=extraction, actor=ACTOR)
    return source.version_id


def _request(source_version_id: str) -> PersonalCourseRequest:
    return PersonalCourseRequest(
        request_id="personal-request-" + "a" * 32,
        prompt="为个人讲师制作 60 分钟 AI 工作流实战课",
        source_version_ids=(source_version_id,),
        created_at=NOW,
        requested_by=ACTOR,
    )


def test_one_request_reaches_ready_without_manual_card_or_visual_steps(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    request = _request(_seed_source(config))

    queued = create_personal_course_run(config, request, ACTOR)
    ready = resume_personal_course(config, queued.run_id, ACTOR)

    assert ready.status == "ready"
    assert ready.result is not None
    assert ready.result.title == "个人 AI 工作流实战"
    assert ready.result.chapter_count > 0
    assert ready.attention_bundle is None


def test_restart_resumes_persisted_work_without_duplicate_publication(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    request = _request(_seed_source(config))
    queued = create_personal_course_run(config, request, ACTOR)

    interrupted = resume_personal_course(
        config,
        queued.run_id,
        ACTOR,
        stop_after_status="organizing_knowledge",
    )
    assert interrupted.status == "organizing_knowledge"

    committed_without_transition = orchestrator._organize_phase(
        config, interrupted, ACTOR
    )
    assert committed_without_transition.next_status == "composing"

    ready = resume_personal_course(config, queued.run_id, ACTOR)
    replayed = resume_personal_course(config, queued.run_id, ACTOR)
    assert replayed == ready
    assert ready.status == "ready"
    assert ready.result is not None

    with KnowledgeCatalog.open_read_only(config.database_path) as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM course_versions WHERE logical_id = ? AND payload_json LIKE ?",
            ("course-" + queued.run_id, '%"status":"published"%'),
        ).fetchone() == (1,)


def test_more_than_fifty_sources_is_rejected_before_run_creation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    request = PersonalCourseRequest(
        request_id="personal-request-" + "b" * 32,
        prompt="组一门课",
        source_version_ids=tuple(f"source-{index}" for index in range(51)),
        created_at=NOW,
        requested_by=ACTOR,
    )

    try:
        create_personal_course_run(config, request, ACTOR)
    except ValueError as error:
        assert "50" in str(error)
    else:
        raise AssertionError("source ceiling was not enforced")


def test_visual_choice_prefers_verified_source_then_data_then_network() -> None:
    network = VisualCandidate("network", 2, 10_000, True, True)
    data = VisualCandidate("data", 1, 5_000, True, True)
    source = VisualCandidate("source", 0, 1_000, True, True)
    unsafe_source = VisualCandidate("unsafe", 0, 100_000, True, False)

    assert choose_visual((network, data, source, unsafe_source)) == source
    assert choose_visual((network, data, unsafe_source)) == data
    assert choose_visual((unsafe_source,)) is None
