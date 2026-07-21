from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from course_helper.catalog import CatalogMigrationError, KnowledgeCatalog, canonical_model_json
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    TagVocabularyVersion,
)
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery


LIFECYCLE_MODULE_MISSING = importlib.util.find_spec("course_helper.lifecycle") is None
if not LIFECYCLE_MODULE_MISSING:
    from course_helper.lifecycle import (
        CardLifecycleConflict,
        CardLifecycleTransitionError,
        append_card_lifecycle_event,
        get_card_lifecycle,
        register_card_lifecycle,
        rebuild_card_lifecycle_projection,
        reopen_card_version,
    )


REQUIRES_LIFECYCLE = pytest.mark.skipif(
    LIFECYCLE_MODULE_MISSING,
    reason="Task 2 lifecycle module is not implemented",
)
NOW = datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="system", actor_id="lifecycle-tests")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_v1_database(
    path: Path,
    *,
    card_status: str = "published",
    card_version_id: str = "legacy-card-v1",
) -> tuple[KnowledgeCardVersion, str, str]:
    migration = (
        Path(__file__).parents[1]
        / "course_helper"
        / "migrations"
        / "0001_knowledge_catalog.sql"
    ).read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    connection.executescript(migration)
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        (NOW.isoformat(),),
    )
    source = SourceAssetVersion(
        logical_id="legacy-source",
        version_id="legacy-source-v1",
        revision=1,
        content_digest=_digest("legacy-source"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="legacy.md"),
        display_name="legacy.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=64,
        extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id="legacy-chunk-1",
        source_version_id=source.version_id,
        ordinal=0,
        modality="text",
        language="en",
        normalized_text="legacy lifecycle retrieval content",
        content_digest=_digest("legacy chunk"),
        locator=ChunkLocator(kind="markdown-section", ast_path=(0,)),
    )
    vocabulary = TagVocabularyVersion(
        logical_id="legacy-vocabulary",
        version_id="legacy-vocabulary-v1",
        revision=1,
        content_digest=_digest("legacy vocabulary"),
        created_at=NOW,
        created_by=ACTOR,
        dimensions=(),
    )
    card = KnowledgeCardVersion(
        logical_id="legacy-card",
        version_id=card_version_id,
        revision=1,
        content_digest=_digest(f"legacy card:{card_version_id}"),
        created_at=NOW,
        created_by=ACTOR,
        main_type_id="concept",
        title="Legacy lifecycle",
        learning_objective="Explain lifecycle migration",
        content_ast=(CardContentNode(type="paragraph", text="legacy lifecycle content"),),
        suggested_minutes=5,
        vocabulary_version_id=vocabulary.version_id,
        chunk_citations=(
            ChunkCitation(chunk_id=chunk.chunk_id, source_version_id=source.version_id),
        ),
        status=card_status,
    )
    for table, values in (
        (
            "sources",
            (
                source.version_id,
                source.logical_id,
                source.revision,
                source.content_digest,
                canonical_model_json(source),
            ),
        ),
        (
            "chunks",
            (
                chunk.chunk_id,
                chunk.source_version_id,
                chunk.ordinal,
                chunk.content_digest,
                canonical_model_json(chunk),
            ),
        ),
        (
            "tag_vocabularies",
            (vocabulary.version_id, vocabulary.content_digest, canonical_model_json(vocabulary)),
        ),
        (
            "cards",
            (
                card.version_id,
                card.logical_id,
                card.revision,
                card.status,
                card.content_digest,
                canonical_model_json(card),
            ),
        ),
    ):
        placeholders = ", ".join("?" for _ in values)
        connection.execute(f"INSERT INTO {table} VALUES ({placeholders})", values)
    if card_status == "published":
        connection.execute(
            "INSERT INTO card_fts(version_id,title,learning_objective,body,chunk_text,projected_text) "
            "VALUES (?,?,?,?,?,?)",
            (
                card.version_id,
                card.title,
                card.learning_objective,
                "legacy lifecycle content",
                chunk.normalized_text,
                "legacy lifecycle retrieval content",
            ),
        )
    connection.commit()
    raw = connection.execute(
        "SELECT payload_json, content_digest FROM cards WHERE version_id = ?",
        (card.version_id,),
    ).fetchone()
    connection.close()
    return card, raw[0], raw[1]


def test_v1_migration_backfills_projection_without_rewriting_immutable_card(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    card, original_payload, original_digest = _create_v1_database(database)

    with KnowledgeCatalog.open(database) as catalog:
        versions = tuple(
            row[0]
            for row in catalog.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        assert versions == (1, 2, 3, 4, 5, 6, 7, 8)
        stored = catalog.connection.execute(
            "SELECT status, payload_json, content_digest FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone()
        assert stored == ("published", original_payload, original_digest)
        projection = catalog.connection.execute(
            "SELECT status, suspended, last_sequence FROM card_lifecycle_current "
            "WHERE card_version_id = ?",
            (card.version_id,),
        ).fetchone()
        assert projection == ("published", 0, 1)
        assert catalog.connection.execute(
            "SELECT event_type, status_after FROM card_lifecycle_events "
            "WHERE card_version_id = ?",
            (card.version_id,),
        ).fetchall() == [("backfill", "published")]
        assert catalog.connection.execute(
            "SELECT title, learning_objective, body, chunk_text, projected_text "
            "FROM card_fts WHERE version_id = ?",
            (card.version_id,),
        ).fetchone() == (
            "Legacy lifecycle",
            "Explain lifecycle migration",
            "legacy lifecycle content",
            "legacy lifecycle retrieval content",
            "Legacy lifecycle\nExplain lifecycle migration\n"
            "legacy lifecycle content\nlegacy lifecycle retrieval content",
        )
        result = KnowledgeRetriever(catalog).search(RetrievalQuery(text="legacy lifecycle"))
        assert [hit.card.version_id for hit in result.hits] == [card.version_id]


@REQUIRES_LIFECYCLE
def test_append_only_transitions_keep_card_bytes_and_drive_fts(tmp_path: Path) -> None:
    database = tmp_path / "transitions.sqlite3"
    card, original_payload, original_digest = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        suspended = append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-suspend",
            request_digest=_digest("suspend"),
            event_type="suspend",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        assert suspended.status == "published"
        assert suspended.suspended is True
        assert catalog.connection.execute(
            "SELECT count(*) FROM card_fts WHERE version_id = ?", (card.version_id,)
        ).fetchone()[0] == 0
        assert KnowledgeRetriever(catalog).search(
            RetrievalQuery(text="legacy lifecycle")
        ).hits == ()

        reinstated = append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-reinstate",
            request_digest=_digest("reinstate"),
            event_type="reinstate",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        assert reinstated.suspended is False
        assert catalog.connection.execute(
            "SELECT count(*) FROM card_fts WHERE version_id = ?", (card.version_id,)
        ).fetchone()[0] == 1
        assert [
            hit.card.version_id
            for hit in KnowledgeRetriever(catalog).search(
                RetrievalQuery(text="legacy lifecycle")
            ).hits
        ] == [card.version_id]
        stored = catalog.connection.execute(
            "SELECT status, payload_json, content_digest FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone()
        assert stored == ("published", original_payload, original_digest)


@REQUIRES_LIFECYCLE
def test_event_idempotency_conflict_and_illegal_transition_are_explicit(tmp_path: Path) -> None:
    database = tmp_path / "idempotency.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        arguments = dict(
            card_version_id=card.version_id,
            event_id="event-suspend",
            request_digest=_digest("suspend"),
            event_type="suspend",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        first = append_card_lifecycle_event(catalog.connection, **arguments)
        repeated = append_card_lifecycle_event(catalog.connection, **arguments)
        assert repeated == first
        assert catalog.connection.execute(
            "SELECT count(*) FROM card_lifecycle_events WHERE card_version_id = ?",
            (card.version_id,),
        ).fetchone()[0] == 2
        with pytest.raises(CardLifecycleConflict):
            append_card_lifecycle_event(
                catalog.connection,
                **{**arguments, "request_digest": _digest("different")},
            )
        with pytest.raises(CardLifecycleTransitionError):
            append_card_lifecycle_event(
                catalog.connection,
                **{
                    **arguments,
                    "event_id": "event-suspend-again",
                    "request_digest": _digest("suspend-again"),
                },
            )


@REQUIRES_LIFECYCLE
def test_registration_validates_requests_and_rejects_a_different_existing_event(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registration-validation.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        common = {
            "connection": catalog.connection,
            "card": card,
            "event_id": "different-register-event",
            "request_digest": _digest("register"),
            "occurred_at": NOW,
            "actor_id": "trainer-1",
        }
        with pytest.raises(ValueError, match="request_digest"):
            register_card_lifecycle(**{**common, "request_digest": "bad"})
        with pytest.raises(ValueError, match="actor_id"):
            register_card_lifecycle(**{**common, "actor_id": ""})
        with pytest.raises(ValueError, match="timezone-aware"):
            register_card_lifecycle(
                **{**common, "occurred_at": datetime(2026, 7, 17, 1, 0)}
            )
        with pytest.raises(CardLifecycleConflict, match="different immutable request"):
            register_card_lifecycle(**common)


@REQUIRES_LIFECYCLE
def test_database_enforces_append_only_events_and_immutable_card_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "append-only.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-suspend",
            request_digest=_digest("suspend"),
            event_type="suspend",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.connection.execute(
                "UPDATE card_lifecycle_events SET actor_id = 'tampered' WHERE event_id = ?",
                ("event-suspend",),
            )
        catalog.connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.connection.execute(
                "DELETE FROM card_lifecycle_events WHERE event_id = ?",
                (f"backfill:{card.version_id}",),
            )
        catalog.connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable card bytes"):
            catalog.connection.execute(
                "UPDATE cards SET logical_id = 'tampered' WHERE version_id = ?",
                (card.version_id,),
            )
        catalog.connection.rollback()
        assert catalog.connection.execute(
            "SELECT logical_id FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone()[0] == card.logical_id


@REQUIRES_LIFECYCLE
def test_projection_and_fts_rebuild_replays_events_and_old_course_reopens_with_warning(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rebuild.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-suspend",
            request_digest=_digest("suspend"),
            event_type="suspend",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        catalog.connection.execute("DELETE FROM card_lifecycle_current")
        catalog.connection.execute("DELETE FROM card_fts")
        catalog.connection.commit()
        rebuild_card_lifecycle_projection(catalog.connection)
        state = get_card_lifecycle(catalog.connection, card.version_id)
        assert state.status == "published"
        assert state.suspended is True
        assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 0
        reopened = reopen_card_version(catalog.connection, card.version_id)
        assert reopened.card.status == "published"
        assert reopened.suspended is True
        assert "CARD_VERSION_SUSPENDED" in reopened.warnings


@REQUIRES_LIFECYCLE
def test_projection_rebuild_rejects_event_payloads_that_disagree_with_event_type(
    tmp_path: Path,
) -> None:
    database = tmp_path / "corrupt-rebuild.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-suspend",
            request_digest=_digest("suspend"),
            event_type="suspend",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        catalog.connection.execute(
            "DROP TRIGGER IF EXISTS card_lifecycle_events_append_only_update"
        )
        catalog.connection.execute(
            "UPDATE card_lifecycle_events SET status_after = 'archived' WHERE event_id = ?",
            ("event-suspend",),
        )
        catalog.connection.commit()

        with pytest.raises(CardLifecycleTransitionError, match="does not match event type"):
            rebuild_card_lifecycle_projection(catalog.connection)

        assert get_card_lifecycle(catalog.connection, card.version_id).status == "published"


@REQUIRES_LIFECYCLE
def test_projection_rebuild_rejects_a_card_without_an_initial_event(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-initial-event.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database) as catalog:
        catalog.connection.execute(
            "DROP TRIGGER IF EXISTS card_lifecycle_events_append_only_delete"
        )
        catalog.connection.execute("DELETE FROM card_lifecycle_current")
        catalog.connection.execute(
            "DELETE FROM card_lifecycle_events WHERE card_version_id = ?",
            (card.version_id,),
        )
        catalog.connection.commit()

        with pytest.raises(CardLifecycleTransitionError, match="missing an initial event"):
            rebuild_card_lifecycle_projection(catalog.connection)


@REQUIRES_LIFECYCLE
def test_publish_supersede_and_archive_are_projected_without_payload_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "status-transitions.sqlite3"
    card, original_payload, original_digest = _create_v1_database(
        database,
        card_status="review",
    )
    with KnowledgeCatalog.open(database) as catalog:
        published = append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-publish",
            request_digest=_digest("publish"),
            event_type="publish",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        assert published.status == "published"
        assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 1
        superseded = append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-supersede",
            request_digest=_digest("supersede"),
            event_type="supersede",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        assert superseded.status == "superseded"
        archived = append_card_lifecycle_event(
            catalog.connection,
            card_version_id=card.version_id,
            event_id="event-archive",
            request_digest=_digest("archive"),
            event_type="archive",
            occurred_at=NOW,
            actor_id="trainer-1",
        )
        assert archived.status == "archived"
        assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT status, payload_json, content_digest FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone() == ("review", original_payload, original_digest)
        with pytest.raises(CardLifecycleTransitionError):
            append_card_lifecycle_event(
                catalog.connection,
                card_version_id=card.version_id,
                event_id="event-republish-archived",
                request_digest=_digest("republish archived"),
                event_type="publish",
                occurred_at=NOW,
                actor_id="trainer-1",
            )


@REQUIRES_LIFECYCLE
def test_concurrent_events_receive_contiguous_per_card_sequences(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.sqlite3"
    card, _, _ = _create_v1_database(database)
    with KnowledgeCatalog.open(database):
        pass

    def write(event_type: str) -> None:
        connection = sqlite3.connect(database, timeout=30.0)
        try:
            append_card_lifecycle_event(
                connection,
                card_version_id=card.version_id,
                event_id=f"event-{event_type}",
                request_digest=_digest(event_type),
                event_type=event_type,
                occurred_at=NOW,
                actor_id="trainer-1",
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(write, event_type) for event_type in ("suspend", "archive"))
        for future in futures:
            future.result(timeout=10)

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT sequence FROM card_lifecycle_events WHERE card_version_id = ? "
            "ORDER BY sequence",
            (card.version_id,),
        ).fetchall() == [(1,), (2,), (3,)]
    finally:
        connection.close()


@REQUIRES_LIFECYCLE
def test_failed_migration_rolls_back_and_allows_explicit_read_only_recovery(
    tmp_path: Path,
) -> None:
    database = tmp_path / "broken-migration.sqlite3"
    card, original_payload, original_digest = _create_v1_database(database)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE card_lifecycle_events(unexpected TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(CatalogMigrationError):
        KnowledgeCatalog.open(database)

    inspection = sqlite3.connect(database)
    try:
        assert inspection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert inspection.execute(
            "SELECT payload_json, content_digest FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone() == (original_payload, original_digest)
        assert inspection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='card_lifecycle_current'"
        ).fetchone() is None
    finally:
        inspection.close()

    with KnowledgeCatalog.open_read_only(database) as recovered:
        assert recovered.read_only is True
        reopened = reopen_card_version(recovered.connection, card.version_id)
        assert reopened.card.version_id == card.version_id
        assert "LIFECYCLE_MIGRATION_PENDING" in reopened.warnings


@REQUIRES_LIFECYCLE
def test_malformed_partial_current_table_is_not_treated_as_a_complete_lifecycle_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "partial-current.sqlite3"
    card, _, _ = _create_v1_database(database)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE card_lifecycle_current(unexpected TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(CatalogMigrationError):
        KnowledgeCatalog.open(database)

    with KnowledgeCatalog.open_read_only(database) as recovered:
        reopened = reopen_card_version(recovered.connection, card.version_id)
        assert reopened.card.version_id == card.version_id
        assert reopened.warnings == ("LIFECYCLE_MIGRATION_PENDING",)
