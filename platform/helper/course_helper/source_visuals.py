"""Read-only PPTX media materialization into the verified artifact store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import PurePosixPath
import posixpath
from typing import Callable, Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from defusedxml import ElementTree
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_helper.artifacts import (
    ArtifactError,
    ArtifactMetadata,
    ArtifactStore,
    ArtifactTooLarge,
    ArtifactValidationError,
)
from course_helper.catalog import (
    CatalogMigrationError,
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.composition import canonical_digest
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject, LineageEdge
from course_helper.domain.sources import (
    ExtractedChunk,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.source_roots import (
    SourceRootRegistry,
    candidate_logical_id,
    candidate_version_id,
    stream_sha256,
)


Clock = Callable[[], datetime]
PRODUCER = "course-helper/source-visuals"
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_IMAGE_REL = _OFFICE_REL_NS + "/image"
_SLIDE_REL = _OFFICE_REL_NS + "/slide"
_MAX_XML_BYTES = 4 * 1024 * 1024


class SourceVisualMaterialization(BaseModel):
    """Path-free binding from one exact PPTX relationship to artifact bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    materialization_id: str = Field(
        pattern=r"^source-visual-[0-9a-f]{64}$"
    )
    visual_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    artifact_id: str = Field(pattern=r"^artifact-[0-9a-f]{64}$")
    source_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    slide_number: int = Field(ge=1)
    relationship_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(pattern=r"^source-visual-evidence-[0-9a-f]{64}$")
    created_at: datetime


@dataclass(frozen=True)
class SourceVisualOutcome:
    visual_version_id: str
    status: Literal["materialized", "failed"]
    artifact_id: str | None = None
    evidence_id: str | None = None
    reused: bool = False
    error_code: str | None = None
    message: str | None = None


class _MaterializationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


def _source(catalog: KnowledgeCatalog, version_id: str) -> SourceAssetVersion:
    row = catalog.connection.execute(
        "SELECT logical_id, revision, content_digest, payload_json "
        "FROM sources WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise _MaterializationError("SOURCE_NOT_FOUND", "Registered source was not found")
    try:
        source = SourceAssetVersion.model_validate_json(row[3])
    except ValidationError as error:
        raise _MaterializationError(
            "SOURCE_ENVELOPE_INVALID", "Registered source metadata is invalid"
        ) from error
    if (
        canonical_model_json(source) != row[3]
        or (source.logical_id, source.revision, source.content_digest) != tuple(row[:3])
        or source.version_id != version_id
        or source.source_kind != "pptx"
    ):
        raise _MaterializationError(
            "SOURCE_ENVELOPE_INVALID", "Registered source metadata is invalid"
        )
    return source


def _visual(catalog: KnowledgeCatalog, version_id: str) -> VisualAssetVersion:
    row = catalog.connection.execute(
        "SELECT logical_id, revision, content_digest, payload_json "
        "FROM visuals WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise _MaterializationError("VISUAL_NOT_FOUND", "Visual metadata was not found")
    try:
        visual = VisualAssetVersion.model_validate_json(row[3])
    except ValidationError as error:
        raise _MaterializationError(
            "VISUAL_ENVELOPE_INVALID", "Visual metadata is invalid"
        ) from error
    if (
        canonical_model_json(visual) != row[3]
        or (visual.logical_id, visual.revision, visual.content_digest) != tuple(row[:3])
        or visual.version_id != version_id
    ):
        raise _MaterializationError(
            "VISUAL_ENVELOPE_INVALID", "Visual metadata is invalid"
        )
    return visual


def validate_source_visual_identity(
    catalog: KnowledgeCatalog,
    source: SourceAssetVersion,
    visual: VisualAssetVersion,
) -> None:
    locator = visual.source_locator
    if locator is None or locator.slide_number is None or not locator.relationship_id:
        raise _MaterializationError(
            "VISUAL_IDENTITY_INVALID", "Visual relationship identity is incomplete"
        )
    if len(set(visual.derived_from_version_ids)) != len(
        visual.derived_from_version_ids
    ):
        raise _MaterializationError(
            "VISUAL_IDENTITY_INVALID", "Visual parent identities are duplicated"
        )
    rows = catalog.connection.execute(
        "SELECT source_version_id, ordinal, content_digest, payload_json "
        "FROM chunks WHERE source_version_id = ? ORDER BY ordinal, chunk_id",
        (source.version_id,),
    ).fetchall()
    slide_chunks: list[ExtractedChunk] = []
    for row in rows:
        try:
            chunk = ExtractedChunk.model_validate_json(row[3])
        except ValidationError as error:
            raise _MaterializationError(
                "VISUAL_IDENTITY_INVALID", "Visual parent chunk metadata is invalid"
            ) from error
        if (
            canonical_model_json(chunk) != row[3]
            or (chunk.source_version_id, chunk.ordinal, chunk.content_digest)
            != tuple(row[:3])
        ):
            raise _MaterializationError(
                "VISUAL_IDENTITY_INVALID", "Visual parent chunk envelope is invalid"
            )
        if chunk.locator.slide_number == locator.slide_number:
            slide_chunks.append(chunk)
    referenced = tuple(
        chunk for chunk in slide_chunks if visual.version_id in chunk.media_version_ids
    )
    if slide_chunks and len(referenced) != 1:
        raise _MaterializationError(
            "VISUAL_IDENTITY_INVALID", "Visual is not pinned by its exact slide chunk"
        )
    expected_parents = (
        (source.version_id, referenced[0].chunk_id)
        if referenced
        else (source.version_id,)
    )
    semantic_locator = (
        f"{source.logical_id}:slide-{locator.slide_number}:{locator.relationship_id}"
    )
    expected_logical_id = candidate_logical_id("visual", semantic_locator)
    expected_version_id = candidate_version_id(
        expected_logical_id,
        expected_parents,
        visual.content_digest,
    )
    if (
        visual.logical_id != expected_logical_id
        or visual.version_id != expected_version_id
        or visual.derived_from_version_ids != expected_parents
    ):
        raise _MaterializationError(
            "VISUAL_IDENTITY_INVALID", "Visual identity does not match parser semantics"
        )


def _visual_ids(
    catalog: KnowledgeCatalog,
    source_version_id: str,
    requested: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if requested is not None:
        if len(set(requested)) != len(requested):
            raise ValueError("visual version IDs must be unique")
        return requested
    rows = catalog.connection.execute(
        "SELECT payload_json FROM visuals ORDER BY version_id"
    ).fetchall()
    selected: list[str] = []
    for row in rows:
        try:
            visual = VisualAssetVersion.model_validate_json(row[0])
        except ValidationError:
            continue
        if source_version_id in visual.derived_from_version_ids:
            selected.append(visual.version_id)
    return tuple(selected)


def _unique_info(archive: ZipFile, name: str) -> ZipInfo:
    matches = tuple(info for info in archive.infolist() if info.filename == name)
    if len(matches) != 1 or matches[0].is_dir() or matches[0].flag_bits & 0x1:
        raise _MaterializationError(
            "RELATIONSHIP_INVALID", "PPTX package member is missing or ambiguous"
        )
    return matches[0]


def _xml_root(archive: ZipFile, name: str):
    info = _unique_info(archive, name)
    if info.file_size > _MAX_XML_BYTES:
        raise _MaterializationError("PPTX_INVALID", "PPTX metadata is oversized")
    try:
        with archive.open(info, "r") as source:
            payload = source.read(_MAX_XML_BYTES + 1)
        if len(payload) > _MAX_XML_BYTES:
            raise _MaterializationError("PPTX_INVALID", "PPTX metadata is oversized")
        return ElementTree.fromstring(payload)
    except _MaterializationError:
        raise
    except Exception as error:
        raise _MaterializationError("PPTX_INVALID", "PPTX metadata is invalid") from error


def _relationship(root, relationship_id: str, expected_type: str):
    matches = tuple(
        item
        for item in root
        if item.attrib.get("Id") == relationship_id
    )
    if (
        len(matches) != 1
        or matches[0].attrib.get("Type") != expected_type
        or matches[0].attrib.get("TargetMode", "Internal") == "External"
    ):
        raise _MaterializationError(
            "RELATIONSHIP_NOT_FOUND", "PPTX image relationship was not found"
        )
    return matches[0]


def _resolved_member(base_name: str, target: str) -> str:
    if not target or "\\" in target or target.startswith("/"):
        raise _MaterializationError(
            "RELATIONSHIP_INVALID", "PPTX relationship target is invalid"
        )
    normalized = posixpath.normpath(
        posixpath.join(str(PurePosixPath(base_name).parent), target)
    )
    member = PurePosixPath(normalized)
    if member.is_absolute() or ".." in member.parts:
        raise _MaterializationError(
            "RELATIONSHIP_INVALID", "PPTX relationship target is invalid"
        )
    return member.as_posix()


def _content_type(archive: ZipFile, member_name: str) -> str:
    root = _xml_root(archive, "[Content_Types].xml")
    part_name = "/" + member_name
    overrides = tuple(
        item.attrib.get("ContentType")
        for item in root
        if item.tag.endswith("}Override") and item.attrib.get("PartName") == part_name
    )
    if overrides:
        if len(overrides) != 1 or not overrides[0]:
            raise _MaterializationError(
                "MEDIA_TYPE_MISMATCH", "PPTX media type is missing or ambiguous"
            )
        return str(overrides[0])
    extension = PurePosixPath(member_name).suffix.lower().lstrip(".")
    defaults = tuple(
        item.attrib.get("ContentType")
        for item in root
        if item.tag.endswith("}Default")
        and str(item.attrib.get("Extension", "")).lower() == extension
    )
    if len(defaults) != 1 or not defaults[0]:
        raise _MaterializationError(
            "MEDIA_TYPE_MISMATCH", "PPTX media type is missing or ambiguous"
        )
    return str(defaults[0])


def _zip_member(path, visual: VisualAssetVersion) -> tuple[ZipFile, ZipInfo, str]:
    locator = visual.source_locator
    if (
        locator is None
        or locator.slide_number is None
        or not locator.relationship_id
    ):
        raise _MaterializationError(
            "RELATIONSHIP_NOT_FOUND", "Visual has no exact PPTX relationship locator"
        )
    try:
        archive = ZipFile(path, "r")
    except (BadZipFile, OSError) as error:
        raise _MaterializationError("PPTX_INVALID", "PPTX package is invalid") from error
    try:
        if len(archive.infolist()) > 10_000:
            raise _MaterializationError(
                "PPTX_INVALID", "PPTX package contains too many members"
            )
        presentation = _xml_root(archive, "ppt/presentation.xml")
        slide_ids = tuple(
            item
            for item in presentation.iter()
            if item.tag == f"{{{_PRESENTATION_NS}}}sldId"
        )
        if locator.slide_number > len(slide_ids):
            raise _MaterializationError(
                "RELATIONSHIP_NOT_FOUND", "PPTX slide relationship was not found"
            )
        slide_relationship_id = slide_ids[locator.slide_number - 1].attrib.get(
            f"{{{_OFFICE_REL_NS}}}id"
        )
        if not slide_relationship_id:
            raise _MaterializationError(
                "RELATIONSHIP_NOT_FOUND", "PPTX slide relationship was not found"
            )
        presentation_rels = _xml_root(
            archive, "ppt/_rels/presentation.xml.rels"
        )
        slide_relationship = _relationship(
            presentation_rels, slide_relationship_id, _SLIDE_REL
        )
        slide_member = _resolved_member(
            "ppt/presentation.xml", str(slide_relationship.attrib.get("Target", ""))
        )
        if tuple(PurePosixPath(slide_member).parts[:2]) != ("ppt", "slides"):
            raise _MaterializationError(
                "RELATIONSHIP_INVALID", "PPTX slide target is invalid"
            )
        _unique_info(archive, slide_member)
        slide_path = PurePosixPath(slide_member)
        slide_rels_name = (
            slide_path.parent / "_rels" / f"{slide_path.name}.rels"
        ).as_posix()
        slide_rels = _xml_root(archive, slide_rels_name)
        image_relationship = _relationship(
            slide_rels, locator.relationship_id, _IMAGE_REL
        )
        member_name = _resolved_member(
            slide_member, str(image_relationship.attrib.get("Target", ""))
        )
        if tuple(PurePosixPath(member_name).parts[:2]) != ("ppt", "media"):
            raise _MaterializationError(
                "RELATIONSHIP_INVALID", "PPTX image relationship target is invalid"
            )
        member = _unique_info(archive, member_name)
        media_type = _content_type(archive, member_name)
        if media_type != visual.media_type:
            raise _MaterializationError(
                "MEDIA_TYPE_MISMATCH", "PPTX media type does not match visual metadata"
            )
        return archive, member, media_type
    except BaseException:
        archive.close()
        raise


def _evidence(
    *,
    source: SourceAssetVersion,
    visual: VisualAssetVersion,
    artifact: ArtifactMetadata,
    created_at: datetime,
) -> EvidenceObject:
    core = {
        "source_version_id": source.version_id,
        "source_content_digest": source.content_digest,
        "visual_version_id": visual.version_id,
        "visual_content_digest": visual.content_digest,
        "artifact_id": artifact.artifact_id,
        "artifact_digest": artifact.content_digest,
        "slide_number": visual.source_locator.slide_number,
        "relationship_id": visual.source_locator.relationship_id,
    }
    evidence_id = "source-visual-evidence-" + canonical_digest(core)
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="validation",
        subject_version_id=visual.version_id,
        status="verified",
        input_summary={
            "source_version_id": source.version_id,
            "source_content_digest": source.content_digest,
            "visual_version_id": visual.version_id,
            "visual_content_digest": visual.content_digest,
            "slide_number": visual.source_locator.slide_number,
            "relationship_id": visual.source_locator.relationship_id,
        },
        output_summary={
            "artifact_id": artifact.artifact_id,
            "artifact_digest": artifact.content_digest,
            "byte_size": artifact.byte_size,
            "media_type": artifact.media_type,
            "width": artifact.width,
            "height": artifact.height,
        },
        producer=PRODUCER,
        producer_version="1",
        started_at=created_at,
        finished_at=created_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="exact-pptx-relationship",
                status="passed",
                message="Exact registered PPTX relationship matched verified artifact bytes",
                details={
                    "source_content_digest": source.content_digest,
                    "visual_content_digest": visual.content_digest,
                    "artifact_digest": artifact.content_digest,
                },
            ),
        ),
    )


def _materialization(
    *,
    source: SourceAssetVersion,
    visual: VisualAssetVersion,
    artifact: ArtifactMetadata,
    evidence: EvidenceObject,
    created_at: datetime,
) -> SourceVisualMaterialization:
    locator = visual.source_locator
    core = {
        "source_version_id": source.version_id,
        "source_content_digest": source.content_digest,
        "visual_version_id": visual.version_id,
        "visual_content_digest": visual.content_digest,
        "artifact_id": artifact.artifact_id,
        "slide_number": locator.slide_number,
        "relationship_id": locator.relationship_id,
        "evidence_id": evidence.evidence_id,
    }
    return SourceVisualMaterialization(
        materialization_id="source-visual-" + canonical_digest(core),
        visual_version_id=visual.version_id,
        artifact_id=artifact.artifact_id,
        source_version_id=source.version_id,
        source_content_digest=source.content_digest,
        visual_content_digest=visual.content_digest,
        slide_number=locator.slide_number,
        relationship_id=locator.relationship_id,
        evidence_id=evidence.evidence_id,
        created_at=created_at,
    )


def _materialize_one(
    catalog: KnowledgeCatalog,
    source_roots: SourceRootRegistry,
    artifact_store: ArtifactStore,
    source: SourceAssetVersion,
    visual_version_id: str,
    *,
    clock: Clock,
) -> SourceVisualOutcome:
    path = source_roots.resolve(source.locator)
    if stream_sha256(path) != source.content_digest:
        raise _MaterializationError(
            "SOURCE_DIGEST_MISMATCH", "Source bytes no longer match the registered digest"
        )
    visual = _visual(catalog, visual_version_id)
    if source.version_id not in visual.derived_from_version_ids:
        raise _MaterializationError(
            "VISUAL_SOURCE_MISMATCH", "Visual is not pinned to the requested source"
        )
    existing = catalog.get_source_visual_materialization(visual.version_id)
    if existing is not None:
        artifact = catalog.get_artifact(existing.payload.artifact_id)
        if artifact is None:
            raise _MaterializationError(
                "ARTIFACT_METADATA_MISSING", "Stored artifact metadata is missing"
            )
        artifact_store.verify(artifact.payload)
        return SourceVisualOutcome(
            visual_version_id=visual.version_id,
            status="materialized",
            artifact_id=artifact.payload.artifact_id,
            evidence_id=existing.payload.evidence_id,
            reused=True,
        )
    archive, member, media_type = _zip_member(path, visual)
    try:
        with archive.open(member, "r") as media:
            write = artifact_store.put_stream(
                media,
                declared_media_type=media_type,
                expected_digest=visual.content_digest,
                byte_size_hint=member.file_size,
                clock=clock,
            )
    except ArtifactTooLarge as error:
        raise _MaterializationError(
            "ARTIFACT_TOO_LARGE", "PPTX media exceeds the artifact byte limit"
        ) from error
    except ArtifactValidationError as error:
        if "digest" in str(error):
            raise _MaterializationError(
                "VISUAL_DIGEST_MISMATCH", "PPTX media does not match visual digest"
            ) from error
        raise _MaterializationError(
            "ARTIFACT_INVALID", "PPTX media is unsupported or invalid"
        ) from error
    except ArtifactError as error:
        raise _MaterializationError(
            "ARTIFACT_WRITE_FAILED", "PPTX media could not be stored"
        ) from error
    finally:
        archive.close()
    if (
        write.metadata.media_type != visual.media_type
        or write.metadata.width != visual.width
        or write.metadata.height != visual.height
    ):
        raise _MaterializationError(
            "VISUAL_METADATA_MISMATCH", "PPTX media dimensions do not match visual metadata"
        )
    validate_source_visual_identity(catalog, source, visual)
    persisted_artifact = catalog.get_artifact(write.metadata.artifact_id)
    artifact_metadata = write.metadata
    metadata_reused = False
    if persisted_artifact is not None:
        artifact_store.verify(persisted_artifact.payload)
        if (
            persisted_artifact.payload.content_digest
            != write.metadata.content_digest
            or persisted_artifact.payload.byte_size != write.metadata.byte_size
            or persisted_artifact.payload.media_type != write.metadata.media_type
            or persisted_artifact.payload.width != write.metadata.width
            or persisted_artifact.payload.height != write.metadata.height
        ):
            raise _MaterializationError(
                "ARTIFACT_METADATA_MISMATCH",
                "Stored artifact metadata does not match verified bytes",
            )
        artifact_metadata = persisted_artifact.payload
        metadata_reused = True
    if stream_sha256(path) != source.content_digest:
        raise _MaterializationError(
            "SOURCE_DIGEST_MISMATCH", "Source bytes changed during materialization"
        )
    created_at = write.metadata.created_at
    evidence = _evidence(
        source=source,
        visual=visual,
        artifact=artifact_metadata,
        created_at=created_at,
    )
    materialization = _materialization(
        source=source,
        visual=visual,
        artifact=artifact_metadata,
        evidence=evidence,
        created_at=created_at,
    )
    with catalog.atomic_write():
        concurrent = catalog.get_source_visual_materialization(visual.version_id)
        if concurrent is not None:
            value = concurrent.payload
            if (
                value.artifact_id != artifact_metadata.artifact_id
                or value.source_version_id != source.version_id
                or value.source_content_digest != source.content_digest
                or value.visual_content_digest != visual.content_digest
                or value.slide_number != visual.source_locator.slide_number
                or value.relationship_id != visual.source_locator.relationship_id
            ):
                raise _MaterializationError(
                    "MATERIALIZATION_CONFLICT",
                    "Concurrent source visual materialization has different semantics",
                )
            return SourceVisualOutcome(
                visual_version_id=visual.version_id,
                status="materialized",
                artifact_id=value.artifact_id,
                evidence_id=value.evidence_id,
                reused=True,
            )
        current_artifact = catalog.get_artifact(artifact_metadata.artifact_id)
        if current_artifact is not None:
            if (
                current_artifact.payload.content_digest
                != artifact_metadata.content_digest
                or current_artifact.payload.byte_size != artifact_metadata.byte_size
                or current_artifact.payload.media_type != artifact_metadata.media_type
                or current_artifact.payload.width != artifact_metadata.width
                or current_artifact.payload.height != artifact_metadata.height
            ):
                raise _MaterializationError(
                    "ARTIFACT_METADATA_MISMATCH",
                    "Concurrent artifact metadata has different semantics",
                )
            artifact_metadata = current_artifact.payload
            metadata_reused = True
        artifact_store.verify(artifact_metadata)
        catalog.register_artifact(artifact_metadata)
        catalog.insert_evidence(evidence)
        catalog.register_source_visual_materialization(materialization)
        catalog.insert_lineage(
            LineageEdge(
                edge_id="source-visual-lineage-"
                + canonical_digest(
                    {
                        "artifact_id": artifact_metadata.artifact_id,
                        "visual_version_id": visual.version_id,
                        "evidence_id": evidence.evidence_id,
                    }
                ),
                from_version_id=artifact_metadata.artifact_id,
                to_version_id=visual.version_id,
                relation="derived_from",
                evidence_id=evidence.evidence_id,
                created_at=created_at,
            )
        )
    return SourceVisualOutcome(
        visual_version_id=visual.version_id,
        status="materialized",
        artifact_id=artifact_metadata.artifact_id,
        evidence_id=evidence.evidence_id,
        reused=write.reused or metadata_reused,
    )


def materialize_source_visuals(
    catalog: KnowledgeCatalog,
    source_roots: SourceRootRegistry,
    artifact_store: ArtifactStore,
    *,
    source_version_id: str,
    visual_version_ids: tuple[str, ...] | None = None,
    clock: Clock,
) -> tuple[SourceVisualOutcome, ...]:
    """Materialize requested visuals independently; never expose a local path."""

    requested = _visual_ids(catalog, source_version_id, visual_version_ids)
    try:
        source = _source(catalog, source_version_id)
    except _MaterializationError as error:
        return tuple(
            SourceVisualOutcome(
                visual_version_id=visual_id,
                status="failed",
                error_code=error.code,
                message=error.safe_message,
            )
            for visual_id in requested
        )
    outcomes: list[SourceVisualOutcome] = []
    for visual_id in requested:
        try:
            outcome = _materialize_one(
                catalog,
                source_roots,
                artifact_store,
                source,
                visual_id,
                clock=clock,
            )
        except _MaterializationError as error:
            outcome = SourceVisualOutcome(
                visual_version_id=visual_id,
                status="failed",
                error_code=error.code,
                message=error.safe_message,
            )
        except (
            CatalogMigrationError,
            CatalogReferenceError,
            ImmutableVersionConflict,
            OSError,
            ValueError,
        ):
            outcome = SourceVisualOutcome(
                visual_version_id=visual_id,
                status="failed",
                error_code="MATERIALIZATION_FAILED",
                message="Source visual could not be materialized",
            )
        outcomes.append(outcome)
    return tuple(outcomes)


__all__ = [
    "SourceVisualMaterialization",
    "SourceVisualOutcome",
    "materialize_source_visuals",
    "validate_source_visual_identity",
]
