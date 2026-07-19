"""Versioned authenticity, licensing, and visual-placement policy contracts."""

from __future__ import annotations

import posixpath
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from course_helper.domain.composition import UsageScope


_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
AuthenticityClass = Literal[
    "official-primary",
    "source-provided",
    "data-derived",
    "licensed-secondary",
    "generated",
    "unverified",
]
LicenseStatus = Literal[
    "verified",
    "source-provided",
    "licensed",
    "generated",
    "unknown",
    "restricted",
]


def canonical_external_url_identity(
    value: str,
) -> tuple[str, str, int | None, str, tuple[tuple[str, str], ...]]:
    """Normalize a provenance URL for deny-list identity comparisons."""

    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("external URL contains invalid percent encoding")
    parsed = urlsplit(value)
    if not parsed.scheme or parsed.hostname is None:
        raise ValueError("external URL must be absolute")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise ValueError("external URL authority is invalid") from error
    scheme = parsed.scheme.lower()
    if (scheme, port) in {("https", 443), ("http", 80)}:
        port = None
    decoded_path = unicodedata.normalize("NFC", unquote(parsed.path))
    normalized_path = posixpath.normpath(decoded_path or "/")
    if decoded_path.startswith("/") and not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    if decoded_path.endswith("/") and normalized_path != "/":
        normalized_path = f"{normalized_path}/"
    normalized_query = tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (scheme, hostname, port, normalized_path, normalized_query)


class TrustedExternalLink(BaseModel):
    """The sole typed URL-bearing projection allowed in browser/API payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    link_id: str = Field(pattern=_SAFE_ID_PATTERN)
    link_type: Literal["landing", "license"]
    href: str = Field(min_length=1, max_length=2048)
    provenance_kind: Literal["official-primary", "source-provided", "licensed-secondary"]
    label: str = Field(min_length=1, max_length=200)

    @field_validator("href")
    @classmethod
    def safe_https_link(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("trusted external links must use absolute HTTPS URLs")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("trusted external links cannot contain credentials")
        if parsed.fragment:
            raise ValueError("trusted external links cannot contain fragments")
        if any(character.isspace() for character in value) or "\x00" in value:
            raise ValueError("trusted external links contain invalid characters")
        canonical_external_url_identity(value)
        return value


class CropRect(BaseModel):
    """A normalized crop rectangle bound to immutable placement metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stays_inside_asset(self) -> CropRect:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("crop rectangle must stay inside normalized asset bounds")
        return self


class TransformationManifest(BaseModel):
    """Auditable transformation and derivative-license decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    transformation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    crop: CropRect | None = None
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

    @field_validator("color_adjustments")
    @classmethod
    def bounded_adjustments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 200 for item in value):
            raise ValueError("color adjustments must be non-blank and bounded")
        if len(set(value)) != len(value):
            raise ValueError("color adjustments must be unique")
        return value

    @model_validator(mode="after")
    def coherent_license_decision(self) -> TransformationManifest:
        full_crop = self.crop is not None and (
            self.crop.x,
            self.crop.y,
            self.crop.width,
            self.crop.height,
        ) == (0.0, 0.0, 1.0, 1.0)
        content_changed = (
            (self.crop is not None and not full_crop)
            or bool(self.color_adjustments)
            or self.scale_mode == "cover"
        )
        if content_changed and not self.change_notice:
            raise ValueError("content-changing transformations require a change notice")
        if self.derivative_license_decision in {"same-license", "compatible-license"} and not self.export_license:
            raise ValueError("derivative license decisions require an export license")
        if self.derivative_license_decision == "prohibited" and any(
            (
                self.share_alike_compatible,
                self.gfdl_compatible,
                self.no_derivatives_compatible,
            )
        ):
            raise ValueError("prohibited derivative decision cannot be marked compatible")
        if content_changed and self.no_derivatives_compatible:
            raise ValueError("content-changing transformations are not no-derivatives compatible")
        return self


class AttributionBlock(BaseModel):
    """Visible attribution whose links are already server-validated projections."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=500)
    creator: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    license_label: str = Field(min_length=1, max_length=200)
    landing_link: TrustedExternalLink | None = None
    license_link: TrustedExternalLink | None = None

    @model_validator(mode="after")
    def links_match_their_roles(self) -> AttributionBlock:
        if self.landing_link is not None and self.landing_link.link_type != "landing":
            raise ValueError("landing_link must have landing link_type")
        if self.license_link is not None and self.license_link.link_type != "license":
            raise ValueError("license_link must have license link_type")
        return self


class VisualPlacement(BaseModel):
    """Immutable binding from a visual version to one slide slot and lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    placement_id: str = Field(pattern=_SAFE_ID_PATTERN)
    visual_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    slide_node_id: str = Field(pattern=_SAFE_ID_PATTERN)
    slot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    fit: Literal["contain", "cover", "fill"]
    crop: CropRect | None = None
    alt_text: str = Field(min_length=1, max_length=1000)
    authenticity_evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    license_evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attribution: AttributionBlock
    transformation: TransformationManifest
    originating_card_version_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    originating_source_version_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    originating_dataset_version_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)

    @model_validator(mode="after")
    def requires_lineage(self) -> VisualPlacement:
        if not any(
            (
                self.originating_card_version_id,
                self.originating_source_version_id,
                self.originating_dataset_version_id,
            )
        ):
            raise ValueError("visual placement requires originating card/source/dataset lineage")
        if self.crop != self.transformation.crop:
            raise ValueError("visual placement crop must match its transformation crop")
        return self


class AuthenticityPolicy(BaseModel):
    """Pinned authenticity ranking and public network-freshness policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    policy_id: Literal["course-studio-authenticity-v1"] = "course-studio-authenticity-v1"
    selection_order: tuple[AuthenticityClass, ...] = (
        "official-primary",
        "source-provided",
        "data-derived",
        "licensed-secondary",
        "generated",
    )
    network_metadata_ttl_hours: Literal[24] = 24

    @model_validator(mode="after")
    def exact_order(self) -> AuthenticityPolicy:
        expected = (
            "official-primary",
            "source-provided",
            "data-derived",
            "licensed-secondary",
            "generated",
        )
        if self.selection_order != expected:
            raise ValueError("authenticity selection order is policy-pinned")
        return self


class LicensePolicy(BaseModel):
    """Pinned scope rules for authorization and warning behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    policy_id: Literal["course-studio-license-v1"] = "course-studio-license-v1"
    public_requires_verified_authorization: Literal[True] = True
    internal_requires_rights_disposition: Literal[True] = True
    private_unknown_rights_warning: Literal[True] = True


class VisualPolicyContext(BaseModel):
    """Evidence flags used to make one deterministic scope-aware decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    usage_scope: UsageScope
    authenticity: AuthenticityClass
    license_status: LicenseStatus
    rights_verified: bool
    network_metadata_verified_at: datetime | None = None
    dataset_integrity_verified: bool = False
    attribution_verified: bool = False
    generated_asset_existing: bool = True
    generated_labeled: bool = False
    rights_statement_present: bool = False
    lineage_valid: bool = True
    transformation_compatible: bool = True
    now: datetime

    @model_validator(mode="after")
    def aware_times(self) -> VisualPolicyContext:
        if self.now.utcoffset() is None:
            raise ValueError("policy clock must be timezone-aware")
        if (
            self.network_metadata_verified_at is not None
            and self.network_metadata_verified_at.utcoffset() is None
        ):
            raise ValueError("network metadata time must be timezone-aware")
        return self


class VisualPolicyDecision(BaseModel):
    """Typed, deterministic policy outcome suitable for evidence records."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    authenticity_policy_id: Literal["course-studio-authenticity-v1"]
    license_policy_id: Literal["course-studio-license-v1"]
    allowed: bool
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def evaluate_visual_publication(context: VisualPolicyContext) -> VisualPolicyDecision:
    """Apply the v1 scope matrix without network access or mutable state."""

    warnings: list[str] = []
    blockers: list[str] = []

    if not context.lineage_valid:
        _append_unique(blockers, "LINEAGE_INVALID")
    if not context.transformation_compatible:
        _append_unique(blockers, "TRANSFORMATION_LICENSE_INCOMPATIBLE")
    if context.license_status == "restricted":
        _append_unique(blockers, "LICENSE_RESTRICTED")
    if context.usage_scope == "public" and not context.rights_verified:
        _append_unique(blockers, "VERIFIED_AUTHORIZATION_REQUIRED")

    if context.authenticity in {"official-primary", "source-provided", "unverified"}:
        if not context.rights_verified:
            if context.usage_scope == "private-training":
                _append_unique(warnings, "SOURCE_RIGHTS_UNVERIFIED")
            else:
                _append_unique(blockers, "VERIFIED_AUTHORIZATION_REQUIRED")
        if context.authenticity == "unverified" and context.usage_scope != "private-training":
            _append_unique(blockers, "AUTHENTICITY_UNVERIFIED")

    if context.authenticity == "licensed-secondary":
        verified_at = context.network_metadata_verified_at
        metadata_is_current = (
            verified_at is not None
            and verified_at <= context.now
            and context.now - verified_at <= timedelta(hours=24)
        )
        if not metadata_is_current:
            code = "NETWORK_METADATA_EXPIRED" if verified_at is not None else "NETWORK_METADATA_REQUIRED"
            if context.usage_scope == "private-training":
                _append_unique(warnings, code)
            else:
                _append_unique(blockers, code)
        if not context.rights_verified:
            if context.usage_scope == "private-training":
                _append_unique(warnings, "SOURCE_RIGHTS_UNVERIFIED")
            else:
                _append_unique(blockers, "VERIFIED_AUTHORIZATION_REQUIRED")

    if context.authenticity == "data-derived":
        if not context.dataset_integrity_verified:
            _append_unique(blockers, "DATASET_INTEGRITY_REQUIRED")
        if context.usage_scope == "public" and not context.attribution_verified:
            _append_unique(blockers, "ATTRIBUTION_REQUIRED")

    if context.authenticity == "generated":
        if not context.generated_asset_existing:
            _append_unique(blockers, "GENERATED_ASSET_MUST_ALREADY_EXIST")
        if not context.generated_labeled:
            _append_unique(blockers, "GENERATED_LABEL_REQUIRED")
        if not context.rights_statement_present:
            _append_unique(blockers, "RIGHTS_STATEMENT_REQUIRED")
        if context.usage_scope == "public" and not context.rights_verified:
            _append_unique(blockers, "VERIFIED_AUTHORIZATION_REQUIRED")

    if context.license_status == "unknown":
        if context.usage_scope == "private-training":
            _append_unique(warnings, "LICENSE_STATUS_UNKNOWN")
        else:
            _append_unique(blockers, "VERIFIED_AUTHORIZATION_REQUIRED")

    return VisualPolicyDecision(
        authenticity_policy_id="course-studio-authenticity-v1",
        license_policy_id="course-studio-license-v1",
        allowed=not blockers,
        warnings=tuple(warnings),
        blockers=tuple(blockers),
    )


__all__ = [
    "AttributionBlock",
    "AuthenticityClass",
    "AuthenticityPolicy",
    "CropRect",
    "LicensePolicy",
    "LicenseStatus",
    "TransformationManifest",
    "TrustedExternalLink",
    "VisualPlacement",
    "VisualPolicyContext",
    "VisualPolicyDecision",
    "canonical_external_url_identity",
    "evaluate_visual_publication",
]
