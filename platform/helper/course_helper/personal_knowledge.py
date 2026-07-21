"""Deterministic safe-automation policy for personal knowledge cards."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from course_helper.cards import (
    PERSONAL_VOCABULARY_VERSION_ID,
    canonical_card_content_digest,
    publish_card,
    seed_personal_vocabulary,
)
from course_helper.catalog import KnowledgeCatalog, canonical_model_json
from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.knowledge import (
    KnowledgeCardVersion,
    ReviewTask,
    TagAssignment,
)
from course_helper.domain.personal_course import AttentionItem
from course_helper.domain.sources import (
    ExtractedChunk,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.reviews import ReviewResolution, resolve_review_task


class KnowledgeOrganizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    published_card_version_ids: tuple[str, ...]
    attention_items: tuple[AttentionItem, ...]
    evidence: EvidenceObject


def organize_personal_knowledge(
    catalog: KnowledgeCatalog,
    source_version_ids: tuple[str, ...],
    actor: ActorRef,
) -> KnowledgeOrganizationResult:
    """Publish only source-bound candidates whose remaining risk is empty."""

    if not source_version_ids or len(set(source_version_ids)) != len(source_version_ids):
        raise ValueError("personal knowledge source IDs must be non-empty and unique")
    missing = tuple(
        source_id
        for source_id in source_version_ids
        if not catalog._row_exists("sources", "version_id", source_id)
    )
    if missing:
        raise ValueError("personal knowledge source is unavailable")

    seed_personal_vocabulary(catalog)
    candidates = _source_candidates(catalog, frozenset(source_version_ids))
    published: list[str] = []
    attention: list[AttentionItem] = []
    observed_at = max(
        (candidate.created_at for candidate in candidates),
        default=datetime.fromisoformat("2026-07-21T00:00:00+00:00"),
    )

    for candidate in candidates:
        card_attention = _card_attention(catalog, candidate)
        open_tasks = _open_tasks(catalog, candidate.version_id)
        for task in open_tasks:
            if _safe_task(catalog, candidate, task):
                _resolve_safe_task(catalog, task, actor)
            else:
                card_attention.append(_task_attention(task))
        card_attention = _unique_attention(card_attention)
        if card_attention:
            attention.extend(card_attention)
            continue

        organized = _organized_card(catalog, candidate, actor)
        published_card = publish_card(organized, catalog)
        published.append(published_card.version_id)

    published_ids = tuple(dict.fromkeys(published))
    attention_items = tuple(_unique_attention(attention))
    evidence = _organization_evidence(
        source_version_ids=source_version_ids,
        candidate_ids=tuple(card.version_id for card in candidates),
        published_ids=published_ids,
        attention_items=attention_items,
        observed_at=observed_at,
    )
    catalog.insert_evidence(evidence)
    return KnowledgeOrganizationResult(
        published_card_version_ids=published_ids,
        attention_items=attention_items,
        evidence=evidence,
    )


def _source_candidates(
    catalog: KnowledgeCatalog,
    source_ids: frozenset[str],
) -> tuple[KnowledgeCardVersion, ...]:
    rows = catalog.connection.execute(
        """
        SELECT cards.payload_json
        FROM cards
        JOIN card_lifecycle_current lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE lifecycle.status = 'review' AND lifecycle.suspended = 0
        ORDER BY cards.logical_id, cards.revision, cards.version_id
        LIMIT 1000
        """
    ).fetchall()
    cards = tuple(KnowledgeCardVersion.model_validate_json(str(row[0])) for row in rows)
    return tuple(
        card
        for card in cards
        if card.chunk_citations
        and {citation.source_version_id for citation in card.chunk_citations}.issubset(source_ids)
    )


def _open_tasks(catalog: KnowledgeCatalog, card_version_id: str) -> tuple[ReviewTask, ...]:
    rows = catalog.connection.execute(
        """
        SELECT task.payload_json
        FROM review_tasks task
        JOIN review_task_current current USING(task_id)
        WHERE task.subject_version_id = ? AND current.current_status = 'open'
        ORDER BY task.task_id
        """,
        (card_version_id,),
    ).fetchall()
    return tuple(ReviewTask.model_validate_json(str(row[0]), strict=False) for row in rows)


def _safe_task(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
    task: ReviewTask,
) -> bool:
    if task.kind == "provenance":
        return all(
            catalog.connection.execute(
                "SELECT source_version_id FROM chunks WHERE chunk_id = ?",
                (citation.chunk_id,),
            ).fetchone()
            == (citation.source_version_id,)
            for citation in card.chunk_citations
        )
    if task.kind == "exact-duplicate":
        return True
    if task.kind != "near-duplicate" or len(task.evidence_ids) != 1:
        return False
    row = catalog.connection.execute(
        "SELECT payload_json FROM evidence WHERE evidence_id = ?",
        (task.evidence_ids[0],),
    ).fetchone()
    if row is None:
        return False
    try:
        evidence = EvidenceObject.model_validate_json(str(row[0]), strict=False)
        output = evidence.model_dump(mode="json")["output_summary"]
        candidate_ids = output["candidate_ids"]
        semantic_status = output["semantic_status"]
    except Exception:
        return False
    if not isinstance(candidate_ids, list) or candidate_ids:
        return False
    if semantic_status == "available":
        return True
    published_count = catalog.connection.execute(
        """
        SELECT COUNT(*)
        FROM cards
        JOIN card_lifecycle_current lifecycle
          ON lifecycle.card_version_id = cards.version_id
        WHERE lifecycle.status = 'published' AND lifecycle.suspended = 0
        """
    ).fetchone()[0]
    return published_count == 0


def _resolve_safe_task(
    catalog: KnowledgeCatalog,
    task: ReviewTask,
    actor: ActorRef,
) -> None:
    expected_digest = hashlib.sha256(
        canonical_model_json(task).encode("utf-8")
    ).hexdigest()
    decision = "dismiss" if task.kind == "near-duplicate" else "accept"
    resolution_id = "resolution-" + hashlib.sha256(
        json.dumps(
            {
                "decision": decision,
                "expected_review_digest": expected_digest,
                "task_id": task.task_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    resolve_review_task(
        catalog,
        ReviewResolution(
            resolution_id=resolution_id,
            task_id=task.task_id,
            decision=decision,
            expected_review_digest=expected_digest,
            resolved_at=task.created_at,
            resolved_by=actor,
        ),
    )


def _card_attention(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
) -> list[AttentionItem]:
    unsafe = False
    for reference in card.visual_refs:
        row = catalog.connection.execute(
            "SELECT payload_json FROM visuals WHERE version_id = ?",
            (reference.visual_version_id,),
        ).fetchone()
        if row is None:
            unsafe = True
            continue
        visual = VisualAssetVersion.model_validate_json(str(row[0]))
        unsafe = unsafe or visual.license_status in {"unknown", "restricted"}
        unsafe = unsafe or visual.authenticity == "unverified"
    if not unsafe:
        return []
    return [
        AttentionItem(
            attention_id="attention-visual-" + hashlib.sha256(
                card.version_id.encode("utf-8")
            ).hexdigest()[:32],
            kind="visual-license",
            title="确认图形使用方式",
            message="一个知识单元包含授权或真实性尚未核验的图形。",
            allowed_actions=("continue-without-visual", "use-source-visual"),
            recommended_action="continue-without-visual",
        )
    ]


def _task_attention(task: ReviewTask) -> AttentionItem:
    if task.kind in {"visual-rights", "visual-unverified"}:
        return AttentionItem(
            attention_id="attention-visual-" + hashlib.sha256(
                task.subject_version_id.encode("utf-8")
            ).hexdigest()[:32],
            kind="visual-license",
            title="确认图形使用方式",
            message="一个知识单元包含授权或真实性尚未核验的图形。",
            allowed_actions=("continue-without-visual", "use-source-visual"),
            recommended_action="continue-without-visual",
        )
    return AttentionItem(
        attention_id="attention-conflict-" + hashlib.sha256(
            task.subject_version_id.encode("utf-8")
        ).hexdigest()[:32],
        kind="knowledge-conflict",
        title="确认知识处理方式",
        message="一个知识单元存在重复、冲突或需要人工判断的内容。",
        allowed_actions=("approve", "reject"),
        recommended_action="reject",
    )


def _unique_attention(items: list[AttentionItem]) -> list[AttentionItem]:
    return list({item.attention_id: item for item in items}.values())


def _organized_card(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
    actor: ActorRef,
) -> KnowledgeCardVersion:
    title = _source_title(catalog, card)
    assignments = _personal_tags(catalog, card)
    organized = card.model_copy(
        update={
            "title": title,
            "vocabulary_version_id": PERSONAL_VOCABULARY_VERSION_ID,
            "tag_assignments": assignments,
            "created_by": actor,
            "status": "review",
        }
    )
    return organized.model_copy(
        update={"content_digest": canonical_card_content_digest(organized)}
    )


def _source_title(catalog: KnowledgeCatalog, card: KnowledgeCardVersion) -> str:
    headings: list[str] = []
    for citation in card.chunk_citations:
        row = catalog.connection.execute(
            "SELECT payload_json FROM chunks WHERE chunk_id = ?",
            (citation.chunk_id,),
        ).fetchone()
        if row is None:
            continue
        chunk = ExtractedChunk.model_validate_json(str(row[0]))
        normalized = " ".join((chunk.heading or "").split())
        if normalized:
            headings.append(normalized)
    fallback = " ".join(card.title.split()) or "来源知识单元"
    return (headings[0] if headings else fallback)[:40]


def _personal_tags(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
) -> tuple[TagAssignment, ...]:
    text = " ".join(
        [card.title, card.learning_objective]
        + [node.text or "" for node in card.content_ast]
    ).casefold()
    topic = (
        "topic:data-analysis"
        if any(token in text for token in ("数据", "分析", "excel", "csv"))
        else "topic:prompting"
        if any(token in text for token in ("提示", "prompt"))
        else "topic:ai-foundations"
    )
    skill = (
        "skill:practice"
        if card.main_type_id in {"procedure", "example", "case", "exercise"}
        else "skill:analyze"
        if card.main_type_id in {"assessment", "evidence"}
        else "skill:explain"
    )
    source_kinds = {
        SourceAssetVersion.model_validate_json(str(row[0])).source_kind
        for citation in card.chunk_citations
        if (
            row := catalog.connection.execute(
                "SELECT payload_json FROM sources WHERE version_id = ?",
                (citation.source_version_id,),
            ).fetchone()
        )
        is not None
    }
    source_type = (
        "source-type:markdown"
        if source_kinds == {"markdown"}
        else "source-type:presentation"
        if source_kinds == {"pptx"}
        else "source-type:dataset"
        if source_kinds and source_kinds.issubset({"csv", "parquet", "xls", "xlsx", "duckdb"})
        else "source-type:mixed"
    )
    return tuple(
        TagAssignment(
            vocabulary_version_id=PERSONAL_VOCABULARY_VERSION_ID,
            dimension_id=dimension,
            tag_id=tag,
            assigned_by="rule",
            confidence=1.0,
        )
        for dimension, tag in (
            ("topic", topic),
            ("skill", skill),
            ("source-type", source_type),
        )
    )


def _organization_evidence(
    *,
    source_version_ids: tuple[str, ...],
    candidate_ids: tuple[str, ...],
    published_ids: tuple[str, ...],
    attention_items: tuple[AttentionItem, ...],
    observed_at: datetime,
) -> EvidenceObject:
    semantics = {
        "source_version_ids": source_version_ids,
        "candidate_ids": candidate_ids,
        "published_ids": published_ids,
        "attention_kinds": tuple(item.kind for item in attention_items),
    }
    evidence_id = "personal-knowledge-" + hashlib.sha256(
        json.dumps(semantics, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="publish",
        status="warning" if attention_items else "verified",
        input_summary={
            "source_version_ids": list(source_version_ids),
            "candidate_count": len(candidate_ids),
        },
        output_summary={
            "published_card_version_ids": list(published_ids),
            "attention_count": len(attention_items),
            "attention_kinds": [item.kind for item in attention_items],
        },
        producer="course-helper/personal-knowledge",
        producer_version="1",
        started_at=observed_at,
        finished_at=observed_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="source-bound-publication",
                status="warning" if attention_items else "passed",
                message=(
                    "Unsafe knowledge items require one attention bundle"
                    if attention_items
                    else "Every published knowledge card is source-bound and conflict-free"
                ),
                details={
                    "candidate_count": len(candidate_ids),
                    "published_count": len(published_ids),
                    "attention_count": len(attention_items),
                },
            ),
        ),
    )


__all__ = ["KnowledgeOrganizationResult", "organize_personal_knowledge"]
