from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from course_helper.domain.common import ActorRef
from course_helper.domain.personal_course import (
    AttentionBundle,
    AttentionItem,
    PersonalCourseRequest,
    PersonalCourseRun,
    PersonalCourseResult,
)


NOW = datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="human", actor_id="local-user")


def request_fixture() -> PersonalCourseRequest:
    return PersonalCourseRequest(
        request_id="personal-request-0123456789abcdef0123456789abcdef",
        prompt="为产品团队编排一门 90 分钟的 AI 实战课程",
        source_version_ids=("source-v1", "source-v2"),
        created_at=NOW,
        requested_by=ACTOR,
    )


def personal_run_fixture(status: str = "queued") -> PersonalCourseRun:
    return PersonalCourseRun(
        run_id="personal-run-0123456789abcdef0123456789abcdef",
        request=request_fixture(),
        request_digest="a" * 64,
        source_snapshot_digest="b" * 64,
        status=status,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def attention_bundle() -> AttentionBundle:
    return AttentionBundle(
        bundle_id="attention-bundle-1",
        created_at=NOW,
        items=(
            AttentionItem(
                attention_id="attention-1",
                kind="visual-license",
                title="确认图形使用方式",
                message="来源图形缺少可核验的授权信息。",
                allowed_actions=("continue-without-visual", "use-source-visual"),
                recommended_action="continue-without-visual",
            ),
        ),
    )


def test_personal_run_allows_only_declared_transitions() -> None:
    run = personal_run_fixture()
    importing = run.advance(
        "importing",
        evidence_id="evidence-import",
        updated_at=NOW,
    )

    assert importing.status == "importing"
    assert importing.revision == 2
    assert importing.phase_evidence_ids == ("evidence-import",)
    with pytest.raises(ValueError, match="transition"):
        importing.advance("ready", evidence_id="evidence-skip", updated_at=NOW)


def test_attention_bundle_contains_no_safe_auto_action() -> None:
    with pytest.raises(ValidationError):
        AttentionItem(
            attention_id="attention-1",
            kind="visual-license",
            title="确认图形使用方式",
            message="来源图形缺少可核验的授权信息。",
            allowed_actions=("continue-without-visual",),
            recommended_action="ignore",
        )


def test_needs_attention_and_ready_require_their_payloads() -> None:
    with pytest.raises(ValidationError, match="attention"):
        personal_run_fixture("needs_attention")

    validating = personal_run_fixture("validating")
    result = PersonalCourseResult(
        title="AI 产品实战",
        course_version_id="course-v1",
        slide_deck_version_id="deck-v1",
        runtime_manifest_version_id="runtime-v1",
        chapter_count=3,
    )
    ready = validating.advance(
        "ready",
        evidence_id="evidence-ready",
        result=result,
        updated_at=NOW,
    )
    assert ready.result == result


def test_request_rejects_duplicate_sources_and_naive_time() -> None:
    with pytest.raises(ValidationError):
        PersonalCourseRequest(
            request_id="personal-request-0123456789abcdef0123456789abcdef",
            prompt="编排课程",
            source_version_ids=("source-v1", "source-v1"),
            created_at=datetime(2026, 7, 21, 2, 0),
            requested_by=ACTOR,
        )


def test_contracts_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PersonalCourseRequest.model_validate(
            {**request_fixture().model_dump(), "internal_id": "must-not-leak"}
        )
