"""Atomic mutation outcomes, authenticated recovery, and index outbox writes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_serializer,
    field_validator,
)

from course_helper.catalog import (
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.common import (
    ActorRef,
    FrozenDict,
    ImmutableJsonValue,
    freeze_json,
    thaw_json,
)


Clock = Callable[[], datetime]
Mutation = Callable[[], "OperationMutationResult"]
_operation_context: ContextVar[int | None] = ContextVar(
    "course_helper_operation_context", default=None
)
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OperationConflict(ValueError):
    """An operation ID was replayed with different immutable request bytes."""


class OperationAuthenticationError(PermissionError):
    """The caller does not own the stored operation outcome."""


class OperationIntegrityError(RuntimeError):
    """Stored operation facts no longer match their immutable bytes."""


class ItemRejected(ValueError):
    """An expected per-item domain rejection that may be isolated safely."""


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: ActorRef
    session_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class IndexOutboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outbox_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    card_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    action: Literal["upsert", "delete"]


class OperationItemOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    item_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    status: Literal["committed", "rolled-back"]
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


class _FrozenResultRefs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    result_refs: Mapping[str, ImmutableJsonValue] = Field(default_factory=dict)

    @field_validator("result_refs")
    @classmethod
    def freeze_refs(
        cls, value: Mapping[str, ImmutableJsonValue]
    ) -> Mapping[str, ImmutableJsonValue]:
        return cast(Mapping[str, ImmutableJsonValue], freeze_json(value))

    @field_serializer("result_refs", mode="wrap")
    def serialize_refs(
        self,
        value: Mapping[str, ImmutableJsonValue],
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, ImmutableJsonValue]:
        return cast(dict[str, ImmutableJsonValue], handler(thaw_json(value)))


class OperationMutationResult(_FrozenResultRefs):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    item_outcomes: tuple[OperationItemOutcome, ...]
    index_outbox: tuple[IndexOutboxItem, ...]

    @field_validator("item_outcomes")
    @classmethod
    def unique_items(
        cls, value: tuple[OperationItemOutcome, ...]
    ) -> tuple[OperationItemOutcome, ...]:
        if len({item.item_id for item in value}) != len(value):
            raise ValueError("operation item IDs must be unique")
        return value

    @field_validator("index_outbox")
    @classmethod
    def unique_outbox(
        cls, value: tuple[IndexOutboxItem, ...]
    ) -> tuple[IndexOutboxItem, ...]:
        if len({item.outbox_id for item in value}) != len(value):
            raise ValueError("outbox IDs must be unique")
        signatures = {(item.card_version_id, item.action) for item in value}
        if len(signatures) != len(value):
            raise ValueError("duplicate index work is not allowed")
        return value


class OperationOutcome(_FrozenResultRefs):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    request_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["committed", "rolled-back", "unknown", "in-progress"]
    item_outcomes: tuple[OperationItemOutcome, ...] = ()
    index_outbox_ids: tuple[str, ...] = ()
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("operation outcome created_at must be timezone-aware")
        return value

    @field_validator("index_outbox_ids")
    @classmethod
    def unique_safe_outbox_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("operation outbox IDs must be unique")
        if any(_SAFE_OPERATION_ID.fullmatch(item) is None for item in value):
            raise ValueError("operation outbox IDs must be safe opaque IDs")
        return value


@dataclass(frozen=True)
class ItemMutation:
    item_id: str
    mutate: Callable[[], object]

    def __post_init__(self) -> None:
        if not self.item_id or len(self.item_id) > 128:
            raise ValueError("item_id must be non-empty and bounded")


def _session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _aware_clock(clock: Clock) -> datetime:
    value = clock()
    if value.utcoffset() is None:
        raise ValueError("operation clock must return a timezone-aware datetime")
    return value


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_item_bundle(
    catalog: KnowledgeCatalog,
    items: tuple[ItemMutation, ...],
) -> tuple[OperationItemOutcome, ...]:
    """Isolate ordinary per-item validation failures with nested savepoints."""

    owner = _operation_context.get()
    if owner is None:
        raise RuntimeError("run_item_bundle must execute inside run_operation")
    if owner != id(catalog.connection) or catalog._atomic_depth == 0:
        raise RuntimeError("run_item_bundle must use the same catalog as run_operation")
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("item IDs must be unique")
    outcomes: list[OperationItemOutcome] = []
    for item in items:
        try:
            with catalog.atomic_write():
                item.mutate()
        except (
            CatalogReferenceError,
            ImmutableVersionConflict,
            ItemRejected,
        ):
            outcomes.append(
                OperationItemOutcome(
                    item_id=item.item_id,
                    status="rolled-back",
                    error_code="ITEM_REJECTED",
                )
            )
        else:
            outcomes.append(
                OperationItemOutcome(item_id=item.item_id, status="committed")
            )
    return tuple(outcomes)


def _authenticate_row(
    row: tuple[object, ...],
    *,
    actor_id: str,
    actor_type: str,
    session_id: str,
) -> None:
    stored_actor_id = str(row[1])
    stored_actor_type = str(row[2])
    stored_session_digest = str(row[3])
    if (
        stored_actor_id != actor_id
        or stored_actor_type != actor_type
        or stored_session_digest != _session_digest(session_id)
    ):
        raise OperationAuthenticationError("operation outcome is not owned by this session")


def _load_outcome(
    catalog: KnowledgeCatalog,
    row: tuple[object, ...],
    *,
    operation_id: str,
) -> OperationOutcome:
    request_digest = str(row[0])
    status = str(row[4])
    content_digest = str(row[5])
    payload_json = str(row[6])
    created_at_column = str(row[7])
    if _digest_text(payload_json) != content_digest:
        raise OperationIntegrityError("operation outcome payload digest mismatch")
    try:
        outcome = OperationOutcome.model_validate_json(payload_json, strict=False)
    except Exception as error:
        raise OperationIntegrityError("operation outcome payload is invalid") from error
    if (
        canonical_model_json(outcome) != payload_json
        or outcome.operation_id != operation_id
        or outcome.request_digest != request_digest
        or outcome.status != status
        or outcome.created_at is None
        or outcome.created_at.isoformat() != created_at_column
    ):
        raise OperationIntegrityError("operation outcome columns do not match raw bytes")
    item_rows = catalog.connection.execute(
        "SELECT ordinal, item_id, status, content_digest, payload_json, created_at "
        "FROM operation_item_outcomes WHERE operation_id = ? ORDER BY ordinal",
        (operation_id,),
    ).fetchall()
    if len(item_rows) != len(outcome.item_outcomes):
        raise OperationIntegrityError("operation item outcome set is inconsistent")
    for expected_ordinal, (expected, row_item) in enumerate(
        zip(outcome.item_outcomes, item_rows)
    ):
        raw_item = str(row_item[4])
        if (
            int(row_item[0]) != expected_ordinal
            or str(row_item[1]) != expected.item_id
            or str(row_item[2]) != expected.status
            or _digest_text(raw_item) != str(row_item[3])
            or canonical_model_json(expected) != raw_item
            or str(row_item[5]) != created_at_column
        ):
            raise OperationIntegrityError("operation item outcome bytes are inconsistent")
    outbox_rows = catalog.connection.execute(
        "SELECT outbox_id, request_digest, card_version_id, action, content_digest, "
        "payload_json, created_at "
        "FROM knowledge_index_outbox WHERE operation_id = ? ORDER BY outbox_id",
        (operation_id,),
    ).fetchall()
    if (
        len(outbox_rows) != len(outcome.index_outbox_ids)
        or {str(item[0]) for item in outbox_rows} != set(outcome.index_outbox_ids)
    ):
        raise OperationIntegrityError("operation outbox set is inconsistent")
    for outbox_row in outbox_rows:
        raw_outbox = str(outbox_row[5])
        try:
            values = json.loads(raw_outbox)
        except json.JSONDecodeError as error:
            raise OperationIntegrityError("operation outbox payload is invalid") from error
        expected_outbox = json.dumps(
            {
                "action": str(outbox_row[3]),
                "card_version_id": str(outbox_row[2]),
                "operation_id": operation_id,
                "outbox_id": str(outbox_row[0]),
                "request_digest": request_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if (
            _digest_text(raw_outbox) != str(outbox_row[4])
            or raw_outbox != expected_outbox
            or values.get("outbox_id") != str(outbox_row[0])
            or values.get("operation_id") != operation_id
            or values.get("request_digest") != request_digest
            or values.get("card_version_id") != str(outbox_row[2])
            or values.get("action") != str(outbox_row[3])
            or str(outbox_row[6]) != created_at_column
        ):
            raise OperationIntegrityError("operation outbox bytes are inconsistent")
    return outcome


def run_operation(
    catalog: KnowledgeCatalog,
    request: OperationRequest,
    mutation: Mutation,
    *,
    clock: Clock,
    after_commit: Callable[[OperationOutcome], object] | None = None,
) -> OperationOutcome:
    """Commit domain writes, outcomes, item results, and outbox as one transaction."""

    if _operation_context.get() is not None:
        raise RuntimeError("nested run_operation is not allowed")
    if catalog._atomic_depth != 0 or catalog.connection.in_transaction:
        raise RuntimeError("run_operation requires a top-level catalog transaction")
    outcome: OperationOutcome
    with catalog.atomic_write():
        existing = catalog.connection.execute(
            "SELECT request_digest, actor_id, actor_type, session_digest, status, "
            "content_digest, payload_json, created_at "
            "FROM operation_outcomes WHERE operation_id = ?",
            (request.operation_id,),
        ).fetchone()
        if existing is not None:
            _authenticate_row(
                existing,
                actor_id=request.actor.actor_id,
                actor_type=request.actor.actor_type,
                session_id=request.session_id,
            )
            if existing[0] != request.request_digest:
                raise OperationConflict(
                    "operation ID is already bound to a different request digest"
                )
            outcome = _load_outcome(
                catalog, existing, operation_id=request.operation_id
            )
        else:
            token = _operation_context.set(id(catalog.connection))
            try:
                mutation_result = mutation()
            finally:
                _operation_context.reset(token)
            if not isinstance(mutation_result, OperationMutationResult):
                raise TypeError("operation mutation must return OperationMutationResult")
            for outbox_item in mutation_result.index_outbox:
                if outbox_item.action == "upsert":
                    publishable = catalog.connection.execute(
                        "SELECT 1 FROM cards "
                        "JOIN card_lifecycle_current AS lifecycle "
                        "ON lifecycle.card_version_id = cards.version_id "
                        "WHERE cards.version_id = ? "
                        "AND lifecycle.status = 'published' AND lifecycle.suspended = 0",
                        (outbox_item.card_version_id,),
                    ).fetchone()
                    if publishable is None:
                        raise CatalogReferenceError(
                            "index upsert requires a published, non-suspended card version"
                        )
                else:
                    lifecycle = catalog.connection.execute(
                        "SELECT status, suspended FROM card_lifecycle_current "
                        "WHERE card_version_id = ?",
                        (outbox_item.card_version_id,),
                    ).fetchone()
                    if lifecycle is None:
                        raise CatalogReferenceError(
                            "index delete requires a persisted lifecycle projection"
                        )
                    if lifecycle == ("published", 0):
                        raise CatalogReferenceError(
                            "index delete cannot remove an active published card"
                        )
            created_at = _aware_clock(clock)
            outcome = OperationOutcome(
                operation_id=request.operation_id,
                request_digest=request.request_digest,
                status="committed",
                result_refs=cast(
                    Mapping[str, ImmutableJsonValue],
                    thaw_json(mutation_result.result_refs),
                ),
                item_outcomes=mutation_result.item_outcomes,
                index_outbox_ids=tuple(
                    item.outbox_id for item in mutation_result.index_outbox
                ),
                created_at=created_at,
            )
            payload = canonical_model_json(outcome)
            catalog.connection.execute(
                "INSERT INTO operation_outcomes("
                "operation_id, request_digest, actor_id, actor_type, session_digest, "
                "status, content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, 'committed', ?, ?, ?)",
                (
                    request.operation_id,
                    request.request_digest,
                    request.actor.actor_id,
                    request.actor.actor_type,
                    _session_digest(request.session_id),
                    _digest_text(payload),
                    payload,
                    created_at.isoformat(),
                ),
            )
            for ordinal, item_outcome in enumerate(mutation_result.item_outcomes):
                item_payload = canonical_model_json(item_outcome)
                catalog.connection.execute(
                    "INSERT INTO operation_item_outcomes("
                    "operation_id, ordinal, item_id, status, content_digest, payload_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.operation_id,
                        ordinal,
                        item_outcome.item_id,
                        item_outcome.status,
                        _digest_text(item_payload),
                        item_payload,
                        created_at.isoformat(),
                    ),
                )
            for outbox_item in mutation_result.index_outbox:
                outbox_payload = json.dumps(
                    {
                        "action": outbox_item.action,
                        "card_version_id": outbox_item.card_version_id,
                        "operation_id": request.operation_id,
                        "outbox_id": outbox_item.outbox_id,
                        "request_digest": request.request_digest,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                catalog.connection.execute(
                    "INSERT INTO knowledge_index_outbox("
                    "outbox_id, operation_id, request_digest, card_version_id, action, "
                    "content_digest, payload_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        outbox_item.outbox_id,
                        request.operation_id,
                        request.request_digest,
                        outbox_item.card_version_id,
                        outbox_item.action,
                        _digest_text(outbox_payload),
                        outbox_payload,
                        created_at.isoformat(),
                    ),
                )
    if after_commit is not None:
        after_commit(outcome)
    return outcome


def operation_status(
    catalog: KnowledgeCatalog,
    *,
    operation_id: str,
    actor_id: str,
    actor_type: Literal["human", "service", "model", "system"],
    session_id: str,
) -> OperationOutcome:
    """Return a stored outcome only to its original actor/session pair."""

    row = catalog.connection.execute(
        "SELECT request_digest, actor_id, actor_type, session_digest, status, "
        "content_digest, payload_json, created_at "
        "FROM operation_outcomes WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return OperationOutcome(operation_id=operation_id, status="unknown")
    _authenticate_row(
        row, actor_id=actor_id, actor_type=actor_type, session_id=session_id
    )
    return _load_outcome(catalog, row, operation_id=operation_id)


__all__ = [
    "IndexOutboxItem",
    "ItemRejected",
    "ItemMutation",
    "OperationAuthenticationError",
    "OperationConflict",
    "OperationIntegrityError",
    "OperationItemOutcome",
    "OperationMutationResult",
    "OperationOutcome",
    "OperationRequest",
    "operation_status",
    "run_item_bundle",
    "run_operation",
]
