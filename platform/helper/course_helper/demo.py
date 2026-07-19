"""Deterministic orchestration for the read-only reference knowledge demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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

from course_helper.cards import (
    VOCABULARY_VERSION_ID,
    build_candidates,
    publish_card,
    seed_vocabulary,
)
from course_helper.catalog import (
    KnowledgeCatalog,
    SourceRegistrationInput,
)
from course_helper.domain.common import (
    ActorRef,
    SourceLocator,
    freeze_json,
    thaw_json,
)
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.knowledge import (
    DatasetReference,
    KnowledgeCardVersion,
    TagAssignment,
)
from course_helper.domain.sources import (
    DatasetAssetVersion,
    ExtractionResult,
)
from course_helper.parsers.dataset_profiler import DatasetProfiler
from course_helper.parsers.markdown_parser import MarkdownParser
from course_helper.parsers.pptx_parser import PptxParser
from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery
from course_helper.source_roots import (
    SourceRootRegistry,
    SourceRootViolation,
    quick_fingerprint,
    stream_sha256,
)


_MANIFEST_PATH = Path(__file__).with_name("demo") / "reference-demo.json"
_COMMAND_VERSION = "course-helper/demo@1"
_STABLE_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DEMO_ACTOR = ActorRef(actor_type="service", actor_id="course-helper/demo")


class DemoSlideSelection(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )

    start: int = Field(ge=1)
    end_inclusive: int = Field(alias="endInclusive", ge=1)

    @model_validator(mode="after")
    def ordered_range(self) -> DemoSlideSelection:
        if self.end_inclusive < self.start:
            raise ValueError("slide end must not precede slide start")
        return self


class DemoSourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["pptx", "markdown", "csv", "xlsx"]
    path: str = Field(min_length=1)
    slides: DemoSlideSelection | None = None
    headings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_kind_configuration(self) -> DemoSourceSpec:
        normalized = PurePosixPath(self.path.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("demo source paths must stay relative to the root")
        if self.kind == "pptx" and (self.slides is None or self.headings):
            raise ValueError("pptx sources require slides and forbid headings")
        if self.kind == "markdown" and (self.slides is not None or not self.headings):
            raise ValueError("markdown sources require headings and forbid slides")
        if self.kind in {"csv", "xlsx"} and (self.slides is not None or self.headings):
            raise ValueError("dataset sources forbid slide and heading selectors")
        return self


class DemoManifest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )

    schema_version: Literal[1] = Field(alias="schemaVersion")
    root_id: str = Field(alias="rootId", min_length=1)
    inventory_roots: tuple[str, ...] = Field(alias="inventoryRoots")
    sources: tuple[DemoSourceSpec, ...]
    quarantined_extensions: tuple[str, ...] = Field(alias="quarantinedExtensions")

    @model_validator(mode="after")
    def unique_manifest_entries(self) -> DemoManifest:
        source_paths = tuple(source.path for source in self.sources)
        if len(set(source_paths)) != len(source_paths):
            raise ValueError("demo source paths must be unique")
        if len(set(self.inventory_roots)) != len(self.inventory_roots):
            raise ValueError("demo inventory roots must be unique")
        if len(set(self.quarantined_extensions)) != len(
            self.quarantined_extensions
        ):
            raise ValueError("demo quarantined extensions must be unique")
        if any(
            not extension.startswith(".") or extension != extension.casefold()
            for extension in self.quarantined_extensions
        ):
            raise ValueError("demo quarantined extensions must be lowercase suffixes")
        return self


def load_demo_manifest(path: Path | None = None) -> DemoManifest:
    """Load and strictly validate the UTF-8 reference Demo manifest."""

    manifest_path = _MANIFEST_PATH if path is None else Path(path)
    return DemoManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


class DemoFileMetadata(BaseModel):
    """Stat-only metadata recorded beside a deliberate source digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    byte_size: int = Field(ge=0)
    modified_ns: int = Field(ge=0)


class DemoSourceSnapshot(BaseModel):
    """One allowlisted source snapshot without an absolute filesystem path."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    metadata: DemoFileMetadata
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DemoInventoryItemMetadata(BaseModel):
    """Metadata-only identity for an inventoried file or directory entry."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    relative_path: str = Field(min_length=1)
    extension: str
    byte_size: int = Field(ge=0)
    modified_at: datetime
    category: str = Field(min_length=1)
    disposition: str = Field(min_length=1)


class DemoInventoryRootSnapshot(BaseModel):
    """Canonical metadata inventory for one manifest directory."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    items: tuple[DemoInventoryItemMetadata, ...]
    metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DemoIntegritySnapshot(BaseModel):
    """Integrity state for the five deep-read sources and metadata inventory."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    root_id: str = Field(min_length=1)
    sources: tuple[DemoSourceSnapshot, ...]
    inventory_roots: tuple[DemoInventoryRootSnapshot, ...]
    inventory_root_count: int = Field(ge=0)
    inventory_integrity_scope: Literal["metadata-only"] = "metadata-only"
    inventory_item_count: int = Field(ge=0)
    quarantined_extension_counts: Mapping[str, int]

    @field_validator("quarantined_extension_counts")
    @classmethod
    def freeze_extension_counts(cls, value: Mapping[str, int]) -> Mapping[str, int]:
        return cast(Mapping[str, int], freeze_json(value))

    @field_serializer("quarantined_extension_counts", mode="wrap")
    def serialize_extension_counts(
        self,
        value: Mapping[str, int],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, int]:
        return cast(dict[str, int], handler(thaw_json(value)))


def capture_demo_integrity(
    source_root: Path,
    *,
    manifest: DemoManifest | None = None,
) -> DemoIntegritySnapshot:
    """Hash exactly the manifest sources and inventory configured roots by stat only."""

    selected_manifest = load_demo_manifest() if manifest is None else manifest
    registry = SourceRootRegistry({selected_manifest.root_id: Path(source_root)})
    sources = _capture_allowlisted_sources(registry, selected_manifest)
    inventory_roots = _capture_metadata_inventory(registry, selected_manifest)
    quarantined_counts = {
        extension: 0 for extension in selected_manifest.quarantined_extensions
    }
    for inventory_root in inventory_roots:
        for item in inventory_root.items:
            if item.disposition == "quarantined":
                quarantined_counts[item.extension] = (
                    quarantined_counts.get(item.extension, 0) + 1
                )

    return DemoIntegritySnapshot(
        root_id=selected_manifest.root_id,
        sources=sources,
        inventory_roots=inventory_roots,
        inventory_root_count=len(selected_manifest.inventory_roots),
        inventory_item_count=sum(len(root.items) for root in inventory_roots),
        quarantined_extension_counts=quarantined_counts,
    )


def _capture_allowlisted_sources(
    registry: SourceRootRegistry,
    manifest: DemoManifest,
) -> tuple[DemoSourceSnapshot, ...]:
    sources: list[DemoSourceSnapshot] = []
    for source in manifest.sources:
        locator = SourceLocator(root_id=manifest.root_id, relative_path=source.path)
        path = registry.resolve(locator)
        fingerprint = quick_fingerprint(path)
        sources.append(
            DemoSourceSnapshot(
                root_id=locator.root_id,
                relative_path=locator.relative_path,
                metadata=DemoFileMetadata(
                    byte_size=fingerprint.byte_size,
                    modified_ns=fingerprint.modified_ns,
                ),
                sha256=stream_sha256(path),
            )
        )
    return tuple(sources)


def _capture_metadata_inventory(
    registry: SourceRootRegistry,
    manifest: DemoManifest,
) -> tuple[DemoInventoryRootSnapshot, ...]:
    profiler = DatasetProfiler(registry)
    roots: list[DemoInventoryRootSnapshot] = []
    for relative_path in manifest.inventory_roots:
        inventory = profiler.inventory_directory(
            SourceLocator(root_id=manifest.root_id, relative_path=relative_path)
        )
        items = tuple(
            DemoInventoryItemMetadata(
                relative_path=item.relative_path,
                extension=item.extension,
                byte_size=item.byte_size,
                modified_at=item.modified_at,
                category=item.category,
                disposition=item.disposition,
            )
            for item in inventory
        )
        roots.append(
            DemoInventoryRootSnapshot(
                root_id=manifest.root_id,
                relative_path=relative_path,
                items=items,
                metadata_digest=_json_digest(
                    [item.model_dump(mode="json") for item in items]
                ),
            )
        )
    return tuple(roots)


class _AllowlistedReadRegistry:
    """Deny parser payload reads that are not explicit Demo manifest sources."""

    def __init__(self, registry: SourceRootRegistry, manifest: DemoManifest) -> None:
        self._registry = registry
        self._root_id = manifest.root_id
        self._allowed_paths = frozenset(source.path for source in manifest.sources)

    def resolve(self, locator: SourceLocator) -> Path:
        if (
            locator.root_id != self._root_id
            or locator.relative_path not in self._allowed_paths
        ):
            raise SourceRootViolation("Demo parser read is outside the explicit allowlist")
        return self._registry.resolve(locator)


class DemoSourceIntegrity(BaseModel):
    """Before/after integrity evidence for one explicit deep-read source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    before_metadata: DemoFileMetadata
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_metadata: DemoFileMetadata
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DemoInventoryRootIntegrity(BaseModel):
    """Before/after metadata comparison for one inventory directory."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    integrity_scope: Literal["metadata-only"] = "metadata-only"
    before_item_count: int = Field(ge=0)
    before_metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_item_count: int = Field(ge=0)
    after_metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_item_count: int = Field(ge=0)


class DemoRetrievalReceipt(BaseModel):
    """Compact, path-free receipt for one known-phrase retrieval check."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query: str = Field(min_length=1)
    query_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    hit_count: int = Field(ge=0)
    hit_version_ids: tuple[str, ...]
    evidence_id: str = Field(min_length=1)
    evidence_status: Literal["verified", "warning", "failed", "degraded"]


class DemoPassCounts(BaseModel):
    """Mutation and integrity deltas from one deterministic Demo pass."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    new_source_versions: int = Field(ge=0)
    new_card_count: int = Field(ge=0)
    new_evidence_count: int = Field(ge=0)
    duplicate_card_count: int = Field(ge=0)
    forbidden_source_writes: int = Field(ge=0)


class DemoIdempotenceReceipt(BaseModel):
    """Canonical comparison emitted by the two-pass CLI verification mode."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pass_count: Literal[2] = 2
    first_pass: DemoPassCounts
    second_pass: DemoPassCounts
    verified: bool


class DemoReceipt(BaseModel):
    """Canonical, evidence-first output of the reference knowledge Demo."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    schema_version: Literal[1] = 1
    command_version: str = _COMMAND_VERSION
    status: Literal["verified", "warning", "failed", "degraded"]
    root_id: str = Field(min_length=1)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    deep_read_source_count: int = Field(ge=0)
    hash_verified_source_count: int = Field(ge=0)
    inventory_root_count: int = Field(ge=0)
    inventory_integrity_scope: Literal["metadata-only"] = "metadata-only"
    inventory_item_count: int = Field(ge=0)
    quarantined_extension_counts: Mapping[str, int]
    source_integrity: tuple[DemoSourceIntegrity, ...]
    inventory_integrity: tuple[DemoInventoryRootIntegrity, ...]
    pptx_slide_chunks: int = Field(ge=0)
    pptx_chunks_with_notes: int = Field(ge=0)
    markdown_units: frozenset[str]
    profiled_datasets: frozenset[str]
    parser_versions: Mapping[str, str]
    object_digests: Mapping[str, str]
    checks: tuple[EvidenceCheck, ...]
    published_card_count: int = Field(ge=0)
    review_decision_count: int = Field(ge=0)
    retrievals: tuple[DemoRetrievalReceipt, ...]
    new_source_versions: int = Field(ge=0)
    new_card_count: int = Field(ge=0)
    new_evidence_count: int = Field(ge=0)
    duplicate_card_count: int = Field(ge=0)
    forbidden_source_writes: int = Field(ge=0)
    idempotence: DemoIdempotenceReceipt | None = None

    @field_validator(
        "quarantined_extension_counts",
        "parser_versions",
        "object_digests",
    )
    @classmethod
    def freeze_mappings(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return cast(Mapping[str, object], freeze_json(value))

    @field_serializer(
        "quarantined_extension_counts",
        "parser_versions",
        "object_digests",
        mode="wrap",
    )
    def serialize_mappings(
        self,
        value: Mapping[str, object],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, object]:
        return cast(dict[str, object], handler(thaw_json(value)))

    @field_serializer("markdown_units", "profiled_datasets")
    def serialize_sorted_sets(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class DemoIntegrityError(RuntimeError):
    """A source changed during the read-only Demo and publication was rejected."""


class DemoConfigurationError(ValueError):
    """The Demo output topology could overwrite a source or another output."""


class DemoOutputPaths(BaseModel):
    """Canonical output paths validated before any Demo source deep read."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    source_root: Path
    database_path: Path
    evidence_path: Path


def validate_demo_outputs(
    source_root: Path,
    database_path: Path,
    evidence_path: Path,
) -> DemoOutputPaths:
    """Resolve and validate output identity before any source deep read."""

    try:
        resolved_root = Path(source_root).resolve(strict=True)
        if not resolved_root.is_dir():
            raise OSError("source root is not a directory")
        resolved_database = _resolve_demo_output(Path(database_path))
        resolved_evidence = _resolve_demo_output(Path(evidence_path))
    except (OSError, RuntimeError):
        raise DemoConfigurationError(
            "unsafe-demo-output: path resolution failed"
        ) from None
    if (
        resolved_database.is_relative_to(resolved_root)
        or resolved_evidence.is_relative_to(resolved_root)
    ):
        raise DemoConfigurationError("unsafe-demo-output: output is inside source root")
    if resolved_database == resolved_evidence:
        raise DemoConfigurationError("unsafe-demo-output: outputs are not distinct")
    database_sidecars = {
        Path(f"{resolved_database}{suffix}")
        for suffix in ("-wal", "-shm", "-journal")
    }
    if resolved_evidence in database_sidecars:
        raise DemoConfigurationError("unsafe-demo-output: evidence aliases a sidecar")
    if any(os.path.lexists(sidecar) for sidecar in database_sidecars):
        raise DemoConfigurationError("unsafe-demo-output: database sidecar exists")

    existing_database = resolved_database.exists()
    existing_evidence = resolved_evidence.exists()
    if existing_database and existing_evidence:
        try:
            if os.path.samefile(resolved_database, resolved_evidence):
                raise DemoConfigurationError(
                    "unsafe-demo-output: outputs share file identity"
                )
        except OSError:
            raise DemoConfigurationError(
                "unsafe-demo-output: output identity check failed"
            ) from None

    for output in (resolved_database, resolved_evidence):
        if not output.exists():
            continue
        try:
            metadata = output.stat()
        except OSError:
            raise DemoConfigurationError(
                "unsafe-demo-output: output metadata check failed"
            ) from None
        if not output.is_file() or metadata.st_nlink > 1:
            raise DemoConfigurationError(
                "unsafe-demo-output: existing output is not exclusive"
            )

    manifest = load_demo_manifest()
    try:
        source_paths = tuple(
            (resolved_root / source.path).resolve(strict=True)
            for source in manifest.sources
        )
        for output in (resolved_database, resolved_evidence):
            if not output.exists():
                continue
            if any(os.path.samefile(output, source) for source in source_paths):
                raise DemoConfigurationError(
                    "unsafe-demo-output: output shares source identity"
                )
    except DemoConfigurationError:
        raise
    except (OSError, RuntimeError):
        raise DemoConfigurationError(
            "unsafe-demo-output: source identity check failed"
        ) from None
    return DemoOutputPaths(
        source_root=resolved_root,
        database_path=resolved_database,
        evidence_path=resolved_evidence,
    )


def _resolve_demo_output(path: Path) -> Path:
    if os.path.lexists(path):
        metadata = path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        is_junction = bool(getattr(path, "is_junction", lambda: False)())
        if (
            path.is_symlink()
            or is_junction
            or bool(reparse_flag and file_attributes & reparse_flag)
        ):
            raise OSError("output path is a reparse point")
        return path.resolve(strict=True)
    return path.resolve(strict=False)


def _create_staged_database(database_path: Path) -> Path:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{database_path.name}.demo-stage-",
        suffix=".db",
        dir=database_path.parent,
    )
    os.close(descriptor)
    staged_path = Path(temporary_name)
    try:
        if database_path.exists():
            source_uri = f"{database_path.as_uri()}?mode=ro"
            source_connection = sqlite3.connect(source_uri, uri=True)
            try:
                staged_connection = sqlite3.connect(staged_path)
                try:
                    source_connection.backup(staged_connection)
                finally:
                    staged_connection.close()
            finally:
                source_connection.close()
        return staged_path
    except BaseException:
        _cleanup_staged_database(staged_path)
        raise


def _cleanup_staged_database(staged_path: Path) -> None:
    for candidate in (
        Path(f"{staged_path}-wal"),
        Path(f"{staged_path}-shm"),
        Path(f"{staged_path}-journal"),
        staged_path,
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            if candidate.exists():
                raise


def run_reference_demo(
    source_root: Path,
    database_path: Path,
    evidence_path: Path,
) -> DemoReceipt:
    """Build and query governed knowledge from the exact reference allowlist."""

    outputs = validate_demo_outputs(source_root, database_path, evidence_path)
    source_root = outputs.source_root
    database_path = outputs.database_path
    evidence_path = outputs.evidence_path
    manifest = load_demo_manifest()
    registry = SourceRootRegistry({manifest.root_id: Path(source_root)})
    before = capture_demo_integrity(source_root, manifest=manifest)
    before_by_path = {item.relative_path: item for item in before.sources}
    extractions, datasets, parser_versions = _parse_allowlisted_sources(
        registry,
        manifest,
        before_by_path,
    )
    staged_database = _create_staged_database(database_path)
    try:
        receipt = _execute_demo_pass(
            registry=registry,
            manifest=manifest,
            before=before,
            before_by_path=before_by_path,
            extractions=extractions,
            datasets=datasets,
            parser_versions=parser_versions,
            staged_database=staged_database,
        )
        if receipt.forbidden_source_writes:
            _write_receipt(receipt, evidence_path)
            raise DemoIntegrityError(
                "forbidden-source-write: an allowlisted source changed during the Demo"
            )
        os.replace(staged_database, database_path)
        _write_receipt(receipt, evidence_path)
        return receipt
    finally:
        _cleanup_staged_database(staged_database)


def _execute_demo_pass(
    *,
    registry: SourceRootRegistry,
    manifest: DemoManifest,
    before: DemoIntegritySnapshot,
    before_by_path: Mapping[str, DemoSourceSnapshot],
    extractions: tuple[ExtractionResult, ...],
    datasets: tuple[DatasetAssetVersion, ...],
    parser_versions: Mapping[str, str],
    staged_database: Path,
) -> DemoReceipt:
    with KnowledgeCatalog.open(staged_database) as catalog:
        counts_before = _catalog_counts(catalog)
        _persist_parsed_objects(
            catalog,
            manifest,
            extractions,
            datasets,
            before_by_path,
        )
        seed_vocabulary(catalog)
        _review_and_publish(catalog, manifest, extractions, datasets)
        retrievals = _run_known_phrase_retrievals(catalog)
        counts_after = _catalog_counts(catalog)
        object_digests = _catalog_object_digests(catalog)

    after_sources = _capture_allowlisted_sources(registry, manifest)
    after_inventory = _capture_metadata_inventory(registry, manifest)
    source_integrity = tuple(
        DemoSourceIntegrity(
            root_id=before_item.root_id,
            relative_path=before_item.relative_path,
            before_metadata=before_item.metadata,
            before_sha256=before_item.sha256,
            after_metadata=after_item.metadata,
            after_sha256=after_item.sha256,
        )
        for before_item, after_item in zip(
            before.sources,
            after_sources,
            strict=True,
        )
    )
    changed_source_paths = {
        item.relative_path
        for item in source_integrity
        if item.before_metadata != item.after_metadata
        or item.before_sha256 != item.after_sha256
    }
    inventory_integrity, changed_inventory_paths = _compare_metadata_inventories(
        before.inventory_roots,
        after_inventory,
    )
    forbidden_source_writes = len(changed_source_paths | changed_inventory_paths)
    pptx_chunks = tuple(
        chunk
        for extraction in extractions
        if extraction.source.source_kind == "pptx"
        for chunk in extraction.chunks
    )
    markdown_units = frozenset(
        heading
        for source in manifest.sources
        if source.kind == "markdown"
        for heading in source.headings
    )
    new_source_versions = counts_after["sources"] - counts_before["sources"]
    new_card_count = counts_after["cards"] - counts_before["cards"]
    new_evidence_count = counts_after["evidence"] - counts_before["evidence"]
    duplicate_card_count = counts_after["archived_cards"] - counts_before[
        "archived_cards"
    ]
    checks = _receipt_checks(
        manifest=manifest,
        forbidden_source_writes=forbidden_source_writes,
        retrievals=retrievals,
    )
    return DemoReceipt(
        status="failed" if forbidden_source_writes else "degraded",
        root_id=manifest.root_id,
        manifest_digest=_manifest_digest(manifest),
        deep_read_source_count=len(manifest.sources),
        hash_verified_source_count=len(source_integrity),
        inventory_root_count=before.inventory_root_count,
        inventory_item_count=before.inventory_item_count,
        quarantined_extension_counts=before.quarantined_extension_counts,
        source_integrity=source_integrity,
        inventory_integrity=inventory_integrity,
        pptx_slide_chunks=len(pptx_chunks),
        pptx_chunks_with_notes=sum(bool(chunk.notes_text.strip()) for chunk in pptx_chunks),
        markdown_units=markdown_units,
        profiled_datasets=frozenset(
            dataset.locator.relative_path for dataset in datasets
        ),
        parser_versions=parser_versions,
        object_digests=object_digests,
        checks=checks,
        published_card_count=counts_after["published_cards"],
        review_decision_count=counts_after["review_decisions"],
        retrievals=retrievals,
        new_source_versions=new_source_versions,
        new_card_count=new_card_count,
        new_evidence_count=new_evidence_count,
        duplicate_card_count=duplicate_card_count,
        forbidden_source_writes=forbidden_source_writes,
    )


def _compare_metadata_inventories(
    before_roots: tuple[DemoInventoryRootSnapshot, ...],
    after_roots: tuple[DemoInventoryRootSnapshot, ...],
) -> tuple[tuple[DemoInventoryRootIntegrity, ...], set[str]]:
    before_by_root = {root.relative_path: root for root in before_roots}
    after_by_root = {root.relative_path: root for root in after_roots}
    if before_by_root.keys() != after_by_root.keys():
        raise DemoIntegrityError("configured metadata inventory roots changed")
    comparisons: list[DemoInventoryRootIntegrity] = []
    all_changed_paths: set[str] = set()
    for relative_root in before_by_root:
        before = before_by_root[relative_root]
        after = after_by_root[relative_root]
        before_items = {item.relative_path: item for item in before.items}
        after_items = {item.relative_path: item for item in after.items}
        changed_paths = {
            path
            for path in before_items.keys() | after_items.keys()
            if before_items.get(path) != after_items.get(path)
        }
        all_changed_paths.update(changed_paths)
        comparisons.append(
            DemoInventoryRootIntegrity(
                root_id=before.root_id,
                relative_path=relative_root,
                before_item_count=len(before.items),
                before_metadata_digest=before.metadata_digest,
                after_item_count=len(after.items),
                after_metadata_digest=after.metadata_digest,
                changed_item_count=len(changed_paths),
            )
        )
    return tuple(comparisons), all_changed_paths


def _parse_allowlisted_sources(
    registry: SourceRootRegistry,
    manifest: DemoManifest,
    source_snapshots: Mapping[str, DemoSourceSnapshot],
) -> tuple[
    tuple[ExtractionResult, ...],
    tuple[DatasetAssetVersion, ...],
    Mapping[str, str],
]:
    read_registry = cast(
        SourceRootRegistry,
        _AllowlistedReadRegistry(registry, manifest),
    )
    pptx_parser = PptxParser(read_registry)
    markdown_parser = MarkdownParser(read_registry)
    dataset_profiler = DatasetProfiler(read_registry)
    extractions: list[ExtractionResult] = []
    datasets: list[DatasetAssetVersion] = []
    parser_versions: dict[str, str] = {}

    for source in manifest.sources:
        locator = SourceLocator(root_id=manifest.root_id, relative_path=source.path)
        if source.kind == "pptx":
            assert source.slides is not None
            extraction = pptx_parser.parse(
                locator,
                range(source.slides.start, source.slides.end_inclusive + 1),
            )
            extractions.append(extraction)
            parser_versions[source.path] = (
                f"{extraction.source.parser_name}@{extraction.source.parser_version}"
            )
            digest = extraction.source.content_digest
        elif source.kind == "markdown":
            extraction = markdown_parser.parse(locator, source.headings)
            extractions.append(extraction)
            parser_versions[source.path] = (
                f"{extraction.source.parser_name}@{extraction.source.parser_version}"
            )
            digest = extraction.source.content_digest
        elif source.kind == "csv":
            dataset = dataset_profiler.profile_csv(locator)
            datasets.append(dataset)
            parser_versions[source.path] = (
                f"{dataset.evidence.producer}@{dataset.evidence.producer_version}"
            )
            digest = dataset.content_digest
        else:
            dataset = dataset_profiler.profile_xlsx(locator)
            datasets.append(dataset)
            parser_versions[source.path] = (
                f"{dataset.evidence.producer}@{dataset.evidence.producer_version}"
            )
            digest = dataset.content_digest
        if digest != source_snapshots[source.path].sha256:
            raise DemoIntegrityError(
                f"parser digest differs from the pre-ingest digest: {source.path}"
            )
    return tuple(extractions), tuple(datasets), parser_versions


def _persist_parsed_objects(
    catalog: KnowledgeCatalog,
    manifest: DemoManifest,
    extractions: tuple[ExtractionResult, ...],
    datasets: tuple[DatasetAssetVersion, ...],
    source_snapshots: Mapping[str, DemoSourceSnapshot],
) -> None:
    for extraction in extractions:
        registered = catalog.register_or_reuse_source(
            _source_registration_from_extraction(extraction)
        )
        if registered.version_id != extraction.source.version_id:
            raise DemoIntegrityError("registered extraction source version differs")
        for chunk in extraction.chunks:
            catalog.insert_chunk(chunk)
        for visual in extraction.visuals:
            catalog.insert_visual(visual)
        catalog.insert_evidence(extraction.evidence)

    dataset_by_path = {dataset.locator.relative_path: dataset for dataset in datasets}
    for source in manifest.sources:
        if source.kind not in {"csv", "xlsx"}:
            continue
        dataset = dataset_by_path[source.path]
        snapshot = source_snapshots[source.path]
        catalog.register_or_reuse_source(
            _source_registration_from_dataset(dataset, snapshot)
        )
        catalog.insert_dataset(dataset)
        catalog.insert_evidence(dataset.evidence)


def _source_registration_from_extraction(
    extraction: ExtractionResult,
) -> SourceRegistrationInput:
    source = extraction.source
    return SourceRegistrationInput(
        locator=source.locator,
        display_name=source.display_name,
        source_kind=source.source_kind,
        media_type=source.media_type,
        byte_size=source.byte_size,
        modified_at=source.modified_at,
        content_digest=source.content_digest,
        content_summary=source.content_summary,
        extraction_status=source.extraction_status,
        parser_name=source.parser_name,
        parser_version=source.parser_version,
        parser_config_digest=source.parser_config_digest,
        created_at=source.created_at,
        created_by=source.created_by,
    )


def _source_registration_from_dataset(
    dataset: DatasetAssetVersion,
    snapshot: DemoSourceSnapshot,
) -> SourceRegistrationInput:
    source_time = datetime.fromtimestamp(
        snapshot.metadata.modified_ns / 1_000_000_000,
        tz=timezone.utc,
    )
    media_type = (
        "text/csv"
        if dataset.format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    config_digest = cast(str, dataset.evidence.input_summary["profile_config_digest"])
    return SourceRegistrationInput(
        locator=dataset.locator,
        display_name=PurePosixPath(dataset.locator.relative_path).name,
        source_kind=dataset.format,
        media_type=media_type,
        byte_size=snapshot.metadata.byte_size,
        modified_at=source_time,
        content_digest=snapshot.sha256,
        content_summary=f"Bounded profile with {dataset.row_count} rows",
        extraction_status="parsed",
        parser_name=dataset.evidence.producer,
        parser_version=dataset.evidence.producer_version,
        parser_config_digest=config_digest,
        created_at=source_time,
        created_by=_DEMO_ACTOR,
    )


def _review_and_publish(
    catalog: KnowledgeCatalog,
    manifest: DemoManifest,
    extractions: tuple[ExtractionResult, ...],
    datasets: tuple[DatasetAssetVersion, ...],
) -> None:
    ready_datasets = tuple(
        dataset for dataset in datasets if dataset.review_status == "ready"
    )
    source_spec_by_path = {source.path: source for source in manifest.sources}
    for extraction in extractions:
        source_spec = source_spec_by_path[extraction.source.locator.relative_path]
        for candidate in build_candidates(extraction):
            reviewed = _apply_demo_review_policy(
                candidate,
                source_spec,
                ready_datasets,
            )
            stored_review = catalog.insert_card(reviewed)
            catalog.insert_evidence(_demo_review_evidence(stored_review))
            publish_card(stored_review, catalog)


def _apply_demo_review_policy(
    card: KnowledgeCardVersion,
    source: DemoSourceSpec,
    ready_datasets: tuple[DatasetAssetVersion, ...],
) -> KnowledgeCardVersion:
    common_tags = (
        "difficulty:beginner",
        "pedagogy:explain",
    )
    if source.kind == "pptx":
        contextual_tags = (
            "topic:ai-foundations",
            "audience:learner",
            "tool:agnostic",
            "scenario:course-learning",
            "dataType:presentation",
        )
        dataset_refs: tuple[DatasetReference, ...] = ()
    elif source.path == "AIGC实操 -数据分析.md":
        contextual_tags = (
            "topic:data-analysis",
            "audience:analyst",
            "tool:spreadsheet",
            "scenario:data-analysis",
            "dataType:text",
        )
        dataset_refs = tuple(
            DatasetReference(dataset_version_id=dataset.version_id)
            for dataset in ready_datasets
        )
    else:
        contextual_tags = (
            "topic:prompting",
            "audience:learner",
            "tool:agnostic",
            "scenario:prompt-engineering",
            "dataType:text",
        )
        dataset_refs = ()
    tag_ids = tuple(sorted((*common_tags, *contextual_tags)))
    assignments = tuple(
        TagAssignment(
            vocabulary_version_id=VOCABULARY_VERSION_ID,
            dimension_id=tag_id.split(":", 1)[0],
            tag_id=tag_id,
            assigned_by="rule",
            confidence=1.0,
        )
        for tag_id in tag_ids
    )
    return card.model_copy(
        update={
            "status": "review",
            "tag_assignments": assignments,
            "dataset_refs": dataset_refs,
        }
    )


def _demo_review_evidence(card: KnowledgeCardVersion) -> EvidenceObject:
    evidence_payload = {
        "card_version_id": card.version_id,
        "tag_ids": sorted(item.tag_id for item in card.tag_assignments),
        "citation_ids": [item.chunk_id for item in card.chunk_citations],
        "visual_ids": [item.visual_version_id for item in card.visual_refs],
        "dataset_ids": [item.dataset_version_id for item in card.dataset_refs],
        "policy": "reference-demo-fixture-only-v1",
    }
    evidence_id = "demo-review-" + _json_digest(evidence_payload)
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="validation",
        subject_version_id=card.version_id,
        status="verified",
        input_summary={
            "policy": "reference-demo-fixture-only-v1",
            "general_ingestion_auto_publish": False,
        },
        output_summary={
            "approved": True,
            "tag_count": len(card.tag_assignments),
            "citation_count": len(card.chunk_citations),
            "visual_reference_count": len(card.visual_refs),
            "dataset_reference_count": len(card.dataset_refs),
        },
        producer="course-helper/demo-review-policy",
        producer_version="1",
        started_at=_STABLE_TIME,
        finished_at=_STABLE_TIME,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="demo-policy-scope",
                status="passed",
                message="Approval is restricted to the deterministic reference Demo fixture",
            ),
            EvidenceCheck(
                code="publish-input-gates",
                status="passed",
                message="Tags, citations, visuals, datasets, and open reviews are fail-closed at publish",
            ),
        ),
    )


def _run_known_phrase_retrievals(
    catalog: KnowledgeCatalog,
) -> tuple[DemoRetrievalReceipt, ...]:
    retriever = KnowledgeRetriever(catalog)
    receipts: list[DemoRetrievalReceipt] = []
    for phrase in ("人工智能", "自行车共享需求", "正确提问"):
        result = retriever.search(RetrievalQuery(text=phrase, limit=10))
        catalog.insert_evidence(result.evidence)
        receipts.append(
            DemoRetrievalReceipt(
                query=phrase,
                query_digest=result.query_digest,
                hit_count=len(result.hits),
                hit_version_ids=tuple(hit.card.version_id for hit in result.hits),
                evidence_id=result.evidence.evidence_id,
                evidence_status=result.evidence.status,
            )
        )
    return tuple(receipts)


def _catalog_counts(catalog: KnowledgeCatalog) -> dict[str, int]:
    queries = {
        "sources": "SELECT count(*) FROM sources",
        "cards": "SELECT count(*) FROM cards",
        "evidence": "SELECT count(*) FROM evidence",
        "published_cards": (
            "SELECT count(*) FROM card_lifecycle_current "
            "WHERE status = 'published' AND suspended = 0"
        ),
        "archived_cards": (
            "SELECT count(*) FROM card_lifecycle_current WHERE status = 'archived'"
        ),
        "review_decisions": "SELECT count(*) FROM evidence WHERE kind = 'validation'",
    }
    return {
        name: int(catalog.connection.execute(query).fetchone()[0])
        for name, query in queries.items()
    }


def _catalog_object_digests(catalog: KnowledgeCatalog) -> Mapping[str, str]:
    return {
        table: _json_digest(
            [
                tuple(row)
                for row in catalog.connection.execute(
                    f"SELECT {identity}, content_digest FROM {table} ORDER BY {identity}"
                ).fetchall()
            ]
        )
        for table, identity in (
            ("sources", "version_id"),
            ("chunks", "chunk_id"),
            ("visuals", "version_id"),
            ("datasets", "version_id"),
            ("cards", "version_id"),
        )
    } | {
        "evidence": _json_digest(
            [
                tuple(row)
                for row in catalog.connection.execute(
                    "SELECT evidence_id, kind, status FROM evidence ORDER BY evidence_id"
                ).fetchall()
            ]
        )
    }


def _receipt_checks(
    *,
    manifest: DemoManifest,
    forbidden_source_writes: int,
    retrievals: tuple[DemoRetrievalReceipt, ...],
) -> tuple[EvidenceCheck, ...]:
    return (
        EvidenceCheck(
            code="deep-read-allowlist",
            status="passed",
            message="Only the five explicit source locators were eligible for deep reads",
            details={"source_count": len(manifest.sources)},
        ),
        EvidenceCheck(
            code="inventory-integrity-scope",
            status="passed",
            message="Directory inventory used metadata only and was not hash verified",
            details={
                "scope": "metadata-only",
                "root_count": len(manifest.inventory_roots),
            },
        ),
        EvidenceCheck(
            code="parser-digest-recomputation",
            status="warning",
            message="Existing parser interfaces recomputed five source digests internally",
            details={
                "precomputed_registration_digests_reused": True,
                "unavoidable_internal_digest_count": len(manifest.sources),
            },
        ),
        EvidenceCheck(
            code="known-phrase-retrieval",
            status=(
                "warning"
                if all(item.hit_count > 0 for item in retrievals)
                else "failed"
            ),
            message="Known phrases were retrieved through the degraded lexical fallback",
            details={"query_count": len(retrievals), "embedding_enabled": False},
        ),
        EvidenceCheck(
            code="forbidden-source-write",
            status="passed" if forbidden_source_writes == 0 else "failed",
            message=(
                "Before and after source metadata and digests match"
                if forbidden_source_writes == 0
                else "At least one allowlisted source changed during the Demo"
            ),
            details={"changed_source_count": forbidden_source_writes},
        ),
    )


def _catalog_pass_counts(receipt: DemoReceipt) -> DemoPassCounts:
    return DemoPassCounts(
        new_source_versions=receipt.new_source_versions,
        new_card_count=receipt.new_card_count,
        new_evidence_count=receipt.new_evidence_count,
        duplicate_card_count=receipt.duplicate_card_count,
        forbidden_source_writes=receipt.forbidden_source_writes,
    )


def _manifest_digest(manifest: DemoManifest) -> str:
    return _json_digest(manifest.model_dump(mode="json", by_alias=True))


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_receipt(receipt: DemoReceipt, evidence_path: Path) -> None:
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        receipt.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{evidence_path.name}.demo-receipt-",
        suffix=".tmp",
        dir=evidence_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, evidence_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Run the reference Demo from an explicit root or COURSE_REFERENCE_ROOT."""

    parser = argparse.ArgumentParser(
        description="Build the read-only reference knowledge Demo",
    )
    parser.add_argument("--source-root")
    parser.add_argument("--database", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--verify-idempotence", action="store_true")
    arguments = parser.parse_args(argv)
    source_root_value = arguments.source_root or os.environ.get(
        "COURSE_REFERENCE_ROOT"
    )
    if not source_root_value:
        parser.error(
            "--source-root or the COURSE_REFERENCE_ROOT environment variable is required"
        )
    source_root = Path(source_root_value)
    database_path = Path(arguments.database)
    evidence_path = Path(arguments.evidence)

    first = run_reference_demo(source_root, database_path, evidence_path)
    receipt = first
    if arguments.verify_idempotence:
        second = run_reference_demo(source_root, database_path, evidence_path)
        second_counts = _catalog_pass_counts(second)
        verified = (
            second_counts.new_source_versions == 0
            and second_counts.new_card_count == 0
            and second_counts.new_evidence_count == 0
            and second_counts.duplicate_card_count == 0
            and second_counts.forbidden_source_writes == 0
        )
        receipt = second.model_copy(
            update={
                "idempotence": DemoIdempotenceReceipt(
                    first_pass=_catalog_pass_counts(first),
                    second_pass=second_counts,
                    verified=verified,
                )
            }
        )
        _write_receipt(receipt, evidence_path)
        if not verified:
            return 1
    print(
        json.dumps(
            receipt.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
