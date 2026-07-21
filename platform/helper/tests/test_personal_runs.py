from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from course_helper.catalog import CURRENT_MIGRATION_VERSION, KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.domain.personal_course import AttentionBundle, AttentionItem, PersonalCourseRequest
from course_helper.personal_runs import (
    PersonalRunConflict,
    advance_personal_run,
    create_personal_run,
    get_personal_run,
    resolve_personal_attention,
)


NOW = datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc)


def _request() -> PersonalCourseRequest:
    return PersonalCourseRequest(
        request_id="personal-request-fedcba9876543210fedcba9876543210",
        prompt="把已选资料编排成一门个人可直接使用的课程",
        source_version_ids=("source-v1", "source-v2"),
        created_at=NOW,
        requested_by=ActorRef(actor_type="human", actor_id="local-user"),
    )


def _attention() -> AttentionBundle:
    return AttentionBundle(
        bundle_id="attention-bundle-1",
        created_at=NOW,
        items=(
            AttentionItem(
                attention_id="attention-1",
                kind="source-read",
                title="一个资料需要处理",
                message="请选择重试或排除此资料。",
                allowed_actions=("retry", "exclude-source"),
                recommended_action="retry",
            ),
        ),
    )


def test_migration_and_create_are_idempotent(tmp_path: Path) -> None:
    assert CURRENT_MIGRATION_VERSION == 8
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        first = create_personal_run(
            catalog,
            _request(),
            source_snapshot_digest="b" * 64,
            clock=lambda: NOW,
        )
        reopened = create_personal_run(
            catalog,
            _request(),
            source_snapshot_digest="b" * 64,
            clock=lambda: NOW + timedelta(minutes=1),
        )

        assert reopened == first
        assert reopened.run_id.startswith("personal-run-")
        assert get_personal_run(catalog, first.run_id) == first
        assert catalog.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() == (8,)


def test_advance_uses_revision_compare_and_swap(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        queued = create_personal_run(
            catalog,
            _request(),
            source_snapshot_digest="c" * 64,
            clock=lambda: NOW,
        )
        importing = advance_personal_run(
            catalog,
            queued.run_id,
            expected_revision=1,
            next_status="importing",
            evidence_id="evidence-import",
            clock=lambda: NOW + timedelta(seconds=1),
        )

        assert importing.revision == 2
        with pytest.raises(PersonalRunConflict, match="revision"):
            advance_personal_run(
                catalog,
                queued.run_id,
                expected_revision=1,
                next_status="failed",
                evidence_id="evidence-stale",
                failure_message="stale writer",
                clock=lambda: NOW + timedelta(seconds=2),
            )


def test_attention_resolution_resumes_the_named_phase(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        run = create_personal_run(
            catalog,
            _request(),
            source_snapshot_digest="d" * 64,
            clock=lambda: NOW,
        )
        importing = advance_personal_run(
            catalog,
            run.run_id,
            expected_revision=1,
            next_status="importing",
            evidence_id="evidence-import",
            clock=lambda: NOW + timedelta(seconds=1),
        )
        attention = advance_personal_run(
            catalog,
            run.run_id,
            expected_revision=importing.revision,
            next_status="needs_attention",
            evidence_id="evidence-attention",
            attention_bundle=_attention(),
            clock=lambda: NOW + timedelta(seconds=2),
        )
        resumed = resolve_personal_attention(
            catalog,
            run.run_id,
            expected_revision=attention.revision,
            resume_status="importing",
            evidence_id="evidence-resolution",
            clock=lambda: NOW + timedelta(seconds=3),
        )

        assert resumed.status == "importing"
        assert resumed.attention_bundle is None
        assert resumed.phase_evidence_ids[-1] == "evidence-resolution"
