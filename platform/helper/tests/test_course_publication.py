from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from course_helper.artifacts import ArtifactStore
from course_helper.catalog import KnowledgeCatalog
from course_helper.chart_builder import build_dataset_charts
from course_helper.domain.common import SourceLocator
from course_helper.domain.knowledge import ReviewTask
from course_helper.domain.visual_policy import (
    AttributionBlock,
    CropRect,
    TransformationManifest,
    VisualPlacement,
)
from course_helper.network_visuals import current_network_visual_verification
from course_helper.operations import OperationRequest, operation_status
from course_helper.parsers.pptx_parser import PptxParser
from course_helper.source_roots import SourceRootRegistry
from course_helper.source_visuals import materialize_source_visuals
from course_helper.slide_builder import (
    SlideBuildError,
    build_and_register_draft,
    course_publication_request_digest,
    publish_course_version,
    validate_course_version,
)

from test_chart_builder import NOW as CHART_NOW, _csv, _spec
from test_network_visuals import (
    NOW as NETWORK_NOW,
    FixtureTransport,
    _acquire,
    _discover,
)
from test_slide_builder import ACTOR, _prepare
from test_source_visuals import build_pptx, persist_extraction, png_bytes


@dataclass(frozen=True)
class PublicationFixture:
    catalog: KnowledgeCatalog
    confirmed_course_id: str
    confirmed_course_digest: str
    draft_deck_id: str
    draft_deck_bytes: str
    visual_placement_ids: tuple[str, ...]
    network_placement: VisualPlacement


def _unchanged_transform(identifier: str) -> TransformationManifest:
    return TransformationManifest(
        transformation_id=identifier,
        scale_mode="contain",
        derivative_license_decision="not-derivative",
        share_alike_compatible=True,
        gfdl_compatible=True,
        no_derivatives_compatible=True,
    )


def _prepare_publication(tmp_path: Path) -> PublicationFixture:
    catalog = KnowledgeCatalog.open(tmp_path / "publication.sqlite3")
    store = ArtifactStore(tmp_path / ".artifacts")

    pptx = build_pptx(
        tmp_path / "course-source.pptx",
        (png_bytes((20, 40, 60)),),
    )
    registry = SourceRootRegistry({"fixture": tmp_path})
    extraction = PptxParser(registry).parse(
        SourceLocator(root_id="fixture", relative_path=pptx.name)
    )
    persist_extraction(catalog, extraction)

    _source, profiler, dataset = _csv(
        tmp_path,
        "course-data.csv",
        "record_id,category,amount\n1,A,10\n2,A,5\n3,B,8\n",
    )
    catalog.insert_dataset(dataset)
    prepared = _prepare(
        catalog,
        source_override=extraction.source,
        chunk_override=extraction.chunks[0],
        dataset_refs=(),
    )
    draft = build_and_register_draft(
        catalog,
        prepared.course.version_id,
        actor=ACTOR,
        clock=lambda: NETWORK_NOW,
    )
    target_node_id = draft.deck.nodes[0].node_id

    source_outcome = materialize_source_visuals(
        catalog,
        registry,
        store,
        source_version_id=extraction.source.version_id,
        visual_version_ids=(extraction.visuals[0].version_id,),
        clock=lambda: NETWORK_NOW,
    )[0]
    assert source_outcome.status == "materialized"
    source_visual = extraction.visuals[0]
    source_placement = VisualPlacement(
        placement_id="publication-source-visual",
        visual_version_id=source_visual.version_id,
        slide_node_id=target_node_id,
        slot_id="source",
        fit="contain",
        alt_text="Exact source image",
        authenticity_evidence_id=str(source_outcome.evidence_id),
        license_evidence_id=str(source_outcome.evidence_id),
        attribution=AttributionBlock(
            title="Exact source image",
            license_label="source-provided",
        ),
        transformation=_unchanged_transform("transform-source"),
        originating_source_version_id=extraction.source.version_id,
    )

    chart = build_dataset_charts(
        catalog,
        profiler,
        store,
        (_spec(dataset, request_id="publication-chart"),),
        clock=lambda: CHART_NOW,
    )[0]
    assert chart.materialization is not None
    chart_value = chart.materialization
    data_placement = VisualPlacement(
        placement_id="publication-data-visual",
        visual_version_id=chart_value.visual.version_id,
        slide_node_id=target_node_id,
        slot_id="data",
        fit="contain",
        alt_text="Verified data chart",
        authenticity_evidence_id=chart_value.evidence.evidence_id,
        license_evidence_id=chart_value.evidence.evidence_id,
        attribution=AttributionBlock(
            title="Verified data chart",
            license_label="generated",
        ),
        transformation=_unchanged_transform("transform-data"),
        originating_dataset_version_id=dataset.version_id,
    )

    candidate = _discover(catalog, FixtureTransport())[0]
    network = _acquire(tmp_path, catalog, FixtureTransport(), candidate.candidate_id)
    assert network.acquisition is not None and network.visual_version_id is not None
    acquisition = network.acquisition
    verification = current_network_visual_verification(
        catalog, str(network.visual_version_id), now=NETWORK_NOW
    )
    network_placement = VisualPlacement(
        placement_id="publication-network-visual",
        visual_version_id=str(network.visual_version_id),
        slide_node_id=target_node_id,
        slot_id="network",
        fit="contain",
        alt_text="Licensed network visual",
        authenticity_evidence_id=verification.evidence_id,
        license_evidence_id=acquisition.evidence_id,
        attribution=AttributionBlock(
            title=acquisition.title,
            creator=acquisition.creator,
            publisher="Wikimedia Commons",
            license_label=acquisition.license_id,
            landing_link=acquisition.landing_link,
            license_link=acquisition.license_link,
        ),
        transformation=_unchanged_transform("transform-network"),
        originating_card_version_id=prepared.card.version_id,
    )

    for placement in (source_placement, data_placement, network_placement):
        catalog.register_visual_placement(placement, clock=lambda: NETWORK_NOW)
    return PublicationFixture(
        catalog=catalog,
        confirmed_course_id=prepared.course.version_id,
        confirmed_course_digest=prepared.course.content_digest,
        draft_deck_id=draft.deck.version_id,
        draft_deck_bytes=draft.stored_deck.payload_json,
        visual_placement_ids=tuple(
            placement.placement_id
            for placement in (source_placement, data_placement, network_placement)
        ),
        network_placement=network_placement,
    )


def _request(
    value: PublicationFixture,
    operation_id: str,
    placements: tuple[str, ...],
    *,
    course_version_id: str | None = None,
    course_digest: str | None = None,
) -> OperationRequest:
    selected_course_id = course_version_id or value.confirmed_course_id
    selected_course_digest = course_digest or value.confirmed_course_digest
    return OperationRequest(
        operation_id=operation_id,
        request_digest=course_publication_request_digest(
            confirmed_course_version_id=selected_course_id,
            expected_course_digest=selected_course_digest,
            visual_placement_ids=placements,
        ),
        actor=ACTOR,
        session_id="publication-session",
    )


def test_publishes_source_data_and_network_visuals_atomically_and_recovers_response_loss(
    tmp_path: Path,
) -> None:
    value = _prepare_publication(tmp_path)
    catalog = value.catalog
    try:
        validation = validate_course_version(
            catalog,
            value.confirmed_course_id,
            expected_course_digest=value.confirmed_course_digest,
            visual_placement_ids=value.visual_placement_ids,
            actor=ACTOR,
            clock=lambda: NETWORK_NOW + timedelta(hours=1),
        )
        assert validation.course.status == "published"
        assert validation.course.visual_placement_ids == value.visual_placement_ids
        assert len(validation.runtime_manifest.artifact_ids) == 3
        assert catalog.get_course_version(validation.course.version_id) is None

        request = _request(value, "operation-course-publication", value.visual_placement_ids)

        def lose_response(_outcome: object) -> None:
            raise RuntimeError("simulated response loss")

        with pytest.raises(RuntimeError, match="response loss"):
            publish_course_version(
                catalog,
                request,
                confirmed_course_version_id=value.confirmed_course_id,
                expected_course_digest=value.confirmed_course_digest,
                visual_placement_ids=value.visual_placement_ids,
                clock=lambda: NETWORK_NOW + timedelta(hours=1),
                after_commit=lose_response,
            )

        recovered = operation_status(
            catalog,
            operation_id=request.operation_id,
            actor_id=ACTOR.actor_id,
            actor_type=ACTOR.actor_type,
            session_id=request.session_id,
        )
        assert recovered.status == "committed"
        assert set(recovered.result_refs) == {
            "courseVersionId",
            "slideDeckId",
            "runtimeManifestId",
            "runtimeManifestDigest",
            "courseProjectionId",
        }
        published = catalog.get_course_version(str(recovered.result_refs["courseVersionId"]))
        deck = catalog.get_slide_deck(str(recovered.result_refs["slideDeckId"]))
        manifest = catalog.get_runtime_manifest(str(recovered.result_refs["runtimeManifestId"]))
        assert published is not None and published.payload.status == "published"
        assert deck is not None and manifest is not None
        assert deck.payload == validation.deck
        assert manifest.payload == validation.runtime_manifest
        assert manifest.payload.content_digest == recovered.result_refs["runtimeManifestDigest"]
        assert catalog.get_slide_deck(value.draft_deck_id).payload_json == value.draft_deck_bytes
        assert all(not node.asset_bindings for node in catalog.get_slide_deck(value.draft_deck_id).payload.nodes)

        replay = publish_course_version(
            catalog,
            request,
            confirmed_course_version_id=value.confirmed_course_id,
            expected_course_digest=value.confirmed_course_digest,
            visual_placement_ids=value.visual_placement_ids,
            clock=lambda: NETWORK_NOW + timedelta(hours=2),
        )
        assert replay == recovered
        first_deck_bytes = deck.payload_json
        detached_ids = value.visual_placement_ids[:2]
        detached_request = _request(
            value,
            "operation-course-detach-network",
            detached_ids,
            course_version_id=published.payload.version_id,
            course_digest=published.payload.content_digest,
        )
        detached = publish_course_version(
            catalog,
            detached_request,
            confirmed_course_version_id=published.payload.version_id,
            expected_course_digest=published.payload.content_digest,
            visual_placement_ids=detached_ids,
            clock=lambda: NETWORK_NOW + timedelta(hours=2),
        )
        detached_course = catalog.get_course_version(
            str(detached.result_refs["courseVersionId"])
        )
        detached_deck = catalog.get_slide_deck(str(detached.result_refs["slideDeckId"]))
        assert detached_course is not None and detached_course.payload.revision == 3
        assert detached_course.payload.supersedes_version_id == published.payload.version_id
        assert detached_course.payload.visual_placement_ids == detached_ids
        assert detached_deck is not None
        assert len(detached_deck.payload.nodes[0].asset_bindings) == 2
        assert catalog.get_slide_deck(str(recovered.result_refs["slideDeckId"])).payload_json == first_deck_bytes
        assert catalog.connection.execute("SELECT count(*) FROM course_versions").fetchone()[0] == 3
        assert catalog.connection.execute("SELECT count(*) FROM operation_outcomes").fetchone()[0] == 3
    finally:
        catalog.close()


def test_publication_fails_closed_for_expired_network_or_incompatible_share_alike(
    tmp_path: Path,
) -> None:
    value = _prepare_publication(tmp_path)
    catalog = value.catalog
    try:
        mismatched = _request(
            value, "operation-course-mismatched-request", value.visual_placement_ids
        ).model_copy(update={"request_digest": "f" * 64})
        with pytest.raises(SlideBuildError, match="request digest"):
            publish_course_version(
                catalog,
                mismatched,
                confirmed_course_version_id=value.confirmed_course_id,
                expected_course_digest=value.confirmed_course_digest,
                visual_placement_ids=value.visual_placement_ids,
                clock=lambda: NETWORK_NOW + timedelta(hours=1),
            )

        expired_request = _request(
            value, "operation-course-expired", value.visual_placement_ids
        )
        with pytest.raises(SlideBuildError, match="stale|changed"):
            publish_course_version(
                catalog,
                expired_request,
                confirmed_course_version_id=value.confirmed_course_id,
                expected_course_digest=value.confirmed_course_digest,
                visual_placement_ids=value.visual_placement_ids,
                clock=lambda: NETWORK_NOW + timedelta(hours=25),
            )
        assert operation_status(
            catalog,
            operation_id=expired_request.operation_id,
            actor_id=ACTOR.actor_id,
            actor_type=ACTOR.actor_type,
            session_id=expired_request.session_id,
        ).status == "unknown"

        bad_transform = TransformationManifest(
            transformation_id="transform-network-incompatible",
            crop=CropRect(x=0.0, y=0.0, width=0.5, height=1.0),
            scale_mode="contain",
            change_notice="Cropped for the lesson",
            derivative_license_decision="same-license",
            export_license="CC-BY-SA-4.0",
            share_alike_compatible=False,
            gfdl_compatible=True,
            no_derivatives_compatible=False,
        )
        bad = value.network_placement.model_copy(
            update={
                "placement_id": "publication-network-incompatible",
                "slot_id": "network-bad",
                "crop": bad_transform.crop,
                "transformation": bad_transform,
            }
        )
        catalog.register_visual_placement(bad, clock=lambda: NETWORK_NOW)
        bad_ids = (bad.placement_id,)
        bad_request = _request(value, "operation-course-rights", bad_ids)
        with pytest.raises(SlideBuildError, match="policy blocked"):
            publish_course_version(
                catalog,
                bad_request,
                confirmed_course_version_id=value.confirmed_course_id,
                expected_course_digest=value.confirmed_course_digest,
                visual_placement_ids=bad_ids,
                clock=lambda: NETWORK_NOW + timedelta(hours=1),
            )
        assert operation_status(
            catalog,
            operation_id=bad_request.operation_id,
            actor_id=ACTOR.actor_id,
            actor_type=ACTOR.actor_type,
            session_id=bad_request.session_id,
        ).status == "unknown"

        catalog.insert_review_task(
            ReviewTask(
                task_id="review-publication-network-rights",
                kind="visual-rights",
                subject_version_id=value.network_placement.visual_version_id,
                status="open",
                blocking=True,
                created_at=NETWORK_NOW,
                created_by=ACTOR,
            )
        )
        review_request = _request(
            value, "operation-course-blocking-review", value.visual_placement_ids
        )
        with pytest.raises(SlideBuildError, match="blocking review"):
            publish_course_version(
                catalog,
                review_request,
                confirmed_course_version_id=value.confirmed_course_id,
                expected_course_digest=value.confirmed_course_digest,
                visual_placement_ids=value.visual_placement_ids,
                clock=lambda: NETWORK_NOW + timedelta(hours=1),
            )
        assert operation_status(
            catalog,
            operation_id=review_request.operation_id,
            actor_id=ACTOR.actor_id,
            actor_type=ACTOR.actor_type,
            session_id=review_request.session_id,
        ).status == "unknown"
        assert catalog.connection.execute(
            "SELECT count(*) FROM course_versions WHERE json_extract(payload_json, '$.status') = 'published'"
        ).fetchone()[0] == 0
    finally:
        catalog.close()


def test_late_manifest_failure_rolls_back_course_deck_and_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _prepare_publication(tmp_path)
    catalog = value.catalog
    try:
        request = _request(
            value, "operation-course-late-failure", value.visual_placement_ids
        )
        original_deck_count = catalog.connection.execute(
            "SELECT count(*) FROM slide_decks"
        ).fetchone()[0]

        def fail_manifest(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("simulated manifest persistence failure")

        monkeypatch.setattr(catalog, "register_runtime_manifest", fail_manifest)
        with pytest.raises(RuntimeError, match="manifest persistence"):
            publish_course_version(
                catalog,
                request,
                confirmed_course_version_id=value.confirmed_course_id,
                expected_course_digest=value.confirmed_course_digest,
                visual_placement_ids=value.visual_placement_ids,
                clock=lambda: NETWORK_NOW + timedelta(hours=1),
            )
        assert catalog.connection.execute(
            "SELECT count(*) FROM course_versions WHERE json_extract(payload_json, '$.status') = 'published'"
        ).fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM slide_decks"
        ).fetchone()[0] == original_deck_count
        assert operation_status(
            catalog,
            operation_id=request.operation_id,
            actor_id=ACTOR.actor_id,
            actor_type=ACTOR.actor_type,
            session_id=request.session_id,
        ).status == "unknown"
    finally:
        catalog.close()
