"""Evidence and version-lineage contracts."""

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
)

from course_helper.domain.common import ImmutableJsonValue, SourceLocator, freeze_json, thaw_json


class EvidenceCheck(BaseModel):
    """One machine-readable verification performed by a helper workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    code: str = Field(min_length=1)
    status: Literal["passed", "warning", "failed", "skipped"]
    message: str = Field(min_length=1)
    details: Mapping[str, ImmutableJsonValue] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def freeze_details(cls, value: Mapping[str, ImmutableJsonValue]) -> Mapping[str, ImmutableJsonValue]:
        return cast(Mapping[str, ImmutableJsonValue], freeze_json(value))

    @field_serializer("details", mode="wrap")
    def serialize_details(
        self,
        value: Mapping[str, ImmutableJsonValue],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, ImmutableJsonValue]:
        return cast(dict[str, ImmutableJsonValue], handler(thaw_json(value)))


class EvidenceError(BaseModel):
    """Sanitized structured failure information."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    details: Mapping[str, ImmutableJsonValue] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def freeze_details(cls, value: Mapping[str, ImmutableJsonValue]) -> Mapping[str, ImmutableJsonValue]:
        return cast(Mapping[str, ImmutableJsonValue], freeze_json(value))

    @field_serializer("details", mode="wrap")
    def serialize_details(
        self,
        value: Mapping[str, ImmutableJsonValue],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, ImmutableJsonValue]:
        return cast(dict[str, ImmutableJsonValue], handler(thaw_json(value)))


class EvidenceArtifact(BaseModel):
    """Reference to a generated artifact without embedding its bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: str = Field(min_length=1)
    locator: SourceLocator
    media_type: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)


class EvidenceObject(BaseModel):
    """Verifiable output receipt for extraction, retrieval, or publication."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    evidence_id: str = Field(min_length=1)
    kind: Literal[
        "extraction",
        "retrieval",
        "dedup",
        "composition",
        "validation",
        "publish",
        "rehearsal",
        "dataset-profile",
        "execution",
        "runtime",
    ]
    subject_version_id: str | None = None
    status: Literal["verified", "warning", "failed", "degraded"]
    input_summary: Mapping[str, ImmutableJsonValue] = Field(default_factory=dict)
    output_summary: Mapping[str, ImmutableJsonValue] = Field(default_factory=dict)
    producer: str = Field(min_length=1)
    producer_version: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    checks: tuple[EvidenceCheck, ...] = ()
    errors: tuple[EvidenceError, ...] = ()
    artifacts: tuple[EvidenceArtifact, ...] = ()

    @field_validator("input_summary", "output_summary")
    @classmethod
    def freeze_summaries(cls, value: Mapping[str, ImmutableJsonValue]) -> Mapping[str, ImmutableJsonValue]:
        return cast(Mapping[str, ImmutableJsonValue], freeze_json(value))

    @field_serializer("input_summary", "output_summary", mode="wrap")
    def serialize_summaries(
        self,
        value: Mapping[str, ImmutableJsonValue],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, ImmutableJsonValue]:
        return cast(dict[str, ImmutableJsonValue], handler(thaw_json(value)))


class LineageEdge(BaseModel):
    """Evidence-backed relationship between two concrete artifact versions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    edge_id: str = Field(min_length=1)
    from_version_id: str = Field(min_length=1)
    to_version_id: str = Field(min_length=1)
    relation: Literal[
        "extracted_from",
        "derived_from",
        "cites",
        "uses",
        "supersedes",
        "deduplicates",
        "composed_into",
    ]
    evidence_id: str = Field(min_length=1)
    created_at: datetime
