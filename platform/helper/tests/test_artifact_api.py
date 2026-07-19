from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from course_helper.api import HelperRuntime, create_app
from course_helper.artifacts import ArtifactStore
from course_helper.catalog import KnowledgeCatalog
from course_helper.chart_builder import dataset_column_digest, dataset_schema_digest
from course_helper.domain.common import SourceLocator
from course_helper.jobs import (
    ChartBuildJob,
    JobSpec,
    WorkerRuntimeConfig,
    _run_chart_build,
    chart_build_request_digest,
)
from course_helper.parsers.dataset_profiler import DatasetProfiler
from course_helper.session import LaunchSession
from course_helper.source_roots import SourceRootRegistry


NOW = datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)
SESSION = "session-" + "d" * 64
ACTOR = {"actorType": "human", "actorId": "chart-author"}
ORIGIN = "http://127.0.0.1:4173"


def _prepared_chart_job(tmp_path: Path) -> tuple[WorkerRuntimeConfig, ChartBuildJob]:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    (source_root / "sales.csv").write_text(
        "record_id,category,amount\n1,A,10\n2,A,5\n3,B,8\n",
        encoding="utf-8",
    )
    profiler = DatasetProfiler(SourceRootRegistry({"fixture": source_root}))
    dataset = profiler.profile_csv(
        SourceLocator(root_id="fixture", relative_path="sales.csv")
    )
    database = tmp_path / "knowledge.db"
    with KnowledgeCatalog.open(database) as catalog:
        catalog.insert_dataset(dataset)
    columns = {column.name: column for column in dataset.columns}
    spec = {
        "requestId": "chart-api-1",
        "chartType": "bar",
        "datasetVersionId": dataset.version_id,
        "expectedDatasetDigest": dataset.content_digest,
        "expectedSchemaDigest": dataset_schema_digest(dataset),
        "xColumn": "category",
        "xColumnDigest": dataset_column_digest(columns["category"]),
        "yColumn": "amount",
        "yColumnDigest": dataset_column_digest(columns["amount"]),
        "aggregate": "sum",
        "title": "Verified sales",
        "description": "Aggregated from the exact registered dataset.",
        "maxResultRows": 50,
    }
    payload = {
        "type": "chart_build",
        "specs": [spec],
        "operationId": "http-chart-build-1",
        "requestDigest": chart_build_request_digest((spec,)),
        "actor": ACTOR,
    }
    return (
        WorkerRuntimeConfig(
            database_path=str(database),
            app_data_path=str(tmp_path / "app-data"),
            source_roots=(("fixture", str(source_root)),),
        ),
        ChartBuildJob.model_validate(payload),
    )


def _client(config: WorkerRuntimeConfig) -> tuple[TestClient, dict[str, str]]:
    launch = LaunchSession.create(allowed_origin=ORIGIN)

    class UnusedRunner:
        async def run(self, *args, **kwargs):
            raise AssertionError("artifact GET must not dispatch a job")

    client = TestClient(
        create_app(
            HelperRuntime(
                config=config,
                launch_session=launch,
                job_runner=UnusedRunner(),
            )
        )
    )
    exchanged = client.post(
        "/v1/session/exchange",
        headers={"Origin": ORIGIN},
        json={"nonce": launch.launch_nonce},
    )
    assert exchanged.status_code == 200
    return client, {
        "Origin": ORIGIN,
        "X-Course-Session": exchanged.json()["sessionToken"],
    }


def test_chart_job_contract_is_strict_digest_bound_and_contains_no_sql() -> None:
    spec = {
        "requestId": "chart-contract-1",
        "chartType": "bar",
        "datasetVersionId": "dataset-v1",
        "expectedDatasetDigest": "1" * 64,
        "expectedSchemaDigest": "2" * 64,
        "xColumn": "category",
        "xColumnDigest": "3" * 64,
        "yColumn": "amount",
        "yColumnDigest": "4" * 64,
        "aggregate": "sum",
        "title": "Verified chart",
        "description": "Bounded typed chart request.",
        "maxResultRows": 20,
    }
    payload = {
        "type": "chart_build",
        "specs": [spec],
        "operationId": "chart-contract-operation",
        "requestDigest": chart_build_request_digest((spec,)),
        "actor": ACTOR,
    }
    parsed = TypeAdapter(JobSpec).validate_python(payload)
    assert isinstance(parsed, ChartBuildJob)
    assert parsed.model_dump(mode="json", by_alias=True) == payload
    assert "sql" not in str(payload).casefold()
    for invalid in (
        {**payload, "requestDigest": "f" * 64},
        {**payload, "sql": "SELECT * FROM data"},
        {**payload, "specs": [{**spec, "title": "<script>"}]},
    ):
        try:
            TypeAdapter(JobSpec).validate_python(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("unsafe chart contract was accepted")


def test_chart_build_and_authenticated_artifact_get_are_exact_and_path_free(
    tmp_path: Path,
) -> None:
    config, job = _prepared_chart_job(tmp_path)
    result, _ = _run_chart_build(job, config, SESSION)
    replay, _ = _run_chart_build(job, config, SESSION)
    assert replay == result
    assert result["operationStatus"] == "committed"
    assert result["items"][0]["status"] == "materialized"
    artifact_id = result["items"][0]["artifactId"]

    client, headers = _client(config)
    assert client.get(f"/v1/artifacts/{artifact_id}").status_code == 401
    response = client.get(f"/v1/artifacts/{artifact_id}", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"<svg")
    serialized = response.text + str(result)
    assert str(tmp_path) not in serialized
    assert headers["X-Course-Session"] not in serialized
    assert "SELECT " not in serialized


def test_artifact_get_returns_same_safe_404_for_missing_corrupt_and_unbound_svg(
    tmp_path: Path,
) -> None:
    config, _job = _prepared_chart_job(tmp_path)
    store = ArtifactStore(Path(config.app_data_path) / "artifacts")
    unbound = store.put_generated_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10" role="img" aria-labelledby="title"><title id="title">Unbound</title><rect x="0" y="0" width="10" height="10"/></svg>',
        clock=lambda: NOW,
    ).metadata
    with KnowledgeCatalog.open(config.database_path) as catalog:
        catalog.register_artifact(unbound)
    client, headers = _client(config)
    missing_id = "artifact-" + hashlib.sha256(b"missing").hexdigest()
    missing = client.get(f"/v1/artifacts/{missing_id}", headers=headers)
    unbound_response = client.get(
        f"/v1/artifacts/{unbound.artifact_id}", headers=headers
    )
    object_path = (
        Path(config.app_data_path)
        / "artifacts"
        / "objects"
        / unbound.content_digest[:2]
        / unbound.content_digest[2:4]
        / unbound.content_digest
    )
    object_path.write_bytes(b"corrupt")
    corrupt = client.get(f"/v1/artifacts/{unbound.artifact_id}", headers=headers)
    for response in (missing, unbound_response, corrupt):
        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "artifact-not-found",
                "message": "Artifact is unavailable",
            }
        }
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert str(tmp_path) not in response.text
        assert headers["X-Course-Session"] not in response.text
