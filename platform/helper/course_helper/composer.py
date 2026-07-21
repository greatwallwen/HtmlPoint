"""Deterministic preview and authoritative grounded course composition."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.composition import (
    CardPlacement,
    CourseOutline,
    CourseOutlineChapter,
    CourseRequirement,
    UsageScope,
    canonical_digest,
    course_outline_content_digest,
    course_outline_semantic_payload,
)
from course_helper.domain.knowledge import KnowledgeCardVersion
from course_helper.retrieval import (
    KnowledgeRetriever,
    RetrievalQuery,
    RetrievalResult,
    retrieval_query_contract,
    retrieval_query_digest,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMPOSER_BINDING_KIND = "course-outline-binding-v2"
COMPOSER_PRODUCER = "course-helper/composer"
COMPOSER_PRODUCER_VERSION = "2"
RETRIEVAL_PRODUCER = "course-helper/retrieval"
RETRIEVAL_PRODUCER_VERSION = "4"
ALLOCATION_POLICY = "five-minute-minimum-then-suggested-then-round-robin-v1"


class CompositionError(ValueError):
    """A retrieval-pinned outline cannot safely be composed."""


class CompositionGapError(CompositionError):
    """A requested override is not grounded in controlled retrieval."""


class _CardCatalog(Protocol):
    def get_card(self, version_id: str) -> KnowledgeCardVersion | None: ...


class _Retriever(Protocol):
    def search(self, query: RetrievalQuery) -> RetrievalResult: ...


class CompositionOptions(BaseModel):
    """Explicit, bounded adjustments to a requirement's retrieval candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audience_tag_id: str | None = None
    difficulty_tag_id: str | None = None
    index_snapshot_id: str | None = None
    include_card_version_ids: tuple[str, ...] = Field(default=(), max_length=100)
    exclude_card_version_ids: tuple[str, ...] = Field(default=(), max_length=100)
    require_visual_refs: bool = False
    require_dataset_refs: bool = False

    @field_validator("include_card_version_ids", "exclude_card_version_ids")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            _SAFE_ID.fullmatch(item) is None for item in value
        ):
            raise ValueError("card version IDs must be unique safe IDs")
        return value

    @field_validator("audience_tag_id", "difficulty_tag_id")
    @classmethod
    def tag_dimension(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return value
        prefix = (
            "audience:"
            if getattr(info, "field_name", "") == "audience_tag_id"
            else "difficulty:"
        )
        if _SAFE_ID.fullmatch(value) is None or not value.startswith(prefix):
            raise ValueError(
                f"{getattr(info, 'field_name', 'tag')} has the wrong tag dimension"
            )
        return value

    @field_validator("index_snapshot_id")
    @classmethod
    def snapshot_id(cls, value: str | None) -> str | None:
        if value is not None and _SAFE_ID.fullmatch(value) is None:
            raise ValueError("index_snapshot_id must be a safe ID")
        return value

    @model_validator(mode="after")
    def overrides_are_disjoint(self) -> CompositionOptions:
        if set(self.include_card_version_ids) & set(self.exclude_card_version_ids):
            raise ValueError("included and excluded card IDs must not overlap")
        return self


class BoundCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    card_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class BoundRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    goal: str = Field(min_length=1, max_length=500)
    query_contract: dict[str, object]
    query_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_vocabulary_version_id: str = Field(min_length=1)
    evidence_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    evidence_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    returned_cards: tuple[BoundCard, ...]


class BoundGoalSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    goal: str = Field(min_length=1, max_length=500)
    selected_card_version_ids: tuple[str, ...]


class BoundPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    placement: CardPlacement
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComposerBindingPayload(BaseModel):
    """Strict semantic payload carried by the composer-owned evidence receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    receipt_kind: Literal["course-outline-binding-v2"] = COMPOSER_BINDING_KIND
    allocation_policy: Literal[
        "five-minute-minimum-then-suggested-then-round-robin-v1"
    ] = ALLOCATION_POLICY
    requirement_id: str
    requirement_storage_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    requirement_domain_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirement_duration_minutes: int
    learning_goals: tuple[str, ...]
    outline_version_id: str
    outline_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_snapshot_id: str
    index_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    filters: dict[str, object]
    options: CompositionOptions
    retrievals: tuple[BoundRetrieval, ...]
    returned_cards: tuple[BoundCard, ...]
    selected_cards: tuple[BoundCard, ...]
    goal_selections: tuple[BoundGoalSelection, ...]
    placements: tuple[BoundPlacement, ...]
    covered_goals: tuple[str, ...]
    uncovered_goals: tuple[str, ...]
    total_allocated_minutes: int


@dataclass(frozen=True)
class CompositionResult:
    outline: CourseOutline
    blocking_gaps: tuple[str, ...]
    composition_evidence: EvidenceObject
    retrieval_evidence_ids: tuple[str, ...]
    retrieval_evidence: tuple[EvidenceObject, ...] = ()


class ConfirmationSummary(BaseModel):
    """Audience-safe, digest-bound confirmation payload with no card facts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    usage_scope: UsageScope
    outline_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    outline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirement_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(min_length=1, max_length=1000)


def compose_outline(
    catalog: _CardCatalog,
    requirement: CourseRequirement,
    retrieval: RetrievalResult,
    *,
    logical_id: str,
    version_id: str,
    revision: int,
    created_at: datetime,
    created_by: ActorRef,
    options: CompositionOptions = CompositionOptions(),
) -> CompositionResult:
    """Create a non-authoritative preview from caller-supplied retrieval data.

    Preview results are deliberately not registrable: their composer binding
    lacks a persisted requirement digest.  The only authoritative write seam is
    :func:`compose_and_register`, which invokes a retriever for every goal.
    """

    if not isinstance(retrieval, RetrievalResult):
        raise CompositionError("preview composition requires a RetrievalResult")
    snapshot_id, _snapshot_digest = _snapshot_binding(retrieval)
    if options.index_snapshot_id is not None and options.index_snapshot_id != snapshot_id:
        raise CompositionError("composition options do not match the retrieval snapshot")
    query = RetrievalQuery(
        text=requirement.learning_goals[0],
        required_tag_ids=requirement.required_tag_ids,
        excluded_tag_ids=requirement.excluded_tag_ids,
        audience_tag_id=options.audience_tag_id,
        difficulty_tag_id=options.difficulty_tag_id,
        index_snapshot_id=snapshot_id,
    )
    return _compose_from_retrievals(
        catalog,
        requirement,
        ((requirement.learning_goals[0], query, retrieval),),
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        created_at=created_at,
        created_by=created_by,
        options=options,
        requirement_storage_digest=None,
        authoritative=False,
    )


def retrieve_and_compose(
    catalog: _CardCatalog,
    retriever: _Retriever,
    requirement: CourseRequirement,
    *,
    logical_id: str,
    version_id: str,
    revision: int,
    created_at: datetime,
    created_by: ActorRef,
    options: CompositionOptions = CompositionOptions(),
) -> CompositionResult:
    """Create a controlled, non-persisted preview under one explicit snapshot."""

    return _retrieve_and_compose(
        catalog,
        retriever,
        requirement,
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        created_at=created_at,
        created_by=created_by,
        options=options,
        requirement_storage_digest=None,
        authoritative=True,
    )


def compose_and_register(
    catalog: object,
    retriever: _Retriever,
    requirement: CourseRequirement,
    *,
    logical_id: str,
    version_id: str,
    revision: int,
    created_at: datetime,
    created_by: ActorRef,
    options: CompositionOptions = CompositionOptions(),
) -> CompositionResult:
    """Authoritatively retrieve, bind, and atomically persist one exact outline."""

    if isinstance(retriever, RetrievalResult) or not callable(getattr(retriever, "search", None)):
        raise CompositionError("authoritative composition requires a controlled retriever")
    required_methods = (
        "atomic_write",
        "get_course_requirement",
        "get_course_outline",
        "insert_evidence",
        "register_course_outline",
    )
    if any(not hasattr(catalog, name) for name in required_methods):
        raise CompositionError("composition registration requires a KnowledgeCatalog")
    if options.index_snapshot_id is None:
        raise CompositionError("authoritative composition requires an explicit snapshot")
    stored_requirement = catalog.get_course_requirement(requirement.requirement_id)
    if stored_requirement is None or stored_requirement.payload != requirement:
        raise CompositionError("requirement is not persisted with its exact immutable bytes")

    composed = prepare_authoritative_composition(
        catalog,
        retriever,
        requirement,
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        created_at=created_at,
        created_by=created_by,
        options=options,
        requirement_storage_digest=stored_requirement.content_digest,
    )
    return register_prepared_composition(catalog, requirement, composed)


def prepare_authoritative_composition(
    catalog: _CardCatalog,
    retriever: _Retriever,
    requirement: CourseRequirement,
    *,
    logical_id: str,
    version_id: str,
    revision: int,
    created_at: datetime,
    created_by: ActorRef,
    options: CompositionOptions = CompositionOptions(),
    requirement_storage_digest: str | None = None,
) -> CompositionResult:
    """Retrieve outside a write transaction and bind the predicted immutable requirement bytes."""

    if isinstance(retriever, RetrievalResult) or not callable(
        getattr(retriever, "search", None)
    ):
        raise CompositionError("authoritative composition requires a controlled retriever")
    from course_helper.catalog import canonical_model_json

    if requirement_storage_digest is None:
        requirement_storage_digest = hashlib.sha256(
            canonical_model_json(requirement).encode("utf-8")
        ).hexdigest()
    elif _SHA256.fullmatch(requirement_storage_digest) is None:
        raise CompositionError("requirement storage digest is invalid")
    return _retrieve_and_compose(
        catalog,
        retriever,
        requirement,
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        created_at=created_at,
        created_by=created_by,
        options=options,
        requirement_storage_digest=requirement_storage_digest,
        authoritative=True,
    )


def register_prepared_composition(
    catalog: object,
    requirement: CourseRequirement,
    composed: CompositionResult,
) -> CompositionResult:
    """Revalidate and atomically register one prepared authoritative composition."""

    required_methods = (
        "atomic_write",
        "get_course_requirement",
        "get_course_outline",
        "insert_evidence",
        "register_course_outline",
    )
    if any(not hasattr(catalog, name) for name in required_methods):
        raise CompositionError("composition registration requires a KnowledgeCatalog")
    if not isinstance(composed, CompositionResult):
        raise CompositionError("prepared composition is not canonical")
    stored_requirement = catalog.get_course_requirement(requirement.requirement_id)
    if stored_requirement is None or stored_requirement.payload != requirement:
        raise CompositionError("requirement is not persisted with its exact immutable bytes")
    if composed.blocking_gaps:
        raise CompositionGapError(
            "outline has blocking composition gaps and cannot be registered"
        )

    existing = catalog.get_course_outline(composed.outline.version_id)
    if existing is not None:
        if (
            existing.payload.logical_id != composed.outline.logical_id
            or existing.payload.revision != composed.outline.revision
            or existing.payload.content_digest != composed.outline.content_digest
            or course_outline_semantic_payload(existing.payload)
            != course_outline_semantic_payload(composed.outline)
        ):
            raise CompositionError("outline identity already binds different semantics")
        outline = existing.payload
        evidence = composed.composition_evidence.model_copy(
            update={"started_at": outline.created_at, "finished_at": outline.created_at}
        )
        composed = CompositionResult(
            outline,
            composed.blocking_gaps,
            evidence,
            composed.retrieval_evidence_ids,
            composed.retrieval_evidence,
        )

    with catalog.atomic_write():
        for evidence in composed.retrieval_evidence:
            catalog.insert_evidence(evidence)
        catalog.insert_evidence(composed.composition_evidence)
        stored_outline = catalog.register_course_outline(
            composed.outline, clock=lambda: composed.outline.created_at
        )
    return CompositionResult(
        stored_outline.payload,
        composed.blocking_gaps,
        composed.composition_evidence,
        composed.retrieval_evidence_ids,
        composed.retrieval_evidence,
    )


def confirmation_summary(
    outline: CourseOutline, requirement: CourseRequirement
) -> ConfirmationSummary:
    """Return a minimal response envelope bound to recomputed outline semantics."""

    if outline.requirement_id != requirement.requirement_id:
        raise CompositionError("summary requirement does not own the outline")
    recomputed = course_outline_content_digest(outline)
    if recomputed != outline.content_digest:
        raise CompositionError("outline content digest does not match its semantic payload")
    text = {
        "private-training": "Confirm this private training outline and its stated gaps.",
        "internal": "Confirm this internal outline and its stated gaps.",
        "public": "Confirm this public outline and its stated gaps.",
    }[requirement.usage_scope]
    core = {
        "usage_scope": requirement.usage_scope,
        "outline_version_id": outline.version_id,
        "outline_digest": recomputed,
        "requirement_id": requirement.requirement_id,
        "text": text,
    }
    return ConfirmationSummary(**core, confirmation_digest=canonical_digest(core))


def verify_confirmation_summary(
    summary: ConfirmationSummary,
    outline: CourseOutline,
    requirement: CourseRequirement,
) -> bool:
    """Fail closed when scope, ownership, semantic digest, or summary bytes changed."""

    try:
        expected = confirmation_summary(outline, requirement)
    except CompositionError:
        return False
    return summary == expected


def _retrieve_and_compose(
    catalog: _CardCatalog,
    retriever: _Retriever,
    requirement: CourseRequirement,
    *,
    logical_id: str,
    version_id: str,
    revision: int,
    created_at: datetime,
    created_by: ActorRef,
    options: CompositionOptions,
    requirement_storage_digest: str | None,
    authoritative: bool,
) -> CompositionResult:
    if options.index_snapshot_id is None:
        raise CompositionError("composition requires an explicit retrieval snapshot")
    queries = tuple(
        RetrievalQuery(
            text=goal,
            required_tag_ids=requirement.required_tag_ids,
            excluded_tag_ids=requirement.excluded_tag_ids,
            audience_tag_id=options.audience_tag_id,
            difficulty_tag_id=options.difficulty_tag_id,
            index_snapshot_id=options.index_snapshot_id,
        )
        for goal in requirement.learning_goals
    )
    triples: list[tuple[str, RetrievalQuery, RetrievalResult]] = []
    for goal, query in zip(requirement.learning_goals, queries, strict=True):
        result = retriever.search(query)
        if not isinstance(result, RetrievalResult):
            raise CompositionError("controlled retriever returned a non-canonical result")
        normalized = _reuse_first_retrieval_bytes(catalog, result)
        _validate_retrieval_binding(
            normalized,
            query,
            expected_snapshot_id=options.index_snapshot_id,
            authoritative=authoritative,
        )
        triples.append((goal, query, normalized))
    return _compose_from_retrievals(
        catalog,
        requirement,
        tuple(triples),
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        created_at=created_at,
        created_by=created_by,
        options=options,
        requirement_storage_digest=requirement_storage_digest,
        authoritative=authoritative,
    )


def _compose_from_retrievals(
    catalog: _CardCatalog,
    requirement: CourseRequirement,
    goal_retrievals: tuple[tuple[str, RetrievalQuery, RetrievalResult], ...],
    *,
    logical_id: str,
    version_id: str,
    revision: int,
    created_at: datetime,
    created_by: ActorRef,
    options: CompositionOptions,
    requirement_storage_digest: str | None,
    authoritative: bool,
) -> CompositionResult:
    if not isinstance(requirement, CourseRequirement) or not isinstance(options, CompositionOptions):
        raise CompositionError("composition requires canonical requirement and option contracts")
    if _SAFE_ID.fullmatch(logical_id) is None or _SAFE_ID.fullmatch(version_id) is None or revision < 1:
        raise CompositionError("outline identity is invalid")
    if created_at.utcoffset() is None:
        raise CompositionError("outline created_at must be timezone-aware")
    if not goal_retrievals:
        raise CompositionError("composition requires retrieval evidence")

    snapshots = tuple(_snapshot_binding(item[2]) for item in goal_retrievals)
    snapshot_id, snapshot_digest = snapshots[0]
    if any(value != snapshots[0] for value in snapshots):
        raise CompositionError("goal retrieval results do not share one snapshot")
    if options.index_snapshot_id is not None and options.index_snapshot_id != snapshot_id:
        raise CompositionError("composition options do not match the retrieval snapshot")

    hits_by_id: dict[str, object] = {}
    returned_ids: list[str] = []
    for _goal, _query, result in goal_retrievals:
        result_ids = tuple(hit.card.version_id for hit in result.hits)
        if len(result_ids) != len(set(result_ids)):
            raise CompositionError("retrieval result contains duplicate card placements")
        for hit in result.hits:
            previous = hits_by_id.get(hit.card.version_id)
            if previous is not None and previous.card.content_digest != hit.card.content_digest:  # type: ignore[attr-defined]
                raise CompositionError("goal retrieval returned conflicting card bytes")
            if previous is None:
                hits_by_id[hit.card.version_id] = hit
                returned_ids.append(hit.card.version_id)
    live_cards: dict[str, KnowledgeCardVersion] = {}
    for card_id in returned_ids:
        card = hits_by_id[card_id].card  # type: ignore[attr-defined]
        stored = catalog.get_card(card.version_id)
        if stored is None or stored.content_digest != card.content_digest:
            raise CompositionError(f"retrieval result is stale for card {card.version_id!r}")
        if not _eligible(catalog, stored):
            raise CompositionError(
                f"retrieval card lifecycle is not eligible: {card.version_id!r}"
            )
        live_cards[card.version_id] = stored

    unknown_includes = set(options.include_card_version_ids) - set(returned_ids)
    if unknown_includes:
        raise CompositionGapError("included card was not returned by retrieval")
    available_ids = tuple(
        card_id
        for card_id in returned_ids
        if card_id not in options.exclude_card_version_ids
    )
    available = {card_id: live_cards[card_id] for card_id in available_ids}
    selected: list[str] = []
    capacity = requirement.duration_minutes // 5

    def select_with_prerequisites(card_id: str, visiting: tuple[str, ...] = ()) -> bool:
        if card_id in selected:
            return True
        if card_id in visiting:
            raise CompositionGapError("card prerequisite graph contains a cycle")
        card = available.get(card_id)
        if card is None:
            return False
        for prerequisite in card.prerequisite_card_version_ids:
            if prerequisite not in available or not select_with_prerequisites(
                prerequisite, (*visiting, card_id)
            ):
                return False
        if len(selected) >= capacity:
            return False
        selected.append(card_id)
        return True

    uncovered: list[str] = []
    for goal, _query, result in goal_retrievals:
        match = next(
            (
                hit.card.version_id
                for hit in result.hits
                if hit.card.version_id in available
                and _covers_goal(available[hit.card.version_id], goal)
            ),
            None,
        )
        if match is None or not select_with_prerequisites(match):
            uncovered.append(goal)
    # Caller-supplied preview may contain one retrieval for multiple goals.
    if not authoritative:
        for goal in requirement.learning_goals:
            if goal in uncovered or any(_covers_goal(available[item], goal) for item in selected):
                continue
            match = next(
                (item for item in available_ids if _covers_goal(available[item], goal)),
                None,
            )
            if match is None or not select_with_prerequisites(match):
                uncovered.append(goal)
    for card_id in options.include_card_version_ids:
        if not select_with_prerequisites(card_id):
            raise CompositionGapError(
                "included card has an excluded, absent, or over-capacity prerequisite"
            )

    selected_cards = tuple(available[item] for item in selected)
    if not selected_cards:
        raise CompositionGapError("no eligible retrieval card can satisfy the requested outline")
    coverage_gaps = _coverage_gaps(requirement, selected_cards, options)
    allocations = _allocate(selected_cards, requirement.duration_minutes)

    chapters: list[CourseOutlineChapter] = []
    assigned: set[str] = set()
    for index, goal in enumerate(requirement.learning_goals, 1):
        chapter_id = f"chapter-{index:02d}"
        placements: list[CardPlacement] = []
        for card in selected_cards:
            if card.version_id not in assigned and _covers_goal(card, goal):
                placements.append(
                    _placement(
                        card.version_id,
                        chapter_id,
                        len(assigned) + 1,
                        allocations[card.version_id],
                        version_id,
                    )
                )
                assigned.add(card.version_id)
        chapters.append(
            CourseOutlineChapter(
                chapter_id=chapter_id,
                title=goal,
                objective=goal,
                placements=tuple(placements),
            )
        )
    remainder = tuple(card for card in selected_cards if card.version_id not in assigned)
    if remainder:
        chapter_id = f"chapter-{len(chapters) + 1:02d}"
        placements = tuple(
            _placement(
                card.version_id,
                chapter_id,
                len(assigned) + index + 1,
                allocations[card.version_id],
                version_id,
            )
            for index, card in enumerate(remainder)
        )
        chapters.append(
            CourseOutlineChapter(
                chapter_id=chapter_id,
                title="Supporting governed material",
                objective="Use the selected governed material to support the requested goals",
                placements=placements,
            )
        )

    provisional = CourseOutline(
        logical_id=logical_id,
        version_id=version_id,
        revision=revision,
        content_digest="0" * 64,
        created_at=created_at,
        created_by=created_by,
        requirement_id=requirement.requirement_id,
        chapters=tuple(chapters),
        uncovered_goals=tuple(dict.fromkeys(uncovered)),
        retrieval_evidence_id="composition-binding-pending",
        index_snapshot_id=snapshot_id,
    )
    binding_seed = _binding_seed(
        requirement=requirement,
        requirement_storage_digest=requirement_storage_digest,
        goal_retrievals=goal_retrievals,
        snapshot_id=snapshot_id,
        snapshot_digest=snapshot_digest,
        provisional=provisional,
        selected_cards=selected_cards,
        options=options,
    )
    binding_identity_digest = canonical_digest(binding_seed)
    evidence_id = f"composition-binding-{binding_identity_digest[:44]}"
    outline = provisional.model_copy(update={"retrieval_evidence_id": evidence_id})
    outline = outline.model_copy(update={"content_digest": course_outline_content_digest(outline)})
    blocking_gaps = tuple((*outline.uncovered_goals, *coverage_gaps))
    evidence = _composition_evidence(
        evidence_id=evidence_id,
        requirement=requirement,
        requirement_storage_digest=requirement_storage_digest,
        goal_retrievals=goal_retrievals,
        outline=outline,
        selected_cards=selected_cards,
        options=options,
        snapshot_digest=snapshot_digest,
        blocking_gaps=blocking_gaps,
        binding_identity_digest=binding_identity_digest,
    )
    retrieval_evidence = tuple(item[2].evidence for item in goal_retrievals)
    return CompositionResult(
        outline,
        blocking_gaps,
        evidence,
        tuple(item.evidence_id for item in retrieval_evidence),
        retrieval_evidence,
    )


def _binding_seed(**values: object) -> dict[str, object]:
    requirement = values["requirement"]
    provisional = values["provisional"]
    goal_retrievals = values["goal_retrievals"]
    selected_cards = values["selected_cards"]
    options = values["options"]
    assert isinstance(requirement, CourseRequirement)
    assert isinstance(provisional, CourseOutline)
    assert isinstance(options, CompositionOptions)
    return {
        "receipt_kind": COMPOSER_BINDING_KIND,
        "requirement_id": requirement.requirement_id,
        "requirement_storage_digest": values["requirement_storage_digest"],
        "requirement_domain_digest": canonical_digest(requirement),
        "outline_version_id": provisional.version_id,
        "outline_semantics_without_binding": course_outline_semantic_payload(provisional),
        "snapshot_id": values["snapshot_id"],
        "snapshot_digest": values["snapshot_digest"],
        "query_digests": tuple(item[2].query_digest for item in goal_retrievals),  # type: ignore[union-attr]
        "retrieval_evidence_digests": tuple(
            canonical_digest(item[2].evidence) for item in goal_retrievals  # type: ignore[union-attr]
        ),
        "selected_cards": tuple(
            (card.version_id, card.content_digest) for card in selected_cards  # type: ignore[union-attr]
        ),
        "options": options.model_dump(mode="json"),
    }


def _composition_evidence(
    *,
    evidence_id: str,
    requirement: CourseRequirement,
    requirement_storage_digest: str | None,
    goal_retrievals: tuple[tuple[str, RetrievalQuery, RetrievalResult], ...],
    outline: CourseOutline,
    selected_cards: tuple[KnowledgeCardVersion, ...],
    options: CompositionOptions,
    snapshot_digest: str,
    blocking_gaps: tuple[str, ...],
    binding_identity_digest: str,
) -> EvidenceObject:
    placements = tuple(
        placement for chapter in outline.chapters for placement in chapter.placements
    )
    returned: dict[str, BoundCard] = {}
    retrievals: list[BoundRetrieval] = []
    for goal, query, result in goal_retrievals:
        cards = tuple(
            BoundCard(
                card_version_id=hit.card.version_id,
                content_digest=hit.card.content_digest,
            )
            for hit in result.hits
        )
        for card in cards:
            previous = returned.get(card.card_version_id)
            if previous is not None and previous != card:
                raise CompositionError("retrieval receipts bind conflicting card digests")
            returned.setdefault(card.card_version_id, card)
        retrievals.append(
            BoundRetrieval(
                goal=goal,
                query_contract=retrieval_query_contract(query),
                query_digest=result.query_digest,
                resolved_vocabulary_version_id=result.resolved_vocabulary_version_id,
                evidence_id=result.evidence.evidence_id,
                evidence_content_digest=canonical_digest(result.evidence),
                returned_cards=cards,
            )
        )
    covered_goals = tuple(
        goal
        for goal in requirement.learning_goals
        if goal not in outline.uncovered_goals
    )
    binding = ComposerBindingPayload(
        requirement_id=requirement.requirement_id,
        requirement_storage_digest=requirement_storage_digest,
        requirement_domain_digest=canonical_digest(requirement),
        requirement_duration_minutes=requirement.duration_minutes,
        learning_goals=requirement.learning_goals,
        outline_version_id=outline.version_id,
        outline_content_digest=outline.content_digest,
        index_snapshot_id=outline.index_snapshot_id,
        index_snapshot_digest=snapshot_digest,
        filters={
            "required_tag_ids": requirement.required_tag_ids,
            "excluded_tag_ids": requirement.excluded_tag_ids,
            "audience_tag_id": options.audience_tag_id,
            "difficulty_tag_id": options.difficulty_tag_id,
        },
        options=options,
        retrievals=tuple(retrievals),
        returned_cards=tuple(returned.values()),
        selected_cards=tuple(
            BoundCard(card_version_id=card.version_id, content_digest=card.content_digest)
            for card in selected_cards
        ),
        goal_selections=tuple(
            BoundGoalSelection(
                goal=goal,
                selected_card_version_ids=tuple(
                    card.version_id for card in selected_cards if _covers_goal(card, goal)
                ),
            )
            for goal in requirement.learning_goals
        ),
        placements=tuple(
            BoundPlacement(placement=item, content_digest=canonical_digest(item))
            for item in placements
        ),
        covered_goals=covered_goals,
        uncovered_goals=outline.uncovered_goals,
        total_allocated_minutes=sum(item.allocated_minutes for item in placements),
    )
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="composition",
        status="warning" if blocking_gaps or requirement_storage_digest is None else "verified",
        producer=COMPOSER_PRODUCER,
        producer_version=COMPOSER_PRODUCER_VERSION,
        started_at=outline.created_at,
        finished_at=outline.created_at,
        duration_ms=0,
        input_summary={
            "binding_digest": canonical_digest(binding),
            "binding_identity_digest": binding_identity_digest,
            "requirement_id": requirement.requirement_id,
        },
        output_summary=binding.model_dump(mode="json"),
        checks=(
            EvidenceCheck(
                code="composition-binding",
                status=(
                    "passed"
                    if not blocking_gaps and requirement_storage_digest is not None
                    else "warning"
                ),
                message="Requirement, queries, snapshot, hits, selections, and placements were bound",
            ),
        ),
    )


def composer_binding_identity_digest(
    binding: ComposerBindingPayload,
    outline: CourseOutline,
) -> str:
    """Recompute the canonical receipt identity without a digest cycle."""

    provisional = outline.model_copy(
        update={
            "content_digest": "0" * 64,
            "retrieval_evidence_id": "composition-binding-pending",
        }
    )
    return canonical_digest(
        {
            "receipt_kind": COMPOSER_BINDING_KIND,
            "requirement_id": binding.requirement_id,
            "requirement_storage_digest": binding.requirement_storage_digest,
            "requirement_domain_digest": binding.requirement_domain_digest,
            "outline_version_id": provisional.version_id,
            "outline_semantics_without_binding": course_outline_semantic_payload(
                provisional
            ),
            "snapshot_id": binding.index_snapshot_id,
            "snapshot_digest": binding.index_snapshot_digest,
            "query_digests": tuple(item.query_digest for item in binding.retrievals),
            "retrieval_evidence_digests": tuple(
                item.evidence_content_digest for item in binding.retrievals
            ),
            "selected_cards": tuple(
                (card.card_version_id, card.content_digest)
                for card in binding.selected_cards
            ),
            "options": binding.options.model_dump(mode="json"),
        }
    )


def _validate_retrieval_binding(
    result: RetrievalResult,
    query: RetrievalQuery,
    *,
    expected_snapshot_id: str,
    authoritative: bool,
) -> None:
    snapshot_id, snapshot_digest = _snapshot_binding(result)
    if snapshot_id != expected_snapshot_id or not _SHA256.fullmatch(snapshot_digest):
        raise CompositionError("retrieval result does not bind the requested snapshot")
    expected_digest = retrieval_query_digest(
        query,
        resolved_vocabulary_version_id=result.resolved_vocabulary_version_id,
    )
    if (
        result.query_digest != expected_digest
        or result.evidence.input_summary.get("query_digest") != expected_digest
    ):
        raise CompositionError("retrieval query contract does not match its evidence")
    returned_ids = tuple(hit.card.version_id for hit in result.hits)
    expected_order_digest = hashlib.sha256(
        _canonical_sequence(returned_ids).encode("utf-8")
    ).hexdigest()
    if result.evidence.output_summary.get("returned_hit_order_digest") != expected_order_digest:
        raise CompositionError("retrieval returned-hit order is not evidence-bound")
    if authoritative and (
        result.evidence.producer != RETRIEVAL_PRODUCER
        or result.evidence.producer_version != RETRIEVAL_PRODUCER_VERSION
    ):
        raise CompositionError("retrieval evidence producer is not authoritative")


def _reuse_first_retrieval_bytes(
    catalog: object, result: RetrievalResult
) -> RetrievalResult:
    connection = getattr(catalog, "connection", None)
    if connection is None:
        return result
    row = connection.execute(
        "SELECT payload_json FROM evidence WHERE evidence_id = ?",
        (result.evidence.evidence_id,),
    ).fetchone()
    if row is None:
        return result
    try:
        stored = EvidenceObject.model_validate_json(str(row[0]), strict=False)
    except Exception as error:
        raise CompositionError("stored retrieval evidence bytes are invalid") from error
    if _evidence_semantics(stored) != _evidence_semantics(result.evidence):
        raise CompositionError("retrieval evidence identity already binds different semantics")
    return result.model_copy(update={"evidence": stored})


def _evidence_semantics(evidence: EvidenceObject) -> dict[str, object]:
    payload = evidence.model_dump(mode="json", exclude_none=True)
    payload.pop("started_at", None)
    payload.pop("finished_at", None)
    payload.pop("duration_ms", None)
    return payload


def _snapshot_binding(retrieval: RetrievalResult) -> tuple[str, str]:
    if retrieval.evidence.kind != "retrieval" or retrieval.evidence.status not in {
        "verified",
        "degraded",
    }:
        raise CompositionError("retrieval evidence is not composable")
    snapshot_id = retrieval.evidence.output_summary.get("index_snapshot_id")
    snapshot_digest = retrieval.evidence.output_summary.get("index_snapshot_digest")
    if (
        not isinstance(snapshot_id, str)
        or _SAFE_ID.fullmatch(snapshot_id) is None
        or not isinstance(snapshot_digest, str)
        or _SHA256.fullmatch(snapshot_digest) is None
    ):
        raise CompositionError("retrieval result has no pinned snapshot digest")
    return snapshot_id, snapshot_digest


def _canonical_sequence(values: tuple[str, ...]) -> str:
    import json

    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _eligible(catalog: _CardCatalog, card: KnowledgeCardVersion) -> bool:
    checker = getattr(catalog, "card_is_eligible_for_composition", None)
    if callable(checker):
        return bool(checker(card.version_id))
    return card.status == "published"


def _covers_goal(card: KnowledgeCardVersion, goal: str) -> bool:
    return " ".join(card.learning_objective.casefold().split()) == " ".join(
        goal.casefold().split()
    )


def _coverage_gaps(
    requirement: CourseRequirement,
    cards: tuple[KnowledgeCardVersion, ...],
    options: CompositionOptions,
) -> tuple[str, ...]:
    tags = {assignment.tag_id for card in cards for assignment in card.tag_assignments}
    gaps = [f"tag:{tag}" for tag in requirement.required_tag_ids if tag not in tags]
    if any(tag in tags for tag in requirement.excluded_tag_ids):
        gaps.append("excluded-tag")
    if options.audience_tag_id is not None and options.audience_tag_id not in tags:
        gaps.append("audience")
    if options.difficulty_tag_id is not None and options.difficulty_tag_id not in tags:
        gaps.append("difficulty")
    if options.require_visual_refs and not any(card.visual_refs for card in cards):
        gaps.append("visual")
    if options.require_dataset_refs and not any(card.dataset_refs for card in cards):
        gaps.append("dataset")
    return tuple(gaps)


def _allocate(
    cards: tuple[KnowledgeCardVersion, ...], duration: int
) -> dict[str, int]:
    if not cards:
        return {}
    values = {card.version_id: 5 for card in cards}
    remaining = duration - 5 * len(cards)
    for card in cards:
        extra = min(
            remaining,
            max(0, ((card.suggested_minutes + 4) // 5) * 5 - 5),
        )
        values[card.version_id] += extra
        remaining -= extra
    index = 0
    while remaining:
        card = cards[index % len(cards)]
        values[card.version_id] += 5
        remaining -= 5
        index += 1
    return values


def _placement(
    card_id: str,
    chapter_id: str,
    ordinal: int,
    minutes: int,
    outline_version_id: str,
) -> CardPlacement:
    token = hashlib.sha256(
        f"{outline_version_id}|{card_id}|{chapter_id}|{ordinal}".encode("utf-8")
    ).hexdigest()[:24]
    return CardPlacement(
        placement_id=f"placement-{token}",
        card_version_id=card_id,
        chapter_id=chapter_id,
        lesson_id=f"lesson-{ordinal:02d}",
        purpose="core",
        allocated_minutes=minutes,
    )


__all__ = [
    "ALLOCATION_POLICY",
    "COMPOSER_BINDING_KIND",
    "COMPOSER_PRODUCER",
    "COMPOSER_PRODUCER_VERSION",
    "ComposerBindingPayload",
    "CompositionError",
    "CompositionGapError",
    "CompositionOptions",
    "CompositionResult",
    "ConfirmationSummary",
    "compose_and_register",
    "prepare_authoritative_composition",
    "register_prepared_composition",
    "composer_binding_identity_digest",
    "compose_outline",
    "confirmation_summary",
    "retrieve_and_compose",
    "verify_confirmation_summary",
]
