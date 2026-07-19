from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.operations import OperationRequest
from course_helper.uploads import (
    MAX_UPLOAD_BYTES,
    UploadError,
    UploadStore,
    import_cancel_request_digest,
    import_promotion_request_digest,
    import_start_request_digest,
)


NOW = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
SESSION = "session-upload-1"
ACTOR = ActorRef(actor_type="human", actor_id="author-1")


def _request(operation_id: str, digest: str, *, session: str = SESSION) -> OperationRequest:
    return OperationRequest(
        operation_id=operation_id,
        request_digest=digest,
        actor=ACTOR,
        session_id=session,
    )


def _upload(
    store: UploadStore,
    content: bytes = b"# Governed source\n\nOne card.\n",
    *,
    name: str = "course.md",
    media_type: str = "text/markdown; charset=utf-8",
    session: str = SESSION,
):
    return store.create_upload(
        (content[:5], b"", content[5:]),
        file_name=name,
        media_type=media_type,
        byte_size_hint=len(content),
        session_id=session,
        clock=lambda: NOW,
    )


def _start(store: UploadStore, upload, *, operation_id: str = "import-start-1"):
    digest = import_start_request_digest(upload.upload_id, upload.content_digest)
    outcome = store.start_import(
        _request(operation_id, digest),
        upload_id=upload.upload_id,
        expected_content_digest=upload.content_digest,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    return str(outcome.result_refs["importId"]), outcome


def _promote(
    store: UploadStore,
    import_id: str,
    content_digest: str,
    *,
    operation_id: str = "import-promote-1",
):
    digest = import_promotion_request_digest(import_id, content_digest)
    return store.promote_import(
        _request(operation_id, digest),
        import_id=import_id,
        expected_content_digest=content_digest,
        clock=lambda: NOW + timedelta(seconds=2),
    )


def test_streaming_upload_is_bounded_digest_bound_and_path_free(tmp_path: Path) -> None:
    content = b"# AI course\n\nTruthful source.\n"
    database = tmp_path / "knowledge.db"
    app_data = tmp_path / "private-app-data"
    with KnowledgeCatalog.open(database) as catalog:
        store = UploadStore(catalog, app_data)
        upload = _upload(store, content)

        assert upload.byte_size == len(content)
        assert upload.content_digest == hashlib.sha256(content).hexdigest()
        assert upload.state == "available"
        assert upload.expires_at == NOW + timedelta(minutes=15)
        assert upload.safe_name == "course.md"
        public_json = upload.model_dump_json()
        assert str(tmp_path) not in public_json
        assert SESSION not in public_json
        assert content.decode() not in public_json

        row = catalog.connection.execute(
            "SELECT session_digest, payload_json FROM governed_uploads WHERE upload_id = ?",
            (upload.upload_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == hashlib.sha256(SESSION.encode()).hexdigest()
        assert SESSION not in row[1]
        stored = app_data / "uploads" / f"{upload.upload_id}.blob"
        assert stored.read_bytes() == content
        assert not tuple((app_data / "uploads").glob("*.tmp"))


@pytest.mark.parametrize(
    ("name", "media_type"),
    (
        ("../course.md", "text/markdown"),
        ("course.exe", "application/octet-stream"),
        ("course.md", "application/octet-stream"),
        ("course\n.md", "text/markdown"),
    ),
)
def test_upload_rejects_unsafe_name_or_media_without_temp_files(
    tmp_path: Path, name: str, media_type: str
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        with pytest.raises(UploadError, match="invalid|allowlisted"):
            _upload(store, name=name, media_type=media_type)
        assert not tuple(app_data.rglob("*.tmp"))
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_uploads"
        ).fetchone()[0] == 0


def test_upload_rejects_declared_and_streamed_size_violations_without_residue(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        with pytest.raises(UploadError) as declared:
            store.create_upload(
                (b"x",),
                file_name="course.md",
                media_type="text/markdown",
                byte_size_hint=MAX_UPLOAD_BYTES + 1,
                session_id=SESSION,
                clock=lambda: NOW,
            )
        assert declared.value.code == "UPLOAD_TOO_LARGE"
        with pytest.raises(UploadError):
            store.create_upload(
                (b"too-long",),
                file_name="course.md",
                media_type="text/markdown",
                byte_size_hint=3,
                session_id=SESSION,
                clock=lambda: NOW,
            )
        assert not tuple(app_data.rglob("*.tmp"))
        assert not tuple(app_data.rglob("*.blob"))


def test_durable_lease_precedes_ack_and_prevents_expiry_or_cancellation(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        store = UploadStore(catalog, tmp_path / "app-data")
        upload = _upload(store)
        import_id, outcome = _start(store, upload)

        assert outcome.status == "committed"
        assert catalog.connection.execute(
            "SELECT state FROM import_leases WHERE import_id = ?", (import_id,)
        ).fetchone()[0] == "active"
        assert catalog.connection.execute(
            "SELECT state FROM governed_uploads WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0] == "leased"
        assert store.expire_uploads(clock=lambda: NOW + timedelta(hours=1)) == ()
        with pytest.raises(UploadError) as cancelled:
            store.cancel_upload(
                upload.upload_id, session_id=SESSION, clock=lambda: NOW + timedelta(hours=1)
            )
        assert cancelled.value.code == "UPLOAD_CONFLICT"

        second_digest = import_start_request_digest(upload.upload_id, upload.content_digest)
        with pytest.raises(UploadError) as second:
            store.start_import(
                _request("import-start-2", second_digest),
                upload_id=upload.upload_id,
                expected_content_digest=upload.content_digest,
                clock=lambda: NOW + timedelta(seconds=3),
            )
        assert second.value.code == "IMPORT_CONFLICT"
        assert catalog.connection.execute(
            "SELECT count(*) FROM import_leases WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0] == 1


def test_concurrent_import_starts_create_exactly_one_durable_lease(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    app_data = tmp_path / "app-data"
    with KnowledgeCatalog.open(database) as catalog:
        upload = _upload(UploadStore(catalog, app_data))
    gate = Barrier(2)

    def attempt(index: int) -> str:
        with KnowledgeCatalog.open(database) as catalog:
            store = UploadStore(catalog, app_data)
            digest = import_start_request_digest(upload.upload_id, upload.content_digest)
            gate.wait()
            try:
                result = store.start_import(
                    _request(f"concurrent-start-{index}", digest),
                    upload_id=upload.upload_id,
                    expected_content_digest=upload.content_digest,
                    clock=lambda: NOW + timedelta(seconds=1),
                )
            except UploadError as error:
                return error.code
            return result.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(attempt, range(2)))

    assert sorted(outcomes) == ["IMPORT_CONFLICT", "committed"]
    with KnowledgeCatalog.open(database) as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM import_leases WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT state FROM governed_uploads WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0] == "leased"


def test_import_and_expiry_race_never_deletes_a_leased_input(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    app_data = tmp_path / "app-data"
    with KnowledgeCatalog.open(database) as catalog:
        upload = _upload(UploadStore(catalog, app_data))
    gate = Barrier(2)

    def start_import() -> str:
        with KnowledgeCatalog.open(database) as catalog:
            store = UploadStore(catalog, app_data)
            digest = import_start_request_digest(upload.upload_id, upload.content_digest)
            gate.wait()
            try:
                store.start_import(
                    _request("expiry-race-start", digest),
                    upload_id=upload.upload_id,
                    expected_content_digest=upload.content_digest,
                    clock=lambda: upload.expires_at - timedelta(microseconds=1),
                )
            except UploadError as error:
                return error.code
            return "leased"

    def expire() -> str:
        with KnowledgeCatalog.open(database) as catalog:
            gate.wait()
            expired = UploadStore(catalog, app_data).expire_uploads(
                clock=lambda: upload.expires_at
            )
            return "expired" if expired else "skipped"

    with ThreadPoolExecutor(max_workers=2) as pool:
        start_future = pool.submit(start_import)
        expiry_future = pool.submit(expire)
        start_result = start_future.result()
        expiry_result = expiry_future.result()

    upload_path = app_data / "uploads" / f"{upload.upload_id}.blob"
    with KnowledgeCatalog.open(database) as catalog:
        state = catalog.connection.execute(
            "SELECT state FROM governed_uploads WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0]
        if state == "leased":
            assert start_result == "leased"
            assert expiry_result == "skipped"
            assert upload_path.exists()
            assert catalog.connection.execute(
                "SELECT count(*) FROM import_leases WHERE upload_id = ?", (upload.upload_id,)
            ).fetchone()[0] == 1
        else:
            assert state == "expired"
            assert start_result in {"UPLOAD_EXPIRED", "IMPORT_CONFLICT"}
            assert expiry_result == "expired"
            assert not upload_path.exists()
            assert catalog.connection.execute(
                "SELECT count(*) FROM import_leases WHERE upload_id = ?", (upload.upload_id,)
            ).fetchone()[0] == 0


def test_expiry_deletes_only_available_upload_bytes(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        upload = _upload(store)
        path = app_data / "uploads" / f"{upload.upload_id}.blob"

        assert store.expire_uploads(clock=lambda: upload.expires_at) == (upload.upload_id,)
        assert not path.exists()
        assert catalog.connection.execute(
            "SELECT state FROM governed_uploads WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0] == "expired"


def test_promotion_is_atomic_content_addressed_and_replayable_after_cleanup(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        outcome = _promote(store, import_id, upload.content_digest)

        assert outcome.status == "committed"
        source_version_id = str(outcome.result_refs["sourceVersionId"])
        source = catalog.get_source(source_version_id)
        assert source is not None
        assert source.content_digest == upload.content_digest
        assert source.locator.root_id == "governed-upload"
        assert source.locator.relative_path == (
            f"sha256/{upload.content_digest[:2]}/{upload.content_digest}.blob"
        )
        blob = app_data / "source-blobs" / "sha256" / upload.content_digest[:2] / (
            upload.content_digest + ".blob"
        )
        assert blob.read_bytes() == b"# Governed source\n\nOne card.\n"
        assert not (app_data / "uploads" / f"{upload.upload_id}.blob").exists()
        assert not tuple(app_data.rglob("*.tmp"))

        replay = _promote(store, import_id, upload.content_digest)
        assert replay == outcome
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 1


def test_promotion_replay_cleans_short_lived_input_after_committed_response_loss(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        digest = import_promotion_request_digest(import_id, upload.content_digest)
        request = _request("promotion-response-loss", digest)
        upload_path = app_data / "uploads" / f"{upload.upload_id}.blob"

        with pytest.raises(ConnectionError, match="response lost"):
            store.promote_import(
                request,
                import_id=import_id,
                expected_content_digest=upload.content_digest,
                clock=lambda: NOW + timedelta(seconds=2),
                after_commit=lambda _outcome: (_ for _ in ()).throw(
                    ConnectionError("response lost")
                ),
            )

        assert store.import_status(import_id, session_id=SESSION).state == "promoted"
        assert upload_path.exists()
        recovered = store.promote_import(
            request,
            import_id=import_id,
            expected_content_digest=upload.content_digest,
            clock=lambda: NOW + timedelta(seconds=3),
        )
        assert recovered.status == "committed"
        assert not upload_path.exists()


def test_promotion_rejects_reparse_or_symlinked_content_shard(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        outside = tmp_path / "outside"
        outside.mkdir()
        shard = app_data / "source-blobs" / "sha256" / upload.content_digest[:2]
        try:
            shard.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlink creation is not permitted")
        digest = import_promotion_request_digest(import_id, upload.content_digest)

        with pytest.raises(UploadError) as rejected:
            store.promote_import(
                _request("promotion-reparse", digest),
                import_id=import_id,
                expected_content_digest=upload.content_digest,
                clock=lambda: NOW + timedelta(seconds=2),
            )

        assert rejected.value.code == "UPLOAD_INTEGRITY_INVALID"
        assert not tuple(outside.iterdir())
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 0


def test_same_bytes_reuse_one_source_and_blob_across_distinct_uploads(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        store = UploadStore(catalog, tmp_path / "app-data")
        first = _upload(store)
        first_import, _ = _start(store, first, operation_id="start-a")
        first_outcome = _promote(
            store, first_import, first.content_digest, operation_id="promote-a"
        )
        second = _upload(store)
        second_import, _ = _start(store, second, operation_id="start-b")
        second_outcome = _promote(
            store, second_import, second.content_digest, operation_id="promote-b"
        )

        assert second_outcome.result_refs["sourceVersionId"] == first_outcome.result_refs[
            "sourceVersionId"
        ]
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM import_leases WHERE state = 'promoted'"
        ).fetchone()[0] == 2


def test_promotion_failure_rolls_back_rows_and_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        original = catalog.register_or_reuse_source

        def fail_after_source(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("late database failure")

        monkeypatch.setattr(catalog, "register_or_reuse_source", fail_after_source)
        with pytest.raises(RuntimeError, match="late database failure"):
            _promote(store, import_id, upload.content_digest)

        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT state FROM import_leases WHERE import_id = ?", (import_id,)
        ).fetchone()[0] == "active"
        assert catalog.connection.execute(
            "SELECT state FROM governed_uploads WHERE upload_id = ?", (upload.upload_id,)
        ).fetchone()[0] == "leased"
        assert not tuple(app_data.rglob("*.tmp"))
        assert json.loads(catalog.connection.execute(
            "SELECT payload_json FROM import_leases WHERE import_id = ?", (import_id,)
        ).fetchone()[0])["state"] == "active"


def test_import_status_authenticates_and_revalidates_active_and_promoted_state(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        store = UploadStore(catalog, tmp_path / "app-data")
        upload = _upload(store)
        import_id, _ = _start(store, upload)

        active = store.import_status(import_id, session_id=SESSION)
        assert active.state == "active"
        assert active.source_version_id is None
        with pytest.raises(UploadError) as unauthenticated:
            store.import_status(import_id, session_id="another-session")
        assert unauthenticated.value.code == "IMPORT_AUTHENTICATION_FAILED"

        promoted = _promote(store, import_id, upload.content_digest)
        status = store.import_status(import_id, session_id=SESSION)
        assert status.state == "promoted"
        assert status.source_version_id == promoted.result_refs["sourceVersionId"]


def test_ledgered_import_cancel_recovers_response_loss_and_cleans_input_on_replay(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        app_data = tmp_path / "app-data"
        store = UploadStore(catalog, app_data)
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        digest = import_cancel_request_digest(import_id)
        request = _request("cancel-import-1", digest)
        upload_path = app_data / "uploads" / f"{upload.upload_id}.blob"

        with pytest.raises(ConnectionError, match="response lost"):
            store.cancel_import_operation(
                request,
                import_id=import_id,
                clock=lambda: NOW + timedelta(seconds=2),
                after_commit=lambda _outcome: (_ for _ in ()).throw(
                    ConnectionError("response lost")
                ),
            )

        assert store.import_status(import_id, session_id=SESSION).state == "cancelled"
        assert upload_path.exists()
        recovered = store.cancel_import_operation(
            request,
            import_id=import_id,
            clock=lambda: NOW + timedelta(seconds=3),
        )
        assert recovered.status == "committed"
        assert recovered.result_refs == {"importId": import_id, "status": "cancelled"}
        assert not upload_path.exists()
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id = ?",
            (request.operation_id,),
        ).fetchone()[0] == 1


def test_ledgered_import_cancel_rejects_wrong_digest_actor_or_session_without_mutation(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        store = UploadStore(catalog, tmp_path / "app-data")
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        digest = import_cancel_request_digest(import_id)

        for request in (
            _request("cancel-wrong-digest", "f" * 64),
            OperationRequest(
                operation_id="cancel-wrong-actor",
                request_digest=digest,
                actor=ActorRef(actor_type="human", actor_id="another-author"),
                session_id=SESSION,
            ),
            _request("cancel-wrong-session", digest, session="another-session"),
        ):
            with pytest.raises(UploadError):
                store.cancel_import_operation(
                    request,
                    import_id=import_id,
                    clock=lambda: NOW + timedelta(seconds=2),
                )

        assert store.import_status(import_id, session_id=SESSION).state == "active"
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id LIKE 'cancel-wrong-%'"
        ).fetchone()[0] == 0


def test_import_status_fails_closed_on_corrupted_lease_envelope(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        store = UploadStore(catalog, tmp_path / "app-data")
        upload = _upload(store)
        import_id, _ = _start(store, upload)
        row = catalog.connection.execute(
            "SELECT payload_json FROM import_leases WHERE import_id = ?", (import_id,)
        ).fetchone()
        payload = json.loads(row[0])
        payload["state"] = "cancelled"
        catalog.connection.execute(
            "UPDATE import_leases SET payload_json = ? WHERE import_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), import_id),
        )

        with pytest.raises(UploadError) as corrupted:
            store.import_status(import_id, session_id=SESSION)
        assert corrupted.value.code == "UPLOAD_INTEGRITY_INVALID"
