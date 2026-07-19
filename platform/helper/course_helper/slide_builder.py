"""Deterministic, evidence-traced content-only course draft projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Callable

from pydantic import ValidationError

from course_helper.cards import canonical_card_content_digest
from course_helper.catalog import (
    CatalogMigrationError,
    CatalogReferenceError,
    KnowledgeCatalog,
    StoredImmutable,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.composition import (
    CourseVersion,
    canonical_digest,
    course_version_content_digest,
)
from course_helper.domain.knowledge import CardContentNode, KnowledgeCardVersion, ReviewTask
from course_helper.domain.slide_ast import (
    RuntimeJobBinding,
    RuntimeManifest,
    SlideAssetBinding,
    SlideDeckAst,
    SlideNode,
    runtime_manifest_content_digest,
    slide_deck_content_digest,
)
from course_helper.domain.sources import (
    DatasetAssetVersion,
    ExtractedChunk,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.domain.visual_policy import (
    VisualPlacement,
    VisualPolicyContext,
    evaluate_visual_publication,
)
from course_helper.network_visuals import (
    NetworkVisualAcquisition,
    NetworkVisualError,
    current_network_visual_verification,
)
from course_helper.operations import (
    OperationMutationResult,
    OperationOutcome,
    OperationRequest,
    run_operation,
)


Clock = Callable[[], datetime]


class SlideBuildError(ValueError):
    """A confirmed course cannot be projected without weakening traceability."""


@dataclass(frozen=True)
class DraftProjection:
    """Exact content-only draft models and their immutable catalog envelopes."""

    deck: SlideDeckAst
    runtime_manifest: RuntimeManifest
    stored_deck: StoredImmutable[SlideDeckAst]
    stored_manifest: StoredImmutable[RuntimeManifest]


@dataclass(frozen=True)
class CoursePublicationValidation:
    """A fully resolved immutable publication snapshot before catalog commit."""

    course: CourseVersion
    deck: SlideDeckAst
    runtime_manifest: RuntimeManifest
    course_projection_id: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _ResolvedVisual:
    placement: VisualPlacement
    visual: VisualAssetVersion
    artifact_id: str
    artifact_digest: str
    media_type: str
    evidence_ids: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _CardLineage:
    card: KnowledgeCardVersion
    chunk_ids: tuple[str, ...]
    source_version_ids: tuple[str, ...]
    source_labels: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    allowed_runtime_specs: tuple[tuple[str, str], ...]


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _ordered_unique(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _walk_nodes(nodes: tuple[SlideNode, ...]) -> tuple[SlideNode, ...]:
    ordered: list[SlideNode] = []
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(node.children))
    return tuple(ordered)


def _stage_text(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _raw_card(catalog: KnowledgeCatalog, version_id: str) -> KnowledgeCardVersion:
    row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM cards WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    lifecycle = catalog.connection.execute(
        "SELECT status, suspended FROM card_lifecycle_current WHERE card_version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError(f"pinned card is dangling: {version_id!r}")
    try:
        card = KnowledgeCardVersion.model_validate_json(row[1])
    except ValidationError as error:
        raise SlideBuildError("pinned card payload is invalid") from error
    if (
        canonical_model_json(card) != row[1]
        or card.version_id != version_id
        or card.content_digest != row[0]
        or canonical_card_content_digest(card) != card.content_digest
    ):
        raise SlideBuildError("pinned card content digest or storage envelope is invalid")
    if lifecycle != ("published", 0):
        raise SlideBuildError("pinned card lifecycle is suspended, revoked, or unpublished")
    return card.model_copy(update={"status": "published"})


def _chunk(catalog: KnowledgeCatalog, chunk_id: str) -> ExtractedChunk:
    row = catalog.connection.execute(
        "SELECT source_version_id, ordinal, content_digest, payload_json "
        "FROM chunks WHERE chunk_id = ?",
        (chunk_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError(f"pinned chunk is dangling: {chunk_id!r}")
    try:
        chunk = ExtractedChunk.model_validate_json(row[3])
    except ValidationError as error:
        raise SlideBuildError("pinned chunk payload is invalid") from error
    if (
        canonical_model_json(chunk) != row[3]
        or (chunk.source_version_id, chunk.ordinal, chunk.content_digest)
        != tuple(row[:3])
        or chunk.chunk_id != chunk_id
    ):
        raise SlideBuildError("pinned chunk digest or storage envelope is invalid")
    return chunk


def _source(catalog: KnowledgeCatalog, version_id: str) -> SourceAssetVersion:
    row = catalog.connection.execute(
        "SELECT logical_id, revision, content_digest, payload_json "
        "FROM sources WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError(f"pinned source is dangling: {version_id!r}")
    try:
        source = SourceAssetVersion.model_validate_json(row[3])
    except ValidationError as error:
        raise SlideBuildError("pinned source payload is invalid") from error
    if (
        canonical_model_json(source) != row[3]
        or (source.logical_id, source.revision, source.content_digest) != tuple(row[:3])
        or source.version_id != version_id
    ):
        raise SlideBuildError("pinned source digest or storage envelope is invalid")
    if not catalog.source_is_extractable(source):
        raise SlideBuildError("pinned source is revoked or not extractable")
    return source


def _verified_lineage_evidence(
    catalog: KnowledgeCatalog,
    *,
    card: KnowledgeCardVersion,
    target_version_id: str,
    relation: str,
) -> str:
    expected_evidence_id = "publish-" + canonical_digest(
        {"version_id": card.version_id, "content_digest": card.content_digest}
    )
    rows = catalog.connection.execute(
        "SELECT evidence_id FROM lineage WHERE from_version_id = ? "
        "AND to_version_id = ? AND relation = ? ORDER BY evidence_id",
        (card.version_id, target_version_id, relation),
    ).fetchall()
    verified: list[str] = []
    for row in rows:
        evidence = catalog._load_evidence(str(row[0]))
        if (
            evidence.evidence_id == expected_evidence_id
            and evidence.kind == "publish"
            and evidence.status == "verified"
            and evidence.subject_version_id == card.version_id
            and evidence.producer == "course-helper/cards"
            and evidence.producer_version == "1"
        ):
            verified.append(evidence.evidence_id)
    if not verified:
        raise SlideBuildError(
            f"pinned dependency lacks verified {relation} lineage: {target_version_id!r}"
        )
    return verified[0]


def _dataset(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
    version_id: str,
) -> tuple[DatasetAssetVersion, str]:
    row = catalog.connection.execute(
        "SELECT logical_id, revision, content_digest, payload_json "
        "FROM datasets WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError(f"pinned dataset is dangling: {version_id!r}")
    try:
        dataset = DatasetAssetVersion.model_validate_json(row[3])
    except ValidationError as error:
        raise SlideBuildError("pinned dataset payload is invalid") from error
    if (
        canonical_model_json(dataset) != row[3]
        or (dataset.logical_id, dataset.revision, dataset.content_digest) != tuple(row[:3])
        or dataset.version_id != version_id
        or dataset.review_status != "ready"
    ):
        raise SlideBuildError("pinned dataset digest, envelope, or review state is invalid")
    evidence_id = _verified_lineage_evidence(
        catalog,
        card=card,
        target_version_id=version_id,
        relation="uses",
    )
    return dataset, evidence_id


def _card_lineage(catalog: KnowledgeCatalog, version_id: str) -> _CardLineage:
    card = _raw_card(catalog, version_id)
    if not card.chunk_citations:
        raise SlideBuildError("every draft card requires chunk/source lineage")
    chunks: list[str] = []
    sources: list[str] = []
    source_labels: list[str] = []
    evidence: list[str] = []
    for citation in card.chunk_citations:
        chunk = _chunk(catalog, citation.chunk_id)
        if chunk.source_version_id != citation.source_version_id:
            raise SlideBuildError("pinned chunk and source citation do not match")
        source = _source(catalog, citation.source_version_id)
        if source.version_id != chunk.source_version_id:
            raise SlideBuildError("pinned source does not own its cited chunk")
        if citation.quoted_text:
            quote = " ".join(citation.quoted_text.split())
            body = " ".join(chunk.normalized_text.split())
            if quote not in body:
                raise SlideBuildError("pinned citation quote is not present in its chunk")
        evidence_id = _verified_lineage_evidence(
            catalog,
            card=card,
            target_version_id=chunk.chunk_id,
            relation="cites",
        )
        chunks.append(chunk.chunk_id)
        sources.append(source.version_id)
        source_labels.append(source.display_name)
        evidence.append(evidence_id)

    specs: list[tuple[str, str]] = []
    for reference in card.dataset_refs:
        _value, evidence_id = _dataset(
            catalog,
            card,
            reference.dataset_version_id,
        )
        for spec_id in reference.activity_spec_ids:
            specs.append((spec_id, evidence_id))
        evidence.append(evidence_id)
    return _CardLineage(
        card=card,
        chunk_ids=_ordered_unique(chunks),
        source_version_ids=_ordered_unique(sources),
        source_labels=_ordered_unique(source_labels),
        evidence_ids=_ordered_unique(evidence),
        allowed_runtime_specs=tuple(specs),
    )


def _reject_blocking_reviews(catalog: KnowledgeCatalog, subject_ids: set[str]) -> None:
    if not subject_ids:
        return
    ordered = tuple(sorted(subject_ids))
    for offset in range(0, len(ordered), 400):
        batch = ordered[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        rows = catalog.connection.execute(
            "SELECT tasks.payload_json, current.review_digest "
            "FROM review_tasks AS tasks "
            "JOIN review_task_current AS current USING(task_id) "
            f"WHERE tasks.subject_version_id IN ({placeholders}) "
            "AND current.current_status = 'open' ORDER BY tasks.task_id",
            batch,
        ).fetchall()
        for payload_json, review_digest in rows:
            try:
                task = ReviewTask.model_validate_json(payload_json)
            except ValidationError as error:
                raise SlideBuildError("blocking review payload is invalid") from error
            digest = hashlib.sha256(str(payload_json).encode("utf-8")).hexdigest()
            if canonical_model_json(task) != payload_json or digest != review_digest:
                raise SlideBuildError("blocking review projection digest is invalid")
            if task.blocking:
                raise SlideBuildError(
                    "pinned dependency has an unresolved blocking review: "
                    f"{task.task_id}"
                )


def _node_type(node: CardContentNode) -> str:
    return {
        "paragraph": "paragraph",
        "heading": "heading",
        "list": "bullet-list",
        "list-item": "paragraph",
        "code": "code",
        "quote": "quote",
        "callout": "callout",
        "table": "table",
        "image": "callout",
        "dataset-activity": "activity",
    }[node.type]


def _content_node(
    value: CardContentNode,
    *,
    course_version_id: str,
    placement_id: str,
    lineage: _CardLineage,
    path: tuple[int, ...],
) -> SlideNode | None:
    items: tuple[str, ...] = ()
    direct_children = value.children
    if value.type == "list":
        items = tuple(
            text
            for child in direct_children
            if child.type == "list-item"
            for text in (_stage_text(child.text),)
            if text is not None
        )[:100]
        direct_children = tuple(
            child for child in direct_children if child.type != "list-item"
        )
    elif value.type == "table":
        items = tuple(
            text
            for row in value.rows[:100]
            for text in (_stage_text(" | ".join(row)),)
            if text is not None
        )
    text = _stage_text(value.text)
    children = tuple(
        child
        for index, item in enumerate(direct_children)
        for child in (
            _content_node(
                item,
                course_version_id=course_version_id,
                placement_id=placement_id,
                lineage=lineage,
                path=(*path, index),
            ),
        )
        if child is not None
    )
    if text is None and not items and not children:
        return None
    path_value = ".".join(str(item) for item in path)
    return SlideNode(
        node_id=_stable_id(
            "node",
            course_version_id,
            placement_id,
            lineage.card.version_id,
            path_value,
            value.type,
        ),
        node_type=_node_type(value),
        text=text,
        items=items,
        placement_ids=(placement_id,),
        card_version_ids=(lineage.card.version_id,),
        chunk_ids=lineage.chunk_ids,
        source_version_ids=lineage.source_version_ids,
        evidence_ids=lineage.evidence_ids,
        children=children,
    )


def _slide(
    *,
    course_version_id: str,
    placement_id: str,
    placement_purpose: str,
    lineage: _CardLineage,
) -> SlideNode:
    children = tuple(
        child
        for index, item in enumerate(lineage.card.content_ast)
        for child in (
            _content_node(
                item,
                course_version_id=course_version_id,
                placement_id=placement_id,
                lineage=lineage,
                path=(index,),
            ),
        )
        if child is not None
    )
    evidence_summary = ", ".join(lineage.source_labels)
    presenter_notes = (
        f"Learning objective: {lineage.card.learning_objective}\n"
        f"Placement purpose: {placement_purpose}\n"
        f"Evidence sources: {evidence_summary}\n"
        "Use the cited evidence to explain the stage content; do not add unverified claims."
    )
    return SlideNode(
        node_id=_stable_id(
            "slide",
            course_version_id,
            placement_id,
            lineage.card.version_id,
        ),
        node_type="slide",
        text=_stage_text(lineage.card.title),
        placement_ids=(placement_id,),
        card_version_ids=(lineage.card.version_id,),
        chunk_ids=lineage.chunk_ids,
        source_version_ids=lineage.source_version_ids,
        evidence_ids=lineage.evidence_ids,
        presenter_notes=presenter_notes,
        children=children,
    )


def _validate_runtime_jobs(
    job_bindings: tuple[RuntimeJobBinding, ...],
    lineages: tuple[_CardLineage, ...],
) -> None:
    allowed = {
        spec_id: evidence_id
        for lineage in lineages
        for spec_id, evidence_id in lineage.allowed_runtime_specs
    }
    if len({job.job_id for job in job_bindings}) != len(job_bindings):
        raise SlideBuildError("runtime job IDs must be unique")
    for job in job_bindings:
        if job.job_type not in {"dataset_sql", "chart_build"}:
            raise SlideBuildError(
                "runtime job type is unsafe before a typed allowlisted specification exists"
            )
        if allowed.get(job.spec_id) != job.evidence_id:
            raise SlideBuildError(
                "runtime job spec or evidence is not pinned by the selected cards"
            )


def _stored_visual(catalog: KnowledgeCatalog, version_id: str) -> VisualAssetVersion:
    row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM visuals WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError("visual placement references a dangling visual")
    try:
        visual = VisualAssetVersion.model_validate_json(row[1])
    except ValidationError as error:
        raise SlideBuildError("visual storage envelope is invalid") from error
    if (
        canonical_model_json(visual) != row[1]
        or visual.version_id != version_id
        or visual.content_digest != row[0]
    ):
        raise SlideBuildError("visual digest or storage envelope is invalid")
    return visual


def _verified_visual_evidence(
    catalog: KnowledgeCatalog,
    evidence_id: str,
    *,
    visual_version_id: str,
    producer: str,
) -> object:
    evidence = catalog._load_evidence(evidence_id)
    if (
        evidence.status != "verified"
        or evidence.subject_version_id != visual_version_id
        or evidence.producer != producer
    ):
        raise SlideBuildError("visual evidence is not exact and verified")
    return evidence


def _content_changed(placement: VisualPlacement) -> bool:
    transform = placement.transformation
    crop = transform.crop
    return (
        (crop is not None and (crop.x, crop.y, crop.width, crop.height) != (0.0, 0.0, 1.0, 1.0))
        or bool(transform.color_adjustments)
        or transform.scale_mode == "cover"
    )


def _transformation_is_compatible(placement: VisualPlacement, license_id: str) -> bool:
    transform = placement.transformation
    if transform.derivative_license_decision in {"prohibited", "requires-review"}:
        return False
    if _content_changed(placement) and not transform.change_notice:
        return False
    if license_id in {"CC-BY-SA-3.0", "CC-BY-SA-4.0"} and _content_changed(placement):
        return (
            transform.derivative_license_decision == "same-license"
            and transform.export_license == license_id
            and transform.share_alike_compatible
        )
    if license_id == "GFDL-1.2-OR-LATER" and _content_changed(placement):
        return (
            transform.derivative_license_decision == "same-license"
            and transform.export_license == license_id
            and transform.gfdl_compatible
        )
    return True


def _ordinary_attribution_matches(
    placement: VisualPlacement,
    visual: VisualAssetVersion,
) -> bool:
    attribution = placement.attribution
    return (
        attribution.title == placement.alt_text
        and attribution.creator == visual.author
        and attribution.publisher == visual.publisher
        and attribution.license_label == visual.license_status
        and attribution.landing_link is None
        and attribution.license_link is None
    )


def _resolve_source_visual(
    catalog: KnowledgeCatalog,
    placement: VisualPlacement,
    visual: VisualAssetVersion,
) -> tuple[str, tuple[str, ...], bool, str]:
    materialization = catalog.get_source_visual_materialization(visual.version_id)
    if materialization is None:
        raise SlideBuildError("source visual materialization is missing")
    value = materialization.payload
    if (
        placement.originating_source_version_id != value.source_version_id
        or value.visual_content_digest != visual.content_digest
        or placement.authenticity_evidence_id != value.evidence_id
        or placement.license_evidence_id != value.evidence_id
    ):
        raise SlideBuildError("source visual placement does not match its materialization")
    _verified_visual_evidence(
        catalog,
        value.evidence_id,
        visual_version_id=visual.version_id,
        producer="course-helper/source-visuals",
    )
    return (
        value.artifact_id,
        (value.evidence_id,),
        visual.license_status in {"verified", "source-provided"},
        visual.license_status,
    )


def _stored_dataset(catalog: KnowledgeCatalog, version_id: str) -> DatasetAssetVersion:
    row = catalog.connection.execute(
        "SELECT logical_id, revision, content_digest, payload_json FROM datasets "
        "WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError("data visual references a dangling dataset")
    try:
        dataset = DatasetAssetVersion.model_validate_json(row[3])
    except ValidationError as error:
        raise SlideBuildError("data visual dataset envelope is invalid") from error
    if (
        canonical_model_json(dataset) != row[3]
        or (dataset.logical_id, dataset.revision, dataset.content_digest) != tuple(row[:3])
        or dataset.version_id != version_id
        or dataset.review_status not in {"ready", "needs-review"}
    ):
        raise SlideBuildError("data visual dataset digest or review state is invalid")
    if dataset.review_status == "needs-review":
        _reject_blocking_reviews(catalog, {dataset.version_id})
    return dataset


def _resolve_data_visual(
    catalog: KnowledgeCatalog,
    placement: VisualPlacement,
    visual: VisualAssetVersion,
) -> tuple[str, tuple[str, ...], bool, str]:
    dataset_id = placement.originating_dataset_version_id
    if dataset_id is None or visual.derived_from_version_ids != (dataset_id,):
        raise SlideBuildError("data visual does not bind its exact dataset version")
    dataset = _stored_dataset(catalog, dataset_id)
    evidence_id = placement.authenticity_evidence_id
    if placement.license_evidence_id != evidence_id:
        raise SlideBuildError("data visual requires one exact execution evidence object")
    evidence = _verified_visual_evidence(
        catalog,
        evidence_id,
        visual_version_id=visual.version_id,
        producer="course-helper/chart-builder",
    )
    artifact_id = evidence.output_summary.get("artifact_id")
    if (
        not isinstance(artifact_id, str)
        or evidence.output_summary.get("artifact_digest") != visual.content_digest
        or evidence.output_summary.get("visual_version_id") != visual.version_id
        or evidence.input_summary.get("dataset_version_id") != dataset.version_id
        or evidence.input_summary.get("dataset_content_digest") != dataset.content_digest
    ):
        raise SlideBuildError("data visual evidence does not bind exact output semantics")
    artifact_edge = catalog.connection.execute(
        "SELECT 1 FROM lineage WHERE from_version_id = ? AND to_version_id = ? "
        "AND relation = 'derived_from' AND evidence_id = ?",
        (artifact_id, visual.version_id, evidence_id),
    ).fetchone()
    dataset_edge = catalog.connection.execute(
        "SELECT 1 FROM lineage WHERE from_version_id = ? AND to_version_id = ? "
        "AND relation = 'derived_from' AND evidence_id = ?",
        (visual.version_id, dataset.version_id, evidence_id),
    ).fetchone()
    if artifact_edge is None or dataset_edge is None:
        raise SlideBuildError("data visual lineage is incomplete")
    return artifact_id, (evidence_id,), True, visual.license_status


def _network_acquisition(
    catalog: KnowledgeCatalog,
    visual_version_id: str,
) -> NetworkVisualAcquisition:
    row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM network_visual_acquisitions "
        "WHERE visual_version_id = ?",
        (visual_version_id,),
    ).fetchone()
    if row is None:
        raise SlideBuildError("network visual acquisition is missing")
    try:
        value = NetworkVisualAcquisition.model_validate_json(row[1])
    except ValidationError as error:
        raise SlideBuildError("network visual acquisition envelope is invalid") from error
    if (
        canonical_model_json(value) != row[1]
        or hashlib.sha256(row[1].encode("utf-8")).hexdigest() != row[0]
        or value.visual_version_id != visual_version_id
    ):
        raise SlideBuildError("network visual acquisition digest is invalid")
    return value


def _resolve_network_visual(
    catalog: KnowledgeCatalog,
    placement: VisualPlacement,
    visual: VisualAssetVersion,
    *,
    now: datetime,
) -> tuple[str, tuple[str, ...], bool, str, datetime]:
    acquisition = _network_acquisition(catalog, visual.version_id)
    try:
        verification = current_network_visual_verification(
            catalog, visual.version_id, now=now
        )
    except NetworkVisualError as error:
        raise SlideBuildError("network visual verification is missing or invalid") from error
    if (
        verification.status != "verified"
        or placement.authenticity_evidence_id != verification.evidence_id
        or placement.license_evidence_id != acquisition.evidence_id
        or verification.license_id != acquisition.license_id
        or verification.provider_sha1 != acquisition.provider_sha1
    ):
        raise SlideBuildError("network visual authorization is stale or changed")
    _verified_visual_evidence(
        catalog,
        verification.evidence_id,
        visual_version_id=visual.version_id,
        producer="course-helper/network-visuals",
    )
    _verified_visual_evidence(
        catalog,
        acquisition.evidence_id,
        visual_version_id=visual.version_id,
        producer="course-helper/network-visuals",
    )
    attribution = placement.attribution
    if (
        attribution.title != acquisition.title
        or attribution.creator != acquisition.creator
        or attribution.publisher != "Wikimedia Commons"
        or attribution.license_label != acquisition.license_id
        or attribution.landing_link != acquisition.landing_link
        or attribution.license_link != acquisition.license_link
    ):
        raise SlideBuildError("network visual attribution is not exact")
    return (
        acquisition.artifact_id,
        tuple(dict.fromkeys((verification.evidence_id, acquisition.evidence_id))),
        True,
        acquisition.license_id,
        verification.verified_at,
    )


def _resolve_visual(
    catalog: KnowledgeCatalog,
    placement: VisualPlacement,
    *,
    usage_scope: str,
    now: datetime,
) -> _ResolvedVisual:
    visual = _stored_visual(catalog, placement.visual_version_id)
    if visual.usage_scope and usage_scope not in visual.usage_scope:
        raise SlideBuildError("visual is not authorized for the course scope")
    network_verified_at: datetime | None = None
    if visual.authenticity == "source-provided":
        artifact_id, evidence_ids, rights_verified, license_id = _resolve_source_visual(
            catalog, placement, visual
        )
        attribution_verified = _ordinary_attribution_matches(placement, visual)
        dataset_verified = False
    elif visual.authenticity == "data-derived":
        artifact_id, evidence_ids, rights_verified, license_id = _resolve_data_visual(
            catalog, placement, visual
        )
        attribution_verified = _ordinary_attribution_matches(placement, visual)
        dataset_verified = True
    elif visual.authenticity == "licensed-secondary":
        (
            artifact_id,
            evidence_ids,
            rights_verified,
            license_id,
            network_verified_at,
        ) = _resolve_network_visual(catalog, placement, visual, now=now)
        attribution_verified = True
        dataset_verified = False
    else:
        raise SlideBuildError("publication accepts only governed source, data, or network visuals")
    if not attribution_verified:
        raise SlideBuildError("visual attribution does not match its immutable source")
    transformation_compatible = _transformation_is_compatible(placement, license_id)
    decision = evaluate_visual_publication(
        VisualPolicyContext(
            usage_scope=usage_scope,
            authenticity=visual.authenticity,
            license_status=visual.license_status,
            rights_verified=rights_verified,
            network_metadata_verified_at=network_verified_at,
            dataset_integrity_verified=dataset_verified,
            attribution_verified=attribution_verified,
            lineage_valid=True,
            transformation_compatible=transformation_compatible,
            now=now,
        )
    )
    if not decision.allowed:
        raise SlideBuildError(
            "visual publication policy blocked: " + ",".join(decision.blockers)
        )
    artifact = catalog.get_artifact(artifact_id)
    if artifact is None:
        raise SlideBuildError("visual artifact metadata is missing")
    metadata = artifact.payload
    if (
        metadata.content_digest != visual.content_digest
        or metadata.media_type != visual.media_type
        or metadata.width != visual.width
        or metadata.height != visual.height
        or (visual.authenticity != "data-derived" and metadata.media_type == "image/svg+xml")
    ):
        raise SlideBuildError("visual artifact MIME, dimensions, or digest is invalid")
    return _ResolvedVisual(
        placement=placement,
        visual=visual,
        artifact_id=metadata.artifact_id,
        artifact_digest=metadata.content_digest,
        media_type=metadata.media_type,
        evidence_ids=evidence_ids,
        warnings=decision.warnings,
    )


def _bind_visuals(
    nodes: tuple[SlideNode, ...],
    resolved: tuple[_ResolvedVisual, ...],
) -> tuple[SlideNode, ...]:
    by_node: dict[str, list[_ResolvedVisual]] = {}
    for item in resolved:
        by_node.setdefault(item.placement.slide_node_id, []).append(item)
    used: set[str] = set()

    def bind(node: SlideNode) -> SlideNode:
        children = tuple(bind(child) for child in node.children)
        bindings: list[SlideAssetBinding] = []
        evidence_ids = list(node.evidence_ids)
        seen_slots: set[str] = set()
        for item in by_node.get(node.node_id, []):
            placement = item.placement
            if placement.slot_id in seen_slots:
                raise SlideBuildError("visual placement slots must be unique per slide node")
            seen_slots.add(placement.slot_id)
            used.add(placement.placement_id)
            for evidence_id in item.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
            attribution_id = "attribution-" + canonical_digest(
                placement.attribution.model_dump(mode="json", exclude_none=True)
            )
            bindings.append(
                SlideAssetBinding(
                    binding_id=_stable_id(
                        "binding",
                        placement.placement_id,
                        item.artifact_id,
                        item.artifact_digest,
                    ),
                    visual_placement_id=placement.placement_id,
                    visual_version_id=item.visual.version_id,
                    artifact_id=item.artifact_id,
                    artifact_digest=item.artifact_digest,
                    media_type=item.media_type,
                    alt_text=placement.alt_text,
                    authenticity_evidence_id=placement.authenticity_evidence_id,
                    license_evidence_id=placement.license_evidence_id,
                    attribution_id=attribution_id,
                    attribution=placement.attribution,
                    transformation_id=placement.transformation.transformation_id,
                    transformation=placement.transformation,
                )
            )
        return node.model_copy(
            update={
                "children": children,
                "asset_bindings": tuple(bindings),
                "evidence_ids": tuple(evidence_ids),
            }
        )

    bound = tuple(bind(node) for node in nodes)
    expected = {item.placement.placement_id for item in resolved}
    if used != expected:
        raise SlideBuildError("visual placement targets a missing slide node")
    return bound


def _build_and_register(
    catalog: KnowledgeCatalog,
    course_version_id: str,
    *,
    actor: ActorRef,
    clock: Clock,
    job_bindings: tuple[RuntimeJobBinding, ...],
) -> DraftProjection:
    stored_course = catalog.get_course_version(course_version_id)
    if stored_course is None:
        raise SlideBuildError("confirmed course is not persisted")
    course = stored_course.payload
    if course.status != "confirmed":
        raise SlideBuildError("course lifecycle is not confirmed")
    stored_outline = catalog.get_course_outline(course.outline_version_id)
    if stored_outline is None:
        raise SlideBuildError("confirmed course outline is dangling")
    outline = stored_outline.payload
    if course.outline_digest != outline.content_digest:
        raise SlideBuildError("confirmed course outline digest is invalid")
    if outline.uncovered_goals:
        raise SlideBuildError("course outline still has uncovered goals")
    placements = tuple(
        placement
        for chapter in outline.chapters
        for placement in chapter.placements
    )
    if tuple(item.placement_id for item in placements) != course.placement_ids:
        raise SlideBuildError("course placement ordering is invalid")
    lineages = tuple(
        _card_lineage(catalog, placement.card_version_id)
        for placement in placements
    )
    subjects = {
        course.version_id,
        course.requirement_id,
        outline.version_id,
        *(placement.placement_id for placement in placements),
        *(lineage.card.version_id for lineage in lineages),
        *(chunk_id for lineage in lineages for chunk_id in lineage.chunk_ids),
        *(
            source_id
            for lineage in lineages
            for source_id in lineage.source_version_ids
        ),
        *(
            reference.dataset_version_id
            for lineage in lineages
            for reference in lineage.card.dataset_refs
        ),
    }
    _reject_blocking_reviews(catalog, subjects)
    _validate_runtime_jobs(job_bindings, lineages)
    nodes = tuple(
        _slide(
            course_version_id=course.version_id,
            placement_id=placement.placement_id,
            placement_purpose=placement.purpose,
            lineage=lineage,
        )
        for placement, lineage in zip(placements, lineages, strict=True)
    )
    created_at: datetime | None = None
    deck_seed = SlideDeckAst(
        logical_id=_stable_id("deck", course.version_id),
        version_id="deck-pending",
        revision=1,
        content_digest="0" * 64,
        created_at=course.created_at,
        created_by=actor,
        course_version_id=course.version_id,
        nodes=nodes,
    )
    deck_digest = slide_deck_content_digest(deck_seed)
    deck_version_id = _stable_id("deckv", course.version_id, deck_digest)
    existing_deck = catalog.get_slide_deck(deck_version_id)
    if existing_deck is None:
        created_at = clock()
        if created_at.utcoffset() is None:
            raise SlideBuildError("draft clock must be timezone-aware")
        deck = deck_seed.model_copy(
            update={
                "version_id": deck_version_id,
                "content_digest": deck_digest,
                "created_at": created_at,
            }
        )
        stored_deck = catalog.register_slide_deck(deck, clock=lambda: created_at)
    else:
        deck = existing_deck.payload
        if (
            deck.course_version_id != course.version_id
            or deck.nodes != nodes
            or deck.content_digest != deck_digest
        ):
            raise SlideBuildError("stored draft deck conflicts with deterministic semantics")
        stored_deck = existing_deck

    evidence_ids = _ordered_unique(
        [
            *(item for lineage in lineages for item in lineage.evidence_ids),
            *(job.evidence_id for job in job_bindings),
        ]
    )
    manifest_seed = RuntimeManifest(
        logical_id=_stable_id("runtime", course.version_id),
        version_id="runtime-pending",
        revision=1,
        content_digest="0" * 64,
        created_at=deck.created_at,
        created_by=actor,
        course_version_id=course.version_id,
        slide_deck_version_id=deck.version_id,
        slide_deck_digest=deck.content_digest,
        job_bindings=job_bindings,
        artifact_ids=(),
        evidence_ids=evidence_ids,
    )
    manifest_digest = runtime_manifest_content_digest(manifest_seed)
    manifest_version_id = _stable_id(
        "runtimev",
        course.version_id,
        deck.version_id,
        manifest_digest,
    )
    existing_manifest = catalog.get_runtime_manifest(manifest_version_id)
    if existing_manifest is None:
        if created_at is None:
            created_at = clock()
            if created_at.utcoffset() is None:
                raise SlideBuildError("draft clock must be timezone-aware")
        manifest = manifest_seed.model_copy(
            update={
                "version_id": manifest_version_id,
                "content_digest": manifest_digest,
                "created_at": created_at,
            }
        )
        stored_manifest = catalog.register_runtime_manifest(
            manifest,
            clock=lambda: created_at,
        )
    else:
        manifest = existing_manifest.payload
        if (
            manifest.course_version_id != course.version_id
            or manifest.slide_deck_version_id != deck.version_id
            or manifest.job_bindings != job_bindings
            or manifest.evidence_ids != evidence_ids
            or manifest.content_digest != manifest_digest
        ):
            raise SlideBuildError(
                "stored runtime manifest conflicts with deterministic semantics"
            )
        stored_manifest = existing_manifest
    return DraftProjection(deck, manifest, stored_deck, stored_manifest)


def build_and_register_draft(
    catalog: KnowledgeCatalog,
    course_version_id: str,
    *,
    actor: ActorRef,
    clock: Clock,
    job_bindings: tuple[RuntimeJobBinding, ...] = (),
) -> DraftProjection:
    """Build and atomically persist one deterministic, content-only course draft."""

    try:
        with catalog.atomic_write():
            return _build_and_register(
                catalog,
                course_version_id,
                actor=actor,
                clock=clock,
                job_bindings=job_bindings,
            )
    except SlideBuildError:
        raise
    except (CatalogMigrationError, CatalogReferenceError, ValidationError) as error:
        raise SlideBuildError(str(error)) from error


def course_publication_request_digest(
    *,
    confirmed_course_version_id: str,
    expected_course_digest: str,
    visual_placement_ids: tuple[str, ...],
    job_bindings: tuple[RuntimeJobBinding, ...] = (),
) -> str:
    """Bind an operation request to the exact immutable publication inputs."""

    return canonical_digest(
        {
            "confirmed_course_version_id": confirmed_course_version_id,
            "expected_course_digest": expected_course_digest,
            "visual_placement_ids": visual_placement_ids,
            "job_bindings": tuple(
                job.model_dump(mode="json", exclude_none=True) for job in job_bindings
            ),
        }
    )


def _publication_validation(
    catalog: KnowledgeCatalog,
    confirmed_course_version_id: str,
    *,
    expected_course_digest: str,
    visual_placement_ids: tuple[str, ...],
    actor: ActorRef,
    now: datetime,
    job_bindings: tuple[RuntimeJobBinding, ...],
) -> CoursePublicationValidation:
    if now.utcoffset() is None:
        raise SlideBuildError("publication clock must be timezone-aware")
    if len(set(visual_placement_ids)) != len(visual_placement_ids):
        raise SlideBuildError("visual placement IDs must be unique")
    stored_course = catalog.get_course_version(confirmed_course_version_id)
    if stored_course is None or stored_course.payload.status not in {"confirmed", "published"}:
        raise SlideBuildError("publication requires an immutable current course snapshot")
    base_course = stored_course.payload
    if base_course.content_digest != expected_course_digest:
        raise SlideBuildError("course digest changed before publication")
    confirmed = base_course
    seen_course_ids: set[str] = set()
    while confirmed.status == "published":
        if (
            confirmed.version_id in seen_course_ids
            or confirmed.supersedes_version_id is None
        ):
            raise SlideBuildError("published course ancestry is invalid")
        seen_course_ids.add(confirmed.version_id)
        parent = catalog.get_course_version(confirmed.supersedes_version_id)
        if parent is None:
            raise SlideBuildError("published course ancestry is dangling")
        confirmed = parent.payload
    if confirmed.status != "confirmed":
        raise SlideBuildError("published course ancestry has no confirmed root")
    draft = build_and_register_draft(
        catalog,
        confirmed.version_id,
        actor=actor,
        clock=lambda: now,
        job_bindings=job_bindings,
    )
    node_map = {node.node_id: node for node in _walk_nodes(draft.deck.nodes)}
    resolved: list[_ResolvedVisual] = []
    for placement_id in visual_placement_ids:
        stored_placement = catalog.get_visual_placement(placement_id)
        if stored_placement is None:
            raise SlideBuildError("visual placement is dangling")
        placement = stored_placement.payload
        node = node_map.get(placement.slide_node_id)
        if node is None:
            raise SlideBuildError("visual placement targets a missing slide node")
        if (
            placement.originating_card_version_id is not None
            and placement.originating_card_version_id not in node.card_version_ids
        ):
            raise SlideBuildError("visual card origin is outside its target slide")
        if (
            placement.originating_source_version_id is not None
            and placement.originating_source_version_id not in node.source_version_ids
        ):
            raise SlideBuildError("visual source origin is outside its target slide")
        resolved.append(
            _resolve_visual(
                catalog,
                placement,
                usage_scope=base_course.usage_scope,
                now=now,
            )
        )

    _reject_blocking_reviews(
        catalog,
        {
            *visual_placement_ids,
            *(item.visual.version_id for item in resolved),
            *(
                item
                for placement_id in visual_placement_ids
                for placement in (catalog.get_visual_placement(placement_id),)
                if placement is not None
                for item in (placement.payload.originating_dataset_version_id,)
                if item is not None
            ),
            *(item.artifact_id for item in resolved),
            *(evidence_id for item in resolved for evidence_id in item.evidence_ids),
        },
    )

    published_seed = base_course.model_copy(
        update={
            "version_id": "course-pending",
            "revision": base_course.revision + 1,
            "content_digest": "0" * 64,
            "supersedes_version_id": base_course.version_id,
            "created_at": now,
            "created_by": actor,
            "visual_placement_ids": visual_placement_ids,
            "status": "published",
        }
    )
    published_digest = course_version_content_digest(published_seed)
    published = published_seed.model_copy(
        update={
            "version_id": _stable_id(
                "coursev", base_course.version_id, published_digest
            ),
            "content_digest": published_digest,
        }
    )
    bound_nodes = _bind_visuals(draft.deck.nodes, tuple(resolved))
    deck_seed = SlideDeckAst(
        logical_id=_stable_id("deck", published.version_id),
        version_id="deck-pending",
        revision=1,
        content_digest="0" * 64,
        created_at=now,
        created_by=actor,
        course_version_id=published.version_id,
        nodes=bound_nodes,
    )
    deck_digest = slide_deck_content_digest(deck_seed)
    deck = deck_seed.model_copy(
        update={
            "version_id": _stable_id(
                "deckv", published.version_id, deck_digest
            ),
            "content_digest": deck_digest,
        }
    )
    flattened = _walk_nodes(deck.nodes)
    artifact_ids = _ordered_unique(
        [binding.artifact_id for node in flattened for binding in node.asset_bindings]
    )
    evidence_ids = _ordered_unique(
        [
            *(evidence_id for node in flattened for evidence_id in node.evidence_ids),
            *(job.evidence_id for job in job_bindings),
        ]
    )
    manifest_seed = RuntimeManifest(
        logical_id=_stable_id("runtime", published.version_id),
        version_id="runtime-pending",
        revision=1,
        content_digest="0" * 64,
        created_at=now,
        created_by=actor,
        course_version_id=published.version_id,
        slide_deck_version_id=deck.version_id,
        slide_deck_digest=deck.content_digest,
        job_bindings=job_bindings,
        artifact_ids=artifact_ids,
        evidence_ids=evidence_ids,
    )
    manifest_digest = runtime_manifest_content_digest(manifest_seed)
    manifest = manifest_seed.model_copy(
        update={
            "version_id": _stable_id(
                "runtimev", published.version_id, deck.version_id, manifest_digest
            ),
            "content_digest": manifest_digest,
        }
    )
    projection_id = _stable_id(
        "course-projection",
        published.version_id,
        deck.version_id,
        manifest.version_id,
        manifest.content_digest,
    )
    return CoursePublicationValidation(
        course=published,
        deck=deck,
        runtime_manifest=manifest,
        course_projection_id=projection_id,
        warnings=_ordered_unique(
            [warning for item in resolved for warning in item.warnings]
        ),
    )


def validate_course_version(
    catalog: KnowledgeCatalog,
    confirmed_course_version_id: str,
    *,
    expected_course_digest: str,
    visual_placement_ids: tuple[str, ...],
    actor: ActorRef,
    clock: Clock,
    job_bindings: tuple[RuntimeJobBinding, ...] = (),
) -> CoursePublicationValidation:
    """Resolve all immutable dependencies and return a non-published snapshot."""

    try:
        return _publication_validation(
            catalog,
            confirmed_course_version_id,
            expected_course_digest=expected_course_digest,
            visual_placement_ids=visual_placement_ids,
            actor=actor,
            now=clock(),
            job_bindings=job_bindings,
        )
    except SlideBuildError:
        raise
    except (CatalogMigrationError, CatalogReferenceError, NetworkVisualError, ValidationError) as error:
        raise SlideBuildError(str(error)) from error


def publish_course_version(
    catalog: KnowledgeCatalog,
    request: OperationRequest,
    *,
    confirmed_course_version_id: str,
    expected_course_digest: str,
    visual_placement_ids: tuple[str, ...],
    clock: Clock,
    job_bindings: tuple[RuntimeJobBinding, ...] = (),
    after_commit: Callable[[OperationOutcome], object] | None = None,
) -> OperationOutcome:
    """Atomically persist the course/deck/manifest snapshot and recoverable outcome."""

    expected_request_digest = course_publication_request_digest(
        confirmed_course_version_id=confirmed_course_version_id,
        expected_course_digest=expected_course_digest,
        visual_placement_ids=visual_placement_ids,
        job_bindings=job_bindings,
    )
    if request.request_digest != expected_request_digest:
        raise SlideBuildError("publication request digest does not match its inputs")
    now = clock()
    if now.utcoffset() is None:
        raise SlideBuildError("publication clock must be timezone-aware")

    def mutation() -> OperationMutationResult:
        validation = _publication_validation(
            catalog,
            confirmed_course_version_id,
            expected_course_digest=expected_course_digest,
            visual_placement_ids=visual_placement_ids,
            actor=request.actor,
            now=now,
            job_bindings=job_bindings,
        )
        catalog.register_course_version(validation.course, clock=lambda: now)
        catalog.register_slide_deck(validation.deck, clock=lambda: now)
        catalog.register_runtime_manifest(
            validation.runtime_manifest, clock=lambda: now
        )
        return OperationMutationResult(
            result_refs={
                "courseVersionId": validation.course.version_id,
                "slideDeckId": validation.deck.version_id,
                "runtimeManifestId": validation.runtime_manifest.version_id,
                "runtimeManifestDigest": validation.runtime_manifest.content_digest,
                "courseProjectionId": validation.course_projection_id,
            },
            item_outcomes=(),
            index_outbox=(),
        )

    try:
        return run_operation(
            catalog,
            request,
            mutation,
            clock=lambda: now,
            after_commit=after_commit,
        )
    except SlideBuildError:
        raise
    except (CatalogMigrationError, CatalogReferenceError, NetworkVisualError, ValidationError) as error:
        raise SlideBuildError(str(error)) from error


__all__ = [
    "CoursePublicationValidation",
    "DraftProjection",
    "SlideBuildError",
    "build_and_register_draft",
    "course_publication_request_digest",
    "publish_course_version",
    "validate_course_version",
]
