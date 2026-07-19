"""Deterministic knowledge-card candidates and governed publication."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

from course_helper.catalog import (
    KnowledgeCatalog,
    card_parent_version_ids,
    canonical_model_json,
    transition_card_status,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject, LineageEdge
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    ReviewTask,
    TagDimension,
    TagValue,
    TagVocabularyVersion,
    VisualReference,
)
from course_helper.domain.sources import (
    DatasetAssetVersion,
    ExtractedChunk,
    ExtractionResult,
    VisualAssetVersion,
)
from course_helper.lifecycle import register_card_lifecycle, reopen_card_version
from course_helper.reviews import ReviewResolution, resolve_review_task
from course_helper.source_roots import candidate_logical_id, candidate_version_id


VOCABULARY_VERSION_ID = "knowledge-vocabulary-v1"
_ACTOR = ActorRef(actor_type="service", actor_id="course-helper/cards")
_FIXED_CREATED_AT = datetime(2026, 7, 16, tzinfo=timezone.utc)


class PublishBlocked(ValueError):
    """Publication was rejected by a fail-closed governance gate."""


def create_review_task(
    catalog: KnowledgeCatalog,
    *,
    kind: Literal[
        "source-changed",
        "near-duplicate",
        "unknown-tag",
        "deprecated-tag",
        "tag-conflict",
        "citation-missing",
        "visual-rights",
        "visual-unverified",
        "dataset-reference",
        "sensitive-sample",
        "grain-needs-review",
        "provenance",
        "manual-review",
        "exact-duplicate",
        "course-feedback",
    ],
    subject_version_id: str,
    blocking: bool = True,
    evidence_ids: tuple[str, ...] = (),
    created_at: datetime = _FIXED_CREATED_AT,
    created_by: ActorRef = _ACTOR,
) -> ReviewTask:
    """Create one deterministic open governance task."""

    task_id = "review-" + _json_digest(
        {
            "kind": kind,
            "subject_version_id": subject_version_id,
            "blocking": blocking,
            "evidence_ids": sorted(evidence_ids),
        }
    )
    task = ReviewTask(
        task_id=task_id,
        kind=kind,
        subject_version_id=subject_version_id,
        status="open",
        blocking=blocking,
        evidence_ids=tuple(sorted(evidence_ids)),
        created_at=created_at,
        created_by=created_by,
    )
    existing_row = catalog.connection.execute(
        "SELECT payload_json FROM review_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if existing_row is not None:
        existing = ReviewTask.model_validate_json(existing_row[0], strict=False)
        if (
            existing.kind == kind
            and existing.subject_version_id == subject_version_id
            and existing.status == "open"
            and existing.blocking == blocking
            and existing.evidence_ids == tuple(sorted(evidence_ids))
        ):
            # Deterministic detection replays retain the original actor and clock
            # audit rather than conflicting on a later observer's metadata.
            return existing
    return catalog.insert_review_task(task)


def seed_vocabulary(
    catalog: KnowledgeCatalog,
    *,
    deprecated_tag_id: str | None = None,
) -> TagVocabularyVersion:
    """Persist immutable controlled vocabulary version 1 idempotently."""

    if deprecated_tag_id not in (None, "tool:legacy"):
        raise ValueError("version 1 only defines tool:legacy as deprecated")
    dimensions = _vocabulary_dimensions()
    vocabulary = TagVocabularyVersion(
        logical_id="knowledge-vocabulary",
        version_id=VOCABULARY_VERSION_ID,
        revision=1,
        content_digest=_json_digest(
            [dimension.model_dump(mode="json") for dimension in dimensions]
        ),
        created_at=_FIXED_CREATED_AT,
        created_by=_ACTOR,
        dimensions=dimensions,
    )
    return catalog.insert_vocabulary(vocabulary)


def _vocabulary_dimensions() -> tuple[TagDimension, ...]:
    return (
        TagDimension(
            id="topic",
            cardinality="many",
            values=(
                _tag("topic:ai-foundations", "AI foundations", "AI 基础", "ai basics"),
                _tag("topic:prompting", "Prompting", "提示工程", "prompt engineering"),
                _tag("topic:data-analysis", "Data analysis", "数据分析", "analytics"),
            ),
        ),
        TagDimension(
            id="audience",
            cardinality="many",
            values=(
                _tag("audience:learner", "Learner", "学习者", "student"),
                _tag("audience:instructor", "Instructor", "讲师", "teacher"),
                _tag("audience:analyst", "Analyst", "分析师", "data analyst"),
            ),
        ),
        TagDimension(
            id="difficulty",
            cardinality="one",
            values=(
                _tag("difficulty:beginner", "Beginner", "入门", "introductory"),
                _tag("difficulty:intermediate", "Intermediate", "进阶", "developing"),
                _tag("difficulty:advanced", "Advanced", "高级", "expert"),
            ),
        ),
        TagDimension(
            id="pedagogy",
            cardinality="many",
            values=(
                _tag("pedagogy:explain", "Explain", "讲解", "concept explanation"),
                _tag("pedagogy:demonstrate", "Demonstrate", "演示", "demo"),
                _tag("pedagogy:practice", "Practice", "练习", "guided practice"),
                _tag("pedagogy:assess", "Assess", "评估", "assessment"),
            ),
        ),
        TagDimension(
            id="tool",
            cardinality="many",
            values=(
                _tag("tool:agnostic", "Tool agnostic", "工具无关", "none"),
                _tag("tool:spreadsheet", "Spreadsheet", "电子表格", "excel"),
                _tag("tool:python", "Python", "Python", "py"),
                _tag(
                    "tool:legacy",
                    "Legacy tool",
                    "旧版工具",
                    "deprecated tool",
                    status="deprecated",
                    replaced_by="tool:agnostic",
                ),
            ),
        ),
        TagDimension(
            id="scenario",
            cardinality="many",
            values=(
                _tag("scenario:course-learning", "Course learning", "课程学习", "training"),
                _tag(
                    "scenario:prompt-engineering",
                    "Prompt engineering",
                    "提示词设计",
                    "prompt design",
                ),
                _tag(
                    "scenario:data-analysis",
                    "Data-analysis workflow",
                    "数据分析工作流",
                    "analytics workflow",
                ),
            ),
        ),
        TagDimension(
            id="dataType",
            cardinality="many",
            values=(
                _tag("dataType:text", "Text", "文本", "markdown"),
                _tag("dataType:presentation", "Presentation", "演示文稿", "slides"),
                _tag("dataType:tabular", "Tabular data", "表格数据", "dataset"),
            ),
        ),
    )


def _tag(
    tag_id: str,
    english_label: str,
    chinese_label: str,
    *aliases: str,
    status: str = "active",
    replaced_by: str | None = None,
) -> TagValue:
    return TagValue(
        id=tag_id,
        labels={"en": english_label, "zh-CN": chinese_label},
        aliases=aliases,
        status=status,
        replaced_by=replaced_by,
    )


def build_candidates(extraction: ExtractionResult) -> tuple[KnowledgeCardVersion, ...]:
    """Build deterministic, unpublished cards from one extraction result."""

    if extraction.source.source_kind == "pptx":
        groups = _pptx_groups(extraction.chunks)
    elif extraction.source.source_kind == "markdown":
        groups = _markdown_groups(extraction.chunks)
    else:
        groups = tuple((chunk,) for chunk in extraction.chunks)
    return tuple(_candidate_from_chunks(extraction, group) for group in groups)


def find_exact_duplicate(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> KnowledgeCardVersion | None:
    """Return an existing published card with byte-identical canonical content."""

    row = catalog.connection.execute(
        """
        SELECT cards.payload_json
        FROM cards
        JOIN card_lifecycle_current lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE lifecycle.status = 'published'
          AND lifecycle.suspended = 0
          AND cards.content_digest = ?
          AND cards.version_id <> ?
        ORDER BY cards.version_id
        LIMIT 1
        """,
        (_canonical_card_digest(card), card.version_id),
    ).fetchone()
    if row is None:
        return None
    raw = KnowledgeCardVersion.model_validate_json(row[0])
    existing = reopen_card_version(catalog.connection, raw.version_id).card
    if _canonical_card_digest(existing) != _canonical_card_digest(card):
        raise PublishBlocked("stored exact-duplicate digest does not match canonical content")
    return existing


def publish_card(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> KnowledgeCardVersion:
    """Publish one card in a serialized, all-or-nothing transaction."""

    connection = catalog.connection
    if connection.in_transaction:
        raise PublishBlocked("card publication cannot run inside an active transaction")
    try:
        with catalog.atomic_write():
            return _publish_card_in_transaction(card, catalog)
    except PublishBlocked:
        raise
    except sqlite3.Error:
        raise PublishBlocked("card publication failed") from None


def publish_card_in_operation(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> KnowledgeCardVersion:
    """Publish while a durable operation owns the outer atomic transaction."""

    if catalog._atomic_depth <= 0 or not catalog.connection.in_transaction:
        raise PublishBlocked("operation publication requires an active transaction")
    return _publish_card_in_transaction(card, catalog)


def _publish_card_in_transaction(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> KnowledgeCardVersion:
    """Validate and write while ``publish_card`` owns the transaction."""

    existing_row = catalog.connection.execute(
        "SELECT payload_json FROM cards WHERE version_id = ?",
        (card.version_id,),
    ).fetchone()
    if existing_row is not None:
        raw_existing = KnowledgeCardVersion.model_validate_json(existing_row[0])
        reopened_existing = reopen_card_version(
            catalog.connection,
            raw_existing.version_id,
        )
        if reopened_existing.suspended:
            raise PublishBlocked("suspended card version cannot be published")
        existing = reopened_existing.card
        if existing.status == "published":
            if _same_concrete_content(card, existing):
                return existing
        if existing.status == "archived":
            if _same_submission_content(card, existing):
                target_rows = catalog.connection.execute(
                    """
                    SELECT cards.version_id
                    FROM lineage
                    JOIN cards ON cards.version_id = lineage.to_version_id
                    JOIN card_lifecycle_current lifecycle
                      ON lifecycle.card_version_id = cards.version_id
                    WHERE lineage.from_version_id = ?
                      AND lineage.relation = 'deduplicates'
                      AND lifecycle.status = 'published'
                      AND lifecycle.suspended = 0
                    ORDER BY lineage.to_version_id
                    """,
                    (existing.version_id,),
                ).fetchall()
                if len(target_rows) == 1:
                    target = reopen_card_version(
                        catalog.connection,
                        target_rows[0][0],
                    ).card
                    if target.version_id != existing.version_id:
                        return target
                    raise PublishBlocked("archived duplicate has invalid deduplication lineage")
        if existing.logical_id != card.logical_id or card.status != "review":
            raise PublishBlocked("card version already exists with different immutable content")
    if card.status != "review":
        raise PublishBlocked("card status must be review")
    _validate_vocabulary(card, catalog)
    _validate_references(card, catalog)
    _validate_no_blocking_reviews(card, catalog)
    published = _with_effective_version_meta(card, catalog, status="published")
    effective_existing = _stored_version_outcome(published, catalog)
    if effective_existing is not None:
        return effective_existing
    duplicate = find_exact_duplicate(published, catalog)
    if duplicate is not None:
        archived = _with_status_and_digest(published, status="archived")
        _insert_card(catalog, archived)
        evidence = _dedup_evidence(archived, duplicate)
        _insert_evidence(catalog, evidence)
        _insert_dedup_lineage(catalog, archived, duplicate, evidence)
        _record_exact_duplicate_audit(catalog, archived, evidence)
        return duplicate
    _supersede_published_revisions(catalog, published)
    _insert_card(catalog, published)
    evidence = _publication_evidence(published)
    _insert_evidence(catalog, evidence)
    _insert_reference_lineage(catalog, published, evidence)
    return published


def _validate_vocabulary(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> None:
    vocabulary_row = catalog.connection.execute(
        "SELECT payload_json FROM tag_vocabularies WHERE version_id = ?",
        (card.vocabulary_version_id,),
    ).fetchone()
    if vocabulary_row is None:
        raise PublishBlocked("pinned vocabulary version is not persisted")
    vocabulary = TagVocabularyVersion.model_validate_json(vocabulary_row[0])
    cardinality_by_dimension = {
        dimension.id: dimension.cardinality for dimension in vocabulary.dimensions
    }
    assignment_keys = tuple(
        (assignment.vocabulary_version_id, assignment.tag_id)
        for assignment in card.tag_assignments
    )
    if len(set(assignment_keys)) != len(assignment_keys):
        raise PublishBlocked("duplicate tag assignment")
    assigned_values: dict[str, set[str]] = {}
    for assignment in card.tag_assignments:
        if assignment.vocabulary_version_id != card.vocabulary_version_id:
            raise PublishBlocked("tag assignment does not use pinned vocabulary")
        row = catalog.connection.execute(
            """
            SELECT dimension_id, status
            FROM tag_values
            WHERE vocabulary_version_id = ? AND tag_id = ?
            """,
            (card.vocabulary_version_id, assignment.tag_id),
        ).fetchone()
        if row is None or row[0] != assignment.dimension_id:
            raise PublishBlocked(f"unknown tag: {assignment.tag_id}")
        if row[1] == "deprecated":
            raise PublishBlocked(f"deprecated tag: {assignment.tag_id}")
        assigned_values.setdefault(assignment.dimension_id, set()).add(assignment.tag_id)
    conflicting_dimensions = tuple(
        dimension_id
        for dimension_id, tag_ids in assigned_values.items()
        if cardinality_by_dimension.get(dimension_id) == "one" and len(tag_ids) > 1
    )
    if conflicting_dimensions:
        raise PublishBlocked(
            "single-cardinality tag conflict: " + ", ".join(conflicting_dimensions)
        )


def _validate_references(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> None:
    citation_keys = tuple(canonical_model_json(item) for item in card.chunk_citations)
    if len(set(citation_keys)) != len(citation_keys):
        raise PublishBlocked("duplicate chunk citation")
    visual_keys = tuple(canonical_model_json(item) for item in card.visual_refs)
    if len(set(visual_keys)) != len(visual_keys):
        raise PublishBlocked("duplicate visual reference")
    dataset_keys = tuple(canonical_model_json(item) for item in card.dataset_refs)
    if len(set(dataset_keys)) != len(dataset_keys):
        raise PublishBlocked("duplicate dataset reference")
    citation_required_types = {
        "concept",
        "procedure",
        "example",
        "case",
        "evidence",
        "misconception",
        "warning",
    }
    if card.main_type_id in citation_required_types and not card.chunk_citations:
        raise PublishBlocked("at least one source citation is required")
    for citation in card.chunk_citations:
        chunk_row = catalog.connection.execute(
            "SELECT source_version_id, payload_json FROM chunks WHERE chunk_id = ?",
            (citation.chunk_id,),
        ).fetchone()
        if chunk_row is None or chunk_row[0] != citation.source_version_id:
            raise PublishBlocked(f"invalid chunk citation: {citation.chunk_id}")
        if citation.quoted_text:
            chunk = ExtractedChunk.model_validate_json(chunk_row[1])
            normalized_quote = " ".join(citation.quoted_text.split())
            normalized_source = " ".join(chunk.normalized_text.split())
            if normalized_quote not in normalized_source:
                raise PublishBlocked(
                    f"citation quoted text is not present in chunk: {citation.chunk_id}"
                )
    for reference in card.visual_refs:
        visual_row = catalog.connection.execute(
            "SELECT payload_json FROM visuals WHERE version_id = ?",
            (reference.visual_version_id,),
        ).fetchone()
        if visual_row is None:
            raise PublishBlocked(
                f"invalid visual reference: {reference.visual_version_id}"
            )
        visual = VisualAssetVersion.model_validate_json(visual_row[0])
        if (
            visual.license_status in {"unknown", "restricted"}
            or visual.authenticity == "unverified"
        ):
            raise PublishBlocked(
                f"unverified visual reference: {reference.visual_version_id}"
            )
    for reference in card.dataset_refs:
        dataset_row = catalog.connection.execute(
            "SELECT payload_json FROM datasets WHERE version_id = ?",
            (reference.dataset_version_id,),
        ).fetchone()
        if dataset_row is None:
            raise PublishBlocked(
                f"invalid dataset reference: {reference.dataset_version_id}"
            )
        dataset = DatasetAssetVersion.model_validate_json(dataset_row[0])
        if dataset.review_status != "ready":
            raise PublishBlocked(
                f"dataset reference is not ready: {reference.dataset_version_id}"
            )


def _validate_no_blocking_reviews(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> None:
    review_rows = catalog.connection.execute(
        """
        SELECT review_tasks.payload_json
        FROM review_tasks
        JOIN review_task_current current USING(task_id)
        WHERE review_tasks.subject_version_id = ?
          AND current.current_status = 'open'
        """,
        (card.version_id,),
    ).fetchall()
    if any(ReviewTask.model_validate_json(row[0]).blocking for row in review_rows):
        raise PublishBlocked("card has an open blocking review task")
    source_change_rows = catalog.connection.execute(
        "SELECT review_tasks.task_id, upgrade_suggestions.candidate_version_id, "
        "review_resolutions.decision FROM review_tasks "
        "LEFT JOIN upgrade_suggestions "
        "ON upgrade_suggestions.review_task_id = review_tasks.task_id "
        "LEFT JOIN review_resolutions ON review_resolutions.task_id = review_tasks.task_id "
        "WHERE review_tasks.kind = 'source-changed' "
        "AND review_tasks.subject_version_id = ?",
        (card.version_id,),
    ).fetchall()
    if any(
        candidate_version_id != card.version_id or decision != "accept"
        for _task_id, candidate_version_id, decision in source_change_rows
    ):
        raise PublishBlocked("upgrade candidate requires an accepted source-change review")


def _record_exact_duplicate_audit(
    catalog: KnowledgeCatalog,
    archived: KnowledgeCardVersion,
    evidence: EvidenceObject,
) -> None:
    """Append the automatically resolved exact-duplicate review in one publish transaction."""

    task = create_review_task(
        catalog,
        kind="exact-duplicate",
        subject_version_id=archived.version_id,
        blocking=True,
        evidence_ids=(evidence.evidence_id,),
        created_at=archived.created_at,
        created_by=_ACTOR,
    )
    task_digest = hashlib.sha256(
        canonical_model_json(task).encode("utf-8")
    ).hexdigest()
    resolution_id = "resolution-" + _json_digest(
        {
            "decision": "accept",
            "evidence_ids": [evidence.evidence_id],
            "expected_review_digest": task_digest,
            "task_id": task.task_id,
        }
    )
    resolve_review_task(
        catalog,
        ReviewResolution(
            resolution_id=resolution_id,
            task_id=task.task_id,
            decision="accept",
            expected_review_digest=task_digest,
            evidence_ids=(evidence.evidence_id,),
            resolved_at=archived.created_at,
            resolved_by=_ACTOR,
        ),
    )


def _with_status_and_digest(
    card: KnowledgeCardVersion,
    *,
    status: Literal["published", "archived"],
) -> KnowledgeCardVersion:
    values = card.model_dump(mode="python")
    values["status"] = status
    values["content_digest"] = _canonical_card_digest(card)
    return KnowledgeCardVersion.model_validate(values)


def _same_concrete_content(
    candidate: KnowledgeCardVersion,
    stored: KnowledgeCardVersion,
) -> bool:
    return (
        candidate.logical_id == stored.logical_id
        and candidate.version_id == stored.version_id
        and _canonical_card_digest(candidate) == stored.content_digest
    )


def _same_submission_content(
    candidate: KnowledgeCardVersion,
    stored: KnowledgeCardVersion,
) -> bool:
    """Compare status-independent submitted content for a lifecycle archive replay."""

    return (
        candidate.logical_id == stored.logical_id
        and candidate.version_id == stored.version_id
        and _canonical_card_digest(candidate) == _canonical_card_digest(stored)
    )


def _stored_version_outcome(
    candidate: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
) -> KnowledgeCardVersion | None:
    row = catalog.connection.execute(
        "SELECT payload_json FROM cards WHERE version_id = ?",
        (candidate.version_id,),
    ).fetchone()
    if row is None:
        return None
    raw_stored = KnowledgeCardVersion.model_validate_json(row[0])
    reopened_stored = reopen_card_version(catalog.connection, raw_stored.version_id)
    if reopened_stored.suspended:
        raise PublishBlocked("suspended card version cannot be published")
    stored = reopened_stored.card
    if not _same_concrete_content(candidate, stored):
        raise PublishBlocked("effective card version has different immutable content")
    if stored.status == "published":
        return stored
    if stored.status == "archived":
        target_rows = catalog.connection.execute(
            """
            SELECT cards.version_id
            FROM lineage
            JOIN cards ON cards.version_id = lineage.to_version_id
            JOIN card_lifecycle_current lifecycle
              ON lifecycle.card_version_id = cards.version_id
            WHERE lineage.from_version_id = ?
              AND lineage.relation = 'deduplicates'
              AND lifecycle.status = 'published'
              AND lifecycle.suspended = 0
            ORDER BY lineage.to_version_id
            """,
            (stored.version_id,),
        ).fetchall()
        if len(target_rows) == 1:
            target = reopen_card_version(
                catalog.connection,
                target_rows[0][0],
            ).card
            if target.version_id != stored.version_id:
                return target
        raise PublishBlocked("archived duplicate has invalid deduplication lineage")
    raise PublishBlocked("effective card version is not published or archived")


def _with_effective_version_meta(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
    *,
    status: Literal["published", "archived"],
) -> KnowledgeCardVersion:
    content_digest = _canonical_card_digest(card)
    occupied_row = catalog.connection.execute(
        "SELECT logical_id FROM cards WHERE version_id = ?",
        (card.version_id,),
    ).fetchone()
    effective_version_id = card.version_id
    if occupied_row is not None:
        if occupied_row[0] != card.logical_id:
            raise PublishBlocked("card version ID belongs to a different logical card")
        effective_version_id = candidate_version_id(
            card.logical_id,
            card_parent_version_ids(card),
            content_digest,
        )
    latest_row = catalog.connection.execute(
        """
        SELECT revision, version_id
        FROM cards
        WHERE logical_id = ?
        ORDER BY revision DESC
        LIMIT 1
        """,
        (card.logical_id,),
    ).fetchone()
    values = card.model_dump(mode="python")
    values["status"] = status
    values["version_id"] = effective_version_id
    values["content_digest"] = content_digest
    values["revision"] = 1 if latest_row is None else latest_row[0] + 1
    values["supersedes_version_id"] = None if latest_row is None else latest_row[1]
    return KnowledgeCardVersion.model_validate(values)


def _canonical_card_digest(card: KnowledgeCardVersion) -> str:
    return _json_digest(
        {
            "main_type_id": card.main_type_id,
            "title": card.title,
            "learning_objective": card.learning_objective,
            "content_ast": [node.model_dump(mode="json") for node in card.content_ast],
            "suggested_minutes": card.suggested_minutes,
            "prerequisite_card_version_ids": list(card.prerequisite_card_version_ids),
            "vocabulary_version_id": card.vocabulary_version_id,
            "tag_assignments": [
                assignment.model_dump(mode="json")
                for assignment in sorted(
                    card.tag_assignments,
                    key=lambda item: (item.dimension_id, item.tag_id),
                )
            ],
            "chunk_citations": [
                citation.model_dump(mode="json") for citation in card.chunk_citations
            ],
            "visual_refs": [
                reference.model_dump(mode="json") for reference in card.visual_refs
            ],
            "dataset_refs": [
                reference.model_dump(mode="json") for reference in card.dataset_refs
            ],
        }
    )


def canonical_card_content_digest(card: KnowledgeCardVersion) -> str:
    """Return the governed semantic digest used by card publication."""

    return _canonical_card_digest(card)


def _insert_card(catalog: KnowledgeCatalog, card: KnowledgeCardVersion) -> None:
    connection = catalog.connection
    connection.execute(
        """
        INSERT INTO cards(
            version_id, logical_id, revision, status, content_digest, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            card.version_id,
            card.logical_id,
            card.revision,
            card.status,
            card.content_digest,
            canonical_model_json(card),
        ),
    )
    for assignment in card.tag_assignments:
        connection.execute(
            """
            INSERT INTO card_tags(card_version_id, vocabulary_version_id, tag_id)
            VALUES (?, ?, ?)
            """,
            (card.version_id, assignment.vocabulary_version_id, assignment.tag_id),
        )
    register_card_lifecycle(
        connection,
        card,
        event_id=f"register:{card.version_id}",
        request_digest=card.content_digest,
        occurred_at=card.created_at,
        actor_id=card.created_by.actor_id,
    )


def _supersede_published_revisions(
    catalog: KnowledgeCatalog,
    published: KnowledgeCardVersion,
) -> None:
    rows = catalog.connection.execute(
        """
        SELECT cards.version_id
        FROM cards
        JOIN card_lifecycle_current lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE cards.logical_id = ?
          AND lifecycle.status = 'published'
          AND cards.version_id <> ?
        ORDER BY cards.revision, cards.version_id
        """,
        (published.logical_id, published.version_id),
    ).fetchall()
    for row in rows:
        transition_card_status(catalog.connection, row[0], "superseded")


def _publication_evidence(card: KnowledgeCardVersion) -> EvidenceObject:
    evidence_id = "publish-" + _json_digest(
        {"version_id": card.version_id, "content_digest": card.content_digest}
    )
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="publish",
        subject_version_id=card.version_id,
        status="verified",
        input_summary={
            "review_status": "review",
            "vocabulary_version_id": card.vocabulary_version_id,
        },
        output_summary={
            "published_status": card.status,
            "citation_count": len(card.chunk_citations),
            "visual_reference_count": len(card.visual_refs),
            "dataset_reference_count": len(card.dataset_refs),
        },
        producer="course-helper/cards",
        producer_version="1",
        started_at=card.created_at,
        finished_at=card.created_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="publish-gates",
                status="passed",
                message="Card passed vocabulary, reference, and review gates",
            ),
        ),
    )


def _dedup_evidence(
    archived: KnowledgeCardVersion,
    existing: KnowledgeCardVersion,
) -> EvidenceObject:
    evidence_id = "dedup-" + _json_digest(
        {
            "archived_version_id": archived.version_id,
            "existing_version_id": existing.version_id,
            "content_digest": archived.content_digest,
        }
    )
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="dedup",
        subject_version_id=archived.version_id,
        status="verified",
        input_summary={
            "candidate_version_id": archived.version_id,
            "canonical_content_digest": archived.content_digest,
        },
        output_summary={
            "existing_published_version_id": existing.version_id,
            "candidate_status": archived.status,
        },
        producer="course-helper/cards",
        producer_version="1",
        started_at=archived.created_at,
        finished_at=archived.created_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="exact-card-dedup",
                status="passed",
                message="Canonical card content matched an existing published version",
                details={"content_digest": archived.content_digest},
            ),
        ),
    )
def _insert_evidence(catalog: KnowledgeCatalog, evidence: EvidenceObject) -> None:
    catalog.connection.execute(
        """
        INSERT INTO evidence(evidence_id, kind, status, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            evidence.evidence_id,
            evidence.kind,
            evidence.status,
            canonical_model_json(evidence),
        ),
    )


def _insert_reference_lineage(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
    evidence: EvidenceObject,
) -> None:
    relationships = tuple(
        dict.fromkeys(
            (
                *((citation.chunk_id, "cites") for citation in card.chunk_citations),
                *((reference.visual_version_id, "uses") for reference in card.visual_refs),
                *((reference.dataset_version_id, "uses") for reference in card.dataset_refs),
            )
        )
    )
    for to_version_id, relation in relationships:
        edge = LineageEdge(
            edge_id="lineage-"
            + _json_digest(
                {
                    "from_version_id": card.version_id,
                    "to_version_id": to_version_id,
                    "relation": relation,
                    "evidence_id": evidence.evidence_id,
                }
            ),
            from_version_id=card.version_id,
            to_version_id=to_version_id,
            relation=relation,
            evidence_id=evidence.evidence_id,
            created_at=card.created_at,
        )
        catalog.connection.execute(
            """
            INSERT INTO lineage(
                edge_id, from_version_id, to_version_id, relation, evidence_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id,
                edge.from_version_id,
                edge.to_version_id,
                edge.relation,
                edge.evidence_id,
            ),
        )


def _insert_dedup_lineage(
    catalog: KnowledgeCatalog,
    archived: KnowledgeCardVersion,
    existing: KnowledgeCardVersion,
    evidence: EvidenceObject,
) -> None:
    edge = LineageEdge(
        edge_id="lineage-"
        + _json_digest(
            {
                "from_version_id": archived.version_id,
                "to_version_id": existing.version_id,
                "relation": "deduplicates",
                "evidence_id": evidence.evidence_id,
            }
        ),
        from_version_id=archived.version_id,
        to_version_id=existing.version_id,
        relation="deduplicates",
        evidence_id=evidence.evidence_id,
        created_at=archived.created_at,
    )
    catalog.connection.execute(
        """
        INSERT INTO lineage(
            edge_id, from_version_id, to_version_id, relation, evidence_id
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            edge.edge_id,
            edge.from_version_id,
            edge.to_version_id,
            edge.relation,
            edge.evidence_id,
        ),
    )


def _pptx_groups(chunks: Sequence[ExtractedChunk]) -> tuple[tuple[ExtractedChunk, ...], ...]:
    groups: list[tuple[ExtractedChunk, ...]] = []
    pending: list[ExtractedChunk] = []
    for chunk in chunks:
        slide_number = chunk.locator.slide_number
        adjacent = bool(
            pending
            and slide_number is not None
            and pending[-1].locator.slide_number is not None
            and slide_number == pending[-1].locator.slide_number + 1
        )
        current_title = _normalized_title(chunk.heading)
        previous_title = _normalized_title(pending[-1].heading) if pending else ""
        same_title = bool(
            pending
            and current_title
            and previous_title
            and current_title == previous_title
        )
        if pending and (not adjacent or not same_title or len(pending) == 3):
            groups.append(tuple(pending))
            pending = []
        pending.append(chunk)
    if pending:
        groups.append(tuple(pending))
    return tuple(groups)


def _markdown_groups(
    chunks: Sequence[ExtractedChunk],
) -> tuple[tuple[ExtractedChunk, ...], ...]:
    groups: list[tuple[ExtractedChunk, ...]] = []
    pending: list[ExtractedChunk] = []
    for chunk in chunks:
        if pending and not _is_descendant(
            chunk.locator.ast_path,
            pending[0].locator.ast_path,
        ):
            groups.append(tuple(pending))
            pending = []
        pending.append(chunk)
    if pending:
        groups.append(tuple(pending))
    return tuple(groups)


def _is_descendant(path: tuple[int, ...], parent: tuple[int, ...]) -> bool:
    return len(path) > len(parent) and path[: len(parent)] == parent


def _normalized_title(title: str | None) -> str:
    return " ".join((title or "").split()).casefold()


def _candidate_from_chunks(
    extraction: ExtractionResult,
    chunks: tuple[ExtractedChunk, ...],
) -> KnowledgeCardVersion:
    title = next((chunk.heading for chunk in chunks if chunk.heading), "Source-backed unit")
    learning_objective = _candidate_learning_objective(chunks, title)
    citations = tuple(
        ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
            quoted_text=chunk.normalized_text,
        )
        for chunk in chunks
    )
    content_ast = tuple(
        CardContentNode(type="paragraph", text=chunk.normalized_text)
        for chunk in chunks
    )
    media_ids = tuple(
        dict.fromkeys(
            media_id
            for chunk in chunks
            for media_id in chunk.media_version_ids
        )
    )
    visual_refs = tuple(
        VisualReference(visual_version_id=media_id, purpose="illustration")
        for media_id in media_ids
    )
    semantic_locator = _semantic_locator(extraction, chunks, title)
    logical_id = candidate_logical_id("card", semantic_locator)
    card_content = {
        "main_type_id": "concept",
        "title": title,
        "learning_objective": learning_objective,
        "content_ast": [node.model_dump(mode="json") for node in content_ast],
        "suggested_minutes": max(1, len(chunks) * 2),
        "prerequisite_card_version_ids": [],
        "vocabulary_version_id": VOCABULARY_VERSION_ID,
        "tag_assignments": [],
        "chunk_citations": [citation.model_dump(mode="json") for citation in citations],
        "visual_refs": [reference.model_dump(mode="json") for reference in visual_refs],
        "dataset_refs": [],
    }
    content_digest = _json_digest(card_content)
    parent_ids = (extraction.source.version_id, *(chunk.chunk_id for chunk in chunks))
    version_id = candidate_version_id(logical_id, parent_ids, content_digest)
    return KnowledgeCardVersion(
        logical_id=logical_id,
        version_id=version_id,
        revision=1,
        content_digest=content_digest,
        created_at=extraction.source.created_at or _FIXED_CREATED_AT,
        created_by=_ACTOR,
        main_type_id="concept",
        title=title,
        learning_objective=learning_objective,
        content_ast=content_ast,
        suggested_minutes=max(1, len(chunks) * 2),
        vocabulary_version_id=VOCABULARY_VERSION_ID,
        chunk_citations=citations,
        visual_refs=visual_refs,
        status="draft",
    )


def _candidate_learning_objective(
    chunks: tuple[ExtractedChunk, ...],
    title: str,
) -> str:
    objective_headings = {
        "学习目标",
        "课程目标",
        "learning goal",
        "learning goals",
        "learning objective",
        "learning objectives",
        "objectives",
    }
    for chunk in chunks:
        heading = " ".join((chunk.heading or "").split())
        if heading.casefold() not in objective_headings:
            continue
        value = chunk.normalized_text.strip()
        if value.casefold().startswith(heading.casefold()):
            value = value[len(heading) :].strip()
        value = " ".join(value.split()).rstrip("。.!！?？;；").strip()
        if value:
            return value[:1000]
    return " ".join(title.split()).rstrip("。.!！?？;；")[:1000]


def _semantic_locator(
    extraction: ExtractionResult,
    chunks: tuple[ExtractedChunk, ...],
    title: str,
) -> str:
    if extraction.source.source_kind == "pptx":
        numbers = tuple(chunk.locator.slide_number for chunk in chunks)
        position = f"slides/{numbers[0]}-{numbers[-1]}"
    else:
        position = "/".join(str(part) for part in chunks[0].locator.ast_path)
    return f"{extraction.source.logical_id}/{position}/{title}"


def _json_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
