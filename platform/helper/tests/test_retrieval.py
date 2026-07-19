from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    TagAssignment,
    TagDimension,
    TagValue,
    TagVocabularyVersion,
)
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.retrieval import (
    KnowledgeRetriever,
    RetrievalFailure,
    RetrievalQuery,
    RetrievalQueryError,
    safe_fts_match,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="retrieval-tests")


@pytest.fixture
def catalog(tmp_path: Path) -> KnowledgeCatalog:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as opened:
        yield opened


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scoped_vocabulary(
    *,
    version_id: str,
    created_at: datetime,
    tag_status: str,
) -> TagVocabularyVersion:
    return TagVocabularyVersion(
        logical_id="retrieval-scope-vocabulary",
        version_id=version_id,
        revision=1,
        content_digest=_digest(f"{version_id}:{tag_status}"),
        created_at=created_at,
        created_by=ACTOR,
        dimensions=(
            TagDimension(
                id="topic",
                cardinality="many",
                values=(
                    TagValue(
                        id="topic:scoped",
                        labels={"en": "Scoped"},
                        status=tag_status,
                    ),
                ),
            ),
        ),
    )


def _seed_card(
    catalog: KnowledgeCatalog,
    *,
    version_id: str,
    title: str,
    objective: str,
    body: str,
    chunk_text: str,
    tag_ids: tuple[str, ...],
    status: str = "published",
    quoted_text: str | None = None,
) -> KnowledgeCardVersion:
    from course_helper.cards import VOCABULARY_VERSION_ID, publish_card, seed_vocabulary

    seed_vocabulary(catalog)
    source_version_id = f"source-{version_id}"
    chunk_id = f"chunk-{version_id}"
    source = SourceAssetVersion(
        logical_id=f"logical-{source_version_id}",
        version_id=source_version_id,
        revision=1,
        content_digest=_digest(source_version_id),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path=f"{version_id}.md"),
        display_name=f"{version_id}.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=len(chunk_text.encode("utf-8")),
        extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id=chunk_id,
        source_version_id=source_version_id,
        ordinal=0,
        modality="text",
        language="en",
        normalized_text=chunk_text,
        content_digest=_digest(chunk_text),
        locator=ChunkLocator(
            kind="markdown-section",
            ast_path=(1,),
            heading_path=(title,),
        ),
        heading=title,
    )
    catalog.insert_source(source)
    catalog.insert_chunk(chunk)
    assignments = tuple(
        TagAssignment(
            vocabulary_version_id=VOCABULARY_VERSION_ID,
            dimension_id=tag_id.split(":", 1)[0],
            tag_id=tag_id,
        )
        for tag_id in tag_ids
    )
    candidate = KnowledgeCardVersion(
        logical_id=f"logical-{version_id}",
        version_id=version_id,
        revision=1,
        content_digest=_digest(f"candidate-{version_id}"),
        created_at=NOW,
        created_by=ACTOR,
        main_type_id="concept",
        title=title,
        learning_objective=objective,
        content_ast=(CardContentNode(type="paragraph", text=body),),
        suggested_minutes=5,
        vocabulary_version_id=VOCABULARY_VERSION_ID,
        tag_assignments=assignments,
        chunk_citations=(
            ChunkCitation(
                chunk_id=chunk_id,
                source_version_id=source_version_id,
                quoted_text=quoted_text,
            ),
        ),
        status="review",
    )
    if status == "published":
        return publish_card(candidate, catalog)
    stored = candidate.model_copy(update={"status": status})
    catalog.insert_card(stored)
    return stored


def _seed_retrieval_cards(catalog: KnowledgeCatalog) -> tuple[KnowledgeCardVersion, ...]:
    return (
        _seed_card(
            catalog,
            version_id="card-rfm-primary",
            title="RFM customer segmentation",
            objective="Segment customers with evidence",
            body="Use recency frequency monetary cohorts.",
            chunk_text="quoted excerpt plus verified cohort evidence unique-chunk-term",
            quoted_text="quoted excerpt",
            tag_ids=("topic:data-analysis", "tool:spreadsheet"),
        ),
        _seed_card(
            catalog,
            version_id="card-rfm-topic-only",
            title="RFM in Python",
            objective="Explain RFM in a notebook",
            body="Build reproducible cohort features.",
            chunk_text="Python cohort source",
            tag_ids=("topic:data-analysis", "tool:python"),
        ),
        _seed_card(
            catalog,
            version_id="card-ai-short",
            title="AI boundaries",
            objective="Explain AI limitations",
            body="Model constraints need evidence.",
            chunk_text="AI source",
            tag_ids=("topic:ai-foundations", "tool:agnostic"),
        ),
        _seed_card(
            catalog,
            version_id="card-rfm-archived",
            title="RFM obsolete draft",
            objective="Do not retrieve this object",
            body="RFM archive body",
            chunk_text="RFM archived source",
            tag_ids=("topic:data-analysis", "tool:spreadsheet"),
            status="archived",
        ),
    )


def test_safe_fts_match_normalizes_unicode_and_escapes_every_literal() -> None:
    assert safe_fts_match('  ＲＦＭ\t"OR"  title:*  ') == (
        '"RFM" OR """OR""" OR "title:*"'
    )


def test_retriever_interface_is_importable() -> None:
    assert KnowledgeRetriever is not None
    assert RetrievalQuery is not None


@pytest.mark.parametrize("text", ("", " \t\r\n "))
def test_empty_query_is_rejected_with_a_stable_domain_error(text: str) -> None:
    with pytest.raises(RetrievalQueryError) as caught:
        RetrievalQuery(text=text)

    assert caught.value.code == "empty-query"
    with pytest.raises(RetrievalQueryError) as safe_caught:
        safe_fts_match(text)
    assert safe_caught.value.code == "empty-query"


@pytest.mark.parametrize("limit", (0, 51, -1, True, 1.5))
def test_limit_outside_one_to_fifty_is_rejected_as_a_domain_error(limit: object) -> None:
    with pytest.raises(RetrievalQueryError) as caught:
        RetrievalQuery(text="RFM", limit=limit)  # type: ignore[arg-type]

    assert caught.value.code == "invalid-limit"


@pytest.mark.parametrize("tag_ids", (("",), ("topic:llm", "topic:llm")))
def test_invalid_required_tags_are_rejected_as_a_domain_error(
    tag_ids: tuple[str, ...],
) -> None:
    with pytest.raises(RetrievalQueryError) as caught:
        RetrievalQuery(text="RFM", required_tag_ids=tag_ids)

    assert caught.value.code == "invalid-required-tags"


def test_query_is_frozen_and_normalizes_required_tags_to_a_tuple() -> None:
    query = RetrievalQuery(text="RFM", required_tag_ids=["topic:llm"], limit=5)

    assert query.required_tag_ids == ("topic:llm",)
    with pytest.raises(FrozenInstanceError):
        query.limit = 6  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    (
        ({"text": "x" * 16_385}, "query-too-large"),
        ({"text": " ".join(f"token{index}" for index in range(257))}, "too-many-tokens"),
        (
            {"text": "RFM", "required_tag_ids": tuple(f"topic:t{index}" for index in range(51))},
            "too-many-tag-filters",
        ),
        ({"text": "RFM", "required_tag_ids": ("../../escape",)}, "invalid-required-tags"),
        ({"text": "RFM", "index_snapshot_id": "../snapshot"}, "invalid-index-snapshot"),
    ),
)
def test_query_identity_and_collection_bounds_are_fail_closed(
    kwargs: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(RetrievalQueryError) as caught:
        RetrievalQuery(**kwargs)  # type: ignore[arg-type]

    assert caught.value.code == code


def test_fts_candidate_membership_uses_one_json_parameter_beyond_sqlite_variable_limit(
    catalog: KnowledgeCatalog,
) -> None:
    candidate_ids = tuple(f"card-bulk-{index:04d}" for index in range(1_100))
    catalog.connection.executemany(
        "INSERT INTO card_fts("
        "version_id, title, learning_objective, body, chunk_text, projected_text"
        ") VALUES (?, 'needle', '', '', '', 'needle')",
        ((card_id,) for card_id in candidate_ids),
    )
    catalog.connection.commit()
    previous_limit = catalog.connection.setlimit(
        sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,
        999,
    )
    try:
        rows = KnowledgeRetriever(catalog)._search_fts(
            ("needle",),
            candidate_ids,
            None,
        )
    finally:
        catalog.connection.setlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,
            previous_limit,
        )

    assert len(rows) == len(candidate_ids)


def test_search_returns_only_published_cards_matching_every_required_tag(
    catalog: KnowledgeCatalog,
) -> None:
    cards = _seed_retrieval_cards(catalog)

    result = KnowledgeRetriever(catalog).search(
        RetrievalQuery(
            text="RFM",
            required_tag_ids=["topic:data-analysis", "tool:spreadsheet"],
            limit=5,
        )
    )

    assert [hit.card.version_id for hit in result.hits] == [cards[0].version_id]
    assert [hit.card.status for hit in result.hits] == ["published"]
    assert all(
        {"topic:data-analysis", "tool:spreadsheet"}.issubset(hit.card_tag_ids)
        for hit in result.hits
    )


def test_search_projects_real_cited_chunk_text_not_only_the_quote(
    catalog: KnowledgeCatalog,
) -> None:
    primary = _seed_retrieval_cards(catalog)[0]

    result = KnowledgeRetriever(catalog).search(RetrievalQuery(text="unique-chunk-term"))

    assert [hit.card.version_id for hit in result.hits] == [primary.version_id]


def test_fts_search_has_deterministic_degraded_evidence_and_scores(
    catalog: KnowledgeCatalog,
) -> None:
    _seed_retrieval_cards(catalog)
    retriever = KnowledgeRetriever(catalog, embedding_provider=None)
    query = RetrievalQuery(text="RFM", limit=2)

    first = retriever.search(query)
    second = retriever.search(query)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.schema_version == 1
    assert first.index_schema_version == 4
    assert len(first.query_digest) == 64
    assert first.evidence.status == "degraded"
    assert first.evidence.checks[0].code == "embedding-unavailable"
    assert first.evidence.checks[0].status == "warning"
    assert all(hit.score_components.fts_bm25 is not None for hit in first.hits)
    with pytest.raises(ValidationError):
        first.query_digest = "changed"  # type: ignore[misc]


def test_fts_hits_are_ordered_by_bm25_then_version_id(
    catalog: KnowledgeCatalog,
) -> None:
    _seed_retrieval_cards(catalog)

    result = KnowledgeRetriever(catalog).search(RetrievalQuery(text="RFM"))
    ranking = [
        (hit.score_components.fts_bm25, hit.card.version_id)
        for hit in result.hits
    ]

    assert all(score is not None for score, _version_id in ranking)
    assert ranking == sorted(ranking, key=lambda item: (item[0], item[1]))


def test_embedding_provider_is_never_called_in_foundation_retrieval(
    catalog: KnowledgeCatalog,
) -> None:
    _seed_retrieval_cards(catalog)

    class ExplodingProvider:
        def embed(self, _text: str) -> list[float]:
            raise AssertionError("foundation retrieval called the embedding provider")

    result = KnowledgeRetriever(catalog, embedding_provider=ExplodingProvider()).search(
        RetrievalQuery(text="RFM")
    )

    assert result.evidence.status == "degraded"


def test_short_literals_use_the_bound_fallback_and_are_deduplicated(
    catalog: KnowledgeCatalog,
) -> None:
    _seed_retrieval_cards(catalog)

    result = KnowledgeRetriever(catalog).search(RetrievalQuery(text="RFM AI", limit=50))

    assert [hit.card.version_id for hit in result.hits] == [
        "card-rfm-topic-only",
        "card-rfm-primary",
        "card-ai-short",
    ]
    assert len({hit.card.version_id for hit in result.hits}) == len(result.hits)
    assert any(check.code == "short-literal-branch" for check in result.evidence.checks)
    assert result.hits[-1].score_components.matched_via == ("short-literal",)


def test_short_only_hits_are_stable_by_version_id(catalog: KnowledgeCatalog) -> None:
    _seed_retrieval_cards(catalog)
    _seed_card(
        catalog,
        version_id="card-ai-a",
        title="AI practice",
        objective="Practice safely",
        body="AI exercise",
        chunk_text="AI activity",
        tag_ids=("topic:ai-foundations", "tool:agnostic"),
    )

    result = KnowledgeRetriever(catalog).search(RetrievalQuery(text="AI"))

    assert [hit.card.version_id for hit in result.hits] == [
        "card-ai-a",
        "card-ai-short",
        "card-rfm-topic-only",
    ]
    assert all(hit.score_components.fts_bm25 is None for hit in result.hits)


def test_quotes_operators_and_wildcards_are_literal_and_cannot_break_sql(
    catalog: KnowledgeCatalog,
) -> None:
    _seed_retrieval_cards(catalog)

    result = KnowledgeRetriever(catalog).search(
        RetrievalQuery(text='"RFM" OR title:* ) UNION SELECT *')
    )

    assert result.evidence.status == "degraded"
    assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 4


@pytest.mark.parametrize("tag_id", ("topic:missing", "tool:legacy"))
def test_unknown_or_deprecated_required_tag_is_a_sanitized_domain_error(
    catalog: KnowledgeCatalog,
    tag_id: str,
) -> None:
    from course_helper.cards import seed_vocabulary

    seed_vocabulary(catalog)

    with pytest.raises(RetrievalQueryError) as caught:
        KnowledgeRetriever(catalog).search(
            RetrievalQuery(text="RFM", required_tag_ids=[tag_id])
        )

    assert caught.value.code == "invalid-required-tag"
    assert "sqlite" not in str(caught.value).lower()


def test_limit_is_applied_after_stable_ranking(catalog: KnowledgeCatalog) -> None:
    _seed_retrieval_cards(catalog)

    result = KnowledgeRetriever(catalog).search(RetrievalQuery(text="RFM", limit=1))

    assert len(result.hits) == 1


@pytest.mark.parametrize("iteration", range(20))
def test_search_uses_one_snapshot_across_long_short_and_hit_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    iteration: int,
) -> None:
    from course_helper.cards import publish_card

    database = tmp_path / f"snapshot-{iteration}.db"
    with KnowledgeCatalog.open(database) as setup:
        assert setup.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        old = _seed_card(
            setup,
            version_id="snapshot-old",
            title="legacytoken AI old revision",
            objective="Explain legacytoken AI behavior",
            body="legacytoken and AI coexist here",
            chunk_text="legacytoken AI source",
            tag_ids=("topic:ai-foundations", "tool:agnostic"),
        )

    fts_read = Event()
    writer_done = Event()
    with KnowledgeCatalog.open(database) as reader:
        retriever = KnowledgeRetriever(reader)
        original_search_fts = retriever._search_fts

        def pause_after_fts(*args, **kwargs):
            rows = original_search_fts(*args, **kwargs)
            fts_read.set()
            assert writer_done.wait(timeout=10)
            return rows

        monkeypatch.setattr(retriever, "_search_fts", pause_after_fts)

        def publish_new_revision() -> KnowledgeCardVersion:
            assert fts_read.wait(timeout=10)
            try:
                with KnowledgeCatalog.open(database) as writer:
                    candidate = old.model_copy(
                        update={
                            "version_id": "snapshot-new",
                            "status": "review",
                            "title": "AI current revision",
                            "learning_objective": "Explain current AI behavior",
                            "content_ast": (
                                CardContentNode(type="paragraph", text="AI current content"),
                            ),
                        }
                    )
                    return publish_card(candidate, writer)
            finally:
                writer_done.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(publish_new_revision)
            result = retriever.search(RetrievalQuery(text="legacytoken AI", limit=10))
            published = future.result(timeout=10)

        assert published.version_id == "snapshot-new"
        assert [hit.card.version_id for hit in result.hits] == [old.version_id]
        assert reader.connection.in_transaction is False


def test_search_rejects_a_callers_active_transaction_without_ending_it(
    catalog: KnowledgeCatalog,
) -> None:
    _seed_retrieval_cards(catalog)
    catalog.connection.execute("BEGIN")

    try:
        with pytest.raises(RetrievalFailure) as caught:
            KnowledgeRetriever(catalog).search(RetrievalQuery(text="RFM"))
        assert caught.value.code == "active-catalog-transaction"
        assert catalog.connection.in_transaction is True
    finally:
        catalog.connection.rollback()


def test_search_rolls_back_its_snapshot_and_sanitizes_sqlite_failures(
    catalog: KnowledgeCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_retrieval_cards(catalog)
    retriever = KnowledgeRetriever(catalog)

    def fail_fts(*_args, **_kwargs):
        raise sqlite3.OperationalError("sensitive sqlite details")

    monkeypatch.setattr(retriever, "_search_fts", fail_fts)

    with pytest.raises(RetrievalFailure) as caught:
        retriever.search(RetrievalQuery(text="RFM"))

    assert caught.value.code == "catalog-query-failed"
    assert "sqlite" not in str(caught.value).lower()
    assert "sensitive" not in str(caught.value).lower()
    assert catalog.connection.in_transaction is False


def test_required_tag_must_be_active_in_the_latest_vocabulary(
    catalog: KnowledgeCatalog,
) -> None:
    catalog.insert_vocabulary(
        _scoped_vocabulary(
            version_id="scope-v1",
            created_at=NOW,
            tag_status="active",
        )
    )
    catalog.insert_vocabulary(
        _scoped_vocabulary(
            version_id="scope-v2",
            created_at=datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
            tag_status="deprecated",
        )
    )

    with pytest.raises(RetrievalQueryError) as caught:
        KnowledgeRetriever(catalog).search(
            RetrievalQuery(text="RFM", required_tag_ids=["topic:scoped"])
        )

    assert caught.value.code == "invalid-required-tag"
    assert catalog.connection.in_transaction is False


def test_latest_vocabulary_tie_breaks_by_version_id(catalog: KnowledgeCatalog) -> None:
    catalog.insert_vocabulary(
        _scoped_vocabulary(
            version_id="scope-a",
            created_at=NOW,
            tag_status="active",
        )
    )
    catalog.insert_vocabulary(
        _scoped_vocabulary(
            version_id="scope-z",
            created_at=NOW,
            tag_status="deprecated",
        )
    )

    with pytest.raises(RetrievalQueryError) as caught:
        KnowledgeRetriever(catalog).search(
            RetrievalQuery(text="RFM", required_tag_ids=["topic:scoped"])
        )

    assert caught.value.code == "invalid-required-tag"


def test_result_and_query_digest_are_bound_to_the_resolved_vocabulary(
    catalog: KnowledgeCatalog,
) -> None:
    catalog.insert_vocabulary(
        _scoped_vocabulary(
            version_id="scope-v1",
            created_at=NOW,
            tag_status="active",
        )
    )
    first = KnowledgeRetriever(catalog).search(
        RetrievalQuery(text="RFM", required_tag_ids=["topic:scoped"])
    )
    catalog.insert_vocabulary(
        _scoped_vocabulary(
            version_id="scope-v2",
            created_at=datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
            tag_status="active",
        )
    )

    second = KnowledgeRetriever(catalog).search(
        RetrievalQuery(text="RFM", required_tag_ids=["topic:scoped"])
    )

    assert first.resolved_vocabulary_version_id == "scope-v1"
    assert second.resolved_vocabulary_version_id == "scope-v2"
    assert first.query_digest != second.query_digest
    assert second.evidence.input_summary["resolved_vocabulary_version_id"] == "scope-v2"


def test_search_fails_closed_when_no_vocabulary_exists(catalog: KnowledgeCatalog) -> None:
    with pytest.raises(RetrievalQueryError) as caught:
        KnowledgeRetriever(catalog).search(RetrievalQuery(text="RFM"))

    assert caught.value.code == "vocabulary-unavailable"
    assert catalog.connection.in_transaction is False


def test_vocabulary_scope_remains_fixed_when_a_new_version_is_seeded_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "vocabulary-snapshot.db"
    with KnowledgeCatalog.open(database) as setup:
        assert setup.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        setup.insert_vocabulary(
            _scoped_vocabulary(
                version_id="scope-v1",
                created_at=NOW,
                tag_status="active",
            )
        )
    scope_resolved = Event()
    writer_done = Event()

    with KnowledgeCatalog.open(database) as reader:
        retriever = KnowledgeRetriever(reader)
        original_resolve = retriever._resolve_latest_vocabulary

        def pause_after_scope_resolution() -> TagVocabularyVersion:
            vocabulary = original_resolve()
            scope_resolved.set()
            assert writer_done.wait(timeout=10)
            return vocabulary

        monkeypatch.setattr(
            retriever,
            "_resolve_latest_vocabulary",
            pause_after_scope_resolution,
        )

        def seed_new_scope() -> None:
            assert scope_resolved.wait(timeout=10)
            try:
                with KnowledgeCatalog.open(database) as writer:
                    writer.insert_vocabulary(
                        _scoped_vocabulary(
                            version_id="scope-v2",
                            created_at=datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
                            tag_status="deprecated",
                        )
                    )
            finally:
                writer_done.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(seed_new_scope)
            result = retriever.search(
                RetrievalQuery(text="RFM", required_tag_ids=["topic:scoped"])
            )
            future.result(timeout=10)

        assert result.resolved_vocabulary_version_id == "scope-v1"
        assert reader.connection.in_transaction is False

    with KnowledgeCatalog.open(database) as reopened:
        with pytest.raises(RetrievalQueryError) as caught:
            KnowledgeRetriever(reopened).search(
                RetrievalQuery(text="RFM", required_tag_ids=["topic:scoped"])
            )
        assert caught.value.code == "invalid-required-tag"
