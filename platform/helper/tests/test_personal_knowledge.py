from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from course_helper.cards import create_review_task
from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.sources import (
    ChunkLocator,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.import_pipeline import persist_governed_import
from course_helper.personal_knowledge import organize_personal_knowledge


NOW = datetime(2026, 7, 21, 2, 30, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="personal-knowledge-tests")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extraction(*, with_unknown_visual: bool = False) -> ExtractionResult:
    source_kind = "pptx" if with_unknown_visual else "markdown"
    source_id = f"source-{source_kind}-v1"
    visual_id = "visual-unknown-v1"
    chunk = ExtractedChunk(
        chunk_id=f"chunk-{source_kind}-v1",
        source_version_id=source_id,
        ordinal=0,
        modality="slide" if with_unknown_visual else "text",
        language="zh-CN",
        normalized_text="用真实来源约束课程内容，并保留可核验引用。",
        content_digest=_digest(f"chunk-{source_kind}"),
        locator=(
            ChunkLocator(kind="pptx-slide", slide_number=1)
            if with_unknown_visual
            else ChunkLocator(
                kind="markdown-section",
                ast_path=(1,),
                heading_path=("用来源约束生成课程",),
            )
        ),
        heading="用来源约束生成课程",
        media_version_ids=(visual_id,) if with_unknown_visual else (),
    )
    source = SourceAssetVersion(
        logical_id=f"source-{source_kind}",
        version_id=source_id,
        revision=1,
        content_digest=_digest(f"source-{source_kind}"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path=f"demo.{source_kind}"),
        display_name=f"demo.{source_kind}",
        source_kind=source_kind,
        media_type="application/vnd.test" if with_unknown_visual else "text/markdown",
        byte_size=100,
        extraction_status="parsed",
    )
    visuals = (
        VisualAssetVersion(
            logical_id="visual-unknown",
            version_id=visual_id,
            revision=1,
            content_digest=_digest("unknown-visual"),
            created_at=NOW,
            created_by=ACTOR,
            media_type="image/png",
            width=800,
            height=600,
            alt_text="来源图形",
            source_locator=ChunkLocator(
                kind="pptx-slide",
                slide_number=1,
                relationship_id="rId1",
            ),
            license_status="unknown",
            authenticity="source-provided",
            derived_from_version_ids=(source_id,),
        ),
    ) if with_unknown_visual else ()
    evidence = EvidenceObject(
        evidence_id=f"evidence-{source_kind}-extract",
        kind="extraction",
        subject_version_id=source_id,
        status="verified",
        producer="personal-knowledge-tests",
        started_at=NOW,
        finished_at=NOW,
    )
    return ExtractionResult(
        source=source,
        chunks=(chunk,),
        visuals=visuals,
        evidence=evidence,
    )


@pytest.fixture
def catalog(tmp_path: Path):
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as opened:
        yield opened


def _persist_candidate(catalog: KnowledgeCatalog, extraction: ExtractionResult) -> str:
    catalog.insert_source(extraction.source)
    with catalog.atomic_write():
        result = persist_governed_import(
            catalog,
            extraction=extraction,
            actor=ACTOR,
        )
    return result.candidate_card_version_ids[0]


def test_source_bound_nonconflicting_card_is_named_tagged_and_published(
    catalog: KnowledgeCatalog,
) -> None:
    extraction = _extraction()
    _persist_candidate(catalog, extraction)

    result = organize_personal_knowledge(
        catalog,
        (extraction.source.version_id,),
        ACTOR,
    )

    assert len(result.published_card_version_ids) == 1
    card = catalog.get_card(result.published_card_version_ids[0])
    assert card is not None
    assert card.title == "用来源约束生成课程"
    assert {tag.dimension_id for tag in card.tag_assignments} >= {
        "topic",
        "skill",
        "source-type",
    }
    assert result.attention_items == ()
    assert catalog.connection.execute(
        "SELECT 1 FROM evidence WHERE evidence_id = ?",
        (result.evidence.evidence_id,),
    ).fetchone() == (1,)
    assert organize_personal_knowledge(
        catalog,
        (extraction.source.version_id,),
        ACTOR,
    ) == result


def test_conflict_and_unknown_visual_license_share_one_attention_result(
    catalog: KnowledgeCatalog,
) -> None:
    extraction = _extraction(with_unknown_visual=True)
    card_id = _persist_candidate(catalog, extraction)
    create_review_task(
        catalog,
        kind="manual-review",
        subject_version_id=card_id,
        created_at=NOW,
        created_by=ACTOR,
    )

    result = organize_personal_knowledge(
        catalog,
        (extraction.source.version_id,),
        ACTOR,
    )

    assert result.published_card_version_ids == ()
    assert {item.kind for item in result.attention_items} == {
        "knowledge-conflict",
        "visual-license",
    }
    assert len({item.attention_id for item in result.attention_items}) == 2
