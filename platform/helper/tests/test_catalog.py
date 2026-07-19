import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from course_helper.catalog import (
    CatalogMigrationError,
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    SourceRegistrationInput,
    card_parent_version_ids,
    canonical_model_json,
    register_or_reuse_source,
    transition_card_status,
)
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceObject, LineageEdge
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    ReviewTask,
    TagAssignment,
    TagDimension,
    TagValue,
    TagVocabularyVersion,
)
from course_helper.domain.sources import (
    ChunkLocator,
    DatasetAssetVersion,
    DatasetColumn,
    ExtractedChunk,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.lifecycle import append_card_lifecycle_event
from course_helper.source_roots import candidate_version_id


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def actor_fixture() -> ActorRef:
    return ActorRef(actor_type="service", actor_id="catalog-tests", display_name="Catalog tests")


def source_version_fixture(**overrides: object) -> SourceAssetVersion:
    values: dict[str, object] = {
        "logical_id": "source-logical-1",
        "version_id": "source-version-1",
        "revision": 1,
        "content_digest": "a" * 64,
        "created_at": NOW,
        "created_by": actor_fixture(),
        "locator": SourceLocator(root_id="demo", relative_path="AI.pptx"),
        "display_name": "AI.pptx",
        "source_kind": "pptx",
        "media_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "byte_size": 1024,
        "modified_at": NOW,
        "extraction_status": "registered",
    }
    values.update(overrides)
    return SourceAssetVersion(**values)


def source_input(root_id: str, relative_path: str, *, digest: str) -> SourceRegistrationInput:
    return SourceRegistrationInput(
        locator=SourceLocator(root_id=root_id, relative_path=relative_path),
        display_name=Path(relative_path).name,
        source_kind="pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        byte_size=1024,
        modified_at=NOW,
        content_digest=digest,
        created_at=NOW,
        created_by=actor_fixture(),
        extraction_status="registered",
    )


def register_concurrently(
    database: Path,
    inputs: tuple[SourceRegistrationInput, SourceRegistrationInput],
) -> tuple[tuple[SourceAssetVersion, tuple[str, ...]], ...]:
    start = Barrier(2)
    deferred_insert = Barrier(2)

    def worker(
        registration_input: SourceRegistrationInput,
    ) -> tuple[SourceAssetVersion, tuple[str, ...]]:
        statements: list[str] = []
        immediate_writer = False

        def trace(statement: str) -> None:
            nonlocal immediate_writer
            normalized = " ".join(statement.upper().split())
            statements.append(normalized)
            if normalized == "BEGIN IMMEDIATE":
                immediate_writer = True
            if normalized.startswith("INSERT INTO SOURCES") and not immediate_writer:
                deferred_insert.wait(timeout=10)

        with KnowledgeCatalog.open(database) as catalog:
            catalog.connection.set_trace_callback(trace)
            start.wait(timeout=10)
            registered = register_or_reuse_source(catalog, registration_input)
            catalog.connection.set_trace_callback(None)
            return registered, tuple(statements)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(worker, item) for item in inputs)
        return tuple(future.result(timeout=15) for future in futures)


def evidence_fixture(**overrides: object) -> EvidenceObject:
    values: dict[str, object] = {
        "evidence_id": "evidence-1",
        "kind": "validation",
        "status": "verified",
        "input_summary": {"alpha": 1, "beta": 2},
        "output_summary": {"accepted": True},
        "producer": "course-helper/tests",
        "started_at": NOW,
        "finished_at": NOW,
    }
    values.update(overrides)
    return EvidenceObject(**values)


def vocabulary_fixture(
    *,
    version_id: str = "vocabulary-v1",
    revision: int = 1,
    digest: str = "b" * 64,
) -> TagVocabularyVersion:
    return TagVocabularyVersion(
        logical_id="vocabulary-main",
        version_id=version_id,
        revision=revision,
        content_digest=digest,
        supersedes_version_id="vocabulary-v1" if revision > 1 else None,
        created_at=NOW + timedelta(minutes=revision - 1),
        created_by=actor_fixture(),
        dimensions=(
            TagDimension(
                id="difficulty",
                cardinality="one",
                values=(
                    TagValue(
                        id="difficulty:beginner",
                        labels={"en": "Beginner"},
                        status="active",
                    ),
                ),
            ),
        ),
    )


def chunk_fixture(**overrides: object) -> ExtractedChunk:
    values: dict[str, object] = {
        "chunk_id": "chunk-version-1",
        "source_version_id": "source-version-1",
        "ordinal": 0,
        "modality": "slide",
        "language": "en",
        "normalized_text": "Models can make unsupported claims.",
        "content_digest": "c" * 64,
        "locator": ChunkLocator(kind="pptx-slide", slide_number=1),
        "slide_text": "Model boundaries",
    }
    values.update(overrides)
    return ExtractedChunk(**values)


def visual_fixture(**overrides: object) -> VisualAssetVersion:
    values: dict[str, object] = {
        "logical_id": "visual-logical-1",
        "version_id": "visual-version-1",
        "revision": 1,
        "content_digest": "d" * 64,
        "created_at": NOW,
        "created_by": actor_fixture(),
        "media_type": "image/png",
        "width": 640,
        "height": 360,
        "alt_text": "Boundary diagram",
        "license_status": "source-provided",
        "authenticity": "source-provided",
    }
    values.update(overrides)
    return VisualAssetVersion(**values)


def dataset_fixture(**overrides: object) -> DatasetAssetVersion:
    values: dict[str, object] = {
        "logical_id": "dataset-logical-1",
        "version_id": "dataset-version-1",
        "revision": 1,
        "content_digest": "e" * 64,
        "created_at": NOW,
        "created_by": actor_fixture(),
        "locator": SourceLocator(root_id="demo", relative_path="metrics.csv"),
        "format": "csv",
        "row_count": 1,
        "columns": (DatasetColumn(name="metric", data_type="VARCHAR", nullable=False),),
        "grain": "one row per metric",
        "review_status": "ready",
        "evidence": evidence_fixture(evidence_id="dataset-evidence"),
    }
    values.update(overrides)
    return DatasetAssetVersion(**values)


def card_fixture(
    *,
    version_id: str = "card-version-1",
    revision: int = 1,
    vocabulary_version_id: str = "vocabulary-v1",
    tag_id: str = "difficulty:beginner",
    **overrides: object,
) -> KnowledgeCardVersion:
    values: dict[str, object] = {
        "logical_id": "card-logical-1",
        "version_id": version_id,
        "revision": revision,
        "content_digest": "f" * 64,
        "supersedes_version_id": "card-version-1" if revision > 1 else None,
        "created_at": NOW + timedelta(minutes=revision - 1),
        "created_by": actor_fixture(),
        "main_type_id": "concept",
        "title": "Language-model boundaries",
        "learning_objective": "Explain one evidence-backed model limitation.",
        "content_ast": (
            CardContentNode(type="paragraph", text="Models can make unsupported claims."),
        ),
        "suggested_minutes": 5,
        "vocabulary_version_id": vocabulary_version_id,
        "tag_assignments": (
            TagAssignment(
                vocabulary_version_id=vocabulary_version_id,
                dimension_id="difficulty",
                tag_id=tag_id,
            ),
        ),
        "status": "review",
    }
    values.update(overrides)
    return KnowledgeCardVersion(**values)


def test_catalog_rejects_a_different_payload_for_existing_version(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        source = source_version_fixture()
        catalog.insert_source(source)
        catalog.insert_source(source)

        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_source(source.model_copy(update={"byte_size": source.byte_size + 1}))


def test_same_locator_and_digest_reuses_source_version_without_changing_created_at(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        first = register_or_reuse_source(
            catalog,
            source_input("demo", "AI.pptx", digest="a" * 64),
        )
        later_input = source_input("demo", "AI.pptx", digest="a" * 64).model_copy(
            update={"created_at": NOW + timedelta(days=1)},
        )
        second = register_or_reuse_source(catalog, later_input)

        assert second == first
        assert second.version_id == first.version_id
        assert second.revision == 1
        assert second.created_at == first.created_at == NOW


def test_changed_source_digest_creates_next_revision_and_supersedes(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        first = register_or_reuse_source(
            catalog,
            source_input("demo", "AI.pptx", digest="a" * 64),
        )
        changed_input = source_input("demo", "AI.pptx", digest="b" * 64).model_copy(
            update={"created_at": NOW + timedelta(minutes=1)},
        )
        changed = register_or_reuse_source(catalog, changed_input)

        assert changed.logical_id == first.logical_id
        assert changed.version_id != first.version_id
        assert changed.revision == 2
        assert changed.supersedes_version_id == first.version_id
        assert catalog.latest_source(first.logical_id) == changed


def test_concurrent_same_digest_registration_is_atomic_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    with KnowledgeCatalog.open(database):
        pass
    first_input = source_input("demo", "AI.pptx", digest="a" * 64)
    second_input = first_input.model_copy(update={"created_at": NOW + timedelta(minutes=1)})

    outcomes = register_concurrently(database, (first_input, second_input))
    registrations = tuple(outcome[0] for outcome in outcomes)

    assert registrations[0] == registrations[1]
    assert registrations[0].revision == 1
    for _, statements in outcomes:
        assert statements.index("BEGIN IMMEDIATE") < next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT PAYLOAD_JSON FROM SOURCES")
        )
    with KnowledgeCatalog.open(database) as catalog:
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1


def test_concurrent_changed_digests_form_one_revision_chain(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    with KnowledgeCatalog.open(database) as catalog:
        original = register_or_reuse_source(
            catalog,
            source_input("demo", "AI.pptx", digest="a" * 64),
        )
    inputs = (
        source_input("demo", "AI.pptx", digest="b" * 64).model_copy(
            update={"created_at": NOW + timedelta(minutes=1)}
        ),
        source_input("demo", "AI.pptx", digest="c" * 64).model_copy(
            update={"created_at": NOW + timedelta(minutes=2)}
        ),
    )

    outcomes = register_concurrently(database, inputs)
    registrations = tuple(outcome[0] for outcome in outcomes)

    assert {registration.revision for registration in registrations} == {2, 3}
    assert len({registration.version_id for registration in registrations}) == 2
    for _, statements in outcomes:
        assert "BEGIN IMMEDIATE" in statements
    with KnowledgeCatalog.open(database) as catalog:
        rows = catalog.connection.execute(
            "SELECT payload_json FROM sources ORDER BY revision"
        ).fetchall()
        versions = tuple(SourceAssetVersion.model_validate_json(row[0]) for row in rows)
        assert tuple(version.revision for version in versions) == (1, 2, 3)
        assert versions[0] == original
        assert versions[1].supersedes_version_id == versions[0].version_id
        assert versions[2].supersedes_version_id == versions[1].version_id
        assert catalog.latest_source(original.logical_id) == versions[2]


def test_registration_does_not_commit_an_existing_transaction(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.connection.execute("BEGIN")

        with pytest.raises(RuntimeError, match="active transaction"):
            register_or_reuse_source(
                catalog,
                source_input("demo", "AI.pptx", digest="a" * 64),
            )

        assert catalog.connection.in_transaction
        catalog.connection.rollback()
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


def test_registration_rolls_back_a_failed_immediate_transaction(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.connection.execute(
            """
            CREATE TRIGGER reject_source_insert
            BEFORE INSERT ON sources
            BEGIN
                SELECT RAISE(ABORT, 'forced registration failure');
            END
            """
        )
        catalog.connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="forced registration failure"):
            register_or_reuse_source(
                catalog,
                source_input("demo", "AI.pptx", digest="a" * 64),
            )

        assert not catalog.connection.in_transaction
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        catalog.connection.execute("DROP TRIGGER reject_source_insert")
        registered = register_or_reuse_source(
            catalog,
            source_input("demo", "AI.pptx", digest="a" * 64),
        )
        assert registered.revision == 1


def test_atomic_write_rolls_back_all_rows_after_a_late_insert_failure(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.connection.execute(
            """
            CREATE TRIGGER reject_second_chunk
            BEFORE INSERT ON chunks
            WHEN NEW.ordinal = 1
            BEGIN
                SELECT RAISE(ABORT, 'forced second chunk failure');
            END
            """
        )
        catalog.connection.commit()
        before = {
            table: catalog.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("sources", "chunks", "visuals", "datasets", "evidence")
        }

        with pytest.raises(sqlite3.IntegrityError, match="forced second chunk failure"):
            with catalog.atomic_write():
                catalog.insert_source(source_version_fixture())
                catalog.insert_chunk(chunk_fixture())
                catalog.insert_chunk(
                    chunk_fixture(
                        chunk_id="chunk-version-2",
                        ordinal=1,
                        content_digest="2" * 64,
                        locator=ChunkLocator(kind="pptx-slide", slide_number=2),
                    )
                )

        after = {
            table: catalog.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in before
        }
        assert after == before
        assert not catalog.connection.in_transaction


def test_atomic_write_supports_nested_catalog_writes(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        with catalog.atomic_write():
            catalog.insert_source(source_version_fixture())
            with catalog.atomic_write():
                catalog.insert_chunk(chunk_fixture())

        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert catalog.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1
        assert not catalog.connection.in_transaction


def test_atomic_write_does_not_commit_an_unknown_caller_transaction(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.connection.execute("BEGIN")
        catalog.connection.execute(
            "INSERT INTO sources(version_id, logical_id, revision, content_digest, payload_json) "
            "VALUES ('external', 'external', 1, ?, '{}')",
            ("0" * 64,),
        )

        with pytest.raises(RuntimeError, match="active transaction"):
            with catalog.atomic_write():
                pass

        assert catalog.connection.in_transaction
        catalog.connection.rollback()
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


def test_open_applies_and_validates_migration_with_foreign_keys_and_fts5(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    with KnowledgeCatalog.open(database) as catalog:
        assert catalog.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM pragma_module_list WHERE name='fts5'"
        ).fetchone()[0] == 1
        columns = {
            row[1]
            for row in catalog.connection.execute("PRAGMA table_info('card_fts')").fetchall()
        }
        assert "projected_text" in columns
        assert catalog.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]

    with KnowledgeCatalog.open(database) as reopened:
        assert reopened.connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 7


def test_open_rejects_an_unsupported_migration_version(tmp_path: Path) -> None:
    database = tmp_path / "future.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (2, NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    with pytest.raises(CatalogMigrationError, match="unsupported migration versions"):
        KnowledgeCatalog.open(database)


def test_unique_logical_revision_indexes_cover_all_version_tables(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        vocabulary = vocabulary_fixture()
        catalog.insert_vocabulary(vocabulary)

        cases = (
            (catalog.insert_source, source_version_fixture(), "source-version-conflict"),
            (catalog.insert_card, card_fixture(), "card-version-conflict"),
            (catalog.insert_visual, visual_fixture(), "visual-version-conflict"),
            (catalog.insert_dataset, dataset_fixture(), "dataset-version-conflict"),
        )
        for insert, model, conflicting_version_id in cases:
            insert(model)
            with pytest.raises(sqlite3.IntegrityError):
                insert(model.model_copy(update={"version_id": conflicting_version_id}))


def test_chunk_foreign_key_and_immutable_payload_are_enforced(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        chunk = chunk_fixture()
        with pytest.raises(sqlite3.IntegrityError):
            catalog.insert_chunk(chunk)
        assert catalog.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0

        catalog.insert_source(source_version_fixture())
        catalog.insert_chunk(chunk)
        catalog.insert_chunk(chunk)
        assert catalog.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1
        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_chunk(chunk.model_copy(update={"normalized_text": "Changed"}))


def test_tag_ids_are_scoped_to_vocabulary_version_and_cards_are_searchable(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        first = vocabulary_fixture()
        second = vocabulary_fixture(version_id="vocabulary-v2", revision=2, digest="c" * 64)
        catalog.insert_vocabulary(first)
        catalog.insert_vocabulary(second)

        assert catalog.connection.execute(
            "SELECT count(*) FROM tag_values WHERE tag_id = ?",
            ("difficulty:beginner",),
        ).fetchone()[0] == 2

        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        card = card_fixture(
            vocabulary_version_id="vocabulary-v2",
            status="published",
            chunk_citations=(
                ChunkCitation(
                    chunk_id=chunk.chunk_id,
                    source_version_id=chunk.source_version_id,
                    quoted_text="Models can make unsupported claims.",
                ),
            ),
        )
        effective = catalog.insert_card(card)
        assert catalog.connection.execute("SELECT count(*) FROM card_tags").fetchone()[0] == 1
        result = catalog.connection.execute(
            "SELECT version_id, projected_text FROM card_fts WHERE card_fts MATCH ?",
            ("boundaries",),
        ).fetchone()
        assert result[0] == effective.version_id
        assert "Language-model boundaries" in result[1]
        assert "unsupported claims" in result[1]


def test_only_published_cards_are_indexed_with_canonical_ast_and_chunk_projection(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture(normalized_text="Cited source truth unique-source-term")
        catalog.insert_chunk(chunk)
        content_ast = (
            CardContentNode(
                type="table",
                rows=(("Cell A", "Cell B"),),
                children=(CardContentNode(type="list-item", text="Nested point"),),
            ),
        )
        published = card_fixture(
            status="published",
            content_ast=content_ast,
            chunk_citations=(
                ChunkCitation(
                    chunk_id=chunk.chunk_id,
                    source_version_id=chunk.source_version_id,
                    quoted_text="Cited source truth",
                ),
            ),
        )
        catalog.insert_card(published)
        catalog.insert_card(
            card_fixture(version_id="card-review-only").model_copy(
                update={"logical_id": "card-review-logical"}
            )
        )
        catalog.insert_card(
            card_fixture(
                version_id="card-archived-only",
                status="archived",
            ).model_copy(update={"logical_id": "card-archived-logical"})
        )

        row = catalog.connection.execute(
            """
            SELECT title, learning_objective, body, chunk_text, projected_text
            FROM card_fts
            """
        ).fetchone()

        assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 1
        assert row[:4] == (
            published.title,
            published.learning_objective,
            "Cell A\nCell B\nNested point",
            chunk.normalized_text,
        )
        assert row[4] == "\n".join(row[:4])


def test_archiving_a_published_card_removes_fts_in_the_status_transaction(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        published = card_fixture(
            status="published",
            chunk_citations=(
                ChunkCitation(
                    chunk_id=chunk.chunk_id,
                    source_version_id=chunk.source_version_id,
                ),
            ),
        )
        effective = catalog.insert_card(published)

        with catalog.connection:
            archived = transition_card_status(
                catalog.connection,
                effective.version_id,
                "archived",
            )

        assert archived.status == "archived"
        stored = catalog.connection.execute(
            "SELECT status, payload_json FROM cards WHERE version_id = ?",
            (effective.version_id,),
        ).fetchone()
        assert stored[0] == "published"
        assert json.loads(stored[1])["status"] == "published"
        assert catalog.connection.execute(
            """
            SELECT status, suspended
            FROM card_lifecycle_current
            WHERE card_version_id = ?
            """,
            (effective.version_id,),
        ).fetchone() == ("archived", 0)
        assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 0


def test_direct_published_revision_supersedes_the_actual_current_version(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        first = catalog.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
        incoming = card_fixture(
            version_id="direct-card-version-2",
            revision=99,
            status="published",
            content_digest="1" * 64,
            supersedes_version_id=None,
            chunk_citations=(citation,),
            title="Direct published revision two",
        )

        second = catalog.insert_card(incoming)

        assert second.revision == 2
        assert second.version_id != incoming.version_id
        assert second.supersedes_version_id == first.version_id
        rows = catalog.connection.execute(
            """
            SELECT cards.version_id,
                   cards.revision,
                   cards.status,
                   cards.payload_json,
                   lifecycle.status
            FROM cards
            JOIN card_lifecycle_current AS lifecycle
              ON lifecycle.card_version_id = cards.version_id
            WHERE cards.logical_id = ?
            ORDER BY cards.revision, cards.version_id
            """,
            (first.logical_id,),
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in rows] == [
            (first.version_id, 1, "published"),
            (second.version_id, 2, "published"),
        ]
        assert [row[4] for row in rows] == ["superseded", "published"]
        assert json.loads(rows[0][3])["status"] == "published"
        assert json.loads(rows[1][3])["supersedes_version_id"] == first.version_id
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts ORDER BY version_id"
        ).fetchall() == [(second.version_id,)]
        assert catalog.insert_card(incoming) == second
        assert catalog.connection.execute(
            "SELECT count(*) FROM cards WHERE logical_id = ?",
            (first.logical_id,),
        ).fetchone()[0] == 2


def test_direct_publish_supersedes_a_suspended_published_revision(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        first = catalog.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=first.version_id,
            event_id="suspend-before-direct-revision",
            request_digest="a" * 64,
            event_type="suspend",
            occurred_at=NOW,
            actor_id="catalog-tests",
        )

        second = catalog.insert_card(
            card_fixture(
                version_id="direct-after-suspended-version",
                revision=2,
                status="published",
                content_digest="5" * 64,
                chunk_citations=(citation,),
                title="Direct revision after suspension",
            )
        )

        assert catalog.connection.execute(
            """
            SELECT status, suspended
            FROM card_lifecycle_current
            WHERE card_version_id = ?
            """,
            (first.version_id,),
        ).fetchone() == ("superseded", 1)
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=first.version_id,
            event_id="reinstate-superseded-direct-revision",
            request_digest="b" * 64,
            event_type="reinstate",
            occurred_at=NOW,
            actor_id="catalog-tests",
        )
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts ORDER BY version_id"
        ).fetchall() == [(second.version_id,)]


def test_direct_publish_replay_is_blocked_while_the_version_is_suspended(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        submitted = card_fixture(
            status="published",
            chunk_citations=(
                ChunkCitation(
                    chunk_id=chunk.chunk_id,
                    source_version_id=chunk.source_version_id,
                ),
            ),
        )
        published = catalog.insert_card(submitted)
        append_card_lifecycle_event(
            catalog.connection,
            card_version_id=published.version_id,
            event_id="suspend-before-direct-replay",
            request_digest="e" * 64,
            event_type="suspend",
            occurred_at=NOW,
            actor_id="catalog-tests",
        )

        with pytest.raises(CatalogReferenceError, match="suspended"):
            catalog.insert_card(submitted)


def test_direct_first_publish_normalizes_submitted_lifecycle_and_version_id(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        submitted = card_fixture(
            version_id="submitted-first-publish",
            revision=99,
            status="published",
            content_digest="0" * 64,
            supersedes_version_id="forged-predecessor",
            chunk_citations=(citation,),
            title="First direct publish",
        )

        effective = catalog.insert_card(submitted)

        expected_version_id = candidate_version_id(
            submitted.logical_id,
            card_parent_version_ids(submitted),
            submitted.content_digest,
        )
        assert effective.revision == 1
        assert effective.supersedes_version_id is None
        assert effective.version_id == expected_version_id
        assert effective.version_id != submitted.version_id
        assert catalog.connection.execute(
            "SELECT version_id, revision, status, payload_json FROM cards"
        ).fetchone() == (
            effective.version_id,
            1,
            "published",
            canonical_model_json(effective),
        )
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts"
        ).fetchall() == [(effective.version_id,)]


def test_direct_publish_after_archive_uses_latest_history_as_predecessor(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        first = catalog.insert_card(
            card_fixture(
                version_id="submitted-archived-first",
                status="published",
                chunk_citations=(citation,),
            )
        )
        with catalog.connection:
            archived = transition_card_status(
                catalog.connection,
                first.version_id,
                "archived",
            )
        submitted = card_fixture(
            version_id="submitted-after-archive",
            revision=99,
            status="published",
            content_digest="1" * 64,
            supersedes_version_id=None,
            chunk_citations=(citation,),
            title="Published after archived history",
        )

        effective = catalog.insert_card(submitted)

        expected_version_id = candidate_version_id(
            submitted.logical_id,
            tuple(
                dict.fromkeys(
                    (*card_parent_version_ids(submitted), archived.version_id)
                )
            ),
            submitted.content_digest,
        )
        assert effective.revision == 2
        assert effective.supersedes_version_id == archived.version_id
        assert effective.version_id == expected_version_id
        assert effective.version_id != submitted.version_id
        assert catalog.connection.execute(
            """
            SELECT cards.version_id,
                   cards.revision,
                   cards.status,
                   lifecycle.status
            FROM cards
            JOIN card_lifecycle_current AS lifecycle
              ON lifecycle.card_version_id = cards.version_id
            ORDER BY cards.revision
            """
        ).fetchall() == [
            (archived.version_id, 1, "published", "archived"),
            (effective.version_id, 2, "published", "published"),
        ]
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts"
        ).fetchall() == [(effective.version_id,)]


def test_direct_first_publish_replay_reuses_its_concrete_version(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        submitted = card_fixture(
            version_id="submitted-first-replay",
            revision=99,
            status="published",
            content_digest="8" * 64,
            supersedes_version_id="ignored-predecessor",
            chunk_citations=(citation,),
            title="First direct replay",
        )
        effective = catalog.insert_card(submitted)

        replay = catalog.insert_card(submitted)

        assert replay == effective
        assert catalog.connection.execute(
            "SELECT count(*) FROM cards"
        ).fetchone()[0] == 1
        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_card(
                submitted.model_copy(
                    update={
                        "title": "Changed first direct replay",
                        "content_digest": "9" * 64,
                    }
                )
            )


def test_direct_publish_uses_latest_history_but_transitions_only_current_published(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        current = catalog.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
        archived = catalog.insert_card(
            card_fixture(
                version_id="historical-archived",
                revision=2,
                status="archived",
                content_digest="2" * 64,
                supersedes_version_id=current.version_id,
                chunk_citations=(citation,),
                title="Archived historical revision",
            )
        )
        latest = catalog.insert_card(
            card_fixture(
                version_id="historical-superseded",
                revision=3,
                status="superseded",
                content_digest="3" * 64,
                supersedes_version_id=archived.version_id,
                chunk_citations=(citation,),
                title="Latest superseded history",
            )
        )
        submitted = card_fixture(
            version_id="submitted-after-mixed-history",
            revision=99,
            status="published",
            content_digest="4" * 64,
            supersedes_version_id=None,
            chunk_citations=(citation,),
            title="Published after mixed history",
        )

        effective = catalog.insert_card(submitted)

        expected_version_id = candidate_version_id(
            submitted.logical_id,
            tuple(
                dict.fromkeys(
                    (*card_parent_version_ids(submitted), latest.version_id)
                )
            ),
            submitted.content_digest,
        )
        assert effective.revision == 4
        assert effective.supersedes_version_id == latest.version_id
        assert effective.version_id == expected_version_id
        rows = catalog.connection.execute(
            """
            SELECT cards.version_id,
                   cards.revision,
                   cards.status,
                   cards.payload_json,
                   lifecycle.status
            FROM cards
            JOIN card_lifecycle_current AS lifecycle
              ON lifecycle.card_version_id = cards.version_id
            ORDER BY cards.revision, cards.version_id
            """
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in rows] == [
            (current.version_id, 1, "published"),
            (archived.version_id, 2, "archived"),
            (latest.version_id, 3, "superseded"),
            (effective.version_id, 4, "published"),
        ]
        assert [row[4] for row in rows] == [
            "superseded",
            "archived",
            "superseded",
            "published",
        ]
        assert json.loads(rows[0][3])["status"] == "published"
        assert json.loads(rows[1][3])["status"] == "archived"
        assert json.loads(rows[2][3])["status"] == "superseded"
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts"
        ).fetchall() == [(effective.version_id,)]


def test_direct_effective_version_id_is_deterministic_for_the_same_predecessor(
    tmp_path: Path,
) -> None:
    def insert_in(database: Path) -> tuple[str, str]:
        with KnowledgeCatalog.open(database) as catalog:
            catalog.insert_vocabulary(vocabulary_fixture())
            catalog.insert_source(source_version_fixture())
            chunk = chunk_fixture()
            catalog.insert_chunk(chunk)
            citation = ChunkCitation(
                chunk_id=chunk.chunk_id,
                source_version_id=chunk.source_version_id,
            )
            catalog.insert_card(
                card_fixture(status="published", chunk_citations=(citation,))
            )
            submitted = card_fixture(
                version_id="submitted-direct-version",
                revision=42,
                status="published",
                content_digest="5" * 64,
                supersedes_version_id=None,
                chunk_citations=(citation,),
                title="Deterministic direct revision",
            )
            effective = catalog.insert_card(submitted)
            assert catalog.insert_card(submitted) == effective
            return submitted.version_id, effective.version_id

    first = insert_in(tmp_path / "first.db")
    second = insert_in(tmp_path / "second.db")

    assert first == second
    assert first[1] != first[0]


def test_direct_publish_after_archive_is_idempotent_and_deterministic_across_databases(
    tmp_path: Path,
) -> None:
    def publish_in(database: Path) -> tuple[str, str, str]:
        with KnowledgeCatalog.open(database) as catalog:
            catalog.insert_vocabulary(vocabulary_fixture())
            catalog.insert_source(source_version_fixture())
            chunk = chunk_fixture()
            catalog.insert_chunk(chunk)
            citation = ChunkCitation(
                chunk_id=chunk.chunk_id,
                source_version_id=chunk.source_version_id,
            )
            first = catalog.insert_card(
                card_fixture(
                    version_id="submitted-cross-db-first",
                    status="published",
                    chunk_citations=(citation,),
                )
            )
            with catalog.connection:
                archived = transition_card_status(
                    catalog.connection,
                    first.version_id,
                    "archived",
                )
            submitted = card_fixture(
                version_id="submitted-cross-db-after-archive",
                revision=99,
                status="published",
                content_digest="5" * 64,
                supersedes_version_id=None,
                chunk_citations=(citation,),
                title="Cross-database archive replay",
            )
            effective = catalog.insert_card(submitted)
            replay = catalog.insert_card(submitted)
            expected_version_id = candidate_version_id(
                submitted.logical_id,
                tuple(
                    dict.fromkeys(
                        (*card_parent_version_ids(submitted), archived.version_id)
                    )
                ),
                submitted.content_digest,
            )
            assert replay == effective
            assert catalog.connection.execute(
                "SELECT count(*) FROM cards"
            ).fetchone()[0] == 2
            return archived.version_id, effective.version_id, expected_version_id

    first = publish_in(tmp_path / "first.db")
    second = publish_in(tmp_path / "second.db")

    assert first == second
    assert first[1] == first[2]
    assert first[1] != "submitted-cross-db-after-archive"


def test_direct_same_submitted_version_id_with_changed_content_is_a_domain_conflict(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        catalog.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
        submitted = card_fixture(
            version_id="submitted-collision",
            revision=2,
            status="published",
            content_digest="6" * 64,
            chunk_citations=(citation,),
            title="Original submitted content",
        )
        effective = catalog.insert_card(submitted)

        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_card(
                submitted.model_copy(
                    update={
                        "title": "Changed content with a reused submitted ID",
                        "content_digest": "7" * 64,
                    }
                )
            )

        assert catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?",
            (effective.version_id,),
        ).fetchone()[0] == canonical_model_json(effective)
        assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 2


def test_direct_nonpublished_identity_keeps_strict_revision_immutability(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        card = card_fixture()
        catalog.insert_card(card)

        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_card(card.model_copy(update={"revision": 2}))


def test_direct_publish_late_index_failure_rolls_back_old_and_new_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import course_helper.catalog as catalog_module

    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        catalog.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        first = catalog.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
        incoming = card_fixture(
            version_id="direct-failing-version-2",
            revision=2,
            status="published",
            content_digest="2" * 64,
            chunk_citations=(citation,),
            title="Direct late failure",
        )
        before = {
            table: catalog.connection.execute(
                f"SELECT * FROM {table} ORDER BY 1"
            ).fetchall()
            for table in (
                "cards",
                "card_tags",
                "card_fts",
                "card_lifecycle_events",
                "card_lifecycle_current",
            )
        }
        original_register = catalog_module.register_card_lifecycle

        def fail_after_registration(connection, card, **kwargs):
            projection = original_register(connection, card, **kwargs)
            if card.title == incoming.title:
                raise sqlite3.OperationalError("forced late FTS failure")
            return projection

        monkeypatch.setattr(
            catalog_module,
            "register_card_lifecycle",
            fail_after_registration,
        )
        statements: list[str] = []
        catalog.connection.set_trace_callback(
            lambda statement: statements.append(" ".join(statement.upper().split()))
        )

        try:
            with pytest.raises(sqlite3.OperationalError, match="forced late FTS failure"):
                catalog.insert_card(incoming)
        finally:
            catalog.connection.set_trace_callback(None)

        assert not any(
            statement.startswith("UPDATE CARDS SET STATUS")
            for statement in statements
        )
        assert any(
            statement.startswith("INSERT INTO CARD_LIFECYCLE_EVENTS")
            for statement in statements
        )
        assert any(statement.startswith("DELETE FROM CARD_FTS") for statement in statements)
        assert any(statement.startswith("INSERT INTO CARDS") for statement in statements)
        assert any(statement.startswith("INSERT INTO CARD_FTS") for statement in statements)
        after = {
            table: catalog.connection.execute(
                f"SELECT * FROM {table} ORDER BY 1"
            ).fetchall()
            for table in (
                "cards",
                "card_tags",
                "card_fts",
                "card_lifecycle_events",
                "card_lifecycle_current",
            )
        }
        assert after == before
        assert catalog.connection.execute(
            "SELECT status FROM cards WHERE version_id = ?",
            (first.version_id,),
        ).fetchone()[0] == "published"
        assert catalog.connection.in_transaction is False


@pytest.mark.parametrize("iteration", range(20))
def test_concurrent_direct_published_revisions_form_one_current_chain(
    tmp_path: Path,
    iteration: int,
) -> None:
    database = tmp_path / f"direct-concurrent-{iteration}.db"
    with KnowledgeCatalog.open(database) as setup:
        assert setup.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        setup.insert_vocabulary(vocabulary_fixture())
        setup.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        setup.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        first = setup.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
    candidates = tuple(
        card_fixture(
            version_id=f"direct-concurrent-{suffix}-{iteration}",
            revision=2,
            status="published",
            content_digest=("3" if suffix == "a" else "4") * 64,
            chunk_citations=(citation,),
            title=f"Concurrent direct {suffix}",
        )
        for suffix in ("a", "b")
    )
    start = Barrier(2)

    def worker(card: KnowledgeCardVersion) -> KnowledgeCardVersion:
        with KnowledgeCatalog.open(database) as opened:
            start.wait(timeout=10)
            return opened.insert_card(card)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(worker, candidates))

    assert {result.revision for result in results} == {2, 3}
    with KnowledgeCatalog.open(database) as catalog:
        rows = catalog.connection.execute(
            """
            SELECT cards.version_id,
                   cards.revision,
                   cards.status,
                   cards.payload_json,
                   lifecycle.status
            FROM cards
            JOIN card_lifecycle_current AS lifecycle
              ON lifecycle.card_version_id = cards.version_id
            WHERE cards.logical_id = ?
            ORDER BY cards.revision, cards.version_id
            """,
            (first.logical_id,),
        ).fetchall()
        assert [row[1] for row in rows] == [1, 2, 3]
        assert [row[2] for row in rows] == ["published", "published", "published"]
        assert [row[4] for row in rows] == ["superseded", "superseded", "published"]
        assert json.loads(rows[1][3])["supersedes_version_id"] == rows[0][0]
        assert json.loads(rows[2][3])["supersedes_version_id"] == rows[1][0]
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts"
        ).fetchall() == [(rows[2][0],)]


@pytest.mark.parametrize("iteration", range(20))
def test_concurrent_direct_publish_after_archive_forms_one_history_chain(
    tmp_path: Path,
    iteration: int,
) -> None:
    database = tmp_path / f"direct-archive-concurrent-{iteration}.db"
    with KnowledgeCatalog.open(database) as setup:
        assert setup.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        setup.insert_vocabulary(vocabulary_fixture())
        setup.insert_source(source_version_fixture())
        chunk = chunk_fixture()
        setup.insert_chunk(chunk)
        citation = ChunkCitation(
            chunk_id=chunk.chunk_id,
            source_version_id=chunk.source_version_id,
        )
        first = setup.insert_card(
            card_fixture(status="published", chunk_citations=(citation,))
        )
        with setup.connection:
            archived = transition_card_status(
                setup.connection,
                first.version_id,
                "archived",
            )
    candidates = tuple(
        card_fixture(
            version_id=f"submitted-archive-concurrent-{suffix}-{iteration}",
            revision=99,
            status="published",
            content_digest=("6" if suffix == "a" else "7") * 64,
            supersedes_version_id=None,
            chunk_citations=(citation,),
            title=f"Concurrent after archive {suffix}",
        )
        for suffix in ("a", "b")
    )
    start = Barrier(2)

    def worker(card: KnowledgeCardVersion) -> KnowledgeCardVersion:
        with KnowledgeCatalog.open(database) as opened:
            start.wait(timeout=10)
            return opened.insert_card(card)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(worker, candidates))

    assert {result.revision for result in results} == {2, 3}
    assert all(
        result.version_id != candidate.version_id
        for result, candidate in zip(
            sorted(results, key=lambda item: item.title),
            sorted(candidates, key=lambda item: item.title),
        )
    )
    with KnowledgeCatalog.open(database) as catalog:
        rows = catalog.connection.execute(
            """
            SELECT cards.version_id,
                   cards.revision,
                   cards.status,
                   cards.payload_json,
                   lifecycle.status
            FROM cards
            JOIN card_lifecycle_current AS lifecycle
              ON lifecycle.card_version_id = cards.version_id
            WHERE cards.logical_id = ?
            ORDER BY cards.revision, cards.version_id
            """,
            (archived.logical_id,),
        ).fetchall()
        assert [row[1] for row in rows] == [1, 2, 3]
        assert [row[2] for row in rows] == ["published", "published", "published"]
        assert [row[4] for row in rows] == ["archived", "superseded", "published"]
        assert json.loads(rows[1][3])["supersedes_version_id"] == rows[0][0]
        assert json.loads(rows[2][3])["supersedes_version_id"] == rows[1][0]
        assert catalog.connection.execute(
            "SELECT version_id FROM card_fts"
        ).fetchall() == [(rows[2][0],)]


def test_card_write_rolls_back_when_a_tag_reference_is_invalid(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        invalid = card_fixture(tag_id="difficulty:missing")

        with pytest.raises((CatalogReferenceError, sqlite3.IntegrityError)):
            catalog.insert_card(invalid)

        assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == 0
        assert catalog.connection.execute("SELECT count(*) FROM card_fts").fetchone()[0] == 0


def test_existing_card_identity_checks_immutability_before_new_references(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_vocabulary(vocabulary_fixture())
        card = card_fixture()
        catalog.insert_card(card)
        conflicting = card.model_copy(
            update={
                "vocabulary_version_id": "missing-vocabulary",
                "tag_assignments": (),
            }
        )

        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_card(conflicting)


def test_canonical_json_makes_mapping_insertion_order_idempotent(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        first = evidence_fixture(input_summary={"alpha": 1, "beta": 2})
        reordered = evidence_fixture(input_summary={"beta": 2, "alpha": 1})

        catalog.insert_evidence(first)
        catalog.insert_evidence(reordered)

        assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_evidence_subject_must_be_a_persisted_heterogeneous_version(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        missing = evidence_fixture(subject_version_id="missing-version")
        with pytest.raises(CatalogReferenceError, match="subject"):
            catalog.insert_evidence(missing)
        assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0

        catalog.insert_source(source_version_fixture())
        catalog.insert_chunk(chunk_fixture())
        catalog.insert_visual(visual_fixture())
        catalog.insert_dataset(dataset_fixture())
        catalog.insert_vocabulary(vocabulary_fixture())
        catalog.insert_card(card_fixture())
        subject_ids = (
            "source-version-1",
            "chunk-version-1",
            "visual-version-1",
            "dataset-version-1",
            "vocabulary-v1",
            "card-version-1",
        )
        for index, subject_version_id in enumerate(subject_ids):
            catalog.insert_evidence(
                evidence_fixture(
                    evidence_id=f"subject-evidence-{index}",
                    subject_version_id=subject_version_id,
                )
            )
        assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == len(
            subject_ids
        )


def test_lineage_requires_persisted_endpoints_and_evidence(tmp_path: Path) -> None:
    edge = LineageEdge(
        edge_id="edge-1",
        from_version_id="source-version-1",
        to_version_id="source-version-2",
        relation="supersedes",
        evidence_id="evidence-1",
        created_at=NOW,
    )
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        with pytest.raises(CatalogReferenceError, match="endpoint"):
            catalog.insert_lineage(edge)

        catalog.insert_source(source_version_fixture())
        catalog.insert_source(
            source_version_fixture(
                version_id="source-version-2",
                revision=2,
                content_digest="b" * 64,
                supersedes_version_id="source-version-1",
            )
        )
        with pytest.raises(CatalogReferenceError, match="evidence"):
            catalog.insert_lineage(edge)

        catalog.insert_evidence(evidence_fixture())
        catalog.insert_lineage(edge)
        catalog.insert_lineage(edge)
        assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 1
        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_lineage(edge.model_copy(update={"relation": "derived_from"}))


def test_review_tasks_are_immutable(tmp_path: Path) -> None:
    review = ReviewTask(
        task_id="review-1",
        kind="source-changed",
        subject_version_id="source-version-1",
        status="open",
        blocking=True,
        created_at=NOW,
        created_by=actor_fixture(),
    )
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_source(source_version_fixture())
        catalog.insert_review_task(review)
        catalog.insert_review_task(review)
        with pytest.raises(ImmutableVersionConflict):
            catalog.insert_review_task(review.model_copy(update={"blocking": False}))


def test_review_task_requires_every_referenced_evidence(tmp_path: Path) -> None:
    review = ReviewTask(
        task_id="review-with-evidence",
        kind="provenance",
        subject_version_id="source-version-1",
        status="open",
        blocking=True,
        evidence_ids=("evidence-1", "evidence-2"),
        created_at=NOW,
        created_by=actor_fixture(),
    )
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        catalog.insert_source(source_version_fixture())
        catalog.insert_evidence(evidence_fixture(evidence_id="evidence-1"))

        with pytest.raises(CatalogReferenceError, match="evidence-2"):
            catalog.insert_review_task(review)

        assert catalog.connection.execute("SELECT count(*) FROM review_tasks").fetchone()[0] == 0
        catalog.insert_evidence(evidence_fixture(evidence_id="evidence-2"))
        catalog.insert_review_task(review)
        assert catalog.connection.execute("SELECT count(*) FROM review_tasks").fetchone()[0] == 1
