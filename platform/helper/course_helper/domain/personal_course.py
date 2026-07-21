"""Strict contracts for one resumable personal course creation run."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from course_helper.domain.common import ActorRef, OpaqueId


PersonalCourseStatus = Literal[
    "queued",
    "importing",
    "organizing_knowledge",
    "composing",
    "assigning_visuals",
    "validating",
    "needs_attention",
    "ready",
    "failed",
]
AttentionKind = Literal[
    "source-read",
    "knowledge-conflict",
    "knowledge-review",
    "visual-license",
    "course-validation",
]
AttentionAction = Literal[
    "retry",
    "exclude-source",
    "approve",
    "reject",
    "use-source-visual",
    "use-network-visual",
    "continue-without-visual",
]

_RUN_ID_PATTERN = r"^personal-run-[0-9a-f]{32}$"
_REQUEST_ID_PATTERN = r"^personal-request-[0-9a-f]{32}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"importing", "failed"}),
    "importing": frozenset({"organizing_knowledge", "needs_attention", "failed"}),
    "organizing_knowledge": frozenset({"composing", "needs_attention", "failed"}),
    "composing": frozenset({"assigning_visuals", "needs_attention", "failed"}),
    "assigning_visuals": frozenset({"validating", "needs_attention", "failed"}),
    "validating": frozenset({"ready", "needs_attention", "failed"}),
    "needs_attention": frozenset(
        {
            "importing",
            "organizing_knowledge",
            "composing",
            "assigning_visuals",
            "validating",
            "failed",
        }
    ),
    "ready": frozenset(),
    "failed": frozenset(),
}


def _aware(value: datetime, *, label: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _clean(value: str, *, label: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be non-blank without surrounding whitespace")
    return value


class PersonalCourseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=4000)
    source_version_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=200)
    title_hint: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: datetime
    requested_by: ActorRef

    @field_validator("prompt", "title_hint")
    @classmethod
    def clean_human_text(cls, value: str | None, info: Any) -> str | None:
        return value if value is None else _clean(value, label=info.field_name)

    @field_validator("source_version_ids")
    @classmethod
    def unique_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("source_version_ids must be unique")
        return value

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        return _aware(value, label="created_at")


class AttentionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    attention_id: OpaqueId
    kind: AttentionKind
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=1000)
    allowed_actions: tuple[AttentionAction, ...] = Field(min_length=1, max_length=7)
    recommended_action: AttentionAction

    @field_validator("title", "message")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        return _clean(value, label=info.field_name)

    @field_validator("allowed_actions")
    @classmethod
    def unique_actions(
        cls, value: tuple[AttentionAction, ...]
    ) -> tuple[AttentionAction, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_actions must be unique")
        return value

    @model_validator(mode="after")
    def recommendation_is_allowed(self) -> AttentionItem:
        if self.recommended_action not in self.allowed_actions:
            raise ValueError("recommended_action must be one of allowed_actions")
        return self


class AttentionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    bundle_id: OpaqueId
    created_at: datetime
    items: tuple[AttentionItem, ...] = Field(min_length=1, max_length=20)

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        return _aware(value, label="created_at")

    @field_validator("items")
    @classmethod
    def unique_items(cls, value: tuple[AttentionItem, ...]) -> tuple[AttentionItem, ...]:
        if len({item.attention_id for item in value}) != len(value):
            raise ValueError("attention item IDs must be unique")
        return value


class PersonalCourseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=200)
    course_version_id: OpaqueId
    slide_deck_version_id: OpaqueId
    runtime_manifest_version_id: OpaqueId
    chapter_count: int = Field(ge=1, le=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return _clean(value, label="title")


class PersonalCourseRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    request: PersonalCourseRequest
    request_digest: str = Field(pattern=_SHA256_PATTERN)
    source_snapshot_digest: str = Field(pattern=_SHA256_PATTERN)
    status: PersonalCourseStatus
    revision: int = Field(ge=1)
    phase_evidence_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=100)
    attention_bundle: AttentionBundle | None = None
    result: PersonalCourseResult | None = None
    failure_message: str | None = Field(default=None, min_length=1, max_length=1000)
    created_at: datetime
    updated_at: datetime

    @field_validator("phase_evidence_ids")
    @classmethod
    def unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("phase_evidence_ids must be unique")
        return value

    @field_validator("failure_message")
    @classmethod
    def clean_failure(cls, value: str | None) -> str | None:
        return value if value is None else _clean(value, label="failure_message")

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime, info: Any) -> datetime:
        return _aware(value, label=info.field_name)

    @model_validator(mode="after")
    def consistent_state(self) -> PersonalCourseRun:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot predate created_at")
        if (self.status == "needs_attention") != (self.attention_bundle is not None):
            raise ValueError("attention_bundle must exist only while needs_attention")
        if (self.status == "ready") != (self.result is not None):
            raise ValueError("result must exist only while ready")
        if (self.status == "failed") != (self.failure_message is not None):
            raise ValueError("failure_message must exist only while failed")
        return self

    def advance(
        self,
        next_status: PersonalCourseStatus,
        *,
        evidence_id: OpaqueId,
        updated_at: datetime | None = None,
        attention_bundle: AttentionBundle | None = None,
        result: PersonalCourseResult | None = None,
        failure_message: str | None = None,
    ) -> PersonalCourseRun:
        if next_status not in _TRANSITIONS[self.status]:
            raise ValueError(f"invalid personal course transition: {self.status} -> {next_status}")
        timestamp = updated_at or datetime.now(timezone.utc)
        _aware(timestamp, label="updated_at")
        if timestamp < self.updated_at:
            raise ValueError("updated_at cannot move backwards")
        values = self.model_dump(mode="python")
        values.update(
            {
                "status": next_status,
                "revision": self.revision + 1,
                "phase_evidence_ids": (*self.phase_evidence_ids, evidence_id),
                "attention_bundle": attention_bundle,
                "result": result,
                "failure_message": failure_message,
                "updated_at": timestamp,
            }
        )
        return PersonalCourseRun.model_validate(values)


__all__ = [
    "AttentionAction",
    "AttentionBundle",
    "AttentionItem",
    "AttentionKind",
    "PersonalCourseRequest",
    "PersonalCourseResult",
    "PersonalCourseRun",
    "PersonalCourseStatus",
]
