"""Render-neutral Slide AST and typed runtime-manifest contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from course_helper.domain.common import OpaqueId, VersionMeta
from course_helper.domain.composition import canonical_digest
from course_helper.domain.visual_policy import AttributionBlock, TransformationManifest


_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


def _unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return values


class SlideAssetBinding(BaseModel):
    """Opaque, immutable asset binding; never a local path or browser URL."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    binding_id: str = Field(pattern=_SAFE_ID_PATTERN)
    visual_placement_id: str = Field(pattern=_SAFE_ID_PATTERN)
    visual_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    artifact_id: str = Field(pattern=_SAFE_ID_PATTERN)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/svg+xml"]
    alt_text: str = Field(min_length=1, max_length=1000)
    authenticity_evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    license_evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attribution_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attribution: AttributionBlock
    transformation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    transformation: TransformationManifest

    @model_validator(mode="after")
    def exact_policy_bindings(self) -> SlideAssetBinding:
        expected_attribution_id = "attribution-" + canonical_digest(
            self.attribution.model_dump(mode="json", exclude_none=True)
        )
        if self.attribution_id != expected_attribution_id:
            raise ValueError("asset attribution ID must bind the exact attribution block")
        if self.transformation_id != self.transformation.transformation_id:
            raise ValueError("asset transformation ID must bind the exact transformation")
        return self


class SlideNode(BaseModel):
    """One immutable content node with explicit placement and evidence lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    node_id: str = Field(pattern=_SAFE_ID_PATTERN)
    node_type: Literal[
        "slide",
        "title",
        "heading",
        "paragraph",
        "bullet-list",
        "quote",
        "callout",
        "code",
        "table",
        "visual",
        "activity",
    ]
    text: str | None = Field(default=None, max_length=12000)
    items: tuple[str, ...] = Field(default=(), max_length=100)
    placement_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    card_version_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    chunk_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=100)
    source_version_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=100)
    evidence_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=100)
    presenter_notes: str | None = Field(default=None, max_length=12000)
    asset_bindings: tuple[SlideAssetBinding, ...] = Field(default=(), max_length=20)
    children: tuple[SlideNode, ...] = Field(default=(), max_length=100)

    @field_validator("items")
    @classmethod
    def valid_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 2000 for item in value):
            raise ValueError("slide items must be non-blank and bounded")
        return value

    @field_validator(
        "placement_ids",
        "card_version_ids",
        "chunk_ids",
        "source_version_ids",
        "evidence_ids",
    )
    @classmethod
    def unique_lineage_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "lineage IDs")
        return _unique(value, label=field_name)

    @model_validator(mode="after")
    def unique_direct_bindings(self) -> SlideNode:
        _unique(tuple(binding.binding_id for binding in self.asset_bindings), label="binding IDs")
        _unique(
            tuple(binding.visual_placement_id for binding in self.asset_bindings),
            label="visual placement IDs",
        )
        _unique(
            tuple(binding.artifact_id for binding in self.asset_bindings),
            label="artifact IDs",
        )
        return self


def _walk_nodes(nodes: tuple[SlideNode, ...]) -> tuple[SlideNode, ...]:
    ordered: list[SlideNode] = []
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(node.children))
    return tuple(ordered)


def _raw_node_children(node: object) -> tuple[object, ...]:
    if isinstance(node, SlideNode):
        return tuple(node.children)
    if isinstance(node, Mapping):
        children = node.get("children", ())
        if isinstance(children, (list, tuple)):
            return tuple(children)
    return ()


class SlideDeckAst(VersionMeta):
    """An immutable deck projection shared by editor, stage, and presenter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    course_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    nodes: tuple[SlideNode, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def bounded_raw_tree(cls, value: Any) -> Any:
        if isinstance(value, cls):
            roots: tuple[object, ...] = tuple(value.nodes)
        elif isinstance(value, Mapping):
            candidate = value.get("nodes", ())
            roots = tuple(candidate) if isinstance(candidate, (list, tuple)) else ()
        else:
            return value
        stack: list[tuple[object, int]] = [(node, 1) for node in reversed(roots)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > 500:
                raise ValueError("slide deck cannot exceed 500 total nodes")
            if depth > 32:
                raise ValueError("slide deck node depth cannot exceed 32")
            children = _raw_node_children(node)
            stack.extend((child, depth + 1) for child in reversed(children))
        return value

    @model_validator(mode="after")
    def globally_unique_bindings(self) -> SlideDeckAst:
        nodes = _walk_nodes(self.nodes)
        _unique(tuple(node.node_id for node in nodes), label="node IDs")
        bindings = tuple(binding for node in nodes for binding in node.asset_bindings)
        _unique(tuple(item.binding_id for item in bindings), label="binding IDs")
        _unique(
            tuple(item.visual_placement_id for item in bindings),
            label="visual placement IDs",
        )
        return self


class RuntimeJobBinding(BaseModel):
    """A typed job reference whose executable specification remains Helper-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    job_id: str = Field(pattern=_SAFE_ID_PATTERN)
    job_type: Literal[
        "python_snippet",
        "dataset_sql",
        "chart_build",
        "rag_query",
        "doc_export",
    ]
    spec_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    timeout_seconds: int = Field(ge=1, le=300)


class RuntimeManifest(VersionMeta):
    """Exact runtime dependencies for one course/deck version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    course_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    slide_deck_version_id: str = Field(pattern=_SAFE_ID_PATTERN)
    slide_deck_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_bindings: tuple[RuntimeJobBinding, ...] = Field(default=(), max_length=100)
    artifact_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=500)
    evidence_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=500)

    @field_validator("artifact_ids", "evidence_ids")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _unique(value, label=getattr(info, "field_name", "IDs"))

    @model_validator(mode="after")
    def unique_jobs(self) -> RuntimeManifest:
        _unique(tuple(job.job_id for job in self.job_bindings), label="job IDs")
        return self


def slide_deck_semantic_payload(deck: SlideDeckAst) -> dict[str, Any]:
    """Return the render-neutral draft semantics without version metadata."""

    return {
        "course_version_id": deck.course_version_id,
        "nodes": tuple(
            node.model_dump(mode="json", by_alias=False, exclude_none=True)
            for node in deck.nodes
        ),
    }


def slide_deck_content_digest(deck: SlideDeckAst) -> str:
    """Bind one immutable deck version to every ordered node semantic."""

    return canonical_digest(slide_deck_semantic_payload(deck))


def runtime_manifest_semantic_payload(manifest: RuntimeManifest) -> dict[str, Any]:
    """Return every pinned runtime dependency without version metadata."""

    return {
        "course_version_id": manifest.course_version_id,
        "slide_deck_version_id": manifest.slide_deck_version_id,
        "slide_deck_digest": manifest.slide_deck_digest,
        "job_bindings": tuple(
            job.model_dump(mode="json", by_alias=False, exclude_none=True)
            for job in manifest.job_bindings
        ),
        "artifact_ids": manifest.artifact_ids,
        "evidence_ids": manifest.evidence_ids,
    }


def runtime_manifest_content_digest(manifest: RuntimeManifest) -> str:
    """Bind one immutable runtime manifest to its exact dependency set."""

    return canonical_digest(runtime_manifest_semantic_payload(manifest))


__all__ = [
    "RuntimeJobBinding",
    "RuntimeManifest",
    "runtime_manifest_content_digest",
    "runtime_manifest_semantic_payload",
    "SlideAssetBinding",
    "SlideDeckAst",
    "slide_deck_content_digest",
    "slide_deck_semantic_payload",
    "SlideNode",
]
