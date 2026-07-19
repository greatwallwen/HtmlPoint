"""Deterministic snapshot-bound hybrid retrieval for governed knowledge cards."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from course_helper.catalog import KnowledgeCatalog, canonical_model_json
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.knowledge import KnowledgeCardVersion, TagVocabularyVersion
from course_helper.embeddings import (
    RRF_K,
    RRF_POLICY_ID,
    course_studio_rrf_v1,
    validate_embedding_vector,
)


# The retrieval contract evolves independently from catalog storage migrations.
# Artifact metadata migration 0005 does not change ranking or index semantics.
INDEX_SCHEMA_VERSION = 4
from course_helper.index_outbox import (
    DOCUMENT_ENCODING_POLICY,
    QUERY_ENCODING_POLICY,
    IndexSnapshot,
    IndexSnapshotIntegrityError,
    _provider_record,
    _verified_card_projection,
    reopen_index_snapshot,
)
from course_helper.lifecycle import reopen_card_version


_STABLE_EVIDENCE_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_QUERY_UTF8_BYTES = 16_384
_MAX_QUERY_TOKENS = 256
_MAX_TAG_FILTERS = 50


class RetrievalQueryError(ValueError):
    """Stable, sanitized validation failure for a retrieval request."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RetrievalFailure(RuntimeError):
    """Stable, sanitized catalog or snapshot failure raised during retrieval."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RetrievalQuery:
    """Validated query and all filters applied before either ranking lane."""

    text: str
    required_tag_ids: tuple[str, ...] = ()
    limit: int = 10
    excluded_tag_ids: tuple[str, ...] = ()
    audience_tag_id: str | None = None
    difficulty_tag_id: str | None = None
    index_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not _normalize_text(self.text):
            raise RetrievalQueryError("empty-query", "retrieval query must not be empty")
        normalized = _normalize_text(self.text)
        if len(normalized.encode("utf-8")) > _MAX_QUERY_UTF8_BYTES:
            raise RetrievalQueryError("query-too-large", "retrieval query is too large")
        tokens = normalized.split(" ")
        if len(tokens) > _MAX_QUERY_TOKENS:
            raise RetrievalQueryError("too-many-tokens", "retrieval query has too many tokens")
        if any(len(token.encode("utf-8")) > 512 for token in tokens):
            raise RetrievalQueryError("query-token-too-large", "retrieval token is too large")
        if type(self.limit) is not int or not 1 <= self.limit <= 50:
            raise RetrievalQueryError(
                "invalid-limit", "retrieval limit must be from 1 to 50"
            )
        required = _validated_tag_tuple(
            self.required_tag_ids,
            code="invalid-required-tags",
            label="required tags",
        )
        excluded = _validated_tag_tuple(
            self.excluded_tag_ids,
            code="invalid-excluded-tags",
            label="excluded tags",
        )
        if set(required) & set(excluded):
            raise RetrievalQueryError(
                "conflicting-tag-filters",
                "required and excluded tags must not overlap",
            )
        if (
            len(required)
            + len(excluded)
            + int(self.audience_tag_id is not None)
            + int(self.difficulty_tag_id is not None)
            > _MAX_TAG_FILTERS
        ):
            raise RetrievalQueryError(
                "too-many-tag-filters", "retrieval query has too many tag filters"
            )
        for value, prefix, code, label in (
            (
                self.audience_tag_id,
                "audience:",
                "invalid-audience-tag",
                "audience tag",
            ),
            (
                self.difficulty_tag_id,
                "difficulty:",
                "invalid-difficulty-tag",
                "difficulty tag",
            ),
        ):
            if value is not None and (
                not isinstance(value, str)
                or not value.startswith(prefix)
                or not value[len(prefix) :]
                or _SAFE_ID.fullmatch(value) is None
            ):
                raise RetrievalQueryError(code, f"{label} has an invalid dimension")
            if value is not None and value in excluded:
                raise RetrievalQueryError(
                    "conflicting-tag-filters",
                    f"{label} cannot also be excluded",
                )
        if self.index_snapshot_id is not None and (
            not isinstance(self.index_snapshot_id, str)
            or _SAFE_ID.fullmatch(self.index_snapshot_id) is None
        ):
            raise RetrievalQueryError(
                "invalid-index-snapshot", "index snapshot ID is invalid"
            )
        object.__setattr__(self, "required_tag_ids", required)
        object.__setattr__(self, "excluded_tag_ids", excluded)


class RetrievalScoreComponents(BaseModel):
    """Every lexical, semantic, and fusion rank used for one hit."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    fts_bm25: float | None = None
    semantic_score: float | None = None
    fts_rank: int | None = Field(default=None, ge=1)
    semantic_rank: int | None = Field(default=None, ge=1)
    rrf_score: float = Field(ge=0)
    matched_via: tuple[Literal["fts", "short-literal", "semantic"], ...]


class RetrievalHit(BaseModel):
    """One immutable published-card search hit."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    card: KnowledgeCardVersion
    card_tag_ids: tuple[str, ...]
    score_components: RetrievalScoreComponents


class RetrievalResult(BaseModel):
    """Versioned immutable retrieval output with deterministic evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    index_schema_version: int = Field(ge=1)
    resolved_vocabulary_version_id: str = Field(min_length=1)
    query_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    hits: tuple[RetrievalHit, ...]
    evidence: EvidenceObject


@dataclass(frozen=True)
class _Candidate:
    card_version_id: str
    card_content_digest: str
    payload_json: str
    tag_ids: tuple[str, ...]


@dataclass(frozen=True)
class _FtsLaneItem:
    card_version_id: str
    bm25: float | None
    matched_via: tuple[Literal["fts", "short-literal"], ...]


@dataclass(frozen=True)
class FtsCandidateScore:
    """One payload-free FTS candidate score for reuse by governance workflows."""

    card_version_id: str
    bm25: float


class KnowledgeRetriever:
    """Retrieve published card versions from one consistent catalog snapshot."""

    def __init__(
        self,
        catalog: KnowledgeCatalog,
        *,
        embedding_provider: object | None = None,
    ) -> None:
        self._catalog = catalog
        self._embedding_provider = embedding_provider

    def search(self, query: RetrievalQuery) -> RetrievalResult:
        """Filter both lanes first, rank them exactly, then apply fusion limit."""

        if not isinstance(query, RetrievalQuery):
            raise RetrievalQueryError(
                "invalid-query", "retrieval search requires a RetrievalQuery"
            )
        connection = self._catalog.connection
        if connection.in_transaction:
            raise RetrievalFailure(
                "active-catalog-transaction",
                "knowledge retrieval requires an idle catalog connection",
            )
        normalized = _normalize_text(query.text)
        tokens = tuple(normalized.split(" "))
        long_tokens = tuple(token for token in tokens if len(token) >= 3)
        short_tokens = tuple(token for token in tokens if len(token) < 3)
        required_tags = tuple(sorted(query.required_tag_ids))
        excluded_tags = tuple(sorted(query.excluded_tag_ids))
        try:
            connection.execute("BEGIN")
            vocabulary = self._resolve_latest_vocabulary()
            self._validate_filter_tags(query, vocabulary)
            snapshot = self._resolve_snapshot(query.index_snapshot_id)
            candidates = self._filtered_candidates(
                snapshot=snapshot,
                required_tags=required_tags,
                excluded_tags=excluded_tags,
                audience_tag_id=query.audience_tag_id,
                difficulty_tag_id=query.difficulty_tag_id,
            )
            candidate_ids = tuple(item.card_version_id for item in candidates)
            fts_rows = self._search_fts(long_tokens, candidate_ids, snapshot)
            short_rows = self._search_short_literals(
                short_tokens, candidate_ids, snapshot
            )
            fts_lane = self._merge_fts_lane(fts_rows, short_rows)
            semantic_lane, degraded_code = self._semantic_lane(
                normalized,
                candidates,
                snapshot,
            )
            fused = course_studio_rrf_v1(
                tuple(item.card_version_id for item in fts_lane),
                tuple(item[0] for item in semantic_lane),
            )
            candidate_by_id = {item.card_version_id: item for item in candidates}
            fts_by_id = {item.card_version_id: item for item in fts_lane}
            semantic_by_id = {
                card_version_id: (rank, score)
                for rank, (card_version_id, score) in enumerate(semantic_lane, 1)
            }
            hits = tuple(
                self._hit(
                    candidate_by_id[item.card_version_id],
                    fts_by_id.get(item.card_version_id),
                    semantic_by_id.get(item.card_version_id),
                    fts_rank=item.fts_rank,
                    semantic_rank=item.semantic_rank,
                    rrf_score=item.score,
                )
                for item in fused[: query.limit]
            )
            connection.commit()
        except RetrievalQueryError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except IndexSnapshotIntegrityError:
            if connection.in_transaction:
                connection.rollback()
            raise RetrievalFailure(
                "index-snapshot-invalid",
                "the requested retrieval snapshot failed integrity validation",
            ) from None
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise RetrievalFailure(
                "catalog-query-failed",
                "knowledge retrieval could not query the local catalog",
            ) from None
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

        query_digest = retrieval_query_digest(
            query,
            resolved_vocabulary_version_id=vocabulary.version_id,
        )
        candidate_digest = _filtered_candidate_digest(candidates)
        evidence = _retrieval_evidence(
            query_digest=query_digest,
            candidates=candidates,
            candidate_digest=candidate_digest,
            fts_lane=fts_lane,
            semantic_lane=semantic_lane,
            fused=fused,
            returned_ids=tuple(hit.card.version_id for hit in hits),
            snapshot=snapshot,
            degraded_code=degraded_code,
            short_literal_count=len(short_tokens),
            resolved_vocabulary_version_id=vocabulary.version_id,
        )
        return RetrievalResult(
            index_schema_version=INDEX_SCHEMA_VERSION,
            resolved_vocabulary_version_id=vocabulary.version_id,
            query_digest=query_digest,
            hits=hits,
            evidence=evidence,
        )

    def _resolve_latest_vocabulary(self) -> TagVocabularyVersion:
        rows = self._catalog.connection.execute(
            "SELECT payload_json FROM tag_vocabularies"
        ).fetchall()
        if not rows:
            raise RetrievalQueryError(
                "vocabulary-unavailable",
                "no tag vocabulary is available for retrieval",
            )
        vocabularies = tuple(
            TagVocabularyVersion.model_validate_json(row[0]) for row in rows
        )
        return max(vocabularies, key=lambda item: (item.created_at, item.version_id))

    def _validate_filter_tags(
        self,
        query: RetrievalQuery,
        vocabulary: TagVocabularyVersion,
    ) -> None:
        scoped = {
            value.id: (value.status, dimension.id)
            for dimension in vocabulary.dimensions
            for value in dimension.values
        }
        requested = (
            *query.required_tag_ids,
            *query.excluded_tag_ids,
            *(() if query.audience_tag_id is None else (query.audience_tag_id,)),
            *(
                ()
                if query.difficulty_tag_id is None
                else (query.difficulty_tag_id,)
            ),
        )
        for tag_id in requested:
            if scoped.get(tag_id, (None, None))[0] != "active":
                raise RetrievalQueryError(
                    "invalid-required-tag",
                    "retrieval tag is unknown or unavailable",
                )
        if query.audience_tag_id is not None and scoped[query.audience_tag_id][1] != "audience":
            raise RetrievalQueryError(
                "invalid-audience-tag", "audience filter has the wrong dimension"
            )
        if (
            query.difficulty_tag_id is not None
            and scoped[query.difficulty_tag_id][1] != "difficulty"
        ):
            raise RetrievalQueryError(
                "invalid-difficulty-tag", "difficulty filter has the wrong dimension"
            )

    def _resolve_snapshot(self, index_snapshot_id: str | None) -> IndexSnapshot | None:
        if index_snapshot_id is None:
            return None
        row = self._catalog.connection.execute(
            "SELECT 1 FROM embedding_index_snapshots WHERE index_snapshot_id = ?",
            (index_snapshot_id,),
        ).fetchone()
        if row is None:
            raise RetrievalQueryError(
                "snapshot-unavailable", "requested index snapshot is unavailable"
            )
        return reopen_index_snapshot(self._catalog, index_snapshot_id)

    def _filtered_candidates(
        self,
        *,
        snapshot: IndexSnapshot | None,
        required_tags: tuple[str, ...],
        excluded_tags: tuple[str, ...],
        audience_tag_id: str | None,
        difficulty_tag_id: str | None,
    ) -> tuple[_Candidate, ...]:
        if snapshot is None:
            rows = self._catalog.connection.execute(
                "SELECT cards.version_id, cards.content_digest, cards.payload_json "
                "FROM cards JOIN card_lifecycle_current AS lifecycle "
                "ON lifecycle.card_version_id = cards.version_id "
                "WHERE lifecycle.status = 'published' AND lifecycle.suspended = 0 "
                "ORDER BY cards.version_id"
            ).fetchall()
        else:
            rows = self._catalog.connection.execute(
                "SELECT cards.version_id, cards.content_digest, cards.payload_json "
                "FROM embedding_index_fts_rows AS indexed "
                "JOIN cards ON cards.version_id = indexed.card_version_id "
                "JOIN card_lifecycle_current AS lifecycle "
                "ON lifecycle.card_version_id = cards.version_id "
                "WHERE indexed.candidate_id = ? "
                "AND indexed.card_content_digest = cards.content_digest "
                "AND indexed.policy_id = ? "
                "AND indexed.model_manifest_digest IS ? "
                "AND lifecycle.status = 'published' AND lifecycle.suspended = 0 "
                "ORDER BY cards.version_id",
                (
                    snapshot.candidate_id,
                    snapshot.policy_id,
                    snapshot.model_manifest_digest,
                ),
            ).fetchall()
        selected: list[_Candidate] = []
        for row in rows:
            version_id, content_digest, payload_json = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
            )
            try:
                card = KnowledgeCardVersion.model_validate_json(payload_json, strict=False)
            except Exception as error:
                raise IndexSnapshotIntegrityError("candidate card payload is invalid") from error
            if (
                card.version_id != version_id
                or card.content_digest != content_digest
                or canonical_model_json(card) != payload_json
            ):
                raise IndexSnapshotIntegrityError("candidate card envelope is invalid")
            stored_projection = self._catalog.connection.execute(
                "SELECT title, learning_objective, body, chunk_text, projected_text "
                "FROM card_fts WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            expected_projection = _verified_card_projection(self._catalog, card)
            if (
                stored_projection is None
                or tuple(str(value) for value in stored_projection)
                != expected_projection
            ):
                raise IndexSnapshotIntegrityError(
                    "candidate FTS projection is invalid"
                )
            stored_assignments = tuple(
                (str(item[0]), str(item[1]))
                for item in self._catalog.connection.execute(
                    "SELECT vocabulary_version_id, tag_id FROM card_tags "
                    "WHERE card_version_id = ? "
                    "ORDER BY vocabulary_version_id, tag_id",
                    (version_id,),
                ).fetchall()
            )
            immutable_assignments = tuple(
                sorted(
                    (
                        assignment.vocabulary_version_id,
                        assignment.tag_id,
                    )
                    for assignment in card.tag_assignments
                )
            )
            if stored_assignments != immutable_assignments:
                raise IndexSnapshotIntegrityError(
                    "candidate card tag projection is invalid"
                )
            tag_ids = tuple(item[1] for item in immutable_assignments)
            tag_set = set(tag_ids)
            if not set(required_tags).issubset(tag_set):
                continue
            if set(excluded_tags) & tag_set:
                continue
            if audience_tag_id is not None and audience_tag_id not in tag_set:
                continue
            if difficulty_tag_id is not None and difficulty_tag_id not in tag_set:
                continue
            selected.append(
                _Candidate(
                    card_version_id=version_id,
                    card_content_digest=content_digest,
                    payload_json=payload_json,
                    tag_ids=tag_ids,
                )
            )
        return tuple(selected)

    def _search_fts(
        self,
        tokens: tuple[str, ...],
        candidate_ids: tuple[str, ...],
        snapshot: IndexSnapshot | None,
    ) -> tuple[tuple[str, float], ...]:
        if not tokens or not candidate_ids:
            return ()
        candidate_json = _canonical(candidate_ids)
        snapshot_join = ""
        snapshot_parameters: tuple[object, ...] = ()
        if snapshot is not None:
            snapshot_join = (
                "JOIN embedding_index_fts_rows AS indexed "
                "ON indexed.card_version_id = card_fts.version_id "
                "AND indexed.candidate_id = ? "
            )
            snapshot_parameters = (snapshot.candidate_id,)
        rows = self._catalog.connection.execute(
            f"""
            SELECT card_fts.version_id, bm25(card_fts)
            FROM card_fts
            {snapshot_join}
            WHERE card_fts MATCH ?
              AND card_fts.version_id IN (SELECT value FROM json_each(?))
            ORDER BY bm25(card_fts), card_fts.version_id
            """,
            (
                *snapshot_parameters,
                _fts_match_for_literals(tokens),
                candidate_json,
            ),
        ).fetchall()
        return tuple((str(row[0]), float(row[1])) for row in rows)

    def _search_short_literals(
        self,
        tokens: tuple[str, ...],
        candidate_ids: tuple[str, ...],
        snapshot: IndexSnapshot | None,
    ) -> tuple[str, ...]:
        if not tokens or not candidate_ids:
            return ()
        literal_clause = " OR ".join(
            "instr(lower(card_fts.projected_text), lower(?)) > 0" for _ in tokens
        )
        candidate_json = _canonical(candidate_ids)
        snapshot_join = ""
        snapshot_parameters: tuple[object, ...] = ()
        if snapshot is not None:
            snapshot_join = (
                "JOIN embedding_index_fts_rows AS indexed "
                "ON indexed.card_version_id = card_fts.version_id "
                "AND indexed.candidate_id = ? "
            )
            snapshot_parameters = (snapshot.candidate_id,)
        rows = self._catalog.connection.execute(
            f"""
            SELECT card_fts.version_id
            FROM card_fts
            {snapshot_join}
            WHERE ({literal_clause})
              AND card_fts.version_id IN (SELECT value FROM json_each(?))
            ORDER BY card_fts.version_id
            """,
            (*snapshot_parameters, *tokens, candidate_json),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _merge_fts_lane(
        self,
        fts_rows: tuple[tuple[str, float], ...],
        short_rows: tuple[str, ...],
    ) -> tuple[_FtsLaneItem, ...]:
        short_ids = set(short_rows)
        seen: set[str] = set()
        lane: list[_FtsLaneItem] = []
        for version_id, score in fts_rows:
            lane.append(
                _FtsLaneItem(
                    card_version_id=version_id,
                    bm25=score,
                    matched_via=(
                        ("fts", "short-literal")
                        if version_id in short_ids
                        else ("fts",)
                    ),
                )
            )
            seen.add(version_id)
        for version_id in short_rows:
            if version_id not in seen:
                lane.append(
                    _FtsLaneItem(
                        card_version_id=version_id,
                        bm25=None,
                        matched_via=("short-literal",),
                    )
                )
                seen.add(version_id)
        return tuple(lane)

    def _semantic_lane(
        self,
        normalized_query: str,
        candidates: tuple[_Candidate, ...],
        snapshot: IndexSnapshot | None,
    ) -> tuple[tuple[tuple[str, float], ...], str | None]:
        if snapshot is None:
            return (), "embedding-unavailable"
        if snapshot.retrieval_mode != "hybrid":
            return (), "snapshot-fts-degraded"
        if self._embedding_provider is None:
            return (), "embedding-unavailable"
        if not _provider_matches_snapshot(self._embedding_provider, snapshot):
            raise RetrievalFailure(
                "embedding-provider-mismatch",
                "the available embedding provider does not match the requested snapshot",
            )
        embed_query = getattr(self._embedding_provider, "embed_query", None)
        if not callable(embed_query):
            return (), "embedding-unavailable"
        try:
            query_vector = validate_embedding_vector(embed_query(normalized_query), dimension=512)
        except Exception:
            return (), "embedding-query-failed"
        if not candidates:
            return (), None
        candidate_json = _canonical(tuple(item.card_version_id for item in candidates))
        rows = self._catalog.connection.execute(
            f"""
            SELECT vector.card_version_id, vector.card_content_digest,
                   vector.policy_id, vector.model_manifest_digest,
                   vector.vector_dimension, vector.vector_digest, vector.vector_json
            FROM card_embedding_rows AS vector
            JOIN embedding_index_fts_rows AS indexed
              ON indexed.candidate_id = vector.candidate_id
             AND indexed.card_version_id = vector.card_version_id
             AND indexed.card_content_digest = vector.card_content_digest
             AND indexed.policy_id = vector.policy_id
             AND indexed.model_manifest_digest = vector.model_manifest_digest
            WHERE vector.candidate_id = ?
              AND vector.card_version_id IN (SELECT value FROM json_each(?))
            ORDER BY vector.card_version_id
            """,
            (snapshot.candidate_id, candidate_json),
        ).fetchall()
        candidate_by_id = {item.card_version_id: item for item in candidates}
        if len(rows) != len(candidates):
            raise IndexSnapshotIntegrityError("semantic snapshot row set is incomplete")
        scored: list[tuple[str, float]] = []
        for row in rows:
            version_id = str(row[0])
            candidate = candidate_by_id.get(version_id)
            if (
                candidate is None
                or row[1] != candidate.card_content_digest
                or row[2] != RRF_POLICY_ID
                or row[3] != snapshot.model_manifest_digest
                or row[4] != 512
            ):
                raise IndexSnapshotIntegrityError("semantic snapshot row is invalid")
            try:
                raw = json.loads(str(row[6]))
                vector = validate_embedding_vector(raw, dimension=512)
            except Exception as error:
                raise IndexSnapshotIntegrityError("semantic snapshot vector is invalid") from error
            vector_json = _canonical(vector)
            if vector_json != row[6] or _sha256_text(vector_json) != row[5]:
                raise IndexSnapshotIntegrityError("semantic snapshot digest is invalid")
            score = float(sum(left * right for left, right in zip(query_vector, vector, strict=True)))
            scored.append((version_id, score))
        return tuple(sorted(scored, key=lambda item: (-item[1], item[0]))), None

    def _hit(
        self,
        candidate: _Candidate,
        fts: _FtsLaneItem | None,
        semantic: tuple[int, float] | None,
        *,
        fts_rank: int | None,
        semantic_rank: int | None,
        rrf_score: float,
    ) -> RetrievalHit:
        reopened = reopen_card_version(
            self._catalog.connection, candidate.card_version_id
        )
        if reopened.card.content_digest != candidate.card_content_digest:
            raise IndexSnapshotIntegrityError("retrieval hit content digest changed")
        matched: list[Literal["fts", "short-literal", "semantic"]] = []
        if fts is not None:
            matched.extend(fts.matched_via)
        if semantic is not None:
            matched.append("semantic")
        return RetrievalHit(
            card=reopened.card,
            card_tag_ids=candidate.tag_ids,
            score_components=RetrievalScoreComponents(
                fts_bm25=None if fts is None else fts.bm25,
                semantic_score=None if semantic is None else semantic[1],
                fts_rank=fts_rank,
                semantic_rank=semantic_rank,
                rrf_score=rrf_score,
                matched_via=tuple(matched),
            ),
        )


def safe_fts_match(text: str) -> str:
    """Build an FTS5 MATCH expression from whitespace-delimited literals."""

    normalized = _normalize_text(text)
    if not normalized:
        raise RetrievalQueryError("empty-query", "retrieval query must not be empty")
    return _fts_match_for_literals(tuple(normalized.split(" ")))


def fts_candidate_scores(
    catalog: KnowledgeCatalog,
    text: str,
    *,
    candidate_version_ids: tuple[str, ...],
) -> tuple[FtsCandidateScore, ...]:
    """Score an allowlisted published-card set without returning card payloads.

    Near-duplicate review owns its policy and thresholds; this helper only exposes
    the existing FTS5 BM25 lane with deterministic membership and ordering.
    """

    normalized = _normalize_text(text)
    if not normalized:
        raise RetrievalQueryError("empty-query", "retrieval query must not be empty")
    candidate_ids = tuple(sorted(set(candidate_version_ids)))
    if any(_SAFE_ID.fullmatch(version_id) is None for version_id in candidate_ids):
        raise RetrievalQueryError(
            "invalid-candidate-id", "FTS candidate version ID is invalid"
        )
    if not candidate_ids:
        return ()
    tokens = tuple(dict.fromkeys(normalized.split(" ")))
    rows = catalog.connection.execute(
        """
        SELECT card_fts.version_id, bm25(card_fts)
        FROM card_fts
        WHERE card_fts MATCH ?
          AND card_fts.version_id IN (SELECT value FROM json_each(?))
        ORDER BY bm25(card_fts), card_fts.version_id
        """,
        (
            _fts_match_for_literals(tokens),
            _canonical(candidate_ids),
        ),
    ).fetchall()
    return tuple(
        FtsCandidateScore(card_version_id=str(row[0]), bm25=float(row[1]))
        for row in rows
    )


def _validated_tag_tuple(value: object, *, code: str, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RetrievalQueryError(code, f"{label} must be a list or tuple")
    tags = tuple(value)
    if len(tags) > _MAX_TAG_FILTERS:
        raise RetrievalQueryError(
            "too-many-tag-filters", "retrieval query has too many tag filters"
        )
    if (
        any(
            not isinstance(tag_id, str)
            or _SAFE_ID.fullmatch(tag_id) is None
            for tag_id in tags
        )
        or len(set(tags)) != len(tags)
    ):
        raise RetrievalQueryError(code, f"{label} must be unique non-empty strings")
    return tags


def _normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _fts_match_for_literals(literals: tuple[str, ...]) -> str:
    return " OR ".join(
        f'"{literal.replace(chr(34), chr(34) * 2)}"' for literal in literals
    )


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _query_digest(
    normalized_text: str,
    required_tags: tuple[str, ...],
    excluded_tags: tuple[str, ...],
    audience_tag_id: str | None,
    difficulty_tag_id: str | None,
    limit: int,
    resolved_vocabulary_version_id: str,
    index_snapshot_id: str | None,
) -> str:
    return _sha256_text(
        _canonical(
            {
                "audience_tag_id": audience_tag_id,
                "difficulty_tag_id": difficulty_tag_id,
                "excluded_tag_ids": excluded_tags,
                "index_snapshot_id": index_snapshot_id,
                "limit": limit,
                "required_tag_ids": required_tags,
                "resolved_vocabulary_version_id": resolved_vocabulary_version_id,
                "text": normalized_text,
            }
        )
    )


def retrieval_query_contract(query: RetrievalQuery) -> dict[str, object]:
    """Return the exact normalized filter/query contract used by retrieval."""

    if not isinstance(query, RetrievalQuery):
        raise RetrievalQueryError("invalid-query", "retrieval requires a RetrievalQuery")
    return {
        "text": _normalize_text(query.text),
        "required_tag_ids": tuple(sorted(query.required_tag_ids)),
        "excluded_tag_ids": tuple(sorted(query.excluded_tag_ids)),
        "audience_tag_id": query.audience_tag_id,
        "difficulty_tag_id": query.difficulty_tag_id,
        "limit": query.limit,
        "index_snapshot_id": query.index_snapshot_id,
    }


def retrieval_query_digest(
    query: RetrievalQuery,
    *,
    resolved_vocabulary_version_id: str,
) -> str:
    """Digest one exact query contract with its resolved vocabulary scope."""

    contract = retrieval_query_contract(query)
    return _query_digest(
        str(contract["text"]),
        tuple(contract["required_tag_ids"]),  # type: ignore[arg-type]
        tuple(contract["excluded_tag_ids"]),  # type: ignore[arg-type]
        contract["audience_tag_id"],  # type: ignore[arg-type]
        contract["difficulty_tag_id"],  # type: ignore[arg-type]
        int(contract["limit"]),
        resolved_vocabulary_version_id,
        contract["index_snapshot_id"],  # type: ignore[arg-type]
    )


def _filtered_candidate_digest(candidates: tuple[_Candidate, ...]) -> str:
    return _sha256_text(
        _canonical(
            tuple(
                {
                    "card_content_digest": item.card_content_digest,
                    "card_version_id": item.card_version_id,
                    "tag_ids": item.tag_ids,
                }
                for item in candidates
            )
        )
    )


def _provider_matches_snapshot(provider: object, snapshot: IndexSnapshot) -> bool:
    expected = snapshot.provider_identity
    if expected is None:
        return False
    try:
        observed = _provider_record(provider)
    except IndexSnapshotIntegrityError:
        return False
    return observed == expected


def _retrieval_evidence(
    *,
    query_digest: str,
    candidates: tuple[_Candidate, ...],
    candidate_digest: str,
    fts_lane: tuple[_FtsLaneItem, ...],
    semantic_lane: tuple[tuple[str, float], ...],
    fused: tuple[object, ...],
    returned_ids: tuple[str, ...],
    snapshot: IndexSnapshot | None,
    degraded_code: str | None,
    short_literal_count: int,
    resolved_vocabulary_version_id: str,
) -> EvidenceObject:
    policy_core = {
        "filter_before_rank": True,
        "fts_order": "bm25-asc-cardVersionId-asc",
        "id": RRF_POLICY_ID,
        "k": RRF_K,
        "limit_after_fusion": True,
        "semantic_order": "score-desc-cardVersionId-asc",
        "tie_break": "cardVersionId-asc",
        "weights": {"fts": 1, "semantic": 1},
    }
    policy = {"digest": _sha256_text(_canonical(policy_core)), **policy_core}
    fts_by_id = {
        item.card_version_id: (rank, item)
        for rank, item in enumerate(fts_lane, 1)
    }
    semantic_by_id = {
        item[0]: (rank, item[1]) for rank, item in enumerate(semantic_lane, 1)
    }
    fused_by_id = {item.card_version_id: item for item in fused}  # type: ignore[attr-defined]
    fts_facts = tuple(
        {
            "card_version_id": item.card_version_id,
            "matched_via": item.matched_via,
            "rank": rank,
            "score": item.bm25,
        }
        for rank, item in enumerate(fts_lane, 1)
    )
    semantic_facts = tuple(
        {
            "card_version_id": item[0],
            "rank": rank,
            "score": item[1],
        }
        for rank, item in enumerate(semantic_lane, 1)
    )
    fused_facts = tuple(
        {
            "card_version_id": item.card_version_id,  # type: ignore[attr-defined]
            "fts_rank": item.fts_rank,  # type: ignore[attr-defined]
            "rrf_score": item.score,  # type: ignore[attr-defined]
            "semantic_rank": item.semantic_rank,  # type: ignore[attr-defined]
        }
        for item in fused
    )
    lanes = []
    for card_version_id in returned_ids:
        fts = fts_by_id.get(card_version_id)
        semantic = semantic_by_id.get(card_version_id)
        fused_item = fused_by_id.get(card_version_id)
        lanes.append(
            {
                "card_version_id": card_version_id,
                "fts_rank": None if fts is None else fts[0],
                "fts_score": None if fts is None else fts[1].bm25,
                "semantic_rank": None if semantic is None else semantic[0],
                "semantic_score": None if semantic is None else semantic[1],
                "rrf_score": None if fused_item is None else fused_item.score,
            }
        )
    model: dict[str, object] | None = None
    if snapshot is not None and snapshot.provider_identity is not None:
        identity = snapshot.provider_identity
        model = {
            "cache_digest": identity.cache_digest,
            "dimension": identity.dimension,
            "document_encoding_policy": snapshot.document_encoding_policy,
            "artifact_repository": identity.artifact_repository,
            "artifact_revision": identity.artifact_revision,
            "generation_digest": identity.generation_digest,
            "manifest_digest": identity.model_manifest_digest,
            "model_file_sha256s": list(identity.model_file_sha256s),
            "model_id": identity.model_id,
            "model_revision": identity.model_revision,
            "package_name": "fastembed",
            "package_version": identity.provider_version,
            "provider": identity.provider,
            "provider_version": identity.provider_version,
            "query_encoding_policy": snapshot.query_encoding_policy,
            "runtime_digest": identity.runtime_digest,
            "wheel_set_digest": identity.wheel_set_digest,
        }
    snapshot_digest = (
        _sha256_text(
            _canonical(
                {
                    "candidate_digest": candidate_digest,
                    "mode": "live-fts-degraded",
                    "schema_version": INDEX_SCHEMA_VERSION,
                }
            )
        )
        if snapshot is None
        else snapshot.snapshot_digest
    )
    checks = [
        EvidenceCheck(
            code=("hybrid-index-verified" if degraded_code is None else degraded_code),
            status=("passed" if degraded_code is None else "warning"),
            message=(
                "Both verified snapshot lanes were fused with the pinned policy"
                if degraded_code is None
                else "Semantic retrieval was unavailable; verified FTS facts were used"
            ),
        )
    ]
    if short_literal_count:
        checks.append(
            EvidenceCheck(
                code="short-literal-branch",
                status="passed",
                message="Short literals used a bound substring predicate",
                details={"literal_count": short_literal_count},
            )
        )
    output_summary = {
        "filtered_candidate_count": len(candidates),
        "filtered_candidate_digest": candidate_digest,
        "fts_lane_count": len(fts_lane),
        "fts_lane_digest": _sha256_text(_canonical(fts_facts)),
        "fused_count": len(fused),
        "fused_digest": _sha256_text(_canonical(fused_facts)),
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "index_snapshot_digest": snapshot_digest,
        "index_snapshot_id": None if snapshot is None else snapshot.index_snapshot_id,
        "lanes": lanes,
        "lanes_truncated": len(fused) > len(returned_ids),
        "model": model,
        "policy": policy,
        "returned_hit_count": len(returned_ids),
        "returned_hit_order_digest": _sha256_text(_canonical(returned_ids)),
        "semantic_lane_count": len(semantic_lane),
        "semantic_lane_digest": _sha256_text(_canonical(semantic_facts)),
    }
    identity_payload = {
        "candidate_digest": candidate_digest,
        "degraded_code": degraded_code,
        "output": output_summary,
        "query_digest": query_digest,
        "resolved_vocabulary_version_id": resolved_vocabulary_version_id,
    }
    return EvidenceObject(
        evidence_id="retrieval-" + _sha256_text(_canonical(identity_payload)),
        kind="retrieval",
        status="verified" if degraded_code is None else "degraded",
        input_summary={
            "query_digest": query_digest,
            "resolved_vocabulary_version_id": resolved_vocabulary_version_id,
            "snapshot_requested": snapshot is not None,
        },
        output_summary=output_summary,
        producer="course-helper/retrieval",
        producer_version="4",
        started_at=_STABLE_EVIDENCE_TIME,
        finished_at=_STABLE_EVIDENCE_TIME,
        duration_ms=0,
        checks=tuple(checks),
    )


__all__ = [
    "FtsCandidateScore",
    "KnowledgeRetriever",
    "RetrievalFailure",
    "RetrievalHit",
    "RetrievalQuery",
    "RetrievalQueryError",
    "RetrievalResult",
    "RetrievalScoreComponents",
    "fts_candidate_scores",
    "retrieval_query_contract",
    "retrieval_query_digest",
    "safe_fts_match",
]
