"""Bounded path-free inventory projection for governed source blobs."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_helper.catalog import KnowledgeCatalog, canonical_model_json
from course_helper.domain.sources import SourceAssetVersion
from course_helper.uploads import GovernedSourceBlob


class SourceInventoryError(ValueError):
    pass


class SourceInventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    display_name: str = Field(min_length=1, max_length=240)
    source_kind: Literal["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]
    media_type: str = Field(min_length=1, max_length=200)
    byte_size: int = Field(ge=1, le=20 * 1024 * 1024)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "revoked"]


class SourceInventoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    items: tuple[SourceInventoryItem, ...]
    next_cursor: str | None = Field(
        default=None, pattern=r"^inventory-cursor-[0-9a-f]{32}$"
    )


def _cursor(source_version_id: str) -> str:
    return "inventory-cursor-" + hashlib.sha256(
        source_version_id.encode("utf-8")
    ).hexdigest()[:32]


def list_source_inventory(
    catalog: KnowledgeCatalog,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> SourceInventoryPage:
    """Return one deterministic page without locators, bytes, URLs, or paths."""

    if type(limit) is not int or not 1 <= limit <= 100:
        raise SourceInventoryError("inventory limit is invalid")
    after: tuple[str, str] | None = None
    if cursor is not None:
        match = re.fullmatch(r"inventory-cursor-([0-9a-f]{32})", cursor)
        if match is None:
            raise SourceInventoryError("inventory cursor is invalid")
        row = catalog.connection.execute(
            "SELECT created_at, source_version_id FROM governed_source_blobs "
            "WHERE substr(sha256_hex(source_version_id), 1, 32) = ?",
            (match.group(1),),
        ).fetchone()
        if row is None:
            raise SourceInventoryError("inventory cursor is invalid")
        after = (str(row[0]), str(row[1]))
    query = (
        "SELECT source_version_id, source_logical_id, blob_digest, safe_name, "
        "source_kind, media_type, byte_size, status, content_digest, payload_json, "
        "created_at FROM governed_source_blobs "
    )
    params: tuple[object, ...]
    if after is None:
        query += "ORDER BY created_at, source_version_id LIMIT ?"
        params = (limit + 1,)
    else:
        query += (
            "WHERE created_at > ? OR (created_at = ? AND source_version_id > ?) "
            "ORDER BY created_at, source_version_id LIMIT ?"
        )
        params = (after[0], after[0], after[1], limit + 1)
    rows = catalog.connection.execute(query, params).fetchall()
    items: list[SourceInventoryItem] = []
    for row in rows[:limit]:
        try:
            blob = GovernedSourceBlob.model_validate_json(row[9])
        except ValidationError as error:
            raise SourceInventoryError("inventory blob envelope is invalid") from error
        source_row = catalog.connection.execute(
            "SELECT logical_id, content_digest, payload_json FROM sources WHERE version_id = ?",
            (blob.source_version_id,),
        ).fetchone()
        if source_row is None:
            raise SourceInventoryError("inventory source is dangling")
        try:
            source = SourceAssetVersion.model_validate_json(source_row[2])
        except ValidationError as error:
            raise SourceInventoryError("inventory source envelope is invalid") from error
        if (
            canonical_model_json(blob) != row[9]
            or hashlib.sha256(row[9].encode("utf-8")).hexdigest() != row[8]
            or (
                blob.source_version_id,
                blob.source_logical_id,
                blob.blob_digest,
                blob.safe_name,
                blob.source_kind,
                blob.media_type,
                blob.byte_size,
                blob.status,
                blob.created_at.isoformat(),
            )
            != tuple(row[:8]) + (row[10],)
            or canonical_model_json(source) != source_row[2]
            or source.version_id != blob.source_version_id
            or source.logical_id != blob.source_logical_id
            or source.content_digest != blob.blob_digest
            or source.content_digest != source_row[1]
            or source.logical_id != source_row[0]
        ):
            raise SourceInventoryError("inventory source digest is invalid")
        items.append(
            SourceInventoryItem(
                source_id=blob.source_logical_id,
                source_version_id=blob.source_version_id,
                display_name=blob.safe_name,
                source_kind=blob.source_kind,
                media_type=blob.media_type,
                byte_size=blob.byte_size,
                content_digest=blob.blob_digest,
                status=blob.status,
            )
        )
    next_cursor = _cursor(items[-1].source_version_id) if len(rows) > limit and items else None
    return SourceInventoryPage(items=tuple(items), next_cursor=next_cursor)


__all__ = [
    "SourceInventoryError",
    "SourceInventoryItem",
    "SourceInventoryPage",
    "list_source_inventory",
]
