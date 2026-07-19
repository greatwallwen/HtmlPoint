from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from course_helper.cards import PublishBlocked, create_review_task, publish_card, seed_vocabulary
from course_helper.catalog import CatalogReferenceError, ImmutableVersionConflict, KnowledgeCatalog, canonical_model_json
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.composition import CardPlacement, CourseOutline, CourseOutlineChapter, CourseRequirement, CourseVersion
from course_helper.domain.knowledge import CardContentNode, ChunkCitation, DatasetReference, KnowledgeCardVersion, ReviewTask, VisualReference
from course_helper.domain.sources import ChunkLocator, DatasetAssetVersion, DatasetColumn, ExtractedChunk, SourceAssetVersion, VisualAssetVersion
from course_helper.catalog import OutlineConfirmation
from course_helper.reviews import ReviewResolution, resolve_review_task
from course_helper.upgrades import (
    ChunkChangeKind,
    detect_chunk_changes,
    propose_asset_change_upgrade,
    propose_course_feedback,
    propose_source_change_upgrades,
    resolve_upgrade_suggestion,
)


NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="course-helper/test-upgrades")
REPLAY_ACTOR = ActorRef(actor_type="human", actor_id="course-helper/replay-reviewer")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source(version_id: str, revision: int, content: str) -> SourceAssetVersion:
    return SourceAssetVersion(
        logical_id="upgrade-source",
        version_id=version_id,
        revision=revision,
        content_digest=_digest(content),
        supersedes_version_id="source-v1" if revision == 2 else None,
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixtures", relative_path="upgrade.md"),
        display_name="upgrade.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=len(content),
        extraction_status="parsed",
    )


def _chunk(source_version_id: str, chunk_id: str, text: str, ordinal: int) -> ExtractedChunk:
    return ExtractedChunk(
        chunk_id=chunk_id,
        source_version_id=source_version_id,
        ordinal=ordinal,
        modality="text",
        language="en",
        normalized_text=text,
        content_digest=_digest(text),
        locator=ChunkLocator(kind="markdown-section", ast_path=(ordinal,)),
        heading=f"Section {ordinal}",
    )


def _card(source: SourceAssetVersion, chunk: ExtractedChunk) -> KnowledgeCardVersion:
    return KnowledgeCardVersion(
        logical_id="upgrade-card",
        version_id="card-v1",
        revision=1,
        content_digest="0" * 64,
        created_at=NOW,
        created_by=ACTOR,
        main_type_id="concept",
        title="Source-backed lesson",
        learning_objective="Recognize governed upgrades.",
        content_ast=(CardContentNode(type="paragraph", text="Original source lesson."),),
        suggested_minutes=5,
        vocabulary_version_id="knowledge-vocabulary-v1",
        chunk_citations=(
            ChunkCitation(
                chunk_id=chunk.chunk_id,
                source_version_id=source.version_id,
                quoted_text=chunk.normalized_text,
            ),
        ),
        status="published",
    )


def _persist_source_and_card(catalog: KnowledgeCatalog) -> tuple[SourceAssetVersion, ExtractedChunk, KnowledgeCardVersion]:
    source = _source("source-v1", 1, "original source")
    chunk = _chunk(source.version_id, "chunk-v1", "Original governed fact.", 0)
    catalog.insert_source(source)
    catalog.insert_chunk(chunk)
    seed_vocabulary(catalog)
    published = publish_card(_card(source, chunk).model_copy(update={"status": "review"}), catalog)
    return source, chunk, published


def _persist_course(catalog: KnowledgeCatalog, card_version_id: str | None = None, *, suffix: str = "1") -> str:
    if card_version_id is None:
        _, _, card = _persist_source_and_card(catalog)
        card_version_id = card.version_id
    evidence = EvidenceObject(
        evidence_id=f"course-evidence-{suffix}", kind="composition", status="verified",
        producer="course-helper/test-upgrades", started_at=NOW, finished_at=NOW,
    )
    catalog.insert_evidence(evidence)
    requirement = CourseRequirement(
        requirement_id=f"course-requirement-{suffix}", title="Governed course", audience="Instructors",
        learning_goals=("Review governed upgrades",), duration_minutes=30, usage_scope="internal",
    )
    catalog.register_course_requirement(requirement, clock=lambda: NOW)
    placement = CardPlacement(
        placement_id=f"course-placement-{suffix}", card_version_id=card_version_id, chapter_id=f"chapter-{suffix}",
        lesson_id=f"lesson-{suffix}", purpose="core", allocated_minutes=30,
    )
    outline = CourseOutline(
        logical_id=f"course-outline-{suffix}", version_id=f"course-outline-{suffix}-v1", revision=1,
        content_digest="1" * 64, created_at=NOW, created_by=ACTOR,
        requirement_id=requirement.requirement_id,
        chapters=(CourseOutlineChapter(chapter_id=f"chapter-{suffix}", title="Governance", objective="Review upgrades", placements=(placement,)),),
        retrieval_evidence_id=evidence.evidence_id, index_snapshot_id=f"course-index-{suffix}-v1",
    )
    outline_payload = canonical_model_json(outline)
    placement_payload = canonical_model_json(placement)
    catalog.connection.execute(
        "INSERT INTO course_outlines(version_id, logical_id, revision, requirement_id, "
        "domain_digest, content_digest, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            outline.version_id, outline.logical_id, outline.revision,
            outline.requirement_id, outline.content_digest,
            _digest(outline_payload), outline_payload, NOW.isoformat(),
        ),
    )
    catalog.connection.execute(
        "INSERT INTO card_placements(placement_id, outline_version_id, card_version_id, "
        "content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            placement.placement_id, outline.version_id, placement.card_version_id,
            _digest(placement_payload), placement_payload, NOW.isoformat(),
        ),
    )
    confirmation = OutlineConfirmation(
        confirmation_id=f"course-confirmation-{suffix}", requirement_id=requirement.requirement_id,
        outline_version_id=outline.version_id, expected_outline_digest=outline.content_digest,
        confirmation_digest=("2" if suffix == "1" else "4") * 64, confirmed_by=ACTOR,
    )
    confirmation_payload = canonical_model_json(confirmation)
    catalog.connection.execute(
        "INSERT INTO outline_confirmations(confirmation_id, requirement_id, "
        "outline_version_id, expected_outline_digest, confirmation_digest, "
        "content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            confirmation.confirmation_id, confirmation.requirement_id,
            confirmation.outline_version_id, confirmation.expected_outline_digest,
            confirmation.confirmation_digest, _digest(confirmation_payload),
            confirmation_payload, NOW.isoformat(),
        ),
    )
    course = CourseVersion(
        logical_id=f"course-{suffix}", version_id=f"course-v{suffix}", revision=1, content_digest=("3" if suffix == "1" else "5") * 64,
        created_at=NOW, created_by=ACTOR, requirement_id=requirement.requirement_id,
        outline_version_id=outline.version_id, outline_digest=outline.content_digest,
        placement_ids=(placement.placement_id,), usage_scope="internal",
        confirmation_digest=confirmation.confirmation_digest, status="confirmed",
    )
    course_payload = canonical_model_json(course)
    catalog.connection.execute(
        "INSERT INTO course_versions(version_id, logical_id, revision, requirement_id, "
        "outline_version_id, confirmation_digest, domain_digest, content_digest, "
        "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            course.version_id, course.logical_id, course.revision,
            course.requirement_id, course.outline_version_id,
            course.confirmation_digest, course.content_digest,
            _digest(course_payload), course_payload, NOW.isoformat(),
        ),
    )
    catalog.connection.commit()
    return course.version_id


def _resolve_open_task(catalog: KnowledgeCatalog, task_id: str) -> None:
    task = ReviewTask.model_validate_json(
        catalog.connection.execute(
            "SELECT payload_json FROM review_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()[0],
        strict=False,
    )
    digest = hashlib.sha256(canonical_model_json(task).encode("utf-8")).hexdigest()
    resolve_review_task(
        catalog,
        ReviewResolution(
            resolution_id=f"resolution-{_digest(task_id)[:32]}",
            task_id=task.task_id,
            decision="accept",
            expected_review_digest=digest,
            evidence_ids=task.evidence_ids,
            resolved_at=NOW,
            resolved_by=ACTOR,
        ),
    )


def test_chunk_diff_reports_changed_unchanged_and_removed_field_digests() -> None:
    before = (
        _chunk("source-v1", "same-v1", "same", 0),
        _chunk("source-v1", "changed-v1", "old fact", 1),
        _chunk("source-v1", "removed-v1", "removed", 2),
    )
    after = (
        _chunk("source-v2", "same-v2", "same", 0),
        _chunk("source-v2", "changed-v2", "new fact", 1),
    )

    changes = detect_chunk_changes(before, after)

    assert [change.kind for change in changes] == [
        ChunkChangeKind.unchanged,
        ChunkChangeKind.changed,
        ChunkChangeKind.removed,
    ]
    assert changes[1].field_diffs[0].field_name == "normalized_text"
    assert changes[1].field_diffs[0].before_digest == _digest("old fact")
    assert changes[1].field_diffs[0].after_digest == _digest("new fact")
    assert changes[2].current_chunk_id is None
    assert any(diff.field_name == "normalized_text" for diff in changes[2].field_diffs)

    heading_only = _chunk("source-v2", "same-heading-v2", "same", 0).model_copy(
        update={"heading": "Changed heading"}
    )
    added = _chunk("source-v2", "added-v2", "added", 3)
    richer_changes = detect_chunk_changes((before[0],), (heading_only, added))
    assert [item.kind for item in richer_changes] == [ChunkChangeKind.changed, ChunkChangeKind.added]
    assert richer_changes[0].field_diffs[0].field_name == "heading"


def test_source_change_creates_reusable_candidate_without_mutating_old_card(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "upgrades.db") as catalog:
        source, old_chunk, old_card = _persist_source_and_card(catalog)
        second_card = publish_card(
            _card(source, old_chunk).model_copy(
                update={"logical_id": "upgrade-card-second", "version_id": "card-second", "title": "Second source-backed lesson", "status": "review"}
            ),
            catalog,
        )
        affected_course_ids = tuple(sorted((
            _persist_course(catalog, old_card.version_id, suffix="1"),
            _persist_course(catalog, second_card.version_id, suffix="2"),
        )))
        before_old_card = catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?", (old_card.version_id,)
        ).fetchone()[0]
        before_courses = catalog.connection.execute(
            "SELECT payload_json FROM course_versions ORDER BY version_id"
        ).fetchall()
        current = _source("source-v2", 2, "updated source")
        new_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(new_chunk)

        first = propose_source_change_upgrades(
            catalog,
            previous_source_version_id=source.version_id,
            current_source_version_id=current.version_id,
            previous_chunks=(old_chunk,),
            current_chunks=(new_chunk,),
            actor=ACTOR,
            occurred_at=NOW,
        )
        replay_counts_before = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("evidence", "cards", "review_tasks", "upgrade_suggestions")
        }
        replay = propose_source_change_upgrades(
            catalog,
            previous_source_version_id=source.version_id,
            current_source_version_id=current.version_id,
            previous_chunks=(old_chunk,),
            current_chunks=(new_chunk,),
            actor=REPLAY_ACTOR,
            occurred_at=NOW + timedelta(hours=1),
        )

        assert replay == first
        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in replay_counts_before
        } == replay_counts_before
        assert len(first.card_suggestions) == 2
        suggestion = next(
            item for item in first.card_suggestions if item.current_version_id == old_card.version_id
        )
        candidate = catalog.get_card(suggestion.candidate_version_id)
        assert candidate is not None and candidate.status == "review"
        assert candidate.supersedes_version_id == old_card.version_id
        assert candidate.chunk_citations[0].chunk_id == "chunk-v2"
        assert catalog.get_card(old_card.version_id).chunk_citations[0].chunk_id == "chunk-v1"
        assert catalog.connection.execute("SELECT count(*) FROM upgrade_suggestions").fetchone()[0] == 3
        source_task_payload = catalog.connection.execute(
            "SELECT payload_json FROM review_tasks WHERE task_id = ?", (first.source_suggestion.review_task_id,)
        ).fetchone()[0]
        assert ReviewTask.model_validate_json(source_task_payload, strict=False).created_by == ACTOR
        near_task_id = catalog.connection.execute(
            "SELECT task_id FROM review_tasks WHERE subject_version_id = ? AND kind = 'near-duplicate'",
            (candidate.version_id,),
        ).fetchone()[0]
        near_evidence_id = ReviewTask.model_validate_json(
            catalog.connection.execute(
                "SELECT payload_json FROM review_tasks WHERE task_id = ?", (near_task_id,)
            ).fetchone()[0],
            strict=False,
        ).evidence_ids[0]
        assert "course-helper/near-duplicates" in catalog.connection.execute(
            "SELECT payload_json FROM evidence WHERE evidence_id = ?", (near_evidence_id,)
        ).fetchone()[0]
        assert first.affected_course_version_ids == affected_course_ids
        assert first.affected_snapshot.content_digest

        with pytest.raises(PublishBlocked, match="open blocking review task"):
            publish_card(candidate, catalog)
        with pytest.raises(CatalogReferenceError, match="evidence"):
            resolve_upgrade_suggestion(
                catalog,
                suggestion_id=suggestion.suggestion_id,
                decision="accept",
                actor=ACTOR,
                evidence_ids=("missing-evidence",),
                resolved_at=NOW,
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_resolutions WHERE task_id = ?", (suggestion.review_task_id,)
        ).fetchone()[0] == 0
        resolved = resolve_upgrade_suggestion(
            catalog,
            suggestion_id=suggestion.suggestion_id,
            decision="accept",
            actor=ACTOR,
            evidence_ids=(first.evidence_id,),
            resolved_at=NOW,
        )
        assert resolved.decision == "accept"
        with pytest.raises(ImmutableVersionConflict):
            resolve_upgrade_suggestion(
                catalog,
                suggestion_id=suggestion.suggestion_id,
                decision="accept",
                actor=ACTOR,
                evidence_ids=(first.evidence_id,),
                resolved_at=NOW - timedelta(seconds=1),
            )
        assert len(resolved.next_required_review_task_ids) == 2
        assert catalog.get_card(old_card.version_id).status == "published"
        assert catalog.get_card(second_card.version_id).status == "published"
        with pytest.raises(PublishBlocked, match="open blocking review task"):
            publish_card(candidate, catalog)
        for task_id in resolved.next_required_review_task_ids:
            _resolve_open_task(catalog, task_id)
        published = publish_card(candidate, catalog)
        assert published.version_id != candidate.version_id
        assert published.supersedes_version_id == candidate.version_id
        assert published.status == "published"
        assert catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?", (old_card.version_id,)
        ).fetchone()[0] == before_old_card
        assert catalog.connection.execute(
            "SELECT payload_json FROM course_versions ORDER BY version_id"
        ).fetchall() == before_courses


def test_dataset_schema_and_visual_changes_are_typed_and_reused(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "asset-upgrades.db") as catalog:
        profile = EvidenceObject(
            evidence_id="dataset-profile-upgrades", kind="dataset-profile", status="verified",
            producer="course-helper/test-upgrades", started_at=NOW, finished_at=NOW,
        )
        catalog.insert_evidence(profile)
        datasets = tuple(
            DatasetAssetVersion(
                logical_id="dataset-upgrade", version_id=f"dataset-v{revision}", revision=revision,
                content_digest=str(revision) * 64, supersedes_version_id="dataset-v1" if revision == 2 else None,
                created_at=NOW, created_by=ACTOR,
                locator=SourceLocator(root_id="fixtures", relative_path="dataset.csv"), format="csv",
                row_count=2, columns=(DatasetColumn(name="value", data_type="INTEGER" if revision == 1 else "TEXT", nullable=False),),
                grain="one row per value", review_status="ready", evidence=profile,
            )
            for revision in (1, 2)
        )
        visuals = tuple(
            VisualAssetVersion(
                logical_id="visual-upgrade", version_id=f"visual-v{revision}", revision=revision,
                content_digest=str(revision + 2) * 64, supersedes_version_id="visual-v1" if revision == 2 else None,
                created_at=NOW, created_by=ACTOR, media_type="image/png",
                alt_text="Original visual" if revision == 1 else "Updated visual",
                license_status="source-provided", authenticity="source-provided",
            )
            for revision in (1, 2)
        )
        for dataset in datasets:
            catalog.insert_dataset(dataset)
        for visual in visuals:
            catalog.insert_visual(visual)

        schema = propose_asset_change_upgrade(
            catalog, previous_version_id="dataset-v1", current_version_id="dataset-v2",
            change_kind="schema", actor=ACTOR, occurred_at=NOW,
        )
        visual = propose_asset_change_upgrade(
            catalog, previous_version_id="visual-v1", current_version_id="visual-v2",
            change_kind="visual", actor=ACTOR, occurred_at=NOW,
        )

        assert any(diff.field_name == "columns" for diff in schema.field_diffs)
        assert any(diff.field_name == "content_digest" for diff in schema.field_diffs)
        assert any(diff.field_name == "alt_text" for diff in visual.field_diffs)
        assert any(diff.field_name == "content_digest" for diff in visual.field_diffs)
        assert propose_asset_change_upgrade(
            catalog, previous_version_id="dataset-v1", current_version_id="dataset-v2",
            change_kind="schema", actor=ACTOR, occurred_at=NOW,
        ) == schema
        asset_outcome = resolve_upgrade_suggestion(
            catalog,
            suggestion_id=schema.suggestion.suggestion_id,
            decision="accept",
            actor=ACTOR,
            evidence_ids=(schema.evidence_id,),
            resolved_at=NOW,
        )
        assert asset_outcome.next_action == "review_affected_knowledge"
        dataset_non_descendant = datasets[1].model_copy(
            update={
                "version_id": "dataset-v3-non-descendant",
                "revision": 3,
                "content_digest": "8" * 64,
                "supersedes_version_id": None,
            }
        )
        visual_non_descendant = visuals[1].model_copy(
            update={
                "version_id": "visual-v3-non-descendant",
                "revision": 3,
                "content_digest": "9" * 64,
                "supersedes_version_id": None,
            }
        )
        catalog.insert_dataset(dataset_non_descendant)
        catalog.insert_visual(visual_non_descendant)
        tracked = ("evidence", "review_tasks", "upgrade_suggestions")
        before = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        }
        with pytest.raises(CatalogReferenceError, match="newer immutable descendant"):
            propose_asset_change_upgrade(
                catalog, previous_version_id="dataset-v1", current_version_id=dataset_non_descendant.version_id,
                change_kind="schema", actor=ACTOR, occurred_at=NOW,
            )
        with pytest.raises(CatalogReferenceError, match="newer immutable descendant"):
            propose_asset_change_upgrade(
                catalog, previous_version_id="visual-v1", current_version_id=visual_non_descendant.version_id,
                change_kind="visual", actor=ACTOR, occurred_at=NOW,
            )
        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        } == before


def test_rejected_candidate_and_dismissed_feedback_have_no_follow_up_action(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "terminal-actions.db") as catalog:
        source, old_chunk, _ = _persist_source_and_card(catalog)
        current = _source("source-v2", 2, "updated source")
        new_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(new_chunk)
        source_result = propose_source_change_upgrades(
            catalog, previous_source_version_id=source.version_id, current_source_version_id=current.version_id,
            previous_chunks=(old_chunk,), current_chunks=(new_chunk,), actor=ACTOR, occurred_at=NOW,
        )
        card_suggestion = source_result.card_suggestions[0]
        rejected = resolve_upgrade_suggestion(
            catalog, suggestion_id=card_suggestion.suggestion_id, decision="reject", actor=ACTOR,
            evidence_ids=(source_result.evidence_id,), resolved_at=NOW,
        )
        assert rejected.next_action == "no_action" and rejected.next_required_review_task_ids == ()
        candidate = catalog.get_card(card_suggestion.candidate_version_id)
        open_task_ids = tuple(
            row[0]
            for row in catalog.connection.execute(
                "SELECT task_id FROM review_task_current WHERE task_id IN "
                "(SELECT task_id FROM review_tasks WHERE subject_version_id = ?) "
                "AND current_status = 'open'",
                (candidate.version_id,),
            ).fetchall()
        )
        for task_id in open_task_ids:
            _resolve_open_task(catalog, task_id)
        with pytest.raises(PublishBlocked, match="accepted source-change"):
            publish_card(candidate, catalog)

        course_version_id = _persist_course(catalog, suffix="feedback")
        evidence = EvidenceObject(
            evidence_id="feedback-terminal-evidence", kind="composition", status="verified",
            producer="course-helper/test-upgrades", started_at=NOW, finished_at=NOW,
        )
        catalog.insert_evidence(evidence)
        feedback = propose_course_feedback(
            catalog, course_version_id=course_version_id, summary="Dismiss this feedback.", actor=ACTOR,
            evidence_ids=(evidence.evidence_id,), occurred_at=NOW,
        )
        dismissed = resolve_upgrade_suggestion(
            catalog, suggestion_id=feedback.suggestion_id, decision="dismiss", actor=ACTOR,
            evidence_ids=(evidence.evidence_id,), resolved_at=NOW,
        )
        assert dismissed.next_action == "no_action" and dismissed.next_required_review_task_ids == ()


def test_failed_source_and_feedback_proposals_leave_no_review_or_suggestion_orphans(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "proposal-rollback.db") as catalog:
        source, old_chunk, _ = _persist_source_and_card(catalog)
        before = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("review_tasks", "upgrade_suggestions", "feedback_suggestions")
        }
        with pytest.raises(CatalogReferenceError):
            propose_source_change_upgrades(
                catalog, previous_source_version_id=source.version_id, current_source_version_id="missing-source-v2",
                previous_chunks=(old_chunk,), current_chunks=(), actor=ACTOR, occurred_at=NOW,
            )
        course_version_id = _persist_course(catalog, suffix="rollback")
        with pytest.raises(CatalogReferenceError, match="evidence"):
            propose_course_feedback(
                catalog, course_version_id=course_version_id, summary="Missing evidence must fail.", actor=ACTOR,
                evidence_ids=("missing-evidence",), occurred_at=NOW,
            )
        after = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("review_tasks", "upgrade_suggestions", "feedback_suggestions")
        }
        assert after == before


def test_source_lineage_and_chunk_binding_fail_before_proposal_writes(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "source-input-validation.db") as catalog:
        source, old_chunk, old_card = _persist_source_and_card(catalog)
        wrong_logical = _source("wrong-logical-source-v2", 2, "different logical source").model_copy(
            update={"logical_id": "different-logical-source", "supersedes_version_id": source.version_id}
        )
        catalog.insert_source(wrong_logical)
        tracked = ("evidence", "cards", "review_tasks", "upgrade_suggestions")
        before_counts = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        }
        before_card = catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?", (old_card.version_id,)
        ).fetchone()[0]
        with pytest.raises(CatalogReferenceError, match="source-change versions"):
            propose_source_change_upgrades(
                catalog,
                previous_source_version_id=source.version_id,
                current_source_version_id=wrong_logical.version_id,
                previous_chunks=(old_chunk,),
                current_chunks=(),
                actor=ACTOR,
                occurred_at=NOW,
            )
        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        } == before_counts
        assert catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?", (old_card.version_id,)
        ).fetchone()[0] == before_card

        current = _source("source-v2", 2, "updated source")
        current_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(current_chunk)
        with pytest.raises(CatalogReferenceError, match="chunk.*wrong source"):
            propose_source_change_upgrades(
                catalog,
                previous_source_version_id=source.version_id,
                current_source_version_id=current.version_id,
                previous_chunks=(old_chunk.model_copy(update={"source_version_id": current.version_id}),),
                current_chunks=(current_chunk,),
                actor=ACTOR,
                occurred_at=NOW,
            )
        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        } == before_counts


def test_late_source_suggestion_failure_rolls_back_candidate_scan_and_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import course_helper.upgrades as upgrades

    with KnowledgeCatalog.open(tmp_path / "source-bundle-rollback.db") as catalog:
        source, old_chunk, _ = _persist_source_and_card(catalog)
        current = _source("source-v2", 2, "updated source")
        current_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(current_chunk)
        tracked = ("evidence", "cards", "review_tasks", "upgrade_suggestions")
        before = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        }
        monkeypatch.setattr(
            upgrades,
            "register_upgrade_suggestion",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("late suggestion failure")),
        )
        with pytest.raises(RuntimeError, match="late suggestion failure"):
            propose_source_change_upgrades(
                catalog, previous_source_version_id=source.version_id, current_source_version_id=current.version_id,
                previous_chunks=(old_chunk,), current_chunks=(current_chunk,), actor=ACTOR, occurred_at=NOW,
            )
        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        } == before


def test_replay_rejects_preoccupied_suggestion_with_wrong_reason_or_task(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "replay-wrong-suggestion-envelope.db") as catalog:
        source, old_chunk, _ = _persist_source_and_card(catalog)
        current = _source("source-v2", 2, "updated source")
        current_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(current_chunk)
        result = propose_source_change_upgrades(
            catalog, previous_source_version_id=source.version_id,
            current_source_version_id=current.version_id, previous_chunks=(old_chunk,),
            current_chunks=(current_chunk,), actor=ACTOR, occurred_at=NOW,
        )
        suggestion = result.card_suggestions[0]
        bad_task = create_review_task(
            catalog, kind="manual-review", subject_version_id=suggestion.candidate_version_id,
            evidence_ids=(result.evidence_id,), created_at=NOW, created_by=ACTOR,
        )
        bad_suggestion = suggestion.model_copy(
            update={"review_task_id": bad_task.task_id, "reason_code": "manual-review"}
        )
        bad_payload = canonical_model_json(bad_suggestion)
        original_created_at = catalog.connection.execute(
            "SELECT created_at FROM upgrade_suggestions WHERE suggestion_id = ?",
            (suggestion.suggestion_id,),
        ).fetchone()[0]
        catalog.connection.execute("DROP TRIGGER upgrade_suggestions_immutable_update")
        catalog.connection.execute(
            "UPDATE upgrade_suggestions SET review_task_id = ?, content_digest = ?, payload_json = ?, created_at = ? "
            "WHERE suggestion_id = ?",
            (bad_task.task_id, _digest(bad_payload), bad_payload, original_created_at, suggestion.suggestion_id),
        )
        catalog.connection.commit()
        tracked = ("evidence", "cards", "review_tasks", "upgrade_suggestions")
        before = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        }

        with pytest.raises(ImmutableVersionConflict, match="semantic binding"):
            propose_source_change_upgrades(
                catalog, previous_source_version_id=source.version_id,
                current_source_version_id=current.version_id, previous_chunks=(old_chunk,),
                current_chunks=(current_chunk,), actor=REPLAY_ACTOR,
                occurred_at=NOW + timedelta(hours=1),
            )

        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        } == before


def test_replay_rejects_preoccupied_suggestion_missing_candidate_gate_evidence(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "replay-missing-gates.db") as catalog:
        source, old_chunk, _ = _persist_source_and_card(catalog)
        current = _source("source-v2", 2, "updated source")
        current_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(current_chunk)
        result = propose_source_change_upgrades(
            catalog, previous_source_version_id=source.version_id,
            current_source_version_id=current.version_id, previous_chunks=(old_chunk,),
            current_chunks=(current_chunk,), actor=ACTOR, occurred_at=NOW,
        )
        suggestion = result.card_suggestions[0]
        provenance_payload = catalog.connection.execute(
            "SELECT payload_json FROM review_tasks WHERE kind = 'provenance' "
            "AND subject_version_id = ?",
            (suggestion.candidate_version_id,),
        ).fetchone()[0]
        provenance_task = ReviewTask.model_validate_json(provenance_payload, strict=False)
        catalog.connection.execute(
            "DELETE FROM evidence WHERE evidence_id = ?", (provenance_task.evidence_ids[0],)
        )
        catalog.connection.commit()
        tracked = ("evidence", "cards", "review_tasks", "upgrade_suggestions")
        before = {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        }

        with pytest.raises(CatalogReferenceError, match="evidence"):
            propose_source_change_upgrades(
                catalog, previous_source_version_id=source.version_id,
                current_source_version_id=current.version_id, previous_chunks=(old_chunk,),
                current_chunks=(current_chunk,), actor=REPLAY_ACTOR,
                occurred_at=NOW + timedelta(hours=1),
            )

        assert {
            table: catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tracked
        } == before


def test_same_evidence_id_with_changed_semantics_is_not_reused(tmp_path: Path) -> None:
    import course_helper.upgrades as upgrades

    with KnowledgeCatalog.open(tmp_path / "evidence-semantic-conflict.db") as catalog:
        first = EvidenceObject(
            evidence_id="same-evidence-id", kind="validation", status="verified",
            input_summary={"meaning": "first"}, producer="course-helper/test-upgrades",
            started_at=NOW, finished_at=NOW,
        )
        upgrades._reuse_evidence(catalog, first)
        changed = first.model_copy(
            update={"input_summary": {"meaning": "different"}, "started_at": NOW + timedelta(hours=1)}
        )
        with pytest.raises(ImmutableVersionConflict, match="semantic bytes"):
            upgrades._reuse_evidence(catalog, changed)


def test_orphaned_source_change_task_and_early_resolution_are_fail_closed(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "source-gate-and-clock.db") as catalog:
        source, old_chunk, _ = _persist_source_and_card(catalog)
        current = _source("source-v2", 2, "updated source")
        current_chunk = _chunk(current.version_id, "chunk-v2", "Updated governed fact.", 0)
        catalog.insert_source(current)
        catalog.insert_chunk(current_chunk)
        result = propose_source_change_upgrades(
            catalog, previous_source_version_id=source.version_id, current_source_version_id=current.version_id,
            previous_chunks=(old_chunk,), current_chunks=(current_chunk,), actor=ACTOR, occurred_at=NOW,
        )
        suggestion = result.card_suggestions[0]
        with pytest.raises(CatalogReferenceError, match="cannot predate"):
            resolve_upgrade_suggestion(
                catalog, suggestion_id=suggestion.suggestion_id, decision="accept", actor=ACTOR,
                evidence_ids=(result.evidence_id,), resolved_at=NOW - timedelta(seconds=1),
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_resolutions WHERE task_id = ?", (suggestion.review_task_id,)
        ).fetchone()[0] == 0

        catalog.connection.execute("DROP TRIGGER upgrade_suggestions_immutable_delete")
        catalog.connection.execute("DROP TRIGGER upgrade_suggestion_evidence_immutable_delete")
        catalog.connection.execute(
            "DELETE FROM upgrade_suggestion_evidence WHERE suggestion_id = ?", (suggestion.suggestion_id,)
        )
        catalog.connection.execute(
            "DELETE FROM upgrade_suggestions WHERE suggestion_id = ?", (suggestion.suggestion_id,)
        )
        catalog.connection.commit()
        candidate = catalog.get_card(suggestion.candidate_version_id)
        for (task_id,) in catalog.connection.execute(
            "SELECT task_id FROM review_task_current WHERE task_id IN "
            "(SELECT task_id FROM review_tasks WHERE subject_version_id = ?) AND current_status = 'open'",
            (candidate.version_id,),
        ).fetchall():
            _resolve_open_task(catalog, task_id)
        with pytest.raises(PublishBlocked, match="accepted source-change"):
            publish_card(candidate, catalog)


def test_removed_chunk_is_audited_without_fabricating_a_card_candidate(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "removed.db") as catalog:
        source, old_chunk, old_card = _persist_source_and_card(catalog)
        current = _source("source-v2", 2, "source without original section")
        catalog.insert_source(current)

        result = propose_source_change_upgrades(
            catalog,
            previous_source_version_id=source.version_id,
            current_source_version_id=current.version_id,
            previous_chunks=(old_chunk,),
            current_chunks=(),
            actor=ACTOR,
            occurred_at=NOW,
        )

        assert result.unresolved_card_version_ids == (old_card.version_id,)
        assert result.card_suggestions == ()
        evidence = catalog.connection.execute(
            "SELECT payload_json FROM evidence WHERE evidence_id = ?", (result.evidence_id,)
        ).fetchone()[0]
        assert "removed" in evidence
        assert catalog.get_card(old_card.version_id).version_id == old_card.version_id


def test_course_feedback_is_typed_and_dismissal_is_actor_and_evidence_audited(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "feedback.db") as catalog:
        course_version_id = _persist_course(catalog)
        evidence = EvidenceObject(
            evidence_id="feedback-evidence", kind="composition", status="verified",
            producer="course-helper/test-upgrades", started_at=NOW, finished_at=NOW,
        )
        catalog.insert_evidence(evidence)

        suggestion = propose_course_feedback(
            catalog,
            course_version_id=course_version_id,
            summary="Learners requested another grounded example.",
            actor=ActorRef(actor_type="human", actor_id="instructor-1"),
            evidence_ids=(evidence.evidence_id,),
            occurred_at=NOW,
        )
        dismissed = resolve_upgrade_suggestion(
            catalog,
            suggestion_id=suggestion.suggestion_id,
            decision="dismiss",
            actor=ActorRef(actor_type="human", actor_id="reviewer-1"),
            evidence_ids=(evidence.evidence_id,),
            resolved_at=NOW,
        )

        assert suggestion.course_version_id == course_version_id
        assert dismissed.decision == "dismiss"
        audit = catalog.connection.execute(
            "SELECT payload_json FROM review_resolutions WHERE task_id = ?", (suggestion.review_task_id,)
        ).fetchone()[0]
        assert "reviewer-1" in audit and "feedback-evidence" in audit
        distinct = propose_course_feedback(
            catalog,
            course_version_id=course_version_id,
            summary="Learners requested another grounded example.",
            actor=REPLAY_ACTOR,
            evidence_ids=(evidence.evidence_id,),
            occurred_at=NOW + timedelta(hours=1),
        )
        assert distinct.suggestion_id != suggestion.suggestion_id
        assert distinct.review_task_id != suggestion.review_task_id
