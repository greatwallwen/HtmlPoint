from __future__ import annotations

import sqlite3
import hashlib
import json
import socket
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from course_helper.catalog import KnowledgeCatalog
from course_helper.cards import VOCABULARY_VERSION_ID, publish_card, seed_vocabulary
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    TagAssignment,
)
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.embeddings import EmbeddingProviderIdentity
from course_helper.operations import (
    IndexOutboxItem,
    OperationMutationResult,
    OperationRequest,
    run_operation,
)


NOW = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="index-tests")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_published_card(
    catalog: KnowledgeCatalog,
    *,
    version_id: str = "card-index-a",
    title: str = "RFM analysis",
    tags: tuple[str, ...] = (
        "topic:data-analysis",
        "audience:analyst",
        "difficulty:beginner",
        "tool:spreadsheet",
    ),
) -> KnowledgeCardVersion:
    seed_vocabulary(catalog)
    source_id = f"source-{version_id}"
    chunk_id = f"chunk-{version_id}"
    source = SourceAssetVersion(
        logical_id=f"logical-{source_id}",
        version_id=source_id,
        revision=1,
        content_digest=_digest(source_id),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path=f"{version_id}.md"),
        display_name=f"{version_id}.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=20,
        extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id=chunk_id,
        source_version_id=source_id,
        ordinal=0,
        modality="text",
        language="en",
        normalized_text=f"Evidence for {title}",
        content_digest=_digest(f"chunk:{version_id}"),
        locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
    )
    catalog.insert_source(source)
    catalog.insert_chunk(chunk)
    candidate = KnowledgeCardVersion(
        logical_id=f"logical-{version_id}",
        version_id=version_id,
        revision=1,
        content_digest=_digest(f"card:{version_id}"),
        created_at=NOW,
        created_by=ACTOR,
        main_type_id="concept",
        title=title,
        learning_objective=f"Understand {title}",
        content_ast=(CardContentNode(type="paragraph", text=f"Body for {title}"),),
        suggested_minutes=5,
        vocabulary_version_id=VOCABULARY_VERSION_ID,
        tag_assignments=tuple(
            TagAssignment(
                vocabulary_version_id=VOCABULARY_VERSION_ID,
                dimension_id=tag.split(":", 1)[0],
                tag_id=tag,
            )
            for tag in tags
        ),
        chunk_citations=(
            ChunkCitation(chunk_id=chunk_id, source_version_id=source_id),
        ),
        status="review",
    )
    return publish_card(candidate, catalog)


def _enqueue(catalog: KnowledgeCatalog, card: KnowledgeCardVersion, *, suffix: str) -> str:
    outbox_id = f"outbox-{suffix}"
    request = OperationRequest(
        operation_id=f"operation-{suffix}",
        request_digest=_digest(f"request:{suffix}"),
        actor=ACTOR,
        session_id="index-session",
    )

    def mutation() -> OperationMutationResult:
        return OperationMutationResult(
            result_refs={"card_version_id": card.version_id},
            item_outcomes=(),
            index_outbox=(
                IndexOutboxItem(
                    outbox_id=outbox_id,
                    card_version_id=card.version_id,
                    action="upsert",
                ),
            ),
        )

    run_operation(catalog, request, mutation, clock=lambda: NOW)
    return outbox_id


class _FakeEmbeddingProvider:
    identity = EmbeddingProviderIdentity(
        provider="fastembed",
        provider_version="0.8.0",
        model_id="BAAI/bge-small-zh-v1.5",
        model_revision="7999e1d3359715c523056ef9478215996d62a620",
        artifact_repository="Qdrant/bge-small-zh-v1.5",
        artifact_revision="46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
        dimension=512,
        encoding_policy="utf8-nfkc-no-prefix",
        model_manifest_digest="1" * 64,
        cache_digest="2" * 64,
        model_files=(
            ("config.json", "3" * 64, 739),
            (
                "model_optimized.onnx",
                "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
                94781076,
            ),
            ("special_tokens_map.json", "4" * 64, 125),
            ("tokenizer.json", "8" * 64, 439125),
            ("tokenizer_config.json", "9" * 64, 367),
        ),
        runtime_digest="5" * 64,
        wheel_set_digest="6" * 64,
        generation_digest="7" * 64,
    )

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        first = 1.0 if "RFM" in text else 0.0
        second = 0.0 if first else 1.0
        return (first, second, *tuple(0.0 for _ in range(510)))

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)


class _FailingEmbeddingProvider(_FakeEmbeddingProvider):
    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        raise RuntimeError("provider internals must not leak")


class _WrongEmbeddingProvider(_FakeEmbeddingProvider):
    identity = replace(
        _FakeEmbeddingProvider.identity,
        model_id="unverified/wrong-model",
    )


class _WrongInventoryEmbeddingProvider(_FakeEmbeddingProvider):
    identity = replace(
        _FakeEmbeddingProvider.identity,
        model_files=(
            ("config-wrong.json", "3" * 64, 740),
            *_FakeEmbeddingProvider.identity.model_files[1:],
        ),
    )


def test_migration_four_has_append_only_claim_candidate_and_seal_boundaries(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "index.db") as catalog:
        tables = {
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        triggers = {
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }

    assert {
        "embedding_index_candidates",
        "embedding_index_fts_rows",
        "embedding_index_snapshots",
        "card_embedding_rows",
        "knowledge_index_outbox_claims",
        "knowledge_index_outbox_results",
        "knowledge_index_outbox_consumptions",
    }.issubset(tables)
    assert {
        "embedding_index_candidates_immutable_update",
        "embedding_index_snapshots_immutable_update",
        "card_embedding_rows_reject_sealed_candidate",
        "embedding_index_fts_rows_reject_sealed_candidate",
        "knowledge_index_outbox_claims_immutable_update",
        "knowledge_index_outbox_results_immutable_update",
    }.issubset(triggers)


def test_embedding_snapshot_rows_cannot_be_added_after_the_seal(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "sealed.db") as catalog:
        connection = catalog.connection
        candidate_payload = json.dumps(
            {
                "candidate_digest": "d" * 64,
                "candidate_id": "candidate-a",
                "core": {
                    "eligible_cards": [],
                    "eligible_set_digest": "a" * 64,
                    "lifecycle_digest": "b" * 64,
                    "model_manifest_digest": None,
                    "outbox_digest": "c" * 64,
                    "outbox_watermark": 0,
                    "policy_id": "course-studio-rrf-v1",
                },
                "created_at": "2026-07-17T00:00:00+00:00",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO embedding_index_candidates("
            "candidate_id, policy_id, model_manifest_digest, eligible_set_digest, "
            "lifecycle_digest, outbox_digest, outbox_watermark, candidate_digest, "
            "payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "candidate-a",
                "course-studio-rrf-v1",
                None,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                0,
                "d" * 64,
                candidate_payload,
                "2026-07-17T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO embedding_index_snapshots("
            "index_snapshot_id, candidate_id, status, retrieval_mode, policy_id, "
            "model_manifest_digest, eligible_set_digest, lifecycle_digest, outbox_digest, "
            "outbox_watermark, candidate_digest, snapshot_digest, payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "snapshot-a",
                "candidate-a",
                "degraded",
                "fts-degraded",
                "course-studio-rrf-v1",
                None,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                0,
                "d" * 64,
                "e" * 64,
                "{}",
                "2026-07-17T00:00:00+00:00",
            ),
        )

        with pytest.raises(sqlite3.IntegrityError, match="sealed"):
            connection.execute(
                "INSERT INTO embedding_index_fts_rows("
                "candidate_id, card_version_id, card_content_digest, created_at"
                ") VALUES (?, ?, ?, ?)",
                (
                    "candidate-a",
                    "missing-card",
                    "f" * 64,
                    "2026-07-17T00:00:00+00:00",
                ),
            )


def test_raw_snapshot_seal_rejects_an_incomplete_candidate_row_set(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "raw-empty-seal.db") as catalog:
        card = _seed_published_card(catalog)
        core = {
            "document_encoding_policy": "utf8-nfkc-card-projection-v1",
            "eligible_cards": [
                {
                    "card_content_digest": card.content_digest,
                    "card_version_id": card.version_id,
                    "document_digest": "9" * 64,
                }
            ],
            "eligible_set_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "lifecycle_facts": [],
            "model_manifest_digest": None,
            "outbox_digest": "c" * 64,
            "outbox_facts": [],
            "outbox_watermark": 0,
            "policy_id": "course-studio-rrf-v1",
            "provider_identity": None,
            "query_encoding_policy": "utf8-nfkc-no-prefix",
        }
        candidate_payload = json.dumps(
            {
                "candidate_digest": "d" * 64,
                "candidate_id": "candidate-incomplete",
                "core": core,
                "created_at": "2026-07-17T00:00:00+00:00",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        catalog.connection.execute(
            "INSERT INTO embedding_index_candidates("
            "candidate_id, policy_id, model_manifest_digest, eligible_set_digest, "
            "lifecycle_digest, outbox_digest, outbox_watermark, candidate_digest, "
            "payload_json, created_at"
            ") VALUES (?, ?, NULL, ?, ?, ?, 0, ?, ?, ?)",
            (
                "candidate-incomplete",
                "course-studio-rrf-v1",
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                candidate_payload,
                "2026-07-17T00:00:00+00:00",
            ),
        )

        with pytest.raises(sqlite3.IntegrityError, match="incomplete"):
            catalog.connection.execute(
                "INSERT INTO embedding_index_snapshots("
                "index_snapshot_id, candidate_id, status, retrieval_mode, policy_id, "
                "model_manifest_digest, eligible_set_digest, lifecycle_digest, "
                "outbox_digest, outbox_watermark, candidate_digest, snapshot_digest, "
                "payload_json, created_at"
                ") VALUES (?, ?, 'degraded', 'fts-degraded', ?, NULL, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    "snapshot-incomplete",
                    "candidate-incomplete",
                    "course-studio-rrf-v1",
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "d" * 64,
                    "e" * 64,
                    "{}",
                    "2026-07-17T00:00:00+00:00",
                ),
            )


def test_outbox_claim_lease_is_append_only_reclaimable_and_owner_bound(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        IndexLeaseConflict,
        claim_next_index_outbox,
        complete_index_claim,
    )

    with KnowledgeCatalog.open(tmp_path / "claim.db") as catalog:
        card = _seed_published_card(catalog)
        outbox_id = _enqueue(catalog, card, suffix="claim")

        first = claim_next_index_outbox(
            catalog,
            worker_id="worker-a",
            now=NOW,
            lease_seconds=30,
        )
        assert first is not None
        assert first.outbox_id == outbox_id
        assert first.attempt == 1
        assert (
            claim_next_index_outbox(
                catalog,
                worker_id="worker-b",
                now=NOW + timedelta(seconds=20),
                lease_seconds=30,
            )
            is None
        )

        reclaimed = claim_next_index_outbox(
            catalog,
            worker_id="worker-b",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
        assert reclaimed is not None
        assert reclaimed.outbox_id == outbox_id
        assert reclaimed.attempt == 2
        assert reclaimed.claim_id != first.claim_id
        expired = catalog.connection.execute(
            "SELECT status FROM knowledge_index_outbox_results WHERE claim_id = ?",
            (first.claim_id,),
        ).fetchone()
        assert expired == ("lease-expired",)

        with pytest.raises(IndexLeaseConflict, match="owner|lease|claim"):
            complete_index_claim(
                catalog,
                claim_id=first.claim_id,
                worker_id="worker-a",
                embedding_provider=None,
                now=NOW + timedelta(seconds=32),
            )


def test_missing_model_seals_an_explicit_fts_degraded_snapshot_without_vectors(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        claim_next_index_outbox,
        complete_index_claim,
        reopen_index_snapshot,
    )

    with KnowledgeCatalog.open(tmp_path / "degraded.db") as catalog:
        card = _seed_published_card(catalog)
        outbox_id = _enqueue(catalog, card, suffix="degraded")
        claim = claim_next_index_outbox(
            catalog,
            worker_id="worker-a",
            now=NOW,
            lease_seconds=30,
        )
        assert claim is not None

        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=None,
            now=NOW + timedelta(seconds=1),
        )

        assert snapshot.status == "degraded"
        assert snapshot.retrieval_mode == "fts-degraded"
        assert snapshot.model_manifest_digest is None
        assert len(snapshot.snapshot_digest) == 64
        assert catalog.connection.execute(
            "SELECT count(*) FROM embedding_index_fts_rows WHERE candidate_id = ?",
            (snapshot.candidate_id,),
        ).fetchone() == (1,)
        assert catalog.connection.execute(
            "SELECT count(*) FROM card_embedding_rows WHERE candidate_id = ?",
            (snapshot.candidate_id,),
        ).fetchone() == (0,)
        assert catalog.connection.execute(
            "SELECT outbox_id FROM knowledge_index_outbox_consumptions"
        ).fetchall() == [(outbox_id,)]
        assert reopen_index_snapshot(catalog, snapshot.index_snapshot_id) == snapshot
        assert (
            claim_next_index_outbox(
                catalog,
                worker_id="worker-b",
                now=NOW + timedelta(seconds=2),
            )
            is None
        )


def test_missing_published_card_fts_projection_fails_attempt_without_a_snapshot(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        IndexSnapshotIntegrityError,
        claim_next_index_outbox,
        complete_index_claim,
    )

    with KnowledgeCatalog.open(tmp_path / "missing-fts.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="missing-fts")
        catalog.connection.execute(
            "DELETE FROM card_fts WHERE version_id = ?",
            (card.version_id,),
        )
        catalog.connection.commit()
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None

        with pytest.raises(IndexSnapshotIntegrityError):
            complete_index_claim(
                catalog,
                claim_id=claim.claim_id,
                worker_id="worker-a",
                embedding_provider=None,
                now=NOW + timedelta(seconds=1),
            )

        assert catalog.connection.execute(
            "SELECT count(*) FROM embedding_index_snapshots"
        ).fetchone() == (0,)
        assert catalog.connection.execute(
            "SELECT status FROM knowledge_index_outbox_results WHERE claim_id = ?",
            (claim.claim_id,),
        ).fetchone() == ("failed",)


def test_verified_provider_seals_hybrid_rows_with_one_exact_content_and_model_digest(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        claim_next_index_outbox,
        complete_index_claim,
        reopen_index_snapshot,
    )

    with KnowledgeCatalog.open(tmp_path / "hybrid.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="hybrid")
        claim = claim_next_index_outbox(
            catalog,
            worker_id="worker-a",
            now=NOW,
        )
        assert claim is not None

        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=_FakeEmbeddingProvider(),
            now=NOW + timedelta(seconds=1),
        )

        assert snapshot.status == "ready"
        assert snapshot.retrieval_mode == "hybrid"
        assert snapshot.model_manifest_digest == "1" * 64
        fts = catalog.connection.execute(
            "SELECT card_content_digest, policy_id, model_manifest_digest "
            "FROM embedding_index_fts_rows WHERE candidate_id = ?",
            (snapshot.candidate_id,),
        ).fetchone()
        semantic = catalog.connection.execute(
            "SELECT card_content_digest, policy_id, model_manifest_digest, "
            "vector_dimension, vector_digest FROM card_embedding_rows "
            "WHERE candidate_id = ?",
            (snapshot.candidate_id,),
        ).fetchone()
        assert fts is not None and semantic is not None
        assert fts == semantic[:3]
        assert fts == (
            card.content_digest,
            "course-studio-rrf-v1",
            "1" * 64,
        )
        assert semantic[3] == 512
        assert len(semantic[4]) == 64
        assert reopen_index_snapshot(catalog, snapshot.index_snapshot_id) == snapshot


def test_completion_response_loss_retry_reopens_the_same_success_without_new_rows(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim

    with KnowledgeCatalog.open(tmp_path / "retry.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="retry")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        first = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=None,
            now=NOW + timedelta(seconds=1),
        )

        second = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=_FailingEmbeddingProvider(),
            now=NOW + timedelta(days=1),
        )

        assert second == first
        assert catalog.connection.execute(
            "SELECT count(*) FROM embedding_index_snapshots"
        ).fetchone() == (1,)
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox_results"
        ).fetchone() == (1,)
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox_consumptions"
        ).fetchone() == (1,)


def test_provider_failure_records_a_sanitized_failed_attempt_and_never_degrades(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        IndexSnapshotIntegrityError,
        claim_next_index_outbox,
        complete_index_claim,
    )

    with KnowledgeCatalog.open(tmp_path / "failure.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="failure")
        first = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert first is not None

        with pytest.raises(IndexSnapshotIntegrityError) as caught:
            complete_index_claim(
                catalog,
                claim_id=first.claim_id,
                worker_id="worker-a",
                embedding_provider=_FailingEmbeddingProvider(),
                now=NOW + timedelta(seconds=1),
            )

        assert "provider internals" not in str(caught.value)
        result = catalog.connection.execute(
            "SELECT status, index_snapshot_id, payload_json "
            "FROM knowledge_index_outbox_results WHERE claim_id = ?",
            (first.claim_id,),
        ).fetchone()
        assert result is not None
        assert result[0:2] == ("failed", None)
        assert "INDEX_BUILD_FAILED" in result[2]
        assert "provider internals" not in result[2]
        assert catalog.connection.execute(
            "SELECT count(*) FROM embedding_index_snapshots"
        ).fetchone() == (0,)
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox_consumptions"
        ).fetchone() == (0,)

        retry = claim_next_index_outbox(
            catalog,
            worker_id="worker-b",
            now=NOW + timedelta(seconds=2),
        )
        assert retry is not None
        assert retry.attempt == 2


def test_requested_snapshot_filters_every_lane_before_exact_rrf_and_fusion_limit(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery

    with KnowledgeCatalog.open(tmp_path / "retrieval.db") as catalog:
        primary = _seed_published_card(
            catalog,
            version_id="card-filter-primary",
            title="RFM analyst workflow",
            tags=(
                "topic:data-analysis",
                "audience:analyst",
                "difficulty:beginner",
                "tool:python",
            ),
        )
        secondary = _seed_published_card(
            catalog,
            version_id="card-filter-secondary",
            title="Customer cohort workflow",
            tags=(
                "topic:data-analysis",
                "audience:analyst",
                "difficulty:beginner",
                "tool:agnostic",
            ),
        )
        _seed_published_card(
            catalog,
            version_id="card-filter-wrong-audience",
            title="RFM RFM RFM learner workflow",
            tags=(
                "topic:data-analysis",
                "audience:learner",
                "difficulty:beginner",
                "tool:python",
            ),
        )
        _seed_published_card(
            catalog,
            version_id="card-filter-wrong-difficulty",
            title="RFM RFM advanced workflow",
            tags=(
                "topic:data-analysis",
                "audience:analyst",
                "difficulty:advanced",
                "tool:python",
            ),
        )
        _seed_published_card(
            catalog,
            version_id="card-filter-excluded",
            title="RFM spreadsheet workflow",
            tags=(
                "topic:data-analysis",
                "audience:analyst",
                "difficulty:beginner",
                "tool:spreadsheet",
            ),
        )
        _enqueue(catalog, primary, suffix="retrieval")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        provider = _FakeEmbeddingProvider()
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=provider,
            now=NOW + timedelta(seconds=1),
        )

        _seed_published_card(
            catalog,
            version_id="card-filter-post-snapshot",
            title="RFM newest strongest workflow",
            tags=(
                "topic:data-analysis",
                "audience:analyst",
                "difficulty:beginner",
                "tool:python",
            ),
        )
        query = RetrievalQuery(
            text="RFM",
            required_tag_ids=("topic:data-analysis",),
            excluded_tag_ids=("tool:spreadsheet",),
            audience_tag_id="audience:analyst",
            difficulty_tag_id="difficulty:beginner",
            index_snapshot_id=snapshot.index_snapshot_id,
            limit=1,
        )
        result = KnowledgeRetriever(catalog, embedding_provider=provider).search(query)

        assert [hit.card.version_id for hit in result.hits] == [primary.version_id]
        hit = result.hits[0]
        assert hit.score_components.fts_rank == 1
        assert hit.score_components.semantic_rank == 1
        assert hit.score_components.rrf_score == pytest.approx(2 / 61)
        assert result.evidence.status == "verified"
        assert result.evidence.output_summary["filtered_candidate_digest"]
        assert result.evidence.output_summary["index_snapshot_digest"] == (
            snapshot.snapshot_digest
        )
        lanes = result.evidence.output_summary["lanes"]
        assert [item["card_version_id"] for item in lanes] == [primary.version_id]
        assert lanes[0]["fts_rank"] == 1
        assert lanes[0]["semantic_rank"] == 1
        assert lanes[0]["rrf_score"] == pytest.approx(2 / 61)
        assert result.evidence.output_summary["returned_hit_count"] == 1
        assert result.evidence.output_summary["fused_count"] == 2
        assert result.evidence.output_summary["fts_lane_count"] == 1
        assert result.evidence.output_summary["semantic_lane_count"] == 2
        assert result.evidence.output_summary["lanes_truncated"] is True
        for digest_name in (
            "fts_lane_digest",
            "semantic_lane_digest",
            "fused_digest",
            "returned_hit_order_digest",
        ):
            assert len(result.evidence.output_summary[digest_name]) == 64
        assert result.evidence.output_summary["policy"] == {
            "digest": result.evidence.output_summary["policy"]["digest"],
            "filter_before_rank": True,
            "fts_order": "bm25-asc-cardVersionId-asc",
            "id": "course-studio-rrf-v1",
            "k": 60,
            "limit_after_fusion": True,
            "semantic_order": "score-desc-cardVersionId-asc",
            "tie_break": "cardVersionId-asc",
            "weights": {"fts": 1, "semantic": 1},
        }
        model = result.evidence.output_summary["model"]
        assert model["provider"] == "fastembed"
        assert model["provider_version"] == "0.8.0"
        assert model["model_id"] == "BAAI/bge-small-zh-v1.5"
        assert model["model_revision"] == (
            "7999e1d3359715c523056ef9478215996d62a620"
        )
        assert model["artifact_repository"] == "Qdrant/bge-small-zh-v1.5"
        assert model["artifact_revision"] == (
            "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
        )
        assert model["dimension"] == 512
        assert model["model_file_sha256s"] == (
            "3" * 64,
            "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
            "4" * 64,
            "8" * 64,
            "9" * 64,
        )
        serialized_evidence = result.evidence.model_dump_json()
        assert "config.json" not in serialized_evidence
        assert "model_optimized.onnx" not in serialized_evidence
        assert "http://" not in serialized_evidence
        assert "https://" not in serialized_evidence
        assert "\\\\" not in serialized_evidence
        repeated = KnowledgeRetriever(catalog, embedding_provider=provider).search(query)
        assert repeated.evidence.model_dump_json() == result.evidence.model_dump_json()


def test_index_and_snapshot_retrieval_remain_offline_when_sockets_are_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery

    with KnowledgeCatalog.open(tmp_path / "offline-index-retrieval.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="offline-index-retrieval")

        def denied(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise AssertionError("index or retrieval opened a socket")

        monkeypatch.setattr(socket, "socket", denied)
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        provider = _FakeEmbeddingProvider()
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=provider,
            now=NOW + timedelta(seconds=1),
        )
        result = KnowledgeRetriever(catalog, embedding_provider=provider).search(
            RetrievalQuery(
                text="RFM",
                index_snapshot_id=snapshot.index_snapshot_id,
            )
        )

        assert [hit.card.version_id for hit in result.hits] == [card.version_id]
        assert result.evidence.status == "verified"


def test_requested_hybrid_snapshot_without_provider_is_truthful_fts_only_degraded(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery

    with KnowledgeCatalog.open(tmp_path / "retrieval-degraded.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="retrieval-degraded")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=_FakeEmbeddingProvider(),
            now=NOW + timedelta(seconds=1),
        )

        result = KnowledgeRetriever(catalog, embedding_provider=None).search(
            RetrievalQuery(
                text="RFM",
                index_snapshot_id=snapshot.index_snapshot_id,
            )
        )

        assert [hit.card.version_id for hit in result.hits] == [card.version_id]
        assert result.evidence.status == "degraded"
        assert any(
            check.code == "embedding-unavailable"
            for check in result.evidence.checks
        )
        assert result.hits[0].score_components.semantic_rank is None
        assert result.hits[0].score_components.rrf_score == pytest.approx(1 / 61)


def test_unverified_provider_identity_is_rejected_and_never_auto_degraded(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        IndexSnapshotIntegrityError,
        claim_next_index_outbox,
        complete_index_claim,
    )

    with KnowledgeCatalog.open(tmp_path / "wrong-provider.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="wrong-provider")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None

        with pytest.raises(IndexSnapshotIntegrityError, match="identity"):
            complete_index_claim(
                catalog,
                claim_id=claim.claim_id,
                worker_id="worker-a",
                embedding_provider=_WrongEmbeddingProvider(),
                now=NOW + timedelta(seconds=1),
            )

        assert catalog.connection.execute(
            "SELECT count(*) FROM embedding_index_snapshots"
        ).fetchone() == (0,)
        assert catalog.connection.execute(
            "SELECT status FROM knowledge_index_outbox_results WHERE claim_id = ?",
            (claim.claim_id,),
        ).fetchone() == ("failed",)


def test_query_rejects_wrong_provider_identity_instead_of_calling_or_degrading(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import (
        KnowledgeRetriever,
        RetrievalFailure,
        RetrievalQuery,
    )

    with KnowledgeCatalog.open(tmp_path / "wrong-query-provider.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="wrong-query-provider")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=_FakeEmbeddingProvider(),
            now=NOW + timedelta(seconds=1),
        )

        with pytest.raises(RetrievalFailure) as caught:
            KnowledgeRetriever(
                catalog,
                embedding_provider=_WrongEmbeddingProvider(),
            ).search(
                RetrievalQuery(
                    text="RFM",
                    index_snapshot_id=snapshot.index_snapshot_id,
                )
            )

        assert caught.value.code == "embedding-provider-mismatch"


def test_query_rejects_provider_with_same_hashes_but_wrong_file_inventory(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import (
        KnowledgeRetriever,
        RetrievalFailure,
        RetrievalQuery,
    )

    with KnowledgeCatalog.open(tmp_path / "wrong-query-inventory.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="wrong-query-inventory")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=_FakeEmbeddingProvider(),
            now=NOW + timedelta(seconds=1),
        )

        with pytest.raises(RetrievalFailure) as caught:
            KnowledgeRetriever(
                catalog,
                embedding_provider=_WrongInventoryEmbeddingProvider(),
            ).search(
                RetrievalQuery(
                    text="RFM",
                    index_snapshot_id=snapshot.index_snapshot_id,
                )
            )

        assert caught.value.code == "embedding-provider-mismatch"


def test_raw_card_tag_tamper_cannot_change_snapshot_filter_membership(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import (
        KnowledgeRetriever,
        RetrievalFailure,
        RetrievalQuery,
    )

    with KnowledgeCatalog.open(tmp_path / "tag-tamper.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="tag-tamper")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        provider = _FakeEmbeddingProvider()
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=provider,
            now=NOW + timedelta(seconds=1),
        )
        catalog.connection.execute(
            "UPDATE card_tags SET tag_id = 'audience:learner' "
            "WHERE card_version_id = ? AND tag_id = 'audience:analyst'",
            (card.version_id,),
        )
        catalog.connection.commit()

        with pytest.raises(RetrievalFailure) as caught:
            KnowledgeRetriever(catalog, embedding_provider=provider).search(
                RetrievalQuery(
                    text="RFM",
                    audience_tag_id="audience:learner",
                    index_snapshot_id=snapshot.index_snapshot_id,
                )
            )

        assert caught.value.code == "index-snapshot-invalid"


def test_raw_fts_projection_tamper_cannot_change_snapshot_lane_ranking(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.retrieval import (
        KnowledgeRetriever,
        RetrievalFailure,
        RetrievalQuery,
    )

    with KnowledgeCatalog.open(tmp_path / "fts-tamper.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="fts-tamper")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        provider = _FakeEmbeddingProvider()
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=provider,
            now=NOW + timedelta(seconds=1),
        )
        catalog.connection.execute(
            "UPDATE card_fts SET title = 'tampered ranking bait' WHERE version_id = ?",
            (card.version_id,),
        )
        catalog.connection.commit()

        with pytest.raises(RetrievalFailure) as caught:
            KnowledgeRetriever(catalog, embedding_provider=provider).search(
                RetrievalQuery(
                    text="tampered",
                    index_snapshot_id=snapshot.index_snapshot_id,
                )
            )

        assert caught.value.code == "index-snapshot-invalid"


def test_snapshot_lifecycle_filter_is_reapplied_before_both_ranking_lanes(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.lifecycle import append_card_lifecycle_event
    from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery

    with KnowledgeCatalog.open(tmp_path / "lifecycle-filter.db") as catalog:
        suspended = _seed_published_card(
            catalog,
            version_id="card-snapshot-suspended",
            title="RFM suspended card",
        )
        remaining = _seed_published_card(
            catalog,
            version_id="card-snapshot-remaining",
            title="Customer cohort remaining",
        )
        _enqueue(catalog, suspended, suffix="lifecycle-filter")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        provider = _FakeEmbeddingProvider()
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=provider,
            now=NOW + timedelta(seconds=1),
        )
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=suspended.version_id,
            event_id="event-suspend-after-snapshot",
            request_digest=_digest("suspend-after-snapshot"),
            event_type="suspend",
            occurred_at=NOW + timedelta(seconds=2),
            actor_id=ACTOR.actor_id,
        )

        result = KnowledgeRetriever(catalog, embedding_provider=provider).search(
            RetrievalQuery(text="RFM", index_snapshot_id=snapshot.index_snapshot_id)
        )

        assert [hit.card.version_id for hit in result.hits] == [remaining.version_id]
        assert result.evidence.output_summary["filtered_candidate_count"] == 1
        assert [
            item["card_version_id"]
            for item in result.evidence.output_summary["lanes"]
        ] == [remaining.version_id]


def test_concurrent_workers_create_exactly_one_live_claim(tmp_path: Path) -> None:
    database = tmp_path / "claim-race.db"
    with KnowledgeCatalog.open(database) as setup:
        assert setup.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        card = _seed_published_card(setup)
        _enqueue(setup, card, suffix="claim-race")

    barrier = Barrier(2)

    def claim(worker_id: str):
        from course_helper.index_outbox import claim_next_index_outbox

        with KnowledgeCatalog.open(database) as catalog:
            barrier.wait(timeout=10)
            return claim_next_index_outbox(
                catalog,
                worker_id=worker_id,
                now=NOW,
                lease_seconds=30,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(claim, ("worker-a", "worker-b"))
        )

    assert sum(result is not None for result in results) == 1
    with KnowledgeCatalog.open(database) as reopened:
        assert reopened.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox_claims"
        ).fetchone() == (1,)
        assert reopened.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox_results"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("table", "trigger", "identity_column"),
    (
        (
            "embedding_index_candidates",
            "embedding_index_candidates_immutable_update",
            "candidate_id",
        ),
        (
            "embedding_index_snapshots",
            "embedding_index_snapshots_immutable_update",
            "index_snapshot_id",
        ),
        (
            "knowledge_index_outbox",
            "knowledge_index_outbox_immutable_update",
            "outbox_id",
        ),
    ),
)
def test_reopen_rebuilds_candidate_snapshot_and_outbox_digests_after_raw_tamper(
    tmp_path: Path,
    table: str,
    trigger: str,
    identity_column: str,
) -> None:
    from course_helper.index_outbox import (
        IndexSnapshotIntegrityError,
        claim_next_index_outbox,
        complete_index_claim,
        reopen_index_snapshot,
    )

    with KnowledgeCatalog.open(tmp_path / f"tamper-{table}.db") as catalog:
        card = _seed_published_card(catalog)
        outbox_id = _enqueue(catalog, card, suffix=f"tamper-{table}")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None
        snapshot = complete_index_claim(
            catalog,
            claim_id=claim.claim_id,
            worker_id="worker-a",
            embedding_provider=_FakeEmbeddingProvider(),
            now=NOW + timedelta(seconds=1),
        )
        identity = {
            "embedding_index_candidates": snapshot.candidate_id,
            "embedding_index_snapshots": snapshot.index_snapshot_id,
            "knowledge_index_outbox": outbox_id,
        }[table]
        catalog.connection.execute(f"DROP TRIGGER {trigger}")
        catalog.connection.execute(
            f"UPDATE {table} SET payload_json = '{{}}' WHERE {identity_column} = ?",
            (identity,),
        )
        catalog.connection.commit()

        with pytest.raises(IndexSnapshotIntegrityError):
            reopen_index_snapshot(catalog, snapshot.index_snapshot_id)


def test_sql_failure_during_seal_rolls_back_candidate_and_records_failed_attempt(
    tmp_path: Path,
) -> None:
    from course_helper.index_outbox import (
        IndexSnapshotIntegrityError,
        claim_next_index_outbox,
        complete_index_claim,
    )

    with KnowledgeCatalog.open(tmp_path / "seal-rollback.db") as catalog:
        card = _seed_published_card(catalog)
        _enqueue(catalog, card, suffix="seal-rollback")
        claim = claim_next_index_outbox(
            catalog, worker_id="worker-a", now=NOW, lease_seconds=30
        )
        assert claim is not None

        class ProjectionMutatingProvider(_FakeEmbeddingProvider):
            def embed_documents(self, texts: tuple[str, ...]):
                catalog.connection.execute(
                    "UPDATE card_lifecycle_current SET suspended = 1 "
                    "WHERE card_version_id = ?",
                    (card.version_id,),
                )
                return super().embed_documents(texts)

        with pytest.raises(IndexSnapshotIntegrityError):
            complete_index_claim(
                catalog,
                claim_id=claim.claim_id,
                worker_id="worker-a",
                embedding_provider=ProjectionMutatingProvider(),
                now=NOW + timedelta(seconds=1),
            )

        assert catalog.connection.execute(
            "SELECT suspended FROM card_lifecycle_current WHERE card_version_id = ?",
            (card.version_id,),
        ).fetchone() == (0,)
        assert catalog.connection.execute(
            "SELECT count(*) FROM embedding_index_candidates"
        ).fetchone() == (0,)
        assert catalog.connection.execute(
            "SELECT status FROM knowledge_index_outbox_results WHERE claim_id = ?",
            (claim.claim_id,),
        ).fetchone() == ("failed",)


def test_document_embedding_batches_never_exceed_the_provider_limit() -> None:
    from course_helper.index_outbox import _embed_document_batches

    calls: list[int] = []

    class BoundedProvider:
        def embed_documents(self, texts: tuple[str, ...]):
            calls.append(len(texts))
            assert 1 <= len(texts) <= 1_000
            return tuple(
                (1.0, *tuple(0.0 for _ in range(511))) for _ in texts
            )

    vectors = _embed_document_batches(
        BoundedProvider(),
        tuple(f"document {index}" for index in range(2_001)),
    )

    assert len(vectors) == 2_001
    assert calls == [1_000, 1_000, 1]
