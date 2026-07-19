from __future__ import annotations

import sqlite3
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from course_helper.catalog import (
    CatalogMigrationError,
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    _execute_migration_sql,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.composition import (
    CardPlacement,
    CourseOutline,
    CourseOutlineChapter,
    CourseRequirement,
    CourseVersion,
)
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.knowledge import (
    CardContentNode,
    KnowledgeCardVersion,
    ReviewTask,
    TagVocabularyVersion,
)
from course_helper.domain.sources import (
    DatasetAssetVersion,
    DatasetColumn,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.reviews import (
    CourseFeedbackSuggestion,
    ReviewProjectionError,
    ReviewResolution,
    UpgradeSuggestion,
    rebuild_review_task_projection,
    register_feedback_suggestion,
    register_upgrade_suggestion,
    resolve_review_task,
)
from course_helper.lifecycle import append_card_lifecycle_event


NOW = datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc)
LEGACY_MAPPING = {
    "source-changed": ("source-changed", "source-changed"),
    "near-duplicate": ("near-duplicate", "near-duplicate"),
    "unknown-tag": ("tag", "unknown-tag"),
    "deprecated-tag": ("tag", "deprecated-tag"),
    "tag-conflict": ("tag", "tag-conflict"),
    "citation-missing": ("candidate-card", "citation-missing"),
    "visual-rights": ("visual-rights", "visual-rights"),
    "visual-unverified": ("visual-rights", "visual-unverified"),
    "dataset-reference": ("candidate-card", "dataset-reference"),
    "sensitive-sample": ("candidate-card", "sensitive-sample"),
    "grain-needs-review": ("candidate-card", "grain-needs-review"),
    "provenance": ("candidate-card", "provenance"),
    "manual-review": ("candidate-card", "manual-review"),
}


def _create_real_v1_database(path: Path) -> tuple[dict[str, str], str, str]:
    migration = (
        Path(__file__).parents[1]
        / "course_helper"
        / "migrations"
        / "0001_knowledge_catalog.sql"
    ).read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    _execute_migration_sql(connection, migration)
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        (NOW.isoformat(),),
    )
    actor = ActorRef(actor_type="system", actor_id="legacy-import")
    payloads: dict[str, str] = {}
    for index, kind in enumerate(LEGACY_MAPPING):
        task = ReviewTask(
            task_id=f"legacy-review-{index}",
            kind=kind,
            subject_version_id=f"legacy-subject-{index}",
            status="open",
            blocking=True,
            created_at=NOW,
            created_by=actor,
        )
        payload = canonical_model_json(task)
        payloads[task.task_id] = payload
        connection.execute(
            "INSERT INTO review_tasks(task_id, kind, subject_version_id, status, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (task.task_id, task.kind, task.subject_version_id, task.status, payload),
        )
    visual = VisualAssetVersion(
        logical_id="legacy-visual",
        version_id="legacy-visual-v1",
        revision=1,
        content_digest="b" * 64,
        created_at=NOW,
        created_by=actor,
        media_type="image/png",
        landing_page_url="https://example.test/landing",
        asset_url="https://example.test/final.png",
        license_status="licensed",
        authenticity="licensed-secondary",
    )
    visual_payload = canonical_model_json(visual)
    connection.execute(
        "INSERT INTO visuals(version_id, logical_id, revision, content_digest, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            visual.version_id,
            visual.logical_id,
            visual.revision,
            visual.content_digest,
            visual_payload,
        ),
    )
    connection.commit()
    connection.close()
    return payloads, visual_payload, visual.content_digest


def test_v1_migration_backfills_review_mapping_without_rewriting_legacy_bytes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-v1.db"
    review_payloads, visual_payload, visual_digest = _create_real_v1_database(database)

    with KnowledgeCatalog.open(database) as catalog:
        rows = catalog.connection.execute(
            "SELECT task_id, category, reason_code, review_digest FROM review_task_current "
            "ORDER BY task_id"
        ).fetchall()
        assert {
            task_id: (category, reason_code) for task_id, category, reason_code, _ in rows
        } == {
            task_id: LEGACY_MAPPING[ReviewTask.model_validate_json(payload).kind]
            for task_id, payload in review_payloads.items()
        }
        assert {
            task_id: review_digest for task_id, _, _, review_digest in rows
        } == {
            task_id: hashlib.sha256(payload.encode("utf-8")).hexdigest()
            for task_id, payload in review_payloads.items()
        }
        assert dict(
            catalog.connection.execute(
                "SELECT task_id, payload_json FROM review_tasks ORDER BY task_id"
            ).fetchall()
        ) == review_payloads
        assert catalog.connection.execute(
            "SELECT payload_json, content_digest FROM visuals WHERE version_id = ?",
            ("legacy-visual-v1",),
        ).fetchone() == (visual_payload, visual_digest)

    with KnowledgeCatalog.open(database) as reopened:
        before = reopened.connection.execute(
            "SELECT * FROM review_task_current ORDER BY task_id"
        ).fetchall()
        reopened.connection.execute("DELETE FROM review_task_current")
        rebuild_review_task_projection(reopened.connection)
        assert reopened.connection.execute(
            "SELECT * FROM review_task_current ORDER BY task_id"
        ).fetchall() == before
        assert reopened.connection.execute(
            "SELECT payload_json FROM visuals WHERE version_id = 'legacy-visual-v1'"
        ).fetchone()[0] == visual_payload


def _review(
    kind: str,
    *,
    task_id: str = "review-current",
    subject_version_id: str = "review-subject-v1",
) -> ReviewTask:
    return ReviewTask(
        task_id=task_id,
        kind=kind,
        subject_version_id=subject_version_id,
        status="open",
        blocking=True,
        created_at=NOW,
        created_by=ActorRef(actor_type="human", actor_id="review-author"),
    )


def _persist_review_subject(catalog: KnowledgeCatalog, subject_version_id: str) -> None:
    if catalog._version_exists(subject_version_id):
        return
    catalog.insert_source(
        SourceAssetVersion(
            logical_id=f"logical-{subject_version_id}",
            version_id=subject_version_id,
            revision=1,
            content_digest=hashlib.sha256(subject_version_id.encode("utf-8")).hexdigest(),
            created_at=NOW,
            created_by=ActorRef(actor_type="system", actor_id="review-subject-fixture"),
            locator=SourceLocator(
                root_id="review-fixtures", relative_path=f"{subject_version_id}.md"
            ),
            display_name=f"{subject_version_id}.md",
            source_kind="markdown",
            media_type="text/markdown",
            byte_size=1,
            extraction_status="registered",
        )
    )


def _insert_review_task(catalog: KnowledgeCatalog, task: ReviewTask) -> ReviewTask:
    _persist_review_subject(catalog, task.subject_version_id)
    return catalog.insert_review_task(task)


def test_new_review_task_rejects_a_dangling_subject(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "dangling-review.db") as catalog:
        with pytest.raises(CatalogReferenceError, match="subject"):
            catalog.insert_review_task(_review("manual-review"))
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_tasks"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("kind", "category", "reason_code"),
    (
        ("exact-duplicate", "exact-duplicate", "exact-duplicate"),
        ("course-feedback", "course-feedback", "course-feedback"),
    ),
)
def test_new_review_kinds_have_exact_rebuildable_projection(
    tmp_path: Path, kind: str, category: str, reason_code: str
) -> None:
    with KnowledgeCatalog.open(tmp_path / f"{kind}.db") as catalog:
        task = _review(kind, task_id=f"review-{kind}")
        raw = canonical_model_json(task)
        _insert_review_task(catalog, task)
        assert catalog.connection.execute(
            "SELECT category, reason_code, review_digest FROM review_task_current "
            "WHERE task_id = ?",
            (task.task_id,),
        ).fetchone() == (
            category,
            reason_code,
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )


def test_new_review_task_cannot_claim_resolved_status_without_resolution_event(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "resolved-bypass.db") as catalog:
        with pytest.raises(CatalogReferenceError, match="unresolved and open"):
            _insert_review_task(
                catalog,
                _review("manual-review").model_copy(update={"status": "resolved"})
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_tasks"
        ).fetchone()[0] == 0


def test_new_open_review_task_rejects_resolution_metadata_and_naive_time(
    tmp_path: Path,
) -> None:
    invalid_tasks = (
        _review("manual-review", task_id="review-with-resolution").model_copy(
            update={"resolved_at": NOW, "resolved_by": ActorRef(actor_type="human", actor_id="resolver")}
        ),
        _review("manual-review", task_id="review-with-naive-time").model_copy(
            update={"created_at": NOW.replace(tzinfo=None)}
        ),
    )
    with KnowledgeCatalog.open(tmp_path / "invalid-new-review.db") as catalog:
        for task in invalid_tasks:
            _persist_review_subject(catalog, task.subject_version_id)
            with pytest.raises((CatalogReferenceError, sqlite3.IntegrityError)):
                catalog.insert_review_task(task)

        assert catalog.connection.execute(
            "SELECT count(*) FROM review_tasks"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("case", "created_at"),
    (
        ("missing", None),
        ("naive", "2026-07-17T01:00:00"),
        ("non-text", 7),
    ),
)
def test_raw_new_review_task_requires_text_timezone_aware_created_at(
    tmp_path: Path, case: str, created_at: object
) -> None:
    task = _review("manual-review", task_id=f"review-raw-time-{case}")
    raw = json.loads(canonical_model_json(task))
    if created_at is None:
        raw.pop("created_at")
    else:
        raw["created_at"] = created_at
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with KnowledgeCatalog.open(tmp_path / f"raw-time-{case}.db") as catalog:
        _persist_review_subject(catalog, task.subject_version_id)
        with pytest.raises(sqlite3.IntegrityError, match="facts are invalid"):
            catalog.connection.execute(
                "INSERT INTO review_tasks(task_id, kind, subject_version_id, status, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (task.task_id, task.kind, task.subject_version_id, task.status, payload),
            )


def test_resolution_fails_closed_when_projection_is_missing_or_tampered(
    tmp_path: Path,
) -> None:
    task = _review("manual-review", task_id="review-projection-guard")
    digest = hashlib.sha256(canonical_model_json(task).encode("utf-8")).hexdigest()
    resolution = ReviewResolution(
        resolution_id="resolution-projection-guard",
        task_id=task.task_id,
        decision="accept",
        expected_review_digest=digest,
        resolved_at=NOW,
        resolved_by=ActorRef(actor_type="human", actor_id="reviewer-projection-guard"),
    )
    with KnowledgeCatalog.open(tmp_path / "projection-guard.db") as catalog:
        _insert_review_task(catalog, task)
        catalog.connection.execute(
            "UPDATE review_task_current SET review_digest = ? WHERE task_id = ?",
            ("0" * 64, task.task_id),
        )
        catalog.connection.commit()
        with pytest.raises(CatalogReferenceError):
            resolve_review_task(catalog, resolution)
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_resolutions"
        ).fetchone()[0] == 0
        rebuild_review_task_projection(catalog.connection)
        catalog.connection.execute(
            "DELETE FROM review_task_current WHERE task_id = ?", (task.task_id,)
        )
        catalog.connection.commit()
        with pytest.raises(CatalogReferenceError):
            resolve_review_task(catalog, resolution)
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_resolutions"
        ).fetchone()[0] == 0


def test_review_resolution_is_digest_bound_append_only_and_evidence_checked(
    tmp_path: Path,
) -> None:
    task = _review("manual-review")
    task_digest = hashlib.sha256(canonical_model_json(task).encode("utf-8")).hexdigest()
    actor = ActorRef(actor_type="human", actor_id="reviewer-1")
    resolution = ReviewResolution(
        resolution_id="resolution-1",
        task_id=task.task_id,
        decision="accept",
        expected_review_digest=task_digest,
        evidence_ids=("resolution-evidence-1",),
        resolved_at=NOW,
        resolved_by=actor,
    )
    with KnowledgeCatalog.open(tmp_path / "reviews.db") as catalog:
        _insert_review_task(catalog, task)
        from course_helper.domain.evidence import EvidenceObject

        catalog.insert_evidence(
            EvidenceObject(
                evidence_id="resolution-evidence-1",
                kind="validation",
                status="verified",
                producer="course-helper/tests",
                started_at=NOW,
                finished_at=NOW,
            )
        )
        assert resolve_review_task(catalog, resolution) == resolve_review_task(
            catalog, resolution
        )
        assert catalog.connection.execute(
            "SELECT current_status, resolution_id FROM review_task_current WHERE task_id = ?",
            (task.task_id,),
        ).fetchone() == ("resolved", resolution.resolution_id)

        with pytest.raises((CatalogReferenceError, ImmutableVersionConflict)):
            resolve_review_task(
                catalog,
                resolution.model_copy(
                    update={
                        "resolution_id": "resolution-wrong-digest",
                        "expected_review_digest": "0" * 64,
                    }
                ),
            )
        with pytest.raises((CatalogReferenceError, ImmutableVersionConflict)):
            resolve_review_task(
                catalog,
                resolution.model_copy(
                    update={
                        "resolution_id": "resolution-missing-evidence",
                        "evidence_ids": ("missing-evidence",),
                    }
                ),
            )
        with pytest.raises(ImmutableVersionConflict):
            resolve_review_task(
                catalog,
                resolution.model_copy(
                    update={"resolution_id": "resolution-second-for-task"}
                ),
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_resolutions"
        ).fetchone()[0] == 1


def test_raw_review_resolution_rejects_columns_that_diverge_from_payload(
    tmp_path: Path,
) -> None:
    task = _review("manual-review", task_id="review-raw-resolution-envelope")
    resolution = ReviewResolution(
        resolution_id="resolution-raw-envelope",
        task_id=task.task_id,
        decision="accept",
        expected_review_digest=hashlib.sha256(
            canonical_model_json(task).encode("utf-8")
        ).hexdigest(),
        resolved_at=NOW,
        resolved_by=ActorRef(actor_type="human", actor_id="resolver"),
    )
    payload = canonical_model_json(resolution)
    with KnowledgeCatalog.open(tmp_path / "raw-resolution-envelope.db") as catalog:
        _insert_review_task(catalog, task)
        with pytest.raises(sqlite3.IntegrityError, match="envelope"):
            catalog.connection.execute(
                "INSERT INTO review_resolutions("
                "resolution_id, task_id, decision, expected_review_digest, content_digest, "
                "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    resolution.resolution_id,
                    resolution.task_id,
                    "dismiss",
                    resolution.expected_review_digest,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    payload,
                    json.loads(payload)["resolved_at"],
                ),
            )


def test_resolution_replay_and_projection_rebuild_reject_tampered_envelope(
    tmp_path: Path,
) -> None:
    task = _review("manual-review", task_id="review-tampered-resolution-envelope")
    resolution = ReviewResolution(
        resolution_id="resolution-tampered-envelope",
        task_id=task.task_id,
        decision="accept",
        expected_review_digest=hashlib.sha256(
            canonical_model_json(task).encode("utf-8")
        ).hexdigest(),
        resolved_at=NOW,
        resolved_by=ActorRef(actor_type="human", actor_id="resolver"),
    )
    with KnowledgeCatalog.open(tmp_path / "tampered-resolution-envelope.db") as catalog:
        _insert_review_task(catalog, task)
        resolve_review_task(catalog, resolution)
        catalog.connection.execute("DROP TRIGGER review_resolutions_immutable_update")
        catalog.connection.execute(
            "UPDATE review_resolutions SET decision = 'dismiss' WHERE resolution_id = ?",
            (resolution.resolution_id,),
        )
        catalog.connection.commit()
        with pytest.raises(CatalogReferenceError, match="envelope"):
            resolve_review_task(catalog, resolution)
        with pytest.raises(ReviewProjectionError, match="resolution"):
            rebuild_review_task_projection(catalog.connection)


def test_review_raw_tables_are_immutable_but_projection_rebuilds(tmp_path: Path) -> None:
    task = _review("near-duplicate")
    digest = hashlib.sha256(canonical_model_json(task).encode("utf-8")).hexdigest()
    resolution = ReviewResolution(
        resolution_id="resolution-immutable",
        task_id=task.task_id,
        decision="dismiss",
        expected_review_digest=digest,
        resolved_at=NOW,
        resolved_by=ActorRef(actor_type="human", actor_id="reviewer-immutable"),
    )
    with KnowledgeCatalog.open(tmp_path / "immutable.db") as catalog:
        _insert_review_task(catalog, task)
        resolve_review_task(catalog, resolution)
        expected = catalog.connection.execute(
            "SELECT * FROM review_task_current WHERE task_id = ?", (task.task_id,)
        ).fetchone()
        for table, column, identity in (
            ("review_tasks", "task_id", task.task_id),
            ("review_resolutions", "resolution_id", resolution.resolution_id),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"UPDATE {table} SET payload_json = '{{}}' WHERE {column} = ?",
                    (identity,),
                )
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?", (identity,)
                )
        catalog.connection.execute(
            "UPDATE review_task_current SET category='candidate-card', "
            "reason_code='manual-review', current_status='open', resolution_id=NULL "
            "WHERE task_id = ?",
            (task.task_id,),
        )
        rebuild_review_task_projection(catalog.connection)
        assert catalog.connection.execute(
            "SELECT * FROM review_task_current WHERE task_id = ?", (task.task_id,)
        ).fetchone() == expected


def test_projection_rebuild_fails_closed_for_unknown_raw_kind(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "unknown.db") as catalog:
        _insert_review_task(catalog, _review("manual-review", task_id="known-review"))
        before = catalog.connection.execute(
            "SELECT * FROM review_task_current ORDER BY task_id"
        ).fetchall()
        catalog.connection.execute("DROP TRIGGER review_tasks_known_kind_insert")
        catalog.connection.execute("DROP TRIGGER review_tasks_open_only_insert")
        catalog.connection.execute("DROP TRIGGER review_tasks_projection_insert")
        catalog.connection.execute(
            "INSERT INTO review_tasks(task_id, kind, subject_version_id, status, payload_json) "
            "VALUES ('unknown-review', 'future-kind', 'subject', 'open', '{}')"
        )
        with pytest.raises(ReviewProjectionError):
            rebuild_review_task_projection(catalog.connection)
        assert catalog.connection.execute(
            "SELECT * FROM review_task_current ORDER BY task_id"
        ).fetchall() == before


def _prepare_suggestion_dependencies(
    catalog: KnowledgeCatalog,
) -> tuple[str, str, str, str]:
    creator = ActorRef(actor_type="service", actor_id="suggestion-fixture")
    catalog.insert_vocabulary(
        TagVocabularyVersion(
            logical_id="suggestion-vocabulary",
            version_id="suggestion-vocabulary-v1",
            revision=1,
            content_digest="1" * 64,
            created_at=NOW,
            created_by=creator,
            dimensions=(),
        )
    )
    card_ids = ("suggestion-card-v1", "suggestion-card-v2")
    for revision, version_id in enumerate(card_ids, start=1):
        catalog.insert_card(
            KnowledgeCardVersion(
                logical_id="suggestion-card",
                version_id=version_id,
                revision=revision,
                content_digest=str(revision + 1) * 64,
                supersedes_version_id=card_ids[0] if revision == 2 else None,
                created_at=NOW,
                created_by=creator,
                main_type_id="concept",
                title=f"Suggestion card {revision}",
                learning_objective="Review an explicit candidate",
                content_ast=(CardContentNode(type="paragraph", text="Candidate content."),),
                suggested_minutes=5,
                vocabulary_version_id="suggestion-vocabulary-v1",
                status="review",
            )
        )
    with catalog.connection:
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card_ids[1],
            event_id="publish:suggestion-card-v2",
            request_digest="7" * 64,
            event_type="publish",
            occurred_at=NOW,
            actor_id=creator.actor_id,
        )
    evidence_id = "suggestion-evidence-1"
    catalog.insert_evidence(
        EvidenceObject(
            evidence_id=evidence_id,
            kind="composition",
            status="verified",
            producer="course-helper/tests",
            started_at=NOW,
            finished_at=NOW,
        )
    )
    requirement = CourseRequirement(
        requirement_id="feedback-requirement-1",
        title="Feedback course",
        audience="Facilitators",
        learning_goals=("Collect course feedback",),
        duration_minutes=30,
        usage_scope="internal",
    )
    catalog.register_course_requirement(requirement, clock=lambda: NOW)
    placement = CardPlacement(
        placement_id="feedback-card-placement-1",
        card_version_id=card_ids[1],
        chapter_id="feedback-chapter-1",
        lesson_id="feedback-lesson-1",
        purpose="core",
        allocated_minutes=30,
    )
    outline = CourseOutline(
        logical_id="feedback-outline",
        version_id="feedback-outline-v1",
        revision=1,
        content_digest="4" * 64,
        created_at=NOW,
        created_by=creator,
        requirement_id=requirement.requirement_id,
        chapters=(
            CourseOutlineChapter(
                chapter_id="feedback-chapter-1",
                title="Feedback",
                objective="Collect governed feedback",
                placements=(placement,),
            ),
        ),
        retrieval_evidence_id=evidence_id,
        index_snapshot_id="feedback-index-snapshot-1",
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
            hashlib.sha256(outline_payload.encode("utf-8")).hexdigest(),
            outline_payload, NOW.isoformat(),
        ),
    )
    catalog.connection.execute(
        "INSERT INTO card_placements(placement_id, outline_version_id, card_version_id, "
        "content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            placement.placement_id, outline.version_id, placement.card_version_id,
            hashlib.sha256(placement_payload.encode("utf-8")).hexdigest(),
            placement_payload, NOW.isoformat(),
        ),
    )
    from course_helper.catalog import OutlineConfirmation

    confirmation = OutlineConfirmation(
        confirmation_id="feedback-confirmation-1",
        requirement_id=requirement.requirement_id,
        outline_version_id=outline.version_id,
        expected_outline_digest=outline.content_digest,
        confirmation_digest="5" * 64,
        confirmed_by=creator,
    )
    confirmation_payload = canonical_model_json(confirmation)
    catalog.connection.execute(
        "INSERT INTO outline_confirmations(confirmation_id, requirement_id, "
        "outline_version_id, expected_outline_digest, confirmation_digest, "
        "content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            confirmation.confirmation_id, confirmation.requirement_id,
            confirmation.outline_version_id, confirmation.expected_outline_digest,
            confirmation.confirmation_digest,
            hashlib.sha256(confirmation_payload.encode("utf-8")).hexdigest(),
            confirmation_payload, NOW.isoformat(),
        ),
    )
    course = CourseVersion(
        logical_id="feedback-course",
        version_id="course-feedback-v1",
        revision=1,
        content_digest="6" * 64,
        created_at=NOW,
        created_by=creator,
        requirement_id=requirement.requirement_id,
        outline_version_id=outline.version_id,
        outline_digest=outline.content_digest,
        placement_ids=(placement.placement_id,),
        usage_scope="internal",
        confirmation_digest=confirmation.confirmation_digest,
        status="confirmed",
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
            hashlib.sha256(course_payload.encode("utf-8")).hexdigest(),
            course_payload, NOW.isoformat(),
        ),
    )
    catalog.connection.commit()
    return card_ids[0], card_ids[1], course.version_id, evidence_id


def _suggestion_models(
    current_card_id: str,
    candidate_card_id: str,
    course_version_id: str,
    evidence_id: str,
) -> tuple[UpgradeSuggestion, CourseFeedbackSuggestion]:
    return (
        UpgradeSuggestion(
            suggestion_id="upgrade-envelope",
            current_version_id=current_card_id,
            candidate_version_id=candidate_card_id,
            review_task_id="review-upgrade-envelope",
            reason_code="source-changed",
            evidence_ids=(evidence_id,),
            created_at=NOW,
            created_by=ActorRef(actor_type="service", actor_id="upgrade-worker"),
        ),
        CourseFeedbackSuggestion(
            suggestion_id="feedback-envelope",
            course_version_id=course_version_id,
            review_task_id="review-feedback-envelope",
            summary="Envelope integrity matters.",
            evidence_ids=(evidence_id,),
            created_at=NOW,
            created_by=ActorRef(actor_type="human", actor_id="facilitator"),
        ),
    )


def _insert_suggestion_review_tasks(
    catalog: KnowledgeCatalog,
    upgrade: UpgradeSuggestion,
    feedback: CourseFeedbackSuggestion,
) -> None:
    _insert_review_task(
        catalog,
        _review(
            "source-changed",
            task_id=upgrade.review_task_id,
            subject_version_id=upgrade.candidate_version_id,
        ),
    )
    _insert_review_task(
        catalog,
        _review(
            "course-feedback",
            task_id=feedback.review_task_id,
            subject_version_id=feedback.course_version_id,
        ),
    )


def test_raw_suggestions_reject_denormalized_columns_that_diverge_from_payload(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "raw-suggestion-envelope.db") as catalog:
        dependencies = _prepare_suggestion_dependencies(catalog)
        upgrade, feedback = _suggestion_models(*dependencies)
        _insert_suggestion_review_tasks(catalog, upgrade, feedback)
        upgrade_payload = canonical_model_json(upgrade)
        with pytest.raises(sqlite3.IntegrityError, match="envelope"):
            catalog.connection.execute(
                "INSERT INTO upgrade_suggestions("
                "suggestion_id, review_task_id, current_version_id, candidate_version_id, "
                "content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    upgrade.suggestion_id,
                    upgrade.review_task_id,
                    upgrade.candidate_version_id,
                    upgrade.current_version_id,
                    hashlib.sha256(upgrade_payload.encode("utf-8")).hexdigest(),
                    upgrade_payload,
                    json.loads(upgrade_payload)["created_at"],
                ),
            )
        feedback_payload = canonical_model_json(feedback)
        with pytest.raises(sqlite3.IntegrityError, match="envelope"):
            catalog.connection.execute(
                "INSERT INTO feedback_suggestions("
                "suggestion_id, review_task_id, course_version_id, content_digest, "
                "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    feedback.suggestion_id,
                    upgrade.review_task_id,
                    feedback.course_version_id,
                    hashlib.sha256(feedback_payload.encode("utf-8")).hexdigest(),
                    feedback_payload,
                    json.loads(feedback_payload)["created_at"],
                ),
            )


@pytest.mark.parametrize("kind", ("upgrade", "feedback"))
def test_suggestion_replay_rejects_tampered_denormalized_envelope(
    tmp_path: Path, kind: str
) -> None:
    with KnowledgeCatalog.open(tmp_path / f"tampered-{kind}-envelope.db") as catalog:
        dependencies = _prepare_suggestion_dependencies(catalog)
        upgrade, feedback = _suggestion_models(*dependencies)
        _insert_suggestion_review_tasks(catalog, upgrade, feedback)
        register_upgrade_suggestion(catalog, upgrade)
        register_feedback_suggestion(catalog, feedback)
        if kind == "upgrade":
            catalog.connection.execute("DROP TRIGGER upgrade_suggestions_immutable_update")
            catalog.connection.execute(
                "UPDATE upgrade_suggestions SET current_version_id = candidate_version_id, "
                "candidate_version_id = current_version_id WHERE suggestion_id = ?",
                (upgrade.suggestion_id,),
            )
            catalog.connection.commit()
            replay = lambda: register_upgrade_suggestion(catalog, upgrade)
        else:
            catalog.connection.execute("DROP TRIGGER feedback_suggestions_immutable_update")
            catalog.connection.execute(
                "UPDATE feedback_suggestions SET review_task_id = ? WHERE suggestion_id = ?",
                (upgrade.review_task_id, feedback.suggestion_id),
            )
            catalog.connection.commit()
            replay = lambda: register_feedback_suggestion(catalog, feedback)
        with pytest.raises(CatalogReferenceError, match="envelope"):
            replay()


def test_upgrade_descriptor_rejects_columns_that_diverge_from_version_payload(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "upgrade-version-envelope.db") as catalog:
        dependencies = _prepare_suggestion_dependencies(catalog)
        upgrade, feedback = _suggestion_models(*dependencies)
        _insert_suggestion_review_tasks(catalog, upgrade, feedback)
        catalog.connection.execute("DROP TRIGGER cards_immutable_lifecycle_columns")
        catalog.connection.execute(
            "UPDATE cards SET logical_id = 'forged-logical' "
            "WHERE version_id IN (?, ?)",
            (upgrade.current_version_id, upgrade.candidate_version_id),
        )
        catalog.connection.commit()
        with pytest.raises(CatalogReferenceError, match="version.*raw"):
            register_upgrade_suggestion(catalog, upgrade)


def test_upgrade_and_feedback_suggestions_are_immutable_and_reopen(tmp_path: Path) -> None:
    database = tmp_path / "suggestions.db"
    with KnowledgeCatalog.open(database) as catalog:
        current_card_id, candidate_card_id, course_version_id, evidence_id = (
            _prepare_suggestion_dependencies(catalog)
        )
    upgrade = UpgradeSuggestion(
        suggestion_id="upgrade-1",
        current_version_id=current_card_id,
        candidate_version_id=candidate_card_id,
        review_task_id="review-upgrade",
        reason_code="source-changed",
        evidence_ids=(evidence_id,),
        created_at=NOW,
        created_by=ActorRef(actor_type="service", actor_id="upgrade-worker"),
    )
    feedback = CourseFeedbackSuggestion(
        suggestion_id="feedback-1",
        course_version_id=course_version_id,
        review_task_id="review-feedback",
        summary="Learners need a clearer grounding example.",
        evidence_ids=(evidence_id,),
        created_at=NOW,
        created_by=ActorRef(actor_type="human", actor_id="facilitator-1"),
    )
    with KnowledgeCatalog.open(database) as catalog:
        for task in (
            _review(
                "source-changed",
                task_id=upgrade.review_task_id,
                subject_version_id=upgrade.candidate_version_id,
            ),
            _review(
                "course-feedback",
                task_id=feedback.review_task_id,
                subject_version_id=feedback.course_version_id,
            ),
        ):
            _insert_review_task(catalog, task)
        first_upgrade = register_upgrade_suggestion(catalog, upgrade)
        first_feedback = register_feedback_suggestion(catalog, feedback)
        assert register_upgrade_suggestion(catalog, upgrade) == first_upgrade
        assert register_feedback_suggestion(catalog, feedback) == first_feedback
        with pytest.raises(ImmutableVersionConflict):
            register_upgrade_suggestion(
                catalog, upgrade.model_copy(update={"reason_code": "manual-review"})
            )
        with pytest.raises(ImmutableVersionConflict):
            register_feedback_suggestion(
                catalog, feedback.model_copy(update={"summary": "Changed bytes"})
            )
        for table, column, identity in (
            ("upgrade_suggestions", "suggestion_id", upgrade.suggestion_id),
            ("feedback_suggestions", "suggestion_id", feedback.suggestion_id),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"UPDATE {table} SET payload_json = '{{}}' WHERE {column} = ?",
                    (identity,),
                )
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?", (identity,)
                )
    with KnowledgeCatalog.open(database) as reopened:
        assert UpgradeSuggestion.model_validate_json(
            reopened.connection.execute(
                "SELECT payload_json FROM upgrade_suggestions WHERE suggestion_id = ?",
                (upgrade.suggestion_id,),
            ).fetchone()[0],
            strict=False,
        ) == upgrade
        assert CourseFeedbackSuggestion.model_validate_json(
            reopened.connection.execute(
                "SELECT payload_json FROM feedback_suggestions WHERE suggestion_id = ?",
                (feedback.suggestion_id,),
            ).fetchone()[0],
            strict=False,
        ) == feedback


def test_upgrade_suggestions_support_each_versioned_kind_and_reject_false_lineage(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "upgrade-kinds.db") as catalog:
        card_v1, card_v2, _, evidence_id = _prepare_suggestion_dependencies(catalog)
        creator = ActorRef(actor_type="service", actor_id="upgrade-kind-fixture")
        for revision in (1, 2):
            catalog.insert_source(
                SourceAssetVersion(
                    logical_id="upgrade-source",
                    version_id=f"upgrade-source-v{revision}",
                    revision=revision,
                    content_digest=str(revision) * 64,
                    supersedes_version_id=(
                        "upgrade-source-v1" if revision == 2 else None
                    ),
                    created_at=NOW,
                    created_by=creator,
                    locator=SourceLocator(
                        root_id="upgrade-fixtures",
                        relative_path=f"source-v{revision}.md",
                    ),
                    display_name=f"source-v{revision}.md",
                    source_kind="markdown",
                    media_type="text/markdown",
                    byte_size=revision,
                    extraction_status="registered",
                )
            )
            catalog.insert_visual(
                VisualAssetVersion(
                    logical_id="upgrade-visual",
                    version_id=f"upgrade-visual-v{revision}",
                    revision=revision,
                    content_digest=str(revision + 2) * 64,
                    supersedes_version_id=(
                        "upgrade-visual-v1" if revision == 2 else None
                    ),
                    created_at=NOW,
                    created_by=creator,
                    media_type="image/png",
                    license_status="source-provided",
                    authenticity="source-provided",
                )
            )
            catalog.insert_dataset(
                DatasetAssetVersion(
                    logical_id="upgrade-dataset",
                    version_id=f"upgrade-dataset-v{revision}",
                    revision=revision,
                    content_digest=str(revision + 4) * 64,
                    supersedes_version_id=(
                        "upgrade-dataset-v1" if revision == 2 else None
                    ),
                    created_at=NOW,
                    created_by=creator,
                    locator=SourceLocator(
                        root_id="upgrade-fixtures",
                        relative_path=f"dataset-v{revision}.csv",
                    ),
                    format="csv",
                    row_count=revision,
                    columns=(
                        DatasetColumn(name="value", data_type="INTEGER", nullable=False),
                    ),
                    grain="one row per value",
                    review_status="ready",
                    evidence=EvidenceObject(
                        evidence_id=f"dataset-profile-{revision}",
                        kind="dataset-profile",
                        status="verified",
                        producer="course-helper/tests",
                        started_at=NOW,
                        finished_at=NOW,
                    ),
                )
            )
        pairs = (
            ("card", card_v1, card_v2),
            ("source", "upgrade-source-v1", "upgrade-source-v2"),
            ("dataset", "upgrade-dataset-v1", "upgrade-dataset-v2"),
            ("visual", "upgrade-visual-v1", "upgrade-visual-v2"),
        )
        for kind, current_id, candidate_id in pairs:
            task = _review(
                "source-changed",
                task_id=f"review-upgrade-{kind}",
                subject_version_id=candidate_id,
            )
            catalog.insert_review_task(task)
            register_upgrade_suggestion(
                catalog,
                UpgradeSuggestion(
                    suggestion_id=f"upgrade-{kind}",
                    current_version_id=current_id,
                    candidate_version_id=candidate_id,
                    review_task_id=task.task_id,
                    reason_code="source-changed",
                    evidence_ids=(evidence_id,),
                    created_at=NOW,
                    created_by=creator,
                ),
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM upgrade_suggestions"
        ).fetchone()[0] == 4

        invalid_cases = (
            ("cross-kind", "upgrade-source-v1", "upgrade-visual-v2"),
            ("reversed", "upgrade-source-v2", "upgrade-source-v1"),
        )
        for suffix, current_id, candidate_id in invalid_cases:
            task = _review(
                "source-changed",
                task_id=f"review-invalid-{suffix}",
                subject_version_id=candidate_id,
            )
            catalog.insert_review_task(task)
            with pytest.raises(CatalogReferenceError):
                register_upgrade_suggestion(
                    catalog,
                    UpgradeSuggestion(
                        suggestion_id=f"upgrade-invalid-{suffix}",
                        current_version_id=current_id,
                        candidate_version_id=candidate_id,
                        review_task_id=task.task_id,
                        reason_code="source-changed",
                        evidence_ids=(evidence_id,),
                        created_at=NOW,
                        created_by=creator,
                    ),
                )


def test_failed_0003_migration_rolls_back_and_v1_remains_read_only_reopenable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration-conflict.db"
    _create_real_v1_database(database)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE course_requirements(conflict TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(CatalogMigrationError):
        KnowledgeCatalog.open(database)
    check = sqlite3.connect(database)
    try:
        assert check.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
    finally:
        check.close()
    with KnowledgeCatalog.open_read_only(database) as read_only:
        assert read_only.connection.execute(
            "SELECT count(*) FROM review_tasks"
        ).fetchone()[0] == len(LEGACY_MAPPING)
