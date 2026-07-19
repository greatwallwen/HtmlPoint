from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

import pytest

from course_helper.catalog import CatalogReferenceError, KnowledgeCatalog, transition_card_status
from course_helper.domain.common import ActorRef
from course_helper.domain.composition import CourseRequirement
from course_helper.domain.knowledge import CardContentNode, KnowledgeCardVersion, TagVocabularyVersion
from course_helper.operations import (
    IndexOutboxItem,
    ItemRejected,
    ItemMutation,
    OperationAuthenticationError,
    OperationConflict,
    OperationIntegrityError,
    OperationMutationResult,
    OperationRequest,
    operation_status,
    run_item_bundle,
    run_operation,
)


NOW = datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc)


def seed_index_card(catalog: KnowledgeCatalog) -> str:
    actor = ActorRef(actor_type="service", actor_id="index-seed")
    catalog.insert_vocabulary(
        TagVocabularyVersion(
            logical_id="index-vocabulary",
            version_id="index-vocabulary-v1",
            revision=1,
            content_digest="1" * 64,
            created_at=NOW,
            created_by=actor,
            dimensions=(),
        )
    )
    stored = catalog.insert_card(
        KnowledgeCardVersion(
            logical_id="card-index",
            version_id="card-index-v1",
            revision=1,
            content_digest="2" * 64,
            created_at=NOW,
            created_by=actor,
            main_type_id="exercise",
            title="Index card",
            learning_objective="Verify transactional indexing",
            content_ast=(CardContentNode(type="paragraph", text="Indexed content."),),
            suggested_minutes=5,
            vocabulary_version_id="index-vocabulary-v1",
            status="published",
        )
    )
    return stored.version_id


def test_committed_outcome_survives_response_loss_in_same_transaction(tmp_path: Path) -> None:
    request = OperationRequest(
        operation_id="operation-1",
        request_digest="a" * 64,
        actor=ActorRef(actor_type="human", actor_id="reviewer-1"),
        session_id="session-1",
    )
    requirement = CourseRequirement(
        requirement_id="requirement-operation-1",
        title="Recoverable composition",
        audience="Facilitators",
        learning_goals=("Recover committed mutations",),
        duration_minutes=30,
        usage_scope="internal",
    )
    database = tmp_path / "knowledge.db"

    with KnowledgeCatalog.open(database) as catalog:
        seed_index_card(catalog)
        with pytest.raises(ConnectionError, match="lost"):
            run_operation(
                catalog,
                request,
                lambda: OperationMutationResult(
                    result_refs={"requirementId": requirement.requirement_id},
                    item_outcomes=(),
                    index_outbox=(),
                )
                if catalog.register_course_requirement(requirement, clock=lambda: NOW)
                else None,
                clock=lambda: NOW,
                after_commit=lambda _outcome: (_ for _ in ()).throw(
                    ConnectionError("lost")
                ),
            )

    with KnowledgeCatalog.open(database) as reopened:
        outcome = operation_status(
            reopened,
            operation_id=request.operation_id,
            actor_id=request.actor.actor_id,
            actor_type=request.actor.actor_type,
            session_id=request.session_id,
        )
        assert outcome.status == "committed"
        assert reopened.get_course_requirement(requirement.requirement_id) is not None


def test_same_operation_digest_replays_without_duplicate_mutation_or_outbox(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-replay",
        request_digest="b" * 64,
        actor=ActorRef(actor_type="service", actor_id="publisher-1"),
        session_id="session-replay",
    )
    calls = 0
    database = tmp_path / "knowledge.db"

    with KnowledgeCatalog.open(database) as catalog:
        card_version_id = seed_index_card(catalog)
        def mutate() -> OperationMutationResult:
            nonlocal calls
            calls += 1
            return OperationMutationResult(
                result_refs={"cardVersionId": card_version_id},
                item_outcomes=(),
                index_outbox=(
                    IndexOutboxItem(
                        outbox_id="outbox-1",
                        card_version_id=card_version_id,
                        action="upsert",
                    ),
                ),
            )

        first = run_operation(catalog, request, mutate, clock=lambda: NOW)
        second = run_operation(catalog, request, mutate, clock=lambda: NOW)

        assert second == first
        assert calls == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox"
        ).fetchone()[0] == 1
        with pytest.raises(OperationConflict):
            run_operation(
                catalog,
                request.model_copy(update={"request_digest": "c" * 64}),
                mutate,
                clock=lambda: NOW,
            )
        with pytest.raises(CatalogReferenceError):
            run_operation(
                catalog,
                OperationRequest(
                    operation_id="operation-dangling-outbox",
                    request_digest="3" * 64,
                    actor=request.actor,
                    session_id=request.session_id,
                ),
                lambda: OperationMutationResult(
                    result_refs={},
                    item_outcomes=(),
                    index_outbox=(
                        IndexOutboxItem(
                            outbox_id="outbox-missing-card",
                            card_version_id="missing-card",
                            action="upsert",
                        ),
                    ),
                ),
                clock=lambda: NOW,
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes "
            "WHERE operation_id='operation-dangling-outbox'"
        ).fetchone()[0] == 0
        catalog.insert_card(
            KnowledgeCardVersion(
                logical_id="card-review-only",
                version_id="card-review-only-v1",
                revision=1,
                content_digest="9" * 64,
                created_at=NOW,
                created_by=request.actor,
                main_type_id="exercise",
                title="Not publishable to index",
                learning_objective="Remain outside published retrieval",
                content_ast=(CardContentNode(type="paragraph", text="Review only."),),
                suggested_minutes=5,
                vocabulary_version_id="index-vocabulary-v1",
                status="review",
            )
        )
        with pytest.raises(CatalogReferenceError, match="published"):
            run_operation(
                catalog,
                OperationRequest(
                    operation_id="operation-review-outbox",
                    request_digest="0" * 64,
                    actor=request.actor,
                    session_id=request.session_id,
                ),
                lambda: OperationMutationResult(
                    result_refs={},
                    item_outcomes=(),
                    index_outbox=(
                        IndexOutboxItem(
                            outbox_id="outbox-review-card",
                            card_version_id="card-review-only-v1",
                            action="upsert",
                        ),
                    ),
                ),
                clock=lambda: NOW,
            )
        with pytest.raises(CatalogReferenceError, match="active published"):
            run_operation(
                catalog,
                OperationRequest(
                    operation_id="operation-active-delete",
                    request_digest="1" * 64,
                    actor=request.actor,
                    session_id=request.session_id,
                ),
                lambda: OperationMutationResult(
                    result_refs={},
                    item_outcomes=(),
                    index_outbox=(
                        IndexOutboxItem(
                            outbox_id="outbox-active-delete",
                            card_version_id=card_version_id,
                            action="delete",
                        ),
                    ),
                ),
                clock=lambda: NOW,
            )
        with catalog.connection:
            transition_card_status(catalog.connection, card_version_id, "archived")
        deleted = run_operation(
            catalog,
            OperationRequest(
                operation_id="operation-archived-delete",
                request_digest="2" * 64,
                actor=request.actor,
                session_id=request.session_id,
            ),
            lambda: OperationMutationResult(
                result_refs={},
                item_outcomes=(),
                index_outbox=(
                    IndexOutboxItem(
                        outbox_id="outbox-archived-delete",
                        card_version_id=card_version_id,
                        action="delete",
                    ),
                ),
            ),
            clock=lambda: NOW,
        )
        assert deleted.status == "committed"


def test_operation_and_outbox_rows_are_append_only(tmp_path: Path) -> None:
    request = OperationRequest(
        operation_id="operation-immutable",
        request_digest="4" * 64,
        actor=ActorRef(actor_type="service", actor_id="publisher-immutable"),
        session_id="session-immutable",
    )
    with KnowledgeCatalog.open(tmp_path / "immutable.db") as catalog:
        card_version_id = seed_index_card(catalog)
        run_operation(
            catalog,
            request,
            lambda: OperationMutationResult(
                result_refs={},
                item_outcomes=(),
                index_outbox=(
                    IndexOutboxItem(
                        outbox_id="outbox-immutable",
                        card_version_id=card_version_id,
                        action="upsert",
                    ),
                ),
            ),
            clock=lambda: NOW,
        )
        for table, column, identity in (
            ("operation_outcomes", "operation_id", request.operation_id),
            ("knowledge_index_outbox", "outbox_id", "outbox-immutable"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"UPDATE {table} SET payload_json='{{}}' WHERE {column}=?", (identity,)
                )
            with pytest.raises(sqlite3.IntegrityError):
                catalog.connection.execute(
                    f"DELETE FROM {table} WHERE {column}=?", (identity,)
                )


def test_precommit_kill_rolls_back_domain_outcome_and_outbox_and_reports_unknown(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-killed",
        request_digest="d" * 64,
        actor=ActorRef(actor_type="human", actor_id="reviewer-killed"),
        session_id="session-killed",
    )
    requirement = CourseRequirement(
        requirement_id="requirement-killed",
        title="Must roll back",
        audience="Operators",
        learning_goals=("Observe truthful unknown",),
        duration_minutes=30,
        usage_scope="internal",
    )

    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        def killed() -> OperationMutationResult:
            catalog.register_course_requirement(requirement, clock=lambda: NOW)
            raise KeyboardInterrupt("pre-commit kill")

        with pytest.raises(KeyboardInterrupt, match="pre-commit"):
            run_operation(catalog, request, killed, clock=lambda: NOW)

        assert catalog.get_course_requirement(requirement.requirement_id) is None
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes"
        ).fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox"
        ).fetchone()[0] == 0
        assert operation_status(
            catalog,
            operation_id=request.operation_id,
            actor_id=request.actor.actor_id,
            actor_type=request.actor.actor_type,
            session_id=request.session_id,
        ).status == "unknown"


def test_operation_lookup_is_authenticated_and_does_not_leak_existing_outcome(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-private",
        request_digest="e" * 64,
        actor=ActorRef(actor_type="human", actor_id="reviewer-private"),
        session_id="session-private",
    )
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        run_operation(
            catalog,
            request,
            lambda: OperationMutationResult(
                result_refs={}, item_outcomes=(), index_outbox=()
            ),
            clock=lambda: NOW,
        )
        with pytest.raises(OperationAuthenticationError):
            operation_status(
                catalog,
                operation_id=request.operation_id,
                actor_id=request.actor.actor_id,
                actor_type=request.actor.actor_type,
                session_id="wrong-session",
            )
        with pytest.raises(OperationAuthenticationError):
            operation_status(
                catalog,
                operation_id=request.operation_id,
                actor_id=request.actor.actor_id,
                actor_type="service",
                session_id=request.session_id,
            )


def test_item_bundle_savepoints_preserve_successful_siblings(tmp_path: Path) -> None:
    request = OperationRequest(
        operation_id="operation-bundle",
        request_digest="f" * 64,
        actor=ActorRef(actor_type="service", actor_id="bundle-worker"),
        session_id="session-bundle",
    )
    first = CourseRequirement(
        requirement_id="bundle-requirement-1",
        title="First sibling",
        audience="Operators",
        learning_goals=("Keep first",),
        duration_minutes=30,
        usage_scope="internal",
    )
    last = first.model_copy(
        update={"requirement_id": "bundle-requirement-2", "title": "Last sibling"}
    )
    rejected = first.model_copy(
        update={
            "requirement_id": "bundle-requirement-rejected",
            "title": "Rejected sibling",
        }
    )

    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        def invalid_item() -> object:
            catalog.register_course_requirement(rejected, clock=lambda: NOW)
            raise ItemRejected("invalid asset")

        def mutate() -> OperationMutationResult:
            item_outcomes = run_item_bundle(
                catalog,
                (
                    ItemMutation(
                        item_id="first",
                        mutate=lambda: catalog.register_course_requirement(
                            first, clock=lambda: NOW
                        ),
                    ),
                    ItemMutation(item_id="invalid-asset", mutate=invalid_item),
                    ItemMutation(
                        item_id="last",
                        mutate=lambda: catalog.register_course_requirement(
                            last, clock=lambda: NOW
                        ),
                    ),
                ),
            )
            return OperationMutationResult(
                result_refs={"bundleId": "bundle-1"},
                item_outcomes=item_outcomes,
                index_outbox=(),
            )

        outcome = run_operation(catalog, request, mutate, clock=lambda: NOW)

        assert [item.status for item in outcome.item_outcomes] == [
            "committed",
            "rolled-back",
            "committed",
        ]
        assert [item.item_id for item in outcome.item_outcomes] == [
            "first",
            "invalid-asset",
            "last",
        ]
        assert catalog.get_course_requirement(first.requirement_id) is not None
        assert catalog.get_course_requirement(last.requirement_id) is not None
        assert catalog.get_course_requirement(rejected.requirement_id) is None
        assert catalog.connection.execute(
            "SELECT count(*) FROM visual_placements WHERE placement_id = 'invalid-placement'"
        ).fetchone()[0] == 0


def test_item_bundle_cannot_commit_without_an_operation_owner(tmp_path: Path) -> None:
    requirement = CourseRequirement(
        requirement_id="orphan-item",
        title="No orphan",
        audience="Operators",
        learning_goals=("Require outcome ownership",),
        duration_minutes=30,
        usage_scope="internal",
    )
    with KnowledgeCatalog.open(tmp_path / "orphan.db") as catalog:
        with pytest.raises(RuntimeError, match="run_operation"):
            run_item_bundle(
                catalog,
                (
                    ItemMutation(
                        item_id="orphan",
                        mutate=lambda: catalog.register_course_requirement(
                            requirement, clock=lambda: NOW
                        ),
                    ),
                ),
            )
        assert catalog.get_course_requirement(requirement.requirement_id) is None


def test_unknown_item_programming_error_escapes_and_rolls_back_outer_operation(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-programming-error",
        request_digest="5" * 64,
        actor=ActorRef(actor_type="service", actor_id="bundle-programmer"),
        session_id="session-programming-error",
    )
    requirement = CourseRequirement(
        requirement_id="programming-error-first",
        title="Must not remain",
        audience="Operators",
        learning_goals=("Do not hide programming errors",),
        duration_minutes=30,
        usage_scope="internal",
    )
    with KnowledgeCatalog.open(tmp_path / "programming.db") as catalog:
        def mutate() -> OperationMutationResult:
            return OperationMutationResult(
                result_refs={},
                item_outcomes=run_item_bundle(
                    catalog,
                    (
                        ItemMutation(
                            item_id="first",
                            mutate=lambda: catalog.register_course_requirement(
                                requirement, clock=lambda: NOW
                            ),
                        ),
                        ItemMutation(
                            item_id="bug",
                            mutate=lambda: (_ for _ in ()).throw(
                                TypeError("programming bug")
                            ),
                        ),
                    ),
                ),
                index_outbox=(),
            )

        with pytest.raises(TypeError, match="programming bug"):
            run_operation(catalog, request, mutate, clock=lambda: NOW)
        assert catalog.get_course_requirement(requirement.requirement_id) is None
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes"
        ).fetchone()[0] == 0


def test_item_bundle_rejects_a_different_catalog_from_its_operation(tmp_path: Path) -> None:
    request = OperationRequest(
        operation_id="operation-catalog-a",
        request_digest="6" * 64,
        actor=ActorRef(actor_type="service", actor_id="catalog-owner"),
        session_id="session-catalog-owner",
    )
    orphan = CourseRequirement(
        requirement_id="cross-catalog-orphan",
        title="Must stay absent",
        audience="Operators",
        learning_goals=("Bind bundles to one catalog",),
        duration_minutes=30,
        usage_scope="internal",
    )
    with (
        KnowledgeCatalog.open(tmp_path / "a.db") as catalog_a,
        KnowledgeCatalog.open(tmp_path / "b.db") as catalog_b,
    ):
        def cross_catalog() -> OperationMutationResult:
            return OperationMutationResult(
                result_refs={},
                item_outcomes=run_item_bundle(
                    catalog_b,
                    (
                        ItemMutation(
                            item_id="orphan",
                            mutate=lambda: catalog_b.register_course_requirement(
                                orphan, clock=lambda: NOW
                            ),
                        ),
                    ),
                ),
                index_outbox=(),
            )

        with pytest.raises(RuntimeError, match="same catalog"):
            run_operation(catalog_a, request, cross_catalog, clock=lambda: NOW)
        assert catalog_b.get_course_requirement(orphan.requirement_id) is None
        assert catalog_a.connection.execute(
            "SELECT count(*) FROM operation_outcomes"
        ).fetchone()[0] == 0


def test_nested_operation_is_rejected_before_any_false_commit_callback(tmp_path: Path) -> None:
    actor = ActorRef(actor_type="service", actor_id="nested-owner")
    outer = OperationRequest(
        operation_id="operation-outer",
        request_digest="7" * 64,
        actor=actor,
        session_id="session-nested",
    )
    inner = OperationRequest(
        operation_id="operation-inner",
        request_digest="8" * 64,
        actor=actor,
        session_id="session-nested",
    )
    notifications: list[str] = []
    with KnowledgeCatalog.open(tmp_path / "nested.db") as catalog:
        def outer_mutation() -> OperationMutationResult:
            run_operation(
                catalog,
                inner,
                lambda: OperationMutationResult(
                    result_refs={}, item_outcomes=(), index_outbox=()
                ),
                clock=lambda: NOW,
                after_commit=lambda outcome: notifications.append(outcome.operation_id),
            )
            return OperationMutationResult(
                result_refs={}, item_outcomes=(), index_outbox=()
            )

        with pytest.raises(RuntimeError, match="nested run_operation"):
            run_operation(catalog, outer, outer_mutation, clock=lambda: NOW)
        assert notifications == []
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes"
        ).fetchone()[0] == 0


def test_run_operation_rejects_an_unknown_outer_transaction(tmp_path: Path) -> None:
    request = OperationRequest(
        operation_id="operation-outer-transaction",
        request_digest="a" * 64,
        actor=ActorRef(actor_type="service", actor_id="outer-transaction-owner"),
        session_id="session-outer-transaction",
    )
    notifications: list[str] = []
    with KnowledgeCatalog.open(tmp_path / "outer-transaction.db") as catalog:
        with catalog.atomic_write():
            with pytest.raises(RuntimeError, match="top-level"):
                run_operation(
                    catalog,
                    request,
                    lambda: OperationMutationResult(
                        result_refs={}, item_outcomes=(), index_outbox=()
                    ),
                    clock=lambda: NOW,
                    after_commit=lambda outcome: notifications.append(outcome.operation_id),
                )
        assert notifications == []
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes"
        ).fetchone()[0] == 0


def test_unexpected_integrity_error_is_not_reported_as_an_item_rejection(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-integrity-bug",
        request_digest="b" * 64,
        actor=ActorRef(actor_type="service", actor_id="integrity-owner"),
        session_id="session-integrity-bug",
    )
    with KnowledgeCatalog.open(tmp_path / "integrity-bug.db") as catalog:
        def mutate() -> OperationMutationResult:
            return OperationMutationResult(
                result_refs={},
                item_outcomes=run_item_bundle(
                    catalog,
                    (
                        ItemMutation(
                            item_id="schema-bug",
                            mutate=lambda: (_ for _ in ()).throw(
                                sqlite3.IntegrityError("unexpected invariant")
                            ),
                        ),
                    ),
                ),
                index_outbox=(),
            )

        with pytest.raises(sqlite3.IntegrityError, match="unexpected invariant"):
            run_operation(catalog, request, mutate, clock=lambda: NOW)
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes"
        ).fetchone()[0] == 0


def test_operation_reopen_fails_closed_for_raw_digest_or_identity_corruption(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-corrupt",
        request_digest="c" * 64,
        actor=ActorRef(actor_type="human", actor_id="corruption-owner"),
        session_id="session-corrupt",
    )
    with KnowledgeCatalog.open(tmp_path / "corrupt.db") as catalog:
        run_operation(
            catalog,
            request,
            lambda: OperationMutationResult(
                result_refs={}, item_outcomes=(), index_outbox=()
            ),
            clock=lambda: NOW,
        )
        catalog.connection.execute("DROP TRIGGER operation_outcomes_immutable_update")
        catalog.connection.execute(
            "UPDATE operation_outcomes SET content_digest = ? WHERE operation_id = ?",
            ("0" * 64, request.operation_id),
        )
        catalog.connection.commit()
        with pytest.raises(OperationIntegrityError):
            operation_status(
                catalog,
                operation_id=request.operation_id,
                actor_id=request.actor.actor_id,
                actor_type=request.actor.actor_type,
                session_id=request.session_id,
            )


def test_operation_reopen_rejects_duplicate_outbox_ids_in_raw_parent_bytes(
    tmp_path: Path,
) -> None:
    request = OperationRequest(
        operation_id="operation-duplicate-outbox",
        request_digest="d" * 64,
        actor=ActorRef(actor_type="service", actor_id="duplicate-owner"),
        session_id="session-duplicate-outbox",
    )
    with KnowledgeCatalog.open(tmp_path / "duplicate-outbox.db") as catalog:
        card_version_id = seed_index_card(catalog)
        run_operation(
            catalog,
            request,
            lambda: OperationMutationResult(
                result_refs={},
                item_outcomes=(),
                index_outbox=(
                    IndexOutboxItem(
                        outbox_id="outbox-duplicate",
                        card_version_id=card_version_id,
                        action="upsert",
                    ),
                ),
            ),
            clock=lambda: NOW,
        )
        payload = json.loads(
            catalog.connection.execute(
                "SELECT payload_json FROM operation_outcomes WHERE operation_id = ?",
                (request.operation_id,),
            ).fetchone()[0]
        )
        payload["index_outbox_ids"] = ["outbox-duplicate", "outbox-duplicate"]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        catalog.connection.execute("DROP TRIGGER operation_outcomes_immutable_update")
        catalog.connection.execute(
            "UPDATE operation_outcomes SET payload_json = ?, content_digest = ? "
            "WHERE operation_id = ?",
            (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), request.operation_id),
        )
        catalog.connection.commit()

        with pytest.raises(OperationIntegrityError, match="payload|outbox"):
            operation_status(
                catalog,
                operation_id=request.operation_id,
                actor_id=request.actor.actor_id,
                actor_type=request.actor.actor_type,
                session_id=request.session_id,
            )
