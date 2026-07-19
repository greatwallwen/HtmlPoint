from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.operations import OperationRequest
from course_helper.source_inventory import SourceInventoryError, list_source_inventory
from course_helper.uploads import (
    UploadStore,
    import_promotion_request_digest,
    import_start_request_digest,
)


NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)
ACTOR = ActorRef(actor_type="human", actor_id="inventory-author")


def _promote_source(
    store: UploadStore,
    *,
    suffix: str,
    content: bytes,
    at: datetime,
) -> str:
    session = f"inventory-session-{suffix}"
    upload = store.create_upload(
        (content,),
        file_name=f"course-{suffix}.md",
        media_type="text/markdown",
        byte_size_hint=len(content),
        session_id=session,
        clock=lambda: at,
    )
    start_digest = import_start_request_digest(upload.upload_id, upload.content_digest)
    start = store.start_import(
        OperationRequest(
            operation_id=f"inventory-start-{suffix}",
            request_digest=start_digest,
            actor=ACTOR,
            session_id=session,
        ),
        upload_id=upload.upload_id,
        expected_content_digest=upload.content_digest,
        clock=lambda: at,
    )
    import_id = str(start.result_refs["importId"])
    promotion_digest = import_promotion_request_digest(import_id, upload.content_digest)
    promoted = store.promote_import(
        OperationRequest(
            operation_id=f"inventory-promote-{suffix}",
            request_digest=promotion_digest,
            actor=ACTOR,
            session_id=session,
        ),
        import_id=import_id,
        expected_content_digest=upload.content_digest,
        clock=lambda: at,
    )
    return str(promoted.result_refs["sourceVersionId"])


def test_inventory_is_opaque_bounded_path_free_and_stably_paginated(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        store = UploadStore(catalog, tmp_path / "private-app-data")
        ids = tuple(
            _promote_source(
                store,
                suffix=str(index),
                content=f"# Source {index}\n".encode(),
                at=NOW + timedelta(seconds=index),
            )
            for index in range(3)
        )

        first = list_source_inventory(catalog, limit=2)
        second = list_source_inventory(catalog, cursor=first.next_cursor, limit=2)

        assert tuple(item.source_version_id for item in first.items) == ids[:2]
        assert tuple(item.source_version_id for item in second.items) == ids[2:]
        assert first.next_cursor is not None
        assert second.next_cursor is None
        serialized = first.model_dump_json() + second.model_dump_json()
        assert str(tmp_path) not in serialized
        assert "governed-upload" not in serialized
        assert "relative_path" not in serialized
        assert "locator" not in serialized
        assert "payload_json" not in serialized
        assert "# Source" not in serialized
        for item in first.items + second.items:
            assert set(item.model_dump()) == {
                "schema_version",
                "source_id",
                "source_version_id",
                "display_name",
                "source_kind",
                "media_type",
                "byte_size",
                "content_digest",
                "status",
            }


@pytest.mark.parametrize("limit", (0, 101, True))
def test_inventory_rejects_invalid_limits(tmp_path: Path, limit: int) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        with pytest.raises(SourceInventoryError, match="limit"):
            list_source_inventory(catalog, limit=limit)


def test_inventory_rejects_unknown_or_malformed_cursor(tmp_path: Path) -> None:
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        for cursor in ("../private", "inventory-cursor-" + "0" * 32):
            with pytest.raises(SourceInventoryError, match="cursor"):
                list_source_inventory(catalog, cursor=cursor)


def test_inventory_fails_closed_when_immutable_envelope_is_tampered(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    with KnowledgeCatalog.open(database) as catalog:
        store = UploadStore(catalog, tmp_path / "app-data")
        source_version_id = _promote_source(
            store, suffix="tamper", content=b"# Tamper test\n", at=NOW
        )
        catalog.connection.execute(
            "DROP TRIGGER governed_source_blobs_immutable_update"
        )
        row = catalog.connection.execute(
            "SELECT payload_json FROM governed_source_blobs WHERE source_version_id = ?",
            (source_version_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["safe_name"] = "forged.md"
        catalog.connection.execute(
            "UPDATE governed_source_blobs SET payload_json = ? WHERE source_version_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), source_version_id),
        )
        with pytest.raises(SourceInventoryError, match="digest|envelope"):
            list_source_inventory(catalog)

