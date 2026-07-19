"""Append-only claims and deterministic sealing for the knowledge index outbox."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from course_helper.catalog import KnowledgeCatalog, canonical_model_json
from course_helper.domain.knowledge import KnowledgeCardVersion
from course_helper.domain.sources import ExtractedChunk
from course_helper.embeddings import (
    RRF_POLICY_ID,
    EmbeddingProviderIdentity,
    validate_embedding_vector,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_MODEL_FILES = {
    "config.json": 739,
    "model_optimized.onnx": 94781076,
    "special_tokens_map.json": 125,
    "tokenizer.json": 439125,
    "tokenizer_config.json": 367,
}
_EXPECTED_ONNX_SHA256 = (
    "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38"
)
DOCUMENT_ENCODING_POLICY = "utf8-nfkc-card-projection-v1"
QUERY_ENCODING_POLICY = "utf8-nfkc-no-prefix"


class IndexLeaseConflict(RuntimeError):
    """A claim is no longer owned by the caller or its lease is no longer live."""


class IndexSnapshotIntegrityError(RuntimeError):
    """Stored candidate, row, or seal facts no longer match their digest."""


class IndexOutboxClaim(BaseModel):
    """One immutable processing attempt for one transactional outbox row."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    outbox_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    attempt: int = Field(ge=1)
    worker_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    outbox_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease_expires_at: datetime
    claim_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class IndexProviderIdentity(BaseModel):
    """Path-free exact provider identity safe to project in retrieval evidence."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, protected_namespaces=()
    )

    provider: str = Field(min_length=1, max_length=64)
    provider_version: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_repository: str = Field(min_length=1, max_length=200)
    artifact_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dimension: Literal[512]
    encoding_policy: str = Field(min_length=1, max_length=100)
    model_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    wheel_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_file_sha256s: tuple[str, ...] = Field(min_length=1)


class IndexSnapshot(BaseModel):
    """One immutable sealed hybrid or explicitly degraded retrieval snapshot."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, protected_namespaces=()
    )

    index_snapshot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    status: Literal["ready", "degraded"]
    retrieval_mode: Literal["hybrid", "fts-degraded"]
    policy_id: Literal["course-studio-rrf-v1"]
    model_manifest_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    eligible_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outbox_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outbox_watermark: int = Field(ge=0)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fts_rows_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_rows_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_identity: IndexProviderIdentity | None = None
    document_encoding_policy: Literal["utf8-nfkc-card-projection-v1"]
    query_encoding_policy: Literal["utf8-nfkc-no-prefix"]
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    encoded = value if isinstance(value, str) else _canonical(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware_utc(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _provider_record(provider: object | None) -> IndexProviderIdentity | None:
    if provider is None:
        return None
    identity = getattr(provider, "identity", None)
    if not isinstance(identity, EmbeddingProviderIdentity):
        raise IndexSnapshotIntegrityError("embedding provider identity is not verified")
    try:
        if (
            identity.provider != "fastembed"
            or identity.provider_version != "0.8.0"
            or identity.model_id != "BAAI/bge-small-zh-v1.5"
            or identity.model_revision
            != "7999e1d3359715c523056ef9478215996d62a620"
            or identity.artifact_repository != "Qdrant/bge-small-zh-v1.5"
            or identity.artifact_revision
            != "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
            or identity.dimension != 512
            or identity.encoding_policy != QUERY_ENCODING_POLICY
        ):
            raise ValueError("provider identity does not match the pinned model")
        inventory = {
            str(path): (str(digest), int(size))
            for path, digest, size in identity.model_files
        }
        if (
            len(identity.model_files) != len(_EXPECTED_MODEL_FILES)
            or set(inventory) != set(_EXPECTED_MODEL_FILES)
            or any(
                inventory[path][1] != expected_size
                or _SHA256.fullmatch(inventory[path][0]) is None
                for path, expected_size in _EXPECTED_MODEL_FILES.items()
            )
            or inventory["model_optimized.onnx"][0] != _EXPECTED_ONNX_SHA256
            or any(
                _SHA256.fullmatch(value) is None
                for value in (
                    identity.model_manifest_digest,
                    identity.cache_digest,
                    identity.runtime_digest,
                    identity.wheel_set_digest,
                    identity.generation_digest,
                )
            )
        ):
            raise ValueError("provider inventory does not match the pinned model")
        file_hashes = tuple(
            item[1] for item in sorted(identity.model_files, key=lambda item: item[0])
        )
        if len(file_hashes) != len(set(file_hashes)):
            raise ValueError("model file hashes must be unique")
        return IndexProviderIdentity(
            provider=identity.provider,
            provider_version=identity.provider_version,
            model_id=identity.model_id,
            model_revision=identity.model_revision,
            artifact_repository=identity.artifact_repository,
            artifact_revision=identity.artifact_revision,
            dimension=identity.dimension,
            encoding_policy=identity.encoding_policy,
            model_manifest_digest=identity.model_manifest_digest,
            cache_digest=identity.cache_digest,
            runtime_digest=identity.runtime_digest,
            wheel_set_digest=identity.wheel_set_digest,
            generation_digest=identity.generation_digest,
            model_file_sha256s=file_hashes,
        )
    except Exception as error:
        raise IndexSnapshotIntegrityError("embedding provider identity is invalid") from error


def _card_body(card: KnowledgeCardVersion) -> str:
    parts: list[str] = []
    pending = list(reversed(card.content_ast))
    while pending:
        node = pending.pop()
        if node.text:
            parts.append(node.text)
        parts.extend(cell for row in node.rows for cell in row if cell)
        pending.extend(reversed(node.children))
    return "\n".join(parts)


def _verified_card_projection(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
) -> tuple[str, str, str, str, str]:
    body = _card_body(card)
    chunks: list[str] = []
    for citation in card.chunk_citations:
        chunk_row = catalog.connection.execute(
            "SELECT source_version_id, content_digest, payload_json FROM chunks "
            "WHERE chunk_id = ?",
            (citation.chunk_id,),
        ).fetchone()
        if chunk_row is None or str(chunk_row[0]) != citation.source_version_id:
            raise IndexSnapshotIntegrityError("card citation is unavailable")
        try:
            chunk = ExtractedChunk.model_validate_json(str(chunk_row[2]), strict=False)
        except Exception as error:
            raise IndexSnapshotIntegrityError("card citation payload is invalid") from error
        if (
            chunk.chunk_id != citation.chunk_id
            or chunk.source_version_id != citation.source_version_id
            or chunk.content_digest != str(chunk_row[1])
            or canonical_model_json(chunk) != chunk_row[2]
        ):
            raise IndexSnapshotIntegrityError("card citation envelope is invalid")
        chunks.append(chunk.normalized_text)
    chunk_text = "\n".join(chunks)
    projected_text = "\n".join(
        part
        for part in (card.title, card.learning_objective, body, chunk_text)
        if part
    )
    return card.title, card.learning_objective, body, chunk_text, projected_text


def _read_index_state(catalog: KnowledgeCatalog) -> dict[str, object]:
    eligible_rows = catalog.connection.execute(
        "SELECT cards.version_id, cards.content_digest, cards.payload_json, "
        "lifecycle.status, lifecycle.suspended, lifecycle.last_sequence, "
        "lifecycle.last_event_id, card_fts.title, card_fts.learning_objective, "
        "card_fts.body, card_fts.chunk_text, card_fts.projected_text "
        "FROM cards "
        "JOIN card_lifecycle_current AS lifecycle "
        "ON lifecycle.card_version_id = cards.version_id "
        "LEFT JOIN card_fts ON card_fts.version_id = cards.version_id "
        "WHERE lifecycle.status = 'published' AND lifecycle.suspended = 0 "
        "ORDER BY cards.version_id"
    ).fetchall()
    eligible_cards: list[dict[str, object]] = []
    lifecycle_facts: list[dict[str, object]] = []
    documents: list[tuple[str, str]] = []
    for row in eligible_rows:
        version_id, content_digest, payload_json = str(row[0]), str(row[1]), str(row[2])
        try:
            card = KnowledgeCardVersion.model_validate_json(payload_json, strict=False)
        except Exception as error:
            raise IndexSnapshotIntegrityError("eligible card payload is invalid") from error
        if (
            card.version_id != version_id
            or card.content_digest != content_digest
            or canonical_model_json(card) != payload_json
        ):
            raise IndexSnapshotIntegrityError("eligible card envelope is invalid")
        if any(value is None for value in row[7:12]):
            raise IndexSnapshotIntegrityError("eligible card FTS projection is unavailable")
        projection = _verified_card_projection(catalog, card)
        stored_projection = tuple(str(value) for value in row[7:12])
        if stored_projection != projection:
            raise IndexSnapshotIntegrityError("FTS projection does not match immutable card")
        projected_text = projection[-1]
        document_digest = _digest(projected_text)
        eligible_cards.append(
            {
                "card_content_digest": content_digest,
                "card_version_id": version_id,
                "document_digest": document_digest,
            }
        )
        lifecycle_facts.append(
            {
                "card_version_id": version_id,
                "last_event_id": str(row[6]),
                "last_sequence": int(row[5]),
                "status": str(row[3]),
                "suspended": int(row[4]),
            }
        )
        documents.append((version_id, projected_text))

    outbox_rows = catalog.connection.execute(
        "SELECT outbox.rowid, outbox.outbox_id, outbox.operation_id, "
        "outbox.request_digest, outbox.card_version_id, "
        "outbox.action, outbox.content_digest, outbox.payload_json, "
        "operation.request_digest, "
        "operation.status FROM knowledge_index_outbox AS outbox "
        "JOIN operation_outcomes AS operation "
        "ON operation.operation_id = outbox.operation_id ORDER BY outbox.rowid"
    ).fetchall()
    outbox_facts: list[dict[str, object]] = []
    for row in outbox_rows:
        payload_json = str(row[7])
        content_digest = str(row[6])
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError) as error:
            raise IndexSnapshotIntegrityError("index outbox payload is invalid") from error
        expected = {
            "action": str(row[5]),
            "card_version_id": str(row[4]),
            "operation_id": str(row[2]),
            "outbox_id": str(row[1]),
            "request_digest": str(row[3]),
        }
        if (
            payload != expected
            or _canonical(payload) != payload_json
            or _digest(payload_json) != content_digest
            or row[8] != row[3]
            or row[9] != "committed"
        ):
            raise IndexSnapshotIntegrityError("index outbox envelope is invalid")
        outbox_facts.append(
            {
                "action": str(row[5]),
                "card_version_id": str(row[4]),
                "content_digest": content_digest,
                "outbox_id": str(row[1]),
                "rowid": int(row[0]),
            }
        )

    eligible_tuple = tuple(eligible_cards)
    lifecycle_tuple = tuple(lifecycle_facts)
    outbox_tuple = tuple(outbox_facts)
    return {
        "documents": tuple(documents),
        "eligible_cards": eligible_tuple,
        "eligible_set_digest": _digest(eligible_tuple),
        "lifecycle_digest": _digest(lifecycle_tuple),
        "lifecycle_facts": lifecycle_tuple,
        "outbox_digest": _digest(outbox_tuple),
        "outbox_facts": outbox_tuple,
        "outbox_watermark": 0 if not outbox_tuple else int(outbox_tuple[-1]["rowid"]),
    }


def _candidate_core(
    state: dict[str, object],
    provider: IndexProviderIdentity | None,
) -> dict[str, object]:
    return {
        "document_encoding_policy": DOCUMENT_ENCODING_POLICY,
        "eligible_cards": state["eligible_cards"],
        "eligible_set_digest": state["eligible_set_digest"],
        "lifecycle_digest": state["lifecycle_digest"],
        "lifecycle_facts": state["lifecycle_facts"],
        "model_manifest_digest": (
            None if provider is None else provider.model_manifest_digest
        ),
        "outbox_digest": state["outbox_digest"],
        "outbox_facts": state["outbox_facts"],
        "outbox_watermark": state["outbox_watermark"],
        "policy_id": RRF_POLICY_ID,
        "provider_identity": (
            None if provider is None else provider.model_dump(mode="json")
        ),
        "query_encoding_policy": QUERY_ENCODING_POLICY,
    }


def _insert_or_validate_candidate(
    catalog: KnowledgeCatalog,
    *,
    state: dict[str, object],
    provider: IndexProviderIdentity | None,
    created_at: datetime,
) -> tuple[str, str, dict[str, object]]:
    core = _candidate_core(state, provider)
    candidate_digest = _digest(core)
    candidate_id = f"index-candidate-{candidate_digest[:48]}"
    existing = catalog.connection.execute(
        "SELECT policy_id, model_manifest_digest, eligible_set_digest, "
        "lifecycle_digest, outbox_digest, outbox_watermark, candidate_digest, "
        "payload_json FROM embedding_index_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing is not None:
        try:
            payload = json.loads(str(existing[7]))
        except (TypeError, ValueError) as error:
            raise IndexSnapshotIntegrityError("index candidate payload is invalid") from error
        if (
            payload.get("candidate_id") != candidate_id
            or payload.get("candidate_digest") != candidate_digest
            or payload.get("core") != json.loads(_canonical(core))
            or _canonical(payload) != existing[7]
            or tuple(existing[:7])
            != (
                RRF_POLICY_ID,
                None if provider is None else provider.model_manifest_digest,
                state["eligible_set_digest"],
                state["lifecycle_digest"],
                state["outbox_digest"],
                state["outbox_watermark"],
                candidate_digest,
            )
        ):
            raise IndexSnapshotIntegrityError("index candidate envelope is invalid")
        return candidate_id, candidate_digest, payload

    payload = {
        "candidate_digest": candidate_digest,
        "candidate_id": candidate_id,
        "core": core,
        "created_at": _time_text(created_at),
    }
    payload_json = _canonical(payload)
    catalog.connection.execute(
        "INSERT INTO embedding_index_candidates("
        "candidate_id, policy_id, model_manifest_digest, eligible_set_digest, "
        "lifecycle_digest, outbox_digest, outbox_watermark, candidate_digest, "
        "payload_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            candidate_id,
            RRF_POLICY_ID,
            None if provider is None else provider.model_manifest_digest,
            state["eligible_set_digest"],
            state["lifecycle_digest"],
            state["outbox_digest"],
            state["outbox_watermark"],
            candidate_digest,
            payload_json,
            _time_text(created_at),
        ),
    )
    return candidate_id, candidate_digest, json.loads(payload_json)


def _claim_core(
    *,
    outbox_id: str,
    attempt: int,
    worker_id: str,
    outbox_content_digest: str,
    lease_expires_at: datetime,
    created_at: datetime,
) -> dict[str, object]:
    return {
        "attempt": attempt,
        "created_at": _time_text(created_at),
        "lease_expires_at": _time_text(lease_expires_at),
        "outbox_content_digest": outbox_content_digest,
        "outbox_id": outbox_id,
        "worker_id": worker_id,
    }


def _claim_payload(claim: IndexOutboxClaim) -> str:
    return _canonical(
        {
            **_claim_core(
                outbox_id=claim.outbox_id,
                attempt=claim.attempt,
                worker_id=claim.worker_id,
                outbox_content_digest=claim.outbox_content_digest,
                lease_expires_at=claim.lease_expires_at,
                created_at=claim.created_at,
            ),
            "claim_digest": claim.claim_digest,
            "claim_id": claim.claim_id,
        }
    )


def _load_claim(catalog: KnowledgeCatalog, claim_id: str) -> IndexOutboxClaim:
    row = catalog.connection.execute(
        "SELECT outbox_id, attempt, worker_id, outbox_content_digest, "
        "lease_expires_at, claim_digest, payload_json, created_at "
        "FROM knowledge_index_outbox_claims "
        "WHERE claim_id = ?",
        (claim_id,),
    ).fetchone()
    if row is None:
        raise IndexLeaseConflict("index claim is unknown")
    try:
        claim = IndexOutboxClaim.model_validate_json(str(row[6]), strict=False)
    except Exception as error:
        raise IndexLeaseConflict("index claim payload is invalid") from error
    core_digest = _digest(
        _claim_core(
            outbox_id=claim.outbox_id,
            attempt=claim.attempt,
            worker_id=claim.worker_id,
            outbox_content_digest=claim.outbox_content_digest,
            lease_expires_at=claim.lease_expires_at,
            created_at=claim.created_at,
        )
    )
    if (
        claim.claim_id != claim_id
        or (
            claim.outbox_id,
            claim.attempt,
            claim.worker_id,
            claim.outbox_content_digest,
            _time_text(claim.lease_expires_at),
            claim.claim_digest,
            _claim_payload(claim),
            _time_text(claim.created_at),
        )
        != tuple(row)
        or core_digest != claim.claim_digest
    ):
        raise IndexLeaseConflict("index claim integrity check failed")
    return claim


def _append_expired_result(
    catalog: KnowledgeCatalog,
    claim: IndexOutboxClaim,
    *,
    now: datetime,
) -> None:
    core = {
        "attempt": claim.attempt,
        "claim_id": claim.claim_id,
        "created_at": _time_text(now),
        "index_snapshot_id": None,
        "outbox_id": claim.outbox_id,
        "status": "lease-expired",
        "worker_id": claim.worker_id,
    }
    result_digest = _digest(core)
    result_id = f"index-result-{result_digest[:48]}"
    payload = _canonical(
        {**core, "result_digest": result_digest, "result_id": result_id}
    )
    catalog.connection.execute(
        "INSERT INTO knowledge_index_outbox_results("
        "result_id, claim_id, outbox_id, attempt, worker_id, status, "
        "index_snapshot_id, result_digest, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, 'lease-expired', NULL, ?, ?, ?)",
        (
            result_id,
            claim.claim_id,
            claim.outbox_id,
            claim.attempt,
            claim.worker_id,
            result_digest,
            payload,
            _time_text(now),
        ),
    )


def _append_failed_result(
    catalog: KnowledgeCatalog,
    claim: IndexOutboxClaim,
    *,
    now: datetime,
) -> None:
    core = {
        "attempt": claim.attempt,
        "claim_id": claim.claim_id,
        "created_at": _time_text(now),
        "error_code": "INDEX_BUILD_FAILED",
        "index_snapshot_id": None,
        "outbox_id": claim.outbox_id,
        "status": "failed",
        "worker_id": claim.worker_id,
    }
    result_digest = _digest(core)
    result_id = f"index-result-{result_digest[:48]}"
    payload = _canonical(
        {**core, "result_digest": result_digest, "result_id": result_id}
    )
    catalog.connection.execute(
        "INSERT INTO knowledge_index_outbox_results("
        "result_id, claim_id, outbox_id, attempt, worker_id, status, "
        "index_snapshot_id, result_digest, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, 'failed', NULL, ?, ?, ?)",
        (
            result_id,
            claim.claim_id,
            claim.outbox_id,
            claim.attempt,
            claim.worker_id,
            result_digest,
            payload,
            _time_text(now),
        ),
    )


def _candidate_row_facts(
    catalog: KnowledgeCatalog,
    *,
    candidate_id: str,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    fts_rows = catalog.connection.execute(
        "SELECT card_version_id, card_content_digest, policy_id, "
        "model_manifest_digest FROM embedding_index_fts_rows "
        "WHERE candidate_id = ? ORDER BY card_version_id",
        (candidate_id,),
    ).fetchall()
    fts_facts = tuple(
        {
            "card_content_digest": str(row[1]),
            "card_version_id": str(row[0]),
            "model_manifest_digest": None if row[3] is None else str(row[3]),
            "policy_id": str(row[2]),
        }
        for row in fts_rows
    )
    semantic_rows = catalog.connection.execute(
        "SELECT card_version_id, card_content_digest, policy_id, "
        "model_manifest_digest, vector_dimension, vector_digest, vector_json "
        "FROM card_embedding_rows WHERE candidate_id = ? ORDER BY card_version_id",
        (candidate_id,),
    ).fetchall()
    semantic_facts: list[dict[str, object]] = []
    for row in semantic_rows:
        try:
            raw_vector = json.loads(str(row[6]))
            vector = validate_embedding_vector(raw_vector, dimension=int(row[4]))
        except Exception as error:
            raise IndexSnapshotIntegrityError("stored semantic vector is invalid") from error
        vector_json = _canonical(vector)
        if vector_json != row[6] or _digest(vector_json) != row[5]:
            raise IndexSnapshotIntegrityError("stored semantic vector digest is invalid")
        semantic_facts.append(
            {
                "card_content_digest": str(row[1]),
                "card_version_id": str(row[0]),
                "model_manifest_digest": str(row[3]),
                "policy_id": str(row[2]),
                "vector_digest": str(row[5]),
                "vector_dimension": int(row[4]),
            }
        )
    return fts_facts, tuple(semantic_facts)


def _embed_document_batches(
    provider: object,
    documents: tuple[str, ...],
) -> tuple[object, ...]:
    """Call the pinned provider in bounded batches without changing order."""

    embed_documents = getattr(provider, "embed_documents", None)
    if not callable(embed_documents):
        raise IndexSnapshotIntegrityError("embedding provider cannot embed documents")
    vectors: list[object] = []
    for start in range(0, len(documents), 1_000):
        batch = documents[start : start + 1_000]
        try:
            produced = tuple(embed_documents(batch))
        except Exception as error:
            raise IndexSnapshotIntegrityError("embedding provider inference failed") from error
        if len(produced) != len(batch):
            raise IndexSnapshotIntegrityError("embedding provider output count is invalid")
        vectors.extend(produced)
    return tuple(vectors)


def _insert_candidate_rows(
    catalog: KnowledgeCatalog,
    *,
    candidate_id: str,
    state: dict[str, object],
    provider: object | None,
    provider_identity: IndexProviderIdentity | None,
    created_at: datetime,
) -> tuple[str, str | None]:
    eligible_cards = tuple(state["eligible_cards"])  # type: ignore[arg-type]
    documents = tuple(state["documents"])  # type: ignore[arg-type]
    if tuple(item["card_version_id"] for item in eligible_cards) != tuple(
        item[0] for item in documents
    ):
        raise IndexSnapshotIntegrityError("candidate document order is invalid")

    model_digest = (
        None if provider_identity is None else provider_identity.model_manifest_digest
    )
    for item in eligible_cards:
        values = (
            candidate_id,
            item["card_version_id"],
            item["card_content_digest"],
            RRF_POLICY_ID,
            model_digest,
            _time_text(created_at),
        )
        existing = catalog.connection.execute(
            "SELECT card_content_digest, policy_id, model_manifest_digest "
            "FROM embedding_index_fts_rows "
            "WHERE candidate_id = ? AND card_version_id = ?",
            (candidate_id, item["card_version_id"]),
        ).fetchone()
        if existing is None:
            catalog.connection.execute(
                "INSERT INTO embedding_index_fts_rows("
                "candidate_id, card_version_id, card_content_digest, policy_id, "
                "model_manifest_digest, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )
        elif tuple(existing) != (
            item["card_content_digest"],
            RRF_POLICY_ID,
            model_digest,
        ):
            raise IndexSnapshotIntegrityError("stored FTS candidate row is invalid")

    if provider_identity is not None:
        if provider is None or provider_identity.encoding_policy != QUERY_ENCODING_POLICY:
            raise IndexSnapshotIntegrityError("embedding encoding policy is invalid")
        raw_vectors = _embed_document_batches(
            provider,
            tuple(str(item[1]) for item in documents),
        )
        if len(raw_vectors) != len(eligible_cards):
            raise IndexSnapshotIntegrityError("embedding provider output count is invalid")
        for item, raw_vector in zip(eligible_cards, raw_vectors, strict=True):
            try:
                vector = validate_embedding_vector(raw_vector, dimension=512)
            except Exception as error:
                raise IndexSnapshotIntegrityError("embedding provider vector is invalid") from error
            vector_json = _canonical(vector)
            vector_digest = _digest(vector_json)
            existing = catalog.connection.execute(
                "SELECT card_content_digest, policy_id, model_manifest_digest, "
                "vector_dimension, vector_digest, vector_json "
                "FROM card_embedding_rows "
                "WHERE candidate_id = ? AND card_version_id = ?",
                (candidate_id, item["card_version_id"]),
            ).fetchone()
            expected = (
                item["card_content_digest"],
                RRF_POLICY_ID,
                provider_identity.model_manifest_digest,
                512,
                vector_digest,
                vector_json,
            )
            if existing is None:
                catalog.connection.execute(
                    "INSERT INTO card_embedding_rows("
                    "candidate_id, card_version_id, card_content_digest, policy_id, "
                    "model_manifest_digest, vector_dimension, vector_digest, "
                    "vector_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, 512, ?, ?, ?)",
                    (
                        candidate_id,
                        item["card_version_id"],
                        item["card_content_digest"],
                        RRF_POLICY_ID,
                        provider_identity.model_manifest_digest,
                        vector_digest,
                        vector_json,
                        _time_text(created_at),
                    ),
                )
            elif tuple(existing) != expected:
                raise IndexSnapshotIntegrityError("stored semantic candidate row is invalid")

    fts_facts, semantic_facts = _candidate_row_facts(
        catalog, candidate_id=candidate_id
    )
    expected_fts = tuple(
        {
            "card_content_digest": item["card_content_digest"],
            "card_version_id": item["card_version_id"],
            "model_manifest_digest": model_digest,
            "policy_id": RRF_POLICY_ID,
        }
        for item in eligible_cards
    )
    if fts_facts != expected_fts:
        raise IndexSnapshotIntegrityError("FTS candidate row set is incomplete")
    if provider_identity is None:
        if semantic_facts:
            raise IndexSnapshotIntegrityError("degraded candidate contains semantic rows")
        return _digest(fts_facts), None
    expected_semantic_keys = tuple(
        (item["card_version_id"], item["card_content_digest"])
        for item in eligible_cards
    )
    actual_semantic_keys = tuple(
        (item["card_version_id"], item["card_content_digest"])
        for item in semantic_facts
    )
    if actual_semantic_keys != expected_semantic_keys or any(
        item["policy_id"] != RRF_POLICY_ID
        or item["model_manifest_digest"] != provider_identity.model_manifest_digest
        or item["vector_dimension"] != 512
        for item in semantic_facts
    ):
        raise IndexSnapshotIntegrityError("semantic candidate row set is incomplete")
    return _digest(fts_facts), _digest(semantic_facts)


def _snapshot_core(
    *,
    candidate_id: str,
    candidate_digest: str,
    state: dict[str, object],
    provider: IndexProviderIdentity | None,
    fts_rows_digest: str,
    semantic_rows_digest: str | None,
) -> dict[str, object]:
    return {
        "candidate_digest": candidate_digest,
        "candidate_id": candidate_id,
        "document_encoding_policy": DOCUMENT_ENCODING_POLICY,
        "eligible_set_digest": state["eligible_set_digest"],
        "fts_rows_digest": fts_rows_digest,
        "lifecycle_digest": state["lifecycle_digest"],
        "model_manifest_digest": None if provider is None else provider.model_manifest_digest,
        "outbox_digest": state["outbox_digest"],
        "outbox_watermark": state["outbox_watermark"],
        "policy_id": RRF_POLICY_ID,
        "provider_identity": None if provider is None else provider.model_dump(mode="json"),
        "query_encoding_policy": QUERY_ENCODING_POLICY,
        "retrieval_mode": "fts-degraded" if provider is None else "hybrid",
        "semantic_rows_digest": semantic_rows_digest,
        "status": "degraded" if provider is None else "ready",
    }


def _seal_snapshot(
    catalog: KnowledgeCatalog,
    *,
    candidate_id: str,
    candidate_digest: str,
    state: dict[str, object],
    provider: IndexProviderIdentity | None,
    fts_rows_digest: str,
    semantic_rows_digest: str | None,
    created_at: datetime,
) -> IndexSnapshot:
    existing = catalog.connection.execute(
        "SELECT index_snapshot_id FROM embedding_index_snapshots WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing is not None:
        reopened = reopen_index_snapshot(catalog, str(existing[0]))
        expected_core = _snapshot_core(
            candidate_id=candidate_id,
            candidate_digest=candidate_digest,
            state=state,
            provider=provider,
            fts_rows_digest=fts_rows_digest,
            semantic_rows_digest=semantic_rows_digest,
        )
        if reopened.snapshot_digest != _digest(expected_core):
            raise IndexSnapshotIntegrityError("existing snapshot does not match candidate")
        return reopened

    core = _snapshot_core(
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
        state=state,
        provider=provider,
        fts_rows_digest=fts_rows_digest,
        semantic_rows_digest=semantic_rows_digest,
    )
    snapshot_digest = _digest(core)
    snapshot = IndexSnapshot(
        index_snapshot_id=f"index-snapshot-{snapshot_digest[:48]}",
        candidate_id=candidate_id,
        status="degraded" if provider is None else "ready",
        retrieval_mode="fts-degraded" if provider is None else "hybrid",
        policy_id=RRF_POLICY_ID,
        model_manifest_digest=None if provider is None else provider.model_manifest_digest,
        eligible_set_digest=str(state["eligible_set_digest"]),
        lifecycle_digest=str(state["lifecycle_digest"]),
        outbox_digest=str(state["outbox_digest"]),
        outbox_watermark=int(state["outbox_watermark"]),
        candidate_digest=candidate_digest,
        fts_rows_digest=fts_rows_digest,
        semantic_rows_digest=semantic_rows_digest,
        provider_identity=provider,
        document_encoding_policy=DOCUMENT_ENCODING_POLICY,
        query_encoding_policy=QUERY_ENCODING_POLICY,
        snapshot_digest=snapshot_digest,
        created_at=created_at,
    )
    payload_json = canonical_model_json(snapshot)
    catalog.connection.execute(
        "INSERT INTO embedding_index_snapshots("
        "index_snapshot_id, candidate_id, status, retrieval_mode, policy_id, "
        "model_manifest_digest, eligible_set_digest, lifecycle_digest, outbox_digest, "
        "outbox_watermark, candidate_digest, snapshot_digest, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.index_snapshot_id,
            snapshot.candidate_id,
            snapshot.status,
            snapshot.retrieval_mode,
            snapshot.policy_id,
            snapshot.model_manifest_digest,
            snapshot.eligible_set_digest,
            snapshot.lifecycle_digest,
            snapshot.outbox_digest,
            snapshot.outbox_watermark,
            snapshot.candidate_digest,
            snapshot.snapshot_digest,
            payload_json,
            _time_text(snapshot.created_at),
        ),
    )
    return reopen_index_snapshot(catalog, snapshot.index_snapshot_id)


def _append_success_result(
    catalog: KnowledgeCatalog,
    *,
    claim: IndexOutboxClaim,
    snapshot: IndexSnapshot,
    created_at: datetime,
) -> None:
    core = {
        "attempt": claim.attempt,
        "claim_id": claim.claim_id,
        "created_at": _time_text(created_at),
        "index_snapshot_id": snapshot.index_snapshot_id,
        "outbox_id": claim.outbox_id,
        "status": "succeeded",
        "worker_id": claim.worker_id,
    }
    result_digest = _digest(core)
    result_id = f"index-result-{result_digest[:48]}"
    payload = _canonical(
        {**core, "result_digest": result_digest, "result_id": result_id}
    )
    catalog.connection.execute(
        "INSERT INTO knowledge_index_outbox_results("
        "result_id, claim_id, outbox_id, attempt, worker_id, status, "
        "index_snapshot_id, result_digest, payload_json, created_at"
        ") VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)",
        (
            result_id,
            claim.claim_id,
            claim.outbox_id,
            claim.attempt,
            claim.worker_id,
            snapshot.index_snapshot_id,
            result_digest,
            payload,
            _time_text(created_at),
        ),
    )
    catalog.connection.execute(
        "INSERT INTO knowledge_index_outbox_consumptions("
        "outbox_id, claim_id, result_id, index_snapshot_id, "
        "outbox_content_digest, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            claim.outbox_id,
            claim.claim_id,
            result_id,
            snapshot.index_snapshot_id,
            claim.outbox_content_digest,
            _time_text(created_at),
        ),
    )


def _validate_historical_candidate(
    catalog: KnowledgeCatalog,
    *,
    candidate_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    row = catalog.connection.execute(
        "SELECT policy_id, model_manifest_digest, eligible_set_digest, "
        "lifecycle_digest, outbox_digest, outbox_watermark, candidate_digest, "
        "payload_json, created_at FROM embedding_index_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise IndexSnapshotIntegrityError("index candidate is unavailable")
    try:
        payload = json.loads(str(row[7]))
        core = payload["core"]
    except (TypeError, ValueError, KeyError) as error:
        raise IndexSnapshotIntegrityError("index candidate payload is invalid") from error
    candidate_digest = _digest(core)
    expected_envelope = (
        core.get("policy_id"),
        core.get("model_manifest_digest"),
        core.get("eligible_set_digest"),
        core.get("lifecycle_digest"),
        core.get("outbox_digest"),
        core.get("outbox_watermark"),
        candidate_digest,
    )
    if (
        payload.get("candidate_id") != candidate_id
        or payload.get("candidate_digest") != candidate_digest
        or payload.get("created_at") != row[8]
        or _canonical(payload) != row[7]
        or tuple(row[:7]) != expected_envelope
        or _digest(core.get("eligible_cards")) != core.get("eligible_set_digest")
        or _digest(core.get("lifecycle_facts")) != core.get("lifecycle_digest")
        or _digest(core.get("outbox_facts")) != core.get("outbox_digest")
    ):
        raise IndexSnapshotIntegrityError("index candidate envelope is invalid")

    eligible_cards = core.get("eligible_cards")
    lifecycle_facts = core.get("lifecycle_facts")
    outbox_facts = core.get("outbox_facts")
    if not isinstance(eligible_cards, list) or not isinstance(lifecycle_facts, list):
        raise IndexSnapshotIntegrityError("index candidate card facts are invalid")
    if not isinstance(outbox_facts, list):
        raise IndexSnapshotIntegrityError("index candidate outbox facts are invalid")
    if core.get("outbox_watermark") != (
        0 if not outbox_facts else outbox_facts[-1].get("rowid")
    ):
        raise IndexSnapshotIntegrityError("index candidate watermark is invalid")

    for card_fact in eligible_cards:
        card_row = catalog.connection.execute(
            "SELECT content_digest, payload_json FROM cards WHERE version_id = ?",
            (card_fact.get("card_version_id"),),
        ).fetchone()
        if card_row is None or card_row[0] != card_fact.get("card_content_digest"):
            raise IndexSnapshotIntegrityError("snapshot card content is unavailable")
        try:
            card = KnowledgeCardVersion.model_validate_json(str(card_row[1]), strict=False)
        except Exception as error:
            raise IndexSnapshotIntegrityError("snapshot card payload is invalid") from error
        if (
            card.version_id != card_fact.get("card_version_id")
            or card.content_digest != card_fact.get("card_content_digest")
            or canonical_model_json(card) != card_row[1]
        ):
            raise IndexSnapshotIntegrityError("snapshot card envelope is invalid")
        projection = _verified_card_projection(catalog, card)
        if _digest(projection[-1]) != card_fact.get("document_digest"):
            raise IndexSnapshotIntegrityError("snapshot card document digest is invalid")

    for lifecycle in lifecycle_facts:
        event = catalog.connection.execute(
            "SELECT card_version_id, sequence, status_after, suspended_after "
            "FROM card_lifecycle_events WHERE event_id = ?",
            (lifecycle.get("last_event_id"),),
        ).fetchone()
        if event is None or tuple(event) != (
            lifecycle.get("card_version_id"),
            lifecycle.get("last_sequence"),
            lifecycle.get("status"),
            lifecycle.get("suspended"),
        ):
            raise IndexSnapshotIntegrityError("snapshot lifecycle fact is invalid")

    for outbox in outbox_facts:
        stored = catalog.connection.execute(
            "SELECT outbox.outbox_id, outbox.operation_id, outbox.request_digest, "
            "outbox.card_version_id, outbox.action, "
            "outbox.content_digest, outbox.payload_json, operation.request_digest, "
            "operation.status FROM knowledge_index_outbox AS outbox "
            "JOIN operation_outcomes AS operation "
            "ON operation.operation_id = outbox.operation_id WHERE outbox.rowid = ?",
            (outbox.get("rowid"),),
        ).fetchone()
        if stored is None:
            raise IndexSnapshotIntegrityError("snapshot outbox fact is unavailable")
        expected_payload = {
            "action": str(stored[4]),
            "card_version_id": str(stored[3]),
            "operation_id": str(stored[1]),
            "outbox_id": str(stored[0]),
            "request_digest": str(stored[2]),
        }
        if (
            outbox
            != {
                "action": str(stored[4]),
                "card_version_id": str(stored[3]),
                "content_digest": str(stored[5]),
                "outbox_id": str(stored[0]),
                "rowid": outbox.get("rowid"),
            }
            or _canonical(expected_payload) != stored[6]
            or _digest(str(stored[6])) != stored[5]
            or stored[7] != stored[2]
            or stored[8] != "committed"
        ):
            raise IndexSnapshotIntegrityError("snapshot outbox fact is invalid")
    return payload, core


def reopen_index_snapshot(
    catalog: KnowledgeCatalog,
    index_snapshot_id: str,
) -> IndexSnapshot:
    """Rebuild every candidate/row/seal digest before returning a snapshot."""

    row = catalog.connection.execute(
        "SELECT candidate_id, status, retrieval_mode, policy_id, "
        "model_manifest_digest, eligible_set_digest, lifecycle_digest, outbox_digest, "
        "outbox_watermark, candidate_digest, snapshot_digest, payload_json, created_at "
        "FROM embedding_index_snapshots WHERE index_snapshot_id = ?",
        (index_snapshot_id,),
    ).fetchone()
    if row is None:
        raise IndexSnapshotIntegrityError("index snapshot is unavailable")
    try:
        snapshot = IndexSnapshot.model_validate_json(str(row[11]), strict=False)
    except Exception as error:
        raise IndexSnapshotIntegrityError("index snapshot payload is invalid") from error
    if (
        snapshot.index_snapshot_id != index_snapshot_id
        or canonical_model_json(snapshot) != row[11]
        or (
            snapshot.candidate_id,
            snapshot.status,
            snapshot.retrieval_mode,
            snapshot.policy_id,
            snapshot.model_manifest_digest,
            snapshot.eligible_set_digest,
            snapshot.lifecycle_digest,
            snapshot.outbox_digest,
            snapshot.outbox_watermark,
            snapshot.candidate_digest,
            snapshot.snapshot_digest,
            _time_text(snapshot.created_at),
        )
        != (*tuple(row[:11]), str(row[12]))
    ):
        raise IndexSnapshotIntegrityError("index snapshot envelope is invalid")

    _payload, candidate_core = _validate_historical_candidate(
        catalog, candidate_id=snapshot.candidate_id
    )
    expected_provider = (
        None
        if snapshot.provider_identity is None
        else snapshot.provider_identity.model_dump(mode="json")
    )
    if (
        snapshot.candidate_digest != _digest(candidate_core)
        or candidate_core.get("policy_id") != snapshot.policy_id
        or candidate_core.get("model_manifest_digest")
        != snapshot.model_manifest_digest
        or candidate_core.get("provider_identity") != expected_provider
        or candidate_core.get("document_encoding_policy")
        != snapshot.document_encoding_policy
        or candidate_core.get("query_encoding_policy")
        != snapshot.query_encoding_policy
    ):
        raise IndexSnapshotIntegrityError("snapshot candidate digest is invalid")
    fts_facts, semantic_facts = _candidate_row_facts(
        catalog, candidate_id=snapshot.candidate_id
    )
    eligible_cards = candidate_core["eligible_cards"]
    expected_fts = tuple(
        {
            "card_content_digest": item["card_content_digest"],
            "card_version_id": item["card_version_id"],
            "model_manifest_digest": snapshot.model_manifest_digest,
            "policy_id": RRF_POLICY_ID,
        }
        for item in eligible_cards
    )
    if fts_facts != expected_fts or _digest(fts_facts) != snapshot.fts_rows_digest:
        raise IndexSnapshotIntegrityError("snapshot FTS rows are invalid")
    if snapshot.retrieval_mode == "fts-degraded":
        if semantic_facts or snapshot.semantic_rows_digest is not None:
            raise IndexSnapshotIntegrityError("degraded snapshot contains semantic rows")
        provider = None
    else:
        if snapshot.provider_identity is None:
            raise IndexSnapshotIntegrityError("hybrid snapshot provider is unavailable")
        if (
            len(semantic_facts) != len(expected_fts)
            or tuple(item["card_version_id"] for item in semantic_facts)
            != tuple(item["card_version_id"] for item in expected_fts)
            or any(
                item["card_content_digest"] != expected["card_content_digest"]
                or item["policy_id"] != RRF_POLICY_ID
                or item["model_manifest_digest"] != snapshot.model_manifest_digest
                or item["vector_dimension"] != 512
                for item, expected in zip(semantic_facts, expected_fts, strict=True)
            )
            or _digest(semantic_facts) != snapshot.semantic_rows_digest
        ):
            raise IndexSnapshotIntegrityError("snapshot semantic rows are invalid")
        provider = snapshot.provider_identity

    state = {
        "eligible_set_digest": candidate_core["eligible_set_digest"],
        "lifecycle_digest": candidate_core["lifecycle_digest"],
        "outbox_digest": candidate_core["outbox_digest"],
        "outbox_watermark": candidate_core["outbox_watermark"],
    }
    rebuilt_core = _snapshot_core(
        candidate_id=snapshot.candidate_id,
        candidate_digest=snapshot.candidate_digest,
        state=state,
        provider=provider,
        fts_rows_digest=snapshot.fts_rows_digest,
        semantic_rows_digest=snapshot.semantic_rows_digest,
    )
    if _digest(rebuilt_core) != snapshot.snapshot_digest:
        raise IndexSnapshotIntegrityError("index snapshot digest is invalid")
    return snapshot


def _reopen_succeeded_claim(
    catalog: KnowledgeCatalog,
    *,
    claim: IndexOutboxClaim,
    result_row: tuple[object, ...],
) -> IndexSnapshot:
    (
        result_id,
        stored_claim_id,
        stored_outbox_id,
        stored_attempt,
        stored_worker_id,
        status,
        snapshot_id,
        result_digest,
        payload_json,
        created_at,
    ) = result_row
    if status != "succeeded" or snapshot_id is None:
        raise IndexLeaseConflict("index claim is already resolved without success")
    if (
        stored_claim_id,
        stored_outbox_id,
        stored_attempt,
        stored_worker_id,
    ) != (
        claim.claim_id,
        claim.outbox_id,
        claim.attempt,
        claim.worker_id,
    ):
        raise IndexSnapshotIntegrityError("index result owner envelope is invalid")
    try:
        payload = json.loads(str(payload_json))
    except (TypeError, ValueError) as error:
        raise IndexSnapshotIntegrityError("index result payload is invalid") from error
    core = {
        "attempt": claim.attempt,
        "claim_id": claim.claim_id,
        "created_at": str(created_at),
        "index_snapshot_id": str(snapshot_id),
        "outbox_id": claim.outbox_id,
        "status": "succeeded",
        "worker_id": claim.worker_id,
    }
    if (
        payload
        != {
            **core,
            "result_digest": str(result_digest),
            "result_id": str(result_id),
        }
        or _canonical(payload) != payload_json
        or _digest(core) != result_digest
    ):
        raise IndexSnapshotIntegrityError("index result envelope is invalid")
    consumption = catalog.connection.execute(
        "SELECT claim_id, result_id, index_snapshot_id, outbox_content_digest "
        "FROM knowledge_index_outbox_consumptions WHERE outbox_id = ?",
        (claim.outbox_id,),
    ).fetchone()
    if consumption != (
        claim.claim_id,
        result_id,
        snapshot_id,
        claim.outbox_content_digest,
    ):
        raise IndexSnapshotIntegrityError("index consumption envelope is invalid")
    return reopen_index_snapshot(catalog, str(snapshot_id))


def claim_next_index_outbox(
    catalog: KnowledgeCatalog,
    *,
    worker_id: str,
    now: datetime,
    lease_seconds: int = 60,
) -> IndexOutboxClaim | None:
    """Append one exclusive lease, reclaiming expired attempts without mutation."""

    if not isinstance(catalog, KnowledgeCatalog):
        raise TypeError("catalog must be a KnowledgeCatalog")
    if not isinstance(worker_id, str) or _SAFE_ID.fullmatch(worker_id) is None:
        raise ValueError("worker_id must be a safe opaque ID")
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be from 1 to 3600")
    current = _aware_utc(now, label="claim time")
    with catalog.atomic_write():
        unresolved = catalog.connection.execute(
            "SELECT claim_id FROM knowledge_index_outbox_claims AS claim "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM knowledge_index_outbox_results AS result "
            "WHERE result.claim_id = claim.claim_id"
            ") ORDER BY claim.outbox_id, claim.attempt"
        ).fetchall()
        for (claim_id,) in unresolved:
            claim = _load_claim(catalog, str(claim_id))
            if claim.lease_expires_at <= current:
                _append_expired_result(catalog, claim, now=current)

        rows = catalog.connection.execute(
            "SELECT outbox.outbox_id, outbox.content_digest "
            "FROM knowledge_index_outbox AS outbox "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM knowledge_index_outbox_consumptions AS consumed "
            "WHERE consumed.outbox_id = outbox.outbox_id"
            ") AND NOT EXISTS ("
            "SELECT 1 FROM knowledge_index_outbox_claims AS claim "
            "WHERE claim.outbox_id = outbox.outbox_id "
            "AND NOT EXISTS ("
            "SELECT 1 FROM knowledge_index_outbox_results AS result "
            "WHERE result.claim_id = claim.claim_id"
            ")"
            ") ORDER BY outbox.rowid LIMIT 1"
        ).fetchone()
        if rows is None:
            return None
        outbox_id, outbox_content_digest = str(rows[0]), str(rows[1])
        attempt = int(
            catalog.connection.execute(
                "SELECT coalesce(max(attempt), 0) + 1 "
                "FROM knowledge_index_outbox_claims WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()[0]
        )
        lease_expires_at = current + timedelta(seconds=lease_seconds)
        core = _claim_core(
            outbox_id=outbox_id,
            attempt=attempt,
            worker_id=worker_id,
            outbox_content_digest=outbox_content_digest,
            lease_expires_at=lease_expires_at,
            created_at=current,
        )
        claim_digest = _digest(core)
        claim = IndexOutboxClaim(
            claim_id=f"index-claim-{claim_digest[:48]}",
            outbox_id=outbox_id,
            attempt=attempt,
            worker_id=worker_id,
            outbox_content_digest=outbox_content_digest,
            lease_expires_at=lease_expires_at,
            claim_digest=claim_digest,
            created_at=current,
        )
        catalog.connection.execute(
            "INSERT INTO knowledge_index_outbox_claims("
            "claim_id, outbox_id, attempt, worker_id, outbox_content_digest, "
            "lease_expires_at, claim_digest, payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                claim.claim_id,
                claim.outbox_id,
                claim.attempt,
                claim.worker_id,
                claim.outbox_content_digest,
                _time_text(claim.lease_expires_at),
                claim.claim_digest,
                _claim_payload(claim),
                _time_text(claim.created_at),
            ),
        )
        return claim


def complete_index_claim(
    catalog: KnowledgeCatalog,
    *,
    claim_id: str,
    worker_id: str,
    embedding_provider: object | None,
    now: datetime,
) -> IndexSnapshot:
    """Build, revalidate, seal, and consume one claim in one atomic write."""

    current = _aware_utc(now, label="completion time")
    if not isinstance(worker_id, str) or _SAFE_ID.fullmatch(worker_id) is None:
        raise ValueError("worker_id must be a safe opaque ID")
    with catalog.atomic_write():
        claim = _load_claim(catalog, claim_id)
        if claim.worker_id != worker_id:
            raise IndexLeaseConflict("index claim owner does not match")
        result = catalog.connection.execute(
            "SELECT result_id, claim_id, outbox_id, attempt, worker_id, status, "
            "index_snapshot_id, result_digest, payload_json, created_at "
            "FROM knowledge_index_outbox_results "
            "WHERE claim_id = ?",
            (claim.claim_id,),
        ).fetchone()
        if result is not None:
            return _reopen_succeeded_claim(
                catalog,
                claim=claim,
                result_row=tuple(result),
            )
        latest = catalog.connection.execute(
            "SELECT max(attempt) FROM knowledge_index_outbox_claims WHERE outbox_id = ?",
            (claim.outbox_id,),
        ).fetchone()
        if latest is None or int(latest[0]) != claim.attempt:
            raise IndexLeaseConflict("index claim lease was superseded")
        if claim.lease_expires_at <= current:
            raise IndexLeaseConflict("index claim lease has expired")
        outbox = catalog.connection.execute(
            "SELECT content_digest FROM knowledge_index_outbox WHERE outbox_id = ?",
            (claim.outbox_id,),
        ).fetchone()
        if outbox != (claim.outbox_content_digest,):
            raise IndexSnapshotIntegrityError("claimed outbox digest is invalid")

        failure: IndexSnapshotIntegrityError | None = None
        snapshot: IndexSnapshot | None = None
        try:
            with catalog.atomic_write():
                provider_identity = _provider_record(embedding_provider)
                state = _read_index_state(catalog)
                claimed_facts = tuple(
                    item
                    for item in state["outbox_facts"]  # type: ignore[union-attr]
                    if item["outbox_id"] == claim.outbox_id
                )
                if (
                    len(claimed_facts) != 1
                    or claimed_facts[0]["content_digest"]
                    != claim.outbox_content_digest
                ):
                    raise IndexSnapshotIntegrityError(
                        "claimed outbox is outside the watermark"
                    )
                candidate_id, candidate_digest, _candidate_payload = (
                    _insert_or_validate_candidate(
                        catalog,
                        state=state,
                        provider=provider_identity,
                        created_at=current,
                    )
                )
                fts_rows_digest, semantic_rows_digest = _insert_candidate_rows(
                    catalog,
                    candidate_id=candidate_id,
                    state=state,
                    provider=embedding_provider,
                    provider_identity=provider_identity,
                    created_at=current,
                )

                revalidated = _read_index_state(catalog)
                for key in (
                    "documents",
                    "eligible_cards",
                    "eligible_set_digest",
                    "lifecycle_digest",
                    "lifecycle_facts",
                    "outbox_digest",
                    "outbox_facts",
                    "outbox_watermark",
                ):
                    if revalidated[key] != state[key]:
                        raise IndexSnapshotIntegrityError(
                            "index candidate changed before snapshot seal"
                        )
                snapshot = _seal_snapshot(
                    catalog,
                    candidate_id=candidate_id,
                    candidate_digest=candidate_digest,
                    state=state,
                    provider=provider_identity,
                    fts_rows_digest=fts_rows_digest,
                    semantic_rows_digest=semantic_rows_digest,
                    created_at=current,
                )
                _append_success_result(
                    catalog,
                    claim=claim,
                    snapshot=snapshot,
                    created_at=current,
                )
        except IndexSnapshotIntegrityError as error:
            failure = error
            _append_failed_result(catalog, claim, now=current)
        except sqlite3.Error:
            failure = IndexSnapshotIntegrityError(
                "index snapshot storage rejected the candidate"
            )
            _append_failed_result(catalog, claim, now=current)
    if failure is not None:
        raise failure
    if snapshot is None:
        raise IndexSnapshotIntegrityError("index completion produced no snapshot")
    return snapshot


__all__ = [
    "IndexLeaseConflict",
    "IndexOutboxClaim",
    "IndexProviderIdentity",
    "IndexSnapshot",
    "IndexSnapshotIntegrityError",
    "claim_next_index_outbox",
    "complete_index_claim",
    "reopen_index_snapshot",
]
