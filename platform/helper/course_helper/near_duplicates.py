"""Deterministic, blocking near-duplicate review primitives.

This module is the required pre-publication scan seam for course orchestration.
It deliberately does not make ``publish_card`` run a model implicitly: the
orchestrator must scan each persisted review candidate, while ``publish_card``
blocks any open review produced here.  Exact duplicate archival remains inside
the publication transaction because that lane is deterministic and automatic.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from course_helper.cards import create_review_task
from course_helper.catalog import (
    CatalogReferenceError,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject, LineageEdge
from course_helper.domain.knowledge import KnowledgeCardVersion, ReviewTask
from course_helper.embeddings import validate_embedding_vector
from course_helper.index_outbox import (
    IndexProviderIdentity,
    IndexSnapshotIntegrityError,
    _provider_record,
    _verified_card_projection,
)
from course_helper.lifecycle import append_card_lifecycle_event, reopen_card_version
from course_helper.retrieval import FtsCandidateScore, fts_candidate_scores
from course_helper.reviews import ReviewResolution, resolve_review_task


NEAR_DUPLICATE_POLICY_ID = "course-studio-near-dedup-v1"
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class NearDuplicateError(ValueError):
    """A near-duplicate scan or resolution violated a governed precondition."""


class NearDuplicatePolicy(BaseModel):
    """Versioned thresholds for deterministic lane admission and result bounds."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    policy_id: Literal["course-studio-near-dedup-v1"] = NEAR_DUPLICATE_POLICY_ID
    shingle_size: int = Field(default=3, ge=1, le=8)
    shingle_threshold: float = Field(default=0.45, ge=0, le=1)
    semantic_threshold: float = Field(default=0.92, ge=0, le=1)
    max_candidates: int = Field(default=12, ge=1, le=50)
    semantic_batch_size: int = Field(default=32, ge=1, le=128)


DEFAULT_NEAR_DUPLICATE_POLICY = NearDuplicatePolicy()


class NearDuplicateCandidate(BaseModel):
    """A payload-free comparison proposed by one or both scoring lanes."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )

    card_version_id: str = Field(min_length=1)
    fts_bm25: float | None = None
    shingle_score: float | None = Field(default=None, ge=0, le=1)
    semantic_score: float | None = Field(default=None, ge=-1, le=1)
    fts_rank: int | None = Field(default=None, ge=1)
    semantic_rank: int | None = Field(default=None, ge=1)
    matched_lanes: tuple[Literal["shingle-fts", "semantic"], ...]


class NearDuplicateScanResult(BaseModel):
    """Immutable output of one scan, including the persisted review receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_id: Literal["course-studio-near-dedup-v1"]
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_status: Literal[
        "available",
        "embedding-unavailable",
        "embedding-provider-invalid",
        "embedding-inference-failed",
    ]
    candidates: tuple[NearDuplicateCandidate, ...]
    evidence: EvidenceObject
    review_task: ReviewTask | None


def token_shingles(text: str, *, size: int = 3) -> tuple[tuple[str, ...], ...]:
    """Return sorted unique NFKC/case-folded token shingles."""

    if type(size) is not int or not 1 <= size <= 8:
        raise ValueError("shingle size must be from 1 to 8")
    if not isinstance(text, str):
        raise TypeError("shingle text must be a string")
    tokens = tuple(_TOKEN.findall(unicodedata.normalize("NFKC", text).casefold()))
    if not tokens:
        return ()
    width = min(size, len(tokens))
    return tuple(sorted({tokens[index : index + width] for index in range(len(tokens) - width + 1)}))


def scan_near_duplicates(
    card: KnowledgeCardVersion,
    catalog: KnowledgeCatalog,
    *,
    embedding_provider: object | None = None,
    policy: NearDuplicatePolicy = DEFAULT_NEAR_DUPLICATE_POLICY,
    created_at: datetime,
    _allow_active_transaction: bool = False,
) -> NearDuplicateScanResult:
    """Scan one persisted review candidate and open a blocking task when needed.

    Semantic unavailability or invalid provider output is a degraded, blocking
    result.  A scan only proposes candidates; it never archives, merges, or adds
    duplicate lineage without a later lecturer decision.
    """

    if not isinstance(card, KnowledgeCardVersion):
        raise NearDuplicateError("near-duplicate scan requires a knowledge card")
    if not isinstance(policy, NearDuplicatePolicy):
        raise NearDuplicateError("near-duplicate scan requires a versioned policy")
    if created_at.utcoffset() is None:
        raise NearDuplicateError("near-duplicate scan time must be timezone-aware")
    if catalog.connection.in_transaction and not _allow_active_transaction:
        raise NearDuplicateError("near-duplicate scan requires an idle catalog")

    with catalog.atomic_write():
        stored_row = catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone()
        if stored_row is None:
            raise NearDuplicateError(
                "near-duplicate candidate must be persisted before scanning"
            )
        if str(stored_row[0]) != canonical_model_json(card):
            raise NearDuplicateError(
                "near-duplicate candidate does not match persisted immutable bytes"
            )
        reopened = reopen_card_version(catalog.connection, card.version_id)
        if reopened.card.status != "review" or reopened.suspended:
            raise NearDuplicateError(
                "near-duplicate scan requires an unsuspended review candidate"
            )

        candidate_text = _card_projection(card, catalog)
        indexed = _published_index(catalog, excluded_version_id=card.version_id)
        candidate_digest = _digest(
            {
                "candidate_version_id": card.version_id,
                "projected_text_digest": _text_digest(candidate_text),
            }
        )
        index_digest = _digest(
            [
                {
                    "card_version_id": item[0],
                    "content_digest": item[1],
                    "projected_text_digest": _text_digest(item[2]),
                }
                for item in indexed
            ]
        )
        policy_digest = _digest(policy.model_dump(mode="json"))
        candidate_ids = tuple(item[0] for item in indexed)
        fts_scores = fts_candidate_scores(
            catalog,
            candidate_text,
            candidate_version_ids=candidate_ids,
        )
        fts_by_id = {item.card_version_id: item for item in fts_scores}
        fts_rank_by_id = {
            item.card_version_id: rank for rank, item in enumerate(fts_scores, 1)
        }
        candidate_shingles = set(
            token_shingles(candidate_text, size=policy.shingle_size)
        )
        shingle_by_id = {
            version_id: _jaccard(
                candidate_shingles,
                set(token_shingles(text, size=policy.shingle_size)),
            )
            for version_id, _content_digest, text in indexed
            if version_id in fts_by_id
        }
        semantic_scores, semantic_status, provider_summary = _semantic_lane(
            embedding_provider,
            candidate_text,
            indexed,
            batch_size=policy.semantic_batch_size,
        )
        semantic_rank_by_id = {
            version_id: rank
            for rank, (version_id, _score) in enumerate(semantic_scores, 1)
        }
        semantic_by_id = dict(semantic_scores)
        proposed = _proposed_candidates(
            indexed=indexed,
            fts_by_id=fts_by_id,
            fts_rank_by_id=fts_rank_by_id,
            shingle_by_id=shingle_by_id,
            semantic_by_id=semantic_by_id,
            semantic_rank_by_id=semantic_rank_by_id,
            policy=policy,
        )[: policy.max_candidates]
        provider_digest = None if not provider_summary else _digest(provider_summary)
        evidence = _scan_evidence(
            card_version_id=card.version_id,
            policy=policy,
            policy_digest=policy_digest,
            candidate_digest=candidate_digest,
            index_digest=index_digest,
            semantic_status=semantic_status,
            provider_summary=provider_summary,
            provider_digest=provider_digest,
            candidates=proposed,
            created_at=created_at,
        )
        existing_evidence = catalog.connection.execute(
            "SELECT payload_json FROM evidence WHERE evidence_id = ?",
            (evidence.evidence_id,),
        ).fetchone()
        if existing_evidence is not None:
            evidence = EvidenceObject.model_validate_json(
                existing_evidence[0], strict=False
            )
        catalog.insert_evidence(evidence)
        review_task = None
        if proposed or semantic_status != "available":
            review_task = create_review_task(
                catalog,
                kind="near-duplicate",
                subject_version_id=card.version_id,
                blocking=True,
                evidence_ids=(evidence.evidence_id,),
                created_at=evidence.started_at,
            )

    return NearDuplicateScanResult(
        policy_id=policy.policy_id,
        policy_digest=policy_digest,
        candidate_digest=candidate_digest,
        index_digest=index_digest,
        semantic_status=semantic_status,
        candidates=proposed,
        evidence=evidence,
        review_task=review_task,
    )


def resolve_near_duplicate_review(
    catalog: KnowledgeCatalog,
    *,
    task_id: str,
    decision: Literal["dismiss", "duplicate-link"],
    resolved_by: ActorRef,
    resolved_at: datetime,
    target_version_id: str | None = None,
) -> ReviewResolution:
    """Append a lecturer dismissal or explicit duplicate-link decision.

    ``duplicate-link`` maps to the schema-v1 ``accept`` resolution and archives
    the candidate through lifecycle truth in the same transaction as its
    evidence-backed ``deduplicates`` edge.  No scan can perform this action.
    """

    if resolved_at.utcoffset() is None:
        raise NearDuplicateError("near-duplicate resolution time must be timezone-aware")
    if decision == "dismiss" and target_version_id is not None:
        raise NearDuplicateError("dismissal cannot select a duplicate target")
    if decision == "duplicate-link" and not target_version_id:
        raise NearDuplicateError("duplicate-link requires a target version")
    if catalog.connection.in_transaction:
        raise NearDuplicateError("near-duplicate resolution requires an idle catalog")

    with catalog.atomic_write():
        row = catalog.connection.execute(
            "SELECT payload_json FROM review_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise NearDuplicateError("near-duplicate review task is unavailable")
        task = ReviewTask.model_validate_json(row[0], strict=False)
        if task.kind != "near-duplicate":
            raise NearDuplicateError("review task is not a near-duplicate task")
        candidate_ids = _review_candidate_ids(catalog, task)
        if decision == "duplicate-link":
            assert target_version_id is not None
            if target_version_id == task.subject_version_id:
                raise NearDuplicateError("duplicate target cannot be the candidate itself")
            if target_version_id not in candidate_ids:
                raise NearDuplicateError(
                    "duplicate target was not proposed by the reviewed scan"
                )
            published = catalog.connection.execute(
                "SELECT 1 FROM card_lifecycle_current WHERE card_version_id = ? "
                "AND status = 'published' AND suspended = 0",
                (target_version_id,),
            ).fetchone()
            if published is None:
                raise NearDuplicateError(
                    "duplicate target must be an unsuspended published card"
                )

        expected_review_digest = hashlib.sha256(str(row[0]).encode("utf-8")).hexdigest()
        decision_core = {
            "decision": decision,
            "resolved_at": resolved_at.isoformat(),
            "resolved_by": resolved_by.model_dump(mode="json"),
            "review_task_id": task.task_id,
            "target_version_id": target_version_id,
        }
        evidence = EvidenceObject(
            evidence_id="near-dedup-decision-" + _digest(decision_core),
            kind="dedup",
            subject_version_id=task.subject_version_id,
            status="verified",
            input_summary={
                "decision": decision,
                "review_evidence_ids": list(task.evidence_ids),
                "review_task_id": task.task_id,
            },
            output_summary={
                "candidate_version_id": task.subject_version_id,
                "target_version_id": target_version_id,
            },
            producer="course-helper/near-duplicates",
            producer_version="course-studio-near-dedup-v1",
            started_at=resolved_at,
            finished_at=resolved_at,
            duration_ms=0,
            checks=(
                EvidenceCheck(
                    code="near-duplicate-human-decision",
                    status="passed",
                    message="Lecturer recorded an explicit near-duplicate decision",
                    details={"decision": decision},
                ),
            ),
        )
        catalog.insert_evidence(evidence)
        resolution = ReviewResolution(
            resolution_id="near-dedup-resolution-" + _digest(decision_core),
            task_id=task.task_id,
            decision="dismiss" if decision == "dismiss" else "accept",
            expected_review_digest=expected_review_digest,
            evidence_ids=(evidence.evidence_id,),
            resolved_at=resolved_at,
            resolved_by=resolved_by,
        )
        resolution = resolve_review_task(catalog, resolution)

        if decision == "duplicate-link":
            assert target_version_id is not None
            edge = LineageEdge(
                edge_id="lineage-"
                + _digest(
                    {
                        "evidence_id": evidence.evidence_id,
                        "from_version_id": task.subject_version_id,
                        "relation": "deduplicates",
                        "to_version_id": target_version_id,
                    }
                ),
                from_version_id=task.subject_version_id,
                to_version_id=target_version_id,
                relation="deduplicates",
                evidence_id=evidence.evidence_id,
                created_at=resolved_at,
            )
            catalog.insert_lineage(edge)
            lifecycle_request = _digest(
                {
                    "candidate_version_id": task.subject_version_id,
                    "resolution_id": resolution.resolution_id,
                    "target_version_id": target_version_id,
                }
            )
            append_card_lifecycle_event(
                catalog.connection,
                card_version_id=task.subject_version_id,
                event_id=f"near-dedup:archive:{resolution.resolution_id}",
                request_digest=lifecycle_request,
                event_type="archive",
                occurred_at=resolved_at,
                actor_id=resolved_by.actor_id,
            )
    return resolution


def _published_index(
    catalog: KnowledgeCatalog,
    *,
    excluded_version_id: str,
) -> tuple[tuple[str, str, str], ...]:
    rows = catalog.connection.execute(
        "SELECT cards.version_id, cards.content_digest, cards.payload_json, "
        "card_fts.title, card_fts.learning_objective, card_fts.body, "
        "card_fts.chunk_text, card_fts.projected_text "
        "FROM cards JOIN card_lifecycle_current lifecycle "
        "ON lifecycle.card_version_id = cards.version_id "
        "LEFT JOIN card_fts ON card_fts.version_id = cards.version_id "
        "WHERE lifecycle.status = 'published' AND lifecycle.suspended = 0 "
        "AND cards.version_id <> ? ORDER BY cards.version_id",
        (excluded_version_id,),
    ).fetchall()
    indexed: list[tuple[str, str, str]] = []
    for row in rows:
        try:
            card = KnowledgeCardVersion.model_validate_json(row[2], strict=False)
            expected = _verified_card_projection(catalog, card)
        except Exception as error:
            raise NearDuplicateError(
                "published near-duplicate index projection is invalid"
            ) from error
        actual = None if row[3] is None else tuple(str(value) for value in row[3:8])
        if (
            card.version_id != str(row[0])
            or card.content_digest != str(row[1])
            or canonical_model_json(card) != str(row[2])
            or actual != expected
        ):
            raise NearDuplicateError(
                "published near-duplicate index projection is invalid"
            )
        indexed.append((card.version_id, card.content_digest, expected[-1]))
    return tuple(indexed)


def _card_projection(card: KnowledgeCardVersion, catalog: KnowledgeCatalog) -> str:
    body: list[str] = []
    stack = list(reversed(card.content_ast))
    while stack:
        node = stack.pop()
        if node.text:
            body.append(node.text)
        body.extend(cell for row in node.rows for cell in row if cell)
        stack.extend(reversed(node.children))
    chunk_text: list[str] = []
    for citation in card.chunk_citations:
        row = catalog.connection.execute(
            "SELECT json_extract(payload_json, '$.normalized_text') FROM chunks "
            "WHERE chunk_id = ? AND source_version_id = ?",
            (citation.chunk_id, citation.source_version_id),
        ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise NearDuplicateError("candidate citation projection is unavailable")
        chunk_text.append(str(row[0]))
    return "\n".join(
        part
        for part in (
            card.title,
            card.learning_objective,
            "\n".join(body),
            "\n".join(chunk_text),
        )
        if part
    )


def _semantic_lane(
    provider: object | None,
    candidate_text: str,
    indexed: tuple[tuple[str, str, str], ...],
    *,
    batch_size: int,
) -> tuple[
    tuple[tuple[str, float], ...],
    Literal[
        "available",
        "embedding-unavailable",
        "embedding-provider-invalid",
        "embedding-inference-failed",
    ],
    dict[str, object],
]:
    if provider is None:
        return (), "embedding-unavailable", {}
    try:
        provider_record = _provider_record(provider)
    except IndexSnapshotIntegrityError:
        return (), "embedding-provider-invalid", {}
    if provider_record is None:
        return (), "embedding-provider-invalid", {}
    summary = provider_record.model_dump(mode="json")
    embed_query = getattr(provider, "embed_query", None)
    embed_documents = getattr(provider, "embed_documents", None)
    if not callable(embed_query) or not callable(embed_documents):
        return (), "embedding-provider-invalid", summary
    try:
        query_vector = validate_embedding_vector(embed_query(candidate_text), dimension=512)
        scored: list[tuple[str, float]] = []
        for start in range(0, len(indexed), batch_size):
            indexed_batch = indexed[start : start + batch_size]
            batch = tuple(item[2] for item in indexed_batch)
            raw_documents = tuple(embed_documents(batch))
            if len(raw_documents) != len(batch):
                raise ValueError("embedding document count mismatch")
            document_vectors = tuple(
                validate_embedding_vector(vector, dimension=512)
                for vector in raw_documents
            )
            for item, vector in zip(indexed_batch, document_vectors, strict=True):
                cosine = float(
                    sum(
                        left * right
                        for left, right in zip(query_vector, vector, strict=True)
                    )
                )
                scored.append((item[0], max(-1.0, min(1.0, cosine))))
    except Exception:
        return (), "embedding-inference-failed", summary
    scores = tuple(sorted(scored, key=lambda item: (-item[1], item[0])))
    return scores, "available", summary


def _proposed_candidates(
    *,
    indexed: tuple[tuple[str, str, str], ...],
    fts_by_id: dict[str, FtsCandidateScore],
    fts_rank_by_id: dict[str, int],
    shingle_by_id: dict[str, float],
    semantic_by_id: dict[str, float],
    semantic_rank_by_id: dict[str, int],
    policy: NearDuplicatePolicy,
) -> tuple[NearDuplicateCandidate, ...]:
    proposed: list[NearDuplicateCandidate] = []
    for version_id, _content_digest, _text in indexed:
        shingle_score = shingle_by_id.get(version_id)
        semantic_score = semantic_by_id.get(version_id)
        fts_match = (
            version_id in fts_by_id
            and shingle_score is not None
            and shingle_score >= policy.shingle_threshold
        )
        semantic_match = (
            semantic_score is not None
            and semantic_score >= policy.semantic_threshold
        )
        if not fts_match and not semantic_match:
            continue
        lanes: tuple[Literal["shingle-fts", "semantic"], ...] = tuple(
            lane
            for lane, matched in (
                ("shingle-fts", fts_match),
                ("semantic", semantic_match),
            )
            if matched
        )
        proposed.append(
            NearDuplicateCandidate(
                card_version_id=version_id,
                fts_bm25=(
                    None if version_id not in fts_by_id else fts_by_id[version_id].bm25
                ),
                shingle_score=shingle_score,
                semantic_score=semantic_score,
                fts_rank=fts_rank_by_id.get(version_id),
                semantic_rank=semantic_rank_by_id.get(version_id),
                matched_lanes=lanes,
            )
        )
    return tuple(
        sorted(
            proposed,
            key=lambda item: (
                0 if "shingle-fts" in item.matched_lanes else 1,
                item.fts_rank if "shingle-fts" in item.matched_lanes else item.semantic_rank,
                item.card_version_id,
            ),
        )
    )


def _scan_evidence(
    *,
    card_version_id: str,
    policy: NearDuplicatePolicy,
    policy_digest: str,
    candidate_digest: str,
    index_digest: str,
    semantic_status: str,
    provider_summary: dict[str, object],
    provider_digest: str | None,
    candidates: tuple[NearDuplicateCandidate, ...],
    created_at: datetime,
) -> EvidenceObject:
    output_summary = {
        "candidate_ids": [item.card_version_id for item in candidates],
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "semantic_status": semantic_status,
    }
    core = {
        "candidate_digest": candidate_digest,
        "candidate_version_id": card_version_id,
        "index_digest": index_digest,
        "output_summary": output_summary,
        "policy_digest": policy_digest,
        "provider_identity_digest": provider_digest,
    }
    degraded = semantic_status != "available"
    return EvidenceObject(
        evidence_id="near-dedup-" + _digest(core),
        kind="dedup",
        subject_version_id=card_version_id,
        status="degraded" if degraded else ("warning" if candidates else "verified"),
        input_summary={
            "candidate_digest": candidate_digest,
            "candidate_version_id": card_version_id,
            "index_digest": index_digest,
            "policy_digest": policy_digest,
            "policy_id": policy.policy_id,
            "provider_identity": provider_summary,
            "provider_identity_digest": provider_digest,
            "thresholds": {
                "max_candidates": policy.max_candidates,
                "semantic_batch_size": policy.semantic_batch_size,
                "shingle_size": policy.shingle_size,
                "shingle_threshold": policy.shingle_threshold,
                "semantic_threshold": policy.semantic_threshold,
            },
        },
        output_summary=output_summary,
        producer="course-helper/near-duplicates",
        producer_version=policy.policy_id,
        started_at=created_at,
        finished_at=created_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="near-dedup-policy",
                status="passed",
                message="Near-duplicate thresholds and digests are policy-bound",
                details={"policy_digest": policy_digest},
            ),
            EvidenceCheck(
                code="shingle-fts-near-dedup",
                status="passed",
                message="Deterministic shingle and FTS lane completed",
                details={"candidate_count": len(candidates)},
            ),
            EvidenceCheck(
                code="semantic-near-dedup",
                status="warning" if degraded else "passed",
                message=(
                    "Semantic comparison requires lecturer review"
                    if degraded
                    else "Verified semantic comparison completed"
                ),
                details={"semantic_status": semantic_status},
            ),
        ),
    )


def _review_candidate_ids(
    catalog: KnowledgeCatalog, task: ReviewTask
) -> set[str]:
    if len(task.evidence_ids) != 1:
        raise NearDuplicateError(
            "near-duplicate review must reference exactly one scan receipt"
        )
    evidence_id = task.evidence_ids[0]
    row = catalog.connection.execute(
        "SELECT evidence_id, kind, status, payload_json FROM evidence "
        "WHERE evidence_id = ?",
        (evidence_id,),
    ).fetchone()
    if row is None:
        raise CatalogReferenceError(
            f"near-duplicate evidence is unavailable: {evidence_id!r}"
        )
    payload = str(row[3])
    try:
        evidence = EvidenceObject.model_validate_json(payload, strict=False)
        dumped = evidence.model_dump(mode="json")
        input_summary = dumped["input_summary"]
        output_summary = dumped["output_summary"]
        thresholds = input_summary["thresholds"]
        policy = NearDuplicatePolicy(
            policy_id=input_summary["policy_id"],
            **thresholds,
        )
        candidate_models = tuple(
            NearDuplicateCandidate.model_validate(value, strict=False)
            for value in output_summary["candidates"]
        )
    except Exception as error:
        raise NearDuplicateError(
            "near-duplicate scan evidence envelope is invalid"
        ) from error
    raw_candidate_ids = output_summary.get("candidate_ids", ())
    if not isinstance(raw_candidate_ids, list) or any(
        not isinstance(value, str) for value in raw_candidate_ids
    ):
        raise NearDuplicateError("near-duplicate scan evidence envelope is invalid")
    candidate_ids = tuple(raw_candidate_ids)
    semantic_status = output_summary.get("semantic_status")
    provider_summary = input_summary.get("provider_identity")
    provider_digest = input_summary.get("provider_identity_digest")
    expected_status = (
        "degraded"
        if semantic_status != "available"
        else ("warning" if candidate_models else "verified")
    )
    valid_provider = _stored_provider_summary(provider_summary)
    policy_digest = input_summary.get("policy_digest")
    candidate_digest = input_summary.get("candidate_digest")
    index_digest = input_summary.get("index_digest")
    expected_core = {
        "candidate_digest": candidate_digest,
        "candidate_version_id": task.subject_version_id,
        "index_digest": index_digest,
        "output_summary": output_summary,
        "policy_digest": policy_digest,
        "provider_identity_digest": provider_digest,
    }
    if (
        canonical_model_json(evidence) != payload
        or tuple(row[:3]) != (evidence.evidence_id, evidence.kind, evidence.status)
        or evidence.evidence_id != evidence_id
        or evidence.kind != "dedup"
        or evidence.subject_version_id != task.subject_version_id
        or evidence.producer != "course-helper/near-duplicates"
        or evidence.producer_version != NEAR_DUPLICATE_POLICY_ID
        or evidence.status != expected_status
        or set(input_summary)
        != {
            "candidate_digest",
            "candidate_version_id",
            "index_digest",
            "policy_digest",
            "policy_id",
            "provider_identity",
            "provider_identity_digest",
            "thresholds",
        }
        or set(output_summary)
        != {"candidate_ids", "candidates", "semantic_status"}
        or set(thresholds)
        != {
            "max_candidates",
            "semantic_batch_size",
            "shingle_size",
            "shingle_threshold",
            "semantic_threshold",
        }
        or input_summary.get("candidate_version_id") != task.subject_version_id
        or not _is_digest(candidate_digest)
        or not _is_digest(index_digest)
        or not _is_digest(policy_digest)
        or policy_digest != _digest(policy.model_dump(mode="json"))
        or candidate_ids != tuple(item.card_version_id for item in candidate_models)
        or output_summary["candidates"]
        != [item.model_dump(mode="json") for item in candidate_models]
        or len(candidate_ids) != len(set(candidate_ids))
        or len(candidate_ids) > policy.max_candidates
        or semantic_status
        not in {
            "available",
            "embedding-unavailable",
            "embedding-provider-invalid",
            "embedding-inference-failed",
        }
        or (
            valid_provider is None
            and (provider_summary not in ({}, None) or provider_digest is not None)
        )
        or (
            valid_provider is not None
            and (
                provider_summary != valid_provider.model_dump(mode="json")
                or provider_digest != _digest(valid_provider.model_dump(mode="json"))
            )
        )
        or (semantic_status == "available" and valid_provider is None)
        or evidence.evidence_id != "near-dedup-" + _digest(expected_core)
        or tuple(check.code for check in evidence.checks)
        != (
            "near-dedup-policy",
            "shingle-fts-near-dedup",
            "semantic-near-dedup",
        )
    ):
        raise NearDuplicateError("near-duplicate scan evidence envelope is invalid")
    return set(candidate_ids)


def _stored_provider_summary(value: object) -> IndexProviderIdentity | None:
    if value in ({}, None):
        return None
    try:
        record = IndexProviderIdentity.model_validate(value, strict=False)
    except Exception:
        return None
    if (
        record.provider != "fastembed"
        or record.provider_version != "0.8.0"
        or record.model_id != "BAAI/bge-small-zh-v1.5"
        or record.model_revision != "7999e1d3359715c523056ef9478215996d62a620"
        or record.artifact_repository != "Qdrant/bge-small-zh-v1.5"
        or record.artifact_revision != "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
        or record.dimension != 512
        or record.encoding_policy != "utf8-nfkc-no-prefix"
        or len(record.model_file_sha256s) != 5
        or len(set(record.model_file_sha256s)) != 5
        or record.model_file_sha256s[1]
        != "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38"
    ):
        return None
    return record


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _jaccard(
    left: set[tuple[str, ...]], right: set[tuple[str, ...]]
) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _text_digest(value: str) -> str:
    return hashlib.sha256(
        unicodedata.normalize("NFKC", value).encode("utf-8")
    ).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DEFAULT_NEAR_DUPLICATE_POLICY",
    "NEAR_DUPLICATE_POLICY_ID",
    "NearDuplicateCandidate",
    "NearDuplicateError",
    "NearDuplicatePolicy",
    "NearDuplicateScanResult",
    "resolve_near_duplicate_review",
    "scan_near_duplicates",
    "token_shingles",
]
