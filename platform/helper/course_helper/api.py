"""Authenticated FastAPI boundary for the loopback knowledge helper."""

from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from course_helper.catalog import CURRENT_MIGRATION_VERSION, KnowledgeCatalog
from course_helper.artifacts import ArtifactError, ArtifactStore
from course_helper.domain.evidence import EvidenceObject
from course_helper.domain.composition import course_version_content_digest
from course_helper.domain.knowledge import TagVocabularyVersion
from course_helper.domain.slide_ast import (
    runtime_manifest_content_digest,
    slide_deck_content_digest,
)
from course_helper.jobs import (
    JobOutcome,
    JobSpec,
    WorkerRuntimeConfig,
    _camelize_json,
    _lower_camel,
)
from course_helper.index_outbox import reopen_index_snapshot
from course_helper.session import LaunchSession, SessionRejected
from course_helper.source_inventory import (
    SourceInventoryError,
    SourceInventoryItem,
    SourceInventoryPage,
    list_source_inventory,
)
from course_helper.uploads import UploadError, UploadRecord, UploadStore


SERVICE_VERSION = "0.1.0"


class HttpResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_lower_camel,
        extra="forbid",
        populate_by_name=True,
        validate_default=True,
    )


class NonceExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nonce: str = Field(min_length=1)


class SessionExchangeResponse(HttpResponseModel):
    session_token: str = Field(min_length=43)


class SourceLocatorResponse(HttpResponseModel):
    root_id: str
    relative_path: str


class EvidenceCheckResponse(HttpResponseModel):
    code: str
    status: Literal["passed", "warning", "failed", "skipped"]
    message: str
    details: Mapping[str, Any] = Field(default_factory=dict)


class EvidenceErrorResponse(HttpResponseModel):
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = Field(default_factory=dict)


class EvidenceArtifactResponse(HttpResponseModel):
    artifact_id: str
    locator: SourceLocatorResponse
    media_type: str
    content_digest: str
    byte_size: int


class EvidenceResponse(HttpResponseModel):
    evidence_id: str
    kind: Literal[
        "extraction",
        "retrieval",
        "dedup",
        "composition",
        "validation",
        "publish",
        "rehearsal",
        "dataset-profile",
        "execution",
        "runtime",
    ]
    subject_version_id: str | None = None
    status: Literal["verified", "warning", "failed", "degraded"]
    input_summary: Mapping[str, Any] = Field(default_factory=dict)
    output_summary: Mapping[str, Any] = Field(default_factory=dict)
    producer: str
    producer_version: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int | None = None
    checks: tuple[EvidenceCheckResponse, ...] = ()
    errors: tuple[EvidenceErrorResponse, ...] = ()
    artifacts: tuple[EvidenceArtifactResponse, ...] = ()

    @classmethod
    def from_domain(cls, evidence: EvidenceObject) -> EvidenceResponse:
        return cls.model_validate(evidence.model_dump(mode="json"))


class JobResponse(HttpResponseModel):
    result: Mapping[str, Any]
    evidence: EvidenceResponse


class HealthResponse(HttpResponseModel):
    service_version: str
    schema_version: int = Field(ge=1)
    database_ready: bool


class TagOptionResponse(HttpResponseModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    dimension: str = Field(min_length=1, max_length=128)


class KnowledgeSummary(HttpResponseModel):
    schema_version: Literal[1] = 1
    source_count: int = Field(ge=0)
    published_card_count: int = Field(ge=0)
    review_task_count: int = Field(ge=0)
    retrieval_mode: Literal["hybrid", "fts-degraded"]
    index_snapshot_id: str | None = None
    index_snapshot_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    index_state: Literal["ready", "degraded", "unavailable"] = "unavailable"
    tag_labels: tuple[str, ...]
    tag_options: tuple[TagOptionResponse, ...] = ()
    updated_at: datetime


class UploadResponse(HttpResponseModel):
    schema_version: Literal[1] = 1
    upload_id: str
    safe_name: str
    source_kind: Literal["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]
    media_type: str
    byte_size: int = Field(ge=1, le=20 * 1024 * 1024)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["available"]
    expires_at: datetime

    @classmethod
    def from_record(cls, record: UploadRecord) -> UploadResponse:
        return cls(
            upload_id=record.upload_id,
            safe_name=record.safe_name,
            source_kind=record.source_kind,
            media_type=record.media_type,
            byte_size=record.byte_size,
            content_digest=record.content_digest,
            state="available",
            expires_at=record.expires_at,
        )


class SourceInventoryItemResponse(HttpResponseModel):
    schema_version: Literal[1] = 1
    source_id: str
    source_version_id: str
    display_name: str
    source_kind: Literal["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]
    media_type: str
    byte_size: int = Field(ge=1, le=20 * 1024 * 1024)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "revoked"]

    @classmethod
    def from_domain(cls, item: SourceInventoryItem) -> SourceInventoryItemResponse:
        return cls.model_validate(item.model_dump(mode="json"))


class SourceInventoryResponse(HttpResponseModel):
    schema_version: Literal[1] = 1
    items: tuple[SourceInventoryItemResponse, ...]
    next_cursor: str | None = None

    @classmethod
    def from_domain(cls, page: SourceInventoryPage) -> SourceInventoryResponse:
        return cls(
            items=tuple(SourceInventoryItemResponse.from_domain(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class CourseProjectionResponse(HttpResponseModel):
    schema_version: Literal[1] = 1
    course_version_id: str
    course_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage_scope: Literal["private-training", "internal", "public"]
    status: Literal["published"]
    requirement: Mapping[str, Any]
    outline: Mapping[str, Any]
    slide_deck: Mapping[str, Any]
    runtime_manifest: Mapping[str, Any]


class JobRunner(Protocol):
    async def run(
        self,
        job: object,
        *,
        disconnected: Callable[[], Awaitable[bool]],
        session_id: str | None = None,
    ) -> JobOutcome: ...


class ProjectionSupervisor(Protocol):
    def shutdown(self) -> None: ...


@dataclass(frozen=True)
class HelperRuntime:
    config: WorkerRuntimeConfig
    launch_session: LaunchSession
    job_runner: JobRunner
    projection_supervisor: ProjectionSupervisor | None = None


def create_app(runtime: HelperRuntime) -> FastAPI:
    """Create a loopback-only API with explicit origin and session guards."""

    app = FastAPI(title="Course Studio Helper", version=SERVICE_VERSION)
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[runtime.launch_session.allowed_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Course-Session", "X-Upload-Name"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid-request",
                    "message": "Request validation failed",
                }
            },
        )

    def require_session(request: Request) -> str:
        origin = request.headers.get("Origin", "")
        token = request.headers.get("X-Course-Session", "")
        if (
            origin != runtime.launch_session.allowed_origin
            or not runtime.launch_session.verify_token(token)
        ):
            raise HTTPException(
                status_code=401,
                detail="unauthorized",
                headers={"Cache-Control": "no-store"},
            )
        return token

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            service_version=SERVICE_VERSION,
            schema_version=CURRENT_MIGRATION_VERSION,
            database_ready=_database_ready(runtime.config.database_path),
        )

    @app.post("/v1/session/exchange", response_model=SessionExchangeResponse)
    def exchange_session(
        body: NonceExchangeRequest,
        request: Request,
    ) -> SessionExchangeResponse:
        try:
            token = runtime.launch_session.exchange(
                body.nonce,
                origin=request.headers.get("Origin", ""),
            )
        except SessionRejected:
            raise HTTPException(status_code=401, detail="unauthorized") from None
        return SessionExchangeResponse(session_token=token)

    @app.post("/v1/jobs", response_model=JobResponse)
    async def run_job(
        job: JobSpec,
        request: Request,
        _authenticated: str = Depends(require_session),
    ) -> JSONResponse:
        outcome = await runtime.job_runner.run(
            job,
            disconnected=request.is_disconnected,
            session_id=_session_owner_id(_authenticated),
        )
        response = JobResponse(
            result=outcome.result,
            evidence=EvidenceResponse.from_domain(outcome.evidence),
        )
        return JSONResponse(
            status_code=outcome.status_code,
            content=jsonable_encoder(response, by_alias=True),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/knowledge/summary", response_model=KnowledgeSummary)
    def knowledge_summary(
        request: Request,
        _authenticated: str = Depends(require_session),
    ) -> KnowledgeSummary:
        if request.query_params:
            raise HTTPException(status_code=422, detail="invalid request")
        try:
            return _knowledge_summary(runtime.config.database_path)
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="knowledge catalog unavailable",
            ) from None

    @app.get(
        "/v1/courses/{course_version_id}/projection",
        response_model=CourseProjectionResponse,
    )
    def course_projection(
        course_version_id: str,
        slideDeckId: str,
        runtimeManifestId: str,
        request: Request,
        _authenticated: str = Depends(require_session),
    ) -> CourseProjectionResponse:
        if (
            set(request.query_params.keys()) != {"slideDeckId", "runtimeManifestId"}
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", course_version_id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", slideDeckId)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", runtimeManifestId)
        ):
            raise HTTPException(status_code=422, detail="invalid request")
        try:
            with KnowledgeCatalog.open(runtime.config.database_path) as catalog:
                course = catalog.get_course_version(course_version_id)
                deck = catalog.get_slide_deck(slideDeckId)
                manifest = catalog.get_runtime_manifest(runtimeManifestId)
                if course is None or deck is None or manifest is None:
                    raise LookupError
                outline = catalog.get_course_outline(course.payload.outline_version_id)
                requirement = catalog.get_course_requirement(course.payload.requirement_id)
                if outline is None or requirement is None:
                    raise LookupError
                if (
                    course.payload.status != "published"
                    or course.payload.content_digest
                    != course_version_content_digest(course.payload)
                    or deck.payload.course_version_id != course.payload.version_id
                    or deck.payload.content_digest != slide_deck_content_digest(deck.payload)
                    or manifest.payload.course_version_id != course.payload.version_id
                    or manifest.payload.slide_deck_version_id != deck.payload.version_id
                    or manifest.payload.slide_deck_digest != deck.payload.content_digest
                    or manifest.payload.content_digest
                    != runtime_manifest_content_digest(manifest.payload)
                    or outline.payload.requirement_id != requirement.payload.requirement_id
                ):
                    raise LookupError
                return CourseProjectionResponse(
                    course_version_id=course.payload.version_id,
                    course_digest=course.payload.content_digest,
                    usage_scope=course.payload.usage_scope,
                    status="published",
                    requirement=_camelize_json(requirement.payload.model_dump(mode="json")),
                    outline=_camelize_json(outline.payload.model_dump(mode="json")),
                    slide_deck=_camelize_json(deck.payload.model_dump(mode="json")),
                    runtime_manifest=_camelize_json(manifest.payload.model_dump(mode="json")),
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="course projection unavailable",
                headers={"Cache-Control": "no-store"},
            ) from None

    @app.post("/v1/uploads", response_model=UploadResponse, status_code=201)
    async def create_upload(
        request: Request,
        session_id: str = Depends(require_session),
    ) -> JSONResponse:
        names = request.headers.getlist("X-Upload-Name")
        media_types = request.headers.getlist("Content-Type")
        lengths = request.headers.getlist("Content-Length")
        if len(names) != 1 or len(media_types) != 1 or len(lengths) != 1:
            return _safe_error(
                status_code=422,
                code="invalid-upload",
                message="Upload headers are invalid",
            )
        declared_length = lengths[0]
        if re.fullmatch(r"[1-9][0-9]*", declared_length) is None:
            return _safe_error(
                status_code=422,
                code="invalid-upload",
                message="Upload length is invalid",
            )
        try:
            with KnowledgeCatalog.open(runtime.config.database_path) as catalog:
                record = await UploadStore(
                    catalog, Path(runtime.config.app_data_path)
                ).create_upload_async(
                    request.stream(),
                    file_name=names[0],
                    media_type=media_types[0],
                    byte_size_hint=int(declared_length),
                    session_id=_session_owner_id(session_id),
                    clock=lambda: datetime.now(timezone.utc),
                )
        except UploadError as error:
            return _upload_error(error)
        except Exception:
            return _safe_error(
                status_code=503,
                code="upload-unavailable",
                message="Upload storage is unavailable",
            )
        response = UploadResponse.from_record(record)
        return JSONResponse(
            status_code=201,
            content=jsonable_encoder(response, by_alias=True),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/knowledge/sources", response_model=SourceInventoryResponse)
    def source_inventory(
        request: Request,
        _authenticated: str = Depends(require_session),
    ) -> JSONResponse:
        pairs = tuple(request.query_params.multi_items())
        keys = tuple(key for key, _value in pairs)
        if any(key not in {"cursor", "limit"} for key in keys) or len(set(keys)) != len(
            keys
        ):
            return _safe_error(
                status_code=422,
                code="invalid-inventory-request",
                message="Inventory request is invalid",
            )
        raw_limit = request.query_params.get("limit", "50")
        if re.fullmatch(r"(?:[1-9]|[1-9][0-9]|100)", raw_limit) is None:
            return _safe_error(
                status_code=422,
                code="invalid-inventory-request",
                message="Inventory request is invalid",
            )
        cursor = request.query_params.get("cursor")
        try:
            with KnowledgeCatalog.open(runtime.config.database_path) as catalog:
                page = list_source_inventory(
                    catalog,
                    cursor=cursor,
                    limit=int(raw_limit),
                )
        except SourceInventoryError:
            return _safe_error(
                status_code=422,
                code="invalid-inventory-request",
                message="Inventory request is invalid",
            )
        except Exception:
            return _safe_error(
                status_code=503,
                code="inventory-unavailable",
                message="Source inventory is unavailable",
            )
        response = SourceInventoryResponse.from_domain(page)
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(response, by_alias=True),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/artifacts/{artifact_id}", response_model=None)
    def artifact_bytes(
        artifact_id: str,
        request: Request,
        _authenticated: str = Depends(require_session),
    ) -> StreamingResponse | JSONResponse:
        if (
            request.query_params
            or re.fullmatch(r"artifact-[0-9a-f]{64}", artifact_id) is None
        ):
            return _safe_artifact_not_found()
        try:
            with KnowledgeCatalog.open(runtime.config.database_path) as catalog:
                stored = catalog.get_artifact(artifact_id)
                if stored is None:
                    return _safe_artifact_not_found()
                metadata = stored.payload
                if metadata.media_type == "image/svg+xml" and not _is_data_chart_svg(
                    catalog, artifact_id
                ):
                    return _safe_artifact_not_found()
            stream = ArtifactStore(
                Path(runtime.config.app_data_path) / "artifacts"
            ).open_verified(metadata)
        except (ArtifactError, OSError, RuntimeError, ValueError):
            return _safe_artifact_not_found()

        def chunks():
            with stream:
                while block := stream.read(1024 * 1024):
                    yield block

        return StreamingResponse(
            chunks(),
            status_code=200,
            media_type=metadata.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Length": str(metadata.byte_size),
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


def _safe_error(*, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _safe_artifact_not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "artifact-not-found", "message": "Artifact is unavailable"}},
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _is_data_chart_svg(catalog: KnowledgeCatalog, artifact_id: str) -> bool:
    rows = catalog.connection.execute(
        "SELECT visual.payload_json FROM lineage "
        "JOIN visuals AS visual ON visual.version_id = lineage.to_version_id "
        "WHERE lineage.from_version_id = ? AND lineage.relation = 'derived_from'",
        (artifact_id,),
    ).fetchall()
    if len(rows) != 1:
        return False
    try:
        from course_helper.domain.sources import VisualAssetVersion

        visual = VisualAssetVersion.model_validate_json(str(rows[0][0]), strict=False)
    except Exception:
        return False
    return visual.authenticity == "data-derived"


def _session_owner_id(session_token: str) -> str:
    """Derive a safe opaque owner ID without storing or replaying the token."""

    return "session-" + hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def _upload_error(error: UploadError) -> JSONResponse:
    status, code, message = {
        "UPLOAD_INVALID": (422, "invalid-upload", "Upload is invalid"),
        "UPLOAD_TOO_LARGE": (413, "upload-too-large", "Upload exceeds its size limit"),
        "UPLOAD_NOT_FOUND": (404, "upload-not-found", "Upload was not found"),
        "UPLOAD_EXPIRED": (410, "upload-expired", "Upload expired"),
        "UPLOAD_CONFLICT": (409, "upload-conflict", "Upload state changed"),
        "UPLOAD_INTEGRITY_INVALID": (409, "upload-integrity", "Upload integrity failed"),
        "IMPORT_NOT_FOUND": (404, "import-not-found", "Import was not found"),
        "IMPORT_CONFLICT": (409, "import-conflict", "Import state changed"),
        "IMPORT_AUTHENTICATION_FAILED": (401, "unauthorized", "Unauthorized"),
    }[error.code]
    return _safe_error(status_code=status, code=code, message=message)


def _database_ready(database_path: str) -> bool:
    try:
        with KnowledgeCatalog.open(database_path) as catalog:
            version = catalog.connection.execute(
                "SELECT max(version) FROM schema_migrations"
            ).fetchone()[0]
        return version == CURRENT_MIGRATION_VERSION
    except Exception:
        return False


def _knowledge_summary(database_path: str) -> KnowledgeSummary:
    with KnowledgeCatalog.open(database_path) as catalog:
        connection = catalog.connection
        source_count = int(connection.execute("SELECT count(*) FROM sources").fetchone()[0])
        published_card_count = int(
            connection.execute(
                """
                SELECT count(*)
                FROM card_lifecycle_current
                WHERE status = 'published' AND suspended = 0
                """
            ).fetchone()[0]
        )
        review_task_count = int(
            connection.execute(
                "SELECT count(*) FROM review_tasks WHERE status = 'open'"
            ).fetchone()[0]
        )
        vocabulary_rows = connection.execute(
            "SELECT payload_json FROM tag_vocabularies"
        ).fetchall()
        payload_rows = tuple(
            row[0]
            for table in ("sources", "cards", "review_tasks", "tag_vocabularies")
            for row in connection.execute(f"SELECT payload_json FROM {table}").fetchall()
        )
        latest_snapshot_row = connection.execute(
            "SELECT index_snapshot_id FROM embedding_index_snapshots "
            "ORDER BY created_at DESC, index_snapshot_id DESC LIMIT 1"
        ).fetchone()
        latest_snapshot = (
            None
            if latest_snapshot_row is None
            else reopen_index_snapshot(catalog, str(latest_snapshot_row[0]))
        )

    vocabularies = tuple(
        TagVocabularyVersion.model_validate_json(row[0]) for row in vocabulary_rows
    )
    latest_vocabulary = (
        max(vocabularies, key=lambda item: (item.created_at, item.version_id))
        if vocabularies
        else None
    )
    tag_labels = tuple(
        sorted(
            {
                value.labels.get("zh-CN") or value.labels.get("en") or value.id
                for dimension in latest_vocabulary.dimensions
                for value in dimension.values
                if value.status == "active"
            }
        )
    ) if latest_vocabulary is not None else ()
    tag_options = tuple(
        {
            "id": value.id,
            "label": value.labels.get("zh-CN") or value.labels.get("en") or value.id,
            "dimension": dimension.id,
        }
        for dimension in (() if latest_vocabulary is None else latest_vocabulary.dimensions)
        for value in dimension.values
        if value.status == "active"
    )
    updated_at = _latest_created_at(payload_rows)
    return KnowledgeSummary(
        source_count=source_count,
        published_card_count=published_card_count,
        review_task_count=review_task_count,
        retrieval_mode=(
            "fts-degraded" if latest_snapshot is None else latest_snapshot.retrieval_mode
        ),
        index_snapshot_id=(
            None if latest_snapshot is None else latest_snapshot.index_snapshot_id
        ),
        index_snapshot_digest=(
            None if latest_snapshot is None else latest_snapshot.snapshot_digest
        ),
        index_state="unavailable" if latest_snapshot is None else latest_snapshot.status,
        tag_labels=tag_labels,
        tag_options=tag_options,
        updated_at=updated_at,
    )


def _latest_created_at(payload_rows: tuple[str, ...]) -> datetime:
    values: list[datetime] = []
    for payload in payload_rows:
        created_at = json.loads(payload).get("created_at")
        if isinstance(created_at, str):
            values.append(datetime.fromisoformat(created_at.replace("Z", "+00:00")))
    return max(values) if values else datetime(1970, 1, 1, tzinfo=timezone.utc)
