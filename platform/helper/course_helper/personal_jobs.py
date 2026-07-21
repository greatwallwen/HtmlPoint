"""Main-process typed jobs for persisted personal course orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, cast

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, ImmutableJsonValue
from course_helper.domain.composition import canonical_digest
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.personal_course import PersonalCourseRun, PersonalCourseStatus
from course_helper.jobs import (
    JobOutcome,
    PersonalCourseCreateJob,
    PersonalCourseResolveJob,
    PersonalCourseStatusJob,
    WorkerRuntimeConfig,
)
from course_helper.personal_orchestrator import create_personal_course_run
from course_helper.personal_runs import get_personal_run, resolve_personal_attention


class PersonalSupervisor(Protocol):
    def start(self, run_id: str, actor: ActorRef) -> object: ...


class PersonalJobError(ValueError):
    """A personal course job failed at the authenticated domain boundary."""


_PHASE = {
    "queued": ("creating", "准备创建课程"),
    "importing": ("creating", "正在读取资料"),
    "organizing_knowledge": ("creating", "正在整理知识"),
    "composing": ("creating", "正在编排课程"),
    "assigning_visuals": ("creating", "正在匹配真实图形"),
    "validating": ("creating", "正在验证课程"),
    "needs_attention": ("needs-attention", "需要你的确认"),
    "ready": ("ready", "课程已就绪"),
    "failed": ("failed", "创建未完成"),
}


def run_personal_job(
    job: PersonalCourseCreateJob | PersonalCourseStatusJob | PersonalCourseResolveJob,
    config: WorkerRuntimeConfig,
    supervisor: PersonalSupervisor | None,
) -> JobOutcome:
    if supervisor is None:
        raise PersonalJobError("personal course supervisor is unavailable")
    started_at = datetime.now(timezone.utc)
    if isinstance(job, PersonalCourseCreateJob):
        actor = job.actor.as_domain()
        run = create_personal_course_run(
            config,
            job.request.as_domain(actor),
            actor,
        )
        supervisor.start(run.run_id, actor)
        status_code = 202
        action = "create"
    elif isinstance(job, PersonalCourseStatusJob):
        actor = job.actor.as_domain()
        run = _owned_run(config.database_path, job.run_id, actor)
        status_code = 200
        action = "status"
    elif isinstance(job, PersonalCourseResolveJob):
        actor = job.actor.as_domain()
        run = _resolve_attention(config, job, actor)
        supervisor.start(run.run_id, actor)
        status_code = 202
        action = "resolve"
    else:
        raise PersonalJobError("personal course job is not allowlisted")
    with KnowledgeCatalog.open_read_only(config.database_path) as catalog:
        result = {
            "runId": run.run_id,
            "view": _public_view(catalog, run),
        }
    finished_at = datetime.now(timezone.utc)
    job_output: dict[str, ImmutableJsonValue] = {
        "publicStatus": cast(str, result["view"]["status"]),
        "attentionCount": cast(int, result["view"]["attentionCount"]),
    }
    if run.attention_bundle is not None:
        job_output.update(
            {
                "attentionDigest": canonical_digest(run.attention_bundle),
                "recommendedAction": run.attention_bundle.items[0].recommended_action,
            }
        )
    evidence = EvidenceObject(
        evidence_id="personal-job-"
        + canonical_digest(
            {
                "action": action,
                "run_id": run.run_id,
                "revision": run.revision,
                "status": run.status,
            }
        ),
        kind="runtime",
        status="verified",
        input_summary={"action": action},
        output_summary=job_output,
        producer="course-helper/personal-jobs",
        producer_version="1",
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        checks=(
            EvidenceCheck(
                code="public-personal-projection",
                status="passed",
                message="Personal course job returned only the bounded public projection",
            ),
        ),
    )
    return JobOutcome(
        status_code=status_code,
        evidence=evidence,
        result=cast(dict[str, ImmutableJsonValue], result),
    )


def _owned_run(
    database_path: str,
    run_id: str,
    actor: ActorRef,
) -> PersonalCourseRun:
    with KnowledgeCatalog.open_read_only(database_path) as catalog:
        run = get_personal_run(catalog, run_id)
    if run is None:
        raise PersonalJobError("personal course run does not exist")
    if actor != run.request.requested_by and actor.actor_type != "system":
        raise PersonalJobError("personal course run is not owned by this actor")
    return run


def _resolve_attention(
    config: WorkerRuntimeConfig,
    job: PersonalCourseResolveJob,
    actor: ActorRef,
) -> PersonalCourseRun:
    run = _owned_run(config.database_path, job.run_id, actor)
    bundle = run.attention_bundle
    if run.status != "needs_attention" or bundle is None:
        raise PersonalJobError("personal course run does not need attention")
    if canonical_digest(bundle) != job.expected_attention_digest:
        raise PersonalJobError("personal course attention changed")
    allowed = {action for item in bundle.items for action in item.allowed_actions}
    if job.action not in allowed:
        raise PersonalJobError("personal course attention action is unavailable")
    with KnowledgeCatalog.open(config.database_path) as catalog:
        phase = _attention_phase(catalog, run)
        resume_status = _resume_status(phase, job.action)
        now = datetime.now(timezone.utc)
        evidence = EvidenceObject(
            evidence_id="personal-resolution-"
            + canonical_digest(
                {
                    "run_id": run.run_id,
                    "attention_digest": job.expected_attention_digest,
                    "action": job.action,
                    "resume_status": resume_status,
                }
            ),
            kind="validation",
            status="verified",
            input_summary={
                "attention_digest": job.expected_attention_digest,
                "action": job.action,
            },
            output_summary={"resume_status": resume_status},
            producer="course-helper/personal-jobs",
            producer_version="1",
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )
        catalog.insert_evidence(evidence)
        return resolve_personal_attention(
            catalog,
            run.run_id,
            expected_revision=run.revision,
            resume_status=resume_status,
            evidence_id=evidence.evidence_id,
            clock=lambda: now,
        )


def _attention_phase(catalog: KnowledgeCatalog, run: PersonalCourseRun) -> str:
    for evidence_id in reversed(run.phase_evidence_ids):
        row = catalog.connection.execute(
            "SELECT payload_json FROM evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            continue
        evidence = EvidenceObject.model_validate_json(str(row[0]), strict=False)
        phase = evidence.input_summary.get("phase")
        if isinstance(phase, str):
            return phase
    raise PersonalJobError("personal course attention phase is unavailable")


def _resume_status(phase: str, action: str) -> PersonalCourseStatus:
    if phase == "assigning_visuals" and action == "continue-without-visual":
        return "validating"
    mapping: dict[str, PersonalCourseStatus] = {
        "importing": "importing",
        "organizing_knowledge": "organizing_knowledge",
        "composing": "composing",
        "assigning_visuals": "assigning_visuals",
        "validating": "validating",
    }
    try:
        return mapping[phase]
    except KeyError as error:
        raise PersonalJobError("personal course attention phase is invalid") from error


def _public_view(
    catalog: KnowledgeCatalog,
    run: PersonalCourseRun,
) -> dict[str, ImmutableJsonValue]:
    status, label = _PHASE[run.status]
    course = _public_course(catalog, run) if run.status == "ready" else None
    title = (
        run.result.title
        if run.result is not None
        else run.request.title_hint
    )
    return {
        "status": status,
        "phaseLabel": label,
        "title": title,
        "chapterCount": 0 if run.result is None else run.result.chapter_count,
        "attentionCount": (
            0 if run.attention_bundle is None else len(run.attention_bundle.items)
        ),
        "canResume": run.status == "needs_attention",
        "course": course,
    }


def _public_course(
    catalog: KnowledgeCatalog,
    run: PersonalCourseRun,
) -> dict[str, ImmutableJsonValue]:
    requirement = catalog.get_course_requirement("requirement-" + run.run_id)
    outline = catalog.get_course_outline("outline-version-" + run.run_id)
    if requirement is None or outline is None:
        raise PersonalJobError("ready personal course projection is unavailable")
    source_aliases = {
        source_id: f"source-{index}"
        for index, source_id in enumerate(run.request.source_version_ids, start=1)
    }
    sources: list[dict[str, ImmutableJsonValue]] = []
    for source_id in run.request.source_version_ids:
        source = catalog.get_source(source_id)
        if source is None:
            raise PersonalJobError("ready personal course source is unavailable")
        sources.append(
            {
                "id": source_aliases[source_id],
                "name": source.display_name,
                "kind": _public_source_kind(source.source_kind),
                "size": source.byte_size,
                "status": "ready",
                "addedAt": source.created_at.isoformat(),
            }
        )
    chapters: list[dict[str, ImmutableJsonValue]] = []
    for chapter_index, chapter in enumerate(outline.payload.chapters, start=1):
        lessons: list[dict[str, ImmutableJsonValue]] = []
        for lesson_index, placement in enumerate(chapter.placements, start=1):
            card = catalog.get_card(placement.card_version_id)
            if card is None:
                raise PersonalJobError("ready personal course card is unavailable")
            lessons.append(
                {
                    "id": f"lesson-{chapter_index}-{lesson_index}",
                    "title": card.title,
                    "summary": card.learning_objective,
                    "durationMinutes": placement.allocated_minutes,
                    "sourceIds": [
                        source_aliases[citation.source_version_id]
                        for citation in card.chunk_citations
                        if citation.source_version_id in source_aliases
                    ],
                    "status": "grounded",
                }
            )
        chapters.append(
            {
                "id": f"chapter-{chapter_index}",
                "title": chapter.title,
                "objective": chapter.objective,
                "lessons": lessons,
            }
        )
    return {
        "schemaVersion": 1,
        "id": "personal-course",
        "title": requirement.payload.title,
        "audience": requirement.payload.audience,
        "goal": "；".join(requirement.payload.learning_goals),
        "durationMinutes": requirement.payload.duration_minutes,
        "chapters": chapters,
        "sources": sources,
        "updatedAt": run.updated_at.isoformat(),
    }


def _public_source_kind(source_kind: str) -> str:
    return {
        "markdown": "markdown",
        "pptx": "pptx",
    }.get(source_kind, "text")


__all__ = ["PersonalJobError", "PersonalSupervisor", "run_personal_job"]
