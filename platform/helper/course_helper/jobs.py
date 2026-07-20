"""Typed, allowlisted helper jobs and their bounded execution runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import queue
import re
import time
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from course_helper.catalog import (
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    OutlineConfirmation,
    canonical_model_json,
)
from course_helper.artifacts import ArtifactStore
from course_helper.chart_builder import (
    ChartSpec,
    build_dataset_charts,
    dataset_column_digest,
    dataset_schema_digest,
)
from course_helper.cards import PublishBlocked, publish_card, publish_card_in_operation
from course_helper.composer import (
    CompositionError,
    CompositionOptions,
    CompositionResult,
    confirmation_summary,
    prepare_authoritative_composition,
    register_prepared_composition,
)
from course_helper.domain.common import ActorRef, ImmutableJsonValue, SourceLocator
from course_helper.domain.composition import (
    CourseRequirement,
    CourseVersion,
    course_version_content_digest,
)
from course_helper.domain.evidence import EvidenceCheck, EvidenceError, EvidenceObject
from course_helper.domain.projection import ProjectionCommand
from course_helper.domain.visual_policy import (
    AttributionBlock,
    CropRect,
    TransformationManifest,
    VisualPlacement,
)
from course_helper.domain.knowledge import KnowledgeCardVersion
from course_helper.domain.sources import VisualAssetVersion
from course_helper.import_pipeline import (
    parse_promoted_source,
    persist_governed_dataset,
    persist_governed_import,
    profile_promoted_dataset,
)
from course_helper.index_outbox import (
    IndexLeaseConflict,
    IndexSnapshotIntegrityError,
    claim_next_index_outbox,
    complete_index_claim,
)
from course_helper.network_visuals import (
    NetworkVisualAcquisition,
    NetworkVisualError,
    PinnedHttpsTransport,
    WikimediaApiClient,
    acquire_network_visuals,
    current_network_visual_verification,
    discover_network_visuals,
    revalidate_network_visual,
)
from course_helper.source_visuals import (
    SourceVisualMaterialization,
    materialize_source_visuals,
)
from course_helper.parsers.dataset_profiler import DatasetProfiler
from course_helper.parsers.markdown_parser import MarkdownParser
from course_helper.parsers.pptx_parser import PptxParser
from course_helper.retrieval import (
    KnowledgeRetriever,
    RetrievalFailure,
    RetrievalQuery,
    RetrievalQueryError,
)
from course_helper.reviews import (
    ReviewResolution,
    ReviewNotFoundError,
    ReviewProjectionError,
    ReviewQueryError,
    get_review_detail,
    list_review_tasks,
    list_upgrade_suggestions,
    resolve_review_task,
)
from course_helper.slide_builder import (
    SlideBuildError,
    build_and_register_draft,
    course_publication_request_digest,
    publish_course_version,
    validate_course_version,
)
from course_helper.operations import (
    IndexOutboxItem,
    OperationAuthenticationError,
    OperationConflict,
    OperationIntegrityError,
    OperationItemOutcome,
    OperationOutcome,
    OperationMutationResult,
    OperationRequest,
    operation_status,
    run_operation,
)
from course_helper.upgrades import resolve_upgrade_suggestion
from course_helper.source_roots import SourceRootRegistry, SourceRootViolation
from course_helper.uploads import (
    UploadError,
    UploadStore,
    import_cancel_request_digest,
    import_promotion_request_digest,
    import_start_request_digest,
)


def _lower_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class HttpRequestModel(BaseModel):
    """Strict JSON boundary shared by every helper request model."""

    model_config = ConfigDict(
        alias_generator=_lower_camel,
        extra="forbid",
        validate_default=True,
    )


class HttpSourceLocator(HttpRequestModel):
    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        return SourceLocator(root_id="http-boundary", relative_path=value).relative_path


class SourceSelection(HttpRequestModel):
    slide_numbers: tuple[int, ...] = Field(default=(), max_length=64)
    heading_selectors: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("slide_numbers")
    @classmethod
    def valid_slide_numbers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(type(number) is not int or number < 1 for number in value):
            raise ValueError("slide numbers must be positive integers")
        if len(set(value)) != len(value):
            raise ValueError("slide numbers must be unique")
        return value

    @field_validator("heading_selectors")
    @classmethod
    def valid_heading_selectors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not selector.strip() for selector in value):
            raise ValueError("heading selectors must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("heading selectors must be unique")
        return value

    @model_validator(mode="after")
    def one_selection_mode(self) -> SourceSelection:
        if self.slide_numbers and self.heading_selectors:
            raise ValueError("slide numbers and heading selectors are mutually exclusive")
        return self


class SourceIngestJob(HttpRequestModel):
    type: Literal["source_ingest"]
    locator: HttpSourceLocator
    selection: SourceSelection


class DatasetProfileJob(HttpRequestModel):
    type: Literal["dataset_profile"]
    locator: HttpSourceLocator
    sample_limit: int = Field(default=20, ge=0, le=20)
    sheet_name: str | None = Field(default=None, min_length=1)


class KnowledgeRetrieveJob(HttpRequestModel):
    type: Literal["knowledge_retrieve"]
    query: str = Field(min_length=1, max_length=2000)
    required_tag_ids: tuple[str, ...] = Field(default=(), max_length=50)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("required_tag_ids")
    @classmethod
    def valid_required_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tag_id.strip() for tag_id in value):
            raise ValueError("required tags must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("required tags must be unique")
        return value


class KnowledgePublishJob(HttpRequestModel):
    type: Literal["knowledge_publish"]
    card_version_id: str = Field(min_length=1, max_length=256)


class HttpActor(HttpRequestModel):
    actor_type: Literal["human", "service", "model", "system"]
    actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    def as_domain(self) -> ActorRef:
        return ActorRef(actor_type=self.actor_type, actor_id=self.actor_id)


class HttpCourseRequirement(HttpRequestModel):
    requirement_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=500)
    learning_goals: tuple[str, ...] = Field(min_length=1, max_length=20)
    duration_minutes: int = Field(ge=5, le=480)
    required_tag_ids: tuple[str, ...] = Field(default=(), max_length=50)
    excluded_tag_ids: tuple[str, ...] = Field(default=(), max_length=50)
    usage_scope: Literal["private-training", "internal", "public"]

    def as_domain(self) -> CourseRequirement:
        return CourseRequirement(
            requirement_id=self.requirement_id,
            title=self.title,
            audience=self.audience,
            learning_goals=self.learning_goals,
            duration_minutes=self.duration_minutes,
            required_tag_ids=self.required_tag_ids,
            excluded_tag_ids=self.excluded_tag_ids,
            usage_scope=self.usage_scope,
        )


class HttpCompositionOptions(HttpRequestModel):
    audience_tag_id: str | None = None
    difficulty_tag_id: str | None = None
    index_snapshot_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    include_card_version_ids: tuple[str, ...] = Field(default=(), max_length=100)
    exclude_card_version_ids: tuple[str, ...] = Field(default=(), max_length=100)
    require_visual_refs: bool = False
    require_dataset_refs: bool = False

    def as_domain(self) -> CompositionOptions:
        return CompositionOptions(
            audience_tag_id=self.audience_tag_id,
            difficulty_tag_id=self.difficulty_tag_id,
            index_snapshot_id=self.index_snapshot_id,
            include_card_version_ids=self.include_card_version_ids,
            exclude_card_version_ids=self.exclude_card_version_ids,
            require_visual_refs=self.require_visual_refs,
            require_dataset_refs=self.require_dataset_refs,
        )


class HttpChartSpec(HttpRequestModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    chart_type: Literal["bar", "line", "scatter"]
    dataset_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    expected_dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    x_column: str = Field(min_length=1, max_length=128)
    x_column_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    y_column: str = Field(min_length=1, max_length=128)
    y_column_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    aggregate: Literal["count", "sum", "avg", "min", "max", "none"]
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=320)
    max_result_rows: int = Field(default=50, ge=1, le=100)

    def as_domain(self) -> ChartSpec:
        return ChartSpec.model_validate(
            self.model_dump(mode="python"), strict=True
        )


class HttpCropRect(HttpRequestModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    def as_domain(self) -> CropRect:
        return CropRect.model_validate(self.model_dump(mode="python"), strict=True)


class HttpTransformationManifest(HttpRequestModel):
    transformation_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    crop: HttpCropRect | None = None
    scale_mode: Literal["none", "contain", "cover"]
    color_adjustments: tuple[str, ...] = Field(default=(), max_length=20)
    change_notice: str | None = Field(default=None, max_length=1000)
    derivative_license_decision: Literal[
        "not-derivative",
        "same-license",
        "compatible-license",
        "prohibited",
        "requires-review",
    ]
    export_license: str | None = Field(default=None, max_length=200)
    share_alike_compatible: bool
    gfdl_compatible: bool
    no_derivatives_compatible: bool

    def as_domain(self) -> TransformationManifest:
        payload = self.model_dump(mode="python")
        if self.crop is not None:
            payload["crop"] = self.crop.as_domain()
        return TransformationManifest.model_validate(payload, strict=True)


class KnowledgeImportStartJob(HttpRequestModel):
    type: Literal["knowledge_import_start"]
    upload_id: str = Field(pattern=r"^upload-[0-9a-f]{32}$")
    expected_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: HttpActor

    @model_validator(mode="after")
    def exact_request_digest(self) -> KnowledgeImportStartJob:
        if self.request_digest != import_start_request_digest(
            self.upload_id, self.expected_content_digest
        ):
            raise ValueError("import start request digest is invalid")
        return self


class KnowledgeImportStatusJob(HttpRequestModel):
    type: Literal["knowledge_import_status"]
    import_id: str = Field(pattern=r"^import-[0-9a-f]{32}$")
    actor: HttpActor


class KnowledgeImportCancelJob(HttpRequestModel):
    type: Literal["knowledge_import_cancel"]
    import_id: str = Field(pattern=r"^import-[0-9a-f]{32}$")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: HttpActor

    @model_validator(mode="after")
    def exact_request_digest(self) -> KnowledgeImportCancelJob:
        if self.request_digest != import_cancel_request_digest(self.import_id):
            raise ValueError("import cancel request digest is invalid")
        return self


class OperationStatusJob(HttpRequestModel):
    type: Literal["operation_status"]
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    actor: HttpActor


class KnowledgeReviewListJob(HttpRequestModel):
    type: Literal["knowledge_review_list"]
    status: Literal["open", "resolved", "dismissed"] | None = None
    category: Literal[
        "candidate-card",
        "exact-duplicate",
        "near-duplicate",
        "tag",
        "source-changed",
        "course-feedback",
        "visual-rights",
    ] | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(
        default=None, pattern=r"^review-cursor-[0-9a-f]{32}$"
    )


class KnowledgeReviewDetailJob(HttpRequestModel):
    type: Literal["knowledge_review_detail"]
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class KnowledgeUpgradeListJob(HttpRequestModel):
    type: Literal["knowledge_upgrade_list"]
    status: Literal["open", "resolved", "dismissed"] | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(
        default=None, pattern=r"^upgrade-cursor-[0-9a-f]{32}$"
    )


def _knowledge_mutation_request_digest(kind: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"kind": kind, **payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def review_resolution_request_digest(
    *,
    task_id: str,
    decision: Literal["accept", "reject", "dismiss"],
    expected_review_digest: str,
    evidence_ids: tuple[str, ...],
) -> str:
    return _knowledge_mutation_request_digest(
        "knowledge_review_resolve",
        {
            "decision": decision,
            "evidenceIds": sorted(evidence_ids),
            "expectedReviewDigest": expected_review_digest,
            "taskId": task_id,
        },
    )


def card_publish_request_digest(
    *, card_version_id: str, expected_card_digest: str
) -> str:
    return _knowledge_mutation_request_digest(
        "knowledge_card_publish",
        {
            "cardVersionId": card_version_id,
            "expectedCardDigest": expected_card_digest,
        },
    )


def upgrade_resolution_request_digest(
    *,
    suggestion_id: str,
    decision: Literal["accept", "reject", "dismiss"],
    expected_suggestion_digest: str,
    expected_review_digest: str,
    expected_card_digest: str,
    evidence_ids: tuple[str, ...],
) -> str:
    return _knowledge_mutation_request_digest(
        "knowledge_upgrade_resolve",
        {
            "decision": decision,
            "evidenceIds": sorted(evidence_ids),
            "expectedCardDigest": expected_card_digest,
            "expectedReviewDigest": expected_review_digest,
            "expectedSuggestionDigest": expected_suggestion_digest,
            "suggestionId": suggestion_id,
        },
    )


def knowledge_index_request_digest(expected_outbox_id: str) -> str:
    return _knowledge_mutation_request_digest(
        "knowledge_index", {"expectedOutboxId": expected_outbox_id}
    )


def course_compose_request_digest(
    *,
    requirement: Mapping[str, Any] | HttpCourseRequirement,
    options: Mapping[str, Any] | HttpCompositionOptions,
    outline_logical_id: str,
    outline_version_id: str,
    outline_revision: int,
) -> str:
    requirement_payload = (
        requirement.model_dump(mode="json", by_alias=True)
        if isinstance(requirement, HttpCourseRequirement)
        else dict(requirement)
    )
    options_payload = (
        options.model_dump(mode="json", by_alias=True)
        if isinstance(options, HttpCompositionOptions)
        else dict(options)
    )
    return _knowledge_mutation_request_digest(
        "course_compose",
        {
            "options": options_payload,
            "outlineLogicalId": outline_logical_id,
            "outlineRevision": outline_revision,
            "outlineVersionId": outline_version_id,
            "requirement": requirement_payload,
        },
    )


def course_outline_confirm_request_digest(
    *,
    confirmation_id: str,
    requirement_id: str,
    outline_version_id: str,
    expected_outline_digest: str,
    confirmation_digest: str,
    course_logical_id: str,
    course_version_id: str,
    course_revision: int,
) -> str:
    return _knowledge_mutation_request_digest(
        "course_outline_confirm",
        {
            "confirmationDigest": confirmation_digest,
            "confirmationId": confirmation_id,
            "courseLogicalId": course_logical_id,
            "courseRevision": course_revision,
            "courseVersionId": course_version_id,
            "expectedOutlineDigest": expected_outline_digest,
            "outlineVersionId": outline_version_id,
            "requirementId": requirement_id,
        },
    )


def chart_build_request_digest(
    specs: tuple[Mapping[str, Any] | HttpChartSpec, ...]
) -> str:
    payloads = tuple(
        item.model_dump(mode="json", by_alias=True)
        if isinstance(item, HttpChartSpec)
        else dict(item)
        for item in specs
    )
    return _knowledge_mutation_request_digest(
        "chart_build", {"specs": payloads}
    )


def visual_search_request_digest(*, query: str, limit: int) -> str:
    return _knowledge_mutation_request_digest(
        "visual_search", {"limit": limit, "query": query}
    )


def visual_acquire_request_digest(candidate_ids: tuple[str, ...]) -> str:
    return _knowledge_mutation_request_digest(
        "visual_acquire", {"candidateIds": candidate_ids}
    )


def visual_revalidate_request_digest(visual_version_id: str) -> str:
    return _knowledge_mutation_request_digest(
        "visual_revalidate", {"visualVersionId": visual_version_id}
    )


def visual_attach_request_digest(payload: Mapping[str, Any]) -> str:
    return _knowledge_mutation_request_digest("course_visual_attach", dict(payload))


def visual_detach_request_digest(
    *,
    course_version_id: str,
    expected_course_digest: str,
    placement_id: str,
    active_placement_ids: tuple[str, ...],
) -> str:
    return _knowledge_mutation_request_digest(
        "course_visual_detach",
        {
            "activePlacementIds": active_placement_ids,
            "courseVersionId": course_version_id,
            "expectedCourseDigest": expected_course_digest,
            "placementId": placement_id,
        },
    )


class _KnowledgeMutationJob(HttpRequestModel):
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: HttpActor


class KnowledgeIndexJob(_KnowledgeMutationJob):
    type: Literal["knowledge_index"]
    expected_outbox_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )

    @model_validator(mode="after")
    def exact_request_digest(self) -> KnowledgeIndexJob:
        if self.request_digest != knowledge_index_request_digest(
            self.expected_outbox_id
        ):
            raise ValueError("knowledge index request digest is invalid")
        return self


class CourseComposeJob(_KnowledgeMutationJob):
    type: Literal["course_compose"]
    requirement: HttpCourseRequirement
    options: HttpCompositionOptions
    outline_logical_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    outline_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    outline_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def exact_request_digest(self) -> CourseComposeJob:
        self.requirement.as_domain()
        self.options.as_domain()
        if self.request_digest != course_compose_request_digest(
            requirement=self.requirement,
            options=self.options,
            outline_logical_id=self.outline_logical_id,
            outline_version_id=self.outline_version_id,
            outline_revision=self.outline_revision,
        ):
            raise ValueError("course composition request digest is invalid")
        return self


class CourseOutlineConfirmJob(_KnowledgeMutationJob):
    type: Literal["course_outline_confirm"]
    confirmation_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    requirement_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    outline_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    expected_outline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    course_logical_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    course_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    course_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def exact_request_digest(self) -> CourseOutlineConfirmJob:
        if self.request_digest != course_outline_confirm_request_digest(
            confirmation_id=self.confirmation_id,
            requirement_id=self.requirement_id,
            outline_version_id=self.outline_version_id,
            expected_outline_digest=self.expected_outline_digest,
            confirmation_digest=self.confirmation_digest,
            course_logical_id=self.course_logical_id,
            course_version_id=self.course_version_id,
            course_revision=self.course_revision,
        ):
            raise ValueError("course outline confirmation request digest is invalid")
        return self


class ChartBuildJob(_KnowledgeMutationJob):
    type: Literal["chart_build"]
    specs: tuple[HttpChartSpec, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def exact_request_digest(self) -> ChartBuildJob:
        if len({item.request_id for item in self.specs}) != len(self.specs):
            raise ValueError("chart request IDs must be unique")
        for item in self.specs:
            item.as_domain()
        if self.request_digest != chart_build_request_digest(self.specs):
            raise ValueError("chart build request digest is invalid")
        return self


class VisualSearchJob(_KnowledgeMutationJob):
    type: Literal["visual_search"]
    query: str = Field(min_length=2, max_length=120)
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def exact_request_digest(self) -> VisualSearchJob:
        if " ".join(self.query.split()) != self.query or any(
            ord(char) < 32 for char in self.query
        ):
            raise ValueError("visual search query is not canonical")
        if self.request_digest != visual_search_request_digest(
            query=self.query, limit=self.limit
        ):
            raise ValueError("visual search request digest is invalid")
        return self


class VisualAcquireJob(_KnowledgeMutationJob):
    type: Literal["visual_acquire"]
    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def exact_request_digest(self) -> VisualAcquireJob:
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or any(
            re.fullmatch(r"network-candidate-[0-9a-f]{64}", item) is None
            for item in self.candidate_ids
        ):
            raise ValueError("network visual candidate IDs are invalid")
        if self.request_digest != visual_acquire_request_digest(self.candidate_ids):
            raise ValueError("visual acquisition request digest is invalid")
        return self


class VisualRevalidateJob(_KnowledgeMutationJob):
    type: Literal["visual_revalidate"]
    visual_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )

    @model_validator(mode="after")
    def exact_request_digest(self) -> VisualRevalidateJob:
        if self.request_digest != visual_revalidate_request_digest(
            self.visual_version_id
        ):
            raise ValueError("visual revalidation request digest is invalid")
        return self


class CourseVisualAttachJob(_KnowledgeMutationJob):
    type: Literal["course_visual_attach"]
    course_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    expected_course_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    placement_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    visual_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    slide_node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    slot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    fit: Literal["contain", "cover", "fill"]
    crop: HttpCropRect | None = None
    alt_text: str = Field(min_length=1, max_length=1000)
    transformation: HttpTransformationManifest
    originating_card_version_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    originating_source_version_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    originating_dataset_version_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )

    @model_validator(mode="after")
    def exact_request_digest(self) -> CourseVisualAttachJob:
        if not any(
            (
                self.originating_card_version_id,
                self.originating_source_version_id,
                self.originating_dataset_version_id,
            )
        ):
            raise ValueError("visual attachment requires typed lineage")
        crop = None if self.crop is None else self.crop.as_domain()
        transformation = self.transformation.as_domain()
        if crop != transformation.crop:
            raise ValueError("visual attachment crop is inconsistent")
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"type", "operation_id", "request_digest", "actor"},
        )
        if self.request_digest != visual_attach_request_digest(payload):
            raise ValueError("visual attachment request digest is invalid")
        return self


class CourseVisualDetachJob(_KnowledgeMutationJob):
    type: Literal["course_visual_detach"]
    course_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    expected_course_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    placement_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    active_placement_ids: tuple[str, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def exact_request_digest(self) -> CourseVisualDetachJob:
        if (
            len(set(self.active_placement_ids)) != len(self.active_placement_ids)
            or self.placement_id not in self.active_placement_ids
        ):
            raise ValueError("visual detach selection is invalid")
        if self.request_digest != visual_detach_request_digest(
            course_version_id=self.course_version_id,
            expected_course_digest=self.expected_course_digest,
            placement_id=self.placement_id,
            active_placement_ids=self.active_placement_ids,
        ):
            raise ValueError("visual detach request digest is invalid")
        return self


class _CourseProjectionJob(_KnowledgeMutationJob):
    course_version_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    expected_course_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_placement_ids: tuple[str, ...] = Field(default=(), max_length=500)

    @field_validator("visual_placement_ids")
    @classmethod
    def unique_placements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("visual placement IDs must be unique")
        return value

    def publication_digest(self) -> str:
        return course_publication_request_digest(
            confirmed_course_version_id=self.course_version_id,
            expected_course_digest=self.expected_course_digest,
            visual_placement_ids=self.visual_placement_ids,
        )


class CourseValidateJob(_CourseProjectionJob):
    type: Literal["course_validate"]

    @model_validator(mode="after")
    def exact_request_digest(self) -> CourseValidateJob:
        if self.request_digest != self.publication_digest():
            raise ValueError("course validation request digest is invalid")
        return self


class CoursePublishJob(_CourseProjectionJob):
    type: Literal["course_publish"]

    @model_validator(mode="after")
    def exact_request_digest(self) -> CoursePublishJob:
        if self.request_digest != self.publication_digest():
            raise ValueError("course publication request digest is invalid")
        return self


class _KnowledgeResolutionJob(_KnowledgeMutationJob):
    decision: Literal["accept", "reject", "dismiss"]
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("evidence_ids")
    @classmethod
    def unique_safe_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence IDs must be unique")
        if any(
            not item
            or len(item) > 128
            or not all(char.isalnum() or char in "._:-" for char in item)
            for item in value
        ):
            raise ValueError("evidence IDs must be bounded opaque IDs")
        return value


class KnowledgeReviewResolveJob(_KnowledgeResolutionJob):
    type: Literal["knowledge_review_resolve"]
    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_request_digest(self) -> KnowledgeReviewResolveJob:
        if self.request_digest != review_resolution_request_digest(
            task_id=self.task_id,
            decision=self.decision,
            expected_review_digest=self.expected_review_digest,
            evidence_ids=self.evidence_ids,
        ):
            raise ValueError("review resolution request digest is invalid")
        return self


class KnowledgeCardPublishJob(_KnowledgeMutationJob):
    type: Literal["knowledge_card_publish"]
    card_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_card_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_request_digest(self) -> KnowledgeCardPublishJob:
        if self.request_digest != card_publish_request_digest(
            card_version_id=self.card_version_id,
            expected_card_digest=self.expected_card_digest,
        ):
            raise ValueError("card publication request digest is invalid")
        return self


class KnowledgeUpgradeResolveJob(_KnowledgeResolutionJob):
    type: Literal["knowledge_upgrade_resolve"]
    suggestion_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_suggestion_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_card_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_request_digest(self) -> KnowledgeUpgradeResolveJob:
        if self.request_digest != upgrade_resolution_request_digest(
            suggestion_id=self.suggestion_id,
            decision=self.decision,
            expected_suggestion_digest=self.expected_suggestion_digest,
            expected_review_digest=self.expected_review_digest,
            expected_card_digest=self.expected_card_digest,
            evidence_ids=self.evidence_ids,
        ):
            raise ValueError("upgrade resolution request digest is invalid")
        return self


class _EmptyProjectionPayload(HttpRequestModel):
    pass


class _OpenProjectionPayload(HttpRequestModel):
    course_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    slide_deck_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    runtime_manifest_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class _AssignProjectionPayload(HttpRequestModel):
    swap: bool = Field(default=False, strict=True)


class _ProjectionJobBase(HttpRequestModel):
    command_id: UUID
    expected_generation: int = Field(strict=True, ge=0, le=2_147_483_647)


class ProjectionDetectDisplaysJob(_ProjectionJobBase):
    type: Literal["projection_detect_displays"]
    session_id: None = None
    payload: _EmptyProjectionPayload


class ProjectionOpenSessionJob(_ProjectionJobBase):
    type: Literal["projection_open_session"]
    session_id: UUID
    payload: _OpenProjectionPayload


class ProjectionAssignWindowJob(_ProjectionJobBase):
    type: Literal["projection_assign_window"]
    session_id: UUID
    payload: _AssignProjectionPayload


class ProjectionEnterFullscreenJob(_ProjectionJobBase):
    type: Literal["projection_enter_fullscreen"]
    session_id: UUID
    payload: _EmptyProjectionPayload


class ProjectionVerifyAssignmentJob(_ProjectionJobBase):
    type: Literal["projection_verify_assignment"]
    session_id: UUID
    payload: _EmptyProjectionPayload


class ProjectionCloseSessionJob(_ProjectionJobBase):
    type: Literal["projection_close_session"]
    session_id: UUID
    payload: _EmptyProjectionPayload


ProjectionJob = (
    ProjectionDetectDisplaysJob
    | ProjectionOpenSessionJob
    | ProjectionAssignWindowJob
    | ProjectionEnterFullscreenJob
    | ProjectionVerifyAssignmentJob
    | ProjectionCloseSessionJob
)


def projection_job_command(job: ProjectionJob) -> ProjectionCommand:
    commands = {
        ProjectionDetectDisplaysJob: "detect_displays",
        ProjectionOpenSessionJob: "open_projection_session",
        ProjectionAssignWindowJob: "assign_projection_window",
        ProjectionEnterFullscreenJob: "enter_projection_fullscreen",
        ProjectionVerifyAssignmentJob: "verify_projection_assignment",
        ProjectionCloseSessionJob: "close_projection_session",
    }
    command = commands.get(type(job))
    if command is None:
        raise ValueError("projection job type is not allowlisted")
    return ProjectionCommand.model_validate(
        {
            "schemaVersion": 1,
            "commandId": job.command_id,
            "command": command,
            "sessionId": job.session_id,
            "expectedGeneration": job.expected_generation,
            "payload": job.payload.model_dump(mode="json", by_alias=True),
        }
    )


def projection_job_timeout_seconds(job: ProjectionJob) -> int:
    ceilings = {
        "projection_detect_displays": 20,
        "projection_open_session": 120,
        "projection_assign_window": 30,
        "projection_enter_fullscreen": 30,
        "projection_verify_assignment": 30,
        "projection_close_session": 30,
    }
    return ceilings[job.type]


JobSpec = Annotated[
    SourceIngestJob
    | DatasetProfileJob
    | KnowledgeRetrieveJob
    | KnowledgePublishJob
    | KnowledgeImportStartJob
    | KnowledgeImportStatusJob
    | KnowledgeImportCancelJob
    | OperationStatusJob
    | KnowledgeReviewListJob
    | KnowledgeReviewDetailJob
    | KnowledgeUpgradeListJob
    | KnowledgeReviewResolveJob
    | KnowledgeCardPublishJob
    | KnowledgeUpgradeResolveJob
    | KnowledgeIndexJob
    | CourseComposeJob
    | CourseOutlineConfirmJob
    | ChartBuildJob
    | VisualSearchJob
    | VisualAcquireJob
    | VisualRevalidateJob
    | CourseVisualAttachJob
    | CourseVisualDetachJob
    | CourseValidateJob
    | CoursePublishJob
    | ProjectionJob,
    Field(discriminator="type"),
]


@dataclass(frozen=True)
class WorkerRuntimeConfig:
    """Pickle-safe configuration passed to a spawned worker process."""

    database_path: str
    app_data_path: str
    source_roots: tuple[tuple[str, str], ...]
    network_fixture: bool = False

    def __post_init__(self) -> None:
        root_ids = tuple(root_id for root_id, _ in self.source_roots)
        if len(set(root_ids)) != len(root_ids):
            raise ValueError("source root IDs must be unique")


@dataclass(frozen=True)
class JobOutcome:
    """Internal bounded-run result before HTTP response projection."""

    status_code: int
    evidence: EvidenceObject
    result: Mapping[str, ImmutableJsonValue]


_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True)
class _PreflightRejected(Exception):
    status_code: int
    reason_code: str
    message: str
    ceiling: Mapping[str, ImmutableJsonValue]


class BoundedJobRunner:
    """Run one typed job in a spawned child with fail-closed parent ceilings."""

    def __init__(
        self,
        config: WorkerRuntimeConfig,
        *,
        mp_context: Any | None = None,
        monotonic: Any = time.monotonic,
        poll_interval: float = 0.02,
    ) -> None:
        self.config = config
        self._mp_context = (
            multiprocessing.get_context("spawn") if mp_context is None else mp_context
        )
        self._monotonic = monotonic
        self._poll_interval = max(0.0, poll_interval)

    async def run(
        self,
        job: object,
        *,
        disconnected: Any,
        session_id: str | None = None,
    ) -> JobOutcome:
        started_at = datetime.now(timezone.utc)
        started_tick = self._monotonic()
        if not isinstance(
            job,
            (
                SourceIngestJob,
                DatasetProfileJob,
                KnowledgeRetrieveJob,
                KnowledgePublishJob,
                KnowledgeImportStartJob,
                KnowledgeImportStatusJob,
                KnowledgeImportCancelJob,
                OperationStatusJob,
                KnowledgeReviewListJob,
                KnowledgeReviewDetailJob,
                KnowledgeUpgradeListJob,
                KnowledgeReviewResolveJob,
                KnowledgeCardPublishJob,
                KnowledgeUpgradeResolveJob,
                KnowledgeIndexJob,
                CourseComposeJob,
                CourseOutlineConfirmJob,
                ChartBuildJob,
                VisualSearchJob,
                VisualAcquireJob,
                VisualRevalidateJob,
                CourseVisualAttachJob,
                CourseVisualDetachJob,
                CourseValidateJob,
                CoursePublishJob,
            ),
        ):
            outcome = self._failure_outcome(
                status_code=422,
                code="job-preflight",
                message="Job type is not allowlisted",
                ceiling={},
                started_at=started_at,
                started_tick=started_tick,
            )
            self._persist_evidence(outcome.evidence)
            return outcome
        try:
            ceiling = self._preflight(job, session_id=session_id)
        except _PreflightRejected as rejected:
            outcome = self._failure_outcome(
                status_code=rejected.status_code,
                code="job-preflight",
                message=rejected.message,
                ceiling=rejected.ceiling,
                started_at=started_at,
                started_tick=started_tick,
                reason_code=rejected.reason_code,
            )
            self._persist_evidence(outcome.evidence)
            return outcome

        result_queue: Any | None = None
        process: Any | None = None
        uses_simple_queue = False
        try:
            simple_queue_factory = getattr(self._mp_context, "SimpleQueue", None)
            uses_simple_queue = callable(simple_queue_factory)
            result_queue = (
                simple_queue_factory() if uses_simple_queue else self._mp_context.Queue()
            )
            payload = cast(
                dict[str, Any],
                job.model_dump(mode="json", by_alias=True),
            )
            spawn_args = (
                (payload, self.config, result_queue)
                if session_id is None
                else (payload, self.config, result_queue, session_id)
            )
            process = self._mp_context.Process(
                target=_spawn_job_entry,
                args=spawn_args,
            )
            process.start()
        except Exception as error:
            if process is not None and getattr(process, "pid", None) is not None:
                self._terminate_and_join(process)
            outcome = self._failure_outcome(
                status_code=500,
                code="job-failed",
                message="Bounded job failed",
                ceiling=ceiling,
                started_at=started_at,
                started_tick=started_tick,
                reason_code=type(error).__name__,
            )
            try:
                self._persist_evidence(outcome.evidence)
            except OSError:
                pass
            finally:
                self._close_process(process)
                self._close_queue(result_queue)
            return outcome
        timeout_seconds = float(ceiling["timeoutSeconds"])
        try:
            while process.is_alive():
                if await disconnected():
                    self._terminate_and_join(process)
                    recovered = self._recover_committed_operation(
                        job,
                        session_id=session_id,
                        ceiling=ceiling,
                        started_at=started_at,
                        started_tick=started_tick,
                        exit_code=process.exitcode,
                    )
                    if recovered is not None:
                        return recovered
                    outcome = self._failure_outcome(
                        status_code=499,
                        code="job-cancelled",
                        message="Client disconnected and the bounded job was cancelled",
                        ceiling=ceiling,
                        started_at=started_at,
                        started_tick=started_tick,
                        exit_code=process.exitcode,
                    )
                    self._persist_evidence(outcome.evidence)
                    return outcome
                if self._monotonic() - started_tick >= timeout_seconds:
                    self._terminate_and_join(process)
                    recovered = self._recover_committed_operation(
                        job,
                        session_id=session_id,
                        ceiling=ceiling,
                        started_at=started_at,
                        started_tick=started_tick,
                        exit_code=process.exitcode,
                    )
                    if recovered is not None:
                        return recovered
                    outcome = self._failure_outcome(
                        status_code=504,
                        code="job-timeout",
                        message="Bounded job exceeded its time ceiling",
                        ceiling=ceiling,
                        started_at=started_at,
                        started_tick=started_tick,
                        exit_code=process.exitcode,
                    )
                    self._persist_evidence(outcome.evidence)
                    return outcome
                await asyncio.sleep(self._poll_interval)
            process.join()
            if process.exitcode not in (None, 0):
                child_payload = {"ok": False}
            else:
                try:
                    child_payload = (
                        result_queue.get()
                        if uses_simple_queue
                        else result_queue.get(timeout=1.0)
                    )
                except (queue.Empty, AssertionError):
                    child_payload = {"ok": False}
            if not isinstance(child_payload, dict) or child_payload.get("ok") is not True:
                recovered = self._recover_committed_operation(
                    job,
                    session_id=session_id,
                    ceiling=ceiling,
                    started_at=started_at,
                    started_tick=started_tick,
                    exit_code=process.exitcode,
                )
                if recovered is not None:
                    return recovered
                failure_code = (
                    str(child_payload.get("code"))
                    if isinstance(child_payload, dict)
                    else "job-failed"
                )
                if failure_code not in {
                    "job-preflight",
                    "job-rejected",
                    "publish-blocked",
                    "immutable-version-conflict",
                    "import-not-found",
                    "import-conflict",
                    "import-expired",
                    "import-integrity",
                    "import-unauthorized",
                    "operation-conflict",
                    "operation-integrity",
                    "operation-unauthorized",
                    "review-request-invalid",
                    "review-not-found",
                    "review-integrity",
                }:
                    failure_code = "job-failed"
                status_code = (
                    int(child_payload.get("statusCode", 500))
                    if isinstance(child_payload, dict)
                    else 500
                )
                if status_code not in {401, 404, 409, 410, 413, 422}:
                    status_code = 500
                outcome = self._failure_outcome(
                    status_code=status_code,
                    code=failure_code,
                    message=_failure_message(failure_code),
                    ceiling=ceiling,
                    started_at=started_at,
                    started_tick=started_tick,
                    exit_code=process.exitcode,
                    reason_code=(
                        str(
                            child_payload.get("reasonCode")
                            or child_payload.get("errorType")
                        )
                        if isinstance(child_payload, dict)
                        and (
                            child_payload.get("reasonCode")
                            or child_payload.get("errorType")
                        )
                        else None
                    ),
                )
                self._persist_evidence(outcome.evidence)
                return outcome
            upstream = EvidenceObject.model_validate_json(
                json.dumps(child_payload["evidence"], ensure_ascii=False)
            )
            outcome = JobOutcome(
                status_code=200,
                result=cast(Mapping[str, ImmutableJsonValue], child_payload["result"]),
                evidence=self._success_evidence(
                    upstream=upstream,
                    ceiling=ceiling,
                    started_at=started_at,
                    started_tick=started_tick,
                    exit_code=process.exitcode,
                ),
            )
            self._persist_evidence(outcome.evidence)
            return outcome
        except asyncio.CancelledError:
            self._terminate_and_join(process)
            self._recover_committed_operation(
                job,
                session_id=session_id,
                ceiling=ceiling,
                started_at=started_at,
                started_tick=started_tick,
                exit_code=process.exitcode,
            )
            evidence = self._failure_outcome(
                status_code=499,
                code="job-cancelled",
                message="Request cancellation terminated the bounded job",
                ceiling=ceiling,
                started_at=started_at,
                started_tick=started_tick,
                exit_code=process.exitcode,
            ).evidence
            self._persist_evidence(evidence)
            raise
        except Exception as error:
            self._terminate_and_join(process)
            recovered = self._recover_committed_operation(
                job,
                session_id=session_id,
                ceiling=ceiling,
                started_at=started_at,
                started_tick=started_tick,
                exit_code=process.exitcode,
            )
            if recovered is not None:
                return recovered
            outcome = self._failure_outcome(
                status_code=500,
                code="job-failed",
                message="Bounded job failed",
                ceiling=ceiling,
                started_at=started_at,
                started_tick=started_tick,
                exit_code=process.exitcode,
                reason_code=type(error).__name__,
            )
            self._persist_evidence(outcome.evidence)
            return outcome
        finally:
            self._close_process(process)
            self._close_queue(result_queue)

    def _preflight(
        self,
        job: SourceIngestJob
        | DatasetProfileJob
        | KnowledgeRetrieveJob
        | KnowledgePublishJob
        | KnowledgeImportStartJob
        | KnowledgeImportStatusJob
        | KnowledgeImportCancelJob
        | OperationStatusJob
        | KnowledgeReviewListJob
        | KnowledgeReviewDetailJob
        | KnowledgeUpgradeListJob
        | KnowledgeReviewResolveJob
        | KnowledgeCardPublishJob
        | KnowledgeUpgradeResolveJob
        | KnowledgeIndexJob
        | CourseComposeJob
        | CourseOutlineConfirmJob
        | ChartBuildJob
        | VisualSearchJob
        | VisualAcquireJob
        | VisualRevalidateJob
        | CourseVisualAttachJob
        | CourseVisualDetachJob
        | CourseValidateJob
        | CoursePublishJob,
        *,
        session_id: str | None = None,
    ) -> Mapping[str, ImmutableJsonValue]:
        return _preflight_job(job, self.config, session_id=session_id)

    def _recover_committed_operation(
        self,
        job: object,
        *,
        session_id: str | None,
        ceiling: Mapping[str, ImmutableJsonValue],
        started_at: datetime,
        started_tick: float,
        exit_code: int | None,
    ) -> JobOutcome | None:
        if session_id is None or not isinstance(
            job,
            (
                KnowledgeImportStartJob,
                KnowledgeImportCancelJob,
                KnowledgeReviewResolveJob,
                KnowledgeCardPublishJob,
                KnowledgeUpgradeResolveJob,
                KnowledgeIndexJob,
                CourseComposeJob,
                CourseOutlineConfirmJob,
                ChartBuildJob,
                VisualSearchJob,
                VisualAcquireJob,
                VisualRevalidateJob,
                CourseVisualAttachJob,
                CourseVisualDetachJob,
                CourseValidateJob,
                CoursePublishJob,
            ),
        ):
            return None
        try:
            with KnowledgeCatalog.open(self.config.database_path) as catalog:
                candidate = operation_status(
                    catalog,
                    operation_id=job.operation_id,
                    actor_id=job.actor.actor_id,
                    actor_type=job.actor.actor_type,
                    session_id=session_id,
                )
                if (
                    isinstance(job, KnowledgeImportCancelJob)
                    and candidate.status == "committed"
                ):
                    stored = UploadStore(
                        catalog, Path(self.config.app_data_path)
                    ).cancel_import_operation(
                        OperationRequest(
                            operation_id=job.operation_id,
                            request_digest=job.request_digest,
                            actor=job.actor.as_domain(),
                            session_id=session_id,
                        ),
                        import_id=job.import_id,
                        clock=lambda: datetime.now(timezone.utc),
                    )
                else:
                    stored = candidate
        except Exception:
            return None
        if stored.status != "committed" or stored.request_digest != job.request_digest:
            return None
        finished_at = datetime.now(timezone.utc)
        try:
            duration_ms = max(0, int((self._monotonic() - started_tick) * 1000))
        except (StopIteration, RuntimeError):
            duration_ms = 0
        refs = cast(dict[str, ImmutableJsonValue], _camelize_json(dict(stored.result_refs)))
        result: dict[str, ImmutableJsonValue] = {
            "operationId": stored.operation_id,
            "operationStatus": "committed",
            **refs,
        }
        evidence = EvidenceObject(
            evidence_id=f"job-{uuid4()}",
            kind="execution",
            status="verified",
            input_summary={"ceiling": dict(ceiling)},
            output_summary={
                "exitCode": exit_code,
                "verification": "committed-outcome-recovered",
            },
            producer="course-helper/job-runner",
            producer_version="1",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            checks=(
                EvidenceCheck(
                    code="operation-recovery",
                    status="passed",
                    message="A committed durable outcome replaced an unobserved worker response",
                    details={"operationStatus": "committed"},
                ),
            ),
        )
        outcome = JobOutcome(status_code=200, result=result, evidence=evidence)
        self._persist_evidence(evidence)
        return outcome

    def _success_evidence(
        self,
        *,
        upstream: EvidenceObject,
        ceiling: Mapping[str, ImmutableJsonValue],
        started_at: datetime,
        started_tick: float,
        exit_code: int | None,
    ) -> EvidenceObject:
        finished_at = datetime.now(timezone.utc)
        duration_ms = max(0, int((self._monotonic() - started_tick) * 1000))
        return EvidenceObject(
            evidence_id=f"job-{uuid4()}",
            kind=upstream.kind,
            subject_version_id=upstream.subject_version_id,
            status=upstream.status,
            input_summary={
                "ceiling": dict(ceiling),
                "upstream_evidence_id": upstream.evidence_id,
            },
            output_summary={
                "exit_code": exit_code,
                "verification": "completed",
            },
            producer="course-helper/job-runner",
            producer_version="1",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            checks=(
                EvidenceCheck(
                    code="job-ceilings",
                    status="passed",
                    message="Typed job stayed inside its declared ceilings",
                    details={"ceiling": dict(ceiling)},
                ),
                EvidenceCheck(
                    code="job-exit",
                    status="passed" if exit_code == 0 else "warning",
                    message="Spawned worker exit status was captured",
                    details={"exit_code": exit_code},
                ),
                EvidenceCheck(
                    code="job-verification",
                    status="passed",
                    message="Worker returned a validated evidence object",
                    details={"upstream_evidence_id": upstream.evidence_id},
                ),
                *upstream.checks,
            ),
            errors=upstream.errors,
            artifacts=upstream.artifacts,
        )

    def _failure_outcome(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        ceiling: Mapping[str, ImmutableJsonValue],
        started_at: datetime,
        started_tick: float,
        exit_code: int | None = None,
        reason_code: str | None = None,
    ) -> JobOutcome:
        finished_at = datetime.now(timezone.utc)
        try:
            duration_ms = max(0, int((self._monotonic() - started_tick) * 1000))
        except (StopIteration, RuntimeError):
            duration_ms = 0
        details: dict[str, ImmutableJsonValue] = {
            "ceiling": dict(ceiling),
            "exit_code": exit_code,
        }
        if reason_code is not None:
            details["reason_code"] = reason_code
        evidence = EvidenceObject(
            evidence_id=f"job-{uuid4()}",
            kind="execution",
            status="failed",
            input_summary={"ceiling": dict(ceiling)},
            output_summary={"exit_code": exit_code, "verification": "failed"},
            producer="course-helper/job-runner",
            producer_version="1",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            checks=(
                EvidenceCheck(
                    code=code,
                    status="failed",
                    message=message,
                    details=details,
                ),
            ),
            errors=(
                EvidenceError(
                    code=code,
                    message=message,
                    retryable=code in {"job-timeout", "job-cancelled"},
                ),
            ),
        )
        return JobOutcome(status_code=status_code, result={}, evidence=evidence)

    def _persist_evidence(self, evidence: EvidenceObject) -> None:
        directory = Path(self.config.app_data_path) / "job-evidence"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{evidence.evidence_id}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(destination)

    @staticmethod
    def _terminate_and_join(process: Any) -> None:
        if process.is_alive():
            process.terminate()
        process.join()

    @staticmethod
    def _close_process(process: Any | None) -> None:
        if process is None:
            return
        close = getattr(process, "close", None)
        if callable(close):
            try:
                close()
            except (OSError, RuntimeError, ValueError):
                pass

    @staticmethod
    def _close_queue(result_queue: Any | None) -> None:
        if result_queue is None:
            return
        close = getattr(result_queue, "close", None)
        if callable(close):
            try:
                close()
            except (OSError, RuntimeError, ValueError):
                pass
        join_thread = getattr(result_queue, "join_thread", None)
        if callable(join_thread):
            try:
                join_thread()
            except (OSError, RuntimeError, ValueError):
                pass


def _job_ceiling(job_type: str) -> Mapping[str, ImmutableJsonValue]:
    ceilings: dict[str, Mapping[str, ImmutableJsonValue]] = {
        "source_ingest": {
            "timeoutSeconds": 120,
            "maxSourceBytes": 512 * _MIB,
            "maxSelectedSlides": 64,
        },
        "dataset_profile": {
            "timeoutSeconds": 60,
            "maxCsvParquetBytes": _GIB,
            "maxXlsxBytes": 512 * _MIB,
            "maxSamples": 20,
        },
        "knowledge_retrieve": {
            "timeoutSeconds": 5,
            "maxQueryCharacters": 2000,
            "maxTags": 50,
            "maxHits": 50,
        },
        "knowledge_publish": {
            "timeoutSeconds": 10,
            "maxCardVersions": 1,
        },
        "knowledge_import_start": {
            "timeoutSeconds": 30,
            "maxUploads": 1,
            "maxUploadBytes": 20 * _MIB,
        },
        "knowledge_import_status": {
            "timeoutSeconds": 5,
            "maxImports": 1,
        },
        "knowledge_import_cancel": {
            "timeoutSeconds": 10,
            "maxImports": 1,
        },
        "operation_status": {
            "timeoutSeconds": 5,
            "maxOperations": 1,
        },
        "knowledge_review_list": {
            "timeoutSeconds": 5,
            "maxItems": 100,
        },
        "knowledge_review_detail": {
            "timeoutSeconds": 5,
            "maxContentNodes": 50,
            "maxCitations": 50,
        },
        "knowledge_upgrade_list": {
            "timeoutSeconds": 5,
            "maxItems": 100,
        },
        "knowledge_review_resolve": {
            "timeoutSeconds": 10,
            "maxItems": 50,
        },
        "knowledge_card_publish": {
            "timeoutSeconds": 30,
            "maxItems": 1,
        },
        "knowledge_upgrade_resolve": {
            "timeoutSeconds": 15,
            "maxItems": 50,
        },
        "knowledge_index": {
            "timeoutSeconds": 30,
            "maxOutboxItems": 1,
        },
        "course_compose": {
            "timeoutSeconds": 30,
            "maxLearningGoals": 20,
            "maxCardOverrides": 200,
        },
        "course_outline_confirm": {
            "timeoutSeconds": 20,
            "maxOutlines": 1,
        },
        "chart_build": {
            "timeoutSeconds": 30,
            "maxCharts": 20,
            "maxResultRowsPerChart": 100,
        },
        "visual_search": {
            "timeoutSeconds": 15,
            "maxCandidates": 10,
        },
        "visual_acquire": {
            "timeoutSeconds": 45,
            "maxCandidates": 10,
            "maxArtifactBytes": 32 * _MIB,
        },
        "visual_revalidate": {
            "timeoutSeconds": 15,
            "maxVisuals": 1,
        },
        "course_visual_attach": {
            "timeoutSeconds": 20,
            "maxPlacements": 1,
        },
        "course_visual_detach": {
            "timeoutSeconds": 10,
            "maxPlacements": 500,
        },
        "course_validate": {
            "timeoutSeconds": 30,
            "maxPlacements": 500,
        },
        "course_publish": {
            "timeoutSeconds": 30,
            "maxPlacements": 500,
        },
    }
    return ceilings[job_type]


def _preflight_job(
    job: SourceIngestJob
    | DatasetProfileJob
    | KnowledgeRetrieveJob
    | KnowledgePublishJob
    | KnowledgeImportStartJob
    | KnowledgeImportStatusJob
    | KnowledgeImportCancelJob
    | OperationStatusJob
    | KnowledgeReviewListJob
    | KnowledgeReviewDetailJob
    | KnowledgeUpgradeListJob
    | KnowledgeReviewResolveJob
    | KnowledgeCardPublishJob
    | KnowledgeUpgradeResolveJob
    | KnowledgeIndexJob
    | CourseComposeJob
    | CourseOutlineConfirmJob
    | ChartBuildJob
    | VisualSearchJob
    | VisualAcquireJob
    | VisualRevalidateJob
    | CourseVisualAttachJob
    | CourseVisualDetachJob
    | CourseValidateJob
    | CoursePublishJob,
    config: WorkerRuntimeConfig,
    *,
    session_id: str | None = None,
) -> Mapping[str, ImmutableJsonValue]:
    """Apply the same fail-closed ceilings in both parent and spawned child."""

    ceiling = _job_ceiling(job.type)
    if isinstance(
        job,
        (
            KnowledgeImportStartJob,
            KnowledgeImportStatusJob,
            KnowledgeImportCancelJob,
            OperationStatusJob,
            KnowledgeReviewListJob,
            KnowledgeReviewDetailJob,
            KnowledgeUpgradeListJob,
            KnowledgeReviewResolveJob,
            KnowledgeCardPublishJob,
            KnowledgeUpgradeResolveJob,
            KnowledgeIndexJob,
            CourseComposeJob,
            CourseOutlineConfirmJob,
            ChartBuildJob,
            VisualSearchJob,
            VisualAcquireJob,
            VisualRevalidateJob,
            CourseVisualAttachJob,
            CourseVisualDetachJob,
            CourseValidateJob,
            CoursePublishJob,
        ),
    ) and session_id is None:
        raise _PreflightRejected(
            401,
            "session-required",
            "An authenticated session is required",
            ceiling,
        )
    if isinstance(job, SourceIngestJob):
        registry = _source_registry(config)
        locator = _domain_locator(job.locator)
        try:
            path = registry.resolve(locator)
        except SourceRootViolation:
            raise _PreflightRejected(
                422,
                "invalid-source",
                "Source locator is unavailable",
                ceiling,
            ) from None
        if path.stat().st_size > 512 * _MIB:
            raise _PreflightRejected(
                413,
                "source-too-large",
                "Source exceeds the ingest size ceiling",
                ceiling,
            )
        extension = path.suffix.casefold()
        if extension == ".pptx":
            if job.selection.heading_selectors:
                raise _PreflightRejected(
                    422,
                    "invalid-selection",
                    "PPTX ingest requires slide selection",
                    ceiling,
                )
            slide_count = _pptx_slide_count(path)
            selected = job.selection.slide_numbers or tuple(
                range(1, slide_count + 1)
            )
            if len(selected) > 64:
                raise _PreflightRejected(
                    422,
                    "too-many-slides",
                    "PPTX selection exceeds 64 slides",
                    ceiling,
                )
            if any(number > slide_count for number in selected):
                raise _PreflightRejected(
                    422,
                    "invalid-selection",
                    "PPTX selection contains an unavailable slide",
                    ceiling,
                )
        elif extension in {".md", ".markdown"}:
            if job.selection.slide_numbers:
                raise _PreflightRejected(
                    422,
                    "invalid-selection",
                    "Markdown ingest requires heading selection",
                    ceiling,
                )
        else:
            raise _PreflightRejected(
                422,
                "unsupported-source",
                "Source type is not supported by ingest",
                ceiling,
            )
    elif isinstance(job, DatasetProfileJob):
        registry = _source_registry(config)
        try:
            path = registry.resolve(_domain_locator(job.locator))
        except SourceRootViolation:
            raise _PreflightRejected(
                422,
                "invalid-dataset",
                "Dataset locator is unavailable",
                ceiling,
            ) from None
        extension = path.suffix.casefold()
        size_limit = (
            _GIB
            if extension in {".csv", ".parquet"}
            else 512 * _MIB
            if extension in {".xlsx", ".xls"}
            else None
        )
        if size_limit is None:
            raise _PreflightRejected(
                422,
                "unsupported-dataset",
                "Dataset type is not supported by profiling",
                ceiling,
            )
        if path.stat().st_size > size_limit:
            raise _PreflightRejected(
                413,
                "dataset-too-large",
                "Dataset exceeds the profiling size ceiling",
                ceiling,
            )
    elif isinstance(job, KnowledgePublishJob):
        try:
            with KnowledgeCatalog.open(config.database_path) as catalog:
                exists = catalog.connection.execute(
                    "SELECT 1 FROM cards WHERE version_id = ?",
                    (job.card_version_id,),
                ).fetchone()
        except Exception:
            exists = None
        if exists is None:
            raise _PreflightRejected(
                422,
                "card-not-found",
                "Card version is unavailable for publication",
                ceiling,
            )
    return ceiling


def _source_registry(config: WorkerRuntimeConfig) -> SourceRootRegistry:
    return SourceRootRegistry(
        {root_id: Path(path) for root_id, path in config.source_roots}
    )


def _domain_locator(locator: HttpSourceLocator) -> SourceLocator:
    return SourceLocator(root_id=locator.root_id, relative_path=locator.relative_path)


def _pptx_slide_count(path: Path) -> int:
    try:
        with zipfile.ZipFile(path) as archive:
            return sum(
                name.startswith("ppt/slides/slide") and name.endswith(".xml")
                for name in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile):
        raise _PreflightRejected(
            422,
            "invalid-pptx",
            "PPTX source could not be inspected",
            _job_ceiling("source_ingest"),
        ) from None


def _spawn_job_entry(
    job_payload: dict[str, Any],
    config: WorkerRuntimeConfig,
    result_queue: Any,
    session_id: str | None = None,
) -> None:
    """Module-level spawn target; it accepts only serializable job/config values."""

    try:
        job = TypeAdapter(JobSpec).validate_python(job_payload)
        _preflight_job(job, config, session_id=session_id)
        handler = _ALLOWLISTED_HANDLERS[job.type]
        if isinstance(
            job,
            (
                KnowledgeImportStartJob,
                KnowledgeImportStatusJob,
                KnowledgeImportCancelJob,
                OperationStatusJob,
                KnowledgeReviewListJob,
                KnowledgeReviewDetailJob,
                KnowledgeUpgradeListJob,
                KnowledgeReviewResolveJob,
                KnowledgeCardPublishJob,
                KnowledgeUpgradeResolveJob,
                KnowledgeIndexJob,
                CourseComposeJob,
                CourseOutlineConfirmJob,
                ChartBuildJob,
                VisualSearchJob,
                VisualAcquireJob,
                VisualRevalidateJob,
                CourseVisualAttachJob,
                CourseVisualDetachJob,
                CourseValidateJob,
                CoursePublishJob,
            ),
        ):
            if session_id is None:
                raise ValueError("authenticated job session is unavailable")
            result, evidence = handler(job, config, session_id)
        else:
            result, evidence = handler(job, config)
        result_queue.put(
            {
                "ok": True,
                "result": result,
                "evidence": evidence.model_dump(mode="json"),
            }
        )
    except _PreflightRejected as rejected:
        result_queue.put(
            {
                "ok": False,
                "statusCode": rejected.status_code,
                "code": "job-preflight",
                "reasonCode": rejected.reason_code,
            }
        )
    except PublishBlocked:
        result_queue.put(
            {"ok": False, "statusCode": 422, "code": "publish-blocked"}
        )
    except ImmutableVersionConflict:
        result_queue.put(
            {
                "ok": False,
                "statusCode": 422,
                "code": "immutable-version-conflict",
            }
        )
    except (
        CatalogReferenceError,
        RetrievalQueryError,
        RetrievalFailure,
        ValidationError,
        CompositionError,
        IndexLeaseConflict,
        IndexSnapshotIntegrityError,
        NetworkVisualError,
        SlideBuildError,
    ):
        result_queue.put(
            {"ok": False, "statusCode": 422, "code": "job-rejected"}
        )
    except UploadError as error:
        status_code, code = {
            "UPLOAD_INVALID": (422, "import-integrity"),
            "UPLOAD_TOO_LARGE": (413, "import-integrity"),
            "UPLOAD_NOT_FOUND": (404, "import-not-found"),
            "UPLOAD_EXPIRED": (410, "import-expired"),
            "UPLOAD_CONFLICT": (409, "import-conflict"),
            "UPLOAD_INTEGRITY_INVALID": (409, "import-integrity"),
            "IMPORT_NOT_FOUND": (404, "import-not-found"),
            "IMPORT_CONFLICT": (409, "import-conflict"),
            "IMPORT_AUTHENTICATION_FAILED": (401, "import-unauthorized"),
        }[error.code]
        result_queue.put(
            {"ok": False, "statusCode": status_code, "code": code}
        )
    except OperationAuthenticationError:
        result_queue.put(
            {"ok": False, "statusCode": 401, "code": "operation-unauthorized"}
        )
    except OperationConflict:
        result_queue.put(
            {"ok": False, "statusCode": 409, "code": "operation-conflict"}
        )
    except OperationIntegrityError:
        result_queue.put(
            {"ok": False, "statusCode": 409, "code": "operation-integrity"}
        )
    except ReviewQueryError:
        result_queue.put(
            {"ok": False, "statusCode": 422, "code": "review-request-invalid"}
        )
    except ReviewNotFoundError:
        result_queue.put(
            {"ok": False, "statusCode": 404, "code": "review-not-found"}
        )
    except ReviewProjectionError:
        result_queue.put(
            {"ok": False, "statusCode": 409, "code": "review-integrity"}
        )
    except Exception as error:
        result_queue.put(
            {
                "ok": False,
                "code": "job-failed",
                "errorType": type(error).__name__,
            }
        )


def _failure_message(code: str) -> str:
    return {
        "job-preflight": "Worker preflight rejected changed input",
        "job-rejected": "Typed job was rejected",
        "publish-blocked": "Publication was rejected by governance gates",
        "immutable-version-conflict": "Immutable catalog version conflict",
        "import-not-found": "Import was not found",
        "import-conflict": "Import state changed",
        "import-expired": "Import upload expired",
        "import-integrity": "Import integrity validation failed",
        "import-unauthorized": "Import is not owned by this session",
        "operation-conflict": "Operation identity changed",
        "operation-integrity": "Operation integrity validation failed",
        "operation-unauthorized": "Operation is not owned by this session",
        "review-request-invalid": "Review request is invalid",
        "review-not-found": "Review task was not found",
        "review-integrity": "Review projection integrity validation failed",
        "job-failed": "Bounded job failed",
    }.get(code, "Bounded job failed")


def _run_source_ingest(
    job: SourceIngestJob,
    config: WorkerRuntimeConfig,
) -> tuple[dict[str, Any], EvidenceObject]:
    registry = _source_registry(config)
    locator = _domain_locator(job.locator)
    path = registry.resolve(locator)
    if path.suffix.casefold() == ".pptx":
        selected = frozenset(job.selection.slide_numbers) or None
        extraction = PptxParser(registry).parse(
            locator,
            slide_range=cast(Any, selected),
        )
    else:
        extraction = MarkdownParser(registry).parse(
            locator,
            heading_selectors=job.selection.heading_selectors,
        )
    with KnowledgeCatalog.open(config.database_path) as catalog:
        with catalog.atomic_write():
            catalog.insert_source(extraction.source)
            for chunk in extraction.chunks:
                catalog.insert_chunk(chunk)
            for visual in extraction.visuals:
                catalog.insert_visual(visual)
            for dataset in extraction.datasets:
                catalog.insert_dataset(dataset)
            catalog.insert_evidence(extraction.evidence)
    return (
        {
            "sourceVersionId": extraction.source.version_id,
            "chunkCount": len(extraction.chunks),
            "visualCount": len(extraction.visuals),
            "datasetCount": len(extraction.datasets),
        },
        extraction.evidence,
    )


def _run_dataset_profile(
    job: DatasetProfileJob,
    config: WorkerRuntimeConfig,
) -> tuple[dict[str, Any], EvidenceObject]:
    registry = _source_registry(config)
    locator = _domain_locator(job.locator)
    path = registry.resolve(locator)
    profiler = DatasetProfiler(registry)
    if path.suffix.casefold() == ".csv":
        profile = profiler.profile_csv(locator, sample_limit=job.sample_limit)
    elif path.suffix.casefold() == ".parquet":
        profile = profiler.profile_parquet(locator, sample_limit=job.sample_limit)
    elif path.suffix.casefold() in {".xlsx", ".xls"}:
        profile = profiler.profile_xlsx(
            locator,
            sheet_name=job.sheet_name,
            sample_limit=job.sample_limit,
        )
    else:
        raise ValueError("dataset format is not available for deep profiling")
    with KnowledgeCatalog.open(config.database_path) as catalog:
        with catalog.atomic_write():
            catalog.insert_dataset(profile)
            catalog.insert_evidence(profile.evidence)
    return (
        {
            "datasetVersionId": profile.version_id,
            "rowCount": profile.row_count,
            "columnCount": len(profile.columns),
            "sampleCount": len(profile.sample_rows),
            "reviewStatus": profile.review_status,
        },
        profile.evidence,
    )


def _run_knowledge_retrieve(
    job: KnowledgeRetrieveJob,
    config: WorkerRuntimeConfig,
) -> tuple[dict[str, Any], EvidenceObject]:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        result = KnowledgeRetriever(catalog).search(
            RetrievalQuery(
                text=job.query,
                required_tag_ids=job.required_tag_ids,
                limit=job.limit,
            )
        )
        catalog.insert_evidence(result.evidence)
    return (
        cast(
            dict[str, Any],
            _camelize_json(result.model_dump(mode="json", exclude={"evidence"})),
        ),
        result.evidence,
    )


def _run_knowledge_publish(
    job: KnowledgePublishJob,
    config: WorkerRuntimeConfig,
) -> tuple[dict[str, Any], EvidenceObject]:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        row = catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?",
            (job.card_version_id,),
        ).fetchone()
        if row is None:
            raise ValueError("card version is unavailable")
        submitted = KnowledgeCardVersion.model_validate_json(row[0])
        published = publish_card(submitted, catalog)
        evidence = catalog.publication_receipt(
            submitted=submitted,
            published=published,
        )
    return (
        {
            "submittedCardVersionId": job.card_version_id,
            "publishedCardVersionId": published.version_id,
            "status": published.status,
        },
        evidence,
    )


def _operation_result(outcome: OperationOutcome) -> dict[str, Any]:
    return {
        "operationId": outcome.operation_id,
        "status": outcome.status,
        "requestDigest": outcome.request_digest,
        "resultRefs": _camelize_json(dict(outcome.result_refs)),
    }


def _mutation_operation_result(outcome: OperationOutcome) -> dict[str, Any]:
    return {
        "operationId": outcome.operation_id,
        "operationStatus": outcome.status,
        **cast(dict[str, Any], _camelize_json(dict(outcome.result_refs))),
    }


def _operation_resolution_id(prefix: str, operation_id: str, request_digest: str) -> str:
    return prefix + "-" + hashlib.sha256(
        f"{operation_id}\0{request_digest}".encode("utf-8")
    ).hexdigest()[:48]


def _knowledge_operation_evidence(
    *,
    code: str,
    status: str,
    subject_version_id: str | None = None,
) -> EvidenceObject:
    now = datetime.now(timezone.utc)
    return EvidenceObject(
        evidence_id=f"{code}-{uuid4()}",
        kind="execution",
        subject_version_id=subject_version_id,
        status="verified",
        input_summary={"operationType": code},
        output_summary={"status": status},
        producer="course-helper/knowledge-operations",
        producer_version="1",
        started_at=now,
        finished_at=now,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code=code,
                status="passed",
                message="Typed knowledge operation completed through its durable authority",
                details={"status": status},
            ),
        ),
    )


def _run_knowledge_import_start(
    job: KnowledgeImportStartJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    with KnowledgeCatalog.open(config.database_path) as catalog:
        existing = operation_status(
            catalog,
            operation_id=job.operation_id,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            session_id=session_id,
        )
        if existing.status == "committed":
            if existing.request_digest != job.request_digest:
                raise OperationConflict("import operation digest changed")
            return (
                _mutation_operation_result(existing),
                _knowledge_operation_evidence(
                    code="knowledge-import-start",
                    status="promoted",
                    subject_version_id=str(
                        existing.result_refs.get("sourceVersionId") or ""
                    ) or None,
                ),
            )
        store = UploadStore(catalog, Path(config.app_data_path))
        lease_operation_id = "import-lease-" + hashlib.sha256(
            f"{job.operation_id}\0{job.request_digest}".encode("utf-8")
        ).hexdigest()[:32]
        started = store.start_import(
            OperationRequest(
                operation_id=lease_operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            upload_id=job.upload_id,
            expected_content_digest=job.expected_content_digest,
            clock=lambda: datetime.now(timezone.utc),
        )
        import_id = str(started.result_refs["importId"])
        promotion_operation_id = "import-promote-" + hashlib.sha256(
            f"{job.operation_id}\0{import_id}".encode("utf-8")
        ).hexdigest()[:32]
        promotion_digest = import_promotion_request_digest(
            import_id, job.expected_content_digest
        )
        promoted = store.promote_import(
            OperationRequest(
                operation_id=promotion_operation_id,
                request_digest=promotion_digest,
                actor=actor,
                session_id=session_id,
            ),
            import_id=import_id,
            expected_content_digest=job.expected_content_digest,
            clock=lambda: datetime.now(timezone.utc),
        )
        lease = store.import_status(import_id, session_id=session_id, actor=actor)
        if lease.source_version_id is None:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Promoted source is unavailable")
        source = catalog.get_source(lease.source_version_id)
        if source is None:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Promoted source is unavailable")
        extraction = None
        dataset = None
        if source.source_kind in {"markdown", "pptx"}:
            extraction = parse_promoted_source(
                catalog,
                source=source,
                app_data_path=Path(config.app_data_path),
                actor=actor,
            )
        elif source.source_kind in {"csv", "parquet", "xls", "xlsx"}:
            dataset = profile_promoted_dataset(
                catalog,
                source=source,
                app_data_path=Path(config.app_data_path),
                actor=actor,
            )
        else:
            raise CatalogReferenceError("governed import type is unsupported")

        def mutation() -> OperationMutationResult:
            if extraction is not None:
                imported = persist_governed_import(
                    catalog,
                    extraction=extraction,
                    actor=actor,
                )
                chunk_count = imported.chunk_count
                visual_count = imported.visual_count
                visual_ids = list(imported.visual_version_ids)
                if source.source_kind == "pptx" and visual_ids:
                    roots = {
                        root_id: Path(path) for root_id, path in config.source_roots
                    }
                    governed_root = Path(config.app_data_path) / "source-blobs"
                    configured = roots.get("governed-upload")
                    if (
                        configured is not None
                        and configured.resolve() != governed_root.resolve()
                    ):
                        raise CatalogReferenceError(
                            "governed source root conflicts with runtime"
                        )
                    roots["governed-upload"] = governed_root
                    materialized = materialize_source_visuals(
                        catalog,
                        SourceRootRegistry(roots),
                        ArtifactStore(Path(config.app_data_path) / "artifacts"),
                        source_version_id=source.version_id,
                        visual_version_ids=tuple(visual_ids),
                        clock=lambda: source.created_at,
                    )
                    if any(item.status != "materialized" for item in materialized):
                        raise CatalogReferenceError(
                            "governed source visual materialization failed"
                        )
                card_ids = list(imported.candidate_card_version_ids)
                dataset_ids: list[str] = []
                review_ids = list(imported.review_task_ids)
                evidence_id = imported.extraction_evidence_id
            elif dataset is not None:
                profiled = persist_governed_dataset(
                    catalog,
                    dataset=dataset,
                    source=source,
                    actor=actor,
                )
                chunk_count = 0
                visual_count = 0
                visual_ids = []
                card_ids = []
                dataset_ids = [profiled.dataset_version_id]
                dataset_profiles = [
                    {
                        "datasetVersionId": profiled.dataset_version_id,
                        "contentDigest": dataset.content_digest,
                        "schemaDigest": dataset_schema_digest(dataset),
                        "rowCount": dataset.row_count,
                        "columns": [
                            {
                                "name": column.name,
                                "dataType": column.data_type,
                                "digest": dataset_column_digest(column),
                            }
                            for column in dataset.columns
                            if column.sensitive_category is None
                        ],
                    }
                ]
                review_ids = list(profiled.review_task_ids)
                evidence_id = profiled.profile_evidence_id
            else:
                raise RuntimeError("governed import parse result is unavailable")
            return OperationMutationResult(
                result_refs={
                    "importId": import_id,
                    "status": lease.state,
                    "sourceId": promoted.result_refs["sourceId"],
                    "sourceVersionId": source.version_id,
                    "contentDigest": promoted.result_refs["contentDigest"],
                    "chunkCount": chunk_count,
                    "visualCount": visual_count,
                    "visualVersionIds": visual_ids,
                    "candidateCardVersionIds": card_ids,
                    "datasetVersionIds": dataset_ids,
                    "datasetProfiles": dataset_profiles if dataset is not None else [],
                    "reviewTaskIds": review_ids,
                    "extractionEvidenceId": evidence_id,
                },
                item_outcomes=(),
                index_outbox=(),
            )

        completed = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            mutation,
            clock=lambda: datetime.now(timezone.utc),
        )
    result = _mutation_operation_result(completed)
    return (
        result,
        _knowledge_operation_evidence(
            code="knowledge-import-start",
            status=lease.state,
            subject_version_id=lease.source_version_id,
        ),
    )


def _run_knowledge_import_status(
    job: KnowledgeImportStatusJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        lease = UploadStore(catalog, Path(config.app_data_path)).import_status(
            job.import_id,
            session_id=session_id,
            actor=job.actor.as_domain(),
        )
    result = {
        "importId": lease.import_id,
        "status": lease.state,
        "sourceVersionId": lease.source_version_id,
        "createdAt": lease.created_at.isoformat(),
        "updatedAt": lease.updated_at.isoformat(),
    }
    return (
        result,
        _knowledge_operation_evidence(
            code="knowledge-import-status",
            status=lease.state,
            subject_version_id=lease.source_version_id,
        ),
    )


def _run_knowledge_import_cancel(
    job: KnowledgeImportCancelJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = UploadStore(catalog, Path(config.app_data_path)).cancel_import_operation(
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=job.actor.as_domain(),
                session_id=session_id,
            ),
            import_id=job.import_id,
            clock=lambda: datetime.now(timezone.utc),
        )
    return (
        _operation_result(outcome),
        _knowledge_operation_evidence(
            code="knowledge-import-cancel",
            status=outcome.status,
        ),
    )


def _run_operation_status(
    job: OperationStatusJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = operation_status(
            catalog,
            operation_id=job.operation_id,
            actor_id=job.actor.actor_id,
            actor_type=job.actor.actor_type,
            session_id=session_id,
        )
    return (
        _operation_result(outcome),
        _knowledge_operation_evidence(
            code="operation-status",
            status=outcome.status,
        ),
    )


def _run_knowledge_review_list(
    job: KnowledgeReviewListJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    del session_id
    with KnowledgeCatalog.open(config.database_path) as catalog:
        page = list_review_tasks(
            catalog,
            status=job.status,
            category=job.category,
            cursor=job.cursor,
            limit=job.limit,
        )
    return (
        cast(dict[str, Any], _camelize_json(page.model_dump(mode="json"))),
        _knowledge_operation_evidence(
            code="knowledge-review-list",
            status="completed",
        ),
    )


def _run_knowledge_review_detail(
    job: KnowledgeReviewDetailJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    del session_id
    with KnowledgeCatalog.open(config.database_path) as catalog:
        detail = get_review_detail(catalog, job.task_id)
    return (
        cast(dict[str, Any], _camelize_json(detail.model_dump(mode="json"))),
        _knowledge_operation_evidence(
            code="knowledge-review-detail",
            status="completed",
            subject_version_id=detail.task.subject_version_id,
        ),
    )


def _run_knowledge_upgrade_list(
    job: KnowledgeUpgradeListJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    del session_id
    with KnowledgeCatalog.open(config.database_path) as catalog:
        page = list_upgrade_suggestions(
            catalog,
            status=job.status,
            cursor=job.cursor,
            limit=job.limit,
        )
    return (
        cast(dict[str, Any], _camelize_json(page.model_dump(mode="json"))),
        _knowledge_operation_evidence(
            code="knowledge-upgrade-list",
            status="completed",
        ),
    )


def _run_knowledge_review_resolve(
    job: KnowledgeReviewResolveJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    server_now = datetime.now(timezone.utc)
    resolution_id = _operation_resolution_id(
        "resolution", job.operation_id, job.request_digest
    )
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _resolve_review_mutation(
                catalog,
                job=job,
                resolution_id=resolution_id,
                server_now=server_now,
                actor=actor,
            ),
            clock=lambda: server_now,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="knowledge-review-resolve",
            status=outcome.status,
            subject_version_id=job.task_id,
        ),
    )


def _resolve_review_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: KnowledgeReviewResolveJob,
    resolution_id: str,
    server_now: datetime,
    actor: ActorRef,
) -> OperationMutationResult:
    review_created_at = get_review_detail(catalog, job.task_id).task.created_at
    resolved_at = max(server_now, review_created_at)
    stored = resolve_review_task(
        catalog,
        ReviewResolution(
            resolution_id=resolution_id,
            task_id=job.task_id,
            decision=job.decision,
            expected_review_digest=job.expected_review_digest,
            evidence_ids=tuple(sorted(job.evidence_ids)),
            resolved_at=resolved_at,
            resolved_by=actor,
        ),
    )
    return OperationMutationResult(
        result_refs={
            "taskId": stored.task_id,
            "resolutionId": stored.resolution_id,
            "decision": stored.decision,
            "reviewStatus": (
                "dismissed" if stored.decision == "dismiss" else "resolved"
            ),
        },
        item_outcomes=(),
        index_outbox=(),
    )


def _run_knowledge_card_publish(
    job: KnowledgeCardPublishJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _publish_card_mutation(catalog, job=job),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="knowledge-card-publish",
            status=outcome.status,
            subject_version_id=str(
                outcome.result_refs.get("publishedCardVersionId")
                or job.card_version_id
            ),
        ),
    )


def _publish_card_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: KnowledgeCardPublishJob,
) -> OperationMutationResult:
    row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM cards WHERE version_id = ?",
        (job.card_version_id,),
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("card version is unavailable")
    submitted = KnowledgeCardVersion.model_validate_json(str(row[1]), strict=False)
    if (
        canonical_model_json(submitted) != str(row[1])
        or submitted.version_id != job.card_version_id
        or submitted.content_digest != str(row[0])
        or submitted.content_digest != job.expected_card_digest
    ):
        raise CatalogReferenceError("card publication digest is stale")
    published = publish_card_in_operation(submitted, catalog)
    receipt = catalog.publication_receipt(submitted=submitted, published=published)
    outbox_id = "index-outbox-" + hashlib.sha256(
        f"{job.operation_id}\0{job.request_digest}\0{published.version_id}".encode(
            "utf-8"
        )
    ).hexdigest()[:48]
    return OperationMutationResult(
        result_refs={
            "submittedCardVersionId": submitted.version_id,
            "publishedCardVersionId": published.version_id,
            "status": published.status,
            "publicationEvidenceId": receipt.evidence_id,
            "indexState": "queued",
            "indexOutboxId": outbox_id,
            "indexSnapshotId": None,
        },
        item_outcomes=(),
        index_outbox=(
            IndexOutboxItem(
                outbox_id=outbox_id,
                card_version_id=published.version_id,
                action="upsert",
            ),
        ),
    )


def _run_knowledge_upgrade_resolve(
    job: KnowledgeUpgradeResolveJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    server_now = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _resolve_upgrade_mutation(
                catalog,
                job=job,
                actor=actor,
                server_now=server_now,
            ),
            clock=lambda: server_now,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="knowledge-upgrade-resolve",
            status=outcome.status,
            subject_version_id=job.suggestion_id,
        ),
    )


def _resolve_upgrade_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: KnowledgeUpgradeResolveJob,
    actor: ActorRef,
    server_now: datetime,
) -> OperationMutationResult:
    suggestion_row = catalog.connection.execute(
        "SELECT review_task_id FROM upgrade_suggestions WHERE suggestion_id = ?",
        (job.suggestion_id,),
    ).fetchone()
    if suggestion_row is None:
        raise CatalogReferenceError("upgrade suggestion is unavailable")
    review_created_at = get_review_detail(
        catalog, str(suggestion_row[0])
    ).task.created_at
    resolved_at = max(server_now, review_created_at)
    result = resolve_upgrade_suggestion(
        catalog,
        suggestion_id=job.suggestion_id,
        decision=job.decision,
        actor=actor,
        evidence_ids=tuple(sorted(job.evidence_ids)),
        resolved_at=resolved_at,
        expected_suggestion_digest=job.expected_suggestion_digest,
        expected_review_digest=job.expected_review_digest,
        expected_candidate_digest=job.expected_card_digest,
    )
    return OperationMutationResult(
        result_refs={
            "suggestionId": result.suggestion_id,
            "candidateVersionId": result.candidate_version_id,
            "decision": result.decision,
            "resolutionId": result.resolution_id,
            "nextRequiredReviewTaskIds": list(
                result.next_required_review_task_ids
            ),
            "nextAction": result.next_action,
        },
        item_outcomes=(),
        index_outbox=(),
    )


def _run_knowledge_index(
    job: KnowledgeIndexJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _index_one_mutation(
                catalog,
                job=job,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="knowledge-index",
            status=outcome.status,
            subject_version_id=str(
                outcome.result_refs.get("indexSnapshotId") or ""
            )
            or None,
        ),
    )


def _index_one_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: KnowledgeIndexJob,
    committed_at: datetime,
) -> OperationMutationResult:
    worker_id = _operation_resolution_id(
        "index-worker", job.operation_id, job.request_digest
    )
    claim = claim_next_index_outbox(
        catalog,
        worker_id=worker_id,
        now=committed_at,
        lease_seconds=60,
    )
    if claim is None or claim.outbox_id != job.expected_outbox_id:
        raise CatalogReferenceError("expected index work item is not next and pending")
    snapshot = complete_index_claim(
        catalog,
        claim_id=claim.claim_id,
        worker_id=worker_id,
        embedding_provider=None,
        now=committed_at,
    )
    return OperationMutationResult(
        result_refs={
            "consumedOutboxId": claim.outbox_id,
            "indexSnapshotId": snapshot.index_snapshot_id,
            "indexSnapshotDigest": snapshot.snapshot_digest,
            "indexState": snapshot.status,
            "retrievalMode": snapshot.retrieval_mode,
            "semanticIndexAvailable": snapshot.retrieval_mode == "hybrid",
        },
        item_outcomes=(),
        index_outbox=(),
    )


def _run_course_compose(
    job: CourseComposeJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        existing = operation_status(
            catalog,
            operation_id=job.operation_id,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            session_id=session_id,
        )
        if existing.status == "committed":
            if existing.request_digest != job.request_digest:
                raise OperationConflict("course composition operation digest changed")
            return (
                _mutation_operation_result(existing),
                _knowledge_operation_evidence(
                    code="course-compose",
                    status=existing.status,
                    subject_version_id=str(
                        existing.result_refs.get("outlineVersionId") or ""
                    )
                    or None,
                ),
            )
        requirement = job.requirement.as_domain()
        prepared = prepare_authoritative_composition(
            catalog,
            KnowledgeRetriever(catalog),
            requirement,
            logical_id=job.outline_logical_id,
            version_id=job.outline_version_id,
            revision=job.outline_revision,
            created_at=committed_at,
            created_by=actor,
            options=job.options.as_domain(),
        )
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _compose_course_mutation(
                catalog,
                job=job,
                actor=actor,
                committed_at=committed_at,
                prepared=prepared,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="course-compose",
            status=outcome.status,
            subject_version_id=str(outcome.result_refs.get("outlineVersionId") or "")
            or None,
        ),
    )


def _compose_course_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: CourseComposeJob,
    actor: ActorRef,
    committed_at: datetime,
    prepared: CompositionResult,
) -> OperationMutationResult:
    requirement = job.requirement.as_domain()
    stored_requirement = catalog.register_course_requirement(
        requirement, clock=lambda: committed_at
    )
    if stored_requirement.payload != requirement:
        raise CatalogReferenceError("course requirement bytes changed")
    if (
        prepared.outline.logical_id != job.outline_logical_id
        or prepared.outline.version_id != job.outline_version_id
        or prepared.outline.revision != job.outline_revision
        or prepared.outline.created_by != actor
        or prepared.outline.created_at != committed_at
    ):
        raise CatalogReferenceError("prepared course composition identity changed")
    composed = register_prepared_composition(catalog, requirement, prepared)
    summary = confirmation_summary(composed.outline, requirement)
    return OperationMutationResult(
        result_refs={
            "requirementId": requirement.requirement_id,
            "outlineVersionId": composed.outline.version_id,
            "outlineDigest": composed.outline.content_digest,
            "indexSnapshotId": composed.outline.index_snapshot_id,
            "blockingGaps": list(composed.blocking_gaps),
            "compositionEvidenceId": composed.composition_evidence.evidence_id,
            "retrievalEvidenceIds": list(composed.retrieval_evidence_ids),
            "confirmationSummary": summary.model_dump(mode="json"),
            "outline": composed.outline.model_dump(mode="json"),
        },
        item_outcomes=(),
        index_outbox=(),
    )


def _run_course_outline_confirm(
    job: CourseOutlineConfirmJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _confirm_outline_mutation(
                catalog,
                job=job,
                actor=actor,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="course-outline-confirm",
            status=outcome.status,
            subject_version_id=str(outcome.result_refs.get("courseVersionId") or "")
            or None,
        ),
    )


def _confirm_outline_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: CourseOutlineConfirmJob,
    actor: ActorRef,
    committed_at: datetime,
) -> OperationMutationResult:
    stored_requirement = catalog.get_course_requirement(job.requirement_id)
    stored_outline = catalog.get_course_outline(job.outline_version_id)
    if stored_requirement is None or stored_outline is None:
        raise CatalogReferenceError("course requirement or outline is unavailable")
    requirement = stored_requirement.payload
    outline = stored_outline.payload
    expected_summary = confirmation_summary(outline, requirement)
    if (
        outline.requirement_id != job.requirement_id
        or outline.content_digest != job.expected_outline_digest
        or expected_summary.confirmation_digest != job.confirmation_digest
    ):
        raise CatalogReferenceError("course outline confirmation is stale")
    confirmation = catalog.confirm_course_outline(
        OutlineConfirmation(
            confirmation_id=job.confirmation_id,
            requirement_id=job.requirement_id,
            outline_version_id=job.outline_version_id,
            expected_outline_digest=job.expected_outline_digest,
            confirmation_digest=job.confirmation_digest,
            confirmed_by=actor,
        ),
        clock=lambda: committed_at,
    ).payload
    placement_ids = tuple(
        placement.placement_id
        for chapter in outline.chapters
        for placement in chapter.placements
    )
    seed = CourseVersion(
        logical_id=job.course_logical_id,
        version_id=job.course_version_id,
        revision=job.course_revision,
        content_digest="0" * 64,
        created_at=committed_at,
        created_by=actor,
        requirement_id=requirement.requirement_id,
        outline_version_id=outline.version_id,
        outline_digest=outline.content_digest,
        placement_ids=placement_ids,
        usage_scope=requirement.usage_scope,
        confirmation_digest=confirmation.confirmation_digest,
        status="confirmed",
    )
    course = seed.model_copy(
        update={"content_digest": course_version_content_digest(seed)}
    )
    stored_course = catalog.register_course_version(
        course, clock=lambda: committed_at
    ).payload
    draft = build_and_register_draft(
        catalog,
        stored_course.version_id,
        actor=actor,
        clock=lambda: committed_at,
    )
    return OperationMutationResult(
        result_refs={
            "confirmationId": confirmation.confirmation_id,
            "confirmationDigest": confirmation.confirmation_digest,
            "courseVersionId": stored_course.version_id,
            "courseDigest": stored_course.content_digest,
            "courseStatus": stored_course.status,
            "outlineVersionId": outline.version_id,
            "outlineDigest": outline.content_digest,
            "placementIds": list(stored_course.placement_ids),
            "usageScope": stored_course.usage_scope,
            "slideDeckId": draft.deck.version_id,
            "runtimeManifestId": draft.runtime_manifest.version_id,
            "slideDeck": draft.deck.model_dump(mode="json"),
        },
        item_outcomes=(),
        index_outbox=(),
    )


def _run_chart_build(
    job: ChartBuildJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _build_chart_mutation(
                catalog,
                job=job,
                config=config,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="chart-build",
            status=outcome.status,
        ),
    )


def _build_chart_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: ChartBuildJob,
    config: WorkerRuntimeConfig,
    committed_at: datetime,
) -> OperationMutationResult:
    roots = {root_id: Path(path) for root_id, path in config.source_roots}
    governed_root = Path(config.app_data_path) / "source-blobs"
    governed_root.mkdir(parents=True, exist_ok=True)
    configured = roots.get("governed-upload")
    if configured is not None and configured.resolve() != governed_root.resolve():
        raise CatalogReferenceError("governed dataset root conflicts with runtime")
    roots["governed-upload"] = governed_root
    profiler = DatasetProfiler(SourceRootRegistry(roots))
    outcomes = build_dataset_charts(
        catalog,
        profiler,
        ArtifactStore(Path(config.app_data_path) / "artifacts"),
        tuple(item.as_domain() for item in job.specs),
        clock=lambda: committed_at,
    )
    items: list[dict[str, Any]] = []
    item_outcomes: list[OperationItemOutcome] = []
    for outcome in outcomes:
        if outcome.status == "materialized" and outcome.materialization is not None:
            value = outcome.materialization
            items.append(
                {
                    "requestId": outcome.request_id,
                    "status": outcome.status,
                    "artifactId": value.artifact.artifact_id,
                    "visualVersionId": value.visual.version_id,
                    "evidenceId": value.evidence.evidence_id,
                    "reused": outcome.reused,
                }
            )
            item_outcomes.append(
                OperationItemOutcome(
                    item_id=outcome.request_id,
                    status="committed",
                )
            )
        else:
            items.append(
                {
                    "requestId": outcome.request_id,
                    "status": "failed",
                    "errorCode": outcome.error_code or "CHART_REJECTED",
                }
            )
            item_outcomes.append(
                OperationItemOutcome(
                    item_id=outcome.request_id,
                    status="rolled-back",
                    error_code=outcome.error_code or "CHART_REJECTED",
                )
            )
    return OperationMutationResult(
        result_refs={"items": items},
        item_outcomes=tuple(item_outcomes),
        index_outbox=(),
    )


def _network_provider(config: WorkerRuntimeConfig) -> WikimediaApiClient:
    if config.network_fixture:
        if os.environ.get("COURSE_E2E_FIXTURE") != "1":
            raise NetworkVisualError("NETWORK_FIXTURE_DENIED", "E2E fixture is not authorized")
        from course_helper.e2e_network_fixture import fixture_network_provider

        return fixture_network_provider()
    return WikimediaApiClient(PinnedHttpsTransport())


def _run_visual_search(
    job: VisualSearchJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _visual_search_mutation(
                catalog,
                job=job,
                config=config,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="visual-search", status=outcome.status
        ),
    )


def _visual_search_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: VisualSearchJob,
    config: WorkerRuntimeConfig,
    committed_at: datetime,
) -> OperationMutationResult:
    candidates = discover_network_visuals(
        catalog,
        _network_provider(config),
        query=job.query,
        limit=job.limit,
        clock=lambda: committed_at,
    )
    return OperationMutationResult(
        result_refs={
            "items": [
                {
                    "candidateId": item.candidate_id,
                    "fileTitle": item.file_title,
                    "mediaType": item.media_type,
                    "width": item.width,
                    "height": item.height,
                    "licenseId": item.license_id,
                    "expiresAt": item.expires_at.isoformat(),
                }
                for item in candidates
            ]
        },
        item_outcomes=tuple(
            OperationItemOutcome(item_id=item.candidate_id, status="committed")
            for item in candidates
        ),
        index_outbox=(),
    )


def _run_visual_acquire(
    job: VisualAcquireJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _visual_acquire_mutation(
                catalog,
                job=job,
                config=config,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="visual-acquire", status=outcome.status
        ),
    )


def _visual_acquire_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: VisualAcquireJob,
    config: WorkerRuntimeConfig,
    committed_at: datetime,
) -> OperationMutationResult:
    outcomes = acquire_network_visuals(
        catalog,
        _network_provider(config),
        ArtifactStore(Path(config.app_data_path) / "artifacts"),
        job.candidate_ids,
        clock=lambda: committed_at,
    )
    items: list[dict[str, Any]] = []
    item_outcomes: list[OperationItemOutcome] = []
    for outcome in outcomes:
        if outcome.status == "acquired" and outcome.acquisition is not None:
            acquisition = outcome.acquisition
            items.append(
                {
                    "candidateId": outcome.subject_id,
                    "status": outcome.status,
                    "artifactId": outcome.artifact_id,
                    "visualVersionId": outcome.visual_version_id,
                    "evidenceId": outcome.evidence_id,
                    "reused": outcome.reused,
                    "landingLink": acquisition.landing_link.model_dump(mode="json"),
                    "licenseLink": acquisition.license_link.model_dump(mode="json"),
                }
            )
            item_outcomes.append(
                OperationItemOutcome(item_id=outcome.subject_id, status="committed")
            )
        else:
            error_code = outcome.error_code or "VISUAL_REJECTED"
            items.append(
                {
                    "candidateId": outcome.subject_id,
                    "status": "failed",
                    "errorCode": error_code,
                }
            )
            item_outcomes.append(
                OperationItemOutcome(
                    item_id=outcome.subject_id,
                    status="rolled-back",
                    error_code=error_code,
                )
            )
    return OperationMutationResult(
        result_refs={"items": items},
        item_outcomes=tuple(item_outcomes),
        index_outbox=(),
    )


def _run_visual_revalidate(
    job: VisualRevalidateJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _visual_revalidate_mutation(
                catalog,
                job=job,
                config=config,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="visual-revalidate",
            status=outcome.status,
            subject_version_id=job.visual_version_id,
        ),
    )


def _visual_revalidate_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: VisualRevalidateJob,
    config: WorkerRuntimeConfig,
    committed_at: datetime,
) -> OperationMutationResult:
    outcome = revalidate_network_visual(
        catalog,
        _network_provider(config),
        visual_version_id=job.visual_version_id,
        clock=lambda: committed_at,
    )
    if outcome.status == "revalidated" and outcome.verification is not None:
        verification = outcome.verification
        item = {
            "visualVersionId": job.visual_version_id,
            "status": outcome.status,
            "verificationStatus": verification.status,
            "evidenceId": outcome.evidence_id,
            "verifiedAt": verification.verified_at.isoformat(),
            "expiresAt": verification.expires_at.isoformat(),
            "revision": verification.revision,
        }
        item_outcome = OperationItemOutcome(
            item_id=job.visual_version_id, status="committed"
        )
    else:
        error_code = outcome.error_code or "VISUAL_REVALIDATION_REJECTED"
        item = {
            "visualVersionId": job.visual_version_id,
            "status": "failed",
            "errorCode": error_code,
        }
        item_outcome = OperationItemOutcome(
            item_id=job.visual_version_id,
            status="rolled-back",
            error_code=error_code,
        )
    return OperationMutationResult(
        result_refs={"item": item},
        item_outcomes=(item_outcome,),
        index_outbox=(),
    )


def _run_course_visual_attach(
    job: CourseVisualAttachJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _attach_visual_mutation(
                catalog,
                job=job,
                actor=actor,
                committed_at=committed_at,
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="course-visual-attach",
            status=outcome.status,
            subject_version_id=job.placement_id,
        ),
    )


def _stored_visual(catalog: KnowledgeCatalog, visual_version_id: str) -> VisualAssetVersion:
    row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM visuals WHERE version_id = ?",
        (visual_version_id,),
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("visual version is unavailable")
    visual = VisualAssetVersion.model_validate_json(str(row[1]), strict=False)
    if (
        canonical_model_json(visual) != str(row[1])
        or visual.version_id != visual_version_id
        or visual.content_digest != str(row[0])
    ):
        raise CatalogReferenceError("visual version envelope is invalid")
    return visual


def _visual_placement_authority(
    catalog: KnowledgeCatalog,
    visual: VisualAssetVersion,
    *,
    now: datetime,
    alt_text: str,
) -> tuple[str, str, AttributionBlock]:
    network = catalog.connection.execute(
        "SELECT payload_json FROM network_visual_acquisitions WHERE visual_version_id = ?",
        (visual.version_id,),
    ).fetchone()
    if network is not None:
        acquisition = NetworkVisualAcquisition.model_validate_json(
            str(network[0]), strict=False
        )
        if canonical_model_json(acquisition) != str(network[0]):
            raise CatalogReferenceError("network visual acquisition is invalid")
        verification = current_network_visual_verification(
            catalog, visual.version_id, now=now
        )
        return (
            verification.evidence_id,
            acquisition.evidence_id,
            AttributionBlock(
                title=acquisition.title,
                creator=acquisition.creator,
                publisher="Wikimedia Commons",
                license_label=acquisition.license_id,
                landing_link=acquisition.landing_link,
                license_link=acquisition.license_link,
            ),
        )
    source_row = catalog.connection.execute(
        "SELECT payload_json FROM source_visual_artifacts WHERE visual_version_id = ?",
        (visual.version_id,),
    ).fetchone()
    if source_row is not None:
        materialization = SourceVisualMaterialization.model_validate_json(
            str(source_row[0]), strict=False
        )
        if canonical_model_json(materialization) != str(source_row[0]):
            raise CatalogReferenceError("source visual materialization is invalid")
        source = catalog.get_source(materialization.source_version_id)
        if source is None:
            raise CatalogReferenceError("source visual origin is unavailable")
        return (
            materialization.evidence_id,
            materialization.evidence_id,
            AttributionBlock(
                title=alt_text,
                creator=visual.author,
                publisher=visual.publisher,
                license_label=visual.license_status,
            ),
        )
    data_rows = catalog.connection.execute(
        "SELECT evidence_id, to_version_id FROM lineage "
        "WHERE from_version_id = ? AND relation = 'derived_from'",
        (visual.version_id,),
    ).fetchall()
    if visual.authenticity == "data-derived" and len(data_rows) == 1:
        evidence_id, dataset_version_id = map(str, data_rows[0])
        if catalog.connection.execute(
            "SELECT 1 FROM datasets WHERE version_id = ?", (dataset_version_id,)
        ).fetchone() is None:
            raise CatalogReferenceError("data visual dataset is unavailable")
        return (
            evidence_id,
            evidence_id,
            AttributionBlock(
                title=alt_text,
                creator=visual.author,
                publisher=visual.publisher,
                license_label=visual.license_status,
            ),
        )
    raise CatalogReferenceError("visual provenance is not attachable")


def _attach_visual_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: CourseVisualAttachJob,
    actor: ActorRef,
    committed_at: datetime,
) -> OperationMutationResult:
    stored_course = catalog.get_course_version(job.course_version_id)
    if (
        stored_course is None
        or stored_course.payload.content_digest != job.expected_course_digest
        or stored_course.payload.status not in {"confirmed", "published"}
    ):
        raise CatalogReferenceError("course visual attachment is stale")
    draft_course = stored_course.payload
    seen: set[str] = set()
    while draft_course.status == "published":
        if (
            draft_course.version_id in seen
            or draft_course.supersedes_version_id is None
        ):
            raise CatalogReferenceError("course publication ancestry is invalid")
        seen.add(draft_course.version_id)
        parent = catalog.get_course_version(draft_course.supersedes_version_id)
        if parent is None:
            raise CatalogReferenceError("course publication ancestry is unavailable")
        draft_course = parent.payload
    if draft_course.status != "confirmed":
        raise CatalogReferenceError("course has no confirmed visual root")
    draft = build_and_register_draft(
        catalog,
        draft_course.version_id,
        actor=actor,
        clock=lambda: committed_at,
    )
    nodes = []
    stack = list(reversed(draft.deck.nodes))
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(reversed(node.children))
    target = next((item for item in nodes if item.node_id == job.slide_node_id), None)
    if target is None:
        raise CatalogReferenceError("visual placement target is unavailable")
    if (
        job.originating_card_version_id is not None
        and job.originating_card_version_id not in target.card_version_ids
    ) or (
        job.originating_source_version_id is not None
        and job.originating_source_version_id not in target.source_version_ids
    ):
        raise CatalogReferenceError("visual placement origin is outside the target slide")
    visual = _stored_visual(catalog, job.visual_version_id)
    authenticity_id, license_id, attribution = _visual_placement_authority(
        catalog, visual, now=committed_at, alt_text=job.alt_text
    )
    placement = VisualPlacement(
        placement_id=job.placement_id,
        visual_version_id=job.visual_version_id,
        slide_node_id=job.slide_node_id,
        slot_id=job.slot_id,
        fit=job.fit,
        crop=None if job.crop is None else job.crop.as_domain(),
        alt_text=job.alt_text,
        authenticity_evidence_id=authenticity_id,
        license_evidence_id=license_id,
        attribution=attribution,
        transformation=job.transformation.as_domain(),
        originating_card_version_id=job.originating_card_version_id,
        originating_source_version_id=job.originating_source_version_id,
        originating_dataset_version_id=job.originating_dataset_version_id,
    )
    stored = catalog.register_visual_placement(
        placement, clock=lambda: committed_at
    ).payload
    return OperationMutationResult(
        result_refs={
            "placementId": stored.placement_id,
            "visualVersionId": stored.visual_version_id,
            "slideNodeId": stored.slide_node_id,
            "slotId": stored.slot_id,
            "attribution": stored.attribution.model_dump(mode="json"),
            "transformation": stored.transformation.model_dump(mode="json"),
        },
        item_outcomes=(
            OperationItemOutcome(item_id=stored.placement_id, status="committed"),
        ),
        index_outbox=(),
    )


def _run_course_visual_detach(
    job: CourseVisualDetachJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _detach_visual_mutation(catalog, job=job),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="course-visual-detach",
            status=outcome.status,
            subject_version_id=job.placement_id,
        ),
    )


def _detach_visual_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: CourseVisualDetachJob,
) -> OperationMutationResult:
    course = catalog.get_course_version(job.course_version_id)
    if course is None or course.payload.content_digest != job.expected_course_digest:
        raise CatalogReferenceError("course visual detach request is stale")
    if any(catalog.get_visual_placement(item) is None for item in job.active_placement_ids):
        raise CatalogReferenceError("active visual placement set is unavailable")
    remaining = tuple(
        item for item in job.active_placement_ids if item != job.placement_id
    )
    return OperationMutationResult(
        result_refs={
            "detachedPlacementId": job.placement_id,
            "activePlacementIds": list(remaining),
        },
        item_outcomes=(
            OperationItemOutcome(item_id=job.placement_id, status="committed"),
        ),
        index_outbox=(),
    )


def _run_course_validate(
    job: CourseValidateJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = run_operation(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            lambda: _validate_course_mutation(
                catalog, job=job, actor=actor, committed_at=committed_at
            ),
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="course-validate", status=outcome.status
        ),
    )


def _validate_course_mutation(
    catalog: KnowledgeCatalog,
    *,
    job: CourseValidateJob,
    actor: ActorRef,
    committed_at: datetime,
) -> OperationMutationResult:
    validation = validate_course_version(
        catalog,
        job.course_version_id,
        expected_course_digest=job.expected_course_digest,
        visual_placement_ids=job.visual_placement_ids,
        actor=actor,
        clock=lambda: committed_at,
    )
    return OperationMutationResult(
        result_refs={
            "validationStatus": "passed",
            "courseVersionId": validation.course.version_id,
            "courseDigest": validation.course.content_digest,
            "slideDeckId": validation.deck.version_id,
            "runtimeManifestId": validation.runtime_manifest.version_id,
            "runtimeManifestDigest": validation.runtime_manifest.content_digest,
            "courseProjectionId": validation.course_projection_id,
            "warnings": list(validation.warnings),
            "slideDeck": validation.deck.model_dump(mode="json"),
            "runtimeManifest": validation.runtime_manifest.model_dump(mode="json"),
        },
        item_outcomes=(),
        index_outbox=(),
    )


def _run_course_publish(
    job: CoursePublishJob,
    config: WorkerRuntimeConfig,
    session_id: str,
) -> tuple[dict[str, Any], EvidenceObject]:
    actor = job.actor.as_domain()
    committed_at = datetime.now(timezone.utc)
    with KnowledgeCatalog.open(config.database_path) as catalog:
        outcome = publish_course_version(
            catalog,
            OperationRequest(
                operation_id=job.operation_id,
                request_digest=job.request_digest,
                actor=actor,
                session_id=session_id,
            ),
            confirmed_course_version_id=job.course_version_id,
            expected_course_digest=job.expected_course_digest,
            visual_placement_ids=job.visual_placement_ids,
            clock=lambda: committed_at,
        )
    return (
        _mutation_operation_result(outcome),
        _knowledge_operation_evidence(
            code="course-publish",
            status=outcome.status,
            subject_version_id=str(outcome.result_refs.get("courseVersionId") or "")
            or None,
        ),
    )


_ALLOWLISTED_HANDLERS = {
    "source_ingest": _run_source_ingest,
    "dataset_profile": _run_dataset_profile,
    "knowledge_retrieve": _run_knowledge_retrieve,
    "knowledge_publish": _run_knowledge_publish,
    "knowledge_import_start": _run_knowledge_import_start,
    "knowledge_import_status": _run_knowledge_import_status,
    "knowledge_import_cancel": _run_knowledge_import_cancel,
    "operation_status": _run_operation_status,
    "knowledge_review_list": _run_knowledge_review_list,
    "knowledge_review_detail": _run_knowledge_review_detail,
    "knowledge_upgrade_list": _run_knowledge_upgrade_list,
    "knowledge_review_resolve": _run_knowledge_review_resolve,
    "knowledge_card_publish": _run_knowledge_card_publish,
    "knowledge_upgrade_resolve": _run_knowledge_upgrade_resolve,
    "knowledge_index": _run_knowledge_index,
    "course_compose": _run_course_compose,
    "course_outline_confirm": _run_course_outline_confirm,
    "chart_build": _run_chart_build,
    "visual_search": _run_visual_search,
    "visual_acquire": _run_visual_acquire,
    "visual_revalidate": _run_visual_revalidate,
    "course_visual_attach": _run_course_visual_attach,
    "course_visual_detach": _run_course_visual_detach,
    "course_validate": _run_course_validate,
    "course_publish": _run_course_publish,
}


def _camelize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {_lower_camel(str(key)): _camelize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_camelize_json(item) for item in value]
    return value
