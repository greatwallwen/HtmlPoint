from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

from openpyxl import Workbook
from pydantic import ValidationError
import pytest

from course_helper.artifacts import ArtifactStore
from course_helper.catalog import KnowledgeCatalog
from course_helper.chart_builder import (
    ChartSpec,
    build_dataset_charts,
    dataset_column_digest,
    dataset_schema_digest,
)
from course_helper.domain.common import SourceLocator
from course_helper.parsers.dataset_profiler import DatasetProfiler
from course_helper.source_roots import SourceRootRegistry


NOW = datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc)


def _profiler(root: Path) -> DatasetProfiler:
    return DatasetProfiler(SourceRootRegistry({"fixture": root}))


def _csv(root: Path, name: str, payload: str):
    path = root / name
    path.write_text(payload, encoding="utf-8")
    profiler = _profiler(root)
    dataset = profiler.profile_csv(
        SourceLocator(root_id="fixture", relative_path=name)
    )
    return path, profiler, dataset


def _spec(dataset, *, request_id: str = "chart-1", chart_type: str = "bar", aggregate: str = "sum", x: str = "category", y: str = "amount", max_rows: int = 50) -> ChartSpec:
    columns = {column.name: column for column in dataset.columns}
    return ChartSpec(
        request_id=request_id,
        chart_type=chart_type,
        dataset_version_id=dataset.version_id,
        expected_dataset_digest=dataset.content_digest,
        expected_schema_digest=dataset_schema_digest(dataset),
        x_column=x,
        x_column_digest=dataset_column_digest(columns[x]),
        y_column=y,
        y_column_digest=dataset_column_digest(columns[y]),
        aggregate=aggregate,
        title="Verified sales",
        description="Aggregated from the exact registered dataset.",
        max_result_rows=max_rows,
    )


def _build(tmp_path: Path, dataset, profiler: DatasetProfiler, *specs: ChartSpec, clock=lambda: NOW):
    catalog = KnowledgeCatalog.open(tmp_path / "catalog.sqlite3")
    catalog.insert_dataset(dataset)
    store = ArtifactStore(tmp_path / ".artifacts")
    outcomes = build_dataset_charts(
        catalog,
        profiler,
        store,
        tuple(specs),
        clock=clock,
    )
    return catalog, store, outcomes


def test_csv_bar_chart_is_deterministic_path_free_and_lineage_bound(tmp_path: Path) -> None:
    source, profiler, dataset = _csv(
        tmp_path,
        "sales.csv",
        "record_id,category,amount\n1,A,10\n2,A,5\n3,B,8\n",
    )
    before = (source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest())
    spec = _spec(dataset)
    catalog, store, first = _build(tmp_path, dataset, profiler, spec)
    second = build_dataset_charts(
        catalog,
        profiler,
        store,
        (spec,),
        clock=lambda: NOW + timedelta(days=1),
    )
    replay_with_new_request = build_dataset_charts(
        catalog,
        profiler,
        store,
        (spec.model_copy(update={"request_id": "chart-retry"}),),
        clock=lambda: NOW + timedelta(days=2),
    )

    assert first[0].status == second[0].status == "materialized"
    assert first[0].materialization == second[0].materialization
    assert second[0].reused is True
    assert replay_with_new_request[0].materialization is not None
    assert (
        replay_with_new_request[0].materialization.visual
        == first[0].materialization.visual
    )
    assert (
        replay_with_new_request[0].materialization.evidence
        == first[0].materialization.evidence
    )
    value = first[0].materialization
    assert value is not None
    assert value.artifact.media_type == "image/svg+xml"
    assert (value.artifact.width, value.artifact.height) == (960, 540)
    assert value.visual.authenticity == "data-derived"
    assert value.visual.derived_from_version_ids == (dataset.version_id,)
    assert store.verify(value.artifact)
    assert catalog.connection.execute("SELECT count(*) FROM artifact_metadata").fetchone()[0] == 1
    assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 2
    serialized = value.model_dump_json()
    assert str(tmp_path) not in serialized
    assert "SELECT " not in serialized
    assert "15" not in value.evidence.output_summary.values()
    assert before == (source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest())
    catalog.close()


def test_xlsx_line_and_csv_scatter_use_verified_duckdb_relations(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Metrics"
    sheet.append(["record_id", "period", "amount"])
    sheet.append([1, 1, 10])
    sheet.append([2, 2, 14])
    workbook.save(tmp_path / "metrics.xlsx")
    workbook.close()
    profiler = _profiler(tmp_path)
    dataset = profiler.profile_xlsx(
        SourceLocator(root_id="fixture", relative_path="metrics.xlsx"),
        sheet_name="Metrics",
    )
    assert dataset.relation_name == "Metrics"
    line = _spec(
        dataset,
        chart_type="line",
        x="period",
        y="amount",
        aggregate="avg",
    )
    catalog, _, outcome = _build(tmp_path, dataset, profiler, line)
    assert outcome[0].status == "materialized"
    catalog.close()

    scatter_root = tmp_path / "scatter"
    scatter_root.mkdir()
    _, scatter_profiler, scatter_dataset = _csv(
        scatter_root,
        "points.csv",
        "record_id,x_value,y_value\n1,1,2\n2,3,5\n",
    )
    scatter = _spec(
        scatter_dataset,
        chart_type="scatter",
        aggregate="none",
        x="x_value",
        y="y_value",
    )
    scatter_catalog, _, scatter_outcome = _build(
        scatter_root, scatter_dataset, scatter_profiler, scatter
    )
    assert scatter_outcome[0].status == "materialized"
    scatter_catalog.close()


def test_sensitive_dataset_and_schema_or_column_drift_fail_without_artifact(tmp_path: Path) -> None:
    _, profiler, dataset = _csv(
        tmp_path,
        "people.csv",
        "record_id,category,amount,email\n1,A,10,a@example.com\n",
    )
    spec = _spec(dataset)
    catalog, _, outcomes = _build(tmp_path, dataset, profiler, spec)
    assert outcomes[0].status == "failed"
    assert outcomes[0].error_code == "SENSITIVE_DATASET"
    assert catalog.connection.execute("SELECT count(*) FROM artifact_metadata").fetchone()[0] == 0
    catalog.close()

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    _, clean_profiler, clean_dataset = _csv(
        clean_root,
        "sales.csv",
        "record_id,category,amount\n1,A,10\n",
    )
    valid = _spec(clean_dataset)
    invalid_schema = valid.model_copy(update={"request_id": "bad-schema", "expected_schema_digest": "0" * 64})
    invalid_column = valid.model_copy(update={"request_id": "bad-column", "y_column_digest": "1" * 64})
    clean_catalog, _, failed = _build(
        clean_root, clean_dataset, clean_profiler, invalid_schema, invalid_column
    )
    assert [item.error_code for item in failed] == ["SCHEMA_DRIFT", "COLUMN_DRIFT"]
    assert clean_catalog.connection.execute("SELECT count(*) FROM artifact_metadata").fetchone()[0] == 0
    clean_catalog.close()


def test_result_and_source_row_ceilings_preserve_valid_siblings(tmp_path: Path) -> None:
    _, profiler, dataset = _csv(
        tmp_path,
        "sales.csv",
        "record_id,category,amount\n1,A,1\n2,B,2\n3,C,3\n",
    )
    too_small = _spec(dataset, request_id="small", max_rows=2)
    valid = _spec(dataset, request_id="valid", max_rows=3)
    catalog, _, outcomes = _build(tmp_path, dataset, profiler, too_small, valid)
    assert [(item.request_id, item.status) for item in outcomes] == [
        ("small", "failed"),
        ("valid", "materialized"),
    ]
    assert outcomes[0].error_code == "RESULT_LIMIT"
    row_limited = build_dataset_charts(
        catalog,
        profiler,
        ArtifactStore(tmp_path / "row-limit"),
        (valid.model_copy(update={"request_id": "row-limit"}),),
        clock=lambda: NOW,
        max_source_rows=2,
    )
    assert row_limited[0].status == "failed"
    assert row_limited[0].error_code == "DATASET_INVALID"
    catalog.close()


def test_source_digest_change_and_deterministic_time_ceiling_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, profiler, dataset = _csv(
        tmp_path,
        "sales.csv",
        "record_id,category,amount\n1,A,1\n",
    )
    spec = _spec(dataset)
    catalog = KnowledgeCatalog.open(tmp_path / "catalog.sqlite3")
    catalog.insert_dataset(dataset)
    source.write_text("record_id,category,amount\n1,A,2\n", encoding="utf-8")
    changed = build_dataset_charts(
        catalog,
        profiler,
        ArtifactStore(tmp_path / "changed"),
        (spec,),
        clock=lambda: NOW,
    )
    assert changed[0].status == "failed"
    assert changed[0].error_code == "DATASET_INVALID"
    catalog.close()

    time_root = tmp_path / "timeout"
    time_root.mkdir()
    _, time_profiler, time_dataset = _csv(
        time_root,
        "sales.csv",
        "record_id,category,amount\n1,A,1\n",
    )
    time_catalog = KnowledgeCatalog.open(time_root / "catalog.sqlite3")
    time_catalog.insert_dataset(time_dataset)
    ticks = iter((0.0, 10.0))
    monkeypatch.setattr("course_helper.chart_builder.monotonic", lambda: next(ticks))
    timed = build_dataset_charts(
        time_catalog,
        time_profiler,
        ArtifactStore(time_root / ".artifacts"),
        (_spec(time_dataset),),
        clock=lambda: NOW,
        query_timeout_seconds=1.0,
    )
    assert timed[0].error_code == "QUERY_TIMEOUT"
    time_catalog.close()


def test_contract_rejects_sql_screenshot_markup_and_invalid_chart_combinations(tmp_path: Path) -> None:
    _, _, dataset = _csv(
        tmp_path,
        "sales.csv",
        "record_id,category,amount\n1,A,1\n",
    )
    payload = _spec(dataset).model_dump(mode="python")
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**payload, "sql": "SELECT * FROM secret"})
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**payload, "screenshotUrl": "https://example.test/fake.png"})
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**payload, "title": "<script>alert(1)</script>"})
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({**payload, "chart_type": "scatter", "aggregate": "sum"})


def test_generated_svg_seam_rejects_active_or_external_content(tmp_path: Path) -> None:
    from course_helper.artifacts import ArtifactValidationError

    store = ArtifactStore(tmp_path / ".artifacts")
    active = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        'viewBox="0 0 10 10" role="img" aria-labelledby="t d">'
        '<title id="t">T</title><desc id="d">D</desc>'
        '<script>alert(1)</script></svg>'
    ).encode()
    external = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        'viewBox="0 0 10 10" role="img" aria-labelledby="t d">'
        '<title id="t">T</title><desc id="d">D</desc>'
        '<rect x="0" y="0" width="10" height="10" fill="url(https://bad.test/x)"/>'
        '</svg>'
    ).encode()
    with pytest.raises(ArtifactValidationError):
        store.put_generated_svg(active, clock=lambda: NOW)
    with pytest.raises(ArtifactValidationError):
        store.put_generated_svg(external, clock=lambda: NOW)
    assert not any(path.is_file() for path in (tmp_path / ".artifacts").rglob("*"))


def test_xlsx_type_metadata_cannot_become_sql(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Metrics"
    sheet.append(["record_id", "category", "amount"])
    sheet.append([1, "A", 10])
    workbook.save(tmp_path / "metrics.xlsx")
    workbook.close()
    profiler = _profiler(tmp_path)
    dataset = profiler.profile_xlsx(
        SourceLocator(root_id="fixture", relative_path="metrics.xlsx")
    )
    forged_columns = tuple(
        column.model_copy(
            update={"data_type": "BIGINT); DROP TABLE artifact_metadata; --"}
        )
        if column.name == "amount"
        else column
        for column in dataset.columns
    )
    forged = dataset.model_copy(update={"columns": forged_columns})
    spec = _spec(forged)

    catalog, _, outcome = _build(tmp_path, forged, profiler, spec)

    assert outcome[0].status == "failed"
    assert outcome[0].error_code == "DATASET_INVALID"
    assert catalog.connection.execute("SELECT count(*) FROM artifact_metadata").fetchone()[0] == 0
    catalog.close()
