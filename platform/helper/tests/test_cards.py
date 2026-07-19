from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.knowledge import (
    ChunkCitation,
    DatasetReference,
    TagAssignment,
    VisualReference,
)
from course_helper.domain.sources import (
    ChunkLocator,
    DatasetAssetVersion,
    DatasetColumn,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
    VisualAssetVersion,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="cards-tests")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def catalog(tmp_path: Path) -> KnowledgeCatalog:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as opened:
        yield opened


def _pptx_extraction(slide_numbers: tuple[int, ...]) -> ExtractionResult:
    source_version_id = "source-version-pptx"
    chunks = tuple(
        ExtractedChunk(
            chunk_id=f"slide-chunk-{slide_number}",
            source_version_id=source_version_id,
            ordinal=index,
            modality="slide",
            language="zh-CN",
            normalized_text=f"Shared unit\nSlide {slide_number} evidence",
            content_digest=_digest(f"slide-{slide_number}"),
            locator=ChunkLocator(kind="pptx-slide", slide_number=slide_number),
            breadcrumb=("Shared unit",),
            heading="Shared unit",
            slide_text=f"Shared unit\nSlide {slide_number} evidence",
        )
        for index, slide_number in enumerate(slide_numbers)
    )
    source = SourceAssetVersion(
        logical_id="source-logical-pptx",
        version_id=source_version_id,
        revision=1,
        content_digest=_digest("pptx-source"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="demo.pptx"),
        display_name="demo.pptx",
        source_kind="pptx",
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        byte_size=100,
        extraction_status="parsed",
    )
    evidence = EvidenceObject(
        evidence_id="pptx-extraction-evidence",
        kind="extraction",
        subject_version_id=source_version_id,
        status="verified",
        producer="cards-tests",
        started_at=NOW,
        finished_at=NOW,
    )
    return ExtractionResult(source=source, chunks=chunks, evidence=evidence)


def _markdown_extraction() -> ExtractionResult:
    source_version_id = "source-version-markdown"
    definitions = (
        ("Unit", (1,), ("Unit",)),
        ("Method", (1, 5), ("Unit", "Method")),
        ("Example", (1, 5, 9), ("Unit", "Method", "Example")),
        ("Next unit", (20,), ("Next unit",)),
    )
    chunks = tuple(
        ExtractedChunk(
            chunk_id=f"markdown-chunk-{index}",
            source_version_id=source_version_id,
            ordinal=index,
            modality="text",
            language="zh-CN",
            normalized_text=f"{heading}\nSource text {index}",
            content_digest=_digest(f"markdown-{index}"),
            locator=ChunkLocator(
                kind="markdown-section",
                ast_path=ast_path,
                heading_path=heading_path,
            ),
            breadcrumb=heading_path,
            heading=heading,
        )
        for index, (heading, ast_path, heading_path) in enumerate(definitions)
    )
    source = SourceAssetVersion(
        logical_id="source-logical-markdown",
        version_id=source_version_id,
        revision=1,
        content_digest=_digest("markdown-source"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="demo.md"),
        display_name="demo.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=100,
        extraction_status="parsed",
    )
    evidence = EvidenceObject(
        evidence_id="markdown-extraction-evidence",
        kind="extraction",
        subject_version_id=source_version_id,
        status="verified",
        producer="cards-tests",
        started_at=NOW,
        finished_at=NOW,
    )
    return ExtractionResult(source=source, chunks=chunks, evidence=evidence)


def _reviewed_card(
    *,
    tag_ids: tuple[str, ...] = ("difficulty:beginner",),
    logical_id: str | None = None,
    version_id: str | None = None,
):
    from course_helper.cards import VOCABULARY_VERSION_ID, build_candidates

    candidate = build_candidates(_pptx_extraction((3,)))[0]
    assignments = tuple(
        TagAssignment(
            vocabulary_version_id=VOCABULARY_VERSION_ID,
            dimension_id=tag_id.split(":", 1)[0],
            tag_id=tag_id,
        )
        for tag_id in tag_ids
    )
    updates: dict[str, object] = {
        "status": "review",
        "tag_assignments": assignments,
    }
    if logical_id is not None:
        updates["logical_id"] = logical_id
    if version_id is not None:
        updates["version_id"] = version_id
    return candidate.model_copy(update=updates)


def _persist_extraction(
    catalog: KnowledgeCatalog,
    extraction: ExtractionResult,
) -> None:
    catalog.insert_source(extraction.source)
    for chunk in extraction.chunks:
        catalog.insert_chunk(chunk)
    for visual in extraction.visuals:
        catalog.insert_visual(visual)
    for dataset in extraction.datasets:
        catalog.insert_dataset(dataset)


def test_candidate_groups_at_most_three_adjacent_pptx_slides() -> None:
    from course_helper.cards import build_candidates

    candidates = build_candidates(_pptx_extraction((3, 4, 5, 6)))

    assert [
        tuple(int(citation.chunk_id.rsplit("-", 1)[-1]) for citation in card.chunk_citations)
        for card in candidates
    ] == [(3, 4, 5), (6,)]
    assert all(1 <= len(card.chunk_citations) <= 3 for card in candidates)
    assert all(card.status == "draft" for card in candidates)


def test_adjacent_untitled_notes_only_pptx_slides_each_form_one_card() -> None:
    from course_helper.cards import build_candidates

    extraction = _pptx_extraction((3, 4, 5))
    headings = (None, None, "")
    chunks = tuple(
        chunk.model_copy(
            update={
                "heading": heading,
                "breadcrumb": (),
                "slide_text": "",
                "notes_text": f"Presenter notes for slide {slide_number}",
                "normalized_text": f"Presenter notes for slide {slide_number}",
                "content_digest": _digest(f"notes-only-{slide_number}"),
            }
        )
        for chunk, heading, slide_number in zip(
            extraction.chunks,
            headings,
            (3, 4, 5),
            strict=True,
        )
    )

    candidates = build_candidates(extraction.model_copy(update={"chunks": chunks}))

    assert [
        tuple(citation.chunk_id for citation in card.chunk_citations)
        for card in candidates
    ] == [
        ("slide-chunk-3",),
        ("slide-chunk-4",),
        ("slide-chunk-5",),
    ]


def test_markdown_candidate_contains_one_semantic_heading_unit_and_children() -> None:
    from course_helper.cards import build_candidates

    first = build_candidates(_markdown_extraction())
    second = build_candidates(_markdown_extraction())

    assert [
        tuple(citation.chunk_id for citation in card.chunk_citations)
        for card in first
    ] == [
        ("markdown-chunk-0", "markdown-chunk-1", "markdown-chunk-2"),
        ("markdown-chunk-3",),
    ]
    assert second == first
    assert first[0].content_ast[0].text == "Unit\nSource text 0"
    assert first[0].model_config["frozen"] is True


def test_markdown_candidate_uses_explicit_learning_goal_section() -> None:
    from course_helper.cards import build_candidates

    extraction = _markdown_extraction()
    chunks = list(extraction.chunks)
    chunks[1] = chunks[1].model_copy(
        update={
            "heading": "学习目标",
            "normalized_text": "学习目标\n\n理解真实来源与证据链。",
            "content_digest": _digest("explicit-learning-goal"),
        }
    )

    candidate = build_candidates(
        extraction.model_copy(update={"chunks": tuple(chunks)})
    )[0]

    assert candidate.learning_objective == "理解真实来源与证据链"


def test_seed_vocabulary_persists_stable_dimensions_labels_and_aliases(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import VOCABULARY_VERSION_ID, seed_vocabulary

    first = seed_vocabulary(catalog)
    second = seed_vocabulary(catalog)

    assert second == first
    assert first.version_id == VOCABULARY_VERSION_ID
    assert tuple(dimension.id for dimension in first.dimensions) == (
        "topic",
        "audience",
        "difficulty",
        "pedagogy",
        "tool",
        "scenario",
        "dataType",
    )
    rows = catalog.connection.execute(
        "SELECT payload_json FROM tag_values WHERE vocabulary_version_id = ?",
        (first.version_id,),
    ).fetchall()
    assert rows
    assert all(json.loads(row[0])["labels"] for row in rows)
    assert all("aliases" in json.loads(row[0]) for row in rows)
    assert catalog.connection.execute(
        "SELECT count(*) FROM tag_vocabularies"
    ).fetchone()[0] == 1
    assert catalog.connection.execute(
        "SELECT status FROM tag_values WHERE tag_id = ?",
        ("tool:legacy",),
    ).fetchone()[0] == "deprecated"


def test_unknown_tag_blocks_publish_without_partial_state(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    card = _reviewed_card(tag_ids=("topic:invented",))

    with pytest.raises(PublishBlocked, match="unknown tag"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 0


def test_deprecated_tag_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog, deprecated_tag_id="tool:legacy")

    with pytest.raises(PublishBlocked, match="deprecated tag"):
        publish_card(_reviewed_card(tag_ids=("tool:legacy",)), catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_two_values_in_single_cardinality_dimension_block_publish(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    card = _reviewed_card(
        tag_ids=("difficulty:beginner", "difficulty:advanced")
    )

    with pytest.raises(PublishBlocked, match="single-cardinality tag conflict"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_duplicate_tag_assignment_blocks_without_partial_state(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card(
        tag_ids=("difficulty:beginner", "difficulty:beginner")
    )

    with pytest.raises(PublishBlocked, match="duplicate tag assignment"):
        publish_card(card, catalog)

    for table in ("cards", "card_tags", "card_fts", "evidence", "lineage"):
        assert catalog.connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0] == 0


def test_publish_requires_review_status(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    card = _reviewed_card().model_copy(update={"status": "draft"})

    with pytest.raises(PublishBlocked, match="status must be review"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_missing_chunk_citation_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)

    with pytest.raises(PublishBlocked, match="invalid chunk citation"):
        publish_card(_reviewed_card(), catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_fabricated_quoted_text_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card().model_copy(
        update={
            "chunk_citations": (
                ChunkCitation(
                    chunk_id="slide-chunk-3",
                    source_version_id="source-version-pptx",
                    quoted_text="Fabricated statement absent from the source",
                ),
            )
        }
    )

    with pytest.raises(PublishBlocked, match="quoted text"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_duplicate_chunk_citation_blocks_without_partial_state(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    citation = _reviewed_card().chunk_citations[0]
    card = _reviewed_card().model_copy(
        update={"chunk_citations": (citation, citation)}
    )

    with pytest.raises(PublishBlocked, match="duplicate chunk citation"):
        publish_card(card, catalog)

    for table in ("cards", "card_tags", "card_fts", "evidence", "lineage"):
        assert catalog.connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0] == 0


def test_source_backed_card_without_citations_blocks_publish(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    card = _reviewed_card().model_copy(update={"chunk_citations": ()})

    with pytest.raises(PublishBlocked, match="citation is required"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_missing_visual_reference_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card().model_copy(
        update={
            "visual_refs": (
                VisualReference(visual_version_id="missing-visual"),
            )
        }
    )

    with pytest.raises(PublishBlocked, match="invalid visual reference"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_unverified_visual_reference_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    visual = VisualAssetVersion(
        logical_id="unverified-visual-logical",
        version_id="unverified-visual-version",
        revision=1,
        content_digest=_digest("unverified-visual"),
        created_at=NOW,
        created_by=ACTOR,
        media_type="image/png",
        alt_text="Unverified sample",
        license_status="unknown",
        authenticity="unverified",
    )
    catalog.insert_visual(visual)
    card = _reviewed_card().model_copy(
        update={
            "visual_refs": (
                VisualReference(visual_version_id=visual.version_id),
            )
        }
    )

    with pytest.raises(PublishBlocked, match="unverified visual reference"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_duplicate_visual_reference_blocks_without_partial_state(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    visual = VisualAssetVersion(
        logical_id="duplicate-visual-logical",
        version_id="duplicate-visual-version",
        revision=1,
        content_digest=_digest("duplicate-visual"),
        created_at=NOW,
        created_by=ACTOR,
        media_type="image/png",
        alt_text="Verified duplicate fixture",
        license_status="source-provided",
        authenticity="source-provided",
    )
    catalog.insert_visual(visual)
    reference = VisualReference(visual_version_id=visual.version_id, purpose="hero")
    card = _reviewed_card().model_copy(
        update={"visual_refs": (reference, reference)}
    )

    with pytest.raises(PublishBlocked, match="duplicate visual reference"):
        publish_card(card, catalog)

    for table in ("cards", "card_tags", "card_fts", "evidence", "lineage"):
        assert catalog.connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0] == 0


def test_missing_dataset_reference_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card().model_copy(
        update={
            "dataset_refs": (
                DatasetReference(dataset_version_id="missing-dataset"),
            )
        }
    )

    with pytest.raises(PublishBlocked, match="invalid dataset reference"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_dataset_needing_review_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    dataset = DatasetAssetVersion(
        logical_id="dataset-logical-review",
        version_id="dataset-version-review",
        revision=1,
        content_digest=_digest("dataset-review"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="review.csv"),
        format="csv",
        row_count=1,
        columns=(DatasetColumn(name="value", data_type="INTEGER", nullable=False),),
        grain="one row per example",
        review_status="needs-review",
        evidence=EvidenceObject(
            evidence_id="dataset-review-evidence",
            kind="dataset-profile",
            status="warning",
            producer="cards-tests",
            started_at=NOW,
            finished_at=NOW,
        ),
    )
    catalog.insert_dataset(dataset)
    card = _reviewed_card().model_copy(
        update={
            "dataset_refs": (
                DatasetReference(dataset_version_id=dataset.version_id),
            )
        }
    )

    with pytest.raises(PublishBlocked, match="dataset reference is not ready"):
        publish_card(card, catalog)

    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0


def test_duplicate_dataset_reference_blocks_without_partial_state(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    dataset = DatasetAssetVersion(
        logical_id="duplicate-dataset-logical",
        version_id="duplicate-dataset-version",
        revision=1,
        content_digest=_digest("duplicate-dataset"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="duplicate.csv"),
        format="csv",
        row_count=1,
        columns=(DatasetColumn(name="value", data_type="INTEGER", nullable=False),),
        grain="one row per example",
        review_status="ready",
        evidence=EvidenceObject(
            evidence_id="duplicate-dataset-evidence",
            kind="dataset-profile",
            status="verified",
            producer="cards-tests",
            started_at=NOW,
            finished_at=NOW,
        ),
    )
    catalog.insert_dataset(dataset)
    reference = DatasetReference(dataset_version_id=dataset.version_id)
    card = _reviewed_card().model_copy(
        update={"dataset_refs": (reference, reference)}
    )

    with pytest.raises(PublishBlocked, match="duplicate dataset reference"):
        publish_card(card, catalog)

    for table in ("cards", "card_tags", "card_fts", "evidence", "lineage"):
        assert catalog.connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0] == 0


def test_verified_visual_and_ready_dataset_publish_with_uses_lineage(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    visual = VisualAssetVersion(
        logical_id="verified-visual-logical",
        version_id="verified-visual-version",
        revision=1,
        content_digest=_digest("verified-visual"),
        created_at=NOW,
        created_by=ACTOR,
        media_type="image/png",
        alt_text="Verified source visual",
        license_status="source-provided",
        authenticity="source-provided",
    )
    dataset = DatasetAssetVersion(
        logical_id="dataset-logical-ready",
        version_id="dataset-version-ready",
        revision=1,
        content_digest=_digest("dataset-ready"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="ready.csv"),
        format="csv",
        row_count=1,
        columns=(DatasetColumn(name="value", data_type="INTEGER", nullable=False),),
        grain="one row per example",
        review_status="ready",
        evidence=EvidenceObject(
            evidence_id="dataset-ready-evidence",
            kind="dataset-profile",
            status="verified",
            producer="cards-tests",
            started_at=NOW,
            finished_at=NOW,
        ),
    )
    catalog.insert_visual(visual)
    catalog.insert_dataset(dataset)
    card = _reviewed_card().model_copy(
        update={
            "visual_refs": (
                VisualReference(visual_version_id=visual.version_id, purpose="hero"),
                VisualReference(visual_version_id=visual.version_id, purpose="evidence"),
            ),
            "dataset_refs": (
                DatasetReference(
                    dataset_version_id=dataset.version_id,
                    activity_spec_ids=("overview",),
                ),
                DatasetReference(
                    dataset_version_id=dataset.version_id,
                    activity_spec_ids=("practice",),
                ),
            ),
        }
    )

    published = publish_card(card, catalog)

    assert published.status == "published"
    assert [reference.purpose for reference in published.visual_refs] == [
        "hero",
        "evidence",
    ]
    assert [reference.activity_spec_ids for reference in published.dataset_refs] == [
        ("overview",),
        ("practice",),
    ]
    assert catalog.connection.execute(
        "SELECT relation, to_version_id FROM lineage ORDER BY relation, to_version_id"
    ).fetchall() == [
        ("cites", "slide-chunk-3"),
        ("uses", dataset.version_id),
        ("uses", visual.version_id),
    ]


def test_open_blocking_review_task_blocks_publish(catalog: KnowledgeCatalog) -> None:
    from course_helper.cards import (
        PublishBlocked,
        create_review_task,
        publish_card,
        seed_vocabulary,
    )

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card()
    catalog.insert_card(card)
    task = create_review_task(
        catalog,
        kind="manual-review",
        subject_version_id=card.version_id,
        blocking=True,
    )

    assert task.status == "open"
    assert task.model_config["frozen"] is True
    with pytest.raises(PublishBlocked, match="open blocking review task"):
        publish_card(card, catalog)
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 1


def test_valid_reviewed_card_publishes_with_evidence_and_citation_lineage(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))

    published = publish_card(_reviewed_card(), catalog)

    assert published.status == "published"
    assert published.model_config["frozen"] is True
    stored = catalog.connection.execute(
        "SELECT status, payload_json FROM cards WHERE version_id = ?",
        (published.version_id,),
    ).fetchone()
    assert stored[0] == "published"
    assert json.loads(stored[1]) == published.model_dump(mode="json", exclude_none=True)
    assert catalog.connection.execute("SELECT count(*) FROM card_tags").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 1
    assert catalog.connection.execute(
        "SELECT chunk_text FROM card_fts WHERE version_id = ?",
        (published.version_id,),
    ).fetchone()[0] == "Shared unit\nSlide 3 evidence"
    publish_evidence = catalog.connection.execute(
        "SELECT payload_json FROM evidence WHERE kind = 'publish'"
    ).fetchone()
    assert json.loads(publish_evidence[0])["subject_version_id"] == published.version_id
    assert catalog.connection.execute(
        "SELECT relation, from_version_id, to_version_id FROM lineage"
    ).fetchall() == [("cites", published.version_id, "slide-chunk-3")]


def test_publish_rolls_back_every_row_when_lineage_write_fails(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    catalog.connection.execute(
        """
        CREATE TRIGGER reject_publish_lineage
        BEFORE INSERT ON lineage
        BEGIN
            SELECT RAISE(ABORT, 'forced lineage failure');
        END
        """
    )
    catalog.connection.commit()

    with pytest.raises(PublishBlocked, match="card publication failed"):
        publish_card(_reviewed_card(), catalog)

    assert not catalog.connection.in_transaction
    for table in ("cards", "card_tags", "card_fts", "evidence", "lineage"):
        assert catalog.connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0] == 0


def test_republishing_same_deterministic_version_is_idempotent(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card()

    first = publish_card(card, catalog)
    second = publish_card(card, catalog)

    assert second == first
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 1
    assert catalog.connection.execute(
        "SELECT count(*) FROM lineage WHERE relation = 'deduplicates'"
    ).fetchone()[0] == 0


def test_published_return_value_is_directly_idempotent(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    published = publish_card(_reviewed_card(), catalog)

    repeated = publish_card(published, catalog)

    assert repeated == published
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 1


def test_same_logical_new_content_gets_next_revision_without_sqlite_error(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    first = publish_card(_reviewed_card(), catalog)
    changed = _reviewed_card(version_id="same-logical-second-version").model_copy(
        update={
            "title": "Shared unit revised",
            "learning_objective": "Review the revised source-backed unit",
        }
    )

    second = publish_card(changed, catalog)

    assert second.logical_id == first.logical_id
    assert second.version_id == changed.version_id
    assert second.revision == 2
    assert second.supersedes_version_id == first.version_id
    assert catalog.connection.execute(
        """
        SELECT cards.revision, lifecycle.status, cards.status
        FROM cards
        JOIN card_lifecycle_current AS lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE cards.logical_id = ?
        ORDER BY cards.revision
        """,
        (first.logical_id,),
    ).fetchall() == [
        (1, "superseded", "published"),
        (2, "published", "published"),
    ]
    assert catalog.connection.execute(
        "SELECT version_id FROM card_fts ORDER BY version_id"
    ).fetchall() == [(second.version_id,)]
    superseded_payload = catalog.connection.execute(
        "SELECT payload_json FROM cards WHERE version_id = ?",
        (first.version_id,),
    ).fetchone()[0]
    assert json.loads(superseded_payload)["status"] == "published"


def test_publish_card_supersedes_a_suspended_published_revision(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary
    from course_helper.lifecycle import append_card_lifecycle_event

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    first = publish_card(_reviewed_card(), catalog)
    append_card_lifecycle_event(
        catalog.connection,
        card_version_id=first.version_id,
        event_id="suspend-before-publish-card-revision",
        request_digest="c" * 64,
        event_type="suspend",
        occurred_at=NOW,
        actor_id="cards-tests",
    )
    changed = _reviewed_card(version_id="publish-card-after-suspension").model_copy(
        update={"title": "Published revision after suspension"}
    )

    second = publish_card(changed, catalog)

    assert catalog.connection.execute(
        """
        SELECT status, suspended
        FROM card_lifecycle_current
        WHERE card_version_id = ?
        """,
        (first.version_id,),
    ).fetchone() == ("superseded", 1)
    append_card_lifecycle_event(
        catalog.connection,
        card_version_id=first.version_id,
        event_id="reinstate-superseded-publish-card-revision",
        request_digest="d" * 64,
        event_type="reinstate",
        occurred_at=NOW,
        actor_id="cards-tests",
    )
    assert catalog.connection.execute(
        "SELECT version_id FROM card_fts ORDER BY version_id"
    ).fetchall() == [(second.version_id,)]


def test_publish_card_blocks_suspended_review_and_published_replay(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary
    from course_helper.lifecycle import append_card_lifecycle_event

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    candidate = _reviewed_card(version_id="suspended-review-candidate")
    catalog.insert_card(candidate)
    append_card_lifecycle_event(
        catalog.connection,
        card_version_id=candidate.version_id,
        event_id="suspend-review-before-publication",
        request_digest="f" * 64,
        event_type="suspend",
        occurred_at=NOW,
        actor_id="cards-tests",
    )
    with pytest.raises(PublishBlocked, match="suspended"):
        publish_card(candidate, catalog)

    append_card_lifecycle_event(
        catalog.connection,
        card_version_id=candidate.version_id,
        event_id="reinstate-review-before-publication",
        request_digest="1" * 64,
        event_type="reinstate",
        occurred_at=NOW,
        actor_id="cards-tests",
    )
    published = publish_card(candidate, catalog)
    append_card_lifecycle_event(
        catalog.connection,
        card_version_id=published.version_id,
        event_id="suspend-published-before-replay",
        request_digest="2" * 64,
        event_type="suspend",
        occurred_at=NOW,
        actor_id="cards-tests",
    )

    with pytest.raises(PublishBlocked, match="suspended"):
        publish_card(published, catalog)


def test_revision_publish_rolls_back_status_and_fts_when_late_write_fails(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    first = publish_card(_reviewed_card(), catalog)
    changed = _reviewed_card(version_id="rollback-second-version").model_copy(
        update={"title": "Rollback-safe revised title"}
    )
    catalog.connection.execute(
        """
        CREATE TRIGGER reject_late_publish_evidence
        AFTER INSERT ON evidence
        BEGIN
            SELECT RAISE(ABORT, 'forced late evidence failure');
        END
        """
    )
    catalog.connection.commit()
    before = {
        table: catalog.connection.execute(
            f"SELECT * FROM {table} ORDER BY 1"
        ).fetchall()
        for table in (
            "cards",
            "card_tags",
            "card_fts",
            "evidence",
            "lineage",
            "card_lifecycle_events",
            "card_lifecycle_current",
        )
    }
    statements: list[str] = []
    catalog.connection.set_trace_callback(
        lambda statement: statements.append(" ".join(statement.upper().split()))
    )

    try:
        with pytest.raises(PublishBlocked, match="card publication failed"):
            publish_card(changed, catalog)
    finally:
        catalog.connection.set_trace_callback(None)

    assert not any(
        statement.startswith("UPDATE CARDS SET STATUS")
        for statement in statements
    )
    for prefix in (
        "INSERT INTO CARD_LIFECYCLE_EVENTS",
        "DELETE FROM CARD_FTS",
        "INSERT INTO CARDS",
        "INSERT INTO CARD_FTS",
        "INSERT INTO EVIDENCE",
    ):
        assert any(statement.startswith(prefix) for statement in statements)
    assert next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("DELETE FROM CARD_FTS")
    ) < next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("INSERT INTO CARDS")
    )

    stored = catalog.connection.execute(
        "SELECT status, payload_json FROM cards WHERE version_id = ?",
        (first.version_id,),
    ).fetchone()
    assert stored[0] == "published"
    assert json.loads(stored[1])["status"] == "published"
    assert catalog.connection.execute(
        "SELECT version_id FROM card_fts ORDER BY version_id"
    ).fetchall() == [(first.version_id,)]
    assert catalog.connection.execute(
        "SELECT count(*) FROM cards WHERE version_id = ?",
        (changed.version_id,),
    ).fetchone()[0] == 0
    after = {
        table: catalog.connection.execute(
            f"SELECT * FROM {table} ORDER BY 1"
        ).fetchall()
        for table in (
            "cards",
            "card_tags",
            "card_fts",
            "evidence",
            "lineage",
            "card_lifecycle_events",
            "card_lifecycle_current",
        )
    }
    assert after == before


def test_same_logical_new_content_retry_reuses_effective_revision(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    publish_card(_reviewed_card(), catalog)
    changed = _reviewed_card(version_id="retry-second-version").model_copy(
        update={"title": "Retry-safe revised unit"}
    )
    second = publish_card(changed, catalog)

    repeated = publish_card(changed, catalog)

    assert repeated == second
    assert second.revision == 2
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 2
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 2


def test_reused_version_id_for_new_content_derives_a_new_concrete_version(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    original_candidate = _reviewed_card()
    first = publish_card(original_candidate, catalog)
    changed = original_candidate.model_copy(
        update={
            "title": "Colliding ID with changed content",
            "learning_objective": "Review collision-safe versioning",
        }
    )

    second = publish_card(changed, catalog)

    assert second.logical_id == first.logical_id
    assert second.version_id not in {first.version_id, changed.version_id}
    assert second.revision == 2
    assert second.supersedes_version_id == first.version_id
    assert catalog.connection.execute(
        "SELECT version_id, revision FROM cards ORDER BY revision"
    ).fetchall() == [(first.version_id, 1), (second.version_id, 2)]


def test_reused_version_id_derived_version_is_idempotent_on_retry(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    original = _reviewed_card()
    publish_card(original, catalog)
    changed = original.model_copy(update={"title": "Derived version retry"})
    second = publish_card(changed, catalog)

    repeated = publish_card(changed, catalog)

    assert repeated == second
    assert second.revision == 2
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 2


def test_existing_identical_version_returns_before_new_blocking_review(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import (
        create_review_task,
        publish_card,
        seed_vocabulary,
    )

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    card = _reviewed_card()
    first = publish_card(card, catalog)
    create_review_task(
        catalog,
        kind="manual-review",
        subject_version_id=card.version_id,
    )

    second = publish_card(card, catalog)

    assert second == first
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_exact_duplicate_is_archived_and_links_to_existing_published_version(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import (
        find_exact_duplicate,
        publish_card,
        seed_vocabulary,
    )

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    first = publish_card(_reviewed_card(), catalog)
    duplicate = _reviewed_card(
        logical_id="different-logical-id",
        version_id="different-version-id",
    )

    assert find_exact_duplicate(duplicate, catalog) == first
    result = publish_card(duplicate, catalog)

    assert result == first
    archived = catalog.connection.execute(
        "SELECT status, payload_json FROM cards WHERE version_id = ?",
        (duplicate.version_id,),
    ).fetchone()
    assert archived[0] == "archived"
    assert json.loads(archived[1])["status"] == "archived"
    assert catalog.connection.execute(
        "SELECT count(*) FROM cards WHERE status = 'published'"
    ).fetchone()[0] == 1
    assert catalog.connection.execute(
        "SELECT version_id FROM card_fts ORDER BY version_id"
    ).fetchall() == [(first.version_id,)]
    assert catalog.connection.execute(
        """
        SELECT from_version_id, to_version_id, relation
        FROM lineage
        WHERE relation = 'deduplicates'
        """
    ).fetchall() == [
        (duplicate.version_id, first.version_id, "deduplicates")
    ]
    dedup_evidence = catalog.connection.execute(
        "SELECT payload_json FROM evidence WHERE kind = 'dedup'"
    ).fetchone()
    assert json.loads(dedup_evidence[0])["subject_version_id"] == duplicate.version_id


def test_retrying_archived_exact_duplicate_returns_original_without_new_rows(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    original = publish_card(_reviewed_card(), catalog)
    duplicate = _reviewed_card(
        logical_id="retry-duplicate-logical",
        version_id="retry-duplicate-version",
    )
    assert publish_card(duplicate, catalog) == original
    before = tuple(
        catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("cards", "evidence", "lineage", "card_fts")
    )

    repeated = publish_card(duplicate, catalog)

    assert repeated == original
    assert tuple(
        catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("cards", "evidence", "lineage", "card_fts")
    ) == before
    assert catalog.connection.execute(
        """
        SELECT count(*) FROM lineage
        WHERE relation = 'deduplicates' AND from_version_id = to_version_id
        """
    ).fetchone()[0] == 0


def test_same_logical_different_version_exact_duplicate_archives_revision_two(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    original = publish_card(_reviewed_card(), catalog)
    duplicate = _reviewed_card(version_id="same-logical-exact-version-two")

    result = publish_card(duplicate, catalog)

    assert result == original
    stored = catalog.connection.execute(
        "SELECT revision, status, payload_json FROM cards WHERE version_id = ?",
        (duplicate.version_id,),
    ).fetchone()
    assert stored[:2] == (2, "archived")
    archived = json.loads(stored[2])
    assert archived["supersedes_version_id"] == original.version_id
    assert catalog.connection.execute(
        """
        SELECT from_version_id, to_version_id FROM lineage
        WHERE relation = 'deduplicates'
        """
    ).fetchall() == [(duplicate.version_id, original.version_id)]


def test_near_duplicate_hook_opens_blocking_review_without_merging(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import (
        PublishBlocked,
        create_review_task,
        find_exact_duplicate,
        publish_card,
        seed_vocabulary,
    )

    seed_vocabulary(catalog)
    _persist_extraction(catalog, _pptx_extraction((3,)))
    publish_card(_reviewed_card(), catalog)
    near_duplicate = _reviewed_card(
        logical_id="near-logical-id",
        version_id="near-version-id",
    ).model_copy(update={"title": "Shared unit extension"})
    catalog.insert_card(near_duplicate)

    task = create_review_task(
        catalog,
        kind="near-duplicate",
        subject_version_id=near_duplicate.version_id,
    )

    assert task.status == "open"
    assert task.blocking is True
    assert find_exact_duplicate(near_duplicate, catalog) is None
    with pytest.raises(PublishBlocked, match="open blocking review task"):
        publish_card(near_duplicate, catalog)
    assert catalog.connection.execute(
        "SELECT count(*) FROM cards WHERE version_id = ?",
        (near_duplicate.version_id,),
    ).fetchone()[0] == 1
    assert catalog.connection.execute(
        "SELECT count(*) FROM lineage WHERE relation = 'deduplicates'"
    ).fetchone()[0] == 0


def test_concurrent_publish_of_same_version_is_serialized_and_idempotent(
    tmp_path: Path,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    database = tmp_path / "concurrent.db"
    with KnowledgeCatalog.open(database) as setup:
        seed_vocabulary(setup)
        _persist_extraction(setup, _pptx_extraction((3,)))
    card = _reviewed_card()
    start = Barrier(2)

    def worker():
        with KnowledgeCatalog.open(database) as opened:
            start.wait(timeout=10)
            return publish_card(card, opened)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: worker(), range(2)))

    assert results[0] == results[1]
    with KnowledgeCatalog.open(database) as catalog:
        assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 1
        assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
        assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 1


@pytest.mark.parametrize("iteration", range(20))
def test_concurrent_same_logical_different_versions_form_revision_chain(
    tmp_path: Path,
    iteration: int,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary

    database = tmp_path / f"concurrent-revisions-{iteration}.db"
    with KnowledgeCatalog.open(database) as setup:
        seed_vocabulary(setup)
        _persist_extraction(setup, _pptx_extraction((3,)))
    cards = (
        _reviewed_card(version_id=f"concurrent-version-a-{iteration}").model_copy(
            update={"title": "Concurrent revision A"}
        ),
        _reviewed_card(version_id=f"concurrent-version-b-{iteration}").model_copy(
            update={"title": "Concurrent revision B"}
        ),
    )
    start = Barrier(2)

    def worker(card):
        with KnowledgeCatalog.open(database) as opened:
            start.wait(timeout=10)
            return publish_card(card, opened)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(worker, cards))

    assert {result.revision for result in results} == {1, 2}
    with KnowledgeCatalog.open(database) as catalog:
        rows = catalog.connection.execute(
            "SELECT version_id, revision, payload_json FROM cards ORDER BY revision"
        ).fetchall()
        assert [row[1] for row in rows] == [1, 2]
        first_id = rows[0][0]
        second = json.loads(rows[1][2])
        assert second["supersedes_version_id"] == first_id
