"""Deterministic evidence-backed charts over verified local dataset relations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import math
import sqlite3
import threading
from time import monotonic
from typing import Any, Callable, Literal
from uuid import uuid5
from xml.sax.saxutils import escape

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from course_helper.artifacts import ArtifactError, ArtifactMetadata, ArtifactStore
from course_helper.catalog import (
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.composition import canonical_digest
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject, LineageEdge
from course_helper.domain.sources import DatasetAssetVersion, DatasetColumn, VisualAssetVersion
from course_helper.parsers.dataset_profiler import DatasetProfiler, VerifiedDatasetRelation
from course_helper.source_roots import (
    COURSE_STUDIO_ID_NAMESPACE,
    candidate_logical_id,
    candidate_version_id,
)


Clock = Callable[[], datetime]
ChartType = Literal["bar", "line", "scatter"]
Aggregate = Literal["count", "sum", "avg", "min", "max", "none"]
PRODUCER = "course-helper/chart-builder"
PRODUCER_VERSION = "1"
_ACTOR = ActorRef(actor_type="service", actor_id=PRODUCER)
_WIDTH = 960
_HEIGHT = 540
_MAX_RESULT_ROWS = 100
_NUMERIC_TYPES = ("INT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "HUGEINT")


class ChartBuildError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class ChartSpec(BaseModel):
    """No-SQL chart request pinned to exact dataset and column metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    chart_type: ChartType
    dataset_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    x_column: str = Field(min_length=1, max_length=128)
    x_column_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    y_column: str = Field(min_length=1, max_length=128)
    y_column_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    aggregate: Aggregate
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=320)
    max_result_rows: int = Field(default=50, ge=1, le=_MAX_RESULT_ROWS)

    @field_validator("title", "description")
    @classmethod
    def inert_text(cls, value: str) -> str:
        lowered = value.casefold()
        if (
            any(character in value for character in "<>")
            or "javascript:" in lowered
            or "http://" in lowered
            or "https://" in lowered
            or any(ord(character) < 32 and character not in "\t\n\r" for character in value)
        ):
            raise ValueError("chart text cannot contain markup or external references")
        return value

    @model_validator(mode="after")
    def chart_contract(self) -> ChartSpec:
        if self.x_column == self.y_column:
            raise ValueError("chart axes must use distinct columns")
        if self.chart_type == "scatter" and self.aggregate != "none":
            raise ValueError("scatter charts require the none aggregate")
        if self.chart_type != "scatter" and self.aggregate == "none":
            raise ValueError("bar and line charts require an allowlisted aggregate")
        return self


class ChartMaterialization(BaseModel):
    """Path-free immutable output for one verified chart."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    request_id: str
    spec_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_row_count: int = Field(ge=1, le=_MAX_RESULT_ROWS)
    artifact: ArtifactMetadata
    visual: VisualAssetVersion
    evidence: EvidenceObject


@dataclass(frozen=True)
class ChartOutcome:
    request_id: str
    status: Literal["materialized", "failed"]
    materialization: ChartMaterialization | None = None
    reused: bool = False
    error_code: str | None = None
    message: str | None = None


def dataset_column_digest(column: DatasetColumn) -> str:
    return canonical_digest(column.model_dump(mode="json", exclude_none=False))


def dataset_schema_digest(dataset: DatasetAssetVersion) -> str:
    return canonical_digest(
        {
            "dataset_version_id": dataset.version_id,
            "dataset_content_digest": dataset.content_digest,
            "format": dataset.format,
            "relation_name": dataset.relation_name,
            "row_count": dataset.row_count,
            "columns": [
                {
                    "name": column.name,
                    "digest": dataset_column_digest(column),
                }
                for column in dataset.columns
            ],
        }
    )


def _dataset(catalog: KnowledgeCatalog, version_id: str) -> DatasetAssetVersion:
    row = catalog.connection.execute(
        "SELECT logical_id, revision, content_digest, payload_json "
        "FROM datasets WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise ChartBuildError("DATASET_NOT_FOUND", "Registered dataset was not found")
    try:
        dataset = DatasetAssetVersion.model_validate_json(row[3])
    except ValidationError as error:
        raise ChartBuildError(
            "DATASET_ENVELOPE_INVALID", "Registered dataset metadata is invalid"
        ) from error
    semantic_locator = f"{dataset.locator.root_id}:{dataset.locator.relative_path}"
    if dataset.format == "xlsx":
        if dataset.relation_name is None:
            raise ChartBuildError(
                "DATASET_ENVELOPE_INVALID", "Registered dataset relation is not pinned"
            )
        semantic_locator = json.dumps(
            {
                "root_id": dataset.locator.root_id,
                "relative_path": dataset.locator.relative_path,
                "relation_name": dataset.relation_name,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    expected_logical_id = candidate_logical_id("dataset", semantic_locator)
    expected_version_id = candidate_version_id(
        expected_logical_id, (), dataset.content_digest
    )
    evidence_input = dict(dataset.evidence.input_summary)
    evidence_output = dict(dataset.evidence.output_summary)
    profile_config_digest = evidence_input.get("profile_config_digest")
    expected_evidence_id = (
        str(
            uuid5(
                COURSE_STUDIO_ID_NAMESPACE,
                f"evidence\0dataset-profile\0{dataset.version_id}\0{profile_config_digest}",
            )
        )
        if isinstance(profile_config_digest, str)
        and len(profile_config_digest) == 64
        else None
    )
    if (
        canonical_model_json(dataset) != row[3]
        or (dataset.logical_id, dataset.revision, dataset.content_digest) != tuple(row[:3])
        or dataset.version_id != version_id
        or dataset.logical_id != expected_logical_id
        or dataset.version_id != expected_version_id
        or len({column.name for column in dataset.columns}) != len(dataset.columns)
        or dataset.evidence.kind != "dataset-profile"
        or dataset.evidence.subject_version_id != dataset.version_id
        or dataset.evidence.status not in {"verified", "warning", "degraded"}
        or dataset.evidence.evidence_id != expected_evidence_id
        or dataset.evidence.producer != "course-helper/dataset-profiler"
        or dataset.evidence.producer_version != "1"
        or dataset.created_by.actor_id != "course-helper/dataset-profiler"
        or evidence_input.get("source_locator")
        != dataset.locator.model_dump(mode="json")
        or evidence_output.get("row_count") != dataset.row_count
        or evidence_output.get("column_count") != len(dataset.columns)
    ):
        raise ChartBuildError(
            "DATASET_ENVELOPE_INVALID", "Registered dataset metadata is invalid"
        )
    return dataset


def _column(dataset: DatasetAssetVersion, name: str, digest: str) -> DatasetColumn:
    matches = tuple(column for column in dataset.columns if column.name == name)
    if len(matches) != 1 or dataset_column_digest(matches[0]) != digest:
        raise ChartBuildError("COLUMN_DRIFT", "Pinned dataset column metadata changed")
    if matches[0].sensitive_category is not None:
        raise ChartBuildError("SENSITIVE_FIELD", "Sensitive dataset fields cannot be charted")
    return matches[0]


def _is_numeric(data_type: str) -> bool:
    normalized = data_type.upper()
    return any(token in normalized for token in _NUMERIC_TYPES)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _query_plan(spec: ChartSpec) -> dict[str, Any]:
    return {
        "contract": "typed-chart-query-v1",
        "dataset_version_id": spec.dataset_version_id,
        "chart_type": spec.chart_type,
        "x_column": spec.x_column,
        "y_column": spec.y_column,
        "aggregate": spec.aggregate,
        "max_result_rows": spec.max_result_rows,
    }


def _query(
    connection: duckdb.DuckDBPyConnection,
    relation: VerifiedDatasetRelation,
    spec: ChartSpec,
    *,
    timeout_seconds: float,
) -> tuple[tuple[Any, Any], ...]:
    x = _quote_identifier(spec.x_column)
    y = _quote_identifier(spec.y_column)
    if spec.chart_type == "scatter":
        projection = f"{x} AS chart_x, {y} AS chart_y"
        suffix = f" WHERE {x} IS NOT NULL AND {y} IS NOT NULL ORDER BY 1, 2 LIMIT ?"
    else:
        aggregate = {
            "count": f"count({y})",
            "sum": f"sum({y})",
            "avg": f"avg({y})",
            "min": f"min({y})",
            "max": f"max({y})",
        }[spec.aggregate]
        projection = f"{x} AS chart_x, {aggregate} AS chart_y"
        suffix = f" WHERE {x} IS NOT NULL GROUP BY {x} ORDER BY 1 LIMIT ?"
    sql = f"SELECT {projection} FROM {relation.relation_sql}{suffix}"
    interrupted = threading.Event()

    def interrupt() -> None:
        interrupted.set()
        connection.interrupt()

    timer = threading.Timer(timeout_seconds, interrupt)
    timer.daemon = True
    started = monotonic()
    timer.start()
    try:
        rows = tuple(
            connection.execute(
                sql,
                [*relation.parameters, spec.max_result_rows + 1],
            ).fetchall()
        )
    except duckdb.Error as error:
        if interrupted.is_set():
            raise ChartBuildError("QUERY_TIMEOUT", "Chart query exceeded its time ceiling") from error
        raise ChartBuildError("QUERY_FAILED", "Chart query could not be executed") from error
    finally:
        timer.cancel()
    if interrupted.is_set() or monotonic() - started > timeout_seconds:
        raise ChartBuildError("QUERY_TIMEOUT", "Chart query exceeded its time ceiling")
    if len(rows) > spec.max_result_rows:
        raise ChartBuildError("RESULT_LIMIT", "Chart result exceeds its row ceiling")
    if not rows:
        raise ChartBuildError("NO_DATA", "Chart query returned no usable rows")
    return rows


def _json_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, bool)):
        return value
    if isinstance(value, str):
        if len(value) > 512:
            raise ChartBuildError("RESULT_VALUE_LIMIT", "Chart result value is oversized")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ChartBuildError("NON_FINITE_RESULT", "Chart result contains a non-finite value")
        return value
    if isinstance(value, Decimal):
        text = str(value)
        if len(text) > 512:
            raise ChartBuildError("RESULT_VALUE_LIMIT", "Chart result value is oversized")
        return text
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    text = str(value)
    if len(text) > 512:
        raise ChartBuildError("RESULT_VALUE_LIMIT", "Chart result value is oversized")
    return text


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ChartBuildError("NON_NUMERIC_RESULT", "Chart measure is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ChartBuildError("NON_NUMERIC_RESULT", "Chart measure is not numeric") from error
    if not math.isfinite(number):
        raise ChartBuildError("NON_FINITE_RESULT", "Chart result contains a non-finite value")
    return number


def _fmt(value: float) -> str:
    rounded = round(value, 3)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.3f}".rstrip("0").rstrip(".")


def _svg(spec: ChartSpec, rows: tuple[tuple[Any, Any], ...]) -> bytes:
    left, right, top, bottom = 72.0, 28.0, 82.0, 70.0
    plot_width = _WIDTH - left - right
    plot_height = _HEIGHT - top - bottom
    labels = tuple(str(_json_scalar(row[0])) for row in rows)
    values = tuple(_number(row[1]) for row in rows)
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" aria-labelledby="chart-title chart-desc">',
        f'<title id="chart-title">{escape(spec.title)}</title>',
        f'<desc id="chart-desc">{escape(spec.description)}</desc>',
        '<rect x="0" y="0" width="960" height="540" fill="#F8FAFC"/>',
        f'<text x="72" y="42" fill="#0F172A" font-size="24" font-family="Arial, sans-serif">{escape(spec.title)}</text>',
        f'<line x1="{_fmt(left)}" y1="{_fmt(top + plot_height)}" x2="{_fmt(left + plot_width)}" y2="{_fmt(top + plot_height)}" stroke="#94A3B8" stroke-width="1"/>',
        f'<line x1="{_fmt(left)}" y1="{_fmt(top)}" x2="{_fmt(left)}" y2="{_fmt(top + plot_height)}" stroke="#94A3B8" stroke-width="1"/>',
    ]
    if spec.chart_type == "scatter":
        x_values = tuple(_number(row[0]) for row in rows)
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(values), max(values)
        x_span = x_max - x_min or 1.0
        y_span = y_max - y_min or 1.0
        for x_value, y_value in zip(x_values, values, strict=True):
            x = left + (x_value - x_min) / x_span * plot_width
            y = top + plot_height - (y_value - y_min) / y_span * plot_height
            elements.append(
                f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="5" fill="#2563EB"/>'
            )
    else:
        low = min(0.0, min(values))
        high = max(0.0, max(values))
        span = high - low or 1.0
        baseline = top + (high / span) * plot_height
        step = plot_width / len(rows)
        points: list[str] = []
        for index, (label, value) in enumerate(zip(labels, values, strict=True)):
            center = left + step * (index + 0.5)
            y = top + (high - value) / span * plot_height
            if spec.chart_type == "bar":
                bar_width = max(2.0, step * 0.66)
                elements.append(
                    f'<rect x="{_fmt(center - bar_width / 2)}" y="{_fmt(min(y, baseline))}" width="{_fmt(bar_width)}" height="{_fmt(max(1.0, abs(baseline - y)))}" rx="3" fill="#2563EB"/>'
                )
            else:
                points.append(f"{_fmt(center)},{_fmt(y)}")
            if index < 12:
                elements.append(
                    f'<text x="{_fmt(center)}" y="{_fmt(top + plot_height + 24)}" fill="#475569" font-size="11" font-family="Arial, sans-serif" text-anchor="middle">{escape(label[:18])}</text>'
                )
        if spec.chart_type == "line":
            point_values = " ".join(points)
            elements.append(
                f'<polyline points="{point_values}" fill="none" stroke="#2563EB" stroke-width="3"/>'
            )
            for point in points:
                x, y = point.split(",", 1)
                elements.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#2563EB"/>')
    elements.append(
        f'<text x="{_fmt(_WIDTH / 2)}" y="520" fill="#334155" font-size="12" font-family="Arial, sans-serif" text-anchor="middle">{escape(spec.x_column)}</text>'
    )
    elements.append("</svg>")
    return "".join(elements).encode("utf-8")


def _build_one(
    catalog: KnowledgeCatalog,
    profiler: DatasetProfiler,
    artifact_store: ArtifactStore,
    spec: ChartSpec,
    *,
    clock: Clock,
    max_source_rows: int,
    max_source_bytes: int,
    query_timeout_seconds: float,
) -> ChartOutcome:
    dataset = _dataset(catalog, spec.dataset_version_id)
    if dataset.content_digest != spec.expected_dataset_digest:
        raise ChartBuildError("DATASET_DRIFT", "Pinned dataset content changed")
    schema_digest = dataset_schema_digest(dataset)
    if schema_digest != spec.expected_schema_digest:
        raise ChartBuildError("SCHEMA_DRIFT", "Pinned dataset schema changed")
    if any(column.sensitive_category is not None for column in dataset.columns):
        raise ChartBuildError("SENSITIVE_DATASET", "Datasets with sensitive fields cannot be charted")
    x_column = _column(dataset, spec.x_column, spec.x_column_digest)
    y_column = _column(dataset, spec.y_column, spec.y_column_digest)
    if spec.chart_type == "scatter" and not _is_numeric(x_column.data_type):
        raise ChartBuildError("INVALID_AXIS_TYPE", "Scatter axes must be numeric")
    if spec.aggregate != "count" and not _is_numeric(y_column.data_type):
        raise ChartBuildError("INVALID_AXIS_TYPE", "Chart measure must be numeric")

    execution_started = monotonic()
    with duckdb.connect(database=":memory:") as connection:
        relation = profiler.prepare_verified_chart_relation(
            connection,
            dataset,
            max_rows=max_source_rows,
            max_bytes=max_source_bytes,
        )
        remaining_seconds = query_timeout_seconds - (monotonic() - execution_started)
        if remaining_seconds <= 0:
            raise ChartBuildError("QUERY_TIMEOUT", "Chart execution exceeded its time ceiling")
        rows = _query(
            connection,
            relation,
            spec,
            timeout_seconds=remaining_seconds,
        )
    profiler.verify_dataset_source_digest(dataset)
    if monotonic() - execution_started > query_timeout_seconds:
        raise ChartBuildError("QUERY_TIMEOUT", "Chart execution exceeded its time ceiling")

    normalized_rows = tuple(
        (_json_scalar(row[0]), _json_scalar(row[1])) for row in rows
    )
    spec_digest = canonical_digest(
        spec.model_dump(mode="json", exclude={"request_id"})
    )
    query_digest = canonical_digest(_query_plan(spec))
    result_digest = canonical_digest({"rows": normalized_rows})
    svg = _svg(spec, rows)
    svg_digest = hashlib.sha256(svg).hexdigest()
    write = artifact_store.put_generated_svg(
        svg,
        clock=clock,
        expected_digest=svg_digest,
    )
    artifact = write.metadata
    stored_artifact = catalog.get_artifact(artifact.artifact_id)
    metadata_reused = False
    if stored_artifact is not None:
        candidate = stored_artifact.payload
        if (
            candidate.content_digest,
            candidate.byte_size,
            candidate.media_type,
            candidate.width,
            candidate.height,
        ) != (
            artifact.content_digest,
            artifact.byte_size,
            artifact.media_type,
            artifact.width,
            artifact.height,
        ):
            raise ChartBuildError(
                "ARTIFACT_METADATA_MISMATCH", "Stored chart artifact metadata changed"
            )
        artifact = candidate
        metadata_reused = True
    artifact_store.verify(artifact)

    visual_logical_id = candidate_logical_id(
        "visual", f"{dataset.logical_id}:chart:{spec_digest}"
    )
    visual_version_id = candidate_version_id(
        visual_logical_id,
        (dataset.version_id,),
        artifact.content_digest,
    )
    visual = VisualAssetVersion(
        logical_id=visual_logical_id,
        version_id=visual_version_id,
        revision=1,
        content_digest=artifact.content_digest,
        created_at=artifact.created_at,
        created_by=_ACTOR,
        media_type="image/svg+xml",
        width=artifact.width,
        height=artifact.height,
        alt_text=spec.description,
        license_status="generated",
        authenticity="data-derived",
        derived_from_version_ids=(dataset.version_id,),
        usage_scope=("private-training", "internal", "public"),
    )
    evidence_semantics = {
        "dataset_version_id": dataset.version_id,
        "dataset_content_digest": dataset.content_digest,
        "schema_digest": schema_digest,
        "spec_digest": spec_digest,
        "query_digest": query_digest,
        "result_digest": result_digest,
        "result_row_count": len(rows),
        "artifact_id": artifact.artifact_id,
        "artifact_digest": artifact.content_digest,
        "visual_version_id": visual.version_id,
    }
    evidence = EvidenceObject(
        evidence_id="chart-evidence-" + canonical_digest(evidence_semantics),
        kind="execution",
        subject_version_id=visual.version_id,
        status="verified",
        input_summary={
            "dataset_version_id": dataset.version_id,
            "dataset_content_digest": dataset.content_digest,
            "schema_digest": schema_digest,
            "x_column_digest": spec.x_column_digest,
            "y_column_digest": spec.y_column_digest,
            "spec_digest": spec_digest,
            "query_digest": query_digest,
        },
        output_summary={
            "artifact_id": artifact.artifact_id,
            "artifact_digest": artifact.content_digest,
            "visual_version_id": visual.version_id,
            "result_digest": result_digest,
            "result_row_count": len(rows),
            "media_type": artifact.media_type,
            "width": artifact.width,
            "height": artifact.height,
        },
        producer=PRODUCER,
        producer_version=PRODUCER_VERSION,
        started_at=artifact.created_at,
        finished_at=artifact.created_at,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="dataset-pinned",
                status="passed",
                message="Exact dataset bytes and schema were verified",
                details={"row_count": dataset.row_count},
            ),
            EvidenceCheck(
                code="typed-query",
                status="passed",
                message="Only a typed allowlisted chart query was executed",
                details={"chart_type": spec.chart_type, "aggregate": spec.aggregate},
            ),
            EvidenceCheck(
                code="result-bounded",
                status="passed",
                message="Chart result stayed within the configured ceiling",
                details={"result_row_count": len(rows), "result_limit": spec.max_result_rows},
            ),
            EvidenceCheck(
                code="svg-validated",
                status="passed",
                message="Generated SVG passed the inert element and attribute allowlist",
                details={"artifact_digest": artifact.content_digest},
            ),
        ),
    )
    with catalog.atomic_write():
        catalog.register_artifact(artifact)
        catalog.insert_visual(visual)
        catalog.insert_evidence(evidence)
        for label, from_id, to_id in (
            ("artifact", artifact.artifact_id, visual.version_id),
            ("dataset", visual.version_id, dataset.version_id),
        ):
            catalog.insert_lineage(
                LineageEdge(
                    edge_id="chart-lineage-"
                    + canonical_digest(
                        {
                            "label": label,
                            "from": from_id,
                            "to": to_id,
                            "evidence_id": evidence.evidence_id,
                        }
                    ),
                    from_version_id=from_id,
                    to_version_id=to_id,
                    relation="derived_from",
                    evidence_id=evidence.evidence_id,
                    created_at=artifact.created_at,
                )
            )
    return ChartOutcome(
        request_id=spec.request_id,
        status="materialized",
        materialization=ChartMaterialization(
            request_id=spec.request_id,
            spec_digest=spec_digest,
            query_digest=query_digest,
            result_digest=result_digest,
            result_row_count=len(rows),
            artifact=artifact,
            visual=visual,
            evidence=evidence,
        ),
        reused=write.reused or metadata_reused,
    )


def build_dataset_charts(
    catalog: KnowledgeCatalog,
    profiler: DatasetProfiler,
    artifact_store: ArtifactStore,
    specs: tuple[ChartSpec, ...],
    *,
    clock: Clock,
    max_source_rows: int = 100_000,
    max_source_bytes: int = 32 * 1024 * 1024,
    query_timeout_seconds: float = 5.0,
) -> tuple[ChartOutcome, ...]:
    """Build each chart independently and return only sanitized failures."""

    if len({spec.request_id for spec in specs}) != len(specs):
        raise ValueError("chart request IDs must be unique")
    if max_source_rows < 1 or max_source_bytes < 1 or query_timeout_seconds <= 0:
        raise ValueError("chart execution ceilings must be positive")
    outcomes: list[ChartOutcome] = []
    for spec in specs:
        try:
            outcomes.append(
                _build_one(
                    catalog,
                    profiler,
                    artifact_store,
                    spec,
                    clock=clock,
                    max_source_rows=max_source_rows,
                    max_source_bytes=max_source_bytes,
                    query_timeout_seconds=query_timeout_seconds,
                )
            )
        except ChartBuildError as error:
            outcomes.append(
                ChartOutcome(
                    request_id=spec.request_id,
                    status="failed",
                    error_code=error.code,
                    message=error.safe_message,
                )
            )
        except ArtifactError:
            outcomes.append(
                ChartOutcome(
                    request_id=spec.request_id,
                    status="failed",
                    error_code="ARTIFACT_REJECTED",
                    message="Generated chart artifact was rejected",
                )
            )
        except (CatalogReferenceError, ImmutableVersionConflict, sqlite3.Error):
            outcomes.append(
                ChartOutcome(
                    request_id=spec.request_id,
                    status="failed",
                    error_code="CATALOG_REJECTED",
                    message="Chart catalog registration was rejected",
                )
            )
        except (ValueError, duckdb.Error):
            outcomes.append(
                ChartOutcome(
                    request_id=spec.request_id,
                    status="failed",
                    error_code="DATASET_INVALID",
                    message="Dataset verification or chart execution failed",
                )
            )
    return tuple(outcomes)


__all__ = [
    "ChartMaterialization",
    "ChartOutcome",
    "ChartSpec",
    "build_dataset_charts",
    "dataset_column_digest",
    "dataset_schema_digest",
]
