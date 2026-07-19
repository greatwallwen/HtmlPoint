from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from course_helper.catalog import CatalogReferenceError, KnowledgeCatalog
from course_helper.domain.common import ActorRef
from course_helper.jobs import (
    BoundedJobRunner,
    JobSpec,
    KnowledgeCardPublishJob,
    KnowledgeImportCancelJob,
    KnowledgeImportStartJob,
    KnowledgeImportStatusJob,
    KnowledgeReviewDetailJob,
    KnowledgeReviewListJob,
    KnowledgeReviewResolveJob,
    KnowledgeUpgradeListJob,
    KnowledgeUpgradeResolveJob,
    OperationStatusJob,
    WorkerRuntimeConfig,
    _run_knowledge_import_cancel,
    _run_knowledge_import_start,
    _run_knowledge_import_status,
    _run_knowledge_review_detail,
    _run_knowledge_review_list,
    _run_knowledge_review_resolve,
    _run_knowledge_card_publish,
    _run_knowledge_upgrade_list,
    _run_knowledge_upgrade_resolve,
    _run_operation_status,
    card_publish_request_digest,
    review_resolution_request_digest,
    upgrade_resolution_request_digest,
)
from course_helper.operations import OperationAuthenticationError, OperationRequest
from course_helper.reviews import (
    ReviewProjectionError,
    ReviewQueryError,
    UpgradeSuggestion,
    get_review_detail,
    list_review_tasks,
    list_upgrade_suggestions,
    register_upgrade_suggestion,
)
from course_helper.uploads import (
    UploadError,
    UploadStore,
    import_cancel_request_digest,
    import_start_request_digest,
)


NOW = datetime.now(timezone.utc)
SESSION = "session-" + "a" * 64
ACTOR = {"actorType": "human", "actorId": "import-author"}


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "knowledge.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
    )


def _create_upload(
    tmp_path: Path,
    *,
    content: bytes = b"# Imported source\n",
    file_name: str = "imported.md",
    media_type: str = "text/markdown",
):
    config = _config(tmp_path)
    with KnowledgeCatalog.open(Path(config.database_path)) as catalog:
        return UploadStore(catalog, Path(config.app_data_path)).create_upload(
            (content,),
            file_name=file_name,
            media_type=media_type,
            byte_size_hint=len(content),
            session_id=SESSION,
            clock=lambda: NOW,
        )


def _start_job(upload) -> KnowledgeImportStartJob:
    digest = import_start_request_digest(upload.upload_id, upload.content_digest)
    return KnowledgeImportStartJob.model_validate(
        {
            "type": "knowledge_import_start",
            "uploadId": upload.upload_id,
            "expectedContentDigest": upload.content_digest,
            "operationId": "http-import-start-1",
            "requestDigest": digest,
            "actor": ACTOR,
        }
    )


def test_import_and_operation_job_schemas_are_strict_lower_camel_and_digest_bound() -> None:
    upload_id = "upload-" + "1" * 32
    content_digest = "2" * 64
    start_digest = import_start_request_digest(upload_id, content_digest)
    cancel_digest = import_cancel_request_digest("import-" + "3" * 32)
    payloads = (
        {
            "type": "knowledge_import_start",
            "uploadId": upload_id,
            "expectedContentDigest": content_digest,
            "operationId": "operation-start",
            "requestDigest": start_digest,
            "actor": ACTOR,
        },
        {
            "type": "knowledge_import_status",
            "importId": "import-" + "3" * 32,
            "actor": ACTOR,
        },
        {
            "type": "knowledge_import_cancel",
            "importId": "import-" + "3" * 32,
            "operationId": "operation-cancel",
            "requestDigest": cancel_digest,
            "actor": ACTOR,
        },
        {
            "type": "operation_status",
            "operationId": "operation-start",
            "actor": ACTOR,
        },
    )
    adapter = TypeAdapter(JobSpec)

    parsed = tuple(adapter.validate_python(payload) for payload in payloads)
    assert isinstance(parsed[0], KnowledgeImportStartJob)
    assert isinstance(parsed[1], KnowledgeImportStatusJob)
    assert isinstance(parsed[2], KnowledgeImportCancelJob)
    assert isinstance(parsed[3], OperationStatusJob)
    assert all(
        value.model_dump(mode="json", by_alias=True) == payload
        for value, payload in zip(parsed, payloads, strict=True)
    )
    for invalid in (
        {**payloads[0], "sourcePath": "C:/private/source.md"},
        {**payloads[0], "requestDigest": "f" * 64},
        {**payloads[0], "sessionId": SESSION},
        {**payloads[1], "import_id": payloads[1]["importId"]},
        {**payloads[2], "requestDigest": "e" * 64},
        {**payloads[3], "requestDigest": "d" * 64},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_import_start_promotes_exact_upload_and_replays_without_duplicate_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from course_helper import jobs as jobs_module

    upload = _create_upload(tmp_path)
    job = _start_job(upload)
    config = _config(tmp_path)

    first_result, first_evidence = _run_knowledge_import_start(job, config, SESSION)
    monkeypatch.setattr(
        jobs_module,
        "parse_promoted_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("committed replay must not reparse")
        ),
    )
    second_result, second_evidence = _run_knowledge_import_start(job, config, SESSION)

    assert second_result == first_result
    assert first_result["status"] == "promoted"
    assert first_result["importId"].startswith("import-")
    assert len(first_result["sourceVersionId"]) == 36
    assert first_result["chunkCount"] >= 1
    assert first_result["candidateCardVersionIds"]
    assert first_result["reviewTaskIds"]
    serialized = (
        str(first_result)
        + first_evidence.model_dump_json()
        + second_evidence.model_dump_json()
    )
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
    assert "relative_path" not in serialized
    with KnowledgeCatalog.open(Path(config.database_path)) as catalog:
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM import_leases WHERE state = 'promoted'"
        ).fetchone()[0] == 1
        assert catalog.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] >= 1
        assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] == len(
            first_result["candidateCardVersionIds"]
        )
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_tasks"
        ).fetchone()[0] == len(first_result["reviewTaskIds"])


def test_import_status_and_operation_status_are_authenticated_and_path_free(
    tmp_path: Path,
) -> None:
    upload = _create_upload(tmp_path)
    start_job = _start_job(upload)
    config = _config(tmp_path)
    result, _evidence = _run_knowledge_import_start(start_job, config, SESSION)
    import_id = result["importId"]

    status_result, status_evidence = _run_knowledge_import_status(
        KnowledgeImportStatusJob.model_validate(
            {"type": "knowledge_import_status", "importId": import_id, "actor": ACTOR}
        ),
        config,
        SESSION,
    )
    operation_result, operation_evidence = _run_operation_status(
        OperationStatusJob.model_validate(
            {
                "type": "operation_status",
                "operationId": start_job.operation_id,
                "actor": ACTOR,
            }
        ),
        config,
        SESSION,
    )

    assert status_result["status"] == "promoted"
    assert status_result["sourceVersionId"] == result["sourceVersionId"]
    assert operation_result["status"] == "committed"
    assert operation_result["resultRefs"]["importId"] == import_id
    serialized = (
        str(status_result)
        + str(operation_result)
        + status_evidence.model_dump_json()
        + operation_evidence.model_dump_json()
    )
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
    with pytest.raises(UploadError):
        _run_knowledge_import_status(
            KnowledgeImportStatusJob.model_validate(
                {
                    "type": "knowledge_import_status",
                    "importId": import_id,
                    "actor": {"actorType": "human", "actorId": "wrong-actor"},
                }
            ),
            config,
            SESSION,
        )


def test_import_cancel_is_ledgered_authenticated_and_idempotent(tmp_path: Path) -> None:
    upload = _create_upload(tmp_path)
    config = _config(tmp_path)
    actor = ActorRef(actor_type="human", actor_id="import-author")
    start_digest = import_start_request_digest(upload.upload_id, upload.content_digest)
    with KnowledgeCatalog.open(Path(config.database_path)) as catalog:
        store = UploadStore(catalog, Path(config.app_data_path))
        started = store.start_import(
            OperationRequest(
                operation_id="cancel-fixture-start",
                request_digest=start_digest,
                actor=actor,
                session_id=SESSION,
            ),
            upload_id=upload.upload_id,
            expected_content_digest=upload.content_digest,
            clock=lambda: NOW,
        )
    import_id = str(started.result_refs["importId"])
    cancel_digest = import_cancel_request_digest(import_id)
    job = KnowledgeImportCancelJob.model_validate(
        {
            "type": "knowledge_import_cancel",
            "importId": import_id,
            "operationId": "cancel-fixture-operation",
            "requestDigest": cancel_digest,
            "actor": ACTOR,
        }
    )

    first, _evidence = _run_knowledge_import_cancel(job, config, SESSION)
    second, _replay_evidence = _run_knowledge_import_cancel(job, config, SESSION)

    assert second == first
    assert first == {
        "operationId": job.operation_id,
        "status": "committed",
        "requestDigest": job.request_digest,
        "resultRefs": {"importId": import_id, "status": "cancelled"},
    }
    with KnowledgeCatalog.open(Path(config.database_path)) as catalog:
        assert UploadStore(catalog, Path(config.app_data_path)).import_status(
            import_id, session_id=SESSION, actor=actor
        ).state == "cancelled"
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id = ?",
            (job.operation_id,),
        ).fetchone()[0] == 1


class _RecordingQueue:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, value: object) -> None:
        self.items.append(value)

    def get(self, timeout: float | None = None) -> object:
        return self.items.pop(0)

    def close(self) -> None:
        pass

    def join_thread(self) -> None:
        pass


class _DelayedResponseProcess:
    def __init__(self, target, args) -> None:
        self.target = target
        self.args = args
        self.started = False
        self.terminated = False
        self.joined = False
        self.exitcode: int | None = None

    @property
    def pid(self) -> int:
        return 1234

    def start(self) -> None:
        self.started = True
        self.target(*self.args)

    def is_alive(self) -> bool:
        return self.started and not self.terminated

    def terminate(self) -> None:
        self.terminated = True
        self.exitcode = -15

    def join(self) -> None:
        self.joined = True

    def close(self) -> None:
        pass


class _DelayedResponseContext:
    def __init__(self) -> None:
        self.queue = _RecordingQueue()
        self.process: _DelayedResponseProcess | None = None

    def Queue(self) -> _RecordingQueue:
        return self.queue

    def Process(self, *, target, args) -> _DelayedResponseProcess:
        self.process = _DelayedResponseProcess(target, args)
        return self.process


async def _disconnected() -> bool:
    return True


async def _connected() -> bool:
    return False


def test_parent_disconnect_recovers_committed_import_instead_of_false_cancel(
    tmp_path: Path,
) -> None:
    upload = _create_upload(tmp_path)
    job = _start_job(upload)
    context = _DelayedResponseContext()
    runner = BoundedJobRunner(
        _config(tmp_path), mp_context=context, poll_interval=0
    )

    outcome = asyncio.run(
        runner.run(job, disconnected=_disconnected, session_id=SESSION)
    )

    assert outcome.status_code == 200
    assert outcome.result["operationStatus"] == "committed"
    assert outcome.result["importId"].startswith("import-")
    assert outcome.result["candidateCardVersionIds"]
    assert outcome.result["reviewTaskIds"]
    assert outcome.evidence.checks[0].code == "operation-recovery"
    assert context.process is not None
    assert context.process.terminated is True
    assert context.process.joined is True
    serialized = outcome.evidence.model_dump_json() + str(outcome.result)
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized


def test_parent_timeout_recovers_committed_import_instead_of_false_timeout(
    tmp_path: Path,
) -> None:
    upload = _create_upload(tmp_path)
    job = _start_job(upload)
    context = _DelayedResponseContext()
    ticks = iter((0.0, 31.0))
    runner = BoundedJobRunner(
        _config(tmp_path),
        mp_context=context,
        monotonic=lambda: next(ticks),
        poll_interval=0,
    )

    outcome = asyncio.run(
        runner.run(job, disconnected=_connected, session_id=SESSION)
    )

    assert outcome.status_code == 200
    assert outcome.result["operationStatus"] == "committed"
    assert outcome.evidence.checks[0].code == "operation-recovery"
    assert context.process is not None and context.process.terminated is True
    assert outcome.evidence.output_summary["verification"] == (
        "committed-outcome-recovered"
    )


def test_real_spawn_import_uses_the_allowlisted_worker_and_promotes_source(
    tmp_path: Path,
) -> None:
    upload = _create_upload(tmp_path)
    runner = BoundedJobRunner(_config(tmp_path))

    outcome = asyncio.run(
        runner.run(_start_job(upload), disconnected=_connected, session_id=SESSION)
    )

    assert outcome.status_code == 200
    assert outcome.result["status"] == "promoted"
    assert outcome.result["candidateCardVersionIds"]
    assert outcome.result["reviewTaskIds"]
    assert outcome.evidence.status == "verified"
    serialized = outcome.evidence.model_dump_json() + str(outcome.result)
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 1
        assert catalog.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] >= 1
        assert catalog.connection.execute("SELECT count(*) FROM cards").fetchone()[0] >= 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_tasks"
        ).fetchone()[0] >= 1


def test_import_parse_failure_keeps_promoted_source_retryable_without_partial_rows(
    tmp_path: Path,
) -> None:
    upload = _create_upload(tmp_path, content=b"\xff\xfe\x00not-markdown")
    job = _start_job(upload)

    with pytest.raises(UnicodeDecodeError):
        _run_knowledge_import_start(job, _config(tmp_path), SESSION)

    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM governed_source_blobs"
        ).fetchone()[0] == 1
        assert catalog.connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        for table in ("chunks", "cards", "review_tasks"):
            assert catalog.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id = ?",
            (job.operation_id,),
        ).fetchone()[0] == 0
    work_root = Path(_config(tmp_path).app_data_path) / "import-work"
    assert not work_root.exists() or not any(work_root.iterdir())


def test_pptx_import_rebinds_chunks_visuals_and_candidates_to_promoted_source(
    tmp_path: Path,
) -> None:
    from test_pptx_parser import PNG_1X1, build_small_pptx

    fixture = build_small_pptx(
        tmp_path / "lesson.pptx",
        title="Traceable AI lesson",
        image_bytes=PNG_1X1,
    )
    upload = _create_upload(
        tmp_path,
        content=fixture.read_bytes(),
        file_name="lesson.pptx",
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )

    result, evidence = _run_knowledge_import_start(
        _start_job(upload), _config(tmp_path), SESSION
    )

    assert result["chunkCount"] == 1
    assert result["visualCount"] == 1
    assert result["candidateCardVersionIds"]
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        source_id = result["sourceVersionId"]
        assert catalog.connection.execute(
            "SELECT count(*) FROM chunks WHERE source_version_id = ?", (source_id,)
        ).fetchone()[0] == 1
        visual_payload = catalog.connection.execute(
            "SELECT payload_json FROM visuals"
        ).fetchone()[0]
        assert source_id in visual_payload
        assert "governed-import" not in visual_payload
        assert catalog.connection.execute(
            "SELECT count(*) FROM source_visual_artifacts"
        ).fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM artifact_metadata"
        ).fetchone()[0] == 1
        card_payload = catalog.connection.execute(
            "SELECT payload_json FROM cards WHERE version_id = ?",
            (result["candidateCardVersionIds"][0],),
        ).fetchone()[0]
        assert source_id in card_payload
    serialized = str(result) + evidence.model_dump_json()
    assert str(tmp_path) not in serialized
    assert "governed-import" not in serialized


def test_csv_import_profiles_bounded_dataset_and_replays_without_reprofiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import duckdb

    from course_helper import jobs as jobs_module
    from course_helper.chart_builder import _dataset as validated_chart_dataset
    from course_helper.domain.sources import DatasetAssetVersion
    from course_helper.parsers.dataset_profiler import DatasetProfiler
    from course_helper.source_roots import SourceRootRegistry

    rows = ["customer_id,segment,revenue"] + [
        f"{index},segment-{index % 3},{index * 10}" for index in range(40)
    ]
    upload = _create_upload(
        tmp_path,
        content=("\n".join(rows) + "\n").encode("utf-8"),
        file_name="customers.csv",
        media_type="text/csv",
    )
    job = _start_job(upload)

    first, evidence = _run_knowledge_import_start(job, _config(tmp_path), SESSION)
    monkeypatch.setattr(
        jobs_module,
        "profile_promoted_dataset",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("committed replay must not reprofile")
        ),
    )
    replay, _ = _run_knowledge_import_start(job, _config(tmp_path), SESSION)

    assert replay == first
    assert first["candidateCardVersionIds"] == []
    assert len(first["datasetVersionIds"]) == 1
    assert len(first["datasetProfiles"]) == 1
    profile = first["datasetProfiles"][0]
    assert profile["datasetVersionId"] == first["datasetVersionIds"][0]
    assert profile["rowCount"] == 40
    assert [column["name"] for column in profile["columns"]] == [
        "customer_id",
        "segment",
        "revenue",
    ]
    assert all(len(column["digest"]) == 64 for column in profile["columns"])
    assert len(profile["contentDigest"]) == len(profile["schemaDigest"]) == 64
    assert first["reviewTaskIds"]
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        raw = catalog.connection.execute(
            "SELECT payload_json FROM datasets WHERE version_id = ?",
            (first["datasetVersionIds"][0],),
        ).fetchone()[0]
        dataset = DatasetAssetVersion.model_validate_json(raw, strict=False)
        assert dataset.locator.root_id == "governed-upload"
        assert dataset.created_by.actor_id == "course-helper/dataset-profiler"
        assert validated_chart_dataset(catalog, dataset.version_id) == dataset
        profiler = DatasetProfiler(
            SourceRootRegistry(
                {"governed-upload": tmp_path / "app-data" / "source-blobs"}
            )
        )
        with duckdb.connect(database=":memory:") as connection:
            relation = profiler.prepare_verified_chart_relation(
                connection,
                dataset,
                max_rows=100,
                max_bytes=1024 * 1024,
            )
            assert relation.row_count == 40
        assert len(dataset.sample_rows) <= 20
        assert catalog.connection.execute(
            "SELECT count(*) FROM review_tasks WHERE subject_version_id = ?",
            (dataset.version_id,),
        ).fetchone()[0] >= 1
    serialized = str(first) + evidence.model_dump_json()
    assert str(tmp_path) not in serialized
    assert "governed-import" not in serialized


def test_spawned_status_and_operation_recovery_reject_wrong_authority_safely(
    tmp_path: Path,
) -> None:
    upload = _create_upload(tmp_path)
    config = _config(tmp_path)
    result, _evidence = _run_knowledge_import_start(_start_job(upload), config, SESSION)
    runner = BoundedJobRunner(config)
    wrong_session = "session-" + "b" * 64
    status_job = KnowledgeImportStatusJob.model_validate(
        {
            "type": "knowledge_import_status",
            "importId": result["importId"],
            "actor": ACTOR,
        }
    )
    operation_job = OperationStatusJob.model_validate(
        {
            "type": "operation_status",
            "operationId": "http-import-start-1",
            "actor": {"actorType": "human", "actorId": "wrong-actor"},
        }
    )

    status = asyncio.run(
        runner.run(status_job, disconnected=_connected, session_id=wrong_session)
    )
    operation = asyncio.run(
        runner.run(operation_job, disconnected=_connected, session_id=SESSION)
    )

    assert status.status_code == 401
    assert status.evidence.checks[0].code == "import-unauthorized"
    assert operation.status_code == 401
    assert operation.evidence.checks[0].code == "operation-unauthorized"
    serialized = status.evidence.model_dump_json() + operation.evidence.model_dump_json()
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
    assert wrong_session not in serialized


def test_operation_status_unknown_is_explicit_and_bounded(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with KnowledgeCatalog.open(Path(config.database_path)):
        pass
    job = OperationStatusJob.model_validate(
        {
            "type": "operation_status",
            "operationId": "missing-operation",
            "actor": ACTOR,
        }
    )

    result, evidence = _run_operation_status(job, config, SESSION)

    assert result == {
        "operationId": "missing-operation",
        "status": "unknown",
        "requestDigest": None,
        "resultRefs": {},
    }
    assert evidence.status == "verified"
    assert SESSION not in evidence.model_dump_json()


def _seed_review_projection_fixture(tmp_path: Path) -> tuple[str, tuple[str, ...], str]:
    from course_helper.cards import create_review_task, seed_vocabulary
    from course_helper.domain.knowledge import (
        CardContentNode,
        ChunkCitation,
        KnowledgeCardVersion,
    )

    actor = ActorRef(actor_type="human", actor_id="review-author")
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        vocabulary = seed_vocabulary(catalog)
        current = catalog.insert_card(
            KnowledgeCardVersion(
                logical_id="bounded-review-card",
                version_id="bounded-review-card-v1",
                revision=1,
                content_digest="1" * 64,
                created_at=NOW,
                created_by=actor,
                main_type_id="exercise",
                title="Current review card",
                learning_objective="Inspect bounded review data",
                content_ast=(CardContentNode(type="paragraph", text="current"),),
                suggested_minutes=5,
                vocabulary_version_id=vocabulary.version_id,
                status="review",
            )
        )
        candidate = catalog.insert_card(
            KnowledgeCardVersion(
                logical_id=current.logical_id,
                version_id="bounded-review-card-v2",
                revision=2,
                supersedes_version_id=current.version_id,
                content_digest="2" * 64,
                created_at=NOW.replace(second=1),
                created_by=actor,
                main_type_id="exercise",
                title="Candidate " + "T" * 600,
                learning_objective="Objective " + "O" * 1200,
                content_ast=tuple(
                    CardContentNode(type="paragraph", text=f"node-{index}-" + "X" * 2200)
                    for index in range(60)
                ),
                suggested_minutes=5,
                vocabulary_version_id=vocabulary.version_id,
                chunk_citations=tuple(
                    ChunkCitation(
                        chunk_id=f"chunk-{index}",
                        source_version_id=f"source-{index}",
                        quoted_text="Q" * 1200,
                    )
                    for index in range(55)
                ),
                status="review",
            )
        )
        tasks = (
            create_review_task(
                catalog,
                kind="source-changed",
                subject_version_id=candidate.version_id,
                blocking=True,
                created_at=NOW.replace(second=2),
                created_by=actor,
            ),
            create_review_task(
                catalog,
                kind="manual-review",
                subject_version_id=candidate.version_id,
                blocking=True,
                created_at=NOW.replace(second=3),
                created_by=actor,
            ),
            create_review_task(
                catalog,
                kind="citation-missing",
                subject_version_id=candidate.version_id,
                blocking=False,
                created_at=NOW.replace(second=4),
                created_by=actor,
            ),
        )
        suggestion = register_upgrade_suggestion(
            catalog,
            UpgradeSuggestion(
                suggestion_id="bounded-upgrade-1",
                current_version_id=current.version_id,
                candidate_version_id=candidate.version_id,
                review_task_id=tasks[0].task_id,
                reason_code="source-changed",
                created_at=NOW.replace(second=5),
                created_by=actor,
            ),
        )
    return candidate.version_id, tuple(task.task_id for task in tasks), suggestion.suggestion_id


def test_review_list_detail_and_upgrade_list_are_bounded_and_opaque(tmp_path: Path) -> None:
    candidate_id, task_ids, suggestion_id = _seed_review_projection_fixture(tmp_path)
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        first = list_review_tasks(catalog, status="open", limit=2)
        second = list_review_tasks(catalog, cursor=first.next_cursor, limit=2)
        detail = get_review_detail(catalog, task_ids[1])
        upgrades = list_upgrade_suggestions(catalog, status="open", limit=10)

    assert tuple(item.task_id for item in first.items + second.items) == tuple(
        sorted(task_ids)
    )
    assert first.next_cursor is not None and second.next_cursor is None
    assert detail.card_version_id == candidate_id
    assert len(detail.card_title or "") == 500
    assert len(detail.learning_objective or "") == 1000
    assert len(detail.content_nodes) == 50
    assert detail.content_node_total == 60
    assert detail.content_nodes_truncated is True
    assert all(len(node.text or "") <= 2000 for node in detail.content_nodes)
    assert len(detail.citations) == 50
    assert detail.citation_total == 55
    assert detail.citations_truncated is True
    assert all(len(citation.quoted_text or "") <= 1000 for citation in detail.citations)
    assert [item.suggestion_id for item in upgrades.items] == [suggestion_id]
    serialized = (
        first.model_dump_json()
        + second.model_dump_json()
        + detail.model_dump_json()
        + upgrades.model_dump_json()
    )
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
    assert "relative_path" not in serialized
    assert "X" * 2001 not in serialized
    assert "Q" * 1001 not in serialized


def test_review_projection_rejects_malformed_cursor_limits_and_tampered_envelope(
    tmp_path: Path,
) -> None:
    _candidate_id, task_ids, _suggestion_id = _seed_review_projection_fixture(tmp_path)
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        for call in (
            lambda: list_review_tasks(catalog, limit=0),
            lambda: list_review_tasks(catalog, cursor="../private"),
            lambda: list_upgrade_suggestions(catalog, limit=101),
            lambda: list_upgrade_suggestions(catalog, cursor="upgrade-cursor-" + "0" * 32),
        ):
            with pytest.raises((ReviewProjectionError, ReviewQueryError)):
                call()
        catalog.connection.execute("DROP TRIGGER review_tasks_immutable_update")
        row = catalog.connection.execute(
            "SELECT payload_json FROM review_tasks WHERE task_id = ?", (task_ids[0],)
        ).fetchone()
        payload = row[0].replace('"blocking":true', '"blocking":false')
        catalog.connection.execute(
            "UPDATE review_tasks SET payload_json = ? WHERE task_id = ?",
            (payload, task_ids[0]),
        )
        with pytest.raises(ReviewProjectionError):
            list_review_tasks(catalog)


def test_review_read_job_schemas_and_handlers_are_strict_bounded_lower_camel(
    tmp_path: Path,
) -> None:
    _candidate_id, task_ids, suggestion_id = _seed_review_projection_fixture(tmp_path)
    payloads = (
        {
            "type": "knowledge_review_list",
            "status": "open",
            "category": "candidate-card",
            "limit": 2,
            "cursor": None,
        },
        {"type": "knowledge_review_detail", "taskId": task_ids[1]},
        {
            "type": "knowledge_upgrade_list",
            "status": "open",
            "limit": 10,
            "cursor": None,
        },
    )
    adapter = TypeAdapter(JobSpec)
    jobs = tuple(adapter.validate_python(payload) for payload in payloads)
    assert isinstance(jobs[0], KnowledgeReviewListJob)
    assert isinstance(jobs[1], KnowledgeReviewDetailJob)
    assert isinstance(jobs[2], KnowledgeUpgradeListJob)
    assert all(
        job.model_dump(mode="json", by_alias=True) == payload
        for job, payload in zip(jobs, payloads, strict=True)
    )
    for invalid in (
        {**payloads[0], "limit": 101},
        {**payloads[0], "sourcePath": "C:/private"},
        {**payloads[1], "task_id": task_ids[1]},
        {**payloads[2], "cursor": "../private"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)

    list_result, list_evidence = _run_knowledge_review_list(jobs[0], _config(tmp_path), SESSION)
    detail_result, detail_evidence = _run_knowledge_review_detail(
        jobs[1], _config(tmp_path), SESSION
    )
    upgrade_result, upgrade_evidence = _run_knowledge_upgrade_list(
        jobs[2], _config(tmp_path), SESSION
    )

    assert list_result["items"]
    assert len(detail_result["contentNodes"]) == 50
    assert upgrade_result["items"][0]["suggestionId"] == suggestion_id
    serialized = (
        str(list_result)
        + str(detail_result)
        + str(upgrade_result)
        + list_evidence.model_dump_json()
        + detail_evidence.model_dump_json()
        + upgrade_evidence.model_dump_json()
    )
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized


def test_real_spawn_review_list_is_authenticated_and_bounded(tmp_path: Path) -> None:
    _seed_review_projection_fixture(tmp_path)
    runner = BoundedJobRunner(_config(tmp_path))
    job = KnowledgeReviewListJob(
        type="knowledge_review_list",
        status="open",
        limit=2,
    )

    outcome = asyncio.run(
        runner.run(job, disconnected=_connected, session_id=SESSION)
    )

    assert outcome.status_code == 200
    assert len(outcome.result["items"]) == 2
    assert outcome.result["nextCursor"].startswith("review-cursor-")
    assert outcome.evidence.status == "verified"
    serialized = outcome.evidence.model_dump_json() + str(outcome.result)
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized


def _seed_operation_publish_candidate(tmp_path: Path) -> tuple[str, str]:
    from course_helper.cards import VOCABULARY_VERSION_ID, seed_vocabulary
    from course_helper.domain.common import SourceLocator
    from course_helper.domain.knowledge import (
        CardContentNode,
        ChunkCitation,
        KnowledgeCardVersion,
        TagAssignment,
    )
    from course_helper.domain.sources import (
        ChunkLocator,
        ExtractedChunk,
        SourceAssetVersion,
    )

    card_version_id = "governed-publish-card-v1"
    content_digest = hashlib.sha256(card_version_id.encode("utf-8")).hexdigest()
    actor = ActorRef(actor_type="human", actor_id="import-author")
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        seed_vocabulary(catalog)
        catalog.insert_source(
            SourceAssetVersion(
                logical_id="governed-publish-source",
                version_id="governed-publish-source-v1",
                revision=1,
                content_digest=hashlib.sha256(b"source").hexdigest(),
                created_at=NOW,
                created_by=actor,
                locator=SourceLocator(root_id="fixture", relative_path="source.md"),
                display_name="source.md",
                source_kind="markdown",
                media_type="text/markdown",
                byte_size=8,
                extraction_status="parsed",
            )
        )
        catalog.insert_chunk(
            ExtractedChunk(
                chunk_id="governed-publish-chunk",
                source_version_id="governed-publish-source-v1",
                ordinal=0,
                modality="text",
                language="en",
                normalized_text="Grounded governed publication",
                content_digest=hashlib.sha256(
                    b"Grounded governed publication"
                ).hexdigest(),
                locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
            )
        )
        catalog.insert_card(
            KnowledgeCardVersion(
                logical_id="governed-publish-card",
                version_id=card_version_id,
                revision=1,
                content_digest=content_digest,
                created_at=NOW,
                created_by=actor,
                main_type_id="concept",
                title="Governed publication",
                learning_objective="Publish through one durable operation",
                content_ast=(
                    CardContentNode(type="paragraph", text="Grounded content"),
                ),
                suggested_minutes=5,
                vocabulary_version_id=VOCABULARY_VERSION_ID,
                tag_assignments=(
                    TagAssignment(
                        vocabulary_version_id=VOCABULARY_VERSION_ID,
                        dimension_id="difficulty",
                        tag_id="difficulty:beginner",
                    ),
                ),
                chunk_citations=(
                    ChunkCitation(
                        chunk_id="governed-publish-chunk",
                        source_version_id="governed-publish-source-v1",
                        quoted_text="Grounded governed publication",
                    ),
                ),
                status="review",
            )
        )
    return card_version_id, content_digest


def test_mutation_job_schemas_are_strict_digest_bound_and_lower_camel(
    tmp_path: Path,
) -> None:
    card_version_id, card_digest = _seed_operation_publish_candidate(tmp_path)
    _candidate_id, task_ids, suggestion_id = _seed_review_projection_fixture(tmp_path)
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        detail = get_review_detail(catalog, task_ids[1])
        upgrade = list_upgrade_suggestions(catalog, limit=10).items[0]

    review_payload = {
        "type": "knowledge_review_resolve",
        "taskId": task_ids[1],
        "decision": "accept",
        "expectedReviewDigest": detail.task.review_digest,
        "evidenceIds": [],
        "operationId": "resolve-review-operation",
        "actor": ACTOR,
    }
    review_payload["requestDigest"] = review_resolution_request_digest(
        task_id=task_ids[1],
        decision="accept",
        expected_review_digest=detail.task.review_digest,
        evidence_ids=(),
    )
    publish_payload = {
        "type": "knowledge_card_publish",
        "cardVersionId": card_version_id,
        "expectedCardDigest": card_digest,
        "operationId": "publish-card-operation",
        "actor": ACTOR,
    }
    publish_payload["requestDigest"] = card_publish_request_digest(
        card_version_id=card_version_id,
        expected_card_digest=card_digest,
    )
    upgrade_payload = {
        "type": "knowledge_upgrade_resolve",
        "suggestionId": suggestion_id,
        "decision": "accept",
        "expectedSuggestionDigest": upgrade.suggestion_digest,
        "expectedReviewDigest": upgrade.review_digest,
        "expectedCardDigest": upgrade.candidate_digest,
        "evidenceIds": [],
        "operationId": "resolve-upgrade-operation",
        "actor": ACTOR,
    }
    upgrade_payload["requestDigest"] = upgrade_resolution_request_digest(
        suggestion_id=suggestion_id,
        decision="accept",
        expected_suggestion_digest=upgrade.suggestion_digest,
        expected_review_digest=upgrade.review_digest,
        expected_card_digest=upgrade.candidate_digest,
        evidence_ids=(),
    )

    adapter = TypeAdapter(JobSpec)
    jobs = tuple(
        adapter.validate_python(payload)
        for payload in (review_payload, publish_payload, upgrade_payload)
    )
    assert isinstance(jobs[0], KnowledgeReviewResolveJob)
    assert isinstance(jobs[1], KnowledgeCardPublishJob)
    assert isinstance(jobs[2], KnowledgeUpgradeResolveJob)
    assert all(
        job.model_dump(mode="json", by_alias=True) == payload
        for job, payload in zip(
            jobs, (review_payload, publish_payload, upgrade_payload), strict=True
        )
    )
    for invalid in (
        {**review_payload, "requestDigest": "0" * 64},
        {**publish_payload, "cardPath": "C:/private"},
        {**upgrade_payload, "expectedReviewDigest": "0" * 64},
        {**upgrade_payload, "evidenceIds": ["duplicate", "duplicate"]},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_mutation_handlers_commit_atomically_and_replay_byte_identically(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    card_version_id, card_digest = _seed_operation_publish_candidate(tmp_path)
    _candidate_id, task_ids, suggestion_id = _seed_review_projection_fixture(tmp_path)
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        detail = get_review_detail(catalog, task_ids[1])
        upgrade = list_upgrade_suggestions(catalog, limit=10).items[0]

    review_job = KnowledgeReviewResolveJob.model_validate(
        {
            "type": "knowledge_review_resolve",
            "taskId": task_ids[1],
            "decision": "accept",
            "expectedReviewDigest": detail.task.review_digest,
            "evidenceIds": [],
            "operationId": "resolve-review-operation",
            "requestDigest": review_resolution_request_digest(
                task_id=task_ids[1],
                decision="accept",
                expected_review_digest=detail.task.review_digest,
                evidence_ids=(),
            ),
            "actor": ACTOR,
        }
    )
    publish_job = KnowledgeCardPublishJob.model_validate(
        {
            "type": "knowledge_card_publish",
            "cardVersionId": card_version_id,
            "expectedCardDigest": card_digest,
            "operationId": "publish-card-operation",
            "requestDigest": card_publish_request_digest(
                card_version_id=card_version_id,
                expected_card_digest=card_digest,
            ),
            "actor": ACTOR,
        }
    )
    upgrade_job = KnowledgeUpgradeResolveJob.model_validate(
        {
            "type": "knowledge_upgrade_resolve",
            "suggestionId": suggestion_id,
            "decision": "accept",
            "expectedSuggestionDigest": upgrade.suggestion_digest,
            "expectedReviewDigest": upgrade.review_digest,
            "expectedCardDigest": upgrade.candidate_digest,
            "evidenceIds": [],
            "operationId": "resolve-upgrade-operation",
            "requestDigest": upgrade_resolution_request_digest(
                suggestion_id=suggestion_id,
                decision="accept",
                expected_suggestion_digest=upgrade.suggestion_digest,
                expected_review_digest=upgrade.review_digest,
                expected_card_digest=upgrade.candidate_digest,
                evidence_ids=(),
            ),
            "actor": ACTOR,
        }
    )

    review_first, _ = _run_knowledge_review_resolve(review_job, config, SESSION)
    review_replay, _ = _run_knowledge_review_resolve(review_job, config, SESSION)
    publish_first, _ = _run_knowledge_card_publish(publish_job, config, SESSION)
    publish_replay, _ = _run_knowledge_card_publish(publish_job, config, SESSION)
    upgrade_first, _ = _run_knowledge_upgrade_resolve(upgrade_job, config, SESSION)
    upgrade_replay, _ = _run_knowledge_upgrade_resolve(upgrade_job, config, SESSION)

    assert review_replay == review_first
    assert publish_replay == publish_first
    assert upgrade_replay == upgrade_first
    assert review_first["reviewStatus"] == "resolved"
    assert publish_first["submittedCardVersionId"] == card_version_id
    assert publish_first["publishedCardVersionId"]
    assert publish_first["indexState"] == "queued"
    assert str(publish_first["indexOutboxId"]).startswith("index-outbox-")
    assert upgrade_first["suggestionId"] == suggestion_id
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id IN (?, ?, ?)",
            (
                review_job.operation_id,
                publish_job.operation_id,
                upgrade_job.operation_id,
            ),
        ).fetchone()[0] == 3
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox WHERE operation_id = ?",
            (publish_job.operation_id,),
        ).fetchone()[0] == 1


def test_real_spawn_card_publish_recovers_one_durable_operation(tmp_path: Path) -> None:
    card_version_id, card_digest = _seed_operation_publish_candidate(tmp_path)
    job = KnowledgeCardPublishJob.model_validate(
        {
            "type": "knowledge_card_publish",
            "cardVersionId": card_version_id,
            "expectedCardDigest": card_digest,
            "operationId": "spawn-publish-card-operation",
            "requestDigest": card_publish_request_digest(
                card_version_id=card_version_id,
                expected_card_digest=card_digest,
            ),
            "actor": ACTOR,
        }
    )

    outcome = asyncio.run(
        BoundedJobRunner(_config(tmp_path)).run(
            job, disconnected=_connected, session_id=SESSION
        )
    )

    assert outcome.status_code == 200
    assert outcome.result["operationStatus"] == "committed"
    assert outcome.result["submittedCardVersionId"] == card_version_id
    assert outcome.result["publishedCardVersionId"]
    serialized = outcome.evidence.model_dump_json() + str(outcome.result)
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized


def test_card_publish_response_loss_recovers_exact_committed_outcome(
    tmp_path: Path,
) -> None:
    card_version_id, card_digest = _seed_operation_publish_candidate(tmp_path)
    job = KnowledgeCardPublishJob.model_validate(
        {
            "type": "knowledge_card_publish",
            "cardVersionId": card_version_id,
            "expectedCardDigest": card_digest,
            "operationId": "response-loss-publish-operation",
            "requestDigest": card_publish_request_digest(
                card_version_id=card_version_id,
                expected_card_digest=card_digest,
            ),
            "actor": ACTOR,
        }
    )
    context = _DelayedResponseContext()

    outcome = asyncio.run(
        BoundedJobRunner(
            _config(tmp_path), mp_context=context, poll_interval=0
        ).run(job, disconnected=_disconnected, session_id=SESSION)
    )

    assert outcome.status_code == 200
    assert outcome.evidence.checks[0].code == "operation-recovery"
    assert outcome.result["operationStatus"] == "committed"
    assert outcome.result["submittedCardVersionId"] == card_version_id
    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id = ?",
            (job.operation_id,),
        ).fetchone()[0] == 1
        assert catalog.connection.execute(
            "SELECT count(*) FROM knowledge_index_outbox WHERE operation_id = ?",
            (job.operation_id,),
        ).fetchone()[0] == 1


def test_card_publish_fails_closed_for_stale_digest_and_wrong_session(
    tmp_path: Path,
) -> None:
    card_version_id, card_digest = _seed_operation_publish_candidate(tmp_path)
    stale_digest = "0" * 64
    stale_job = KnowledgeCardPublishJob.model_validate(
        {
            "type": "knowledge_card_publish",
            "cardVersionId": card_version_id,
            "expectedCardDigest": stale_digest,
            "operationId": "stale-publish-operation",
            "requestDigest": card_publish_request_digest(
                card_version_id=card_version_id,
                expected_card_digest=stale_digest,
            ),
            "actor": ACTOR,
        }
    )
    with pytest.raises(CatalogReferenceError):
        _run_knowledge_card_publish(stale_job, _config(tmp_path), SESSION)

    valid_job = KnowledgeCardPublishJob.model_validate(
        {
            "type": "knowledge_card_publish",
            "cardVersionId": card_version_id,
            "expectedCardDigest": card_digest,
            "operationId": "authority-publish-operation",
            "requestDigest": card_publish_request_digest(
                card_version_id=card_version_id,
                expected_card_digest=card_digest,
            ),
            "actor": ACTOR,
        }
    )
    _run_knowledge_card_publish(valid_job, _config(tmp_path), SESSION)
    with pytest.raises(OperationAuthenticationError):
        _run_knowledge_card_publish(
            valid_job, _config(tmp_path), "session-" + "b" * 64
        )

    with KnowledgeCatalog.open(tmp_path / "knowledge.db") as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id = ?",
            (stale_job.operation_id,),
        ).fetchone()[0] == 0
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id = ?",
            (valid_job.operation_id,),
        ).fetchone()[0] == 1
