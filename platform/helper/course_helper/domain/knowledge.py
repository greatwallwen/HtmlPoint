"""Controlled vocabulary, knowledge-card, and review contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_serializer,
    field_validator,
    model_validator,
)

from course_helper.domain.common import ActorRef, OpaqueId, VersionMeta, freeze_json, thaw_json


ReviewTaskKind = Literal[
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
]
ReviewTaskCategory = Literal[
    "candidate-card",
    "exact-duplicate",
    "near-duplicate",
    "tag",
    "source-changed",
    "course-feedback",
    "visual-rights",
]
ReviewReasonCode = ReviewTaskKind

_REVIEW_KIND_MAPPING: dict[ReviewTaskKind, tuple[ReviewTaskCategory, ReviewReasonCode]] = {
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


class TagValue(BaseModel):
    """One value whose identity is scoped to a vocabulary version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1)
    labels: Mapping[str, str]
    aliases: tuple[str, ...] = ()
    status: Literal["active", "deprecated"]
    replaced_by: str | None = None

    @field_validator("labels")
    @classmethod
    def freeze_labels(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return cast(Mapping[str, str], freeze_json(value))

    @field_serializer("labels", mode="wrap")
    def serialize_labels(
        self,
        value: Mapping[str, str],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, str]:
        return cast(dict[str, str], handler(thaw_json(value)))


class TagDimension(BaseModel):
    """Controlled tag dimension and its cardinality rule."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1)
    cardinality: Literal["one", "many"]
    values: tuple[TagValue, ...]

class TagVocabularyVersion(VersionMeta):
    """Immutable, versioned controlled vocabulary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dimensions: tuple[TagDimension, ...]

class TagAssignment(BaseModel):
    """Reference to a tag value in a pinned vocabulary version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    vocabulary_version_id: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    tag_id: str = Field(min_length=1)
    assigned_by: Literal["human", "model", "rule"] = "human"
    confidence: float | None = Field(default=None, ge=0, le=1)


class CardContentNode(BaseModel):
    """Render-neutral node in a knowledge card content AST."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal[
        "paragraph",
        "heading",
        "list",
        "list-item",
        "code",
        "quote",
        "callout",
        "table",
        "image",
        "dataset-activity",
    ]
    text: str | None = None
    level: int | None = Field(default=None, ge=1, le=6)
    language: str | None = None
    rows: tuple[tuple[str, ...], ...] = ()
    children: tuple[CardContentNode, ...] = ()


class ChunkCitation(BaseModel):
    """Citation to an extracted chunk and its owning source version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    chunk_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    quoted_text: str | None = None


class VisualReference(BaseModel):
    """Version-pinned visual reference used by a card."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    visual_version_id: str = Field(min_length=1)
    purpose: Literal["hero", "illustration", "evidence", "background", "thumbnail"] = "illustration"
    alt_text: str | None = None


class DatasetReference(BaseModel):
    """Version-pinned dataset reference with allowlisted activities."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dataset_version_id: str = Field(min_length=1)
    activity_spec_ids: tuple[str, ...] = ()


class KnowledgeCardVersion(VersionMeta):
    """Immutable structured knowledge content suitable for governed publish."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    main_type_id: Literal[
        "concept",
        "procedure",
        "example",
        "case",
        "exercise",
        "assessment",
        "evidence",
        "misconception",
        "warning",
    ]
    title: str = Field(min_length=1)
    learning_objective: str = Field(min_length=1)
    content_ast: tuple[CardContentNode, ...]
    suggested_minutes: int = Field(ge=1)
    prerequisite_card_version_ids: tuple[str, ...] = ()
    vocabulary_version_id: str = Field(min_length=1)
    tag_assignments: tuple[TagAssignment, ...] = ()
    chunk_citations: tuple[ChunkCitation, ...] = ()
    visual_refs: tuple[VisualReference, ...] = ()
    dataset_refs: tuple[DatasetReference, ...] = ()
    status: Literal["draft", "review", "published", "superseded", "archived"]

    @model_validator(mode="after")
    def published_source_backed_cards_require_citations(self) -> KnowledgeCardVersion:
        citation_required_types = {
            "concept",
            "procedure",
            "example",
            "case",
            "evidence",
            "misconception",
            "warning",
        }
        if self.status == "published" and self.main_type_id in citation_required_types and not self.chunk_citations:
            raise ValueError("published source-backed cards require at least one chunk citation")
        return self


class ReviewTask(BaseModel):
    """Typed governance task that may block publication."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: OpaqueId
    kind: ReviewTaskKind
    subject_version_id: OpaqueId
    status: Literal["open", "resolved", "dismissed"]
    blocking: bool
    evidence_ids: tuple[OpaqueId, ...] = ()
    created_at: datetime
    created_by: ActorRef
    resolved_at: datetime | None = None
    resolved_by: ActorRef | None = None

    @property
    def category(self) -> ReviewTaskCategory:
        """Rebuildable category projection without changing schema-v1 bytes."""

        return _REVIEW_KIND_MAPPING[self.kind][0]

    @property
    def reason_code(self) -> ReviewReasonCode:
        """Specific machine reason retained by the compatibility projection."""

        return _REVIEW_KIND_MAPPING[self.kind][1]
