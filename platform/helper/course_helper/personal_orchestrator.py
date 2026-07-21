"""Resumable, evidence-bound orchestration for one-click personal courses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol, Sequence, cast

from course_helper.catalog import (
    KnowledgeCatalog,
    OutlineConfirmation,
)
from course_helper.composer import (
    CompositionOptions,
    confirmation_summary,
    prepare_authoritative_composition,
    register_prepared_composition,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.composition import (
    CourseRequirement,
    CourseVersion,
    canonical_digest,
    course_version_content_digest,
)
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.personal_course import (
    AttentionBundle,
    AttentionItem,
    PersonalCourseRequest,
    PersonalCourseResult,
    PersonalCourseRun,
    PersonalCourseStatus,
)
from course_helper.domain.sources import VisualAssetVersion
from course_helper.domain.visual_policy import (
    AttributionBlock,
    TransformationManifest,
    VisualPlacement,
)
from course_helper.index_outbox import (
    claim_next_index_outbox,
    complete_index_claim,
    reopen_index_snapshot,
)
from course_helper.network_visuals import (
    NetworkVisualAcquisition,
    current_network_visual_verification,
)
from course_helper.operations import (
    IndexOutboxItem,
    OperationMutationResult,
    OperationRequest,
    run_operation,
)
from course_helper.personal_knowledge import organize_personal_knowledge
from course_helper.personal_runs import (
    PersonalRunConflict,
    advance_personal_run,
    create_personal_run,
    get_personal_run,
)
from course_helper.retrieval import KnowledgeRetriever
from course_helper.slide_builder import (
    build_and_register_draft,
    course_publication_request_digest,
    publish_course_version,
)
from course_helper.source_visuals import SourceVisualMaterialization


Clock = Callable[[], datetime]
_TERMINAL = frozenset({"ready", "needs_attention", "failed"})
_PHASE_ORDER = {
    "queued": 0,
    "importing": 1,
    "organizing_knowledge": 2,
    "composing": 3,
    "assigning_visuals": 4,
    "validating": 5,
}


class RuntimeConfig(Protocol):
    database_path: str
    app_data_path: str
    source_roots: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class VisualCandidate:
    """Comparable visual choice with evidence already checked by its adapter."""

    version_id: str
    origin_rank: int
    quality_score: int
    provenance_verified: bool
    license_exportable: bool


@dataclass(frozen=True)
class _ResolvedVisualCandidate:
    choice: VisualCandidate
    visual: VisualAssetVersion
    artifact_id: str
    authenticity_evidence_id: str
    license_evidence_id: str
    attribution: AttributionBlock
    originating_source_version_id: str | None = None
    originating_dataset_version_id: str | None = None


@dataclass(frozen=True)
class _PhaseOutcome:
    next_status: PersonalCourseStatus
    evidence_id: str
    attention_bundle: AttentionBundle | None = None
    result: PersonalCourseResult | None = None


def choose_visual(candidates: Sequence[VisualCandidate]) -> VisualCandidate | None:
    """Prefer source, then deterministic data, then verified licensed network media."""

    ordered = sorted(
        candidates,
        key=lambda item: (item.origin_rank, -item.quality_score, item.version_id),
    )
    return next(
        (
            item
            for item in ordered
            if item.provenance_verified and item.license_exportable
        ),
        None,
    )


def create_personal_course_run(
    config: RuntimeConfig,
    request: PersonalCourseRequest,
    actor: ActorRef,
    *,
    clock: Clock | None = None,
) -> PersonalCourseRun:
    """Persist one idempotent run bound to exact registered source bytes."""

    if len(request.source_version_ids) > 50:
        raise ValueError("personal course creation accepts at most 50 sources")
    if actor != request.requested_by:
        raise ValueError("personal course actor does not own the request")
    now = clock or (lambda: datetime.now(timezone.utc))
    with KnowledgeCatalog.open(config.database_path) as catalog:
        source_snapshot_digest = _source_snapshot_digest(
            catalog, tuple(request.source_version_ids)
        )
        return create_personal_run(
            catalog,
            request,
            source_snapshot_digest=source_snapshot_digest,
            clock=now,
        )


def resume_personal_course(
    config: RuntimeConfig,
    run_id: str,
    actor: ActorRef,
    *,
    stop_after_status: PersonalCourseStatus | None = None,
    clock: Clock | None = None,
) -> PersonalCourseRun:
    """Run the bounded state machine until ready, attention, failure, or checkpoint."""

    now = clock or (lambda: datetime.now(timezone.utc))
    for _ in range(16):
        with KnowledgeCatalog.open(config.database_path) as catalog:
            run = get_personal_run(catalog, run_id)
        if run is None:
            raise LookupError("personal course run does not exist")
        if actor != run.request.requested_by and actor.actor_type != "system":
            raise ValueError("personal course actor does not own the run")
        if run.status in _TERMINAL or run.status == stop_after_status:
            return run
        try:
            outcome = _PHASE_HANDLERS[run.status](config, run, actor)
        except Exception as error:
            outcome = _failed_phase(config, run, error)
        with KnowledgeCatalog.open(config.database_path) as catalog:
            try:
                run = advance_personal_run(
                    catalog,
                    run.run_id,
                    expected_revision=run.revision,
                    next_status=outcome.next_status,
                    evidence_id=outcome.evidence_id,
                    attention_bundle=outcome.attention_bundle,
                    result=outcome.result,
                    failure_message=(
                        "课程生成未能安全完成，请检查资料后重试。"
                        if outcome.next_status == "failed"
                        else None
                    ),
                    clock=now,
                )
            except PersonalRunConflict:
                continue
        if run.status in _TERMINAL or run.status == stop_after_status:
            return run
    raise RuntimeError("personal course orchestration exceeded its phase ceiling")


def _source_snapshot_digest(
    catalog: KnowledgeCatalog,
    source_version_ids: tuple[str, ...],
) -> str:
    sources: list[dict[str, str]] = []
    for source_id in source_version_ids:
        source = catalog.get_source(source_id)
        if source is None:
            raise ValueError("personal course source is unavailable")
        sources.append(
            {
                "source_version_id": source.version_id,
                "content_digest": source.content_digest,
            }
        )
    return canonical_digest(tuple(sources))


def _phase_time(run: PersonalCourseRun, status: str | None = None) -> datetime:
    phase = run.status if status is None else status
    return run.created_at + timedelta(seconds=_PHASE_ORDER.get(phase, 9))


def _phase_evidence(
    catalog: KnowledgeCatalog,
    run: PersonalCourseRun,
    *,
    phase: str,
    output: dict[str, object],
    status: str = "verified",
) -> EvidenceObject:
    semantics = {
        "run_id": run.run_id,
        "request_digest": run.request_digest,
        "phase": phase,
        "output": output,
    }
    timestamp = _phase_time(run, phase)
    evidence = EvidenceObject(
        evidence_id="personal-phase-" + canonical_digest(semantics),
        kind="validation" if phase != "composing" else "composition",
        subject_version_id=None,
        status=cast(str, status),
        input_summary={
            "request_digest": run.request_digest,
            "source_snapshot_digest": run.source_snapshot_digest,
            "phase": phase,
        },
        output_summary=output,
        producer="course-helper/personal-orchestrator",
        producer_version="1",
        started_at=timestamp,
        finished_at=timestamp,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="personal-phase-checkpoint",
                status="passed" if status == "verified" else "warning",
                message="Personal course phase committed verifiable outputs",
                details={"phase": phase},
            ),
        ),
    )
    catalog.insert_evidence(evidence)
    return evidence


def _start_phase(
    config: RuntimeConfig, run: PersonalCourseRun, actor: ActorRef
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        evidence = _phase_evidence(
            catalog,
            run,
            phase="queued",
            output={"accepted_source_count": len(run.request.source_version_ids)},
        )
    return _PhaseOutcome("importing", evidence.evidence_id)


def _import_phase(
    config: RuntimeConfig, run: PersonalCourseRun, actor: ActorRef
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        available = tuple(
            source_id
            for source_id in run.request.source_version_ids
            if catalog.get_source(source_id) is not None
        )
        digest_matches = (
            len(available) == len(run.request.source_version_ids)
            and _source_snapshot_digest(catalog, tuple(available))
            == run.source_snapshot_digest
        )
        evidence = _phase_evidence(
            catalog,
            run,
            phase="importing",
            output={
                "available_source_count": len(available),
                "snapshot_matches": digest_matches,
            },
            status="verified" if digest_matches else "warning",
        )
    if digest_matches:
        return _PhaseOutcome("organizing_knowledge", evidence.evidence_id)
    item = AttentionItem(
        attention_id="attention-source-" + canonical_digest({"run": run.run_id})[:32],
        kind="source-read",
        title="检查课程资料",
        message="部分资料已移动、损坏或与创建时的版本不一致。",
        allowed_actions=("retry", "exclude-source"),
        recommended_action="retry",
    )
    return _PhaseOutcome(
        "needs_attention",
        evidence.evidence_id,
        attention_bundle=_attention_bundle(run, (item,)),
    )


def _organize_phase(
    config: RuntimeConfig, run: PersonalCourseRun, actor: ActorRef
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        organized = organize_personal_knowledge(
            catalog,
            tuple(run.request.source_version_ids),
            actor,
        )
        if organized.attention_items:
            evidence = _phase_evidence(
                catalog,
                run,
                phase="organizing_knowledge",
                output={
                    "published_card_version_ids": list(
                        organized.published_card_version_ids
                    ),
                    "attention_count": len(organized.attention_items),
                },
                status="warning",
            )
            return _PhaseOutcome(
                "needs_attention",
                evidence.evidence_id,
                attention_bundle=_attention_bundle(run, organized.attention_items),
            )
        snapshot_id = _index_personal_cards(
            catalog,
            run,
            actor,
            organized.published_card_version_ids,
        )
        evidence = _phase_evidence(
            catalog,
            run,
            phase="organizing_knowledge",
            output={
                "published_card_version_ids": list(
                    organized.published_card_version_ids
                ),
                "index_snapshot_id": snapshot_id,
                "attention_count": 0,
            },
        )
    return _PhaseOutcome("composing", evidence.evidence_id)


def _index_personal_cards(
    catalog: KnowledgeCatalog,
    run: PersonalCourseRun,
    actor: ActorRef,
    card_version_ids: tuple[str, ...],
) -> str:
    if not card_version_ids:
        raise ValueError("no source-bound knowledge card is available for composition")
    outbox = tuple(
        IndexOutboxItem(
            outbox_id="personal-index-"
            + canonical_digest({"run": run.run_id, "card": card_id})[:40],
            card_version_id=card_id,
            action="upsert",
        )
        for card_id in card_version_ids
    )
    request_digest = canonical_digest(
        tuple(item.model_dump(mode="json") for item in outbox)
    )
    outcome = run_operation(
        catalog,
        OperationRequest(
            operation_id="personal-index-enqueue-" + run.run_id,
            request_digest=request_digest,
            actor=actor,
            session_id="personal-session-" + run.run_id,
        ),
        lambda: OperationMutationResult(
            result_refs={"cardVersionIds": list(card_version_ids)},
            item_outcomes=(),
            index_outbox=outbox,
        ),
        clock=lambda: _phase_time(run, "organizing_knowledge"),
    )
    target_ids = set(outcome.index_outbox_ids)
    current = datetime.now(timezone.utc)
    unresolved = catalog.connection.execute(
        """
        SELECT claim.claim_id, claim.worker_id, claim.lease_expires_at
        FROM knowledge_index_outbox_claims claim
        WHERE claim.outbox_id IN (
            SELECT outbox_id FROM knowledge_index_outbox WHERE operation_id = ?
        )
          AND NOT EXISTS (
            SELECT 1 FROM knowledge_index_outbox_results result
            WHERE result.claim_id = claim.claim_id
          )
        ORDER BY claim.outbox_id, claim.attempt
        """,
        ("personal-index-enqueue-" + run.run_id,),
    ).fetchall()
    for claim_id, worker_id, lease_expires_at in unresolved:
        if datetime.fromisoformat(str(lease_expires_at)) > current:
            complete_index_claim(
                catalog,
                claim_id=str(claim_id),
                worker_id=str(worker_id),
                embedding_provider=None,
                now=current,
            )
    for attempt in range(500):
        consumed = {
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT outbox_id FROM knowledge_index_outbox_consumptions"
            ).fetchall()
        }
        if target_ids.issubset(consumed):
            break
        worker_id = "personal-index-worker-" + canonical_digest(
            {"run": run.run_id, "attempt": attempt}
        )[:32]
        claim = claim_next_index_outbox(
            catalog,
            worker_id=worker_id,
            now=current,
            lease_seconds=60,
        )
        if claim is None:
            raise RuntimeError("personal knowledge index work is unavailable")
        complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id=worker_id,
            embedding_provider=None,
            now=current,
        )
    else:
        raise RuntimeError("personal knowledge index exceeded its work ceiling")
    row = catalog.connection.execute(
        "SELECT index_snapshot_id FROM embedding_index_snapshots ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("personal knowledge index did not produce a snapshot")
    return reopen_index_snapshot(catalog, str(row[0])).index_snapshot_id


def _compose_phase(
    config: RuntimeConfig, run: PersonalCourseRun, actor: ActorRef
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        snapshot_row = catalog.connection.execute(
            "SELECT index_snapshot_id FROM embedding_index_snapshots ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if snapshot_row is None:
            raise RuntimeError("personal course composition has no verified index snapshot")
        cards = _published_source_cards(catalog, run)
        if not cards:
            raise RuntimeError("personal course composition has no published source cards")
        requirement = _course_requirement(run, cards)
        stored_requirement = catalog.register_course_requirement(
            requirement, clock=lambda: _phase_time(run, "composing")
        )
        prepared = prepare_authoritative_composition(
            catalog,
            KnowledgeRetriever(catalog),
            requirement,
            logical_id="outline-" + run.run_id,
            version_id="outline-version-" + run.run_id,
            revision=1,
            created_at=_phase_time(run, "composing"),
            created_by=actor,
            options=CompositionOptions(index_snapshot_id=str(snapshot_row[0])),
            requirement_storage_digest=stored_requirement.content_digest,
        )
        if prepared.blocking_gaps:
            evidence = _phase_evidence(
                catalog,
                run,
                phase="composing",
                output={"blocking_gap_count": len(prepared.blocking_gaps)},
                status="warning",
            )
            item = AttentionItem(
                attention_id="attention-course-gap-"
                + canonical_digest({"run": run.run_id})[:32],
                kind="course-validation",
                title="补充课程内容",
                message="当前资料不足以覆盖全部课程目标。",
                allowed_actions=("retry", "approve", "reject"),
                recommended_action="retry",
            )
            return _PhaseOutcome(
                "needs_attention",
                evidence.evidence_id,
                attention_bundle=_attention_bundle(run, (item,)),
            )
        composed = register_prepared_composition(catalog, requirement, prepared)
    return _PhaseOutcome("assigning_visuals", composed.composition_evidence.evidence_id)


def _published_source_cards(
    catalog: KnowledgeCatalog, run: PersonalCourseRun
) -> tuple[object, ...]:
    rows = catalog.connection.execute(
        """
        SELECT cards.payload_json
        FROM cards
        JOIN card_lifecycle_current lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE lifecycle.status = 'published' AND lifecycle.suspended = 0
        ORDER BY cards.logical_id, cards.revision, cards.version_id
        LIMIT 500
        """
    ).fetchall()
    from course_helper.domain.knowledge import KnowledgeCardVersion

    cards = tuple(
        KnowledgeCardVersion.model_validate_json(str(row[0]), strict=False)
        for row in rows
    )
    source_ids = set(run.request.source_version_ids)
    return tuple(
        card
        for card in cards
        if card.chunk_citations
        and {item.source_version_id for item in card.chunk_citations}.issubset(source_ids)
    )


def _course_requirement(run: PersonalCourseRun, cards: tuple[object, ...]) -> CourseRequirement:
    title = run.request.title_hint or _derive_title(run.request.prompt)
    goals = tuple(
        dict.fromkeys(
            cast(str, getattr(card, "learning_objective")) for card in cards
        )
    )[:20]
    duration = _duration_minutes(run.request.prompt, len(cards))
    return CourseRequirement(
        requirement_id="requirement-" + run.run_id,
        title=title,
        audience="个人学习者",
        learning_goals=goals,
        duration_minutes=duration,
        usage_scope="internal",
    )


def _derive_title(prompt: str) -> str:
    value = " ".join(prompt.split())
    value = re.sub(r"^为.{0,40}?(?:制作|创建|生成)", "", value).strip()
    value = re.sub(r"\b\d+\s*分钟\b", "", value).strip()
    value = re.sub(r"(?:课程|课)$", "", value).strip(" ，。,:：")
    if not value:
        value = "AI 实战"
    if not value.startswith("个人"):
        value = "个人 " + value
    return value[:200]


def _duration_minutes(prompt: str, card_count: int) -> int:
    match = re.search(r"(\d{1,3})\s*分钟", prompt)
    requested = int(match.group(1)) if match else max(30, card_count * 10)
    bounded = min(480, max(5, requested))
    return max(5, int(round(bounded / 5.0)) * 5)


def _assign_visuals_phase(
    config: RuntimeConfig, run: PersonalCourseRun, actor: ActorRef
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        requirement = catalog.get_course_requirement("requirement-" + run.run_id)
        outline = catalog.get_course_outline("outline-version-" + run.run_id)
        if requirement is None or outline is None:
            raise RuntimeError("personal course outline is unavailable")
        summary = confirmation_summary(outline.payload, requirement.payload)
        confirmation = catalog.confirm_course_outline(
            OutlineConfirmation(
                confirmation_id="confirmation-" + run.run_id,
                requirement_id=requirement.payload.requirement_id,
                outline_version_id=outline.payload.version_id,
                expected_outline_digest=outline.payload.content_digest,
                confirmation_digest=summary.confirmation_digest,
                confirmed_by=actor,
            ),
            clock=lambda: _phase_time(run, "assigning_visuals"),
        ).payload
        placement_ids = tuple(
            item.placement_id
            for chapter in outline.payload.chapters
            for item in chapter.placements
        )
        seed = CourseVersion(
            logical_id="course-" + run.run_id,
            version_id="course-confirmed-" + run.run_id,
            revision=1,
            content_digest="0" * 64,
            created_at=_phase_time(run, "assigning_visuals"),
            created_by=actor,
            requirement_id=requirement.payload.requirement_id,
            outline_version_id=outline.payload.version_id,
            outline_digest=outline.payload.content_digest,
            placement_ids=placement_ids,
            usage_scope=requirement.payload.usage_scope,
            confirmation_digest=confirmation.confirmation_digest,
            status="confirmed",
        )
        confirmed = seed.model_copy(
            update={"content_digest": course_version_content_digest(seed)}
        )
        confirmed = catalog.register_course_version(
            confirmed, clock=lambda: _phase_time(run, "assigning_visuals")
        ).payload
        draft = build_and_register_draft(
            catalog,
            confirmed.version_id,
            actor=actor,
            clock=lambda: _phase_time(run, "assigning_visuals"),
        )
        visual_placement_ids, missing = _register_automatic_visuals(
            catalog, run, draft.deck, actor
        )
        evidence = _phase_evidence(
            catalog,
            run,
            phase="assigning_visuals",
            output={
                "confirmed_course_version_id": confirmed.version_id,
                "visual_placement_ids": list(visual_placement_ids),
                "unavailable_declared_visual_count": missing,
            },
            status="warning" if missing else "verified",
        )
    if missing:
        item = AttentionItem(
            attention_id="attention-visual-missing-"
            + canonical_digest({"run": run.run_id})[:32],
            kind="visual-license",
            title="确认图形使用方式",
            message="资料声明了图形，但没有可核验的真实素材可安全编排。",
            allowed_actions=("continue-without-visual", "use-source-visual"),
            recommended_action="continue-without-visual",
        )
        return _PhaseOutcome(
            "needs_attention",
            evidence.evidence_id,
            attention_bundle=_attention_bundle(run, (item,)),
        )
    return _PhaseOutcome("validating", evidence.evidence_id)


def _register_automatic_visuals(
    catalog: KnowledgeCatalog,
    run: PersonalCourseRun,
    deck: object,
    actor: ActorRef,
) -> tuple[tuple[str, ...], int]:
    nodes = []
    stack = list(reversed(getattr(deck, "nodes")))
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(reversed(node.children))
    registered: list[str] = []
    missing = 0
    for node in (item for item in nodes if item.node_type == "slide"):
        candidates: list[_ResolvedVisualCandidate] = []
        declared = 0
        for card_id in node.card_version_ids:
            card = catalog.get_card(card_id)
            if card is None:
                continue
            declared += len(card.visual_refs)
            for reference in card.visual_refs:
                candidate = _resolve_visual_candidate(
                    catalog,
                    reference.visual_version_id,
                    now=_phase_time(run, "assigning_visuals"),
                )
                if candidate is not None:
                    candidates.append(candidate)
        selected_choice = choose_visual(tuple(item.choice for item in candidates))
        if selected_choice is None:
            missing += 1 if declared else 0
            continue
        selected = next(item for item in candidates if item.choice == selected_choice)
        card_id = next(
            (
                card_id
                for card_id in node.card_version_ids
                for card in (catalog.get_card(card_id),)
                if card is not None
                and selected.visual.version_id
                in {ref.visual_version_id for ref in card.visual_refs}
            ),
            None,
        )
        if card_id is None:
            missing += 1
            continue
        placement_id = "visual-placement-" + canonical_digest(
            {
                "run": run.run_id,
                "node": node.node_id,
                "visual": selected.visual.version_id,
            }
        )[:40]
        transformation = TransformationManifest(
            transformation_id="transformation-"
            + canonical_digest({"placement": placement_id, "fit": "contain"})[:40],
            scale_mode="contain",
            derivative_license_decision="not-derivative",
            share_alike_compatible=False,
            gfdl_compatible=False,
            no_derivatives_compatible=True,
        )
        placement = VisualPlacement(
            placement_id=placement_id,
            visual_version_id=selected.visual.version_id,
            slide_node_id=node.node_id,
            slot_id="primary-visual",
            fit="contain",
            alt_text=selected.visual.alt_text or "课程来源图形",
            authenticity_evidence_id=selected.authenticity_evidence_id,
            license_evidence_id=selected.license_evidence_id,
            attribution=selected.attribution,
            transformation=transformation,
            originating_card_version_id=card_id,
            originating_source_version_id=selected.originating_source_version_id,
            originating_dataset_version_id=selected.originating_dataset_version_id,
        )
        catalog.register_visual_placement(
            placement, clock=lambda: _phase_time(run, "assigning_visuals")
        )
        registered.append(placement_id)
    return tuple(registered), missing


def _resolve_visual_candidate(
    catalog: KnowledgeCatalog,
    visual_version_id: str,
    *,
    now: datetime,
) -> _ResolvedVisualCandidate | None:
    row = catalog.connection.execute(
        "SELECT payload_json FROM visuals WHERE version_id = ?", (visual_version_id,)
    ).fetchone()
    if row is None:
        return None
    visual = VisualAssetVersion.model_validate_json(str(row[0]), strict=False)
    source_row = catalog.connection.execute(
        "SELECT payload_json FROM source_visual_artifacts WHERE visual_version_id = ?",
        (visual_version_id,),
    ).fetchone()
    if source_row is not None:
        materialized = SourceVisualMaterialization.model_validate_json(
            str(source_row[0]), strict=False
        )
        return _ResolvedVisualCandidate(
            choice=VisualCandidate(
                version_id=visual.version_id,
                origin_rank=0,
                quality_score=visual.width * visual.height,
                provenance_verified=True,
                license_exportable=visual.license_status not in {"unknown", "restricted"},
            ),
            visual=visual,
            artifact_id=materialized.artifact_id,
            authenticity_evidence_id=materialized.evidence_id,
            license_evidence_id=materialized.evidence_id,
            attribution=AttributionBlock(
                title=visual.alt_text or "课程来源图形",
                creator=visual.author,
                publisher=visual.publisher,
                license_label=visual.license_status,
            ),
            originating_source_version_id=materialized.source_version_id,
        )
    network_row = catalog.connection.execute(
        "SELECT payload_json FROM network_visual_acquisitions WHERE visual_version_id = ?",
        (visual_version_id,),
    ).fetchone()
    if network_row is not None:
        acquisition = NetworkVisualAcquisition.model_validate_json(
            str(network_row[0]), strict=False
        )
        verification = current_network_visual_verification(
            catalog, visual_version_id, now=now
        )
        return _ResolvedVisualCandidate(
            choice=VisualCandidate(
                version_id=visual.version_id,
                origin_rank=2,
                quality_score=visual.width * visual.height,
                provenance_verified=verification.status == "verified",
                license_exportable=verification.status == "verified",
            ),
            visual=visual,
            artifact_id=acquisition.artifact_id,
            authenticity_evidence_id=verification.evidence_id,
            license_evidence_id=acquisition.evidence_id,
            attribution=AttributionBlock(
                title=acquisition.title,
                creator=acquisition.creator,
                publisher="Wikimedia Commons",
                license_label=acquisition.license_id,
                landing_link=acquisition.landing_link,
                license_link=acquisition.license_link,
            ),
        )
    artifact_row = catalog.connection.execute(
        "SELECT from_version_id, evidence_id FROM lineage "
        "WHERE to_version_id = ? AND relation = 'derived_from' "
        "AND from_version_id LIKE 'artifact-%' ORDER BY from_version_id LIMIT 1",
        (visual_version_id,),
    ).fetchone()
    dataset_row = catalog.connection.execute(
        "SELECT to_version_id, evidence_id FROM lineage "
        "WHERE from_version_id = ? AND relation = 'derived_from' "
        "AND to_version_id IN (SELECT version_id FROM datasets) "
        "ORDER BY to_version_id LIMIT 1",
        (visual_version_id,),
    ).fetchone()
    if artifact_row is not None and dataset_row is not None:
        return _ResolvedVisualCandidate(
            choice=VisualCandidate(
                version_id=visual.version_id,
                origin_rank=1,
                quality_score=visual.width * visual.height,
                provenance_verified=True,
                license_exportable=True,
            ),
            visual=visual,
            artifact_id=str(artifact_row[0]),
            authenticity_evidence_id=str(dataset_row[1]),
            license_evidence_id=str(dataset_row[1]),
            attribution=AttributionBlock(
                title=visual.alt_text or "数据图形",
                creator=visual.author,
                publisher=visual.publisher,
                license_label=visual.license_status,
            ),
            originating_dataset_version_id=str(dataset_row[0]),
        )
    return None


def _validate_phase(
    config: RuntimeConfig, run: PersonalCourseRun, actor: ActorRef
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        confirmed = catalog.get_course_version("course-confirmed-" + run.run_id)
        outline = catalog.get_course_outline("outline-version-" + run.run_id)
        requirement = catalog.get_course_requirement("requirement-" + run.run_id)
        if confirmed is None or outline is None or requirement is None:
            raise RuntimeError("personal course publication inputs are unavailable")
        phase = _latest_phase_evidence(catalog, run, "assigning_visuals")
        visual_ids = tuple(
            cast(list[str], phase.output_summary.get("visual_placement_ids", []))
        )
        request_digest = course_publication_request_digest(
            confirmed_course_version_id=confirmed.payload.version_id,
            expected_course_digest=confirmed.payload.content_digest,
            visual_placement_ids=visual_ids,
        )
        operation = publish_course_version(
            catalog,
            OperationRequest(
                operation_id="personal-publish-" + run.run_id,
                request_digest=request_digest,
                actor=actor,
                session_id="personal-session-" + run.run_id,
            ),
            confirmed_course_version_id=confirmed.payload.version_id,
            expected_course_digest=confirmed.payload.content_digest,
            visual_placement_ids=visual_ids,
            clock=lambda: _phase_time(run, "validating"),
        )
        evidence = _phase_evidence(
            catalog,
            run,
            phase="validating",
            output={
                "course_version_id": operation.result_refs["courseVersionId"],
                "slide_deck_version_id": operation.result_refs["slideDeckId"],
                "runtime_manifest_version_id": operation.result_refs[
                    "runtimeManifestId"
                ],
            },
        )
        result = PersonalCourseResult(
            title=requirement.payload.title,
            course_version_id=cast(str, operation.result_refs["courseVersionId"]),
            slide_deck_version_id=cast(str, operation.result_refs["slideDeckId"]),
            runtime_manifest_version_id=cast(
                str, operation.result_refs["runtimeManifestId"]
            ),
            chapter_count=len(outline.payload.chapters),
        )
    return _PhaseOutcome("ready", evidence.evidence_id, result=result)


def _latest_phase_evidence(
    catalog: KnowledgeCatalog, run: PersonalCourseRun, phase: str
) -> EvidenceObject:
    for evidence_id in reversed(run.phase_evidence_ids):
        row = catalog.connection.execute(
            "SELECT payload_json FROM evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            continue
        evidence = EvidenceObject.model_validate_json(str(row[0]), strict=False)
        if evidence.input_summary.get("phase") == phase:
            return evidence
    raise RuntimeError("personal course phase evidence is unavailable")


def _attention_bundle(
    run: PersonalCourseRun, items: Sequence[AttentionItem]
) -> AttentionBundle:
    unique = tuple({item.attention_id: item for item in items}.values())
    return AttentionBundle(
        bundle_id="attention-bundle-"
        + canonical_digest(
            {"run": run.run_id, "items": [item.attention_id for item in unique]}
        )[:40],
        created_at=_phase_time(run),
        items=unique,
    )


def _failed_phase(
    config: RuntimeConfig, run: PersonalCourseRun, error: Exception
) -> _PhaseOutcome:
    with KnowledgeCatalog.open(config.database_path) as catalog:
        evidence = _phase_evidence(
            catalog,
            run,
            phase=run.status,
            output={"error_type": type(error).__name__, "safe_message": "phase failed"},
            status="failed",
        )
    return _PhaseOutcome("failed", evidence.evidence_id)


_PHASE_HANDLERS = {
    "queued": _start_phase,
    "importing": _import_phase,
    "organizing_knowledge": _organize_phase,
    "composing": _compose_phase,
    "assigning_visuals": _assign_visuals_phase,
    "validating": _validate_phase,
}


__all__ = [
    "RuntimeConfig",
    "VisualCandidate",
    "choose_visual",
    "create_personal_course_run",
    "resume_personal_course",
]
