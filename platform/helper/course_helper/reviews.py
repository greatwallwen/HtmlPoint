"""Append-only review resolution and immutable suggestion storage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from course_helper.catalog import (
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef, OpaqueId
from course_helper.domain.knowledge import KnowledgeCardVersion, ReviewTask, ReviewTaskKind
from course_helper.domain.sources import (
    DatasetAssetVersion,
    SourceAssetVersion,
    VisualAssetVersion,
)


_REVIEW_MAPPING: dict[str, tuple[str, str]] = {
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
    "exact-duplicate": ("exact-duplicate", "exact-duplicate"),
    "course-feedback": ("course-feedback", "course-feedback"),
}


class ReviewProjectionError(RuntimeError):
    """Raw review facts cannot be projected without losing integrity."""


class ReviewQueryError(ValueError):
    """A bounded review query is malformed or references an unknown cursor."""


class ReviewNotFoundError(LookupError):
    """A requested opaque review identity does not exist."""


class ReviewResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    resolution_id: OpaqueId
    task_id: OpaqueId
    decision: Literal["accept", "reject", "dismiss"]
    expected_review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_ids: tuple[OpaqueId, ...] = ()
    resolved_at: datetime
    resolved_by: ActorRef

    @field_validator("resolved_at")
    @classmethod
    def aware_resolved_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("resolved_at must be timezone-aware")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_resolution_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("resolution evidence IDs must be unique")
        return value


class UpgradeSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    suggestion_id: OpaqueId
    current_version_id: OpaqueId
    candidate_version_id: OpaqueId
    review_task_id: OpaqueId
    reason_code: ReviewTaskKind
    evidence_ids: tuple[OpaqueId, ...] = ()
    created_at: datetime
    created_by: ActorRef

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_upgrade_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("upgrade evidence IDs must be unique")
        return value


class CourseFeedbackSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    suggestion_id: OpaqueId
    course_version_id: OpaqueId
    review_task_id: OpaqueId
    summary: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[OpaqueId, ...] = ()
    created_at: datetime
    created_by: ActorRef

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_feedback_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("feedback evidence IDs must be unique")
        return value


class ReviewListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: OpaqueId
    subject_version_id: OpaqueId
    category: str = Field(min_length=1, max_length=64)
    reason_code: ReviewTaskKind
    status: Literal["open", "resolved", "dismissed"]
    blocking: bool
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_count: int = Field(ge=0)
    created_at: datetime


class ReviewListPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    items: tuple[ReviewListItem, ...]
    next_cursor: str | None = Field(
        default=None, pattern=r"^review-cursor-[0-9a-f]{32}$"
    )


class ReviewContentExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: tuple[int, ...] = Field(max_length=32)
    depth: int = Field(ge=1)
    node_type: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=2000)
    level: int | None = Field(default=None, ge=1, le=6)
    language: str | None = Field(default=None, max_length=64)
    rows: tuple[tuple[str, ...], ...] = Field(default=(), max_length=5)


class ReviewCitationExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    chunk_id: OpaqueId
    source_version_id: OpaqueId
    quoted_text: str | None = Field(default=None, max_length=1000)


class ReviewDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: ReviewListItem
    evidence_ids: tuple[OpaqueId, ...] = Field(max_length=50)
    evidence_total: int = Field(ge=0)
    evidence_truncated: bool
    card_version_id: OpaqueId | None = None
    card_content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    card_title: str | None = Field(default=None, max_length=500)
    learning_objective: str | None = Field(default=None, max_length=1000)
    content_nodes: tuple[ReviewContentExcerpt, ...] = Field(max_length=50)
    content_node_total: int = Field(ge=0)
    content_nodes_truncated: bool
    citations: tuple[ReviewCitationExcerpt, ...] = Field(max_length=50)
    citation_total: int = Field(ge=0)
    citations_truncated: bool


class UpgradeListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    suggestion_id: OpaqueId
    current_version_id: OpaqueId
    candidate_version_id: OpaqueId
    review_task_id: OpaqueId
    reason_code: ReviewTaskKind
    status: Literal["open", "resolved", "dismissed"]
    suggestion_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_count: int = Field(ge=0)
    created_at: datetime


class UpgradeListPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    items: tuple[UpgradeListItem, ...]
    next_cursor: str | None = Field(
        default=None, pattern=r"^upgrade-cursor-[0-9a-f]{32}$"
    )


def _payload_digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_timestamp(payload: str, field: str) -> str:
    try:
        value = json.loads(payload)[field]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise CatalogReferenceError(f"immutable payload is missing {field}") from error
    if not isinstance(value, str):
        raise CatalogReferenceError(f"immutable payload {field} must be text")
    return value


def _raw_review(
    catalog: KnowledgeCatalog, task_id: str
) -> tuple[ReviewTask, str, str] | None:
    row = catalog.connection.execute(
        "SELECT kind, status, payload_json FROM review_tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        task = ReviewTask.model_validate_json(row[2], strict=False)
    except Exception as error:
        raise CatalogReferenceError("stored review task cannot be validated") from error
    if task.task_id != task_id or task.kind != row[0] or task.status != row[1]:
        raise CatalogReferenceError("stored review task columns do not match raw bytes")
    if canonical_model_json(task) != row[2]:
        raise CatalogReferenceError("stored review task payload is not canonical")
    return task, row[2], _payload_digest(row[2])


def _existing_resolution(
    catalog: KnowledgeCatalog,
    resolution_id: str,
) -> tuple[ReviewResolution, str, str] | None:
    row = catalog.connection.execute(
        "SELECT resolution_id, task_id, decision, expected_review_digest, "
        "content_digest, payload_json, created_at FROM review_resolutions "
        "WHERE resolution_id = ?",
        (resolution_id,),
    ).fetchone()
    if row is None:
        return None
    payload = str(row[5])
    digest = str(row[4])
    try:
        stored = ReviewResolution.model_validate_json(payload, strict=False)
        resolved_at = _payload_timestamp(payload, "resolved_at")
    except Exception as error:
        raise CatalogReferenceError("review resolution envelope is invalid") from error
    if (
        canonical_model_json(stored) != payload
        or _payload_digest(payload) != digest
        or stored.resolution_id != str(row[0])
        or stored.resolution_id != resolution_id
        or stored.task_id != str(row[1])
        or stored.decision != str(row[2])
        or stored.expected_review_digest != str(row[3])
        or resolved_at != str(row[6])
    ):
        raise CatalogReferenceError(
            "review resolution envelope does not match immutable payload"
        )
    return stored, payload, digest


def _require_resolution_projection(
    catalog: KnowledgeCatalog,
    resolution: ReviewResolution,
) -> None:
    raw = _raw_review(catalog, resolution.task_id)
    if raw is None:
        raise CatalogReferenceError("review resolution task is not persisted")
    task, _, raw_digest = raw
    expected_category, expected_reason = _REVIEW_MAPPING[task.kind]
    expected_status = "dismissed" if resolution.decision == "dismiss" else "resolved"
    projection = catalog.connection.execute(
        "SELECT category, reason_code, review_digest, current_status, resolution_id "
        "FROM review_task_current WHERE task_id = ?",
        (resolution.task_id,),
    ).fetchone()
    if (
        resolution.expected_review_digest != raw_digest
        or projection
        != (
            expected_category,
            expected_reason,
            raw_digest,
            expected_status,
            resolution.resolution_id,
        )
    ):
        raise CatalogReferenceError(
            "review resolution projection does not match immutable envelope"
        )


def resolve_review_task(
    catalog: KnowledgeCatalog,
    resolution: ReviewResolution,
) -> ReviewResolution:
    """Append one digest-bound resolution and its evidence links atomically."""

    payload = canonical_model_json(resolution)
    digest = _payload_digest(payload)
    with catalog.atomic_write():
        existing = _existing_resolution(catalog, resolution.resolution_id)
        if existing is not None:
            stored, stored_payload, stored_digest = existing
            if (stored_payload, stored_digest) == (payload, digest):
                _require_resolution_projection(catalog, stored)
                joined = {
                    evidence_id
                    for (evidence_id,) in catalog.connection.execute(
                        "SELECT evidence_id FROM review_resolution_evidence "
                        "WHERE resolution_id = ?",
                        (resolution.resolution_id,),
                    ).fetchall()
                }
                if joined != set(stored.evidence_ids):
                    raise CatalogReferenceError(
                        "review resolution evidence links do not match raw bytes"
                    )
                return stored
            raise ImmutableVersionConflict(
                "review resolution ID already has different bytes"
            )
        existing_task = catalog.connection.execute(
            "SELECT resolution_id FROM review_resolutions WHERE task_id = ?",
            (resolution.task_id,),
        ).fetchone()
        if existing_task is not None:
            raise ImmutableVersionConflict("review task already has a resolution")
        raw = _raw_review(catalog, resolution.task_id)
        if raw is None:
            raise CatalogReferenceError("review task is not persisted")
        task, _, raw_digest = raw
        if resolution.resolved_at < task.created_at:
            raise CatalogReferenceError("review resolution cannot predate task creation")
        projection = catalog.connection.execute(
            "SELECT category, reason_code, review_digest, current_status, resolution_id "
            "FROM review_task_current WHERE task_id = ?",
            (resolution.task_id,),
        ).fetchone()
        expected_category, expected_reason = _REVIEW_MAPPING[task.kind]
        if projection != (
            expected_category,
            expected_reason,
            raw_digest,
            "open",
            None,
        ):
            raise CatalogReferenceError("review projection is missing or inconsistent")
        if resolution.expected_review_digest != raw_digest:
            raise CatalogReferenceError("review resolution digest does not match raw task bytes")
        for evidence_id in resolution.evidence_ids:
            if not catalog._row_exists("evidence", "evidence_id", evidence_id):
                raise CatalogReferenceError(
                    f"review resolution evidence is not persisted: {evidence_id!r}"
                )
        catalog.connection.execute(
            "INSERT INTO review_resolutions("
            "resolution_id, task_id, decision, expected_review_digest, content_digest, "
            "payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                resolution.resolution_id,
                resolution.task_id,
                resolution.decision,
                resolution.expected_review_digest,
                digest,
                payload,
                _payload_timestamp(payload, "resolved_at"),
            ),
        )
    return resolution


def _suggestion_existing_or_conflict(
    catalog: KnowledgeCatalog,
    *,
    table: str,
    suggestion_id: str,
    payload: str,
    digest: str,
    model_type: type[UpgradeSuggestion] | type[CourseFeedbackSuggestion],
    evidence_table: str,
) -> UpgradeSuggestion | CourseFeedbackSuggestion | None:
    if table == "upgrade_suggestions":
        row = catalog.connection.execute(
            "SELECT suggestion_id, review_task_id, current_version_id, "
            "candidate_version_id, content_digest, payload_json, created_at "
            "FROM upgrade_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
    elif table == "feedback_suggestions":
        row = catalog.connection.execute(
            "SELECT suggestion_id, review_task_id, course_version_id, "
            "content_digest, payload_json, created_at "
            "FROM feedback_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
    else:
        raise ValueError(f"unsupported suggestion table: {table}")
    if row is None:
        return None
    stored_payload = str(row[-2])
    stored_digest = str(row[-3])
    try:
        model = model_type.model_validate_json(stored_payload, strict=False)
        created_at = _payload_timestamp(stored_payload, "created_at")
    except Exception as error:
        raise CatalogReferenceError("suggestion envelope is invalid") from error
    common_matches = (
        canonical_model_json(model) == stored_payload
        and _payload_digest(stored_payload) == stored_digest
        and model.suggestion_id == str(row[0])
        and model.suggestion_id == suggestion_id
        and model.review_task_id == str(row[1])
        and created_at == str(row[-1])
    )
    if isinstance(model, UpgradeSuggestion):
        envelope_matches = (
            common_matches
            and model.current_version_id == str(row[2])
            and model.candidate_version_id == str(row[3])
        )
    else:
        envelope_matches = common_matches and model.course_version_id == str(row[2])
    if not envelope_matches:
        raise CatalogReferenceError(
            "suggestion envelope does not match immutable payload"
        )
    if (stored_payload, stored_digest) != (payload, digest):
        raise ImmutableVersionConflict(
            f"{table} suggestion ID already has different bytes"
        )
    joined = {
        evidence_id
        for (evidence_id,) in catalog.connection.execute(
            f"SELECT evidence_id FROM {evidence_table} WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchall()
    }
    if joined != set(model.evidence_ids):
        raise CatalogReferenceError("suggestion evidence links do not match raw bytes")
    return model


def _require_evidence(catalog: KnowledgeCatalog, evidence_ids: tuple[str, ...]) -> None:
    for evidence_id in evidence_ids:
        if not catalog._row_exists("evidence", "evidence_id", evidence_id):
            raise CatalogReferenceError(
                f"suggestion evidence is not persisted: {evidence_id!r}"
            )


def _upgrade_version_descriptor(
    catalog: KnowledgeCatalog,
    version_id: str,
) -> tuple[str, str, int, str | None, str] | None:
    model_types = {
        "sources": SourceAssetVersion,
        "cards": KnowledgeCardVersion,
        "datasets": DatasetAssetVersion,
        "visuals": VisualAssetVersion,
    }
    for table, model_type in model_types.items():
        row = catalog.connection.execute(
            f"SELECT version_id, logical_id, revision, content_digest, payload_json "
            f"FROM {table} WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is not None:
            try:
                model = model_type.model_validate_json(str(row[4]), strict=False)
            except Exception as error:
                raise CatalogReferenceError("upgrade version raw payload is invalid") from error
            if (
                canonical_model_json(model) != str(row[4])
                or model.version_id != str(row[0])
                or model.version_id != version_id
                or model.logical_id != str(row[1])
                or model.revision != int(row[2])
                or model.content_digest != str(row[3])
            ):
                raise CatalogReferenceError(
                    "upgrade version raw columns do not match its canonical payload"
                )
            return (
                table,
                model.logical_id,
                model.revision,
                model.supersedes_version_id,
                model.content_digest,
            )
    return None


def _upgrade_descends_from(
    catalog: KnowledgeCatalog,
    *,
    candidate_id: str,
    current_id: str,
    table: str,
) -> bool:
    cursor = candidate_id
    visited: set[str] = set()
    while cursor not in visited:
        visited.add(cursor)
        descriptor = _upgrade_version_descriptor(catalog, cursor)
        if descriptor is None or descriptor[0] != table:
            return False
        predecessor = descriptor[3]
        if predecessor == current_id:
            return True
        if predecessor is None:
            return False
        cursor = predecessor
    return False


def _require_upgrade_binding(
    catalog: KnowledgeCatalog,
    suggestion: UpgradeSuggestion,
) -> None:
    task_raw = _raw_review(catalog, suggestion.review_task_id)
    if task_raw is None:
        raise CatalogReferenceError("upgrade review task is not persisted")
    task = task_raw[0]
    if (
        task.kind != suggestion.reason_code
        or task.subject_version_id != suggestion.candidate_version_id
    ):
        raise CatalogReferenceError("upgrade review task does not bind its candidate")
    if suggestion.current_version_id == suggestion.candidate_version_id:
        raise CatalogReferenceError("upgrade versions must differ")
    current = _upgrade_version_descriptor(catalog, suggestion.current_version_id)
    candidate = _upgrade_version_descriptor(catalog, suggestion.candidate_version_id)
    if current is None or candidate is None:
        raise CatalogReferenceError("upgrade version is not persisted")
    if current[0] != candidate[0] or current[1] != candidate[1]:
        raise CatalogReferenceError(
            "upgrade versions must share one durable kind and logical ID"
        )
    if candidate[2] <= current[2]:
        raise CatalogReferenceError("upgrade candidate must have a newer revision")
    if not _upgrade_descends_from(
        catalog,
        candidate_id=suggestion.candidate_version_id,
        current_id=suggestion.current_version_id,
        table=current[0],
    ):
        raise CatalogReferenceError(
            "upgrade candidate must descend from the current immutable version"
        )


def _require_feedback_binding(
    catalog: KnowledgeCatalog,
    suggestion: CourseFeedbackSuggestion,
) -> None:
    task_raw = _raw_review(catalog, suggestion.review_task_id)
    if task_raw is None:
        raise CatalogReferenceError("feedback review task is not persisted")
    task = task_raw[0]
    if task.kind != "course-feedback" or task.subject_version_id != suggestion.course_version_id:
        raise CatalogReferenceError("feedback review task does not bind its course")
    if not catalog._row_exists(
        "course_versions", "version_id", suggestion.course_version_id
    ):
        raise CatalogReferenceError("feedback course version is not persisted")


def register_upgrade_suggestion(
    catalog: KnowledgeCatalog,
    suggestion: UpgradeSuggestion,
) -> UpgradeSuggestion:
    payload = canonical_model_json(suggestion)
    digest = _payload_digest(payload)
    with catalog.atomic_write():
        existing = _suggestion_existing_or_conflict(
            catalog,
            table="upgrade_suggestions",
            suggestion_id=suggestion.suggestion_id,
            payload=payload,
            digest=digest,
            model_type=UpgradeSuggestion,
            evidence_table="upgrade_suggestion_evidence",
        )
        if existing is not None:
            _require_upgrade_binding(catalog, existing)  # type: ignore[arg-type]
            return existing  # type: ignore[return-value]
        _require_upgrade_binding(catalog, suggestion)
        _require_evidence(catalog, suggestion.evidence_ids)
        catalog.connection.execute(
            "INSERT INTO upgrade_suggestions("
            "suggestion_id, review_task_id, current_version_id, candidate_version_id, "
            "content_digest, payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                suggestion.suggestion_id,
                suggestion.review_task_id,
                suggestion.current_version_id,
                suggestion.candidate_version_id,
                digest,
                payload,
                _payload_timestamp(payload, "created_at"),
            ),
        )
    return suggestion


def register_feedback_suggestion(
    catalog: KnowledgeCatalog,
    suggestion: CourseFeedbackSuggestion,
) -> CourseFeedbackSuggestion:
    payload = canonical_model_json(suggestion)
    digest = _payload_digest(payload)
    with catalog.atomic_write():
        existing = _suggestion_existing_or_conflict(
            catalog,
            table="feedback_suggestions",
            suggestion_id=suggestion.suggestion_id,
            payload=payload,
            digest=digest,
            model_type=CourseFeedbackSuggestion,
            evidence_table="feedback_suggestion_evidence",
        )
        if existing is not None:
            _require_feedback_binding(catalog, existing)  # type: ignore[arg-type]
            return existing  # type: ignore[return-value]
        _require_feedback_binding(catalog, suggestion)
        _require_evidence(catalog, suggestion.evidence_ids)
        catalog.connection.execute(
            "INSERT INTO feedback_suggestions("
            "suggestion_id, review_task_id, course_version_id, content_digest, "
            "payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                suggestion.suggestion_id,
                suggestion.review_task_id,
                suggestion.course_version_id,
                digest,
                payload,
                _payload_timestamp(payload, "created_at"),
            ),
        )
    return suggestion


def _opaque_cursor(prefix: str, identity: str) -> str:
    return f"{prefix}-cursor-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _review_list_item(
    catalog: KnowledgeCatalog,
    task_id: str,
) -> tuple[ReviewListItem, ReviewTask]:
    raw = _raw_review(catalog, task_id)
    if raw is None:
        raise ReviewNotFoundError("review task is unavailable")
    task, _payload, digest = raw
    if not catalog._version_exists(task.subject_version_id):
        raise ReviewProjectionError("review subject is dangling")
    projection = catalog.connection.execute(
        "SELECT category, reason_code, review_digest, current_status, resolution_id "
        "FROM review_task_current WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    expected_category, expected_reason = _REVIEW_MAPPING[task.kind]
    if projection is None:
        raise ReviewProjectionError("review projection is unavailable")
    category, reason_code, review_digest, current_status, resolution_id = projection
    if (
        category != expected_category
        or reason_code != expected_reason
        or review_digest != digest
        or current_status not in {"open", "resolved", "dismissed"}
    ):
        raise ReviewProjectionError("review projection is inconsistent")
    if resolution_id is None:
        if current_status != "open":
            raise ReviewProjectionError("review resolution is unavailable")
    else:
        resolution = _existing_resolution(catalog, str(resolution_id))
        if resolution is None or resolution[0].task_id != task_id:
            raise ReviewProjectionError("review resolution is unavailable")
        try:
            _require_resolution_projection(catalog, resolution[0])
        except CatalogReferenceError as error:
            raise ReviewProjectionError("review resolution is inconsistent") from error
    return (
        ReviewListItem(
            task_id=task.task_id,
            subject_version_id=task.subject_version_id,
            category=str(category),
            reason_code=task.reason_code,
            status=str(current_status),
            blocking=task.blocking,
            review_digest=digest,
            evidence_count=len(task.evidence_ids),
            created_at=task.created_at,
        ),
        task,
    )


def list_review_tasks(
    catalog: KnowledgeCatalog,
    *,
    status: Literal["open", "resolved", "dismissed"] | None = None,
    category: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ReviewListPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ReviewQueryError("review limit is invalid")
    if status not in {None, "open", "resolved", "dismissed"}:
        raise ReviewQueryError("review status is invalid")
    if category not in {None, *{value[0] for value in _REVIEW_MAPPING.values()}}:
        raise ReviewQueryError("review category is invalid")
    after: str | None = None
    if cursor is not None:
        match = re.fullmatch(r"review-cursor-([0-9a-f]{32})", cursor)
        if match is None:
            raise ReviewQueryError("review cursor is invalid")
        row = catalog.connection.execute(
            "SELECT task_id FROM review_tasks "
            "WHERE substr(sha256_hex(task_id), 1, 32) = ?",
            (match.group(1),),
        ).fetchone()
        if row is None:
            raise ReviewQueryError("review cursor is invalid")
        after = str(row[0])
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("current.current_status = ?")
        params.append(status)
    if category is not None:
        clauses.append("current.category = ?")
        params.append(category)
    if after is not None:
        clauses.append("task.task_id > ?")
        params.append(after)
    query = (
        "SELECT task.task_id FROM review_tasks task "
        "JOIN review_task_current current USING(task_id)"
    )
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY task.task_id LIMIT ?"
    params.append(limit + 1)
    rows = catalog.connection.execute(query, tuple(params)).fetchall()
    items = tuple(_review_list_item(catalog, str(row[0]))[0] for row in rows[:limit])
    return ReviewListPage(
        items=items,
        next_cursor=(
            _opaque_cursor("review", items[-1].task_id)
            if len(rows) > limit and items
            else None
        ),
    )


def get_review_detail(catalog: KnowledgeCatalog, task_id: str) -> ReviewDetail:
    item, task = _review_list_item(catalog, task_id)
    card_row = catalog.connection.execute(
        "SELECT version_id, logical_id, revision, status, content_digest, payload_json "
        "FROM cards WHERE version_id = ?",
        (task.subject_version_id,),
    ).fetchone()
    card: KnowledgeCardVersion | None = None
    if card_row is not None:
        try:
            candidate = KnowledgeCardVersion.model_validate_json(str(card_row[5]), strict=False)
        except Exception as error:
            raise ReviewProjectionError("review card bytes are invalid") from error
        if (
            canonical_model_json(candidate) != str(card_row[5])
            or (
                candidate.version_id,
                candidate.logical_id,
                candidate.revision,
                candidate.status,
                candidate.content_digest,
            )
            != tuple(card_row[:5])
        ):
            raise ReviewProjectionError("review card envelope is inconsistent")
        card = candidate
    excerpts: list[ReviewContentExcerpt] = []
    node_total = 0
    citations: tuple[ReviewCitationExcerpt, ...] = ()
    citation_total = 0
    if card is not None:
        stack = [
            (node, (index,))
            for index, node in reversed(tuple(enumerate(card.content_ast)))
        ]
        while stack:
            node, path = stack.pop()
            node_total += 1
            if len(excerpts) < 50:
                excerpts.append(
                    ReviewContentExcerpt(
                        path=path[:32],
                        depth=len(path),
                        node_type=node.type,
                        text=None if node.text is None else node.text[:2000],
                        level=node.level,
                        language=None if node.language is None else node.language[:64],
                        rows=tuple(
                            tuple(str(cell)[:500] for cell in row[:5])
                            for row in node.rows[:5]
                        ),
                    )
                )
            stack.extend(
                (child, (*path, index))
                for index, child in reversed(tuple(enumerate(node.children)))
            )
        citation_total = len(card.chunk_citations)
        citations = tuple(
            ReviewCitationExcerpt(
                chunk_id=citation.chunk_id,
                source_version_id=citation.source_version_id,
                quoted_text=(
                    None
                    if citation.quoted_text is None
                    else citation.quoted_text[:1000]
                ),
            )
            for citation in card.chunk_citations[:50]
        )
    evidence_ids = tuple(task.evidence_ids[:50])
    return ReviewDetail(
        task=item,
        evidence_ids=evidence_ids,
        evidence_total=len(task.evidence_ids),
        evidence_truncated=len(task.evidence_ids) > len(evidence_ids),
        card_version_id=None if card is None else card.version_id,
        card_content_digest=None if card is None else card.content_digest,
        card_title=None if card is None else card.title[:500],
        learning_objective=(
            None if card is None else card.learning_objective[:1000]
        ),
        content_nodes=tuple(excerpts),
        content_node_total=node_total,
        content_nodes_truncated=node_total > len(excerpts),
        citations=citations,
        citation_total=citation_total,
        citations_truncated=citation_total > len(citations),
    )


def list_upgrade_suggestions(
    catalog: KnowledgeCatalog,
    *,
    status: Literal["open", "resolved", "dismissed"] | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> UpgradeListPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ReviewQueryError("upgrade limit is invalid")
    if status not in {None, "open", "resolved", "dismissed"}:
        raise ReviewQueryError("upgrade status is invalid")
    after: str | None = None
    if cursor is not None:
        match = re.fullmatch(r"upgrade-cursor-([0-9a-f]{32})", cursor)
        if match is None:
            raise ReviewQueryError("upgrade cursor is invalid")
        row = catalog.connection.execute(
            "SELECT suggestion_id FROM upgrade_suggestions "
            "WHERE substr(sha256_hex(suggestion_id), 1, 32) = ?",
            (match.group(1),),
        ).fetchone()
        if row is None:
            raise ReviewQueryError("upgrade cursor is invalid")
        after = str(row[0])
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("current.current_status = ?")
        params.append(status)
    if after is not None:
        clauses.append("suggestion.suggestion_id > ?")
        params.append(after)
    query = (
        "SELECT suggestion.suggestion_id, suggestion.payload_json, "
        "suggestion.content_digest FROM upgrade_suggestions suggestion "
        "JOIN review_task_current current "
        "ON current.task_id = suggestion.review_task_id"
    )
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY suggestion.suggestion_id LIMIT ?"
    params.append(limit + 1)
    rows = catalog.connection.execute(query, tuple(params)).fetchall()
    items: list[UpgradeListItem] = []
    for suggestion_id, payload, digest in rows[:limit]:
        try:
            suggestion = UpgradeSuggestion.model_validate_json(str(payload), strict=False)
            stored = _suggestion_existing_or_conflict(
                catalog,
                table="upgrade_suggestions",
                suggestion_id=str(suggestion_id),
                payload=str(payload),
                digest=str(digest),
                model_type=UpgradeSuggestion,
                evidence_table="upgrade_suggestion_evidence",
            )
            if not isinstance(stored, UpgradeSuggestion):
                raise CatalogReferenceError("upgrade suggestion is unavailable")
            _require_upgrade_binding(catalog, stored)
            review, _task = _review_list_item(catalog, stored.review_task_id)
            candidate_descriptor = _upgrade_version_descriptor(
                catalog, stored.candidate_version_id
            )
            if candidate_descriptor is None:
                raise CatalogReferenceError("upgrade candidate is unavailable")
        except (CatalogReferenceError, ImmutableVersionConflict, ValueError) as error:
            raise ReviewProjectionError("upgrade suggestion is inconsistent") from error
        items.append(
            UpgradeListItem(
                suggestion_id=stored.suggestion_id,
                current_version_id=stored.current_version_id,
                candidate_version_id=stored.candidate_version_id,
                review_task_id=stored.review_task_id,
                reason_code=stored.reason_code,
                status=review.status,
                suggestion_digest=str(digest),
                review_digest=review.review_digest,
                candidate_digest=candidate_descriptor[4],
                evidence_count=len(stored.evidence_ids),
                created_at=stored.created_at,
            )
        )
    return UpgradeListPage(
        items=tuple(items),
        next_cursor=(
            _opaque_cursor("upgrade", items[-1].suggestion_id)
            if len(rows) > limit and items
            else None
        ),
    )


def rebuild_review_task_projection(connection: sqlite3.Connection) -> None:
    """Rebuild category/reason/status only from immutable raw task/resolution facts."""

    savepoint = "review_projection_rebuild"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        tasks: list[tuple[ReviewTask, str]] = []
        for task_id, kind, status, payload_json in connection.execute(
            "SELECT task_id, kind, status, payload_json FROM review_tasks ORDER BY task_id"
        ).fetchall():
            try:
                task = ReviewTask.model_validate_json(payload_json, strict=False)
            except Exception as error:
                raise ReviewProjectionError("review task raw bytes are invalid") from error
            if (
                task.task_id != task_id
                or task.kind != kind
                or task.status != status
                or canonical_model_json(task) != payload_json
                or kind not in _REVIEW_MAPPING
            ):
                raise ReviewProjectionError("review task raw columns are inconsistent")
            tasks.append((task, payload_json))
        resolutions: dict[str, ReviewResolution] = {}
        for (
            resolution_id,
            task_id,
            decision,
            expected_review_digest,
            content_digest,
            payload_json,
            created_at,
        ) in connection.execute(
            "SELECT resolution_id, task_id, decision, expected_review_digest, "
            "content_digest, payload_json, created_at "
            "FROM review_resolutions ORDER BY resolution_id"
        ).fetchall():
            try:
                resolution = ReviewResolution.model_validate_json(
                    payload_json, strict=False
                )
                raw_resolved_at = _payload_timestamp(payload_json, "resolved_at")
            except Exception as error:
                raise ReviewProjectionError("review resolution bytes are invalid") from error
            if (
                resolution.resolution_id != resolution_id
                or resolution.task_id != task_id
                or resolution.decision != decision
                or resolution.expected_review_digest != expected_review_digest
                or raw_resolved_at != created_at
                or canonical_model_json(resolution) != payload_json
                or _payload_digest(payload_json) != content_digest
                or task_id in resolutions
            ):
                raise ReviewProjectionError("review resolution raw facts are inconsistent")
            joined_evidence = {
                evidence_id
                for (evidence_id,) in connection.execute(
                    "SELECT evidence_id FROM review_resolution_evidence "
                    "WHERE resolution_id = ?",
                    (resolution_id,),
                ).fetchall()
            }
            if joined_evidence != set(resolution.evidence_ids):
                raise ReviewProjectionError(
                    "review resolution evidence links do not match raw bytes"
                )
            resolutions[task_id] = resolution
        connection.execute("DELETE FROM review_task_current")
        for task, payload_json in tasks:
            raw_digest = _payload_digest(payload_json)
            category, reason_code = _REVIEW_MAPPING[task.kind]
            resolution = resolutions.get(task.task_id)
            if resolution is not None and resolution.expected_review_digest != raw_digest:
                raise ReviewProjectionError("review resolution digest is stale")
            if resolution is None:
                current_status = task.status
                resolution_id = None
            else:
                current_status = (
                    "dismissed" if resolution.decision == "dismiss" else "resolved"
                )
                resolution_id = resolution.resolution_id
            connection.execute(
                "INSERT INTO review_task_current("
                "task_id, category, reason_code, review_digest, current_status, resolution_id"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task.task_id,
                    category,
                    reason_code,
                    raw_digest,
                    current_status,
                    resolution_id,
                ),
            )
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException as error:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        if isinstance(error, ReviewProjectionError):
            raise
        raise ReviewProjectionError("review projection rebuild failed") from error


__all__ = [
    "CourseFeedbackSuggestion",
    "ReviewContentExcerpt",
    "ReviewCitationExcerpt",
    "ReviewDetail",
    "ReviewListItem",
    "ReviewListPage",
    "ReviewNotFoundError",
    "ReviewProjectionError",
    "ReviewQueryError",
    "ReviewResolution",
    "UpgradeSuggestion",
    "UpgradeListItem",
    "UpgradeListPage",
    "get_review_detail",
    "list_review_tasks",
    "list_upgrade_suggestions",
    "rebuild_review_task_projection",
    "register_feedback_suggestion",
    "register_upgrade_suggestion",
    "resolve_review_task",
]
