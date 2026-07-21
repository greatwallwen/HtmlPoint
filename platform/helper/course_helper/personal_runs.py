"""Compare-and-swap persistence for resumable personal course runs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast

from course_helper.catalog import KnowledgeCatalog, canonical_model_json
from course_helper.domain.composition import canonical_digest
from course_helper.domain.personal_course import (
    AttentionBundle,
    PersonalCourseRequest,
    PersonalCourseResult,
    PersonalCourseRun,
    PersonalCourseStatus,
)


Clock = Callable[[], datetime]


class PersonalRunConflict(RuntimeError):
    """A stale writer attempted to replace a newer run revision."""


class PersonalRunNotFound(LookupError):
    """The requested personal run does not exist."""


class PersonalRunIntegrityError(RuntimeError):
    """Stored projection columns and canonical payload disagree."""


def create_personal_run(
    catalog: KnowledgeCatalog,
    request: PersonalCourseRequest,
    *,
    source_snapshot_digest: str,
    clock: Clock,
) -> PersonalCourseRun:
    request_digest = canonical_digest(request)
    run_id = "personal-run-" + canonical_digest(
        {
            "request_digest": request_digest,
            "source_snapshot_digest": source_snapshot_digest,
        }
    )[:32]
    timestamp = clock()
    candidate = PersonalCourseRun(
        run_id=run_id,
        request=request,
        request_digest=request_digest,
        source_snapshot_digest=source_snapshot_digest,
        status="queued",
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    with catalog.atomic_write():
        existing = _find_by_snapshot(catalog, request_digest, source_snapshot_digest)
        if existing is not None:
            return existing
        catalog.connection.execute(
            """
            INSERT INTO personal_course_runs(
                run_id, request_digest, source_snapshot_digest, status,
                revision, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.run_id,
                candidate.request_digest,
                candidate.source_snapshot_digest,
                candidate.status,
                candidate.revision,
                canonical_model_json(candidate),
                candidate.updated_at.isoformat(),
            ),
        )
    return candidate


def get_personal_run(
    catalog: KnowledgeCatalog,
    run_id: str,
) -> PersonalCourseRun | None:
    row = catalog.connection.execute(
        """
        SELECT request_digest, source_snapshot_digest, status, revision,
               payload_json, updated_at
        FROM personal_course_runs
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return None if row is None else _decode_row(run_id, row)


def advance_personal_run(
    catalog: KnowledgeCatalog,
    run_id: str,
    *,
    expected_revision: int,
    next_status: PersonalCourseStatus,
    evidence_id: str,
    clock: Clock,
    attention_bundle: AttentionBundle | None = None,
    result: PersonalCourseResult | None = None,
    failure_message: str | None = None,
) -> PersonalCourseRun:
    with catalog.atomic_write():
        current = get_personal_run(catalog, run_id)
        if current is None:
            raise PersonalRunNotFound("personal course run does not exist")
        if current.revision != expected_revision:
            raise PersonalRunConflict("personal course run revision conflict")
        updated = current.advance(
            next_status,
            evidence_id=evidence_id,
            updated_at=clock(),
            attention_bundle=attention_bundle,
            result=result,
            failure_message=failure_message,
        )
        changed = catalog.connection.execute(
            """
            UPDATE personal_course_runs
            SET status = ?, revision = ?, payload_json = ?, updated_at = ?
            WHERE run_id = ? AND revision = ?
            """,
            (
                updated.status,
                updated.revision,
                canonical_model_json(updated),
                updated.updated_at.isoformat(),
                run_id,
                expected_revision,
            ),
        ).rowcount
        if changed != 1:
            raise PersonalRunConflict("personal course run revision conflict")
    return updated


def resolve_personal_attention(
    catalog: KnowledgeCatalog,
    run_id: str,
    *,
    expected_revision: int,
    resume_status: PersonalCourseStatus,
    evidence_id: str,
    clock: Clock,
) -> PersonalCourseRun:
    return advance_personal_run(
        catalog,
        run_id,
        expected_revision=expected_revision,
        next_status=resume_status,
        evidence_id=evidence_id,
        clock=clock,
    )


def _find_by_snapshot(
    catalog: KnowledgeCatalog,
    request_digest: str,
    source_snapshot_digest: str,
) -> PersonalCourseRun | None:
    row = catalog.connection.execute(
        """
        SELECT run_id, request_digest, source_snapshot_digest, status, revision,
               payload_json, updated_at
        FROM personal_course_runs
        WHERE request_digest = ? AND source_snapshot_digest = ?
        """,
        (request_digest, source_snapshot_digest),
    ).fetchone()
    return None if row is None else _decode_row(cast(str, row[0]), row[1:])


def _decode_row(run_id: str, row: tuple[object, ...]) -> PersonalCourseRun:
    try:
        run = PersonalCourseRun.model_validate_json(cast(str, row[4]))
    except Exception as error:
        raise PersonalRunIntegrityError("personal course run payload is invalid") from error
    projected = (
        run.request_digest,
        run.source_snapshot_digest,
        run.status,
        run.revision,
        run.updated_at.isoformat(),
    )
    stored = (row[0], row[1], row[2], row[3], row[5])
    if run.run_id != run_id or projected != stored:
        raise PersonalRunIntegrityError("personal course run projection mismatch")
    return run


__all__ = [
    "PersonalRunConflict",
    "PersonalRunIntegrityError",
    "PersonalRunNotFound",
    "advance_personal_run",
    "create_personal_run",
    "get_personal_run",
    "resolve_personal_attention",
]
