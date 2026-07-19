"""Deterministic, evidence-backed upgrade suggestions for immutable knowledge."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from course_helper.cards import create_review_task, find_exact_duplicate
from course_helper.catalog import (
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.knowledge import ChunkCitation, KnowledgeCardVersion, ReviewTask
from course_helper.domain.sources import ExtractedChunk
from course_helper.domain.sources import DatasetAssetVersion, SourceAssetVersion, VisualAssetVersion
from course_helper.reviews import (
    CourseFeedbackSuggestion,
    ReviewResolution,
    UpgradeSuggestion,
    register_feedback_suggestion,
    register_upgrade_suggestion,
    resolve_review_task,
)
from course_helper.near_duplicates import scan_near_duplicates
from course_helper.source_roots import candidate_version_id


class ChunkChangeKind(str, Enum):
    """A content comparison outcome for one stable chunk locator."""

    changed = "changed"
    unchanged = "unchanged"
    removed = "removed"
    added = "added"


@dataclass(frozen=True)
class FieldDigestDiff:
    """A field-level delta which contains hashes, never copied source content."""

    field_name: str
    before_digest: str
    after_digest: str | None


@dataclass(frozen=True)
class ChunkChange:
    kind: ChunkChangeKind
    previous_chunk_id: str | None
    current_chunk_id: str | None
    field_diffs: tuple[FieldDigestDiff, ...]


@dataclass(frozen=True)
class SourceChangeUpgradeResult:
    """One immutable source upgrade and every card suggestion it safely produced."""

    evidence_id: str
    source_suggestion: UpgradeSuggestion
    card_suggestions: tuple[UpgradeSuggestion, ...]
    unresolved_card_version_ids: tuple[str, ...]
    affected_course_version_ids: tuple[str, ...]
    affected_snapshot: AffectedPlacementSnapshot
    chunk_changes: tuple[ChunkChange, ...]


@dataclass(frozen=True)
class AffectedPlacementSnapshot:
    """Digest-bound card/course payload snapshot taken before proposal writes."""

    card_version_ids: tuple[str, ...]
    course_version_ids: tuple[str, ...]
    content_digest: str


@dataclass(frozen=True)
class UpgradeAcceptanceOutcome:
    """Acceptance receipt which names the remaining common publication gates."""

    suggestion_id: str
    candidate_version_id: str | None
    decision: Literal["accept", "reject", "dismiss"]
    resolution_id: str
    next_required_review_task_ids: tuple[str, ...]
    next_action: Literal[
        "knowledge_card_publish",
        "review_affected_knowledge",
        "compose_candidate_from_feedback",
        "no_action",
    ]


@dataclass(frozen=True)
class AssetChangeUpgradeResult:
    """One governed dataset/schema/visual/source version-change suggestion."""

    evidence_id: str
    suggestion: UpgradeSuggestion
    field_diffs: tuple[FieldDigestDiff, ...]
    affected_card_version_ids: tuple[str, ...]
    affected_course_version_ids: tuple[str, ...]
    affected_snapshot: AffectedPlacementSnapshot


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _field_digest(value: object) -> str:
    """Digest a scalar as itself and structured values as canonical JSON."""

    if isinstance(value, str):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return _digest(value)


def _task_digest(task: ReviewTask) -> str:
    return hashlib.sha256(canonical_model_json(task).encode("utf-8")).hexdigest()


def _chunk_identity(chunk: ExtractedChunk) -> str:
    """Match revisions by typed location rather than a mutable chunk identifier."""

    locator = chunk.locator.model_dump(mode="json", exclude_none=True)
    return json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_diffs(before: ExtractedChunk, after: ExtractedChunk) -> tuple[FieldDigestDiff, ...]:
    before_fields = before.model_dump(mode="json", exclude={"chunk_id", "source_version_id", "content_digest"})
    after_fields = after.model_dump(mode="json", exclude={"chunk_id", "source_version_id", "content_digest"})
    preferred = (
        "normalized_text",
        "notes_text",
        "slide_text",
        "code_blocks",
        "table_rows",
        "media_version_ids",
        "warnings",
        "heading",
        "breadcrumb",
        "ordinal",
        "modality",
        "language",
        "locator",
    )
    names = tuple(dict.fromkeys((*preferred, *sorted(set(before_fields) | set(after_fields)))))
    return tuple(
        FieldDigestDiff(
            name,
            _field_digest(before_fields.get(name)),
            _field_digest(after_fields.get(name)),
        )
        for name in names
        if before_fields.get(name) != after_fields.get(name)
    )


def detect_chunk_changes(
    previous_chunks: tuple[ExtractedChunk, ...],
    current_chunks: tuple[ExtractedChunk, ...],
) -> tuple[ChunkChange, ...]:
    """Return stable changed/unchanged/removed chunk facts without source bodies."""

    current_by_identity = {_chunk_identity(chunk): chunk for chunk in current_chunks}
    previous_identities = {_chunk_identity(chunk) for chunk in previous_chunks}
    changes: list[ChunkChange] = []
    for previous in previous_chunks:
        current = current_by_identity.get(_chunk_identity(previous))
        if current is None:
            changes.append(
                ChunkChange(ChunkChangeKind.removed, previous.chunk_id, None, _removed_field_diffs(previous))
            )
        elif not _field_diffs(previous, current):
            changes.append(
                ChunkChange(ChunkChangeKind.unchanged, previous.chunk_id, current.chunk_id, ())
            )
        else:
            changes.append(
                ChunkChange(
                    ChunkChangeKind.changed,
                    previous.chunk_id,
                    current.chunk_id,
                    _field_diffs(previous, current),
                )
            )
    for current in current_chunks:
        if _chunk_identity(current) not in previous_identities:
            changes.append(
                ChunkChange(ChunkChangeKind.added, None, current.chunk_id, _added_field_diffs(current))
            )
    return tuple(changes)


def _removed_field_diffs(chunk: ExtractedChunk) -> tuple[FieldDigestDiff, ...]:
    fields = chunk.model_dump(mode="json", exclude={"chunk_id", "source_version_id", "content_digest"})
    return tuple(FieldDigestDiff(name, _field_digest(value), None) for name, value in sorted(fields.items()))


def _added_field_diffs(chunk: ExtractedChunk) -> tuple[FieldDigestDiff, ...]:
    fields = chunk.model_dump(mode="json", exclude={"chunk_id", "source_version_id", "content_digest"})
    return tuple(FieldDigestDiff(name, _field_digest(None), _field_digest(value)) for name, value in sorted(fields.items()))


def _card_content_digest(card: KnowledgeCardVersion) -> str:
    """Hash content, excluding version/lifecycle metadata exactly as publication does."""

    return _digest(
        {
            "main_type_id": card.main_type_id,
            "title": card.title,
            "learning_objective": card.learning_objective,
            "content_ast": [node.model_dump(mode="json") for node in card.content_ast],
            "suggested_minutes": card.suggested_minutes,
            "prerequisite_card_version_ids": list(card.prerequisite_card_version_ids),
            "vocabulary_version_id": card.vocabulary_version_id,
            "tag_assignments": [
                item.model_dump(mode="json")
                for item in sorted(card.tag_assignments, key=lambda item: (item.dimension_id, item.tag_id))
            ],
            "chunk_citations": [item.model_dump(mode="json") for item in card.chunk_citations],
            "visual_refs": [item.model_dump(mode="json") for item in card.visual_refs],
            "dataset_refs": [item.model_dump(mode="json") for item in card.dataset_refs],
        }
    )


def _affected_cards(catalog: KnowledgeCatalog, source_version_id: str) -> tuple[KnowledgeCardVersion, ...]:
    cards: list[KnowledgeCardVersion] = []
    for (payload,) in catalog.connection.execute(
        "SELECT payload_json FROM cards ORDER BY version_id"
    ).fetchall():
        card = KnowledgeCardVersion.model_validate_json(payload)
        if any(citation.source_version_id == source_version_id for citation in card.chunk_citations):
            cards.append(catalog.get_card(card.version_id) or card)
    return tuple(cards)


def _affected_cards_for_asset(
    catalog: KnowledgeCatalog,
    *,
    version_id: str,
    asset_kind: Literal["source", "dataset", "visual"],
) -> tuple[KnowledgeCardVersion, ...]:
    cards: list[KnowledgeCardVersion] = []
    for (payload,) in catalog.connection.execute(
        "SELECT payload_json FROM cards ORDER BY version_id"
    ).fetchall():
        card = KnowledgeCardVersion.model_validate_json(payload)
        referenced = (
            any(item.source_version_id == version_id for item in card.chunk_citations)
            if asset_kind == "source"
            else any(item.dataset_version_id == version_id for item in card.dataset_refs)
            if asset_kind == "dataset"
            else any(item.visual_version_id == version_id for item in card.visual_refs)
        )
        if referenced:
            cards.append(catalog.get_card(card.version_id) or card)
    return tuple(cards)


def _affected_courses(catalog: KnowledgeCatalog, card_version_ids: tuple[str, ...]) -> tuple[str, ...]:
    if not card_version_ids:
        return ()
    placeholders = ",".join("?" for _ in card_version_ids)
    rows = catalog.connection.execute(
        "SELECT DISTINCT course_versions.version_id FROM course_versions "
        "JOIN card_placements ON card_placements.outline_version_id = course_versions.outline_version_id "
        f"WHERE card_placements.card_version_id IN ({placeholders}) ORDER BY course_versions.version_id",
        card_version_ids,
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _affected_snapshot(
    catalog: KnowledgeCatalog,
    *,
    version_id: str,
    asset_kind: Literal["source", "dataset", "visual"],
) -> tuple[tuple[KnowledgeCardVersion, ...], AffectedPlacementSnapshot]:
    """Read cards, courses, and their canonical bytes from one SQLite snapshot."""

    if catalog.connection.in_transaction:
        raise CatalogReferenceError("affected snapshot requires an idle catalog")
    catalog.connection.execute("BEGIN")
    try:
        cards = (
            _affected_cards(catalog, version_id)
            if asset_kind == "source"
            else _affected_cards_for_asset(catalog, version_id=version_id, asset_kind=asset_kind)
        )
        card_ids = tuple(card.version_id for card in cards)
        course_ids = _affected_courses(catalog, card_ids)
        card_payloads = catalog.connection.execute(
            "SELECT version_id, payload_json FROM cards WHERE version_id IN ("
            + ",".join("?" for _ in card_ids)
            + ") ORDER BY version_id",
            card_ids,
        ).fetchall() if card_ids else []
        course_payloads = catalog.connection.execute(
            "SELECT version_id, payload_json FROM course_versions WHERE version_id IN ("
            + ",".join("?" for _ in course_ids)
            + ") ORDER BY version_id",
            course_ids,
        ).fetchall() if course_ids else []
        snapshot = AffectedPlacementSnapshot(
            card_version_ids=card_ids,
            course_version_ids=course_ids,
            content_digest=_digest(
                {"cards": list(card_payloads), "courses": list(course_payloads)}
            ),
        )
        catalog.connection.execute("COMMIT")
        return cards, snapshot
    except Exception:
        catalog.connection.execute("ROLLBACK")
        raise


def _upgrade_id(prefix: str, values: object) -> str:
    return f"{prefix}-{_digest(values)}"


def _reuse_evidence(catalog: KnowledgeCatalog, evidence: EvidenceObject) -> EvidenceObject:
    """Return first immutable evidence bytes when a detector is replayed."""

    row = catalog.connection.execute(
        "SELECT payload_json FROM evidence WHERE evidence_id = ?", (evidence.evidence_id,)
    ).fetchone()
    if row is not None:
        stored = EvidenceObject.model_validate_json(row[0], strict=False)
        if _evidence_semantic_json(stored) != _evidence_semantic_json(evidence):
            raise ImmutableVersionConflict("evidence ID already has different semantic bytes")
        return stored
    return catalog.insert_evidence(evidence)


def _evidence_semantic_json(evidence: EvidenceObject) -> str:
    """Compare receipt meaning while allowing detector retries to keep first time."""

    payload = evidence.model_dump(mode="json")
    payload.pop("started_at", None)
    payload.pop("finished_at", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reuse_candidate(
    catalog: KnowledgeCatalog, candidate: KnowledgeCardVersion
) -> KnowledgeCardVersion:
    """Preserve the first candidate's actor/timestamp across detector retries."""

    row = catalog.connection.execute(
        "SELECT payload_json FROM cards WHERE version_id = ?", (candidate.version_id,)
    ).fetchone()
    if row is None:
        return catalog.insert_card(candidate)
    stored = KnowledgeCardVersion.model_validate_json(row[0], strict=False)
    if (
        stored.logical_id != candidate.logical_id
        or stored.content_digest != candidate.content_digest
        or stored.supersedes_version_id != candidate.supersedes_version_id
        or stored.revision != candidate.revision
        or _card_content_digest(stored) != _card_content_digest(candidate)
    ):
        raise CatalogReferenceError("candidate version ID conflicts with immutable bytes")
    return stored


def _existing_upgrade_suggestion(
    catalog: KnowledgeCatalog, suggestion_id: str
) -> UpgradeSuggestion | None:
    row = catalog.connection.execute(
        "SELECT payload_json FROM upgrade_suggestions WHERE suggestion_id = ?", (suggestion_id,)
    ).fetchone()
    return None if row is None else UpgradeSuggestion.model_validate_json(row[0], strict=False)


def _existing_feedback_suggestion(
    catalog: KnowledgeCatalog, suggestion_id: str
) -> CourseFeedbackSuggestion | None:
    row = catalog.connection.execute(
        "SELECT payload_json FROM feedback_suggestions WHERE suggestion_id = ?", (suggestion_id,)
    ).fetchone()
    return None if row is None else CourseFeedbackSuggestion.model_validate_json(row[0], strict=False)


def _stored_review_task(catalog: KnowledgeCatalog, task_id: str) -> ReviewTask:
    """Load a review task only when its indexed envelope matches immutable bytes."""

    row = catalog.connection.execute(
        "SELECT kind, status, payload_json FROM review_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("review task is not persisted")
    try:
        task = ReviewTask.model_validate_json(row[2], strict=False)
    except Exception as error:
        raise CatalogReferenceError("stored review task cannot be validated") from error
    if (
        task.task_id != task_id
        or task.kind != str(row[0])
        or task.status != str(row[1])
        or canonical_model_json(task) != str(row[2])
    ):
        raise CatalogReferenceError("stored review task envelope does not match immutable bytes")
    return task


def _stored_evidence(catalog: KnowledgeCatalog, evidence_id: str) -> EvidenceObject:
    """Load canonical immutable evidence, refusing partial or malformed receipts."""

    row = catalog.connection.execute(
        "SELECT payload_json FROM evidence WHERE evidence_id = ?", (evidence_id,)
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("review gate evidence is not persisted")
    try:
        evidence = EvidenceObject.model_validate_json(row[0], strict=False)
    except Exception as error:
        raise CatalogReferenceError("review gate evidence cannot be validated") from error
    if canonical_model_json(evidence) != str(row[0]):
        raise CatalogReferenceError("review gate evidence is not canonical immutable bytes")
    return evidence


def _require_existing_suggestion_task_envelope(
    catalog: KnowledgeCatalog,
    suggestion: UpgradeSuggestion,
    *,
    evidence_id: str,
) -> None:
    """Verify the task side of an existing suggestion before detector replay."""

    task = _stored_review_task(catalog, suggestion.review_task_id)
    if (
        task.kind != "source-changed"
        or task.subject_version_id != suggestion.candidate_version_id
        or not task.blocking
        or task.evidence_ids != (evidence_id,)
    ):
        raise CatalogReferenceError("upgrade review task envelope does not match source-change evidence")


def _require_existing_card_replay_gates(
    catalog: KnowledgeCatalog,
    *,
    candidate_version_id: str,
    source_evidence_id: str,
) -> None:
    """Fail closed if a replayed candidate lacks its first-run governance proof.

    The Task 5 scan is intentionally not rerun: its index is live and a replay
    must validate the original immutable near-dedup receipt rather than create
    a new one against a changed index.
    """

    rows = catalog.connection.execute(
        "SELECT task_id FROM review_tasks WHERE subject_version_id = ? "
        "AND kind IN ('near-duplicate', 'provenance')",
        (candidate_version_id,),
    ).fetchall()
    tasks = tuple(_stored_review_task(catalog, str(row[0])) for row in rows)
    near_gate_present = False
    provenance_gate_present = False
    required_candidate_checks = {
        "exact-dedup-clean-scan",
        "tag-clean-scan",
        "provenance-clean-scan",
    }
    for task in tasks:
        if not task.blocking:
            continue
        if task.kind == "near-duplicate":
            for evidence_id in task.evidence_ids:
                evidence = _stored_evidence(catalog, evidence_id)
                if (
                    evidence.kind == "dedup"
                    and evidence.subject_version_id == candidate_version_id
                    and evidence.producer == "course-helper/near-duplicates"
                ):
                    near_gate_present = True
        elif task.kind == "provenance" and len(task.evidence_ids) == 1:
            evidence = _stored_evidence(catalog, task.evidence_ids[0])
            if (
                evidence.evidence_id.startswith("candidate-gate-evidence-")
                and evidence.kind == "dedup"
                and evidence.subject_version_id == candidate_version_id
                and evidence.producer == "course-helper/upgrades"
                and evidence.producer_version == "1"
                and evidence.input_summary == {"source_evidence_id": source_evidence_id}
                and {check.code for check in evidence.checks} == required_candidate_checks
            ):
                provenance_gate_present = True
    if not near_gate_present or not provenance_gate_present:
        raise CatalogReferenceError(
            "replayed card candidate is missing a complete immutable near-dedup or provenance gate"
        )


def _stored_asset(
    catalog: KnowledgeCatalog, version_id: str
) -> tuple[Literal["source", "dataset", "visual"], SourceAssetVersion | DatasetAssetVersion | VisualAssetVersion]:
    tables: tuple[tuple[str, Literal["source", "dataset", "visual"], type[SourceAssetVersion] | type[DatasetAssetVersion] | type[VisualAssetVersion]], ...] = (
        ("sources", "source", SourceAssetVersion),
        ("datasets", "dataset", DatasetAssetVersion),
        ("visuals", "visual", VisualAssetVersion),
    )
    for table, kind, model_type in tables:
        row = catalog.connection.execute(
            f"SELECT payload_json FROM {table} WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is not None:
            return kind, model_type.model_validate_json(row[0], strict=False)
    raise CatalogReferenceError("asset version is not persisted")


def _validate_persisted_chunk(
    catalog: KnowledgeCatalog,
    chunk: ExtractedChunk,
    *,
    source_version_id: str,
) -> None:
    if chunk.source_version_id != source_version_id:
        raise CatalogReferenceError("source-change chunk is bound to the wrong source version")
    row = catalog.connection.execute(
        "SELECT source_version_id, payload_json FROM chunks WHERE chunk_id = ?", (chunk.chunk_id,)
    ).fetchone()
    if row is None or str(row[0]) != source_version_id:
        raise CatalogReferenceError("source-change chunk is not persisted for its source")
    stored = ExtractedChunk.model_validate_json(row[1], strict=False)
    if canonical_model_json(stored) != canonical_model_json(chunk):
        raise CatalogReferenceError("source-change chunk bytes do not match persisted immutable bytes")


def _source_descends_from(
    catalog: KnowledgeCatalog,
    *,
    current: SourceAssetVersion,
    previous_version_id: str,
) -> bool:
    cursor: SourceAssetVersion | None = current
    seen: set[str] = set()
    while cursor is not None and cursor.version_id not in seen:
        seen.add(cursor.version_id)
        if cursor.supersedes_version_id == previous_version_id:
            return True
        if cursor.supersedes_version_id is None:
            return False
        kind, predecessor = _stored_asset(catalog, cursor.supersedes_version_id)
        if kind != "source" or not isinstance(predecessor, SourceAssetVersion):
            return False
        cursor = predecessor
    return False


def _asset_descends_from(
    catalog: KnowledgeCatalog,
    *,
    current: SourceAssetVersion | DatasetAssetVersion | VisualAssetVersion,
    previous_version_id: str,
    expected_kind: Literal["source", "dataset", "visual"],
) -> bool:
    cursor: SourceAssetVersion | DatasetAssetVersion | VisualAssetVersion | None = current
    seen: set[str] = set()
    while cursor is not None and cursor.version_id not in seen:
        seen.add(cursor.version_id)
        if cursor.supersedes_version_id == previous_version_id:
            return True
        if cursor.supersedes_version_id is None:
            return False
        kind, predecessor = _stored_asset(catalog, cursor.supersedes_version_id)
        if kind != expected_kind:
            return False
        cursor = predecessor
    return False


def _validate_source_change_inputs(
    catalog: KnowledgeCatalog,
    *,
    previous_source_version_id: str,
    current_source_version_id: str,
    previous_chunks: tuple[ExtractedChunk, ...],
    current_chunks: tuple[ExtractedChunk, ...],
) -> None:
    previous_kind, previous = _stored_asset(catalog, previous_source_version_id)
    current_kind, current = _stored_asset(catalog, current_source_version_id)
    if (
        previous_kind != "source"
        or current_kind != "source"
        or not isinstance(previous, SourceAssetVersion)
        or not isinstance(current, SourceAssetVersion)
        or previous.logical_id != current.logical_id
        or current.revision <= previous.revision
        or not _source_descends_from(
            catalog, current=current, previous_version_id=previous.version_id
        )
    ):
        raise CatalogReferenceError("source-change versions are not a newer source lineage")
    for chunk in previous_chunks:
        _validate_persisted_chunk(
            catalog, chunk, source_version_id=previous_source_version_id
        )
    for chunk in current_chunks:
        _validate_persisted_chunk(
            catalog, chunk, source_version_id=current_source_version_id
        )


def _asset_field_diffs(
    previous: SourceAssetVersion | DatasetAssetVersion | VisualAssetVersion,
    current: SourceAssetVersion | DatasetAssetVersion | VisualAssetVersion,
) -> tuple[FieldDigestDiff, ...]:
    ignored = {"schema_version", "logical_id", "version_id", "revision", "supersedes_version_id", "created_at", "created_by"}
    before = previous.model_dump(mode="json", exclude=ignored)
    after = current.model_dump(mode="json", exclude=ignored)
    return tuple(
        FieldDigestDiff(field, _field_digest(before.get(field)), _field_digest(after.get(field)))
        for field in sorted(set(before) | set(after))
        if before.get(field) != after.get(field)
    )


def propose_asset_change_upgrade(
    catalog: KnowledgeCatalog,
    *,
    previous_version_id: str,
    current_version_id: str,
    change_kind: Literal["source", "dataset", "schema", "visual"],
    actor: ActorRef,
    occurred_at: datetime,
) -> AssetChangeUpgradeResult:
    """Propose a direct immutable asset upgrade and snapshot its affected placements."""

    previous_kind, previous = _stored_asset(catalog, previous_version_id)
    current_kind, current = _stored_asset(catalog, current_version_id)
    if previous_kind != current_kind or previous.logical_id != current.logical_id:
        raise CatalogReferenceError("asset upgrade versions must share kind and logical ID")
    if (
        current.revision <= previous.revision
        or not _asset_descends_from(
            catalog,
            current=current,
            previous_version_id=previous.version_id,
            expected_kind=current_kind,
        )
    ):
        raise CatalogReferenceError("asset upgrade candidate is not a newer immutable descendant")
    if change_kind == "schema" and current_kind != "dataset":
        raise ValueError("schema changes require dataset versions")
    if change_kind != "schema" and change_kind != current_kind:
        raise ValueError("change kind does not match its immutable asset")
    diffs = _asset_field_diffs(previous, current)
    affected_cards, snapshot = _affected_snapshot(
        catalog, version_id=previous_version_id, asset_kind=previous_kind
    )
    affected_card_ids = snapshot.card_version_ids
    affected_courses = snapshot.course_version_ids
    evidence = EvidenceObject(
        evidence_id=_upgrade_id(
            "asset-upgrade-evidence",
            {
                "previous_version_id": previous_version_id,
                "current_version_id": current_version_id,
                "change_kind": change_kind,
                "field_diffs": [item.__dict__ for item in diffs],
                "affected_card_version_ids": affected_card_ids,
                "affected_course_version_ids": affected_courses,
                "affected_snapshot_digest": snapshot.content_digest,
            },
        ),
        kind="validation",
        subject_version_id=current_version_id,
        status="verified",
        input_summary={"change_kind": change_kind, "previous_version_id": previous_version_id},
        output_summary={
            "field_diffs": [item.__dict__ for item in diffs],
            "affected_card_version_ids": list(affected_card_ids),
            "affected_course_version_ids": list(affected_courses),
            "affected_snapshot_digest": snapshot.content_digest,
        },
        producer="course-helper/upgrades",
        producer_version="1",
        started_at=occurred_at,
        finished_at=occurred_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="field-level-asset-diff",
                status="passed",
                message="Compared immutable asset fields by digest.",
            ),
        ),
    )
    with catalog.atomic_write():
        _reuse_evidence(catalog, evidence)
        suggestion = _make_upgrade_suggestion(
            catalog,
            current_version_id=previous_version_id,
            candidate_version_id=current_version_id,
            evidence_id=evidence.evidence_id,
            actor=actor,
            occurred_at=occurred_at,
        )
    return AssetChangeUpgradeResult(
        evidence_id=evidence.evidence_id,
        suggestion=suggestion,
        field_diffs=diffs,
        affected_card_version_ids=affected_card_ids,
        affected_course_version_ids=affected_courses,
        affected_snapshot=snapshot,
    )


def _make_upgrade_suggestion(
    catalog: KnowledgeCatalog,
    *,
    current_version_id: str,
    candidate_version_id: str,
    evidence_id: str,
    actor: ActorRef,
    occurred_at: datetime,
) -> UpgradeSuggestion:
    suggestion_id = _upgrade_id(
        "upgrade",
        {
            "current_version_id": current_version_id,
            "candidate_version_id": candidate_version_id,
            "evidence_id": evidence_id,
        },
    )
    existing = _existing_upgrade_suggestion(catalog, suggestion_id)
    if existing is not None:
        register_upgrade_suggestion(catalog, existing)
        if (
            existing.current_version_id != current_version_id
            or existing.candidate_version_id != candidate_version_id
            or existing.reason_code != "source-changed"
            or existing.evidence_ids != (evidence_id,)
        ):
            raise ImmutableVersionConflict("upgrade suggestion ID has different semantic binding")
        _require_existing_suggestion_task_envelope(
            catalog, existing, evidence_id=evidence_id
        )
        return existing
    task = create_review_task(
        catalog,
        kind="source-changed",
        subject_version_id=candidate_version_id,
        evidence_ids=(evidence_id,),
        created_at=occurred_at,
        created_by=actor,
    )
    suggestion = UpgradeSuggestion(
        suggestion_id=suggestion_id,
        current_version_id=current_version_id,
        candidate_version_id=candidate_version_id,
        review_task_id=task.task_id,
        reason_code="source-changed",
        evidence_ids=(evidence_id,),
        created_at=occurred_at,
        created_by=actor,
    )
    return register_upgrade_suggestion(catalog, suggestion)


def _candidate_card(
    card: KnowledgeCardVersion,
    replacements: dict[str, ExtractedChunk],
    *,
    actor: ActorRef,
    occurred_at: datetime,
) -> KnowledgeCardVersion | None:
    citations: list[ChunkCitation] = []
    changed = False
    for citation in card.chunk_citations:
        replacement = replacements.get(citation.chunk_id)
        if replacement is None:
            citations.append(citation)
            continue
        changed = True
        citations.append(
            ChunkCitation(
                chunk_id=replacement.chunk_id,
                source_version_id=replacement.source_version_id,
                quoted_text=replacement.normalized_text if citation.quoted_text else None,
            )
        )
    if not changed:
        return None
    draft = card.model_copy(
        update={
            "chunk_citations": tuple(citations),
            "status": "review",
            "created_at": occurred_at,
            "created_by": actor,
            "supersedes_version_id": card.version_id,
            "revision": card.revision + 1,
        }
    )
    content_digest = _card_content_digest(draft)
    return draft.model_copy(
        update={
            "content_digest": content_digest,
            "version_id": candidate_version_id(
                card.logical_id,
                (card.version_id, *(citation.chunk_id for citation in citations)),
                content_digest,
            ),
        }
    )


def propose_source_change_upgrades(
    catalog: KnowledgeCatalog,
    *,
    previous_source_version_id: str,
    current_source_version_id: str,
    previous_chunks: tuple[ExtractedChunk, ...],
    current_chunks: tuple[ExtractedChunk, ...],
    actor: ActorRef,
    occurred_at: datetime,
) -> SourceChangeUpgradeResult:
    """Detect a source revision and create review-gated, immutable suggestions.

    Removed citations remain pinned to their old valid source and are reported as
    unresolved.  The helper never invents replacement card content merely to
    make a suggestion publishable.
    """

    if previous_source_version_id == current_source_version_id:
        raise ValueError("source upgrade versions must differ")
    _validate_source_change_inputs(
        catalog,
        previous_source_version_id=previous_source_version_id,
        current_source_version_id=current_source_version_id,
        previous_chunks=previous_chunks,
        current_chunks=current_chunks,
    )
    changes = detect_chunk_changes(previous_chunks, current_chunks)
    replacements = {
        change.previous_chunk_id: current
        for change in changes
        if change.kind is ChunkChangeKind.changed and change.current_chunk_id is not None
        for current in current_chunks
        if current.chunk_id == change.current_chunk_id
    }
    affected_cards, snapshot = _affected_snapshot(
        catalog, version_id=previous_source_version_id, asset_kind="source"
    )
    affected_card_ids = snapshot.card_version_ids
    affected_courses = snapshot.course_version_ids
    evidence_id = _upgrade_id(
        "upgrade-evidence",
        {
            "previous_source_version_id": previous_source_version_id,
            "current_source_version_id": current_source_version_id,
            "chunk_changes": [
                {
                    "kind": item.kind.value,
                    "previous_chunk_id": item.previous_chunk_id,
                    "current_chunk_id": item.current_chunk_id,
                    "field_diffs": [diff.__dict__ for diff in item.field_diffs],
                }
                for item in changes
            ],
            "affected_card_version_ids": list(affected_card_ids),
            "affected_course_version_ids": list(affected_courses),
            "affected_snapshot_digest": snapshot.content_digest,
        },
    )
    evidence = EvidenceObject(
        evidence_id=evidence_id,
        kind="validation",
        subject_version_id=current_source_version_id,
        status="verified",
        input_summary={
            "previous_source_version_id": previous_source_version_id,
            "changed_chunk_count": sum(item.kind is ChunkChangeKind.changed for item in changes),
            "unchanged_chunk_count": sum(item.kind is ChunkChangeKind.unchanged for item in changes),
            "removed_chunk_count": sum(item.kind is ChunkChangeKind.removed for item in changes),
        },
        output_summary={
            "affected_card_version_ids": list(affected_card_ids),
            "affected_course_version_ids": list(affected_courses),
            "affected_snapshot_digest": snapshot.content_digest,
            "field_diffs": [
                {
                    "chunk_id": item.previous_chunk_id,
                    "field": diff.field_name,
                    "before_digest": diff.before_digest,
                    "after_digest": diff.after_digest,
                }
                for item in changes
                for diff in item.field_diffs
            ],
        },
        producer="course-helper/upgrades",
        producer_version="1",
        started_at=occurred_at,
        finished_at=occurred_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="field-level-source-diff",
                status="passed",
                message="Compared typed chunk fields by digest without copying source bodies.",
            ),
        ),
    )
    with catalog.atomic_write():
        _reuse_evidence(catalog, evidence)
        suggestions: list[UpgradeSuggestion] = []
        unresolved: list[str] = []
        removed_ids = {item.previous_chunk_id for item in changes if item.kind is ChunkChangeKind.removed}
        for card in affected_cards:
            if any(citation.chunk_id in removed_ids for citation in card.chunk_citations):
                unresolved.append(card.version_id)
                continue
            candidate = _candidate_card(card, replacements, actor=actor, occurred_at=occurred_at)
            if candidate is None:
                continue
            candidate = _reuse_candidate(catalog, candidate)
            suggestion_id = _upgrade_id(
                "upgrade",
                {
                    "current_version_id": card.version_id,
                    "candidate_version_id": candidate.version_id,
                    "evidence_id": evidence_id,
                },
            )
            if _existing_upgrade_suggestion(catalog, suggestion_id) is not None:
                suggestion = _make_upgrade_suggestion(
                    catalog,
                    current_version_id=card.version_id,
                    candidate_version_id=candidate.version_id,
                    evidence_id=evidence_id,
                    actor=actor,
                    occurred_at=occurred_at,
                )
                _require_existing_card_replay_gates(
                    catalog,
                    candidate_version_id=candidate.version_id,
                    source_evidence_id=evidence_id,
                )
                suggestions.append(suggestion)
                continue
            candidate_evidence = _candidate_gate_evidence(
                catalog, candidate, source_evidence_id=evidence_id, occurred_at=occurred_at
            )
            _reuse_evidence(catalog, candidate_evidence)
            # Task 5 owns this scan and its degraded-mode blocking task.  Do not
            # substitute a hand-created review, even when no embedding provider is
            # available.
            scan_near_duplicates(
                candidate, catalog, embedding_provider=None, created_at=occurred_at,
                _allow_active_transaction=True,
            )
            create_review_task(
                catalog,
                kind="provenance",
                subject_version_id=candidate.version_id,
                evidence_ids=(candidate_evidence.evidence_id,),
                created_at=occurred_at,
                created_by=actor,
            )
            suggestions.append(
                _make_upgrade_suggestion(
                    catalog,
                    current_version_id=card.version_id,
                    candidate_version_id=candidate.version_id,
                    evidence_id=evidence_id,
                    actor=actor,
                    occurred_at=occurred_at,
                )
            )
        source_suggestion = _make_upgrade_suggestion(
            catalog,
            current_version_id=previous_source_version_id,
            candidate_version_id=current_source_version_id,
            evidence_id=evidence_id,
            actor=actor,
            occurred_at=occurred_at,
        )
        result = SourceChangeUpgradeResult(
            evidence_id=evidence_id,
            source_suggestion=source_suggestion,
            card_suggestions=tuple(suggestions),
            unresolved_card_version_ids=tuple(sorted(unresolved)),
            affected_course_version_ids=affected_courses,
            affected_snapshot=snapshot,
            chunk_changes=changes,
        )
    return result


def propose_course_feedback(
    catalog: KnowledgeCatalog,
    *,
    course_version_id: str,
    summary: str,
    actor: ActorRef,
    evidence_ids: tuple[str, ...],
    occurred_at: datetime,
) -> CourseFeedbackSuggestion:
    """Persist human course feedback as a typed, digest-bound review suggestion."""

    if not evidence_ids:
        raise ValueError("course feedback requires evidence IDs")
    for evidence_id in evidence_ids:
        if not catalog._row_exists("evidence", "evidence_id", evidence_id):
            raise CatalogReferenceError("course feedback evidence is not persisted")
    feedback_identity = {
        "course_version_id": course_version_id,
        "summary": summary,
        "actor": actor.model_dump(mode="json"),
        "evidence_ids": sorted(evidence_ids),
    }
    audit_evidence = EvidenceObject(
        evidence_id=_upgrade_id("feedback-audit", feedback_identity),
        kind="validation",
        subject_version_id=course_version_id,
        status="verified",
        input_summary={
            "course_version_id": course_version_id,
            "summary_digest": _field_digest(summary),
            "actor_digest": _digest(actor.model_dump(mode="json")),
        },
        output_summary={"evidence_ids": list(sorted(evidence_ids))},
        producer="course-helper/upgrades",
        producer_version="1",
        started_at=occurred_at,
        finished_at=occurred_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="course-feedback-identity",
                status="passed",
                message="Feedback task identity is bound to summary and actor digests.",
            ),
        ),
    )
    with catalog.atomic_write():
        audit_evidence = _reuse_evidence(catalog, audit_evidence)
        task_evidence_ids = tuple(sorted((*evidence_ids, audit_evidence.evidence_id)))
        suggestion_id = _upgrade_id("feedback", feedback_identity)
        existing = _existing_feedback_suggestion(catalog, suggestion_id)
        if existing is not None:
            register_feedback_suggestion(catalog, existing)
            if (
                existing.course_version_id != course_version_id
                or existing.summary != summary
                or existing.created_by != actor
                or existing.evidence_ids != task_evidence_ids
            ):
                raise ImmutableVersionConflict("feedback suggestion ID has different semantic binding")
            return existing
        task = create_review_task(
            catalog,
            kind="course-feedback",
            subject_version_id=course_version_id,
            evidence_ids=task_evidence_ids,
            created_at=occurred_at,
            created_by=actor,
        )
        suggestion = CourseFeedbackSuggestion(
            suggestion_id=suggestion_id,
            course_version_id=course_version_id,
            review_task_id=task.task_id,
            summary=summary,
            evidence_ids=task_evidence_ids,
            created_at=occurred_at,
            created_by=actor,
        )
        return register_feedback_suggestion(catalog, suggestion)


def _find_suggestion(
    catalog: KnowledgeCatalog,
    suggestion_id: str,
) -> UpgradeSuggestion | CourseFeedbackSuggestion:
    for table, model_type in (
        ("upgrade_suggestions", UpgradeSuggestion),
        ("feedback_suggestions", CourseFeedbackSuggestion),
    ):
        row = catalog.connection.execute(
            f"SELECT payload_json FROM {table} WHERE suggestion_id = ?", (suggestion_id,)
        ).fetchone()
        if row is not None:
            return model_type.model_validate_json(row[0], strict=False)
    raise CatalogReferenceError("upgrade suggestion is not persisted")


def _candidate_gate_evidence(
    catalog: KnowledgeCatalog,
    candidate: KnowledgeCardVersion,
    *,
    source_evidence_id: str,
    occurred_at: datetime,
) -> EvidenceObject:
    """Record exact, tag, and provenance checks; Task 5 owns near-dedup evidence."""

    unknown_tags = tuple(
        assignment.tag_id
        for assignment in candidate.tag_assignments
        if not catalog._row_exists(
            "tag_values",
            "vocabulary_version_id",
            assignment.vocabulary_version_id,
            secondary_column="tag_id",
            secondary_value=assignment.tag_id,
        )
    )
    missing_citations = tuple(
        citation.chunk_id
        for citation in candidate.chunk_citations
        if not catalog._row_exists("chunks", "chunk_id", citation.chunk_id)
    )
    exact = find_exact_duplicate(candidate, catalog)
    return EvidenceObject(
        evidence_id=_upgrade_id(
            "candidate-gate-evidence",
            {
                "candidate_version_id": candidate.version_id,
                "source_evidence_id": source_evidence_id,
                "unknown_tags": unknown_tags,
                "missing_citations": missing_citations,
                "exact_duplicate_version_id": None if exact is None else exact.version_id,
            },
        ),
        kind="dedup",
        subject_version_id=candidate.version_id,
        status="verified" if not unknown_tags and not missing_citations and exact is None else "warning",
        input_summary={"source_evidence_id": source_evidence_id},
        output_summary={
            "exact_duplicate_version_id": None if exact is None else exact.version_id,
            "unknown_tag_ids": list(unknown_tags),
            "missing_citation_ids": list(missing_citations),
        },
        producer="course-helper/upgrades",
        producer_version="1",
        started_at=occurred_at,
        finished_at=occurred_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="exact-dedup-clean-scan",
                status="passed" if exact is None else "warning",
                message="No exact duplicate found." if exact is None else "Exact duplicate requires review.",
            ),
            EvidenceCheck(
                code="tag-clean-scan",
                status="passed" if not unknown_tags else "failed",
                message="All candidate tags are pinned." if not unknown_tags else "Candidate has unknown tags.",
            ),
            EvidenceCheck(
                code="provenance-clean-scan",
                status="passed" if not missing_citations else "failed",
                message="All cited chunks are persisted." if not missing_citations else "Candidate has missing citations.",
            ),
        ),
    )


def resolve_upgrade_suggestion(
    catalog: KnowledgeCatalog,
    *,
    suggestion_id: str,
    decision: Literal["accept", "reject", "dismiss"],
    actor: ActorRef,
    evidence_ids: tuple[str, ...],
    resolved_at: datetime,
    expected_suggestion_digest: str | None = None,
    expected_review_digest: str | None = None,
    expected_candidate_digest: str | None = None,
) -> UpgradeAcceptanceOutcome:
    """Resolve a suggestion with the normal append-only review audit.

    Acceptance only resolves the suggestion review.  A card candidate remains a
    ``review`` version and must traverse the existing dedup/tag/provenance
    gates plus ``publish_card``; neither old card nor old course bytes change.
    """

    suggestion = _find_suggestion(catalog, suggestion_id)
    suggestion_row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM upgrade_suggestions "
        "WHERE suggestion_id = ?",
        (suggestion_id,),
    ).fetchone()
    if suggestion_row is None:
        if expected_suggestion_digest is not None:
            raise CatalogReferenceError(
                "knowledge upgrade resolution requires a card upgrade suggestion"
            )
    else:
        suggestion_payload = str(suggestion_row[1])
        suggestion_digest = hashlib.sha256(
            suggestion_payload.encode("utf-8")
        ).hexdigest()
        if (
            canonical_model_json(suggestion) != suggestion_payload
            or suggestion_digest != str(suggestion_row[0])
            or (
                expected_suggestion_digest is not None
                and expected_suggestion_digest != suggestion_digest
            )
        ):
            raise CatalogReferenceError("upgrade suggestion digest is stale")
    row = catalog.connection.execute(
        "SELECT payload_json FROM review_tasks WHERE task_id = ?", (suggestion.review_task_id,)
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("suggestion review task is not persisted")
    task = ReviewTask.model_validate_json(row[0], strict=False)
    stored_review_digest = _task_digest(task)
    if (
        expected_review_digest is not None
        and expected_review_digest != stored_review_digest
    ):
        raise CatalogReferenceError("upgrade review digest is stale")
    if expected_candidate_digest is not None:
        candidate_row = catalog.connection.execute(
            "SELECT content_digest, payload_json FROM cards WHERE version_id = ?",
            (getattr(suggestion, "candidate_version_id", ""),),
        ).fetchone()
        if candidate_row is None:
            raise CatalogReferenceError(
                "knowledge upgrade resolution requires a card candidate"
            )
        candidate = KnowledgeCardVersion.model_validate_json(
            str(candidate_row[1]), strict=False
        )
        if (
            canonical_model_json(candidate) != str(candidate_row[1])
            or candidate.content_digest != str(candidate_row[0])
            or candidate.content_digest != expected_candidate_digest
        ):
            raise CatalogReferenceError("upgrade candidate digest is stale")
    resolution = ReviewResolution(
        resolution_id=_upgrade_id(
            "resolution",
            {
                "suggestion_id": suggestion_id,
                "decision": decision,
                "expected_review_digest": stored_review_digest,
                "actor": actor.model_dump(mode="json"),
                "evidence_ids": sorted(evidence_ids),
                "resolved_at": resolved_at.isoformat(),
            },
        ),
        task_id=task.task_id,
        decision=decision,
        expected_review_digest=stored_review_digest,
        evidence_ids=tuple(sorted(evidence_ids)),
        resolved_at=resolved_at,
        resolved_by=actor,
    )
    stored = resolve_review_task(catalog, resolution)
    candidate_version_id = (
        suggestion.candidate_version_id
        if isinstance(suggestion, UpgradeSuggestion)
        else None
    )
    if stored.decision != "accept":
        return UpgradeAcceptanceOutcome(
            suggestion_id=suggestion_id,
            candidate_version_id=candidate_version_id,
            decision=stored.decision,
            resolution_id=stored.resolution_id,
            next_required_review_task_ids=(),
            next_action="no_action",
        )
    if candidate_version_id is None:
        return UpgradeAcceptanceOutcome(
            suggestion_id=suggestion_id,
            candidate_version_id=None,
            decision=stored.decision,
            resolution_id=stored.resolution_id,
            next_required_review_task_ids=(),
            next_action="compose_candidate_from_feedback",
        )
    rows = catalog.connection.execute(
        "SELECT task_id FROM review_task_current WHERE task_id IN "
        "(SELECT task_id FROM review_tasks WHERE subject_version_id = ?) "
        "AND current_status = 'open' ORDER BY task_id",
        (candidate_version_id,),
    ).fetchall()
    candidate_is_card = catalog.get_card(candidate_version_id) is not None
    return UpgradeAcceptanceOutcome(
        suggestion_id=suggestion_id,
        candidate_version_id=candidate_version_id,
        decision=stored.decision,
        resolution_id=stored.resolution_id,
        next_required_review_task_ids=tuple(str(row[0]) for row in rows),
        next_action=(
            "knowledge_card_publish"
            if candidate_is_card
            else "review_affected_knowledge"
        ),
    )


__all__ = [
    "ChunkChange",
    "ChunkChangeKind",
    "FieldDigestDiff",
    "AssetChangeUpgradeResult",
    "AffectedPlacementSnapshot",
    "SourceChangeUpgradeResult",
    "UpgradeAcceptanceOutcome",
    "detect_chunk_changes",
    "propose_course_feedback",
    "propose_asset_change_upgrade",
    "propose_source_change_upgrades",
    "resolve_upgrade_suggestion",
]
