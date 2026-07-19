from __future__ import annotations

import base64
import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
import pytest

import course_helper.jobs as jobs_module
from course_helper.catalog import KnowledgeCatalog
from course_helper.jobs import (
    JobSpec,
    VisualAcquireJob,
    VisualRevalidateJob,
    VisualSearchJob,
    WorkerRuntimeConfig,
    _run_visual_acquire,
    _run_visual_revalidate,
    _run_visual_search,
    visual_acquire_request_digest,
    visual_revalidate_request_digest,
    visual_search_request_digest,
)
from course_helper.network_visuals import (
    NetworkVisualError,
    ProviderDownload,
    WikimediaApiClient,
)


FIXTURES = Path(__file__).parent / "fixtures" / "visual-providers" / "wikimedia"
SESSION = "session-" + "e" * 64
ACTOR = {"actorType": "human", "actorId": "visual-author"}


def _fixture_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureTransport:
    def __init__(self) -> None:
        self.body = base64.b64decode(
            (FIXTURES / "tiny.png.b64").read_text(encoding="ascii")
        )
        self.calls: list[str] = []

    def get_json(self, url: str, *, max_bytes: int):
        assert max_bytes == 1024 * 1024
        self.calls.append(url)
        return _fixture_json("search.json")

    def get_bytes(self, url: str, *, max_bytes: int) -> ProviderDownload:
        assert max_bytes == 32 * 1024 * 1024
        self.calls.append(url)
        return ProviderDownload(
            body=self.body,
            final_url="https://upload.wikimedia.org/wikipedia/commons/a/ab/Verified.png",
            media_type="image/png",
            content_length=len(self.body),
            redirect_count=0,
            resolved_host_count=1,
        )


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_path=str(tmp_path / "visual-jobs.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
    )


def test_e2e_network_fixture_requires_exact_process_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WorkerRuntimeConfig(
        database_path=str(tmp_path / "fixture.db"),
        app_data_path=str(tmp_path / "app-data"),
        source_roots=(),
        network_fixture=True,
    )
    monkeypatch.delenv("COURSE_E2E_FIXTURE", raising=False)
    with pytest.raises(NetworkVisualError, match="not authorized"):
        jobs_module._network_provider(config)

    monkeypatch.setenv("COURSE_E2E_FIXTURE", "1")
    assert isinstance(jobs_module._network_provider(config), WikimediaApiClient)


def _search_job() -> VisualSearchJob:
    return VisualSearchJob.model_validate(
        {
            "type": "visual_search",
            "query": "verified AI diagram",
            "limit": 5,
            "operationId": "http-visual-search",
            "requestDigest": visual_search_request_digest(
                query="verified AI diagram", limit=5
            ),
            "actor": ACTOR,
        }
    )


def _acquire_job(candidate_id: str) -> VisualAcquireJob:
    return VisualAcquireJob.model_validate(
        {
            "type": "visual_acquire",
            "candidateIds": [candidate_id],
            "operationId": "http-visual-acquire",
            "requestDigest": visual_acquire_request_digest((candidate_id,)),
            "actor": ACTOR,
        }
    )


def _revalidate_job(visual_version_id: str) -> VisualRevalidateJob:
    return VisualRevalidateJob.model_validate(
        {
            "type": "visual_revalidate",
            "visualVersionId": visual_version_id,
            "operationId": "http-visual-revalidate",
            "requestDigest": visual_revalidate_request_digest(visual_version_id),
            "actor": ACTOR,
        }
    )


def test_visual_job_contracts_are_strict_digest_bound_and_accept_no_input_url() -> None:
    search = _search_job()
    acquire = _acquire_job("network-candidate-" + "1" * 64)
    revalidate = _revalidate_job("network-visual-v1")
    adapter = TypeAdapter(JobSpec)
    for item in (search, acquire, revalidate):
        payload = item.model_dump(mode="json", by_alias=True)
        assert adapter.validate_python(payload) == item
        assert not any("url" in key.casefold() for key in payload)
    for invalid in (
        {**search.model_dump(mode="json", by_alias=True), "query": " padded "},
        {
            **acquire.model_dump(mode="json", by_alias=True),
            "finalMediaUrl": "https://example.invalid/media.png",
        },
        {
            **revalidate.model_dump(mode="json", by_alias=True),
            "requestDigest": "f" * 64,
        },
    ):
        try:
            adapter.validate_python(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("unsafe visual job was accepted")


def test_fixture_visual_search_acquire_revalidate_replay_is_bounded_and_path_free(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    with KnowledgeCatalog.open(config.database_path):
        pass
    transport = FixtureTransport()
    provider = WikimediaApiClient(transport)
    monkeypatch.setattr(jobs_module, "_network_provider", lambda _config: provider)

    searched, _ = _run_visual_search(_search_job(), config, SESSION)
    search_call_count = len(transport.calls)
    search_replay, _ = _run_visual_search(_search_job(), config, SESSION)
    assert search_replay == searched
    assert len(transport.calls) == search_call_count
    candidate = searched["items"][0]
    assert set(candidate) == {
        "candidateId",
        "fileTitle",
        "mediaType",
        "width",
        "height",
        "licenseId",
        "expiresAt",
    }

    acquire_job = _acquire_job(candidate["candidateId"])
    acquired, _ = _run_visual_acquire(acquire_job, config, SESSION)
    acquire_call_count = len(transport.calls)
    acquire_replay, _ = _run_visual_acquire(acquire_job, config, SESSION)
    assert acquire_replay == acquired
    assert len(transport.calls) == acquire_call_count
    item = acquired["items"][0]
    assert item["status"] == "acquired"
    assert item["landingLink"]["linkType"] == "landing"
    assert item["licenseLink"]["linkType"] == "license"
    assert item["landingLink"]["href"].startswith("https://commons.wikimedia.org/")
    assert "upload.wikimedia.org" not in str(acquired)

    revalidate_job = _revalidate_job(item["visualVersionId"])
    revalidated, _ = _run_visual_revalidate(revalidate_job, config, SESSION)
    revalidate_call_count = len(transport.calls)
    revalidate_replay, _ = _run_visual_revalidate(
        revalidate_job, config, SESSION
    )
    assert revalidate_replay == revalidated
    assert len(transport.calls) == revalidate_call_count
    assert revalidated["item"]["status"] == "revalidated"
    assert revalidated["item"]["verificationStatus"] == "verified"

    serialized = str(searched) + str(acquired) + str(revalidated)
    assert str(tmp_path) not in serialized
    assert SESSION not in serialized
    assert "media_url" not in serialized
    with KnowledgeCatalog.open(config.database_path) as catalog:
        assert catalog.connection.execute(
            "SELECT count(*) FROM operation_outcomes WHERE operation_id LIKE 'http-visual-%'"
        ).fetchone()[0] == 3
