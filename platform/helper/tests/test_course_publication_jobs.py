from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from course_helper.jobs import (
    CoursePublishJob,
    CourseValidateJob,
    CourseVisualAttachJob,
    CourseVisualDetachJob,
    JobSpec,
    WorkerRuntimeConfig,
    _run_course_publish,
    _run_course_validate,
    _run_course_visual_attach,
    _run_course_visual_detach,
    visual_attach_request_digest,
    visual_detach_request_digest,
)
from course_helper.slide_builder import course_publication_request_digest
from test_course_publication import _prepare_publication
from test_slide_builder import ACTOR


SESSION = "session-" + "f" * 64
ACTOR_JSON = {"actorType": ACTOR.actor_type, "actorId": ACTOR.actor_id}


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "publication.sqlite3"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(("fixture", str(tmp_path)),),
    )


def _attach_job(fixture) -> CourseVisualAttachJob:
    source = fixture.catalog.get_visual_placement(fixture.visual_placement_ids[0])
    assert source is not None
    placement = source.payload
    transformation = {
        "transformationId": "transform-source-http-copy",
        "crop": None,
        "scaleMode": placement.transformation.scale_mode,
        "colorAdjustments": [],
        "changeNotice": None,
        "derivativeLicenseDecision": placement.transformation.derivative_license_decision,
        "exportLicense": None,
        "shareAlikeCompatible": placement.transformation.share_alike_compatible,
        "gfdlCompatible": placement.transformation.gfdl_compatible,
        "noDerivativesCompatible": placement.transformation.no_derivatives_compatible,
    }
    core = {
        "courseVersionId": fixture.confirmed_course_id,
        "expectedCourseDigest": fixture.confirmed_course_digest,
        "placementId": "publication-source-visual-http-copy",
        "visualVersionId": placement.visual_version_id,
        "slideNodeId": placement.slide_node_id,
        "slotId": "source-copy",
        "fit": placement.fit,
        "crop": None,
        "altText": "Exact source image copy",
        "transformation": transformation,
        "originatingCardVersionId": None,
        "originatingSourceVersionId": placement.originating_source_version_id,
        "originatingDatasetVersionId": None,
    }
    return CourseVisualAttachJob.model_validate(
        {
            "type": "course_visual_attach",
            **core,
            "operationId": "http-course-visual-attach",
            "requestDigest": visual_attach_request_digest(core),
            "actor": ACTOR_JSON,
        }
    )


def _detach_job(fixture, placement_id: str) -> CourseVisualDetachJob:
    active = (*fixture.visual_placement_ids, placement_id)
    return CourseVisualDetachJob.model_validate(
        {
            "type": "course_visual_detach",
            "courseVersionId": fixture.confirmed_course_id,
            "expectedCourseDigest": fixture.confirmed_course_digest,
            "placementId": placement_id,
            "activePlacementIds": list(active),
            "operationId": "http-course-visual-detach",
            "requestDigest": visual_detach_request_digest(
                course_version_id=fixture.confirmed_course_id,
                expected_course_digest=fixture.confirmed_course_digest,
                placement_id=placement_id,
                active_placement_ids=active,
            ),
            "actor": ACTOR_JSON,
        }
    )


def _projection_job(fixture, *, publish: bool):
    digest = course_publication_request_digest(
        confirmed_course_version_id=fixture.confirmed_course_id,
        expected_course_digest=fixture.confirmed_course_digest,
        visual_placement_ids=fixture.visual_placement_ids,
    )
    model = CoursePublishJob if publish else CourseValidateJob
    return model.model_validate(
        {
            "type": "course_publish" if publish else "course_validate",
            "courseVersionId": fixture.confirmed_course_id,
            "expectedCourseDigest": fixture.confirmed_course_digest,
            "visualPlacementIds": list(fixture.visual_placement_ids),
            "operationId": (
                "http-course-publish" if publish else "http-course-validate"
            ),
            "requestDigest": digest,
            "actor": ACTOR_JSON,
        }
    )


def test_course_publication_job_contracts_are_strict_lower_camel_and_digest_bound(
    tmp_path: Path,
) -> None:
    fixture = _prepare_publication(tmp_path)
    try:
        jobs = (
            _attach_job(fixture),
            _detach_job(fixture, "publication-source-visual-http-copy"),
            _projection_job(fixture, publish=False),
            _projection_job(fixture, publish=True),
        )
        adapter = TypeAdapter(JobSpec)
        for job in jobs:
            payload = job.model_dump(mode="json", by_alias=True)
            assert adapter.validate_python(payload) == job
            assert not any("url" in key.casefold() for key in payload)
        invalid = jobs[-1].model_dump(mode="json", by_alias=True)
        invalid["requestDigest"] = "0" * 64
        try:
            adapter.validate_python(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("stale course publish digest was accepted")
    finally:
        fixture.catalog.close()


def test_attach_detach_validate_publish_and_replay_use_durable_authorities(
    tmp_path: Path,
) -> None:
    fixture = _prepare_publication(tmp_path)
    fixture.catalog.close()
    config = _config(tmp_path)

    reopened = _prepare_publication_for_reopen(tmp_path)
    attach_job = _attach_job(reopened)
    reopened.catalog.close()
    attached, _ = _run_course_visual_attach(attach_job, config, SESSION)
    attach_replay, _ = _run_course_visual_attach(attach_job, config, SESSION)
    assert attach_replay == attached
    assert attached["operationStatus"] == "committed"
    assert attached["placementId"] == "publication-source-visual-http-copy"
    assert "href" not in str(attached)

    reopened = _prepare_publication_for_reopen(tmp_path)
    detach_job = _detach_job(reopened, attached["placementId"])
    reopened.catalog.close()
    detached, _ = _run_course_visual_detach(detach_job, config, SESSION)
    assert attached["placementId"] not in detached["activePlacementIds"]

    reopened = _prepare_publication_for_reopen(tmp_path)
    validate_job = _projection_job(reopened, publish=False)
    publish_job = _projection_job(reopened, publish=True)
    reopened.catalog.close()
    validated, _ = _run_course_validate(validate_job, config, SESSION)
    validation_replay, _ = _run_course_validate(validate_job, config, SESSION)
    assert validation_replay == validated
    assert validated["validationStatus"] == "passed"
    assert validated["slideDeck"]["nodes"]
    assert validated["runtimeManifest"]["artifactIds"]

    published, _ = _run_course_publish(publish_job, config, SESSION)
    publish_replay, _ = _run_course_publish(publish_job, config, SESSION)
    assert publish_replay == published
    assert published["operationStatus"] == "committed"
    assert published["courseVersionId"].startswith("coursev-")
    serialized = str(attached) + str(detached) + str(validated) + str(published)
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized


def _prepare_publication_for_reopen(tmp_path: Path):
    """Reopen the already prepared fixture without rebuilding immutable rows."""

    from test_course_publication import PublicationFixture
    from course_helper.catalog import KnowledgeCatalog

    catalog = KnowledgeCatalog.open(tmp_path / "publication.sqlite3")
    course = catalog.connection.execute(
        "SELECT version_id, domain_digest FROM course_versions WHERE revision = 1"
    ).fetchone()
    placements = tuple(
        row[0]
        for row in catalog.connection.execute(
            "SELECT placement_id FROM visual_placements "
            "WHERE placement_id LIKE 'publication-%-visual' ORDER BY rowid"
        ).fetchall()
    )
    draft = catalog.connection.execute(
        "SELECT version_id, payload_json FROM slide_decks ORDER BY rowid LIMIT 1"
    ).fetchone()
    network = catalog.get_visual_placement("publication-network-visual")
    assert course is not None and draft is not None and network is not None
    return PublicationFixture(
        catalog=catalog,
        confirmed_course_id=str(course[0]),
        confirmed_course_digest=str(course[1]),
        draft_deck_id=str(draft[0]),
        draft_deck_bytes=str(draft[1]),
        visual_placement_ids=placements,
        network_placement=network.payload,
    )
