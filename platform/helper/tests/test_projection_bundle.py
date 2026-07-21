from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from course_helper.artifacts import ArtifactError, ArtifactStore
from course_helper.domain.projection import ProjectionCommand
from course_helper.projection_host import ProjectionHostError
from course_helper.slide_builder import publish_course_version
from course_helper.projection_bundle import (
    PublishedProjectionBundleResolver,
    _projection_asset_sources,
)

from test_course_publication import NETWORK_NOW, _prepare_publication, _request


def _published_projection(tmp_path: Path):
    fixture = _prepare_publication(tmp_path)
    outcome = publish_course_version(
        fixture.catalog,
        _request(fixture, "operation-projection-bundle", fixture.visual_placement_ids),
        confirmed_course_version_id=fixture.confirmed_course_id,
        expected_course_digest=fixture.confirmed_course_digest,
        visual_placement_ids=fixture.visual_placement_ids,
        clock=lambda: NETWORK_NOW + timedelta(hours=1),
    )
    return fixture, outcome.result_refs


def _open_command(refs: dict[str, object]) -> ProjectionCommand:
    return ProjectionCommand.model_validate(
        {
            "schemaVersion": 1,
            "commandId": str(uuid4()),
            "command": "open_projection_session",
            "sessionId": str(uuid4()),
            "expectedGeneration": 0,
            "payload": {
                "courseVersionId": refs["courseVersionId"],
                "slideDeckId": refs["slideDeckId"],
                "runtimeManifestId": refs["runtimeManifestId"],
            },
        }
    )


def test_resolver_returns_exact_published_path_free_bundle(tmp_path: Path) -> None:
    fixture, refs = _published_projection(tmp_path)
    try:
        resolver = PublishedProjectionBundleResolver(
            database_path=fixture.catalog.path,
            artifact_root=tmp_path / ".artifacts",
        )
        command = _open_command(refs)

        first = resolver(command)
        second = resolver(command)

        assert first.course_version_id == refs["courseVersionId"]
        assert first.runtime_manifest_digest == refs["runtimeManifestDigest"]
        assert first.navigation_identity == second.navigation_identity
        stored_course = fixture.catalog.get_course_version(str(refs["courseVersionId"]))
        assert stored_course is not None
        assert first.bootstrap["courseDigest"] == stored_course.payload.content_digest
        teaching_course = first.bootstrap["course"]
        assert teaching_course["schemaVersion"] == 1
        assert teaching_course["id"] == refs["courseVersionId"]
        assert teaching_course["title"]
        assert teaching_course["chapters"]
        assert teaching_course["chapters"][0]["lessons"]
        assert teaching_course["chapters"][0]["lessons"][0]["status"] == "grounded"
        assert [asset.opaque_id for asset in first.assets] == list(
            first.bootstrap["projection"]["runtimeManifest"]["artifactIds"]
        )
        assert len(first.assets) == 3
        for asset in first.assets:
            with asset.open_verified() as source:
                payload = source.read()
            assert len(payload) == asset.byte_size
            assert hashlib.sha256(payload).hexdigest() == asset.sha256
        raw = json.dumps(first.bootstrap, ensure_ascii=False, sort_keys=True)
        assert str(tmp_path) not in raw
        assert "artifactUrl" not in raw
        assert "localPath" not in raw
        assert "token" not in raw.casefold()
        assert len(raw.encode("utf-8")) <= 40 * 1024

        first_asset = first.assets[0]
        object_path = (
            tmp_path
            / ".artifacts"
            / "objects"
            / first_asset.sha256[:2]
            / first_asset.sha256[2:4]
            / first_asset.sha256
        )
        object_path.write_bytes(b"x" * first_asset.byte_size)
        with pytest.raises(ArtifactError):
            first_asset.open_verified()
    finally:
        fixture.catalog.close()


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra": True},
        {"courseVersionId": "course-wrong"},
        {"slideDeckId": "deck-wrong"},
        {"runtimeManifestId": "runtime-wrong"},
    ],
)
def test_resolver_fails_closed_for_nonexact_or_dangling_projection(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    fixture, refs = _published_projection(tmp_path)
    try:
        resolver = PublishedProjectionBundleResolver(
            database_path=fixture.catalog.path,
            artifact_root=tmp_path / ".artifacts",
        )
        command = _open_command(refs)
        command = command.model_copy(
            update={"payload": {**command.payload, **mutation}}
        )

        with pytest.raises(ProjectionHostError, match="published_bundle_unavailable"):
            resolver(command)
    finally:
        fixture.catalog.close()


def test_resolver_maps_corrupt_catalog_to_one_redacted_failure(tmp_path: Path) -> None:
    fixture, refs = _published_projection(tmp_path)
    database = fixture.catalog.path
    fixture.catalog.close()
    database.write_bytes(b"not-a-sqlite-catalog")
    resolver = PublishedProjectionBundleResolver(
        database_path=database,
        artifact_root=tmp_path / ".artifacts",
    )

    with pytest.raises(ProjectionHostError, match="published_bundle_unavailable"):
        resolver(_open_command(refs))


def test_resolver_rejects_catalog_file_replaced_after_construction(
    tmp_path: Path,
) -> None:
    fixture, refs = _published_projection(tmp_path)
    database = fixture.catalog.path
    resolver = PublishedProjectionBundleResolver(
        database_path=database,
        artifact_root=tmp_path / ".artifacts",
    )
    fixture.catalog.close()
    replacement = tmp_path / "replacement.sqlite3"
    shutil.copy2(database, replacement)
    replacement.replace(database)

    with pytest.raises(ProjectionHostError, match="published_bundle_unavailable"):
        resolver(_open_command(refs))


def test_resolver_binds_existing_wal_sidecars_during_one_snapshot(
    tmp_path: Path,
) -> None:
    fixture, refs = _published_projection(tmp_path)
    try:
        assert (
            fixture.catalog.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            == "wal"
        )
        fixture.catalog.connection.execute(
            "CREATE TABLE projection_wal_probe (identity TEXT PRIMARY KEY)"
        )
        fixture.catalog.connection.execute(
            "INSERT INTO projection_wal_probe(identity) VALUES ('bound')"
        )
        fixture.catalog.connection.commit()
        assert Path(str(fixture.catalog.path) + "-wal").is_file()
        assert Path(str(fixture.catalog.path) + "-shm").is_file()

        resolver = PublishedProjectionBundleResolver(
            database_path=fixture.catalog.path,
            artifact_root=tmp_path / ".artifacts",
        )

        assert (
            resolver(_open_command(refs)).course_version_id == refs["courseVersionId"]
        )
    finally:
        fixture.catalog.close()


def test_every_duplicate_artifact_binding_is_validated_before_one_asset_is_sent(
    tmp_path: Path,
) -> None:
    fixture, refs = _published_projection(tmp_path)
    try:
        deck = fixture.catalog.get_slide_deck(str(refs["slideDeckId"]))
        assert deck is not None
        binding = deck.payload.nodes[0].asset_bindings[0]
        duplicate = binding.model_copy(
            update={
                "binding_id": "binding-duplicate",
                "visual_placement_id": "placement-duplicate",
            }
        )
        store = ArtifactStore(tmp_path / ".artifacts")

        assets = _projection_asset_sources(
            fixture.catalog,
            store,
            (binding, duplicate),
            (binding.artifact_id,),
        )
        assert len(assets) == 1

        conflicting = duplicate.model_copy(update={"artifact_digest": "f" * 64})
        with pytest.raises(ProjectionHostError, match="published_bundle_unavailable"):
            _projection_asset_sources(
                fixture.catalog,
                store,
                (binding, conflicting),
                (binding.artifact_id,),
            )
    finally:
        fixture.catalog.close()
