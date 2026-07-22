from __future__ import annotations

import importlib
import ctypes
import hashlib
import io
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import types
from dataclasses import replace
from pathlib import Path

import pytest

from course_helper.catalog import KnowledgeCatalog
from course_helper.model_cache import (
    ModelIdentity,
    ModelManifest,
    ModelMember,
    PackageIdentity,
    VerifiedModelCache,
)


def _embeddings_module():
    return importlib.import_module("course_helper.embeddings")


def test_migration_four_creates_append_only_embedding_snapshot_tables(
    tmp_path: Path,
) -> None:
    with KnowledgeCatalog.open(tmp_path / "embedding-catalog.db") as catalog:
        versions = tuple(
            row[0]
            for row in catalog.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        tables = {
            row[0]
            for row in catalog.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert versions == (1, 2, 3, 4, 5, 6, 7, 8)
    assert {
        "embedding_index_snapshots",
        "card_embedding_rows",
        "knowledge_index_outbox_consumptions",
    }.issubset(tables)


@pytest.mark.parametrize(
    "vector",
    (
        (1.0,),
        tuple(0.0 for _ in range(512)),
        (math.nan,) + tuple(0.0 for _ in range(511)),
        (math.inf,) + tuple(0.0 for _ in range(511)),
        tuple(1.0 for _ in range(512)),
    ),
)
def test_vector_validation_rejects_wrong_dimension_nonfinite_zero_and_unnormalized(
    vector: tuple[float, ...],
) -> None:
    embeddings = _embeddings_module()

    with pytest.raises(embeddings.EmbeddingVectorError):
        embeddings.validate_embedding_vector(vector, dimension=512)


def test_vector_validation_returns_an_immutable_normalized_512_vector() -> None:
    embeddings = _embeddings_module()
    vector = (1.0,) + tuple(0.0 for _ in range(511))

    validated = embeddings.validate_embedding_vector(vector, dimension=512)

    assert validated == vector
    assert isinstance(validated, tuple)


def test_course_studio_rrf_v1_uses_exact_formula_and_card_id_tie_break() -> None:
    embeddings = _embeddings_module()

    fused = embeddings.course_studio_rrf_v1(
        fts_ranked_ids=("card-z", "card-a"),
        semantic_ranked_ids=("card-a", "card-z"),
    )

    assert [item.card_version_id for item in fused] == ["card-a", "card-z"]
    assert all(item.score == pytest.approx(1 / 61 + 1 / 62) for item in fused)
    assert fused[0].fts_rank == 2
    assert fused[0].semantic_rank == 1


def test_rrf_rejects_duplicate_lane_members_instead_of_hiding_rank_corruption() -> None:
    embeddings = _embeddings_module()

    with pytest.raises(ValueError, match="duplicate"):
        embeddings.course_studio_rrf_v1(
            fts_ranked_ids=("card-a", "card-a"),
            semantic_ranked_ids=(),
        )


def test_offline_embedding_primitives_do_not_open_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = _embeddings_module()

    def denied(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline embedding code opened a socket")

    monkeypatch.setattr(socket, "socket", denied)
    assert embeddings.validate_embedding_vector(
        (1.0,) + tuple(0.0 for _ in range(511)), dimension=512
    )
    assert embeddings.course_studio_rrf_v1(("card-a",), ("card-a",))[0].score == pytest.approx(
        2 / 61
    )


def _verified_cache(tmp_path: Path) -> VerifiedModelCache:
    generation_root = tmp_path / "generation"
    model_root = generation_root / "model"
    runtime_root = generation_root / "runtime"
    model_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    manifest = ModelManifest(
        schema_version=1,
        manifest_id="bge-small-zh-v1.5-fastembed-0.8.0",
        package=PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename="fastembed-0.8.0-py3-none-any.whl",
            wheel_sha256="40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0",
        ),
        model=ModelIdentity(
            id="BAAI/bge-small-zh-v1.5",
            revision="7999e1d3359715c523056ef9478215996d62a620",
            artifact_repository="Qdrant/bge-small-zh-v1.5",
            artifact_revision="46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
            dimension=512,
            normalized=True,
            encoding_policy="utf8-nfkc-no-prefix",
        ),
        files=(ModelMember(path="config.json", size=2, sha256="a" * 64),),
        aggregate_digest="b" * 64,
    )
    return VerifiedModelCache(
        manifest=manifest,
        specific_model_path=model_root.resolve(),
        cache_digest="c" * 64,
        generation_root=generation_root.resolve(),
        runtime_root=runtime_root.resolve(),
        runtime_digest="d" * 64,
        wheel_set_digest="e" * 64,
        generation_digest="f" * 64,
    )


def test_fastembed_provider_uses_only_verified_specific_path_and_local_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = _embeddings_module()
    calls: list[dict[str, object]] = []

    class LocalModel:
        def embed(self, values: list[str]):
            assert values
            return iter([(1.0,) + tuple(0.0 for _ in range(511)) for _ in values])

    def factory(**kwargs: object) -> LocalModel:
        calls.append(kwargs)
        return LocalModel()

    def denied(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verified provider opened a socket")

    monkeypatch.setattr(socket, "socket", denied)
    provider = embeddings.FastEmbedProvider(
        _verified_cache(tmp_path),
        model_factory=factory,
        package_version_getter=lambda _name: "0.8.0",
    )

    assert provider.embed_query("  RFM\t分析 ") == (
        1.0,
        *tuple(0.0 for _ in range(511)),
    )
    assert provider.embed_documents(("card one", "card two")) == (
        (1.0, *tuple(0.0 for _ in range(511))),
        (1.0, *tuple(0.0 for _ in range(511))),
    )
    assert calls == [
        {
            "model_name": "BAAI/bge-small-zh-v1.5",
            "specific_model_path": str((tmp_path / "generation/model").resolve()),
            "local_files_only": True,
            "providers": ["CPUExecutionProvider"],
        }
    ]
    assert provider.identity.provider == "fastembed"
    assert provider.identity.provider_version == "0.8.0"
    assert provider.identity.model_manifest_digest == "b" * 64
    assert provider.identity.model_files == (("config.json", "a" * 64, 2),)
    assert provider.identity.runtime_digest == "d" * 64
    assert provider.identity.wheel_set_digest == "e" * 64
    assert provider.identity.generation_digest == "f" * 64


def test_fastembed_provider_exposes_deep_copied_finite_child_evidence(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    vector = (1.0, *tuple(0.0 for _ in range(511)))
    source_evidence = {
        "schemaVersion": 1,
        "challengeDigest": "1" * 64,
        "processId": 41_001,
        "tempTokenDigest": "2" * 64,
        "vectorDigest": "3" * 64,
        "providerOrigins": [],
        "pythonIsolation": {"scope": "trusted-hash-locked-cpython-runtime"},
        "jobScope": "windows-job-kill-on-close",
    }
    calls: list[list[str]] = []

    class EvidenceModel:
        def embed(self, values: list[str]):
            calls.append(list(values))
            return iter(vector for _value in values)

        def embed_with_evidence(self, values: list[str]):
            calls.append(list(values))
            return tuple(vector for _value in values), source_evidence

    provider = embeddings.FastEmbedProvider(
        _verified_cache(tmp_path),
        model_factory=lambda **_kwargs: EvidenceModel(),
        package_version_getter=lambda _name: "0.8.0",
    )

    documents, document_evidence = provider.embed_documents_with_evidence(
        ("  RFM\tanalysis ", "cohort")
    )
    query, query_evidence = provider.embed_query_with_evidence("  RFM\tanalysis ")

    assert documents == (vector, vector)
    assert query == vector
    assert calls == [["RFM analysis", "cohort"], ["RFM analysis"]]
    assert document_evidence == query_evidence == source_evidence
    assert document_evidence is not query_evidence
    assert document_evidence is not source_evidence
    document_evidence["providerOrigins"].append({"forged": True})
    assert source_evidence["providerOrigins"] == []
    json.dumps(document_evidence, allow_nan=False)
    json.dumps(query_evidence, allow_nan=False)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_fastembed_provider_rejects_non_finite_child_evidence(
    tmp_path: Path,
    value: float,
) -> None:
    embeddings = _embeddings_module()
    vector = (1.0, *tuple(0.0 for _ in range(511)))

    class EvidenceModel:
        def embed(self, values: list[str]):
            return iter(vector for _value in values)

        def embed_with_evidence(self, values: list[str]):
            return tuple(vector for _value in values), {"value": value}

    provider = embeddings.FastEmbedProvider(
        _verified_cache(tmp_path),
        model_factory=lambda **_kwargs: EvidenceModel(),
        package_version_getter=lambda _name: "0.8.0",
    )

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        provider.embed_documents_with_evidence(("fixture",))

    assert caught.value.code == "EMBEDDING_PROVIDER_OUTPUT_MISMATCH"


def test_fastembed_provider_rejects_wrong_installed_package_without_loading_model(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    called = False

    def factory(**_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("model factory must not run")

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings.FastEmbedProvider(
            _verified_cache(tmp_path),
            model_factory=factory,
            package_version_getter=lambda _name: "0.8.1",
        )

    assert caught.value.code == "EMBEDDING_PROVIDER_VERSION_MISMATCH"
    assert called is False


def test_vector_validation_is_bounded_before_materializing_untrusted_iterable() -> None:
    embeddings = _embeddings_module()

    def oversized():
        for index in range(514):
            if index == 513:
                raise AssertionError("validator consumed beyond dimension + 1")
            yield 0.0

    with pytest.raises(embeddings.EmbeddingVectorError):
        embeddings.validate_embedding_vector(oversized(), dimension=512)


def test_vector_validation_converts_numeric_overflow_to_domain_error() -> None:
    embeddings = _embeddings_module()
    vector = (10**1000,) + tuple(0.0 for _ in range(511))

    with pytest.raises(embeddings.EmbeddingVectorError):
        embeddings.validate_embedding_vector(vector, dimension=512)


def test_global_same_version_fastembed_is_rejected_without_verified_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = _embeddings_module()
    forged_global = types.ModuleType("fastembed")
    forged_global.TextEmbedding = object
    monkeypatch.setitem(sys.modules, "fastembed", forged_global)
    unbound = replace(
        _verified_cache(tmp_path),
        generation_root=None,
        runtime_root=None,
        runtime_digest=None,
        wheel_set_digest=None,
        generation_digest=None,
    )

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings.FastEmbedProvider(
            unbound,
            package_version_getter=lambda _name: "0.8.0",
        )

    assert caught.value.code == "EMBEDDING_RUNTIME_NOT_VERIFIED"


def test_provider_rejects_model_or_runtime_outside_verified_generation(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    outside = tmp_path / "outside"
    outside.mkdir()
    called = False

    def factory(**_kwargs: object) -> object:
        nonlocal called
        called = True
        return object()

    forged = replace(_verified_cache(tmp_path), runtime_root=outside.resolve())
    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings.FastEmbedProvider(
            forged,
            model_factory=factory,
            package_version_getter=lambda _name: "0.8.0",
        )

    assert caught.value.code == "EMBEDDING_GENERATION_PATH_INVALID"
    assert called is False


def test_isolated_worker_keeps_windows_dll_directory_handles_alive() -> None:
    embeddings = _embeddings_module()

    assert "_dll_handles = []" in embeddings._ISOLATED_WORKER
    assert "_dll_handles.append(os.add_dll_directory(candidate))" in embeddings._ISOLATED_WORKER
    assert 'sys.stdin.buffer.read(16_000_001).decode("utf-8", errors="strict")' in (
        embeddings._ISOLATED_WORKER
    )


_CPYTHON_GUARD_PROBES = {
    "asyncio.windows_utils.Popen",
    "audit.socket",
    "audit.subprocess",
    "socket.socket",
    "socket.create_connection",
    "socket.getaddrinfo",
    "_socket.socket",
    "_socket.getaddrinfo",
    "subprocess.Popen",
    "_winapi.CreateProcess",
}


def _write_fake_embedding_runtime(
    root: Path,
    *,
    restore_socket_during_import: bool = False,
) -> tuple[Path, list[dict[str, str]]]:
    runtime = root / "runtime"
    fastembed = runtime / "fastembed"
    onnxruntime = runtime / "onnxruntime"
    fastembed.mkdir(parents=True)
    onnxruntime.mkdir(parents=True)
    restore = (
        "import socket\nsocket.socket = socket.SocketType\n"
        if restore_socket_during_import
        else ""
    )
    fastembed_source = (
        "import os\n"
        + "import asyncio\n"
        + "import ssl\n"
        + "import platform\n"
        + "platform.system()\n"
        + restore
        + "class TextEmbedding:\n"
        + "    def __init__(self, **kwargs):\n"
        + "        self.kwargs = kwargs\n"
        + "        os.mkdir(kwargs['cache_dir'])\n"
        + "    def embed(self, values):\n"
        + "        return [[1.0] + [0.0] * 511 for _ in values]\n"
    )
    (fastembed / "__init__.py").write_text(fastembed_source, encoding="utf-8")
    (onnxruntime / "__init__.py").write_text("__version__ = 'fixture'\n", encoding="utf-8")
    origins = []
    for distribution in ("fastembed", "onnxruntime"):
        relative = f"runtime/{distribution}/__init__.py"
        origins.append(
            {
                "distribution": distribution,
                "path": relative,
                "sha256": hashlib.sha256(
                    (root / relative).read_bytes()
                ).hexdigest(),
            }
        )
    return runtime, origins


def _worker_payload(
    request: dict[str, object],
    *,
    pid: int,
    origins: list[dict[str, str]],
    schema_version: object = 1,
) -> dict[str, object]:
    vector = [1.0, *[0.0 for _ in range(511)]]
    vectors = [vector]
    challenge_digest = hashlib.sha256(
        str(request["challenge"]).encode("ascii")
    ).hexdigest()
    temp_token_digest = hashlib.sha256(
        str(request["tempToken"]).encode("ascii")
    ).hexdigest()
    probes = {surface: "denied" for surface in sorted(_CPYTHON_GUARD_PROBES)}
    isolation_core = {
        "scope": "trusted-hash-locked-cpython-runtime",
        "preImportProbes": probes,
        "postInferenceProbes": probes,
    }
    evidence_digest = hashlib.sha256(
        json.dumps(
            {
                "challengeDigest": challenge_digest,
                "processId": pid,
                "tempTokenDigest": temp_token_digest,
                **isolation_core,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": schema_version,
        "challengeDigest": challenge_digest,
        "processId": pid,
        "tempTokenDigest": temp_token_digest,
        "vectorDigest": hashlib.sha256(
            json.dumps(vectors, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "vectors": vectors,
        "providerOrigins": origins,
        "pythonIsolation": {
            **isolation_core,
            "evidenceDigest": evidence_digest,
        },
    }


@pytest.mark.parametrize(
    "case",
    ("bool-schema", "pid-mismatch", "duplicate-key", "nan", "infinity"),
)
def test_worker_response_rejects_noncanonical_or_parent_unbound_evidence(
    tmp_path: Path,
    case: str,
) -> None:
    embeddings = _embeddings_module()
    runtime, origins = _write_fake_embedding_runtime(tmp_path)
    request: dict[str, object] = {
        "schemaVersion": 1,
        "challenge": "a" * 64,
        "tempToken": "b" * 64,
        "tempPath": str(tmp_path / "quarantine" / "run"),
        "values": ["fixture"],
    }
    payload = _worker_payload(
        request,
        pid=4101 if case != "pid-mismatch" else 9999,
        origins=origins,
        schema_version=True if case == "bool-schema" else 1,
    )
    raw = json.dumps(payload, separators=(",", ":"))
    if case == "duplicate-key":
        raw = raw.replace('"schemaVersion":1', '"schemaVersion":1,"schemaVersion":1', 1)
    elif case == "nan":
        raw = raw.replace('"vectors":[[1.0', '"vectors":[[NaN', 1)
    elif case == "infinity":
        raw = raw.replace('"vectors":[[1.0', '"vectors":[[Infinity', 1)

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings._validate_worker_response(
            raw.encode("utf-8"),
            expected_challenge="a" * 64,
            expected_temp_token="b" * 64,
            actual_pid=4101,
            runtime_root=runtime,
            expected_vector_count=1,
        )

    assert caught.value.code == "EMBEDDING_PROVIDER_OUTPUT_MISMATCH"


def test_isolated_model_uses_parent_owned_temp_and_challenge_bound_evidence(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    runtime, origins = _write_fake_embedding_runtime(tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    calls: list[dict[str, object]] = []

    def fresh_child(
        command: list[str],
        *,
        input_bytes: bytes,
        cwd: Path,
        env: dict[str, str],
        limits: object,
    ) -> object:
        request = json.loads(input_bytes)
        assert set(request) == {
            "schemaVersion",
            "challenge",
            "tempToken",
            "tempPath",
            "values",
        }
        assert request["schemaVersion"] == 1
        assert re.fullmatch(r"[0-9a-f]{64}", request["challenge"])
        assert re.fullmatch(r"[0-9a-f]{64}", request["tempToken"])
        assert Path(request["tempPath"]) == cwd
        assert cwd.parent == quarantine
        assert cwd.is_dir() and tuple(cwd.iterdir()) == ()
        assert env["TEMP"] == env["TMP"] == str(cwd)
        assert command[:5] == [sys.executable, "-I", "-S", "-c", embeddings._ISOLATED_WORKER]
        assert limits.timeout_seconds <= 120
        pid = 4101 + len(calls)
        payload = _worker_payload(request, pid=pid, origins=origins)
        calls.append({"request": request, "cwd": cwd, "pid": pid})
        return embeddings.WorkerExecution(
            pid=pid,
            stdout=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            stderr=b"",
            job_scope="windows-job-kill-on-close",
            overflow_stream=None,
        )

    model = embeddings._IsolatedFastEmbedModel(
        runtime_root=runtime,
        model_name="BAAI/bge-small-zh-v1.5",
        specific_model_path=str(tmp_path / "model"),
        local_files_only=True,
        providers=["CPUExecutionProvider"],
        temp_parent=quarantine,
        worker_launcher=fresh_child,
    )

    first_vectors, first_evidence = model.embed_with_evidence(["fixture"])
    second_vectors, second_evidence = model.embed_with_evidence(["fixture"])

    vector = (1.0, *tuple(0.0 for _ in range(511)))
    assert first_vectors == second_vectors == (vector,)
    assert first_evidence["challengeDigest"] != second_evidence["challengeDigest"]
    assert first_evidence["tempTokenDigest"] != second_evidence["tempTokenDigest"]
    assert first_evidence["vectorDigest"] == second_evidence["vectorDigest"]
    assert first_evidence["providerOrigins"] == second_evidence["providerOrigins"]
    assert first_evidence["pythonIsolation"]["scope"] == (
        "trusted-hash-locked-cpython-runtime"
    )
    assert set(first_evidence["pythonIsolation"]["preImportProbes"]) == (
        _CPYTHON_GUARD_PROBES
    )
    assert set(first_evidence["pythonIsolation"]["postInferenceProbes"]) == (
        _CPYTHON_GUARD_PROBES
    )
    assert "nativeSocket" not in json.dumps(first_evidence)
    assert "nativeProcess" not in json.dumps(first_evidence)
    assert len(calls) == 2
    assert calls[0]["request"] != calls[1]["request"]
    assert calls[0]["cwd"] != calls[1]["cwd"]
    assert tuple(quarantine.iterdir()) == ()


@pytest.mark.parametrize("case", ("forged-hash", "outside-runtime"))
def test_worker_response_reopens_and_rehashes_provider_origins(
    tmp_path: Path,
    case: str,
) -> None:
    embeddings = _embeddings_module()
    runtime, origins = _write_fake_embedding_runtime(tmp_path)
    request: dict[str, object] = {
        "challenge": "a" * 64,
        "tempToken": "b" * 64,
    }
    payload = _worker_payload(request, pid=4101, origins=origins)
    if case == "forged-hash":
        payload["providerOrigins"][0]["sha256"] = "f" * 64
    else:
        outside = tmp_path / "outside.py"
        outside.write_text("outside = True\n", encoding="utf-8")
        payload["providerOrigins"][0]["path"] = "runtime/../outside.py"
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings._validate_worker_response(
            raw,
            expected_challenge="a" * 64,
            expected_temp_token="b" * 64,
            actual_pid=4101,
            runtime_root=runtime,
            expected_vector_count=1,
        )

    assert caught.value.code == "EMBEDDING_PROVIDER_OUTPUT_MISMATCH"


def test_parent_owned_temp_is_removed_when_launcher_fails(tmp_path: Path) -> None:
    embeddings = _embeddings_module()
    runtime, _origins = _write_fake_embedding_runtime(tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()

    def fail_after_temp_created(
        _command: list[str],
        *,
        input_bytes: bytes,
        cwd: Path,
        env: dict[str, str],
        limits: object,
    ) -> object:
        assert input_bytes and cwd.is_dir() and env["TEMP"] == str(cwd)
        assert limits.timeout_seconds > 0
        raise embeddings.EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED")

    model = embeddings._IsolatedFastEmbedModel(
        runtime_root=runtime,
        model_name="BAAI/bge-small-zh-v1.5",
        specific_model_path=str(tmp_path / "model"),
        local_files_only=True,
        providers=["CPUExecutionProvider"],
        temp_parent=quarantine,
        worker_launcher=fail_after_temp_created,
    )

    with pytest.raises(embeddings.EmbeddingProviderError):
        model.embed_with_evidence(["fixture"])

    assert tuple(quarantine.iterdir()) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory sharing semantics")
def test_temp_chain_pin_coexists_with_workspace_reader_and_still_denies_rename(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    workspace = tmp_path / "workspace"
    quarantine = workspace / "quarantine"
    quarantine.mkdir(parents=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    existing = create_file(
        str(workspace),
        0x80,  # FILE_READ_ATTRIBUTES
        0x1 | 0x2,  # FILE_SHARE_READ | FILE_SHARE_WRITE; no share-delete.
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,
        None,
    )
    assert existing and existing != ctypes.c_void_p(-1).value
    renamed = workspace / "renamed-quarantine"
    try:
        with embeddings._hold_no_delete_directory_chain(quarantine):
            with pytest.raises(OSError):
                quarantine.rename(renamed)
    finally:
        assert close_handle(existing)

    quarantine.rename(renamed)
    assert renamed.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_real_worker_runs_pre_and_post_cpython_probes_with_parent_temp_cleanup(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    runtime, _origins = _write_fake_embedding_runtime(tmp_path)
    model_path = tmp_path / "model"
    model_path.mkdir()
    quarantine = tmp_path / "课程隔离"
    quarantine.mkdir()
    model = embeddings._IsolatedFastEmbedModel(
        runtime_root=runtime,
        model_name="BAAI/bge-small-zh-v1.5",
        specific_model_path=str(model_path),
        local_files_only=True,
        providers=["CPUExecutionProvider"],
        temp_parent=quarantine,
    )

    vectors, evidence = model.embed_with_evidence(["fixture"])

    assert len(vectors) == 1 and len(vectors[0]) == 512
    isolation = evidence["pythonIsolation"]
    assert isolation["scope"] == "trusted-hash-locked-cpython-runtime"
    assert isolation["preImportProbes"] == {
        surface: "denied" for surface in sorted(_CPYTHON_GUARD_PROBES)
    }
    assert isolation["postInferenceProbes"] == isolation["preImportProbes"]
    assert evidence["jobScope"] == "windows-job-kill-on-close"
    assert tuple(quarantine.iterdir()) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_runtime_guard_restoration_is_caught_by_post_inference_probe(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    runtime, _origins = _write_fake_embedding_runtime(
        tmp_path,
        restore_socket_during_import=True,
    )
    model_path = tmp_path / "model"
    model_path.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    model = embeddings._IsolatedFastEmbedModel(
        runtime_root=runtime,
        model_name="BAAI/bge-small-zh-v1.5",
        specific_model_path=str(model_path),
        local_files_only=True,
        providers=["CPUExecutionProvider"],
        temp_parent=quarantine,
    )

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        model.embed_with_evidence(["fixture"])

    assert caught.value.code == "EMBEDDING_PROVIDER_INFERENCE_FAILED"
    assert tuple(quarantine.iterdir()) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_windows_worker_launcher_streams_a_bounded_stdout_stderr_burst(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    stdout = 128 * 1024
    stderr = 96 * 1024
    script = (
        "import sys;"
        f"sys.stdout.buffer.write(b'o'*{stdout});sys.stdout.buffer.flush();"
        f"sys.stderr.buffer.write(b'e'*{stderr});sys.stderr.buffer.flush()"
    )
    limits = embeddings.WorkerLimits(
        timeout_seconds=10,
        stdout_bytes=stdout,
        stderr_bytes=stderr,
        process_memory_bytes=256 * 1024 * 1024,
        job_memory_bytes=320 * 1024 * 1024,
        job_user_time_100ns=100_000_000,
    )

    result = embeddings._run_windows_bounded_child(
        [sys.executable, "-I", "-c", script],
        input_bytes=b"",
        cwd=tmp_path,
        env=dict(os.environ),
        limits=limits,
    )

    assert result.pid > 0
    assert result.stdout == b"o" * stdout
    assert result.stderr == b"e" * stderr
    assert result.job_scope == "windows-job-kill-on-close"
    assert result.overflow_stream is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
@pytest.mark.parametrize("case", ("stdout-overflow", "timeout"))
def test_windows_worker_launcher_kills_bounded_failures_and_releases_resources(
    tmp_path: Path,
    case: str,
) -> None:
    embeddings = _embeddings_module()
    cwd = tmp_path / f"cwd-{case}"
    cwd.mkdir()
    if case == "stdout-overflow":
        script = "import sys;sys.stdout.buffer.write(b'x'*131072);sys.stdout.buffer.flush()"
        timeout = 10.0
        expected_code = "EMBEDDING_PROVIDER_OUTPUT_LIMIT"
    elif case == "timeout":
        script = "import threading;threading.Event().wait()"
        timeout = 0.2
        expected_code = "EMBEDDING_PROVIDER_TIMEOUT"
    limits = embeddings.WorkerLimits(
        timeout_seconds=timeout,
        stdout_bytes=64 * 1024,
        stderr_bytes=64 * 1024,
        process_memory_bytes=256 * 1024 * 1024,
        job_memory_bytes=320 * 1024 * 1024,
        job_user_time_100ns=100_000_000,
    )

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings._run_windows_bounded_child(
            [sys.executable, "-I", "-c", script],
            input_bytes=b"",
            cwd=cwd,
            env=dict(os.environ),
            limits=limits,
        )

    assert caught.value.code == expected_code
    assert not any(
        thread.name.startswith("embedding-worker-stream-")
        for thread in threading.enumerate()
    )
    renamed = tmp_path / f"released-{case}"
    cwd.rename(renamed)
    renamed.rename(cwd)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_windows_worker_job_active_process_limit_denies_grandchild(
    tmp_path: Path,
) -> None:
    embeddings = _embeddings_module()
    cwd = tmp_path / "cwd-grandchild"
    cwd.mkdir()
    script = (
        "import subprocess,sys;"
        "\ntry:\n subprocess.Popen([sys.executable,'-I','-c','import threading;threading.Event().wait()'])"
        "\nexcept OSError:\n sys.stdout.write('grandchild-denied')"
        "\nelse:\n sys.stdout.write('grandchild-spawned')\n"
    )
    limits = embeddings.WorkerLimits(
        timeout_seconds=5.0,
        stdout_bytes=64 * 1024,
        stderr_bytes=64 * 1024,
        process_memory_bytes=256 * 1024 * 1024,
        job_memory_bytes=320 * 1024 * 1024,
        job_user_time_100ns=100_000_000,
    )

    result = embeddings._run_windows_bounded_child(
        [sys.executable, "-I", "-c", script],
        input_bytes=b"",
        cwd=cwd,
        env=dict(os.environ),
        limits=limits,
    )

    assert result.stdout == b"grandchild-denied"
    assert result.stderr == b""
    assert not any(
        thread.name.startswith("embedding-worker-stream-")
        for thread in threading.enumerate()
    )
    renamed = tmp_path / "released-grandchild"
    cwd.rename(renamed)
    renamed.rename(cwd)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object fault injection")
@pytest.mark.parametrize(
    "stage", ("missing-pipe", "missing-process-handle", "assign-failure")
)
def test_unassigned_suspended_worker_is_directly_killed_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    embeddings = _embeddings_module()
    calls: list[str] = []

    class FakeCall:
        argtypes: object = None
        restype: object = None

        def __init__(self, name: str, result: object) -> None:
            self.name = name
            self.result = result

        def __call__(self, *_args: object) -> object:
            calls.append(self.name)
            return self.result

    class FakeKernel:
        CreateJobObjectW = FakeCall("create-job", 1)
        SetInformationJobObject = FakeCall("set-limits", 1)
        AssignProcessToJobObject = FakeCall(
            "assign", 0 if stage == "assign-failure" else 1
        )
        TerminateJobObject = FakeCall("terminate-job", 1)
        CloseHandle = FakeCall("close-job", 1)

    class FakeNtdll:
        NtResumeProcess = FakeCall("resume", 0)

    real_windll = ctypes.WinDLL
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **_kwargs: FakeKernel() if name == "kernel32" else FakeNtdll(),
    )

    class FakeHandle(int):
        closed = False

        def Close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 4242
            self.stdin = io.BytesIO()
            self.stdout = None if stage == "missing-pipe" else io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode: int | None = None
            self._handle = (
                None if stage == "missing-process-handle" else FakeHandle(99)
            )
            self.killed = False
            self.wait_calls: list[float | None] = []

        def kill(self) -> None:
            calls.append("kill-process")
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return -9

    process = FakeProcess()
    monkeypatch.setattr(embeddings.subprocess, "Popen", lambda *_args, **_kwargs: process)
    limits = embeddings.WorkerLimits(
        timeout_seconds=1.0,
        stdout_bytes=1024,
        stderr_bytes=1024,
        process_memory_bytes=128 * 1024 * 1024,
        job_memory_bytes=192 * 1024 * 1024,
        job_user_time_100ns=10_000_000,
    )

    try:
        with pytest.raises(embeddings.EmbeddingProviderError):
            embeddings._run_windows_bounded_child(
                [sys.executable, "-I", "-c", "pass"],
                input_bytes=b"",
                cwd=tmp_path,
                env=dict(os.environ),
                limits=limits,
            )
    finally:
        monkeypatch.setattr(ctypes, "WinDLL", real_windll)

    assert process.killed is True
    assert "kill-process" in calls
    assert process.wait_calls and process.returncode == -9
    if process._handle is not None:
        assert process._handle.closed is True
    assert "resume" not in calls
    assert "terminate-job" not in calls


@pytest.mark.model_download
def test_temp_parent_rename_swap_is_blocked_before_run_root_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = _embeddings_module()
    runtime, origins = _write_fake_embedding_runtime(tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    swapped = tmp_path / "quarantine-swapped"
    original_mkdtemp = embeddings.tempfile.mkdtemp
    swap_blocked: list[bool] = []
    launcher_called = False

    def attempted_swap(*, prefix: str, dir: object) -> str:
        parent = Path(dir)
        try:
            parent.rename(swapped)
        except OSError:
            swap_blocked.append(True)
        else:
            swap_blocked.append(False)
            parent.mkdir()
        return original_mkdtemp(prefix=prefix, dir=parent)

    def child(
        _command: list[str],
        *,
        input_bytes: bytes,
        cwd: Path,
        env: dict[str, str],
        limits: object,
    ) -> object:
        nonlocal launcher_called
        launcher_called = True
        request = json.loads(input_bytes)
        assert env["TEMP"] == str(cwd) and limits.timeout_seconds > 0
        payload = _worker_payload(request, pid=4242, origins=origins)
        return embeddings.WorkerExecution(
            pid=4242,
            stdout=json.dumps(payload, separators=(",", ":")).encode(),
            stderr=b"",
            job_scope="windows-job-kill-on-close",
            overflow_stream=None,
        )

    monkeypatch.setattr(embeddings.tempfile, "mkdtemp", attempted_swap)
    model = embeddings._IsolatedFastEmbedModel(
        runtime_root=runtime,
        model_name="BAAI/bge-small-zh-v1.5",
        specific_model_path=str(tmp_path / "model"),
        local_files_only=True,
        providers=["CPUExecutionProvider"],
        temp_parent=quarantine,
        worker_launcher=child,
    )

    try:
        model.embed_with_evidence(["fixture"])
        assert swap_blocked == [True]
        assert launcher_called is True
        released = tmp_path / "quarantine-released"
        quarantine.rename(released)
        released.rename(quarantine)
    finally:
        if swapped.exists():
            if quarantine.exists():
                quarantine.rmdir()
            swapped.rename(quarantine)


def test_temp_parent_junction_is_rejected_before_worker_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = _embeddings_module()
    runtime, _origins = _write_fake_embedding_runtime(tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    original_is_junction = getattr(Path, "is_junction", None)

    def marked_junction(path: Path) -> bool:
        if path.absolute() == quarantine.absolute():
            return True
        return False if original_is_junction is None else original_is_junction(path)

    monkeypatch.setattr(Path, "is_junction", marked_junction, raising=False)

    with pytest.raises(embeddings.EmbeddingProviderError) as caught:
        embeddings._IsolatedFastEmbedModel(
            runtime_root=runtime,
            model_name="BAAI/bge-small-zh-v1.5",
            specific_model_path=str(tmp_path / "model"),
            local_files_only=True,
            providers=["CPUExecutionProvider"],
            temp_parent=quarantine,
            worker_launcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("worker launched for a junction temp parent")
            ),
        )

    assert caught.value.code == "EMBEDDING_GENERATION_PATH_INVALID"
