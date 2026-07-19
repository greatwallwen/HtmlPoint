"""Append-only card lifecycle events and rebuildable current projections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from course_helper.domain.knowledge import CardContentNode, KnowledgeCardVersion
from course_helper.domain.sources import ExtractedChunk


CardStatus = Literal["draft", "review", "published", "superseded", "archived"]
LifecycleEventType = Literal["publish", "supersede", "archive", "suspend", "reinstate"]


class CardLifecycleConflict(ValueError):
    """An event identity was replayed with different immutable request bytes."""


class CardLifecycleTransitionError(ValueError):
    """A requested state transition is not valid from the current projection."""


class CardLifecycleProjection(BaseModel):
    """Rebuildable effective status; suspension is deliberately separate."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    card_version_id: str = Field(min_length=1)
    status: CardStatus
    suspended: bool
    last_sequence: int = Field(ge=1)
    last_event_id: str = Field(min_length=1)


class ReopenedCardVersion(BaseModel):
    """In-memory old-course view that never rewrites immutable card payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    card: KnowledgeCardVersion
    suspended: bool
    warnings: tuple[str, ...] = ()


def lifecycle_schema_available(connection: sqlite3.Connection) -> bool:
    migration_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if migration_table is None:
        return False
    try:
        versions = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
    except sqlite3.Error as error:
        raise CardLifecycleTransitionError(
            "card lifecycle migration metadata is corrupt"
        ) from error
    if 2 not in versions:
        return False

    required_columns = {
        "card_lifecycle_events": {
            "event_id",
            "card_version_id",
            "sequence",
            "event_type",
            "request_digest",
            "status_before",
            "status_after",
            "suspended_before",
            "suspended_after",
            "occurred_at",
            "actor_id",
            "payload_json",
        },
        "card_lifecycle_current": {
            "card_version_id",
            "status",
            "suspended",
            "last_sequence",
            "last_event_id",
        },
    }
    for table, expected in required_columns.items():
        actual = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()
        }
        if not expected.issubset(actual):
            raise CardLifecycleTransitionError(
                f"card lifecycle schema is corrupt: {table}"
            )
    return True


def get_card_lifecycle(
    connection: sqlite3.Connection,
    card_version_id: str,
) -> CardLifecycleProjection:
    row = connection.execute(
        """
        SELECT status, suspended, last_sequence, last_event_id
        FROM card_lifecycle_current
        WHERE card_version_id = ?
        """,
        (card_version_id,),
    ).fetchone()
    if row is None:
        raise CardLifecycleTransitionError(
            f"card lifecycle projection is unavailable: {card_version_id!r}"
        )
    return CardLifecycleProjection(
        card_version_id=card_version_id,
        status=row[0],
        suspended=bool(row[1]),
        last_sequence=row[2],
        last_event_id=row[3],
    )


def register_card_lifecycle(
    connection: sqlite3.Connection,
    card: KnowledgeCardVersion,
    *,
    event_id: str,
    request_digest: str,
    occurred_at: datetime,
    actor_id: str,
) -> CardLifecycleProjection:
    """Create the first event for a newly inserted immutable card row."""

    _validate_request(event_id, request_digest, occurred_at, actor_id)
    with _writer_transaction(connection):
        stored_row = connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?",
            (card.version_id,),
        ).fetchone()
        if stored_row is None:
            raise CardLifecycleTransitionError(
                f"card version does not exist: {card.version_id!r}"
            )
        stored_card = KnowledgeCardVersion.model_validate_json(stored_row[0])
        if stored_card != card:
            raise CardLifecycleConflict(
                f"card registration {card.version_id!r} has different immutable request bytes"
            )
        existing = connection.execute(
            "SELECT 1 FROM card_lifecycle_current WHERE card_version_id = ?",
            (card.version_id,),
        ).fetchone()
        if existing is not None:
            replay = connection.execute(
                """
                SELECT card_version_id, event_type, request_digest, occurred_at, actor_id
                FROM card_lifecycle_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            expected = (
                card.version_id,
                "register",
                request_digest,
                _utc_timestamp(occurred_at),
                actor_id,
            )
            if replay != expected:
                raise CardLifecycleConflict(
                    f"card registration {event_id!r} has different immutable request bytes"
                )
            return get_card_lifecycle(connection, card.version_id)
        projection = _insert_event(
            connection,
            card_version_id=card.version_id,
            event_id=event_id,
            request_digest=request_digest,
            event_type="register",
            status_before=card.status,
            status_after=card.status,
            suspended_before=False,
            suspended_after=False,
            sequence=1,
            occurred_at=occurred_at,
            actor_id=actor_id,
        )
        _refresh_card_fts(connection, projection)
        return projection


def append_card_lifecycle_event(
    connection: sqlite3.Connection,
    *,
    card_version_id: str,
    event_id: str,
    request_digest: str,
    event_type: LifecycleEventType,
    occurred_at: datetime,
    actor_id: str,
) -> CardLifecycleProjection:
    """Append one idempotent event and refresh its effective FTS membership."""

    _validate_request(event_id, request_digest, occurred_at, actor_id)
    with _writer_transaction(connection):
        replay = connection.execute(
            """
            SELECT card_version_id, event_type, request_digest, status_after,
                   suspended_after, sequence
            FROM card_lifecycle_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if replay is not None:
            if (
                replay[0] != card_version_id
                or replay[1] != event_type
                or replay[2] != request_digest
            ):
                raise CardLifecycleConflict(
                    f"lifecycle event {event_id!r} has different immutable request bytes"
                )
            return CardLifecycleProjection(
                card_version_id=card_version_id,
                status=replay[3],
                suspended=bool(replay[4]),
                last_sequence=replay[5],
                last_event_id=event_id,
            )

        current = get_card_lifecycle(connection, card_version_id)
        status_after, suspended_after = _next_state(current, event_type)
        projection = _insert_event(
            connection,
            card_version_id=card_version_id,
            event_id=event_id,
            request_digest=request_digest,
            event_type=event_type,
            status_before=current.status,
            status_after=status_after,
            suspended_before=current.suspended,
            suspended_after=suspended_after,
            sequence=current.last_sequence + 1,
            occurred_at=occurred_at,
            actor_id=actor_id,
        )
        _refresh_card_fts(connection, projection)
        return projection


def rebuild_card_lifecycle_projection(connection: sqlite3.Connection) -> None:
    """Replay every event and reconstruct both current state and FTS membership."""

    with _writer_transaction(connection):
        rows = connection.execute(
            """
            SELECT event_id, card_version_id, sequence, event_type,
                   status_before, status_after, suspended_before, suspended_after
            FROM card_lifecycle_events
            ORDER BY card_version_id, sequence
            """
        ).fetchall()
        raw_statuses = {
            row[0]: row[1]
            for row in connection.execute("SELECT version_id, status FROM cards").fetchall()
        }
        current: dict[str, CardLifecycleProjection] = {}
        for row in rows:
            (
                event_id,
                card_version_id,
                sequence,
                event_type,
                status_before,
                status_after,
                suspended_before,
                suspended_after,
            ) = row
            if card_version_id not in raw_statuses:
                raise CardLifecycleTransitionError(
                    f"lifecycle event references a missing card: {card_version_id!r}"
                )
            previous = current.get(card_version_id)
            if previous is None:
                if sequence != 1 or event_type not in {"backfill", "register"}:
                    raise CardLifecycleTransitionError(
                        f"lifecycle sequence for {card_version_id!r} has no initial event"
                    )
                if (
                    status_before != raw_statuses[card_version_id]
                    or status_after != raw_statuses[card_version_id]
                    or bool(suspended_before)
                    or bool(suspended_after)
                ):
                    raise CardLifecycleTransitionError(
                        f"initial lifecycle event for {card_version_id!r} disagrees with card bytes"
                    )
            else:
                if sequence != previous.last_sequence + 1:
                    raise CardLifecycleTransitionError(
                        f"lifecycle sequence for {card_version_id!r} is not contiguous"
                    )
                if status_before != previous.status or bool(suspended_before) != previous.suspended:
                    raise CardLifecycleTransitionError(
                        f"lifecycle event chain for {card_version_id!r} is inconsistent"
                    )
                if event_type in {"backfill", "register"}:
                    raise CardLifecycleTransitionError(
                        f"lifecycle event type {event_type!r} is only valid at sequence 1"
                    )
                expected_status, expected_suspended = _next_state(previous, event_type)
                if (
                    status_after != expected_status
                    or bool(suspended_after) != expected_suspended
                ):
                    raise CardLifecycleTransitionError(
                        f"lifecycle event {event_id!r} does not match event type {event_type!r}"
                    )
            projection = CardLifecycleProjection(
                card_version_id=card_version_id,
                status=status_after,
                suspended=bool(suspended_after),
                last_sequence=sequence,
                last_event_id=event_id,
            )
            current[card_version_id] = projection

        missing = sorted(set(raw_statuses).difference(current))
        if missing:
            raise CardLifecycleTransitionError(
                f"card lifecycle projection is missing an initial event: {missing[0]!r}"
            )

        connection.execute("DELETE FROM card_lifecycle_current")
        for card_version_id in sorted(current):
            projection = current[card_version_id]
            connection.execute(
                """
                INSERT INTO card_lifecycle_current(
                    card_version_id, status, suspended, last_sequence, last_event_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    projection.card_version_id,
                    projection.status,
                    int(projection.suspended),
                    projection.last_sequence,
                    projection.last_event_id,
                ),
            )

        connection.execute("DELETE FROM card_fts")
        for card_version_id in sorted(current):
            _refresh_card_fts(connection, current[card_version_id], delete_first=False)


def reopen_card_version(
    connection: sqlite3.Connection,
    card_version_id: str,
) -> ReopenedCardVersion:
    """Reopen a pinned old-course card even when suspended, with an explicit warning."""

    row = connection.execute(
        "SELECT payload_json FROM cards WHERE version_id = ?",
        (card_version_id,),
    ).fetchone()
    if row is None:
        raise CardLifecycleTransitionError(f"card version does not exist: {card_version_id!r}")
    stored = KnowledgeCardVersion.model_validate_json(row[0])
    if not lifecycle_schema_available(connection):
        return ReopenedCardVersion(
            card=stored,
            suspended=False,
            warnings=("LIFECYCLE_MIGRATION_PENDING",),
        )
    projection = get_card_lifecycle(connection, card_version_id)
    projected_card = stored.model_copy(update={"status": projection.status})
    warnings = ("CARD_VERSION_SUSPENDED",) if projection.suspended else ()
    return ReopenedCardVersion(
        card=projected_card,
        suspended=projection.suspended,
        warnings=warnings,
    )


def refresh_card_fts(connection: sqlite3.Connection, card_version_id: str) -> None:
    """Refresh one FTS row from the effective lifecycle projection."""

    _refresh_card_fts(connection, get_card_lifecycle(connection, card_version_id))


def _validate_request(
    event_id: str,
    request_digest: str,
    occurred_at: datetime,
    actor_id: str,
) -> None:
    if not event_id or len(event_id) > 256:
        raise ValueError("event_id must be non-empty and bounded")
    if len(request_digest) != 64 or any(
        character not in "0123456789abcdef" for character in request_digest
    ):
        raise ValueError("request_digest must be lowercase SHA-256")
    if occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    if not actor_id or len(actor_id) > 256:
        raise ValueError("actor_id must be non-empty and bounded")


def _next_state(
    current: CardLifecycleProjection,
    event_type: LifecycleEventType,
) -> tuple[CardStatus, bool]:
    if event_type == "publish":
        if current.status != "review" or current.suspended:
            raise CardLifecycleTransitionError("publish requires an unsuspended review card")
        return "published", False
    if event_type == "supersede":
        if current.status != "published":
            raise CardLifecycleTransitionError("supersede requires a published card")
        return "superseded", current.suspended
    if event_type == "archive":
        if current.status == "archived":
            raise CardLifecycleTransitionError("archive requires a non-archived card")
        return "archived", current.suspended
    if event_type == "suspend":
        if current.suspended:
            raise CardLifecycleTransitionError("card is already suspended")
        return current.status, True
    if event_type == "reinstate":
        if not current.suspended:
            raise CardLifecycleTransitionError("reinstate requires a suspended card")
        return current.status, False
    raise CardLifecycleTransitionError(f"unknown lifecycle event: {event_type!r}")


def _insert_event(
    connection: sqlite3.Connection,
    *,
    card_version_id: str,
    event_id: str,
    request_digest: str,
    event_type: str,
    status_before: CardStatus,
    status_after: CardStatus,
    suspended_before: bool,
    suspended_after: bool,
    sequence: int,
    occurred_at: datetime,
    actor_id: str,
) -> CardLifecycleProjection:
    payload = json.dumps(
        {
            "actor_id": actor_id,
            "card_version_id": card_version_id,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": _utc_timestamp(occurred_at),
            "request_digest": request_digest,
            "sequence": sequence,
            "status_after": status_after,
            "status_before": status_before,
            "suspended_after": suspended_after,
            "suspended_before": suspended_before,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO card_lifecycle_events(
            event_id, card_version_id, sequence, event_type, request_digest,
            status_before, status_after, suspended_before, suspended_after,
            occurred_at, actor_id, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            card_version_id,
            sequence,
            event_type,
            request_digest,
            status_before,
            status_after,
            int(suspended_before),
            int(suspended_after),
            _utc_timestamp(occurred_at),
            actor_id,
            payload,
        ),
    )
    connection.execute(
        """
        INSERT INTO card_lifecycle_current(
            card_version_id, status, suspended, last_sequence, last_event_id
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(card_version_id) DO UPDATE SET
            status=excluded.status,
            suspended=excluded.suspended,
            last_sequence=excluded.last_sequence,
            last_event_id=excluded.last_event_id
        """,
        (card_version_id, status_after, int(suspended_after), sequence, event_id),
    )
    return CardLifecycleProjection(
        card_version_id=card_version_id,
        status=status_after,
        suspended=suspended_after,
        last_sequence=sequence,
        last_event_id=event_id,
    )


def _refresh_card_fts(
    connection: sqlite3.Connection,
    projection: CardLifecycleProjection,
    *,
    delete_first: bool = True,
) -> None:
    if delete_first:
        connection.execute("DELETE FROM card_fts WHERE version_id = ?", (projection.card_version_id,))
    if projection.status != "published" or projection.suspended:
        return
    row = connection.execute(
        "SELECT payload_json FROM cards WHERE version_id = ?",
        (projection.card_version_id,),
    ).fetchone()
    if row is None:
        raise CardLifecycleTransitionError(
            f"card payload is unavailable: {projection.card_version_id!r}"
        )
    card = KnowledgeCardVersion.model_validate_json(row[0]).model_copy(
        update={"status": "published"}
    )
    body = _card_body(card.content_ast)
    chunks: list[str] = []
    for citation in card.chunk_citations:
        chunk_row = connection.execute(
            "SELECT source_version_id, payload_json FROM chunks WHERE chunk_id = ?",
            (citation.chunk_id,),
        ).fetchone()
        if chunk_row is None or chunk_row[0] != citation.source_version_id:
            raise CardLifecycleTransitionError(
                f"card citation is unavailable: {citation.chunk_id!r}"
            )
        chunks.append(ExtractedChunk.model_validate_json(chunk_row[1]).normalized_text)
    chunk_text = "\n".join(chunks)
    projected_text = "\n".join(
        part for part in (card.title, card.learning_objective, body, chunk_text) if part
    )
    connection.execute(
        """
        INSERT INTO card_fts(
            version_id, title, learning_objective, body, chunk_text, projected_text
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            card.version_id,
            card.title,
            card.learning_objective,
            body,
            chunk_text,
            projected_text,
        ),
    )


def _card_body(nodes: tuple[CardContentNode, ...]) -> str:
    parts: list[str] = []
    pending = list(reversed(nodes))
    while pending:
        node = pending.pop()
        if node.text:
            parts.append(node.text)
        parts.extend(cell for row in node.rows for cell in row if cell)
        pending.extend(reversed(node.children))
    return "\n".join(parts)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _writer_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        if owns_transaction:
            connection.commit()
    except BaseException:
        if owns_transaction and connection.in_transaction:
            connection.rollback()
        raise


__all__ = [
    "CardLifecycleConflict",
    "CardLifecycleProjection",
    "CardLifecycleTransitionError",
    "ReopenedCardVersion",
    "append_card_lifecycle_event",
    "get_card_lifecycle",
    "lifecycle_schema_available",
    "rebuild_card_lifecycle_projection",
    "refresh_card_fts",
    "register_card_lifecycle",
    "reopen_card_version",
]
