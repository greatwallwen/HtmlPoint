from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.knowledge import CardContentNode
from course_helper.embeddings import EmbeddingProviderIdentity

from test_cards import _persist_extraction, _pptx_extraction, _reviewed_card


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
LECTURER = ActorRef(actor_type="human", actor_id="lecturer-near-dedup")


@pytest.fixture
def catalog(tmp_path: Path) -> KnowledgeCatalog:
    with KnowledgeCatalog.open(tmp_path / "near-duplicates.db") as opened:
        from course_helper.cards import seed_vocabulary

        seed_vocabulary(opened)
        _persist_extraction(opened, _pptx_extraction((3,)))
        yield opened


def _text_card(
    *,
    logical_id: str,
    version_id: str,
    title: str,
    body: str,
):
    return _reviewed_card(
        logical_id=logical_id,
        version_id=version_id,
    ).model_copy(
        update={
            "title": title,
            "learning_objective": f"Explain {body}",
            "content_ast": (CardContentNode(type="paragraph", text=body),),
        }
    )


class _LaneProvider:
    """Small deterministic provider; production vectors are never copied to evidence."""

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

    def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, *(0.0 for _ in range(511)))

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            (1.0, *(0.0 for _ in range(511)))
            if "semantic-only-anchor" in text
            else (0.0, 1.0, *(0.0 for _ in range(510)))
            for text in texts
        )


class _MissingIdentityProvider:
    def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, *(0.0 for _ in range(511)))

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, *(0.0 for _ in range(511))) for _ in texts)


class _WrongDimensionProvider(_LaneProvider):
    identity = replace(_LaneProvider.identity, dimension=3)


class _NonFiniteProvider(_LaneProvider):
    def embed_query(self, _text: str) -> tuple[float, ...]:
        return (float("nan"), *(0.0 for _ in range(511)))


class _WrongCountProvider(_LaneProvider):
    def embed_documents(self, _texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()


class _BatchProvider(_LaneProvider):
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.batch_sizes.append(len(texts))
        return super().embed_documents(texts)


def test_token_shingles_are_nfkc_casefolded_unique_and_deterministic() -> None:
    from course_helper.near_duplicates import token_shingles

    first = token_shingles("  Alpha，BETA alpha beta Gamma  ", size=2)
    second = token_shingles("Alpha, beta ALPHA beta gamma", size=2)

    assert first == second == (
        ("alpha", "beta"),
        ("beta", "alpha"),
        ("beta", "gamma"),
    )


def test_fts_candidate_scores_are_stable_and_id_only(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card
    from course_helper.retrieval import fts_candidate_scores

    alpha = publish_card(
        _text_card(
            logical_id="fts-alpha",
            version_id="fts-alpha-v1",
            title="alpha beta gamma workshop",
            body="alpha beta gamma delta epsilon zeta",
        ),
        catalog,
    )
    beta = publish_card(
        _text_card(
            logical_id="fts-beta",
            version_id="fts-beta-v1",
            title="unrelated semantic-only-anchor",
            body="theta iota kappa lambda mu nu",
        ),
        catalog,
    )

    first = fts_candidate_scores(
        catalog,
        "alpha beta gamma delta",
        candidate_version_ids=(beta.version_id, alpha.version_id),
    )
    second = fts_candidate_scores(
        catalog,
        "alpha beta gamma delta",
        candidate_version_ids=(alpha.version_id, beta.version_id),
    )

    assert first == second
    assert first[0].card_version_id == alpha.version_id
    assert isinstance(first[0].bm25, float)
    assert not hasattr(first[0], "card")


def test_scan_unions_fts_shingle_and_cosine_lanes_with_digest_bound_evidence(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import PublishBlocked, publish_card
    from course_helper.near_duplicates import (
        NearDuplicatePolicy,
        scan_near_duplicates,
    )

    lexical = publish_card(
        _text_card(
            logical_id="published-lexical",
            version_id="published-lexical-v1",
            title="alpha beta gamma workshop",
            body="alpha beta gamma delta epsilon zeta eta theta",
        ),
        catalog,
    )
    semantic = publish_card(
        _text_card(
            logical_id="published-semantic",
            version_id="published-semantic-v1",
            title="semantic-only-anchor",
            body="one two three four five six seven eight",
        ),
        catalog,
    )
    candidate = _text_card(
        logical_id="candidate-union",
        version_id="candidate-union-v1",
        title="alpha beta gamma workshop extension",
        body="alpha beta gamma delta epsilon zeta eta addition",
    )
    catalog.insert_card(candidate)
    policy = NearDuplicatePolicy(
        shingle_size=3,
        shingle_threshold=0.20,
        semantic_threshold=0.90,
        max_candidates=12,
    )

    first = scan_near_duplicates(
        candidate,
        catalog,
        embedding_provider=_LaneProvider(),
        policy=policy,
        created_at=NOW,
    )
    second = scan_near_duplicates(
        candidate,
        catalog,
        embedding_provider=_LaneProvider(),
        policy=policy,
        created_at=NOW,
    )

    assert first == second
    assert first.policy_id == "course-studio-near-dedup-v1"
    assert len(first.policy_digest) == len(first.candidate_digest) == len(first.index_digest) == 64
    assert tuple(item.card_version_id for item in first.candidates) == (
        lexical.version_id,
        semantic.version_id,
    )
    assert first.candidates[0].matched_lanes == ("shingle-fts",)
    assert first.candidates[1].matched_lanes == ("semantic",)
    assert first.review_task is not None
    assert first.review_task.kind == "near-duplicate"
    assert first.review_task.blocking is True
    assert first.evidence.status == "warning"

    stored = json.loads(
        catalog.connection.execute(
            "SELECT payload_json FROM evidence WHERE evidence_id = ?",
            (first.evidence.evidence_id,),
        ).fetchone()[0]
    )
    summaries = json.dumps(
        {"input": stored["input_summary"], "output": stored["output_summary"]},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "content_ast" not in summaries
    assert candidate.title not in summaries
    assert stored["output_summary"]["candidate_ids"] == [
        lexical.version_id,
        semantic.version_id,
    ]
    assert stored["output_summary"]["candidates"][0]["fts_rank"] == 1
    assert stored["output_summary"]["candidates"][1]["semantic_rank"] == 1
    assert catalog.connection.execute(
        "SELECT count(*) FROM lineage WHERE from_version_id = ? AND relation = 'deduplicates'",
        (candidate.version_id,),
    ).fetchone()[0] == 0
    with pytest.raises(PublishBlocked, match="open blocking review task"):
        publish_card(candidate, catalog)


def test_semantic_unavailable_creates_explicit_degraded_blocking_review(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.near_duplicates import scan_near_duplicates

    candidate = _text_card(
        logical_id="candidate-degraded",
        version_id="candidate-degraded-v1",
        title="entirely new title",
        body="entirely new governed lesson material",
    )
    catalog.insert_card(candidate)

    result = scan_near_duplicates(candidate, catalog, created_at=NOW)

    assert result.semantic_status == "embedding-unavailable"
    assert result.evidence.status == "degraded"
    assert result.review_task is not None
    assert result.review_task.blocking is True
    assert result.candidates == ()
    assert result.evidence.checks[-1].code == "semantic-near-dedup"
    assert result.evidence.checks[-1].status == "warning"


@pytest.mark.parametrize("tamper", ("missing", "mismatch"))
def test_scan_fails_closed_for_missing_or_mismatched_published_fts_projection(
    catalog: KnowledgeCatalog,
    tamper: str,
) -> None:
    from course_helper.cards import publish_card
    from course_helper.near_duplicates import NearDuplicateError, scan_near_duplicates

    published = publish_card(
        _text_card(
            logical_id=f"fts-integrity-{tamper}",
            version_id=f"fts-integrity-{tamper}-v1",
            title="immutable projection target",
            body="immutable projection alpha beta gamma",
        ),
        catalog,
    )
    if tamper == "missing":
        catalog.connection.execute(
            "DELETE FROM card_fts WHERE version_id = ?", (published.version_id,)
        )
    else:
        catalog.connection.execute(
            "UPDATE card_fts SET projected_text = ? WHERE version_id = ?",
            ("tampered projection", published.version_id),
        )
    catalog.connection.commit()
    candidate = _text_card(
        logical_id=f"fts-integrity-candidate-{tamper}",
        version_id=f"fts-integrity-candidate-{tamper}-v1",
        title="projection candidate",
        body="projection candidate alpha beta",
    )
    catalog.insert_card(candidate)

    with pytest.raises(NearDuplicateError, match="projection is invalid"):
        scan_near_duplicates(candidate, catalog, created_at=NOW)

    assert catalog.connection.execute(
        "SELECT count(*) FROM evidence "
        "WHERE json_extract(payload_json, '$.subject_version_id') = ?",
        (candidate.version_id,),
    ).fetchone()[0] == 0
    assert catalog.connection.execute(
        "SELECT count(*) FROM review_tasks WHERE subject_version_id = ?",
        (candidate.version_id,),
    ).fetchone()[0] == 0


def test_semantic_documents_are_processed_in_policy_bounded_batches(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card
    from course_helper.near_duplicates import NearDuplicatePolicy, scan_near_duplicates

    for index in range(5):
        publish_card(
            _text_card(
                logical_id=f"batch-target-{index}",
                version_id=f"batch-target-{index}-v1",
                title=f"batch target {index}",
                body=f"bounded semantic document batch token {index}",
            ),
            catalog,
        )
    candidate = _text_card(
        logical_id="batch-candidate",
        version_id="batch-candidate-v1",
        title="batch candidate",
        body="bounded semantic candidate",
    )
    catalog.insert_card(candidate)
    provider = _BatchProvider()

    scan_near_duplicates(
        candidate,
        catalog,
        embedding_provider=provider,
        policy=NearDuplicatePolicy(semantic_batch_size=2),
        created_at=NOW,
    )

    assert provider.batch_sizes == [2, 2, 1]


@pytest.mark.parametrize(
    "provider",
    (
        _MissingIdentityProvider(),
        _WrongDimensionProvider(),
        _NonFiniteProvider(),
        _WrongCountProvider(),
    ),
)
def test_unverified_or_invalid_semantic_provider_is_degraded_and_blocking(
    catalog: KnowledgeCatalog,
    provider: object,
) -> None:
    from course_helper.cards import publish_card
    from course_helper.near_duplicates import scan_near_duplicates

    publish_card(
        _text_card(
            logical_id="provider-validation-target",
            version_id="provider-validation-target-v1",
            title="provider validation target",
            body="published semantic comparison target",
        ),
        catalog,
    )
    candidate = _text_card(
        logical_id="candidate-invalid-provider",
        version_id="candidate-invalid-provider-v1",
        title="invalid provider candidate",
        body="governed candidate body for strict semantic validation",
    )
    catalog.insert_card(candidate)

    result = scan_near_duplicates(
        candidate,
        catalog,
        embedding_provider=provider,
        created_at=NOW,
    )

    assert result.semantic_status in {
        "embedding-provider-invalid",
        "embedding-inference-failed",
    }
    assert result.evidence.status == "degraded"
    assert result.review_task is not None
    assert result.review_task.blocking is True


def test_dismissal_resolves_review_and_allows_separate_publication(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card
    from course_helper.near_duplicates import (
        resolve_near_duplicate_review,
        scan_near_duplicates,
    )

    candidate = _text_card(
        logical_id="candidate-dismiss",
        version_id="candidate-dismiss-v1",
        title="dismiss candidate",
        body="dismiss candidate body",
    )
    catalog.insert_card(candidate)
    scan = scan_near_duplicates(candidate, catalog, created_at=NOW)
    assert scan.review_task is not None

    resolution = resolve_near_duplicate_review(
        catalog,
        task_id=scan.review_task.task_id,
        decision="dismiss",
        resolved_by=LECTURER,
        resolved_at=NOW,
    )
    published = publish_card(candidate, catalog)

    assert resolution.decision == "dismiss"
    assert catalog.connection.execute(
        "SELECT current_status FROM review_task_current WHERE task_id = ?",
        (scan.review_task.task_id,),
    ).fetchone()[0] == "dismissed"
    assert published.status == "published"


def test_lecturer_duplicate_link_is_human_directed_and_archives_candidate(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card
    from course_helper.near_duplicates import (
        NearDuplicatePolicy,
        resolve_near_duplicate_review,
        scan_near_duplicates,
    )

    existing = publish_card(
        _text_card(
            logical_id="linked-existing",
            version_id="linked-existing-v1",
            title="linked alpha beta gamma",
            body="linked alpha beta gamma delta epsilon zeta",
        ),
        catalog,
    )
    candidate = _text_card(
        logical_id="linked-candidate",
        version_id="linked-candidate-v1",
        title="linked alpha beta gamma extension",
        body="linked alpha beta gamma delta epsilon addition",
    )
    catalog.insert_card(candidate)
    scan = scan_near_duplicates(
        candidate,
        catalog,
        embedding_provider=_LaneProvider(),
        policy=NearDuplicatePolicy(
            shingle_size=2,
            shingle_threshold=0.20,
            semantic_threshold=0.99,
            max_candidates=12,
        ),
        created_at=NOW,
    )
    assert scan.review_task is not None
    assert catalog.connection.execute(
        "SELECT count(*) FROM lineage WHERE from_version_id = ? AND relation = 'deduplicates'",
        (candidate.version_id,),
    ).fetchone()[0] == 0

    resolution = resolve_near_duplicate_review(
        catalog,
        task_id=scan.review_task.task_id,
        decision="duplicate-link",
        target_version_id=existing.version_id,
        resolved_by=LECTURER,
        resolved_at=NOW,
    )

    assert resolution.decision == "accept"
    assert catalog.connection.execute(
        "SELECT status FROM card_lifecycle_current WHERE card_version_id = ?",
        (candidate.version_id,),
    ).fetchone()[0] == "archived"
    assert catalog.connection.execute(
        "SELECT to_version_id FROM lineage WHERE from_version_id = ? AND relation = 'deduplicates'",
        (candidate.version_id,),
    ).fetchall() == [(existing.version_id,)]
    assert publish_card(candidate, catalog) == existing
    assert catalog.connection.execute(
        "SELECT count(*) FROM cards JOIN card_lifecycle_current lifecycle "
        "ON lifecycle.card_version_id = cards.version_id "
        "WHERE lifecycle.status = 'published'"
    ).fetchone()[0] == 1


def test_duplicate_link_rejects_forged_arbitrary_evidence(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import create_review_task, publish_card
    from course_helper.near_duplicates import (
        NearDuplicateError,
        resolve_near_duplicate_review,
    )

    existing = publish_card(
        _text_card(
            logical_id="forged-target",
            version_id="forged-target-v1",
            title="forged evidence target",
            body="published target for forged evidence",
        ),
        catalog,
    )
    candidate = _text_card(
        logical_id="forged-candidate",
        version_id="forged-candidate-v1",
        title="forged evidence candidate",
        body="candidate whose review cannot trust arbitrary evidence",
    )
    catalog.insert_card(candidate)
    forged = EvidenceObject(
        evidence_id="forged-near-dedup-evidence",
        kind="dedup",
        subject_version_id=candidate.version_id,
        status="warning",
        output_summary={"candidate_ids": [existing.version_id]},
        producer="course-helper/tests",
        producer_version="course-studio-near-dedup-v1",
        started_at=NOW,
        finished_at=NOW,
    )
    catalog.insert_evidence(forged)
    task = create_review_task(
        catalog,
        kind="near-duplicate",
        subject_version_id=candidate.version_id,
        evidence_ids=(forged.evidence_id,),
        created_at=NOW,
    )

    with pytest.raises(NearDuplicateError, match="evidence envelope is invalid"):
        resolve_near_duplicate_review(
            catalog,
            task_id=task.task_id,
            decision="duplicate-link",
            target_version_id=existing.version_id,
            resolved_by=LECTURER,
            resolved_at=NOW,
        )

    assert catalog.connection.execute(
        "SELECT count(*) FROM review_resolutions WHERE task_id = ?", (task.task_id,)
    ).fetchone()[0] == 0
    assert catalog.connection.execute(
        "SELECT count(*) FROM lineage WHERE from_version_id = ? AND relation = 'deduplicates'",
        (candidate.version_id,),
    ).fetchone()[0] == 0


def test_exact_duplicate_audit_is_auto_resolved_in_same_publish_transaction(
    catalog: KnowledgeCatalog,
) -> None:
    from course_helper.cards import publish_card

    original = publish_card(_reviewed_card(), catalog)
    duplicate = _reviewed_card(
        logical_id="exact-audit-logical",
        version_id="exact-audit-version",
    )

    result = publish_card(duplicate, catalog)

    assert result == original
    task = catalog.connection.execute(
        "SELECT task.task_id, current.current_status, current.resolution_id "
        "FROM review_tasks task JOIN review_task_current current USING(task_id) "
        "WHERE task.subject_version_id = ? AND task.kind = 'exact-duplicate'",
        (duplicate.version_id,),
    ).fetchone()
    assert task is not None
    assert task[1] == "resolved"
    assert task[2] is not None
    assert catalog.connection.execute(
        "SELECT decision FROM review_resolutions WHERE resolution_id = ?",
        (task[2],),
    ).fetchone()[0] == "accept"
    assert catalog.connection.execute(
        "SELECT to_version_id FROM lineage WHERE from_version_id = ? AND relation = 'deduplicates'",
        (duplicate.version_id,),
    ).fetchall() == [(original.version_id,)]

    counts = tuple(
        catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "cards",
            "lineage",
            "evidence",
            "review_tasks",
            "review_resolutions",
        )
    )
    assert publish_card(duplicate, catalog) == original
    assert tuple(
        catalog.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "cards",
            "lineage",
            "evidence",
            "review_tasks",
            "review_resolutions",
        )
    ) == counts
    assert catalog.connection.execute(
        "SELECT count(*) FROM lineage WHERE from_version_id = to_version_id"
    ).fetchone()[0] == 0


def test_policy_and_scan_digests_are_stable_across_database_insertion_order(
    tmp_path: Path,
) -> None:
    from course_helper.cards import publish_card, seed_vocabulary
    from course_helper.near_duplicates import NearDuplicatePolicy, scan_near_duplicates

    results = []
    definitions = (
        ("stable-a", "stable-a-v1", "stable alpha beta", "alpha beta gamma delta"),
        ("stable-b", "stable-b-v1", "stable alpha beta", "alpha beta gamma epsilon"),
    )
    for index, ordered in enumerate((definitions, tuple(reversed(definitions)))):
        with KnowledgeCatalog.open(tmp_path / f"stable-{index}.db") as catalog:
            seed_vocabulary(catalog)
            _persist_extraction(catalog, _pptx_extraction((3,)))
            for logical_id, version_id, title, body in ordered:
                publish_card(
                    _text_card(
                        logical_id=logical_id,
                        version_id=version_id,
                        title=title,
                        body=body,
                    ),
                    catalog,
                )
            candidate = _text_card(
                logical_id="stable-candidate",
                version_id="stable-candidate-v1",
                title="stable alpha beta extension",
                body="alpha beta gamma addition",
            )
            catalog.insert_card(candidate)
            results.append(
                scan_near_duplicates(
                    candidate,
                    catalog,
                    embedding_provider=_LaneProvider(),
                    policy=NearDuplicatePolicy(
                        shingle_size=2,
                        shingle_threshold=0.1,
                        semantic_threshold=0.9,
                        max_candidates=12,
                    ),
                    created_at=NOW,
                )
            )

    assert results[0].policy_digest == results[1].policy_digest
    assert results[0].candidate_digest == results[1].candidate_digest
    assert results[0].index_digest == results[1].index_digest
    assert [item.card_version_id for item in results[0].candidates] == [
        item.card_version_id for item in results[1].candidates
    ]
    assert hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in results[0].candidates],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest() == hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in results[1].candidates],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
