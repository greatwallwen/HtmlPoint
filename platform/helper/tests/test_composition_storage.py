from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
import hashlib
import sqlite3

import pytest

from course_helper.catalog import (
    CatalogMigrationError,
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    OutlineConfirmation,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.composition import (
    CardPlacement,
    CourseOutline,
    CourseOutlineChapter,
    CourseRequirement,
    CourseVersion,
    canonical_digest,
)
from course_helper.domain.evidence import EvidenceObject, LineageEdge
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    TagVocabularyVersion,
)
from course_helper.domain.slide_ast import (
    RuntimeManifest,
    SlideAssetBinding,
    SlideDeckAst,
    SlideNode,
    runtime_manifest_content_digest,
    slide_deck_content_digest,
)
from course_helper.domain.common import SourceLocator
from course_helper.domain.sources import (
    ChunkLocator,
    ExtractedChunk,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.domain.visual_policy import (
    AttributionBlock,
    TransformationManifest,
    VisualPlacement,
)
from course_helper.lifecycle import append_card_lifecycle_event


NOW = datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc)


def actor() -> ActorRef:
    return ActorRef(actor_type="human", actor_id="composition-author")


def requirement() -> CourseRequirement:
    return CourseRequirement(
        requirement_id="requirement-1",
        title="Grounded AI course",
        audience="Product managers",
        learning_goals=("Explain grounding",),
        duration_minutes=30,
        usage_scope="internal",
    )


def evidence(evidence_id: str, *, subject_version_id: str | None = None) -> EvidenceObject:
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="composition",
        subject_version_id=subject_version_id,
        status="verified",
        producer="course-helper/tests",
        started_at=NOW,
        finished_at=NOW,
    )


def outline() -> CourseOutline:
    placement = CardPlacement(
        placement_id="card-placement-1",
        card_version_id="card-storage-v1",
        chapter_id="chapter-1",
        lesson_id="lesson-1",
        purpose="core",
        allocated_minutes=30,
    )
    return CourseOutline(
        logical_id="outline-logical-1",
        version_id="outline-v1",
        revision=1,
        content_digest="1" * 64,
        created_at=NOW,
        created_by=actor(),
        requirement_id=requirement().requirement_id,
        chapters=(
            CourseOutlineChapter(
                chapter_id="chapter-1",
                title="Grounded composition",
                objective="Build one evidence-backed course",
                placements=(placement,),
            ),
        ),
        retrieval_evidence_id="retrieval-evidence-1",
        index_snapshot_id="index-snapshot-1",
    )


def confirmation(
    *, confirmation_id: str = "confirmation-1", confirmation_digest: str = "2" * 64
) -> OutlineConfirmation:
    return OutlineConfirmation(
        confirmation_id=confirmation_id,
        requirement_id=requirement().requirement_id,
        outline_version_id=outline().version_id,
        expected_outline_digest=outline().content_digest,
        confirmation_digest=confirmation_digest,
        confirmed_by=actor(),
    )


def course() -> CourseVersion:
    return CourseVersion(
        logical_id="course-logical-1",
        version_id="course-v1",
        revision=1,
        content_digest="3" * 64,
        created_at=NOW,
        created_by=actor(),
        requirement_id=requirement().requirement_id,
        outline_version_id=outline().version_id,
        outline_digest=outline().content_digest,
        placement_ids=("card-placement-1",),
        usage_scope="internal",
        confirmation_digest=confirmation().confirmation_digest,
        status="confirmed",
    )


def deck(
    *,
    placement_id: str = "card-placement-1",
    evidence_id: str = "retrieval-evidence-1",
) -> SlideDeckAst:
    evidence_ids = tuple(dict.fromkeys((evidence_id, "retrieval-evidence-1")))
    value = SlideDeckAst(
        logical_id="deck-logical-1",
        version_id="deck-v1",
        revision=1,
        content_digest="0" * 64,
        created_at=NOW,
        created_by=actor(),
        course_version_id=course().version_id,
        nodes=(
            SlideNode(
                node_id="slide-node-1",
                node_type="slide",
                placement_ids=(placement_id,),
                card_version_ids=("card-storage-v1",),
                chunk_ids=("chunk-storage-v1",),
                source_version_ids=("source-storage-v1",),
                evidence_ids=evidence_ids,
                presenter_notes="Explain the grounded storage evidence to the learner.",
            ),
        ),
    )
    return value.model_copy(update={"content_digest": slide_deck_content_digest(value)})


def runtime(deck_value: SlideDeckAst | None = None) -> RuntimeManifest:
    selected_deck = deck() if deck_value is None else deck_value
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for node in selected_deck.nodes
            for evidence_id in node.evidence_ids
        )
    )
    value = RuntimeManifest(
        logical_id="runtime-logical-1",
        version_id="runtime-v1",
        revision=1,
        content_digest="0" * 64,
        created_at=NOW,
        created_by=actor(),
        course_version_id=course().version_id,
        slide_deck_version_id=selected_deck.version_id,
        slide_deck_digest=selected_deck.content_digest,
        evidence_ids=evidence_ids,
    )
    return value.model_copy(
        update={"content_digest": runtime_manifest_content_digest(value)}
    )


def with_deck_digest(value: SlideDeckAst) -> SlideDeckAst:
    return value.model_copy(update={"content_digest": slide_deck_content_digest(value)})


def with_manifest_digest(value: RuntimeManifest) -> RuntimeManifest:
    return value.model_copy(
        update={"content_digest": runtime_manifest_content_digest(value)}
    )


def visual_placement() -> VisualPlacement:
    return VisualPlacement(
        placement_id="visual-placement-1",
        visual_version_id="visual-storage-v1",
        slide_node_id="slide-node-1",
        slot_id="hero",
        fit="contain",
        alt_text="Grounded composition diagram",
        authenticity_evidence_id="authenticity-evidence-1",
        license_evidence_id="license-evidence-1",
        attribution=AttributionBlock(
            title="Grounded composition diagram", license_label="Internal source"
        ),
        transformation=TransformationManifest(
            transformation_id="transformation-1",
            scale_mode="contain",
            derivative_license_decision="not-derivative",
            share_alike_compatible=True,
            gfdl_compatible=True,
            no_derivatives_compatible=True,
        ),
        originating_card_version_id="card-storage-v1",
    )


def prepare_dependencies(catalog: KnowledgeCatalog) -> None:
    catalog.insert_vocabulary(
        TagVocabularyVersion(
            logical_id="vocabulary-storage",
            version_id="vocabulary-storage-v1",
            revision=1,
            content_digest="6" * 64,
            created_at=NOW,
            created_by=actor(),
            dimensions=(),
        )
    )
    source = SourceAssetVersion(
        logical_id="source-storage", version_id="source-storage-v1", revision=1,
        content_digest="a" * 64, created_at=NOW, created_by=actor(),
        locator=SourceLocator(root_id="fixture", relative_path="storage.md"),
        display_name="storage.md", source_kind="markdown", media_type="text/markdown",
        byte_size=20, extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id="chunk-storage-v1", source_version_id=source.version_id, ordinal=0,
        modality="text", language="en", normalized_text="Explain grounding",
        content_digest="b" * 64,
        locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
    )
    catalog.insert_source(source)
    catalog.insert_chunk(chunk)
    stored_card = catalog.insert_card(
        KnowledgeCardVersion(
            logical_id="card-storage",
            version_id="card-storage-v1",
            revision=1,
            content_digest="7" * 64,
            created_at=NOW,
            created_by=actor(),
            main_type_id="concept",
            title="Grounding",
            learning_objective="Explain grounding",
            content_ast=(CardContentNode(type="paragraph", text="Use evidence."),),
            suggested_minutes=30,
            vocabulary_version_id="vocabulary-storage-v1",
            chunk_citations=(
                ChunkCitation(
                    chunk_id=chunk.chunk_id, source_version_id=source.version_id
                ),
            ),
            status="review",
        )
    )
    with catalog.connection:
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=stored_card.version_id,
            event_id="publish:card-storage-v1",
            request_digest="a" * 64,
            event_type="publish",
            occurred_at=NOW,
            actor_id=actor().actor_id,
        )
    for evidence_id in (
        "retrieval-evidence-1",
        "authenticity-evidence-1",
        "license-evidence-1",
    ):
        catalog.insert_evidence(evidence(evidence_id))
    catalog.insert_lineage(
        LineageEdge(
            edge_id="lineage-storage-card-chunk",
            from_version_id=stored_card.version_id,
            to_version_id=chunk.chunk_id,
            relation="cites",
            evidence_id="retrieval-evidence-1",
            created_at=NOW,
        )
    )
    catalog.insert_visual(
        VisualAssetVersion(
            logical_id="visual-storage",
            version_id="visual-storage-v1",
            revision=1,
            content_digest="8" * 64,
            created_at=NOW,
            created_by=actor(),
            media_type="image/png",
            license_status="source-provided",
            authenticity="source-provided",
        )
    )
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.operations import (
        IndexOutboxItem,
        OperationMutationResult,
        OperationRequest,
        run_operation,
    )

    run_operation(
        catalog,
        OperationRequest(
            operation_id="operation-storage-index",
            request_digest="c" * 64,
            actor=actor(),
            session_id="storage-session",
        ),
        lambda: OperationMutationResult(
            result_refs={}, item_outcomes=(),
            index_outbox=(
                IndexOutboxItem(
                    outbox_id="outbox-storage-index",
                    card_version_id=stored_card.version_id,
                    action="upsert",
                ),
            ),
        ),
        clock=lambda: NOW,
    )
    claim = claim_next_index_outbox(
        catalog, worker_id="storage-worker", now=NOW, lease_seconds=30
    )
    assert claim is not None
    complete_index_claim(
        catalog, claim_id=claim.claim_id, worker_id="storage-worker",
        embedding_provider=None, now=NOW + timedelta(seconds=1),
    )


def register_grounded_outline(
    catalog: KnowledgeCatalog,
    *,
    requirement_value: CourseRequirement | None = None,
    logical_id: str = "outline-logical-1",
    version_id: str = "outline-v1",
    revision: int = 1,
) -> CourseOutline:
    from course_helper.composer import CompositionOptions, compose_and_register
    from course_helper.retrieval import KnowledgeRetriever

    value = requirement() if requirement_value is None else requirement_value
    snapshot_id = catalog.connection.execute(
        "SELECT index_snapshot_id FROM embedding_index_snapshots "
        "ORDER BY created_at DESC, index_snapshot_id DESC LIMIT 1"
    ).fetchone()[0]
    return compose_and_register(
        catalog,
        KnowledgeRetriever(catalog),
        value,
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        created_at=NOW,
        created_by=actor(),
        options=CompositionOptions(index_snapshot_id=snapshot_id),
    ).outline


def confirm_grounded_outline(
    catalog: KnowledgeCatalog,
    value: CourseOutline,
    *,
    confirmation_id: str = "confirmation-1",
    confirmation_digest: str | None = None,
) -> OutlineConfirmation:
    if confirmation_digest is None:
        from course_helper.composer import confirmation_summary

        confirmation_digest = confirmation_summary(
            value, requirement()
        ).confirmation_digest
    confirmation_value = OutlineConfirmation(
        confirmation_id=confirmation_id,
        requirement_id=value.requirement_id,
        outline_version_id=value.version_id,
        expected_outline_digest=value.content_digest,
        confirmation_digest=confirmation_digest,
        confirmed_by=actor(),
    )
    return catalog.confirm_course_outline(
        confirmation_value, clock=lambda: NOW
    ).payload


def grounded_course(
    value: CourseOutline,
    confirmed: OutlineConfirmation,
    *,
    version_id: str = "course-v1",
    logical_id: str = "course-logical-1",
) -> CourseVersion:
    return CourseVersion(
        logical_id=logical_id, version_id=version_id, revision=1,
        content_digest="3" * 64, created_at=NOW, created_by=actor(),
        requirement_id=value.requirement_id, outline_version_id=value.version_id,
        outline_digest=value.content_digest,
        placement_ids=tuple(
            item.placement_id for chapter in value.chapters for item in chapter.placements
        ),
        usage_scope="internal", confirmation_digest=confirmed.confirmation_digest,
        status="confirmed",
    )


def prepare_confirmed_composition(
    catalog: KnowledgeCatalog,
) -> tuple[CourseOutline, OutlineConfirmation, CourseVersion]:
    catalog.register_course_requirement(requirement(), clock=lambda: NOW)
    outline_value = register_grounded_outline(catalog)
    confirmation_value = confirm_grounded_outline(catalog, outline_value)
    course_value = grounded_course(outline_value, confirmation_value)
    catalog.register_course_version(course_value, clock=lambda: NOW)
    return outline_value, confirmation_value, course_value


def test_current_migrations_and_requirement_registration_reuse_original_clock(
    tmp_path: Path,
) -> None:
    ticks = iter((NOW, NOW + timedelta(hours=1)))
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        assert catalog.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]
        first = catalog.register_course_requirement(requirement(), clock=lambda: next(ticks))
        second = catalog.register_course_requirement(requirement(), clock=lambda: next(ticks))
        assert second == first
        assert second.created_at == NOW
        with pytest.raises(ImmutableVersionConflict):
            catalog.register_course_requirement(
                requirement().model_copy(update={"title": "Changed immutable title"}),
                clock=lambda: NOW,
            )


def test_all_composition_versions_reopen_byte_identically_and_join_evidence_lineage(
    tmp_path: Path,
) -> None:
    database = tmp_path / "composition.db"
    with KnowledgeCatalog.open(database) as catalog:
        prepare_dependencies(catalog)
        stored_requirement = catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        stored_outline = catalog.get_course_outline(outline_value.version_id)
        assert stored_outline is not None
        confirmation_value = confirm_grounded_outline(catalog, outline_value)
        stored_confirmation = catalog.get_outline_confirmation(outline_value.version_id)
        assert stored_confirmation is not None
        course_value = grounded_course(outline_value, confirmation_value)
        stored_course = catalog.register_course_version(course_value, clock=lambda: NOW)
        placement_id = outline_value.chapters[0].placements[0].placement_id
        deck_value = deck(
            placement_id=placement_id,
            evidence_id=outline_value.retrieval_evidence_id,
        )
        stored_deck = catalog.register_slide_deck(deck_value, clock=lambda: NOW)
        stored_runtime = catalog.register_runtime_manifest(
            runtime(deck_value), clock=lambda: NOW
        )
        stored_visual = catalog.register_visual_placement(visual_placement(), clock=lambda: NOW)
        stored_card_placement = catalog.get_card_placement(placement_id)
        records = (
            stored_requirement,
            stored_outline,
            stored_confirmation,
            stored_course,
            stored_deck,
            stored_runtime,
            stored_visual,
        )
        assert all(record.created_at == NOW for record in records)
        assert stored_card_placement is not None
        assert stored_card_placement.payload == outline_value.chapters[0].placements[0]
        replays = (
            catalog.register_course_requirement(requirement(), clock=lambda: NOW + timedelta(days=1)),
            catalog.register_course_outline(outline_value, clock=lambda: NOW + timedelta(days=1)),
            catalog.confirm_course_outline(confirmation_value, clock=lambda: NOW + timedelta(days=1)),
            catalog.register_course_version(course_value, clock=lambda: NOW + timedelta(days=1)),
            catalog.register_slide_deck(deck_value, clock=lambda: NOW + timedelta(days=1)),
            catalog.register_runtime_manifest(
                runtime(deck_value), clock=lambda: NOW + timedelta(days=1)
            ),
            catalog.register_visual_placement(
                visual_placement(), clock=lambda: NOW + timedelta(days=1)
            ),
        )
        assert replays == records
        conflicting_calls = (
            lambda: catalog.register_course_requirement(
                requirement().model_copy(update={"title": "Conflicting requirement"}),
                clock=lambda: NOW,
            ),
            lambda: catalog.register_course_outline(
                outline_value.model_copy(update={"uncovered_goals": ("Missing goal",)}),
                clock=lambda: NOW,
            ),
            lambda: catalog.confirm_course_outline(
                confirmation_value.model_copy(update={"confirmation_digest": "9" * 64}),
                clock=lambda: NOW,
            ),
            lambda: catalog.register_course_version(
                course_value.model_copy(update={"status": "archived"}), clock=lambda: NOW
            ),
            lambda: catalog.register_slide_deck(
                deck_value.model_copy(update={"content_digest": "9" * 64}), clock=lambda: NOW
            ),
            lambda: catalog.register_runtime_manifest(
                runtime(deck_value).model_copy(update={"content_digest": "9" * 64}),
                clock=lambda: NOW,
            ),
            lambda: catalog.register_visual_placement(
                visual_placement().model_copy(update={"alt_text": "Conflicting alt text"}),
                clock=lambda: NOW,
            ),
        )
        for conflicting_call in conflicting_calls:
            with pytest.raises(ImmutableVersionConflict):
                conflicting_call()
        course_evidence = evidence("course-evidence-1", subject_version_id=course_value.version_id)
        catalog.insert_evidence(course_evidence)
        edge = LineageEdge(
            edge_id="course-deck-edge-1",
            from_version_id=course_value.version_id,
            to_version_id=deck_value.version_id,
            relation="composed_into",
            evidence_id=course_evidence.evidence_id,
            created_at=NOW,
        )
        catalog.insert_lineage(edge)
        catalog.insert_lineage(edge)

    with KnowledgeCatalog.open(database) as reopened:
        assert reopened.get_course_requirement(requirement().requirement_id) == stored_requirement
        assert reopened.get_course_outline(outline_value.version_id) == stored_outline
        assert reopened.get_outline_confirmation(outline_value.version_id) == stored_confirmation
        assert reopened.get_course_version(course_value.version_id) == stored_course
        assert reopened.get_slide_deck(deck_value.version_id) == stored_deck
        assert reopened.get_runtime_manifest(runtime(deck_value).version_id) == stored_runtime
        assert reopened.get_visual_placement(visual_placement().placement_id) == stored_visual
        assert reopened.get_card_placement(placement_id) == stored_card_placement
        assert reopened.connection.execute(
            "SELECT count(*) FROM lineage WHERE edge_id = 'course-deck-edge-1'"
        ).fetchone()[0] == 1


def test_missing_composition_references_fail_without_partial_rows(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        with pytest.raises(CatalogReferenceError):
            catalog.register_course_outline(outline(), clock=lambda: NOW)
        with pytest.raises(CatalogReferenceError):
            catalog.register_course_version(course(), clock=lambda: NOW)
        with pytest.raises(CatalogReferenceError):
            catalog.register_visual_placement(visual_placement(), clock=lambda: NOW)
        assert catalog.connection.execute("SELECT count(*) FROM course_outlines").fetchone()[0] == 0
        assert catalog.connection.execute("SELECT count(*) FROM course_versions").fetchone()[0] == 0
        assert catalog.connection.execute("SELECT count(*) FROM visual_placements").fetchone()[0] == 0


def test_course_scope_cannot_escalate_beyond_confirmed_requirement(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "course-scope.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        confirmation_value = confirm_grounded_outline(catalog, outline_value)
        course_value = grounded_course(outline_value, confirmation_value)

        with pytest.raises(CatalogReferenceError, match="usage scope"):
            catalog.register_course_version(
                course_value.model_copy(update={"usage_scope": "public"}),
                clock=lambda: NOW,
            )

        assert catalog.connection.execute(
            "SELECT count(*) FROM course_versions"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("status", ("published", "archived"))
def test_initial_course_registration_only_accepts_confirmed_status(
    tmp_path: Path, status: str
) -> None:
    with KnowledgeCatalog.open(tmp_path / f"course-{status}.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        confirmation_value = confirm_grounded_outline(catalog, outline_value)
        course_value = grounded_course(outline_value, confirmation_value)

        with pytest.raises(CatalogReferenceError, match="confirmed"):
            catalog.register_course_version(
                course_value.model_copy(update={"status": status}),
                clock=lambda: NOW,
            )

        assert catalog.connection.execute(
            "SELECT count(*) FROM course_versions"
        ).fetchone()[0] == 0


def test_concurrent_confirmation_has_one_persisted_winner(tmp_path: Path) -> None:
    database = tmp_path / "confirmation.db"
    with KnowledgeCatalog.open(database) as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
    start = Barrier(2)
    from course_helper.composer import confirmation_summary

    summary_digest = confirmation_summary(
        outline_value, requirement()
    ).confirmation_digest
    candidates = (
        OutlineConfirmation(
            confirmation_id="confirmation-a", requirement_id=outline_value.requirement_id,
            outline_version_id=outline_value.version_id,
            expected_outline_digest=outline_value.content_digest,
            confirmation_digest=summary_digest, confirmed_by=actor(),
        ),
        OutlineConfirmation(
            confirmation_id="confirmation-b", requirement_id=outline_value.requirement_id,
            outline_version_id=outline_value.version_id,
            expected_outline_digest=outline_value.content_digest,
            confirmation_digest=summary_digest, confirmed_by=actor(),
        ),
    )

    def worker(value: OutlineConfirmation):
        with KnowledgeCatalog.open(database) as catalog:
            start.wait(timeout=10)
            try:
                return catalog.confirm_course_outline(value, clock=lambda: NOW)
            except ImmutableVersionConflict:
                return catalog.get_outline_confirmation(value.outline_version_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(worker, candidates))
    assert results[0] == results[1]
    with KnowledgeCatalog.open(database) as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM outline_confirmations"
        ).fetchone()[0] == 1
        assert catalog.get_outline_confirmation(outline_value.version_id) == results[0]


def test_confirmation_fails_closed_for_gaps_and_a_newer_outline_response(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "confirmation-fail-closed.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        original = register_grounded_outline(catalog)
        register_grounded_outline(
            catalog, version_id="outline-v2", revision=2
        )
        original_confirmation = OutlineConfirmation(
            confirmation_id="confirmation-stale",
            requirement_id=original.requirement_id,
            outline_version_id=original.version_id,
            expected_outline_digest=original.content_digest,
            confirmation_digest=__import__(
                "course_helper.composer", fromlist=["confirmation_summary"]
            ).confirmation_summary(original, requirement()).confirmation_digest,
            confirmed_by=actor(),
        )
        with pytest.raises(CatalogReferenceError, match="stale"):
            catalog.confirm_course_outline(original_confirmation, clock=lambda: NOW)
        assert catalog.connection.execute(
            "SELECT count(*) FROM outline_confirmations"
        ).fetchone()[0] == 0
        gap = original.model_copy(
            update={
                "logical_id": "outline-gap-logical",
                "version_id": "outline-gap-v1",
                "uncovered_goals": ("Missing governed goal",),
            }
        )
        with pytest.raises(CatalogReferenceError, match="binding"):
            catalog.register_course_outline(gap, clock=lambda: NOW)


def test_new_outline_rejects_non_retrieval_evidence_bypass(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "non-retrieval-bypass.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)

        with pytest.raises(CatalogReferenceError, match="composer binding"):
            catalog.register_course_outline(outline(), clock=lambda: NOW)

        assert catalog.connection.execute(
            "SELECT count(*) FROM course_outlines"
        ).fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM card_placements"
        ).fetchone()[0] == 0


def test_confirmation_reopens_and_rejects_tampered_snapshot_digest(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "stale-snapshot.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        catalog.connection.execute(
            "DROP TRIGGER embedding_index_snapshots_immutable_update"
        )
        catalog.connection.execute(
            "UPDATE embedding_index_snapshots SET snapshot_digest = ? "
            "WHERE index_snapshot_id = ?",
            ("f" * 64, outline_value.index_snapshot_id),
        )
        catalog.connection.commit()
        confirmation_value = OutlineConfirmation(
            confirmation_id="confirmation-stale-snapshot",
            requirement_id=outline_value.requirement_id,
            outline_version_id=outline_value.version_id,
            expected_outline_digest=outline_value.content_digest,
            confirmation_digest=__import__(
                "course_helper.composer", fromlist=["confirmation_summary"]
            ).confirmation_summary(outline_value, requirement()).confirmation_digest,
            confirmed_by=actor(),
        )

        with pytest.raises(CatalogReferenceError, match="snapshot"):
            catalog.confirm_course_outline(confirmation_value, clock=lambda: NOW)


def test_course_registration_rechecks_card_lifecycle_after_confirmation(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "post-confirm-suspension.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        confirmation_value = confirm_grounded_outline(catalog, outline_value)
        course_value = grounded_course(outline_value, confirmation_value)
        with catalog.connection:
            append_card_lifecycle_event(
                catalog.connection,
                card_version_id="card-storage-v1",
                event_id="suspend:card-storage-v1:after-confirmation",
                request_digest="e" * 64,
                event_type="suspend",
                occurred_at=NOW + timedelta(minutes=1),
                actor_id=actor().actor_id,
            )

        with pytest.raises(CatalogReferenceError, match="lifecycle"):
            catalog.register_course_version(course_value, clock=lambda: NOW)

        assert catalog.connection.execute(
            "SELECT count(*) FROM course_versions"
        ).fetchone()[0] == 0

def test_composition_tables_reject_raw_update_and_delete(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        prepare_dependencies(catalog)
        outline_value, confirmation_value, course_value = prepare_confirmed_composition(catalog)
        placement_id = outline_value.chapters[0].placements[0].placement_id
        deck_value = deck(
            placement_id=placement_id,
            evidence_id=outline_value.retrieval_evidence_id,
        )
        catalog.register_slide_deck(deck_value, clock=lambda: NOW)
        catalog.register_runtime_manifest(runtime(deck_value), clock=lambda: NOW)
        catalog.register_visual_placement(visual_placement(), clock=lambda: NOW)
        cases = (
            ("course_requirements", "requirement_id", requirement().requirement_id),
            ("course_outlines", "version_id", outline_value.version_id),
            ("card_placements", "placement_id", placement_id),
            ("outline_confirmations", "confirmation_id", confirmation_value.confirmation_id),
            ("course_versions", "version_id", course_value.version_id),
            ("slide_decks", "version_id", deck_value.version_id),
            ("runtime_manifests", "version_id", runtime(deck_value).version_id),
            ("visual_placements", "placement_id", visual_placement().placement_id),
        )
        for table, identity_column, identity in cases:
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"UPDATE {table} SET payload_json = '{{}}' WHERE {identity_column} = ?",
                    (identity,),
                )
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"DELETE FROM {table} WHERE {identity_column} = ?", (identity,)
                )


def test_version_tables_reject_duplicate_logical_revision(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "revision.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        with pytest.raises(sqlite3.IntegrityError):
            register_grounded_outline(
                catalog,
                version_id="outline-conflicting-v1",
            )


def test_runtime_rejects_a_deck_owned_by_a_different_course(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "runtime-cross-bind.db") as catalog:
        prepare_dependencies(catalog)
        outline_value, confirmation_value, course_value = prepare_confirmed_composition(catalog)
        placement_id = outline_value.chapters[0].placements[0].placement_id
        deck_value = deck(
            placement_id=placement_id,
            evidence_id=outline_value.retrieval_evidence_id,
        )
        catalog.register_slide_deck(deck_value, clock=lambda: NOW)
        other_course = course_value.model_copy(
            update={
                "logical_id": "other-course-logical",
                "version_id": "other-course-v1",
            }
        )
        catalog.register_course_version(other_course, clock=lambda: NOW)
        with pytest.raises(CatalogReferenceError, match="deck.*course"):
            catalog.register_runtime_manifest(
                with_manifest_digest(runtime(deck_value).model_copy(
                    update={
                        "logical_id": "cross-runtime-logical",
                        "version_id": "cross-runtime-v1",
                        "course_version_id": other_course.version_id,
                    }
                )),
                clock=lambda: NOW,
            )


def test_visual_placement_requires_each_typed_origin_in_its_exact_table(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "typed-origin.db") as catalog:
        prepare_dependencies(catalog)
        outline_value, _confirmation_value, _course_value = prepare_confirmed_composition(catalog)
        catalog.register_slide_deck(
            deck(
                placement_id=outline_value.chapters[0].placements[0].placement_id,
                evidence_id=outline_value.retrieval_evidence_id,
            ),
            clock=lambda: NOW,
        )
        wrong = visual_placement().model_copy(
            update={
                "placement_id": "visual-placement-wrong-origin",
                "originating_card_version_id": "visual-storage-v1",
            }
        )
        with pytest.raises(CatalogReferenceError, match="origin"):
            catalog.register_visual_placement(wrong, clock=lambda: NOW)


def test_slide_binding_cannot_replace_visual_placement_alt_text(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "binding-alt.db") as catalog:
        prepare_dependencies(catalog)
        outline_value, _confirmation_value, _course_value = prepare_confirmed_composition(catalog)
        deck_value = deck(
            placement_id=outline_value.chapters[0].placements[0].placement_id,
            evidence_id=outline_value.retrieval_evidence_id,
        )
        catalog.register_visual_placement(visual_placement(), clock=lambda: NOW)
        binding = SlideAssetBinding(
            binding_id="binding-alt-mismatch",
            visual_placement_id=visual_placement().placement_id,
            visual_version_id=visual_placement().visual_version_id,
            artifact_id="artifact-1",
            artifact_digest="a" * 64,
            media_type="image/png",
            alt_text="Silently replaced description",
            authenticity_evidence_id=visual_placement().authenticity_evidence_id,
            license_evidence_id=visual_placement().license_evidence_id,
            attribution_id="attribution-"
            + canonical_digest(
                visual_placement().attribution.model_dump(mode="json", exclude_none=True)
            ),
            attribution=visual_placement().attribution,
            transformation_id=visual_placement().transformation.transformation_id,
            transformation=visual_placement().transformation,
        )
        bound_deck = with_deck_digest(deck_value.model_copy(
            update={
                "nodes": (
                    deck_value.nodes[0].model_copy(update={"asset_bindings": (binding,)}),
                )
            }
        ))
        with pytest.raises(CatalogReferenceError, match="alt text"):
            catalog.register_slide_deck(bound_deck, clock=lambda: NOW)
        wrong_media = binding.model_copy(
            update={
                "binding_id": "binding-media-mismatch",
                "alt_text": visual_placement().alt_text,
                "media_type": "image/jpeg",
            }
        )
        with pytest.raises(CatalogReferenceError, match="media"):
            catalog.register_slide_deck(
                with_deck_digest(deck_value.model_copy(
                    update={
                        "nodes": (
                            deck_value.nodes[0].model_copy(
                                update={"asset_bindings": (wrong_media,)}
                            ),
                        )
                    }
                )),
                clock=lambda: NOW,
            )


def test_slide_deck_rejects_raw_extra_placement_outside_course_version(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "raw-extra-placement.db") as catalog:
        prepare_dependencies(catalog)
        outline_value, _confirmation_value, _course_value = prepare_confirmed_composition(catalog)
        deck_value = deck(
            placement_id=outline_value.chapters[0].placements[0].placement_id,
            evidence_id=outline_value.retrieval_evidence_id,
        )
        extra = CardPlacement(
            placement_id="raw-extra-placement",
            card_version_id="card-storage-v1",
            chapter_id="chapter-1",
            lesson_id="lesson-extra",
            purpose="core",
            allocated_minutes=5,
        )
        payload = canonical_model_json(extra)
        catalog.connection.execute(
            "INSERT INTO card_placements("
            "placement_id, outline_version_id, card_version_id, content_digest, "
            "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                extra.placement_id,
                outline_value.version_id,
                extra.card_version_id,
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                payload,
                NOW.isoformat(),
            ),
        )
        forged_deck = with_deck_digest(deck_value.model_copy(
            update={
                "logical_id": "deck-extra-logical",
                "version_id": "deck-extra-v1",
                "nodes": (
                    deck_value.nodes[0].model_copy(
                        update={
                            "placement_ids": (extra.placement_id,),
                            "card_version_ids": (extra.card_version_id,),
                        }
                    ),
                ),
            }
        ))
        catalog.connection.commit()
        with pytest.raises(CatalogReferenceError, match="outside"):
            catalog.register_slide_deck(forged_deck, clock=lambda: NOW)


def test_course_registration_rejects_confirmation_digest_column_alias(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "confirmation-envelope.db") as catalog:
        prepare_dependencies(catalog)
        catalog.register_course_requirement(requirement(), clock=lambda: NOW)
        outline_value = register_grounded_outline(catalog)
        value = OutlineConfirmation(
            confirmation_id="confirmation-1",
            requirement_id=outline_value.requirement_id,
            outline_version_id=outline_value.version_id,
            expected_outline_digest=outline_value.content_digest,
            confirmation_digest="2" * 64,
            confirmed_by=actor(),
        )
        payload = canonical_model_json(value)
        alias_digest = "f" * 64
        catalog.connection.execute(
            "INSERT INTO outline_confirmations("
            "confirmation_id, requirement_id, outline_version_id, "
            "expected_outline_digest, confirmation_digest, content_digest, "
            "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                value.confirmation_id,
                value.requirement_id,
                value.outline_version_id,
                value.expected_outline_digest,
                alias_digest,
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                payload,
                NOW.isoformat(),
            ),
        )
        catalog.connection.commit()
        with pytest.raises(CatalogMigrationError, match="envelope"):
            catalog.register_course_version(
                grounded_course(outline_value, value).model_copy(
                    update={"confirmation_digest": alias_digest}
                ),
                clock=lambda: NOW,
            )


def test_task_four_creates_index_tables_without_a_provenance_table(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        names = {
            row[0]
            for row in catalog.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "embedding_index_candidates",
        "embedding_index_fts_rows",
        "card_embedding_rows",
        "embedding_index_snapshots",
        "knowledge_index_outbox_claims",
        "knowledge_index_outbox_results",
        "knowledge_index_outbox_consumptions",
    }.issubset(names)
    assert not any("provenance" in name for name in names)


def test_composition_getter_rejects_identity_column_that_diverges_from_payload(
    tmp_path: Path,
) -> None:
    payload = requirement().model_copy(update={"requirement_id": "payload-real"})
    raw = canonical_model_json(payload)
    with KnowledgeCatalog.open(tmp_path / "composition-identity-envelope.db") as catalog:
        catalog.connection.execute(
            "INSERT INTO course_requirements("
            "requirement_id, content_digest, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (
                "queried-alias",
                hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                raw,
                NOW.isoformat(),
            ),
        )
        with pytest.raises(CatalogMigrationError, match="envelope"):
            catalog.get_course_requirement("queried-alias")
