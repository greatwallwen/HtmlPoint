from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from course_helper.domain.common import ActorRef
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    DatasetReference,
    KnowledgeCardVersion,
    TagAssignment,
    VisualReference,
)
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.domain.common import SourceLocator
from course_helper.domain.composition import canonical_digest
from course_helper.retrieval import (
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    RetrievalScoreComponents,
    retrieval_query_digest,
)


NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="service", actor_id="composer-tests")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def card(
    version_id: str,
    objective: str,
    *,
    tags: tuple[str, ...] = ("topic:grounding", "audience:pm", "difficulty:intro"),
    prerequisites: tuple[str, ...] = (),
    visual: bool = False,
    dataset: bool = False,
    status: str = "published",
) -> KnowledgeCardVersion:
    return KnowledgeCardVersion(
        logical_id=f"logical-{version_id}", version_id=version_id, revision=1,
        content_digest=digest(version_id), created_at=NOW, created_by=ACTOR,
        main_type_id="concept", title=f"Title {version_id}", learning_objective=objective,
        content_ast=(CardContentNode(type="paragraph", text="Source-backed text"),),
        suggested_minutes=10, prerequisite_card_version_ids=prerequisites,
        vocabulary_version_id="vocab-v1",
        tag_assignments=tuple(TagAssignment(vocabulary_version_id="vocab-v1", dimension_id=tag.split(":", 1)[0], tag_id=tag) for tag in tags),
        chunk_citations=(ChunkCitation(chunk_id=f"chunk-{version_id}", source_version_id=f"source-{version_id}"),),
        visual_refs=(VisualReference(visual_version_id="visual-v1"),) if visual else (),
        dataset_refs=(DatasetReference(dataset_version_id="dataset-v1"),) if dataset else (),
        status=status,
    )


class Catalog:
    def __init__(self, cards: tuple[KnowledgeCardVersion, ...], *, suspended: tuple[str, ...] = ()) -> None:
        self.cards = {item.version_id: item for item in cards}
        self.suspended = set(suspended)

    def get_card(self, version_id: str) -> KnowledgeCardVersion | None:
        return self.cards.get(version_id)

    def card_is_eligible_for_composition(self, version_id: str) -> bool:
        value = self.cards.get(version_id)
        return value is not None and value.status == "published" and version_id not in self.suspended


def retrieval(
    *cards: KnowledgeCardVersion,
    snapshot_id: str = "snapshot-v1",
    query: RetrievalQuery | None = None,
    evidence_id: str = "retrieval-evidence-v1",
) -> RetrievalResult:
    query_digest = (
        digest("query")
        if query is None
        else retrieval_query_digest(
            query, resolved_vocabulary_version_id="vocab-v1"
        )
    )
    returned_ids = tuple(item.version_id for item in cards)
    evidence = EvidenceObject(
        evidence_id=evidence_id, kind="retrieval", status="verified",
        producer="course-helper/retrieval", producer_version="4",
        started_at=NOW, finished_at=NOW,
        input_summary={"query_digest": query_digest},
        output_summary={
            "index_snapshot_id": snapshot_id,
            "index_snapshot_digest": digest(snapshot_id),
            "returned_hit_count": len(returned_ids),
            "returned_hit_order_digest": canonical_digest(returned_ids),
        },
    )
    return RetrievalResult(
        index_schema_version=4, resolved_vocabulary_version_id="vocab-v1",
        query_digest=query_digest,
        hits=tuple(RetrievalHit(card=item, card_tag_ids=tuple(x.tag_id for x in item.tag_assignments), score_components=RetrievalScoreComponents(rrf_score=float(len(cards) - index), matched_via=("fts",))) for index, item in enumerate(cards)),
        evidence=evidence,
    )


def requirement(**changes: object):
    from course_helper.domain.composition import CourseRequirement
    value = dict(requirement_id="requirement-v1", title="Grounded composition", audience="Product managers", learning_goals=("Explain grounding", "Apply grounding"), duration_minutes=30, required_tag_ids=("topic:grounding",), excluded_tag_ids=("topic:unsafe",), usage_scope="internal")
    value.update(changes)
    return CourseRequirement(**value)


def compose(catalog: Catalog, result: RetrievalResult, **changes: object):
    from course_helper.composer import CompositionOptions, compose_outline
    return compose_outline(catalog, requirement(**changes.pop("requirement", {})), result, logical_id="outline-logical", version_id="outline-v1", revision=1, created_at=NOW, created_by=ACTOR, options=changes.pop("options", CompositionOptions(audience_tag_id="audience:pm", difficulty_tag_id="difficulty:intro")))


def test_composes_every_requirement_field_into_deterministic_five_minute_outline() -> None:
    from course_helper.composer import CompositionOptions
    first = card("card-apply", "Apply grounding")
    second = card("card-explain", "Explain grounding")
    result = retrieval(first, second)
    options = CompositionOptions(audience_tag_id="audience:pm", difficulty_tag_id="difficulty:intro")
    one = compose(Catalog((first, second)), result, options=options)
    two = compose(Catalog((first, second)), result, options=options)
    assert one.outline == two.outline
    assert one.outline.uncovered_goals == ()
    assert tuple(chapter.title for chapter in one.outline.chapters) == requirement().learning_goals
    selected_ids = tuple(item.card_version_id for chapter in one.outline.chapters for item in chapter.placements)
    assert selected_ids == ("card-explain", "card-apply")
    assert set(selected_ids).issubset({hit.card.version_id for hit in result.hits})
    assert {item["content_digest"] for item in one.composition_evidence.output_summary["selected_cards"]} == {hit.card.content_digest for hit in result.hits}
    assert sum(item.allocated_minutes for chapter in one.outline.chapters for item in chapter.placements) == 30
    assert all(item.allocated_minutes % 5 == 0 for chapter in one.outline.chapters for item in chapter.placements)
    assert one.outline.retrieval_evidence_id == one.composition_evidence.evidence_id
    assert one.outline.index_snapshot_id == "snapshot-v1"
    assert dict(one.composition_evidence.output_summary["filters"]) == {"audience_tag_id": "audience:pm", "difficulty_tag_id": "difficulty:intro", "excluded_tag_ids": ("topic:unsafe",), "required_tag_ids": ("topic:grounding",)}
    assert one.composition_evidence.output_summary["index_snapshot_id"] == "snapshot-v1"


def test_include_exclude_prerequisites_and_visual_dataset_requirements_are_explicit() -> None:
    from course_helper.composer import CompositionGapError, CompositionOptions
    prerequisite = card("card-prerequisite", "Explain grounding")
    applied = card("card-applied", "Apply grounding", prerequisites=("card-prerequisite",), visual=True, dataset=True)
    result = retrieval(applied, prerequisite)
    with pytest.raises(ValueError, match="overlap"):
        CompositionOptions(
            include_card_version_ids=("card-applied",),
            exclude_card_version_ids=("card-applied",),
        )
    options = CompositionOptions(include_card_version_ids=("card-applied",), exclude_card_version_ids=("card-prerequisite",), audience_tag_id="audience:pm", difficulty_tag_id="difficulty:intro", require_visual_refs=True, require_dataset_refs=True)
    with pytest.raises(CompositionGapError, match="excluded"):
        compose(Catalog((applied, prerequisite)), result, options=options)
    with pytest.raises(CompositionGapError, match="not returned by retrieval"):
        compose(Catalog((applied, prerequisite)), result, options=CompositionOptions(include_card_version_ids=("not-a-hit",)))


def test_rejects_duplicate_stale_or_lifecycle_invalid_retrieval_cards() -> None:
    from course_helper.composer import CompositionError
    valid = card("card-valid", "Explain grounding")
    duplicate = retrieval(valid, valid)
    with pytest.raises(CompositionError, match="duplicate"):
        compose(Catalog((valid,)), duplicate)
    stale = card("card-valid", "Explain grounding").model_copy(update={"content_digest": digest("new")})
    with pytest.raises(CompositionError, match="stale"):
        compose(Catalog((stale,)), retrieval(valid))
    with pytest.raises(CompositionError, match="lifecycle"):
        compose(Catalog((valid,), suspended=(valid.version_id,)), retrieval(valid))


def test_covered_and_uncovered_goals_are_an_exact_requirement_partition() -> None:
    value = card("card-explain", "Explain grounding")
    outcome = compose(Catalog((value,)), retrieval(value))
    covered = {chapter.objective for chapter in outcome.outline.chapters if chapter.placements}
    uncovered = set(outcome.outline.uncovered_goals)
    assert covered | uncovered == set(requirement().learning_goals)
    assert not covered & uncovered


def test_atomic_registration_rolls_back_retrieval_and_composition_evidence_on_late_failure() -> None:
    from course_helper.composer import CompositionOptions, compose_and_register

    value = card("card-explain", "Explain grounding")

    class LateFailureCatalog(Catalog):
        def __init__(self) -> None:
            super().__init__((value,))
            self.evidence: list[str] = []

        def get_course_requirement(self, _: str):
            return SimpleNamespace(payload=requirement(learning_goals=("Explain grounding",)), content_digest=digest("stored-requirement"))

        def get_course_outline(self, _: str):
            return None

        @contextmanager
        def atomic_write(self):
            checkpoint = list(self.evidence)
            try:
                yield
            except BaseException:
                self.evidence = checkpoint
                raise

        def insert_evidence(self, value: EvidenceObject) -> None:
            self.evidence.append(value.evidence_id)

        def register_course_outline(self, *_: object, **__: object) -> None:
            raise RuntimeError("late persistence failure")

    catalog = LateFailureCatalog()

    class FakeRetriever:
        queries: list[RetrievalQuery] = []

        def search(self, query: RetrievalQuery) -> RetrievalResult:
            self.queries.append(query)
            return retrieval(
                value,
                query=query,
                evidence_id=f"retrieval-{digest(query.text)[:24]}",
            )

    retriever = FakeRetriever()
    with pytest.raises(RuntimeError, match="late persistence"):
        compose_and_register(
            catalog,
            retriever,
            requirement(learning_goals=("Explain grounding",)),
            logical_id="outline-logical",
            version_id="outline-v1",
            revision=1,
            created_at=NOW,
            created_by=ACTOR,
            options=CompositionOptions(index_snapshot_id="snapshot-v1"),
        )
    assert catalog.evidence == []
    assert [query.text for query in retriever.queries] == ["Explain grounding"]


@pytest.mark.parametrize("scope", ("private-training", "internal", "public"))
def test_confirmation_summaries_are_scope_and_outline_digest_bound(scope: str) -> None:
    from course_helper.composer import CompositionOptions, confirmation_summary, verify_confirmation_summary
    value = card("card-explain", "Explain grounding")
    outcome = compose(Catalog((value,)), retrieval(value), requirement={"usage_scope": scope, "learning_goals": ("Explain grounding",)}, options=CompositionOptions(audience_tag_id="audience:pm", difficulty_tag_id="difficulty:intro"))
    summary = confirmation_summary(outcome.outline, requirement(usage_scope=scope, learning_goals=("Explain grounding",)))
    assert summary.usage_scope == scope
    assert verify_confirmation_summary(summary, outcome.outline, requirement(usage_scope=scope, learning_goals=("Explain grounding",)))
    assert not verify_confirmation_summary(summary, outcome.outline.model_copy(update={"content_digest": digest("other")}), requirement(usage_scope=scope, learning_goals=("Explain grounding",)))
    placement = outcome.outline.chapters[0].placements[0]
    changed = outcome.outline.model_copy(
        update={
            "chapters": (
                outcome.outline.chapters[0].model_copy(
                    update={
                        "placements": (
                            placement.model_copy(update={"allocated_minutes": 5}),
                        )
                    }
                ),
            )
        }
    )
    assert not verify_confirmation_summary(
        summary,
        changed,
        requirement(usage_scope=scope, learning_goals=("Explain grounding",)),
    )


def test_authoritative_multi_goal_registration_binds_queries_and_reuses_first_bytes(
    tmp_path: Path,
) -> None:
    from course_helper.cards import VOCABULARY_VERSION_ID, publish_card, seed_vocabulary
    from course_helper.catalog import KnowledgeCatalog
    from course_helper.composer import CompositionOptions, compose_and_register
    from course_helper.domain.composition import CourseRequirement
    from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
    from course_helper.operations import (
        IndexOutboxItem,
        OperationMutationResult,
        OperationRequest,
        run_operation,
    )
    from course_helper.retrieval import KnowledgeRetriever

    with KnowledgeCatalog.open(tmp_path / "authoritative.db") as catalog:
        seed_vocabulary(catalog)
        stored_cards = []
        for ordinal, goal in enumerate(("Explain grounding", "Apply grounding"), 1):
            version_id = f"card-authoritative-{ordinal}"
            source_id = f"source-authoritative-{ordinal}"
            chunk_id = f"chunk-authoritative-{ordinal}"
            catalog.insert_source(
                SourceAssetVersion(
                    logical_id=f"logical-{source_id}", version_id=source_id,
                    revision=1, content_digest=digest(source_id), created_at=NOW,
                    created_by=ACTOR,
                    locator=SourceLocator(root_id="fixture", relative_path=f"{version_id}.md"),
                    display_name=f"{version_id}.md", source_kind="markdown",
                    media_type="text/markdown", byte_size=10, extraction_status="parsed",
                )
            )
            catalog.insert_chunk(
                ExtractedChunk(
                    chunk_id=chunk_id, source_version_id=source_id, ordinal=0,
                    modality="text", language="en", normalized_text=goal,
                    content_digest=digest(chunk_id),
                    locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
                )
            )
            candidate = KnowledgeCardVersion(
                logical_id=f"logical-{version_id}", version_id=version_id,
                revision=1, content_digest=digest(version_id), created_at=NOW,
                created_by=ACTOR, main_type_id="concept", title=goal,
                learning_objective=goal,
                content_ast=(CardContentNode(type="paragraph", text=goal),),
                suggested_minutes=10, vocabulary_version_id=VOCABULARY_VERSION_ID,
                tag_assignments=tuple(
                    TagAssignment(
                        vocabulary_version_id=VOCABULARY_VERSION_ID,
                        dimension_id=tag_id.split(":", 1)[0], tag_id=tag_id,
                    )
                    for tag_id in (
                        "topic:ai-foundations", "audience:learner", "difficulty:beginner"
                    )
                ),
                chunk_citations=(ChunkCitation(chunk_id=chunk_id, source_version_id=source_id),),
                status="review",
            )
            stored_cards.append(publish_card(candidate, catalog))
        request = OperationRequest(
            operation_id="operation-authoritative-index",
            request_digest=digest("authoritative-index"), actor=ACTOR,
            session_id="authoritative-session",
        )
        run_operation(
            catalog,
            request,
            lambda: OperationMutationResult(
                result_refs={}, item_outcomes=(),
                index_outbox=(
                    IndexOutboxItem(
                        outbox_id="outbox-authoritative-index",
                        card_version_id=stored_cards[-1].version_id,
                        action="upsert",
                    ),
                ),
            ),
            clock=lambda: NOW,
        )
        claim = claim_next_index_outbox(
            catalog, worker_id="composer-worker", now=NOW, lease_seconds=30
        )
        assert claim is not None
        snapshot = complete_index_claim(
            catalog, claim_id=claim.claim_id, worker_id="composer-worker",
            embedding_provider=None, now=NOW + timedelta(seconds=1),
        )
        exact_requirement = CourseRequirement(
            requirement_id="requirement-authoritative", title="Grounded course",
            audience="Learners",
            learning_goals=("Explain grounding", "Apply grounding"),
            duration_minutes=30, required_tag_ids=("topic:ai-foundations",),
            usage_scope="internal",
        )
        catalog.register_course_requirement(exact_requirement, clock=lambda: NOW)

        class RecordingRetriever:
            def __init__(self) -> None:
                self.queries: list[RetrievalQuery] = []
                self.real = KnowledgeRetriever(catalog)

            def search(self, query: RetrievalQuery) -> RetrievalResult:
                self.queries.append(query)
                return self.real.search(query)

        retriever = RecordingRetriever()
        options = CompositionOptions(
            audience_tag_id="audience:learner",
            difficulty_tag_id="difficulty:beginner",
            index_snapshot_id=snapshot.index_snapshot_id,
        )
        evidence_before = catalog.connection.execute(
            "SELECT count(*) FROM evidence"
        ).fetchone()[0]
        first = compose_and_register(
            catalog, retriever, exact_requirement, logical_id="outline-authoritative",
            version_id="outline-authoritative-v1", revision=1, created_at=NOW,
            created_by=ACTOR, options=options,
        )
        replay = compose_and_register(
            catalog, retriever, exact_requirement, logical_id="outline-authoritative",
            version_id="outline-authoritative-v1", revision=1,
            created_at=NOW + timedelta(days=1),
            created_by=ActorRef(actor_type="human", actor_id="different-actor"),
            options=options,
        )
        assert [item.text for item in retriever.queries] == [
            "Explain grounding", "Apply grounding",
            "Explain grounding", "Apply grounding",
        ]
        assert replay.outline == first.outline
        assert replay.composition_evidence == first.composition_evidence
        assert len(set(first.retrieval_evidence_ids)) == 2
        assert catalog.connection.execute(
            "SELECT count(*) FROM evidence"
        ).fetchone()[0] == evidence_before + 3
