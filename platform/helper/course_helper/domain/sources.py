"""Contracts for registered sources and deterministic extraction results."""

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

from course_helper.domain.common import ImmutableJsonValue, OpaqueId, SourceLocator, VersionMeta, freeze_json, thaw_json
from course_helper.domain.composition import UsageScope
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.visual_policy import TrustedExternalLink, canonical_external_url_identity


class ChunkLocator(BaseModel):
    """Typed position inside a source asset."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal[
        "pptx-slide",
        "pptx-notes",
        "markdown-section",
        "markdown-block",
        "table-region",
        "dataset-field",
    ]
    slide_number: int | None = Field(default=None, ge=1)
    relationship_id: str | None = None
    ast_path: tuple[int, ...] = ()
    heading_path: tuple[str, ...] = ()
    sheet_name: str | None = None
    cell_range: str | None = None
    field_name: str | None = None

class SourceAssetVersion(VersionMeta):
    """Immutable registration and parse state for one source version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    locator: SourceLocator
    display_name: str = Field(min_length=1)
    source_kind: Literal[
        "pptx",
        "markdown",
        "csv",
        "parquet",
        "xls",
        "xlsx",
        "duckdb",
        "image",
        "other",
    ]
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    modified_at: datetime | None = None
    content_summary: str | None = None
    extraction_status: Literal["registered", "parsing", "parsed", "partial", "failed", "unsupported"]
    parser_name: str | None = None
    parser_version: str | None = None
    parser_config_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ExtractedChunk(BaseModel):
    """Ordered, addressable content extracted from a concrete source version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    chunk_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    modality: Literal["slide", "notes", "text", "code", "table", "image", "dataset"]
    language: str = Field(min_length=1)
    normalized_text: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: ChunkLocator
    breadcrumb: tuple[str, ...] = ()
    heading: str | None = None
    notes_text: str = ""
    slide_text: str = ""
    code_blocks: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()
    media_version_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class VisualAssetServerProvenance(BaseModel):
    """Server-only compatibility view of legacy schema-v1 URL fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    visual_version_id: OpaqueId
    landing_page_url: str | None = None
    asset_url: str | None = None
    acquired_at: datetime | None = None


class VisualAssetBrowserView(BaseModel):
    """Bounded browser projection; URL fields exist only in typed trusted links."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    visual_version_id: OpaqueId
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    alt_text: str = ""
    publisher: str | None = None
    author: str | None = None
    license_status: Literal["verified", "source-provided", "licensed", "generated", "unknown", "restricted"]
    authenticity: Literal[
        "official-primary",
        "source-provided",
        "data-derived",
        "licensed-secondary",
        "generated",
        "unverified",
    ]
    trusted_links: tuple[TrustedExternalLink, ...] = ()

    @model_validator(mode="after")
    def unique_links(self) -> VisualAssetBrowserView:
        if len({link.link_id for link in self.trusted_links}) != len(self.trusted_links):
            raise ValueError("trusted link IDs must be unique")
        if len({link.link_type for link in self.trusted_links}) != len(self.trusted_links):
            raise ValueError("at most one trusted link is allowed for each role")
        return self


class VisualAssetVersion(VersionMeta):
    """Immutable visual metadata; the binary remains outside browser state."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_id: OpaqueId
    version_id: OpaqueId
    media_type: str = Field(min_length=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    alt_text: str = ""
    source_locator: ChunkLocator | None = None
    landing_page_url: str | None = None
    asset_url: str | None = None
    publisher: str | None = None
    author: str | None = None
    acquired_at: datetime | None = None
    license_status: Literal["verified", "source-provided", "licensed", "generated", "unknown", "restricted"]
    authenticity: Literal[
        "official-primary",
        "source-provided",
        "data-derived",
        "licensed-secondary",
        "generated",
        "unverified",
    ]
    derived_from_version_ids: tuple[OpaqueId, ...] = ()
    usage_scope: tuple[UsageScope, ...] = ()

    @field_validator("usage_scope")
    @classmethod
    def unique_usage_scopes(cls, value: tuple[UsageScope, ...]) -> tuple[UsageScope, ...]:
        if len(set(value)) != len(value):
            raise ValueError("usage scope values must be unique")
        return value

    def server_provenance(self) -> VisualAssetServerProvenance:
        """Read immutable legacy URL bytes only inside server-side provenance code."""

        return VisualAssetServerProvenance(
            visual_version_id=self.version_id,
            landing_page_url=self.landing_page_url,
            asset_url=self.asset_url,
            acquired_at=self.acquired_at,
        )

    def to_browser_view(
        self,
        *,
        trusted_links: tuple[TrustedExternalLink, ...] = (),
    ) -> VisualAssetBrowserView:
        """Build the bounded API projection without copying legacy/final media URLs."""

        if self.asset_url is not None:
            final_media_identity = canonical_external_url_identity(self.asset_url)
            if any(
                canonical_external_url_identity(link.href) == final_media_identity
                for link in trusted_links
            ):
                raise ValueError("legacy final media URL cannot become a trusted browser link")
        return VisualAssetBrowserView(
            visual_version_id=self.version_id,
            content_digest=self.content_digest,
            media_type=self.media_type,
            width=self.width,
            height=self.height,
            alt_text=self.alt_text,
            publisher=self.publisher,
            author=self.author,
            license_status=self.license_status,
            authenticity=self.authenticity,
            trusted_links=trusted_links,
        )


class DatasetColumn(BaseModel):
    """Serializable schema and profile facts for one dataset column."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    nullable: bool
    missing_count: int | None = Field(default=None, ge=0)
    missing_rate: float | None = Field(default=None, ge=0, le=1)
    distinct_count: int | None = Field(default=None, ge=0)
    sensitive_category: str | None = None


class DatasetAssetVersion(VersionMeta):
    """Bounded metadata profile for a dataset version, never the raw dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    locator: SourceLocator
    format: Literal["csv", "parquet", "xls", "xlsx", "duckdb"]
    row_count: int = Field(ge=0)
    columns: tuple[DatasetColumn, ...]
    sheets: tuple[str, ...] = ()
    grain: str = Field(min_length=1)
    missingness: Mapping[str, float] = Field(default_factory=dict)
    category_tags: tuple[str, ...] = ()
    relation_name: str | None = None
    sample_rows: tuple[Mapping[str, ImmutableJsonValue], ...] = ()
    review_status: Literal["ready", "needs-review", "unsupported"]
    evidence: EvidenceObject

    @field_validator("missingness")
    @classmethod
    def freeze_missingness(cls, value: Mapping[str, float]) -> Mapping[str, float]:
        return cast(Mapping[str, float], freeze_json(value))

    @field_serializer("missingness", mode="wrap")
    def serialize_missingness(
        self,
        value: Mapping[str, float],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, float]:
        return cast(dict[str, float], handler(thaw_json(value)))

    @field_validator("sample_rows")
    @classmethod
    def freeze_sample_rows(
        cls,
        value: tuple[Mapping[str, ImmutableJsonValue], ...],
    ) -> tuple[Mapping[str, ImmutableJsonValue], ...]:
        return tuple(cast(Mapping[str, ImmutableJsonValue], freeze_json(row)) for row in value)

    @field_serializer("sample_rows", mode="wrap")
    def serialize_sample_rows(
        self,
        value: tuple[Mapping[str, ImmutableJsonValue], ...],
        handler: SerializerFunctionWrapHandler,
    ) -> tuple[dict[str, ImmutableJsonValue], ...] | list[dict[str, ImmutableJsonValue]]:
        rows = tuple(cast(dict[str, ImmutableJsonValue], thaw_json(row)) for row in value)
        return cast(
            tuple[dict[str, ImmutableJsonValue], ...] | list[dict[str, ImmutableJsonValue]],
            handler(rows),
        )


class ExtractionResult(BaseModel):
    """Atomic structured result returned by source parsers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: SourceAssetVersion
    chunks: tuple[ExtractedChunk, ...] = ()
    visuals: tuple[VisualAssetVersion, ...] = ()
    datasets: tuple[DatasetAssetVersion, ...] = ()
    evidence: EvidenceObject
