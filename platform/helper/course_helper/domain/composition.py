"""Immutable contracts for grounded course requirements and composition."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from course_helper.domain.common import OpaqueId, VersionMeta


_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_JSON_ADAPTER = TypeAdapter(Any)

UsageScope = Literal["private-training", "internal", "public"]


def canonical_json(value: BaseModel | Mapping[str, Any] | Sequence[Any]) -> str:
    """Return deterministic UTF-8 JSON text for an immutable domain payload."""

    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", by_alias=False, exclude_none=True)
    else:
        payload = _JSON_ADAPTER.dump_python(value, mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_digest(value: BaseModel | Mapping[str, Any] | Sequence[Any]) -> str:
    """Hash a canonical domain payload without depending on input key order."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def course_outline_semantic_payload(outline: "CourseOutline") -> dict[str, Any]:
    """Return every outline semantic while excluding version/lifecycle metadata.

    ``content_digest`` is intentionally excluded from its own preimage, as are
    the immutable-version envelope fields.  Chapter and placement payloads are
    included in full so allocation, lesson, purpose, and ordering edits always
    invalidate confirmation bytes.
    """

    return {
        "requirement_id": outline.requirement_id,
        "chapters": tuple(
            chapter.model_dump(mode="json", by_alias=False, exclude_none=True)
            for chapter in outline.chapters
        ),
        "uncovered_goals": outline.uncovered_goals,
        "retrieval_evidence_id": outline.retrieval_evidence_id,
        "index_snapshot_id": outline.index_snapshot_id,
    }


def course_outline_content_digest(outline: "CourseOutline") -> str:
    """Compute the canonical digest for one exact outline semantic payload."""

    return canonical_digest(course_outline_semantic_payload(outline))


def course_version_semantic_payload(course: "CourseVersion") -> dict[str, Any]:
    """Return the immutable course snapshot semantics without version metadata."""

    return {
        "requirement_id": course.requirement_id,
        "outline_version_id": course.outline_version_id,
        "outline_digest": course.outline_digest,
        "placement_ids": course.placement_ids,
        "visual_placement_ids": course.visual_placement_ids,
        "usage_scope": course.usage_scope,
        "confirmation_digest": course.confirmation_digest,
        "status": course.status,
    }


def course_version_content_digest(course: "CourseVersion") -> str:
    """Bind a published course version to its exact outline and visual snapshot."""

    return canonical_digest(course_version_semantic_payload(course))


def _require_unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return values


def _require_clean_text(value: str, *, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if value != value.strip():
        raise ValueError(f"{label} must not have surrounding whitespace")
    return value


class CourseRequirement(BaseModel):
    """A bounded request that can be composed without passing raw prompts onward."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    requirement_id: str = Field(pattern=_SAFE_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=500)
    learning_goals: tuple[str, ...] = Field(min_length=1, max_length=20)
    duration_minutes: int = Field(ge=5, le=480)
    required_tag_ids: tuple[str, ...] = Field(default=(), max_length=50)
    excluded_tag_ids: tuple[str, ...] = Field(default=(), max_length=50)
    usage_scope: UsageScope

    @field_validator("title", "audience")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        return _require_clean_text(value, label=info.field_name)

    @field_validator("learning_goals")
    @classmethod
    def valid_goals(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for goal in value:
            _require_clean_text(goal, label="learning goal")
            if len(goal) > 500:
                raise ValueError("learning goals are limited to 500 characters")
        return _require_unique(value, label="learning goals")

    @field_validator("required_tag_ids", "excluded_tag_ids")
    @classmethod
    def unique_tags(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        for tag_id in value:
            if not tag_id or len(tag_id) > 128 or "/" in tag_id or "\\" in tag_id:
                raise ValueError(f"{info.field_name} contains an invalid tag ID")
        return _require_unique(value, label=info.field_name)

    @field_validator("duration_minutes")
    @classmethod
    def five_minute_duration(cls, value: int) -> int:
        if value % 5:
            raise ValueError("duration must use five-minute increments")
        return value

    @model_validator(mode="after")
    def tags_are_disjoint(self) -> CourseRequirement:
        if set(self.required_tag_ids) & set(self.excluded_tag_ids):
            raise ValueError("required and excluded tags must be disjoint")
        return self


class CardPlacement(BaseModel):
    """One immutable use of a published card version inside an outline."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    placement_id: str = Field(pattern=_SAFE_ID_PATTERN)
    card_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    chapter_id: str = Field(pattern=_SAFE_ID_PATTERN)
    lesson_id: str = Field(pattern=_SAFE_ID_PATTERN)
    purpose: Literal["core", "example", "exercise", "evidence", "warning"]
    allocated_minutes: int = Field(ge=5, le=480)

    @field_validator("allocated_minutes")
    @classmethod
    def five_minute_allocation(cls, value: int) -> int:
        if value % 5:
            raise ValueError("allocated minutes must use five-minute increments")
        return value


class CourseOutlineChapter(BaseModel):
    """One ordered chapter in an adjustable outline."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    chapter_id: str = Field(pattern=_SAFE_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=500)
    placements: tuple[CardPlacement, ...] = Field(default=(), max_length=100)

    @field_validator("title", "objective")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        return _require_clean_text(value, label=info.field_name)

    @model_validator(mode="after")
    def placements_belong_to_chapter(self) -> CourseOutlineChapter:
        if any(placement.chapter_id != self.chapter_id for placement in self.placements):
            raise ValueError("every placement chapter ID must match its chapter")
        _require_unique(tuple(item.placement_id for item in self.placements), label="placement IDs")
        signatures = tuple(
            (item.card_version_id, item.lesson_id, item.purpose) for item in self.placements
        )
        if len(set(signatures)) != len(signatures):
            raise ValueError("duplicate card placements are not allowed")
        return self


class CourseOutline(VersionMeta):
    """A version-pinned, adjustable outline with explicit uncovered goals."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    requirement_id: str = Field(pattern=_SAFE_ID_PATTERN)
    chapters: tuple[CourseOutlineChapter, ...] = Field(min_length=1, max_length=50)
    uncovered_goals: tuple[str, ...] = Field(default=(), max_length=20)
    retrieval_evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    index_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)

    @field_validator("uncovered_goals")
    @classmethod
    def valid_uncovered_goals(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for goal in value:
            _require_clean_text(goal, label="uncovered goal")
        return _require_unique(value, label="uncovered goals")

    @model_validator(mode="after")
    def unique_outline_ids(self) -> CourseOutline:
        _require_unique(tuple(chapter.chapter_id for chapter in self.chapters), label="chapter IDs")
        placements = tuple(
            placement for chapter in self.chapters for placement in chapter.placements
        )
        _require_unique(tuple(item.placement_id for item in placements), label="placement IDs")
        return self


class CourseVersion(VersionMeta):
    """A digest-bound confirmation or publication of one exact outline."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    requirement_id: str = Field(pattern=_SAFE_ID_PATTERN)
    outline_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    outline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    placement_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=500)
    visual_placement_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=500)
    usage_scope: UsageScope
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["confirmed", "published", "archived"]

    @field_validator("placement_ids", "visual_placement_ids")
    @classmethod
    def unique_placement_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique(value, label="placement IDs")


__all__ = [
    "CardPlacement",
    "CourseOutline",
    "CourseOutlineChapter",
    "CourseRequirement",
    "CourseVersion",
    "UsageScope",
    "canonical_digest",
    "canonical_json",
    "course_outline_content_digest",
    "course_outline_semantic_payload",
    "course_version_content_digest",
    "course_version_semantic_payload",
]
