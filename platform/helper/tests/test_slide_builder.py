from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from course_helper.cards import VOCABULARY_VERSION_ID, publish_card, seed_vocabulary
from course_helper.catalog import (
    KnowledgeCatalog,
    OutlineConfirmation,
    canonical_model_json,
)
from course_helper.composer import (
    CompositionOptions,
    compose_and_register,
    confirmation_summary,
)
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.composition import (
    CourseRequirement,
    CourseVersion,
    course_outline_content_digest,
)
from course_helper.domain.evidence import EvidenceObject, LineageEdge
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    DatasetReference,
    KnowledgeCardVersion,
    ReviewTask,
    TagAssignment,
)
from course_helper.domain.slide_ast import RuntimeJobBinding
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
from course_helper.lifecycle import append_card_lifecycle_event
from course_helper.operations import (
    IndexOutboxItem,
    OperationMutationResult,
    OperationRequest,
    run_operation,
)
from course_helper.retrieval import KnowledgeRetriever


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="slide-builder-tests")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedDraft:
    catalog: KnowledgeCatalog
    course: CourseVersion
    card: KnowledgeCardVersion
    source: SourceAssetVersion
    chunk: ExtractedChunk


def _prepare(
    catalog: KnowledgeCatalog,
    *,
    source_override: SourceAssetVersion | None = None,
    chunk_override: ExtractedChunk | None = None,
    dataset_refs: tuple[DatasetReference, ...] = (),
) -> PreparedDraft:
    seed_vocabulary(catalog)
    source = source_override or SourceAssetVersion(
        logical_id="source-slide-builder",
        version_id="source-slide-builder-v1",
        revision=1,
        content_digest=_digest("source-v1"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(root_id="fixture", relative_path="grounding.md"),
        display_name="grounding.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=64,
        extraction_status="parsed",
    )
    chunk = chunk_override or ExtractedChunk(
        chunk_id="chunk-slide-builder-v1",
        source_version_id=source.version_id,
        ordinal=0,
        modality="text",
        language="en",
        normalized_text="Explain grounding with traceable evidence.",
        content_digest=_digest("chunk-v1"),
        locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
        breadcrumb=("Grounding",),
        heading="Grounding",
    )
    catalog.insert_source(source)
    catalog.insert_chunk(chunk)
    candidate = KnowledgeCardVersion(
        logical_id="card-slide-builder",
        version_id="card-slide-builder-v1",
        revision=1,
        content_digest=_digest("candidate"),
        created_at=NOW,
        created_by=ACTOR,
        main_type_id="concept",
        title="Grounding",
        learning_objective="Explain grounding",
        content_ast=(
            CardContentNode(
                type="paragraph",
                text="Grounding connects each generated claim to inspectable evidence.",
            ),
            CardContentNode(
                type="list",
                children=(
                    CardContentNode(type="list-item", text="Pin the exact source version"),
                    CardContentNode(type="list-item", text="Keep evidence visible"),
                ),
            ),
        ),
        suggested_minutes=10,
        vocabulary_version_id=VOCABULARY_VERSION_ID,
        tag_assignments=tuple(
            TagAssignment(
                vocabulary_version_id=VOCABULARY_VERSION_ID,
                dimension_id=tag_id.split(":", 1)[0],
                tag_id=tag_id,
            )
            for tag_id in (
                "topic:ai-foundations",
                "audience:learner",
                "difficulty:beginner",
            )
        ),
        chunk_citations=(
            ChunkCitation(
                chunk_id=chunk.chunk_id,
                source_version_id=source.version_id,
                quoted_text=(
                    "traceable evidence"
                    if "traceable evidence" in chunk.normalized_text
                    else None
                ),
            ),
        ),
        dataset_refs=dataset_refs,
        status="review",
    )
    card = publish_card(candidate, catalog)
    run_operation(
        catalog,
        OperationRequest(
            operation_id="operation-slide-builder-index",
            request_digest=_digest("slide-builder-index"),
            actor=ACTOR,
            session_id="slide-builder-session",
        ),
        lambda: OperationMutationResult(
            result_refs={},
            item_outcomes=(),
            index_outbox=(
                IndexOutboxItem(
                    outbox_id="outbox-slide-builder-index",
                    card_version_id=card.version_id,
                    action="upsert",
                ),
            ),
        ),
        clock=lambda: NOW,
    )
    claim = claim_next_index_outbox(
        catalog,
        worker_id="slide-builder-worker",
        now=NOW,
        lease_seconds=30,
    )
    assert claim is not None
    snapshot = complete_index_claim(
        catalog,
        claim_id=claim.claim_id,
        worker_id="slide-builder-worker",
        embedding_provider=None,
        now=NOW + timedelta(seconds=1),
    )
    requirement = CourseRequirement(
        requirement_id="requirement-slide-builder",
        title="Grounded course",
        audience="Learners",
        learning_goals=("Explain grounding",),
        duration_minutes=10,
        required_tag_ids=("topic:ai-foundations",),
        usage_scope="internal",
    )
    catalog.register_course_requirement(requirement, clock=lambda: NOW)
    composition = compose_and_register(
        catalog,
        KnowledgeRetriever(catalog),
        requirement,
        logical_id="outline-slide-builder",
        version_id="outline-slide-builder-v1",
        revision=1,
        created_at=NOW,
        created_by=ACTOR,
        options=CompositionOptions(
            audience_tag_id="audience:learner",
            difficulty_tag_id="difficulty:beginner",
            index_snapshot_id=snapshot.index_snapshot_id,
        ),
    )
    summary = confirmation_summary(composition.outline, requirement)
    confirmation = catalog.confirm_course_outline(
        OutlineConfirmation(
            confirmation_id="confirmation-slide-builder-v1",
            requirement_id=requirement.requirement_id,
            outline_version_id=composition.outline.version_id,
            expected_outline_digest=composition.outline.content_digest,
            confirmation_digest=summary.confirmation_digest,
            confirmed_by=ACTOR,
        ),
        clock=lambda: NOW,
    ).payload
    course = CourseVersion(
        logical_id="course-slide-builder",
        version_id="course-slide-builder-v1",
        revision=1,
        content_digest=_digest("course-slide-builder-v1"),
        created_at=NOW,
        created_by=ACTOR,
        requirement_id=requirement.requirement_id,
        outline_version_id=composition.outline.version_id,
        outline_digest=composition.outline.content_digest,
        placement_ids=tuple(
            placement.placement_id
            for chapter in composition.outline.chapters
            for placement in chapter.placements
        ),
        usage_scope=requirement.usage_scope,
        confirmation_digest=confirmation.confirmation_digest,
        status="confirmed",
    )
    catalog.register_course_version(course, clock=lambda: NOW)
    return PreparedDraft(catalog, course, card, source, chunk)


@pytest.fixture
def prepared(tmp_path: Path):
    with KnowledgeCatalog.open(tmp_path / "slide-builder.db") as catalog:
        yield _prepare(catalog)


def _flatten(nodes):
    ordered = []
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(node.children))
    return tuple(ordered)


def test_builds_deterministic_content_only_draft_with_exact_lineage(
    prepared: PreparedDraft,
) -> None:
    from course_helper.slide_builder import build_and_register_draft

    first = build_and_register_draft(
        prepared.catalog,
        prepared.course.version_id,
        actor=ACTOR,
        clock=lambda: NOW,
    )
    replay = build_and_register_draft(
        prepared.catalog,
        prepared.course.version_id,
        actor=ActorRef(actor_type="human", actor_id="later-author"),
        clock=lambda: NOW + timedelta(days=1),
    )

    assert replay == first
    assert first.stored_deck.payload_json == replay.stored_deck.payload_json
    assert first.stored_manifest.payload_json == replay.stored_manifest.payload_json
    nodes = _flatten(first.deck.nodes)
    assert nodes
    for node in nodes:
        assert node.placement_ids == prepared.course.placement_ids
        assert node.card_version_ids == (prepared.card.version_id,)
        assert node.chunk_ids == (prepared.chunk.chunk_id,)
        assert node.source_version_ids == (prepared.source.version_id,)
        assert node.evidence_ids
        assert not node.asset_bindings
    root = first.deck.nodes[0]
    stage_text = {node.text for node in nodes if node.text}
    assert root.presenter_notes
    assert root.presenter_notes not in stage_text
    assert all(len(text) <= 500 for text in stage_text)
    assert first.runtime_manifest.artifact_ids == ()
    assert first.runtime_manifest.job_bindings == ()


@pytest.mark.parametrize("event_type", ("suspend", "archive"))
def test_rejects_lifecycle_invalid_pinned_card(
    prepared: PreparedDraft,
    event_type: str,
) -> None:
    from course_helper.slide_builder import SlideBuildError, build_and_register_draft

    append_card_lifecycle_event(
        prepared.catalog.connection,
        card_version_id=prepared.card.version_id,
        event_id=f"{event_type}:slide-builder",
        request_digest=_digest(event_type),
        event_type=event_type,
        occurred_at=NOW + timedelta(minutes=1),
        actor_id=ACTOR.actor_id,
    )
    with pytest.raises(SlideBuildError, match="lifecycle"):
        build_and_register_draft(
            prepared.catalog,
            prepared.course.version_id,
            actor=ACTOR,
            clock=lambda: NOW,
        )


def test_rejects_digest_invalid_or_dangling_pinned_lineage(
    prepared: PreparedDraft,
) -> None:
    from course_helper.slide_builder import SlideBuildError, build_and_register_draft

    tampered = prepared.card.model_copy(update={"content_digest": "f" * 64})
    prepared.catalog.connection.execute(
        "DROP TRIGGER cards_immutable_lifecycle_columns"
    )
    prepared.catalog.connection.execute(
        "UPDATE cards SET content_digest = ?, payload_json = ? WHERE version_id = ?",
        (tampered.content_digest, canonical_model_json(tampered), tampered.version_id),
    )
    prepared.catalog.connection.commit()
    with pytest.raises(SlideBuildError, match="content digest"):
        build_and_register_draft(
            prepared.catalog,
            prepared.course.version_id,
            actor=ACTOR,
            clock=lambda: NOW,
        )


def test_rejects_invalid_chunk_envelope_and_revoked_source(tmp_path: Path) -> None:
    from course_helper.slide_builder import SlideBuildError, build_and_register_draft

    with KnowledgeCatalog.open(tmp_path / "invalid-chunk.db") as catalog:
        value = _prepare(catalog)
        catalog.connection.execute(
            "UPDATE chunks SET content_digest = ? WHERE chunk_id = ?",
            ("f" * 64, value.chunk.chunk_id),
        )
        catalog.connection.commit()
        with pytest.raises(SlideBuildError, match="chunk digest"):
            build_and_register_draft(
                catalog, value.course.version_id, actor=ACTOR, clock=lambda: NOW
            )

    with KnowledgeCatalog.open(tmp_path / "revoked-source.db") as catalog:
        value = _prepare(catalog)
        revoked = value.source.model_copy(update={"extraction_status": "failed"})
        catalog.connection.execute(
            "UPDATE sources SET payload_json = ? WHERE version_id = ?",
            (canonical_model_json(revoked), revoked.version_id),
        )
        catalog.connection.commit()
        with pytest.raises(SlideBuildError, match="revoked"):
            build_and_register_draft(
                catalog, value.course.version_id, actor=ACTOR, clock=lambda: NOW
            )


def test_accepts_registered_governed_source_with_verified_extraction(
    tmp_path: Path,
) -> None:
    from course_helper.slide_builder import build_and_register_draft
    from course_helper.uploads import GovernedSourceBlob, UploadRecord

    source = SourceAssetVersion(
        logical_id="source-governed",
        version_id="source-governed-v1",
        revision=1,
        content_digest=_digest("governed-source"),
        created_at=NOW,
        created_by=ACTOR,
        locator=SourceLocator(
            root_id="governed-upload",
            relative_path="sha256/aa/governed-source.blob",
        ),
        display_name="governed.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=64,
        extraction_status="registered",
    )
    with KnowledgeCatalog.open(tmp_path / "governed-source.db") as catalog:
        value = _prepare(catalog, source_override=source)
        blob = GovernedSourceBlob(
            source_version_id=source.version_id,
            source_logical_id=source.logical_id,
            upload_id="upload-" + "1" * 32,
            blob_digest=source.content_digest,
            safe_name=source.display_name,
            source_kind="markdown",
            media_type=source.media_type,
            byte_size=source.byte_size,
            created_at=NOW,
        )
        upload = UploadRecord(
            upload_id=blob.upload_id,
            safe_name=blob.safe_name,
            source_kind=blob.source_kind,
            media_type=blob.media_type,
            byte_size=blob.byte_size,
            content_digest=blob.blob_digest,
            state="promoted",
            expires_at=NOW + timedelta(hours=1),
            created_at=NOW,
            updated_at=NOW,
        )
        upload_payload = canonical_model_json(upload)
        catalog.connection.execute(
            "INSERT INTO governed_uploads(upload_id, session_digest, safe_name, "
            "source_kind, media_type, byte_size, content_digest, state, expires_at, "
            "payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                upload.upload_id,
                _digest("session"),
                upload.safe_name,
                upload.source_kind,
                upload.media_type,
                upload.byte_size,
                upload.content_digest,
                upload.state,
                upload.expires_at.isoformat(),
                upload_payload,
                upload.created_at.isoformat(),
                upload.updated_at.isoformat(),
            ),
        )
        blob_payload = canonical_model_json(blob)
        catalog.connection.execute(
            "INSERT INTO governed_source_blobs(source_version_id, source_logical_id, "
            "upload_id, blob_digest, safe_name, source_kind, media_type, byte_size, "
            "status, content_digest, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                blob.source_version_id,
                blob.source_logical_id,
                blob.upload_id,
                blob.blob_digest,
                blob.safe_name,
                blob.source_kind,
                blob.media_type,
                blob.byte_size,
                blob.status,
                hashlib.sha256(blob_payload.encode("utf-8")).hexdigest(),
                blob_payload,
                NOW.isoformat(),
            ),
        )
        catalog.connection.commit()
        catalog.insert_evidence(
            EvidenceObject(
                evidence_id="governed-extraction-evidence",
                kind="extraction",
                subject_version_id=source.version_id,
                status="verified",
                output_summary={"chunkCount": 1},
                producer="slide-builder-tests",
                started_at=NOW,
                finished_at=NOW,
            )
        )

        projection = build_and_register_draft(
            catalog, value.course.version_id, actor=ACTOR, clock=lambda: NOW
        )

        assert projection.deck.course_version_id == value.course.version_id


def test_rejects_missing_lineage_and_open_blocking_review(
    tmp_path: Path,
) -> None:
    from course_helper.slide_builder import SlideBuildError, build_and_register_draft

    with KnowledgeCatalog.open(tmp_path / "missing-lineage.db") as catalog:
        value = _prepare(catalog)
        catalog.connection.execute(
            "DELETE FROM lineage WHERE from_version_id = ? AND relation = 'cites'",
            (value.card.version_id,),
        )
        catalog.connection.commit()
        with pytest.raises(SlideBuildError, match="lineage"):
            build_and_register_draft(
                catalog, value.course.version_id, actor=ACTOR, clock=lambda: NOW
            )

    with KnowledgeCatalog.open(tmp_path / "blocking-review.db") as catalog:
        value = _prepare(catalog)
        catalog.insert_review_task(
            ReviewTask(
                task_id="review-slide-builder-blocking",
                kind="manual-review",
                subject_version_id=value.card.version_id,
                status="open",
                blocking=True,
                created_at=NOW,
                created_by=ACTOR,
            )
        )
        with pytest.raises(SlideBuildError, match="blocking review"):
            build_and_register_draft(
                catalog, value.course.version_id, actor=ACTOR, clock=lambda: NOW
            )


def test_rejects_nonpublication_evidence_substituted_for_card_lineage(
    prepared: PreparedDraft,
) -> None:
    from course_helper.slide_builder import SlideBuildError, build_and_register_draft

    substitute = EvidenceObject(
        evidence_id="substitute-lineage-evidence",
        kind="validation",
        subject_version_id=prepared.card.version_id,
        status="verified",
        producer="untrusted-substitute",
        started_at=NOW,
        finished_at=NOW,
    )
    prepared.catalog.insert_evidence(substitute)
    prepared.catalog.connection.execute(
        "DELETE FROM lineage WHERE from_version_id = ? AND to_version_id = ?",
        (prepared.card.version_id, prepared.chunk.chunk_id),
    )
    prepared.catalog.connection.commit()
    prepared.catalog.insert_lineage(
        LineageEdge(
            edge_id="substitute-lineage-edge",
            from_version_id=prepared.card.version_id,
            to_version_id=prepared.chunk.chunk_id,
            relation="cites",
            evidence_id=substitute.evidence_id,
            created_at=NOW,
        )
    )
    with pytest.raises(SlideBuildError, match="verified cites lineage"):
        build_and_register_draft(
            prepared.catalog,
            prepared.course.version_id,
            actor=ACTOR,
            clock=lambda: NOW,
        )


def test_rejects_uncovered_gap_and_unsafe_runtime_job(
    tmp_path: Path,
) -> None:
    from course_helper.slide_builder import SlideBuildError, build_and_register_draft

    with KnowledgeCatalog.open(tmp_path / "gap.db") as catalog:
        value = _prepare(catalog)
        stored_outline = catalog.get_course_outline(value.course.outline_version_id)
        assert stored_outline is not None
        changed = stored_outline.payload.model_copy(update={"uncovered_goals": ("Gap",)})
        changed = changed.model_copy(
            update={"content_digest": course_outline_content_digest(changed)}
        )
        payload = canonical_model_json(changed)
        catalog.connection.execute("DROP TRIGGER course_outlines_immutable_update")
        catalog.connection.execute("DROP TRIGGER course_versions_immutable_update")
        catalog.connection.execute(
            "UPDATE course_outlines SET domain_digest = ?, content_digest = ?, payload_json = ? "
            "WHERE version_id = ?",
            (
                changed.content_digest,
                _digest(payload),
                payload,
                changed.version_id,
            ),
        )
        changed_course = value.course.model_copy(
            update={"outline_digest": changed.content_digest}
        )
        course_payload = canonical_model_json(changed_course)
        catalog.connection.execute(
            "UPDATE course_versions SET content_digest = ?, payload_json = ? WHERE version_id = ?",
            (_digest(course_payload), course_payload, changed_course.version_id),
        )
        catalog.connection.commit()
        with pytest.raises(SlideBuildError, match="uncovered"):
            build_and_register_draft(
                catalog, value.course.version_id, actor=ACTOR, clock=lambda: NOW
            )

    with KnowledgeCatalog.open(tmp_path / "unsafe-job.db") as catalog:
        value = _prepare(catalog)
        evidence_id = catalog.connection.execute(
            "SELECT evidence_id FROM lineage WHERE from_version_id = ? AND relation = 'cites'",
            (value.card.version_id,),
        ).fetchone()[0]
        unsafe = RuntimeJobBinding(
            job_id="job-unsafe-shell",
            job_type="python_snippet",
            spec_id="shell-command",
            evidence_id=evidence_id,
            timeout_seconds=30,
        )
        with pytest.raises(SlideBuildError, match="runtime job"):
            build_and_register_draft(
                catalog,
                value.course.version_id,
                actor=ACTOR,
                clock=lambda: NOW,
                job_bindings=(unsafe,),
            )


def test_newer_valid_source_creates_suggestion_without_mutating_pinned_draft(
    prepared: PreparedDraft,
) -> None:
    from course_helper.slide_builder import build_and_register_draft
    from course_helper.upgrades import propose_source_change_upgrades

    before = build_and_register_draft(
        prepared.catalog,
        prepared.course.version_id,
        actor=ACTOR,
        clock=lambda: NOW,
    )
    newer_source = prepared.source.model_copy(
        update={
            "version_id": "source-slide-builder-v2",
            "revision": 2,
            "supersedes_version_id": prepared.source.version_id,
            "content_digest": _digest("source-v2"),
            "created_at": NOW + timedelta(hours=1),
        }
    )
    newer_chunk = prepared.chunk.model_copy(
        update={
            "chunk_id": "chunk-slide-builder-v2",
            "source_version_id": newer_source.version_id,
            "normalized_text": "Explain grounding with upgraded traceable evidence.",
            "content_digest": _digest("chunk-v2"),
        }
    )
    prepared.catalog.insert_source(newer_source)
    prepared.catalog.insert_chunk(newer_chunk)
    upgrade = propose_source_change_upgrades(
        prepared.catalog,
        previous_source_version_id=prepared.source.version_id,
        current_source_version_id=newer_source.version_id,
        previous_chunks=(prepared.chunk,),
        current_chunks=(newer_chunk,),
        actor=ACTOR,
        occurred_at=NOW + timedelta(hours=1),
    )
    assert upgrade.source_suggestion
    assert prepared.catalog.connection.execute(
        "SELECT count(*) FROM upgrade_suggestions"
    ).fetchone()[0] >= 1

    after = build_and_register_draft(
        prepared.catalog,
        prepared.course.version_id,
        actor=ACTOR,
        clock=lambda: NOW + timedelta(days=1),
    )
    assert after == before
    assert after.deck.nodes[0].source_version_ids == (prepared.source.version_id,)
