"""Bounded upload slots, durable import leases, and content-addressed promotion."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import AsyncIterable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_helper.catalog import (
    KnowledgeCatalog,
    SourceRegistrationInput,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.composition import canonical_digest
from course_helper.operations import (
    OperationMutationResult,
    OperationOutcome,
    OperationRequest,
    operation_status,
    run_operation,
)


Clock = Callable[[], datetime]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_TTL = timedelta(minutes=15)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SOURCE_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    ".pptx": (
        "pptx",
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation",),
    ),
    ".md": ("markdown", ("text/markdown", "text/plain")),
    ".csv": ("csv", ("text/csv", "text/plain")),
    ".parquet": ("parquet", ("application/vnd.apache.parquet", "application/octet-stream")),
    ".xls": ("xls", ("application/vnd.ms-excel",)),
    ".xlsx": (
        "xlsx",
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    ),
}


class UploadError(ValueError):
    _CODES = {
        "UPLOAD_INVALID",
        "UPLOAD_TOO_LARGE",
        "UPLOAD_NOT_FOUND",
        "UPLOAD_EXPIRED",
        "UPLOAD_CONFLICT",
        "UPLOAD_INTEGRITY_INVALID",
        "IMPORT_NOT_FOUND",
        "IMPORT_CONFLICT",
        "IMPORT_AUTHENTICATION_FAILED",
    }

    def __init__(self, code: str, message: str) -> None:
        if code not in self._CODES:
            raise ValueError("invalid upload error code")
        self.code = code
        super().__init__(message)


class UploadRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    upload_id: str = Field(pattern=r"^upload-[0-9a-f]{32}$")
    safe_name: str = Field(min_length=1, max_length=240)
    source_kind: Literal["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]
    media_type: str = Field(min_length=1, max_length=200)
    byte_size: int = Field(ge=1, le=MAX_UPLOAD_BYTES)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["available", "leased", "promoted", "cancelled", "expired"]
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class ImportLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    import_id: str = Field(pattern=r"^import-[0-9a-f]{32}$")
    upload_id: str = Field(pattern=r"^upload-[0-9a-f]{32}$")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["active", "promoted", "cancelled", "failed"]
    source_version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    created_at: datetime
    updated_at: datetime


class GovernedSourceBlob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    source_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    source_logical_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    upload_id: str = Field(pattern=r"^upload-[0-9a-f]{32}$")
    blob_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    safe_name: str = Field(min_length=1, max_length=240)
    source_kind: Literal["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]
    media_type: str = Field(min_length=1, max_length=200)
    byte_size: int = Field(ge=1, le=MAX_UPLOAD_BYTES)
    status: Literal["active", "revoked"] = "active"
    created_at: datetime


def _aware(clock: Clock) -> datetime:
    value = clock()
    if value.utcoffset() is None:
        raise ValueError("upload clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _session_digest(session_id: str) -> str:
    if _SAFE_ID.fullmatch(session_id) is None:
        raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _safe_upload_name(file_name: str, media_type: str) -> tuple[str, str, str]:
    normalized = unicodedata.normalize("NFC", file_name)
    if (
        not normalized
        or len(normalized) > 240
        or normalized != Path(normalized).name
        or "/" in normalized
        or "\\" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise UploadError("UPLOAD_INVALID", "Upload name is invalid")
    suffix = Path(normalized).suffix.casefold()
    policy = _SOURCE_TYPES.get(suffix)
    selected_media_type = media_type.split(";", 1)[0].strip().casefold()
    if policy is None or selected_media_type not in policy[1]:
        raise UploadError("UPLOAD_INVALID", "Upload type is not allowlisted")
    return normalized, policy[0], selected_media_type


def import_start_request_digest(upload_id: str, expected_content_digest: str) -> str:
    return canonical_digest(
        {
            "operation": "governed-import-start-v1",
            "upload_id": upload_id,
            "expected_content_digest": expected_content_digest,
        }
    )


def import_promotion_request_digest(import_id: str, expected_content_digest: str) -> str:
    return canonical_digest(
        {
            "operation": "governed-import-promote-v1",
            "import_id": import_id,
            "expected_content_digest": expected_content_digest,
        }
    )


def import_cancel_request_digest(import_id: str) -> str:
    return canonical_digest(
        {
            "operation": "governed-import-cancel-v1",
            "import_id": import_id,
        }
    )


class _UploadWriter:
    def __init__(
        self,
        store: "UploadStore",
        *,
        file_name: str,
        media_type: str,
        byte_size_hint: int,
        session_id: str,
        now: datetime,
    ) -> None:
        if type(byte_size_hint) is not int or not 1 <= byte_size_hint <= MAX_UPLOAD_BYTES:
            code = "UPLOAD_TOO_LARGE" if byte_size_hint > MAX_UPLOAD_BYTES else "UPLOAD_INVALID"
            raise UploadError(code, "Upload length is invalid")
        self.store = store
        self.safe_name, self.source_kind, self.media_type = _safe_upload_name(
            file_name, media_type
        )
        self.byte_size_hint = byte_size_hint
        self.session_digest = _session_digest(session_id)
        self.now = now
        self.size = 0
        self.digest = hashlib.sha256()
        self.store._ensure_roots()
        descriptor, name = tempfile.mkstemp(
            prefix="upload-", suffix=".tmp", dir=self.store._upload_root
        )
        self.path = Path(name)
        self.target = os.fdopen(descriptor, "wb")

    def feed(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise UploadError("UPLOAD_INVALID", "Upload stream is invalid")
        if not chunk:
            return
        self.size += len(chunk)
        if self.size > MAX_UPLOAD_BYTES or self.size > self.byte_size_hint:
            raise UploadError("UPLOAD_TOO_LARGE", "Upload exceeds its declared ceiling")
        self.digest.update(chunk)
        self.target.write(chunk)

    def finish(self) -> UploadRecord:
        self.target.flush()
        os.fsync(self.target.fileno())
        self.target.close()
        if self.size != self.byte_size_hint:
            raise UploadError("UPLOAD_INVALID", "Upload length did not match its declaration")
        upload_id = "upload-" + uuid4().hex
        final_path = self.store._upload_path(upload_id)
        os.replace(self.path, final_path)
        record = UploadRecord(
            upload_id=upload_id,
            safe_name=self.safe_name,
            source_kind=self.source_kind,
            media_type=self.media_type,
            byte_size=self.size,
            content_digest=self.digest.hexdigest(),
            state="available",
            expires_at=self.now + UPLOAD_TTL,
            created_at=self.now,
            updated_at=self.now,
        )
        payload = canonical_model_json(record)
        try:
            with self.store.catalog.atomic_write():
                self.store.catalog.connection.execute(
                    "INSERT INTO governed_uploads(upload_id, session_digest, safe_name, "
                    "source_kind, media_type, byte_size, content_digest, state, expires_at, "
                    "payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.upload_id,
                        self.session_digest,
                        record.safe_name,
                        record.source_kind,
                        record.media_type,
                        record.byte_size,
                        record.content_digest,
                        record.state,
                        record.expires_at.isoformat(),
                        payload,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        return record

    def abort(self) -> None:
        try:
            self.target.close()
        except Exception:
            pass
        self.path.unlink(missing_ok=True)


class UploadStore:
    """Own upload bytes under app data while SQLite owns all durable authority."""

    def __init__(self, catalog: KnowledgeCatalog, app_data_root: Path) -> None:
        self.catalog = catalog
        self._root = Path(app_data_root).absolute()
        self._upload_root = self._root / "uploads"
        self._blob_root = self._root / "source-blobs" / "sha256"

    def _ensure_roots(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for path in (self._root, self._upload_root, self._blob_root):
            path.mkdir(parents=True, exist_ok=True)
            info = os.lstat(path)
            attributes = getattr(info, "st_file_attributes", 0)
            if path.is_symlink() or bool(
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Upload storage boundary is invalid")

    def _upload_path(self, upload_id: str) -> Path:
        if re.fullmatch(r"upload-[0-9a-f]{32}", upload_id) is None:
            raise UploadError("UPLOAD_NOT_FOUND", "Upload was not found")
        return self._upload_root / f"{upload_id}.blob"

    def _blob_path(self, digest: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Source digest is invalid")
        return self._blob_root / digest[:2] / f"{digest}.blob"

    def create_upload(
        self,
        chunks: Iterable[bytes],
        *,
        file_name: str,
        media_type: str,
        byte_size_hint: int,
        session_id: str,
        clock: Clock,
    ) -> UploadRecord:
        writer = _UploadWriter(
            self,
            file_name=file_name,
            media_type=media_type,
            byte_size_hint=byte_size_hint,
            session_id=session_id,
            now=_aware(clock),
        )
        try:
            for chunk in chunks:
                writer.feed(chunk)
            return writer.finish()
        except Exception:
            writer.abort()
            raise

    async def create_upload_async(
        self,
        chunks: AsyncIterable[bytes],
        *,
        file_name: str,
        media_type: str,
        byte_size_hint: int,
        session_id: str,
        clock: Clock,
    ) -> UploadRecord:
        writer = _UploadWriter(
            self,
            file_name=file_name,
            media_type=media_type,
            byte_size_hint=byte_size_hint,
            session_id=session_id,
            now=_aware(clock),
        )
        try:
            async for chunk in chunks:
                writer.feed(chunk)
            return writer.finish()
        except Exception:
            writer.abort()
            raise

    def _load_upload(self, upload_id: str) -> tuple[UploadRecord, str]:
        row = self.catalog.connection.execute(
            "SELECT session_digest, safe_name, source_kind, media_type, byte_size, "
            "content_digest, state, expires_at, payload_json, created_at, updated_at "
            "FROM governed_uploads WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        if row is None:
            raise UploadError("UPLOAD_NOT_FOUND", "Upload was not found")
        try:
            value = UploadRecord.model_validate_json(row[8])
        except ValidationError as error:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Upload metadata is invalid") from error
        if canonical_model_json(value) != row[8] or (
            value.safe_name,
            value.source_kind,
            value.media_type,
            value.byte_size,
            value.content_digest,
            value.state,
            value.expires_at.isoformat(),
            value.created_at.isoformat(),
            value.updated_at.isoformat(),
        ) != tuple(row[1:8]) + tuple(row[9:11]):
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Upload metadata is invalid")
        return value, str(row[0])

    def _update_upload(self, value: UploadRecord) -> None:
        cursor = self.catalog.connection.execute(
            "UPDATE governed_uploads SET state = ?, payload_json = ?, updated_at = ? "
            "WHERE upload_id = ?",
            (
                value.state,
                canonical_model_json(value),
                value.updated_at.isoformat(),
                value.upload_id,
            ),
        )
        if cursor.rowcount != 1:
            raise UploadError("UPLOAD_CONFLICT", "Upload state changed")

    def _verify_upload_file(self, upload: UploadRecord) -> Path:
        path = self._upload_path(upload.upload_id)
        try:
            info = os.lstat(path)
            attributes = getattr(info, "st_file_attributes", 0)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
                or info.st_size != upload.byte_size
            ):
                raise OSError("unsafe upload")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise OSError("oversized upload")
                    digest.update(chunk)
            if size != upload.byte_size or digest.hexdigest() != upload.content_digest:
                raise OSError("upload digest mismatch")
        except OSError as error:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Upload bytes are invalid") from error
        return path

    def start_import(
        self,
        request: OperationRequest,
        *,
        upload_id: str,
        expected_content_digest: str,
        clock: Clock,
    ) -> OperationOutcome:
        if request.request_digest != import_start_request_digest(
            upload_id, expected_content_digest
        ):
            raise UploadError("IMPORT_CONFLICT", "Import request digest is invalid")
        now = _aware(clock)
        session_digest = _session_digest(request.session_id)

        def mutation() -> OperationMutationResult:
            upload, owner_digest = self._load_upload(upload_id)
            if owner_digest != session_digest:
                raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
            if upload.state != "available":
                raise UploadError("IMPORT_CONFLICT", "Upload is not available")
            if now >= upload.expires_at:
                raise UploadError("UPLOAD_EXPIRED", "Upload expired")
            if upload.content_digest != expected_content_digest:
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Upload digest changed")
            self._verify_upload_file(upload)
            import_id = "import-" + canonical_digest(
                {"operation_id": request.operation_id, "upload_id": upload_id}
            )[:32]
            lease = ImportLease(
                import_id=import_id,
                upload_id=upload_id,
                operation_id=request.operation_id,
                request_digest=request.request_digest,
                state="active",
                created_at=now,
                updated_at=now,
            )
            self.catalog.connection.execute(
                "INSERT INTO import_leases(import_id, upload_id, operation_id, request_digest, "
                "actor_id, actor_type, session_digest, state, source_version_id, payload_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?)",
                (
                    lease.import_id,
                    lease.upload_id,
                    lease.operation_id,
                    lease.request_digest,
                    request.actor.actor_id,
                    request.actor.actor_type,
                    session_digest,
                    canonical_model_json(lease),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._update_upload(
                upload.model_copy(update={"state": "leased", "updated_at": now})
            )
            return OperationMutationResult(
                result_refs={"importId": import_id, "uploadId": upload_id},
                item_outcomes=(),
                index_outbox=(),
            )

        return run_operation(
            self.catalog, request, mutation, clock=lambda: now
        )

    def _load_lease(self, import_id: str) -> tuple[ImportLease, tuple[str, str, str]]:
        row = self.catalog.connection.execute(
            "SELECT upload_id, operation_id, request_digest, actor_id, actor_type, "
            "session_digest, state, source_version_id, payload_json, created_at, updated_at "
            "FROM import_leases WHERE import_id = ?",
            (import_id,),
        ).fetchone()
        if row is None:
            raise UploadError("IMPORT_NOT_FOUND", "Import was not found")
        try:
            value = ImportLease.model_validate_json(row[8])
        except ValidationError as error:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Import lease is invalid") from error
        if canonical_model_json(value) != row[8] or (
            value.upload_id,
            value.operation_id,
            value.request_digest,
            value.state,
            value.source_version_id,
            value.created_at.isoformat(),
            value.updated_at.isoformat(),
        ) != (row[0], row[1], row[2], row[6], row[7], row[9], row[10]):
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Import lease is invalid")
        return value, (str(row[3]), str(row[4]), str(row[5]))

    def import_status(
        self,
        import_id: str,
        *,
        session_id: str,
        actor: ActorRef | None = None,
    ) -> ImportLease:
        lease, authority = self._load_lease(import_id)
        if (
            authority[2] != _session_digest(session_id)
            or (
                actor is not None
                and (authority[0], authority[1])
                != (actor.actor_id, actor.actor_type)
            )
        ):
            raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
        upload, owner = self._load_upload(lease.upload_id)
        expected_upload_state = {
            "active": "leased",
            "promoted": "promoted",
            "cancelled": "cancelled",
            "failed": "leased",
        }[lease.state]
        if owner != authority[2] or upload.state != expected_upload_state:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Import state is invalid")
        if lease.state == "active":
            self._verify_upload_file(upload)
        elif lease.state == "promoted":
            if lease.source_version_id is None:
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Import source is invalid")
            blob = self.catalog.connection.execute(
                "SELECT blob_digest, byte_size, media_type FROM governed_source_blobs "
                "WHERE source_version_id = ?",
                (lease.source_version_id,),
            ).fetchone()
            if blob is None or tuple(blob) != (
                upload.content_digest,
                upload.byte_size,
                upload.media_type,
            ):
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Import source is invalid")
            self._verify_blob(self._blob_path(upload.content_digest), upload)
        elif lease.source_version_id is not None:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Import source is invalid")
        return lease

    def _install_blob(self, upload: UploadRecord, source_path: Path) -> Path:
        self._ensure_roots()
        target = self._blob_path(upload.content_digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        parent_info = os.lstat(target.parent)
        parent_attributes = getattr(parent_info, "st_file_attributes", 0)
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or target.parent.is_symlink()
            or bool(
                parent_attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            raise UploadError(
                "UPLOAD_INTEGRITY_INVALID", "Source storage boundary is invalid"
            )
        if target.exists():
            self._verify_blob(target, upload)
            return target
        descriptor, name = tempfile.mkstemp(prefix="source-blob-", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            digest = hashlib.sha256()
            size = 0
            with source_path.open("rb") as source, os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise UploadError("UPLOAD_TOO_LARGE", "Source blob exceeds its ceiling")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size != upload.byte_size or digest.hexdigest() != upload.content_digest:
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Source blob digest is invalid")
            try:
                os.link(temporary, target)
                temporary.unlink()
            except FileExistsError:
                temporary.unlink(missing_ok=True)
            except OSError:
                if target.exists():
                    temporary.unlink(missing_ok=True)
                else:
                    os.replace(temporary, target)
            self._verify_blob(target, upload)
            return target
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise

    def _verify_blob(self, path: Path, upload: UploadRecord) -> None:
        digest = hashlib.sha256()
        size = 0
        try:
            info = os.lstat(path)
            attributes = getattr(info, "st_file_attributes", 0)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or bool(
                    attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
            ):
                raise OSError("unsafe source blob")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            if size != upload.byte_size or digest.hexdigest() != upload.content_digest:
                raise OSError("source blob mismatch")
        except OSError as error:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Source blob is invalid") from error

    def promote_import(
        self,
        request: OperationRequest,
        *,
        import_id: str,
        expected_content_digest: str,
        clock: Clock,
        after_commit: Callable[[OperationOutcome], None] | None = None,
    ) -> OperationOutcome:
        if request.request_digest != import_promotion_request_digest(
            import_id, expected_content_digest
        ):
            raise UploadError("IMPORT_CONFLICT", "Promotion request digest is invalid")
        existing = operation_status(
            self.catalog,
            operation_id=request.operation_id,
            actor_id=request.actor.actor_id,
            actor_type=request.actor.actor_type,
            session_id=request.session_id,
        )
        if existing.status == "committed":
            if existing.request_digest != request.request_digest:
                raise UploadError("IMPORT_CONFLICT", "Promotion operation digest changed")
            lease = self.import_status(
                import_id,
                session_id=request.session_id,
                actor=request.actor,
            )
            if lease.state != "promoted" or lease.source_version_id is None:
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Promotion outcome is invalid")
            source = self.catalog.get_source(lease.source_version_id)
            if source is None or dict(existing.result_refs) != {
                "importId": import_id,
                "sourceId": source.logical_id,
                "sourceVersionId": source.version_id,
                "contentDigest": source.content_digest,
            }:
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Promotion outcome is invalid")
            self._upload_path(lease.upload_id).unlink(missing_ok=True)
            return existing
        now = _aware(clock)
        lease, authority = self._load_lease(import_id)
        actor_id, actor_type, owner_session_digest = authority
        if (
            actor_id != request.actor.actor_id
            or actor_type != request.actor.actor_type
            or owner_session_digest != _session_digest(request.session_id)
        ):
            raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
        upload, upload_owner = self._load_upload(lease.upload_id)
        if upload_owner != owner_session_digest:
            raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
        if upload.content_digest != expected_content_digest:
            raise UploadError("UPLOAD_INTEGRITY_INVALID", "Upload digest changed")
        source_path = self._verify_upload_file(upload)
        self._install_blob(upload, source_path)

        def mutation() -> OperationMutationResult:
            current_lease, current_authority = self._load_lease(import_id)
            current_upload, current_owner = self._load_upload(current_lease.upload_id)
            if (
                current_authority != authority
                or current_owner != owner_session_digest
                or current_lease.state != "active"
                or current_upload.state != "leased"
                or current_upload.content_digest != expected_content_digest
            ):
                raise UploadError("IMPORT_CONFLICT", "Import lease changed")
            source = self.catalog.register_or_reuse_source(
                SourceRegistrationInput(
                    locator=SourceLocator(
                        root_id="governed-upload",
                        relative_path=(
                            f"sha256/{current_upload.content_digest[:2]}/"
                            f"{current_upload.content_digest}.blob"
                        ),
                    ),
                    display_name=current_upload.safe_name,
                    source_kind=current_upload.source_kind,
                    media_type=current_upload.media_type,
                    byte_size=current_upload.byte_size,
                    content_digest=current_upload.content_digest,
                    extraction_status="registered",
                    created_at=now,
                    created_by=request.actor,
                )
            )
            blob = GovernedSourceBlob(
                source_version_id=source.version_id,
                source_logical_id=source.logical_id,
                upload_id=current_upload.upload_id,
                blob_digest=current_upload.content_digest,
                safe_name=current_upload.safe_name,
                source_kind=current_upload.source_kind,
                media_type=current_upload.media_type,
                byte_size=current_upload.byte_size,
                created_at=now,
            )
            payload = canonical_model_json(blob)
            existing_blob = self.catalog.connection.execute(
                "SELECT blob_digest, byte_size, media_type FROM governed_source_blobs "
                "WHERE source_version_id = ?",
                (source.version_id,),
            ).fetchone()
            if existing_blob is None:
                self.catalog.connection.execute(
                    "INSERT INTO governed_source_blobs(source_version_id, source_logical_id, "
                    "upload_id, blob_digest, safe_name, source_kind, media_type, byte_size, "
                    "status, content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                        payload,
                        now.isoformat(),
                    ),
                )
            elif tuple(existing_blob) != (
                blob.blob_digest,
                blob.byte_size,
                blob.media_type,
            ):
                raise UploadError(
                    "UPLOAD_INTEGRITY_INVALID", "Existing source blob metadata changed"
                )
            promoted_lease = current_lease.model_copy(
                update={
                    "state": "promoted",
                    "source_version_id": source.version_id,
                    "updated_at": now,
                }
            )
            self.catalog.connection.execute(
                "UPDATE import_leases SET state = 'promoted', source_version_id = ?, "
                "payload_json = ?, updated_at = ? WHERE import_id = ? AND state = 'active'",
                (
                    source.version_id,
                    canonical_model_json(promoted_lease),
                    now.isoformat(),
                    import_id,
                ),
            )
            self._update_upload(
                current_upload.model_copy(update={"state": "promoted", "updated_at": now})
            )
            return OperationMutationResult(
                result_refs={
                    "importId": import_id,
                    "sourceId": source.logical_id,
                    "sourceVersionId": source.version_id,
                    "contentDigest": source.content_digest,
                },
                item_outcomes=(),
                index_outbox=(),
            )

        outcome = run_operation(
            self.catalog,
            request,
            mutation,
            clock=lambda: now,
            after_commit=after_commit,
        )
        self._upload_path(upload.upload_id).unlink(missing_ok=True)
        return outcome

    def cancel_upload(self, upload_id: str, *, session_id: str, clock: Clock) -> bool:
        now = _aware(clock)
        with self.catalog.atomic_write():
            upload, owner = self._load_upload(upload_id)
            if owner != _session_digest(session_id):
                raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
            if upload.state in {"cancelled", "expired"}:
                return True
            if upload.state != "available":
                raise UploadError("UPLOAD_CONFLICT", "Leased or promoted upload cannot be cancelled")
            self._update_upload(
                upload.model_copy(update={"state": "cancelled", "updated_at": now})
            )
        self._upload_path(upload_id).unlink(missing_ok=True)
        return True

    def cancel_import_operation(
        self,
        request: OperationRequest,
        *,
        import_id: str,
        clock: Clock,
        after_commit: Callable[[OperationOutcome], None] | None = None,
    ) -> OperationOutcome:
        if request.request_digest != import_cancel_request_digest(import_id):
            raise UploadError("IMPORT_CONFLICT", "Cancel request digest is invalid")
        existing = operation_status(
            self.catalog,
            operation_id=request.operation_id,
            actor_id=request.actor.actor_id,
            actor_type=request.actor.actor_type,
            session_id=request.session_id,
        )
        if existing.status == "committed":
            if existing.request_digest != request.request_digest:
                raise UploadError("IMPORT_CONFLICT", "Cancel operation digest changed")
            lease, authority = self._load_lease(import_id)
            if (
                authority[0] != request.actor.actor_id
                or authority[1] != request.actor.actor_type
                or authority[2] != _session_digest(request.session_id)
                or lease.state != "cancelled"
                or dict(existing.result_refs)
                != {"importId": import_id, "status": "cancelled"}
            ):
                raise UploadError("UPLOAD_INTEGRITY_INVALID", "Cancel outcome is invalid")
            self._upload_path(lease.upload_id).unlink(missing_ok=True)
            return existing

        now = _aware(clock)
        lease, authority = self._load_lease(import_id)
        if (
            authority[0] != request.actor.actor_id
            or authority[1] != request.actor.actor_type
            or authority[2] != _session_digest(request.session_id)
        ):
            raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")

        def mutation() -> OperationMutationResult:
            current_lease, current_authority = self._load_lease(import_id)
            upload, owner = self._load_upload(current_lease.upload_id)
            if (
                current_authority != authority
                or current_lease.state != "active"
                or current_lease.source_version_id is not None
                or owner != authority[2]
                or upload.state != "leased"
            ):
                raise UploadError("IMPORT_CONFLICT", "Import lease changed")
            cancelled = current_lease.model_copy(
                update={"state": "cancelled", "updated_at": now}
            )
            cursor = self.catalog.connection.execute(
                "UPDATE import_leases SET state = 'cancelled', payload_json = ?, "
                "updated_at = ? WHERE import_id = ? AND state = 'active'",
                (canonical_model_json(cancelled), now.isoformat(), import_id),
            )
            if cursor.rowcount != 1:
                raise UploadError("IMPORT_CONFLICT", "Import lease changed")
            self._update_upload(
                upload.model_copy(update={"state": "cancelled", "updated_at": now})
            )
            return OperationMutationResult(
                result_refs={"importId": import_id, "status": "cancelled"},
                item_outcomes=(),
                index_outbox=(),
            )

        outcome = run_operation(
            self.catalog,
            request,
            mutation,
            clock=lambda: now,
            after_commit=after_commit,
        )
        self._upload_path(lease.upload_id).unlink(missing_ok=True)
        return outcome

    def cancel_import(self, import_id: str, *, session_id: str, clock: Clock) -> bool:
        now = _aware(clock)
        upload_id: str
        with self.catalog.atomic_write():
            lease, authority = self._load_lease(import_id)
            if authority[2] != _session_digest(session_id):
                raise UploadError("IMPORT_AUTHENTICATION_FAILED", "Import session is invalid")
            if lease.state == "cancelled":
                return True
            if lease.state != "active":
                raise UploadError("IMPORT_CONFLICT", "Promoted import cannot be cancelled")
            upload, owner = self._load_upload(lease.upload_id)
            if owner != authority[2] or upload.state != "leased":
                raise UploadError("IMPORT_CONFLICT", "Import lease changed")
            cancelled = lease.model_copy(update={"state": "cancelled", "updated_at": now})
            self.catalog.connection.execute(
                "UPDATE import_leases SET state = 'cancelled', payload_json = ?, updated_at = ? "
                "WHERE import_id = ? AND state = 'active'",
                (canonical_model_json(cancelled), now.isoformat(), import_id),
            )
            self._update_upload(
                upload.model_copy(update={"state": "cancelled", "updated_at": now})
            )
            upload_id = upload.upload_id
        self._upload_path(upload_id).unlink(missing_ok=True)
        return True

    def expire_uploads(self, *, clock: Clock, limit: int = 100) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("expiry limit is invalid")
        now = _aware(clock)
        expired: list[str] = []
        with self.catalog.atomic_write():
            rows = self.catalog.connection.execute(
                "SELECT upload_id FROM governed_uploads WHERE state = 'available' "
                "AND expires_at <= ? ORDER BY expires_at, upload_id LIMIT ?",
                (now.isoformat(), limit),
            ).fetchall()
            for row in rows:
                upload, _owner = self._load_upload(str(row[0]))
                self._update_upload(
                    upload.model_copy(update={"state": "expired", "updated_at": now})
                )
                expired.append(upload.upload_id)
        for upload_id in expired:
            self._upload_path(upload_id).unlink(missing_ok=True)
        return tuple(expired)


__all__ = [
    "GovernedSourceBlob",
    "ImportLease",
    "MAX_UPLOAD_BYTES",
    "UPLOAD_TTL",
    "UploadError",
    "UploadRecord",
    "UploadStore",
    "import_cancel_request_digest",
    "import_promotion_request_digest",
    "import_start_request_digest",
]
