from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid5

from course_helper.cards import (
    VOCABULARY_VERSION_ID,
    build_candidates,
    canonical_card_content_digest,
    create_review_task,
    find_exact_duplicate,
    seed_vocabulary,
)
from course_helper.catalog import CatalogReferenceError, KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.knowledge import KnowledgeCardVersion, TagAssignment
from course_helper.domain.sources import (
    DatasetAssetVersion,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.near_duplicates import scan_near_duplicates
from course_helper.parsers.dataset_profiler import DatasetProfiler
from course_helper.parsers.markdown_parser import MarkdownParser
from course_helper.parsers.pptx_parser import PptxParser
from course_helper.source_roots import (
    COURSE_STUDIO_ID_NAMESPACE,
    SourceRootRegistry,
    candidate_logical_id,
    candidate_version_id,
    chunk_logical_id,
    chunk_version_id,
)


@dataclass(frozen=True)
class GovernedImportResult:
    source_version_id: str
    chunk_count: int
    visual_count: int
    visual_version_ids: tuple[str, ...]
    candidate_card_version_ids: tuple[str, ...]
    review_task_ids: tuple[str, ...]
    extraction_evidence_id: str


@dataclass(frozen=True)
class GovernedDatasetImportResult:
    source_version_id: str
    dataset_version_id: str
    review_task_ids: tuple[str, ...]
    profile_evidence_id: str


def parse_promoted_source(
    catalog: KnowledgeCatalog,
    *,
    source: SourceAssetVersion,
    app_data_path: Path,
    actor: ActorRef,
) -> ExtractionResult:
    """Parse one verified governed blob through a short-lived contained root."""

    row = catalog.connection.execute(
        "SELECT safe_name, source_kind, blob_digest, byte_size FROM governed_source_blobs "
        "WHERE source_version_id = ?",
        (source.version_id,),
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("governed source blob is unavailable")
    safe_name, source_kind, blob_digest, byte_size = (
        str(row[0]),
        str(row[1]),
        str(row[2]),
        int(row[3]),
    )
    if (
        safe_name != Path(safe_name).name
        or source_kind != source.source_kind
        or blob_digest != source.content_digest
        or byte_size != source.byte_size
        or source.locator.root_id != "governed-upload"
    ):
        raise CatalogReferenceError("governed source blob envelope is inconsistent")
    blob_root = Path(app_data_path) / "source-blobs"
    blob = SourceRootRegistry({"governed-upload": blob_root}).resolve(source.locator)
    _verify_regular_blob(blob, expected_digest=blob_digest, expected_size=byte_size)

    work_root = Path(app_data_path) / "import-work"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parse-", dir=work_root) as directory:
        temporary_root = Path(directory)
        temporary_source = temporary_root / safe_name
        shutil.copyfile(blob, temporary_source)
        _verify_regular_blob(
            temporary_source,
            expected_digest=blob_digest,
            expected_size=byte_size,
            require_single_link=False,
        )
        registry = SourceRootRegistry({"governed-import": temporary_root})
        locator = SourceLocator(
            root_id="governed-import", relative_path=temporary_source.name
        )
        if source_kind == "markdown":
            parsed = MarkdownParser(registry).parse(locator)
        elif source_kind == "pptx":
            parsed = PptxParser(registry).parse(locator)
        else:
            raise CatalogReferenceError("governed content parser is unsupported")
        return _rebind_extraction(parsed, source=source, actor=actor)


def persist_governed_import(
    catalog: KnowledgeCatalog,
    *,
    extraction: ExtractionResult,
    actor: ActorRef,
) -> GovernedImportResult:
    """Persist one already-parsed extraction inside the caller's transaction."""

    if not catalog.connection.in_transaction or catalog._atomic_depth <= 0:
        raise RuntimeError("governed import persistence requires an operation transaction")
    seed_vocabulary(catalog)
    for chunk in extraction.chunks:
        catalog.insert_chunk(chunk)
    for visual in extraction.visuals:
        catalog.insert_visual(visual)
    catalog.insert_evidence(extraction.evidence)

    card_ids: list[str] = []
    review_ids: set[str] = set()
    for draft in build_candidates(extraction):
        reviewed = _review_candidate(draft, extraction=extraction, actor=actor)
        stored = catalog.insert_card(reviewed)
        card_ids.append(stored.version_id)
        provenance = create_review_task(
            catalog,
            kind="provenance",
            subject_version_id=stored.version_id,
            blocking=True,
            evidence_ids=(extraction.evidence.evidence_id,),
            created_at=extraction.source.created_at,
            created_by=actor,
        )
        review_ids.add(provenance.task_id)
        if find_exact_duplicate(stored, catalog) is not None:
            exact = create_review_task(
                catalog,
                kind="exact-duplicate",
                subject_version_id=stored.version_id,
                blocking=True,
                evidence_ids=(extraction.evidence.evidence_id,),
                created_at=extraction.source.created_at,
                created_by=actor,
            )
            review_ids.add(exact.task_id)
        near = scan_near_duplicates(
            stored,
            catalog,
            embedding_provider=None,
            created_at=extraction.source.created_at,
            _allow_active_transaction=True,
        )
        if near.review_task is not None:
            review_ids.add(near.review_task.task_id)

    return GovernedImportResult(
        source_version_id=extraction.source.version_id,
        chunk_count=len(extraction.chunks),
        visual_count=len(extraction.visuals),
        visual_version_ids=tuple(visual.version_id for visual in extraction.visuals),
        candidate_card_version_ids=tuple(card_ids),
        review_task_ids=tuple(sorted(review_ids)),
        extraction_evidence_id=extraction.evidence.evidence_id,
    )


def profile_promoted_dataset(
    catalog: KnowledgeCatalog,
    *,
    source: SourceAssetVersion,
    app_data_path: Path,
    actor: ActorRef,
) -> DatasetAssetVersion:
    """Profile one promoted tabular blob through a short-lived contained root."""

    row = catalog.connection.execute(
        "SELECT safe_name, source_kind, blob_digest, byte_size FROM governed_source_blobs "
        "WHERE source_version_id = ?",
        (source.version_id,),
    ).fetchone()
    if row is None:
        raise CatalogReferenceError("governed dataset blob is unavailable")
    safe_name, source_kind, blob_digest, byte_size = (
        str(row[0]),
        str(row[1]),
        str(row[2]),
        int(row[3]),
    )
    if (
        safe_name != Path(safe_name).name
        or source_kind != source.source_kind
        or blob_digest != source.content_digest
        or byte_size != source.byte_size
        or source.locator.root_id != "governed-upload"
    ):
        raise CatalogReferenceError("governed dataset blob envelope is inconsistent")
    blob = SourceRootRegistry(
        {"governed-upload": Path(app_data_path) / "source-blobs"}
    ).resolve(source.locator)
    _verify_regular_blob(blob, expected_digest=blob_digest, expected_size=byte_size)

    work_root = Path(app_data_path) / "import-work"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="profile-", dir=work_root) as directory:
        temporary_root = Path(directory)
        temporary_source = temporary_root / safe_name
        shutil.copyfile(blob, temporary_source)
        _verify_regular_blob(
            temporary_source,
            expected_digest=blob_digest,
            expected_size=byte_size,
            require_single_link=False,
        )
        registry = SourceRootRegistry({"governed-import": temporary_root})
        locator = SourceLocator(
            root_id="governed-import", relative_path=temporary_source.name
        )
        profiler = DatasetProfiler(registry)
        if source_kind == "csv":
            profile = profiler.profile_csv(locator, sample_limit=20)
        elif source_kind == "parquet":
            profile = profiler.profile_parquet(locator, sample_limit=20)
        elif source_kind in {"xls", "xlsx"}:
            profile = profiler.profile_xlsx(locator, sample_limit=20)
        else:
            raise CatalogReferenceError("governed dataset profiler is unsupported")
        return _rebind_dataset(profile, source=source, actor=actor)


def persist_governed_dataset(
    catalog: KnowledgeCatalog,
    *,
    dataset: DatasetAssetVersion,
    source: SourceAssetVersion,
    actor: ActorRef,
) -> GovernedDatasetImportResult:
    if not catalog.connection.in_transaction or catalog._atomic_depth <= 0:
        raise RuntimeError("governed dataset persistence requires an operation transaction")
    stored = catalog.insert_dataset(dataset)
    catalog.insert_evidence(stored.evidence)
    review_ids: set[str] = set()
    reference = create_review_task(
        catalog,
        kind="dataset-reference",
        subject_version_id=stored.version_id,
        blocking=True,
        evidence_ids=(stored.evidence.evidence_id,),
        created_at=source.created_at,
        created_by=actor,
    )
    review_ids.add(reference.task_id)
    if stored.review_status != "ready":
        grain = create_review_task(
            catalog,
            kind="grain-needs-review",
            subject_version_id=stored.version_id,
            blocking=True,
            evidence_ids=(stored.evidence.evidence_id,),
            created_at=source.created_at,
            created_by=actor,
        )
        review_ids.add(grain.task_id)
    if any(column.sensitive_category is not None for column in stored.columns):
        sensitive = create_review_task(
            catalog,
            kind="sensitive-sample",
            subject_version_id=stored.version_id,
            blocking=True,
            evidence_ids=(stored.evidence.evidence_id,),
            created_at=source.created_at,
            created_by=actor,
        )
        review_ids.add(sensitive.task_id)
    return GovernedDatasetImportResult(
        source_version_id=source.version_id,
        dataset_version_id=stored.version_id,
        review_task_ids=tuple(sorted(review_ids)),
        profile_evidence_id=stored.evidence.evidence_id,
    )


def _review_candidate(
    draft: KnowledgeCardVersion,
    *,
    extraction: ExtractionResult,
    actor: ActorRef,
) -> KnowledgeCardVersion:
    data_type = (
        "dataType:presentation"
        if extraction.source.source_kind == "pptx"
        else "dataType:text"
    )
    tags = tuple(
        TagAssignment(
            vocabulary_version_id=VOCABULARY_VERSION_ID,
            dimension_id=dimension,
            tag_id=tag,
        )
        for dimension, tag in (
            ("audience", "audience:learner"),
            ("difficulty", "difficulty:beginner"),
            ("pedagogy", "pedagogy:explain"),
            ("tool", "tool:agnostic"),
            ("scenario", "scenario:course-learning"),
            ("dataType", data_type),
        )
    )
    provisional = draft.model_copy(
        update={
            "created_at": extraction.source.created_at,
            "created_by": actor,
            "status": "review",
            "tag_assignments": tags,
        }
    )
    digest = canonical_card_content_digest(provisional)
    parents = (
        extraction.source.version_id,
        *(citation.chunk_id for citation in provisional.chunk_citations),
    )
    return provisional.model_copy(
        update={
            "content_digest": digest,
            "version_id": candidate_version_id(
                provisional.logical_id, parents, digest
            ),
        }
    )


def _rebind_extraction(
    parsed: ExtractionResult,
    *,
    source: SourceAssetVersion,
    actor: ActorRef,
) -> ExtractionResult:
    chunk_id_map: dict[str, str] = {}
    for chunk in parsed.chunks:
        logical_id = chunk_logical_id(source.logical_id, chunk.locator)
        chunk_id_map[chunk.chunk_id] = chunk_version_id(
            logical_id, source.version_id, chunk.content_digest
        )

    visual_id_map: dict[str, str] = {}
    rebound_visuals: list[VisualAssetVersion] = []
    for visual in parsed.visuals:
        locator = visual.source_locator
        if (
            locator is None
            or locator.slide_number is None
            or not locator.relationship_id
        ):
            raise CatalogReferenceError(
                "governed PPTX visual relationship identity is incomplete"
            )
        logical_id = candidate_logical_id(
            "visual",
            f"{source.logical_id}:slide-{locator.slide_number}:"
            f"{locator.relationship_id}",
        )
        parents = tuple(
            dict.fromkeys(
                chunk_id_map.get(
                    parent,
                    source.version_id
                    if parent == parsed.source.version_id
                    else parent,
                )
                for parent in visual.derived_from_version_ids
            )
        ) or (source.version_id,)
        version_id = candidate_version_id(logical_id, parents, visual.content_digest)
        visual_id_map[visual.version_id] = version_id
        rebound_visuals.append(
            visual.model_copy(
                update={
                    "logical_id": logical_id,
                    "version_id": version_id,
                    "created_at": source.created_at,
                    "created_by": actor,
                    "derived_from_version_ids": parents,
                }
            )
        )

    rebound_chunks = tuple(
        chunk.model_copy(
            update={
                "chunk_id": chunk_id_map[chunk.chunk_id],
                "source_version_id": source.version_id,
                "media_version_ids": tuple(
                    visual_id_map[item]
                    for item in chunk.media_version_ids
                    if item in visual_id_map
                ),
            }
        )
        for chunk in parsed.chunks
    )
    evidence_id = "import-extraction-" + hashlib.sha256(
        f"{source.version_id}\0{parsed.evidence.evidence_id}".encode("utf-8")
    ).hexdigest()[:48]
    evidence = EvidenceObject(
        evidence_id=evidence_id,
        kind="extraction",
        subject_version_id=source.version_id,
        status=parsed.evidence.status,
        input_summary={
            "sourceVersionId": source.version_id,
            "sourceKind": source.source_kind,
        },
        output_summary={
            "chunkCount": len(rebound_chunks),
            "visualCount": len(rebound_visuals),
        },
        producer=parsed.evidence.producer,
        producer_version=parsed.evidence.producer_version,
        started_at=source.created_at,
        finished_at=source.created_at,
        duration_ms=0,
        checks=parsed.evidence.checks,
        errors=parsed.evidence.errors,
    )
    return ExtractionResult(
        source=source,
        chunks=rebound_chunks,
        visuals=tuple(rebound_visuals),
        datasets=(),
        evidence=evidence,
    )


def _rebind_dataset(
    profile: DatasetAssetVersion,
    *,
    source: SourceAssetVersion,
    actor: ActorRef,
) -> DatasetAssetVersion:
    semantic_locator = f"{source.locator.root_id}:{source.locator.relative_path}"
    if profile.relation_name is not None:
        semantic_locator = json.dumps(
            {
                "root_id": source.locator.root_id,
                "relative_path": source.locator.relative_path,
                "relation_name": profile.relation_name,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    logical_id = candidate_logical_id("dataset", semantic_locator)
    version_id = candidate_version_id(logical_id, (), profile.content_digest)
    config_digest = profile.evidence.input_summary.get("profile_config_digest")
    if not isinstance(config_digest, str) or len(config_digest) != 64:
        raise CatalogReferenceError("governed dataset profile config is invalid")
    evidence_id = str(
        uuid5(
            COURSE_STUDIO_ID_NAMESPACE,
            f"evidence\0dataset-profile\0{version_id}\0{config_digest}",
        )
    )
    evidence = EvidenceObject(
        evidence_id=evidence_id,
        kind=profile.evidence.kind,
        subject_version_id=version_id,
        status=profile.evidence.status,
        input_summary={
            "source_locator": source.locator.model_dump(mode="json"),
            "profile_config_digest": config_digest,
        },
        output_summary={
            **profile.evidence.output_summary,
            "row_count": profile.row_count,
            "column_count": len(profile.columns),
        },
        producer=profile.evidence.producer,
        producer_version=profile.evidence.producer_version,
        started_at=source.created_at,
        finished_at=source.created_at,
        duration_ms=0,
        checks=profile.evidence.checks,
        errors=profile.evidence.errors,
    )
    return profile.model_copy(
        update={
            "logical_id": logical_id,
            "version_id": version_id,
            "created_at": source.created_at,
            "created_by": profile.created_by,
            "locator": source.locator,
            "evidence": evidence,
        }
    )


def _verify_regular_blob(
    path: Path,
    *,
    expected_digest: str,
    expected_size: int,
    require_single_link: bool = True,
) -> None:
    try:
        info = os.lstat(path)
        attributes = getattr(info, "st_file_attributes", 0)
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or (require_single_link and info.st_nlink != 1)
            or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        ):
            raise OSError("unsafe source blob")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                digest.update(block)
        if size != expected_size or digest.hexdigest() != expected_digest:
            raise OSError("source blob digest mismatch")
    except OSError as error:
        raise CatalogReferenceError("governed source blob is invalid") from error


__all__ = [
    "GovernedDatasetImportResult",
    "GovernedImportResult",
    "parse_promoted_source",
    "profile_promoted_dataset",
    "persist_governed_dataset",
    "persist_governed_import",
]
