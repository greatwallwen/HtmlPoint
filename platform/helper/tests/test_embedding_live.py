from __future__ import annotations

import builtins
import copy
import hashlib
import importlib
import inspect
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any, Iterable

import pytest


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
PIPELINE_NOW = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)
POLICY_ID = "course-studio-rrf-v1"
FASTEMBED_RUNTIME_SOURCE = b"__version__ = '0.8.0'\n"
ONNXRUNTIME_RUNTIME_SOURCE = b"__version__ = '1.23.2'\n"
CPYTHON_GUARD_PROBES = {
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


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


FIXTURE_VECTOR = (1.0, *tuple(0.0 for _ in range(511)))
INDEX_VECTOR_DIGEST = _canonical_digest(FIXTURE_VECTOR)
PROVIDER_VECTOR_DIGEST = _canonical_digest((FIXTURE_VECTOR,))


def _python_isolation(
    challenge_digest: str,
    process_id: int,
    temp_token_digest: str,
) -> dict[str, object]:
    probes = {surface: "denied" for surface in sorted(CPYTHON_GUARD_PROBES)}
    core = {
        "scope": "trusted-hash-locked-cpython-runtime",
        "preImportProbes": probes,
        "postInferenceProbes": probes,
    }
    evidence_core = {
        "challengeDigest": challenge_digest,
        "processId": process_id,
        "tempTokenDigest": temp_token_digest,
        **core,
    }
    return {**core, "evidenceDigest": _canonical_digest(evidence_core)}


def _live_module() -> Any:
    # Import inside each test so a missing production module yields one RED per
    # contract slice instead of aborting collection for the whole file.
    return importlib.import_module("course_helper.embedding_live")


def test_cpython_guard_probe_contract_matches_worker() -> None:
    live = _live_module()
    embeddings = importlib.import_module("course_helper.embeddings")

    assert live._CPYTHON_GUARD_PROBES == embeddings._CPYTHON_GUARD_PROBES


def _load_unique_authority_modules(tmp_path: Path) -> tuple[Any, Any]:
    source_root = Path(__file__).resolve().parents[1] / "course_helper"
    token = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:16]
    package_name = f"_embedding_live_authority_{token}"
    package = ModuleType(package_name)
    package.__file__ = str(source_root / "__init__.py")
    package.__package__ = package_name
    package.__path__ = [str(source_root)]
    sys.modules[package_name] = package

    def load(name: str) -> Any:
        qualified = f"{package_name}.{name}"
        specification = importlib.util.spec_from_file_location(
            qualified, source_root / f"{name}.py"
        )
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        sys.modules[qualified] = module
        specification.loader.exec_module(module)
        return module

    model_cache = load("model_cache")
    embeddings = load("embeddings")
    return model_cache, embeddings


def _authority(
    tmp_path: Path,
    *,
    model_cache: Any | None = None,
    embeddings: Any | None = None,
) -> tuple[Any, Any, Any, Any]:
    if model_cache is None or embeddings is None:
        model_cache, embeddings = _load_unique_authority_modules(tmp_path)
    package = model_cache.PackageIdentity(
        name="fastembed",
        version="0.8.0",
        wheel_filename="fastembed-0.8.0-py3-none-any.whl",
        wheel_sha256="f" * 64,
        wheel_size=116_572,
    )
    model = model_cache.ModelIdentity(
        id="BAAI/bge-small-zh-v1.5",
        revision="7999e1d3359715c523056ef9478215996d62a620",
        artifact_repository="Qdrant/bge-small-zh-v1.5",
        artifact_revision="46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
        dimension=512,
        normalized=True,
        encoding_policy="utf8-nfkc-no-prefix",
    )
    files = tuple(
        model_cache.ModelMember(path=path, size=size, sha256=digest)
        for path, size, digest in (
            ("config.json", 739, "1" * 64),
            (
                "model_optimized.onnx",
                94_781_076,
                "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
            ),
            ("special_tokens_map.json", 125, "2" * 64),
            ("tokenizer.json", 439_125, "3" * 64),
            ("tokenizer_config.json", 367, "4" * 64),
        )
    )
    runtime = model_cache.RuntimeIdentity(
        python="3.12",
        os="windows",
        architecture="x86_64",
        wheels=(
            model_cache.RuntimeWheel(
                name="fastembed",
                version="0.8.0",
                filename="fastembed-0.8.0-py3-none-any.whl",
                size=116_572,
                sha256="f" * 64,
                artifact_url="https://files.pythonhosted.org/fixed/fastembed.whl",
            ),
            model_cache.RuntimeWheel(
                name="onnxruntime",
                version="1.23.2",
                filename="onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
                size=100,
                sha256="8" * 64,
                artifact_url="https://files.pythonhosted.org/fixed/onnxruntime.whl",
            ),
        ),
    )
    manifest = model_cache.ModelManifest(
        schema_version=1,
        manifest_id="bge-small-zh-v1.5",
        package=package,
        model=model,
        files=files,
        aggregate_digest="a" * 64,
        runtime=runtime,
    )
    generation_root = tmp_path / "generation"
    model_root = generation_root / "model"
    runtime_root = generation_root / "runtime"
    model_root.mkdir(parents=True)
    runtime_root.mkdir()
    fastembed_runtime = runtime_root / "fastembed"
    onnxruntime_runtime = runtime_root / "onnxruntime"
    fastembed_runtime.mkdir()
    onnxruntime_runtime.mkdir()
    (fastembed_runtime / "__init__.py").write_bytes(FASTEMBED_RUNTIME_SOURCE)
    (onnxruntime_runtime / "__init__.py").write_bytes(ONNXRUNTIME_RUNTIME_SOURCE)
    verified = model_cache.VerifiedModelCache(
        manifest=manifest,
        specific_model_path=model_root,
        cache_digest="9" * 64,
        generation_root=generation_root,
        runtime_root=runtime_root,
        runtime_digest="5" * 64,
        wheel_set_digest="6" * 64,
        generation_digest="7" * 64,
    )
    pipeline = _fixed_pipeline_evidence(tmp_path)
    verification = {
        "generationDigest": verified.generation_digest,
        "childEvidenceDigest": pipeline["childEvidenceDigest"],
        "childLoadedOrigins": final_origins(),
        "providerOrigins": final_origins(),
        "pipeline": pipeline,
    }
    verification["pipelineDigest"] = _canonical_digest(pipeline)
    final_result = model_cache.FinalPhaseResult(
        verified=verified,
        quarantine_root=tmp_path / "phase-b-quarantine",
        verification=verification,
        promoted_new=True,
        write_boundary=model_cache._WriteBoundaryResult(
            root_identity=model_cache._DirectoryIdentity(volume=1, file_id=2),
            applied_directory_count=1,
            denied_probe_count=1,
            restored_directory_count=1,
            identities_verified=True,
            acl_restored=True,
            completed=True,
        ),
    )
    return model_cache, embeddings, manifest, final_result


class _EvidenceProvider:
    def __init__(
        self,
        verified: Any,
        *,
        isolated_temp_parent: Path,
        embeddings_module: Any,
        ordinal: int,
    ) -> None:
        self.verified = verified
        self.temp_parent = isolated_temp_parent
        self.ordinal = ordinal
        manifest = verified.manifest
        self.identity = embeddings_module.EmbeddingProviderIdentity(
            provider="fastembed",
            provider_version="0.8.0",
            model_id=manifest.model.id,
            model_revision=manifest.model.revision,
            artifact_repository=manifest.model.artifact_repository,
            artifact_revision=manifest.model.artifact_revision,
            dimension=manifest.model.dimension,
            encoding_policy=manifest.model.encoding_policy,
            model_manifest_digest=manifest.aggregate_digest,
            cache_digest=verified.cache_digest,
            model_files=tuple(
                (item.path, item.sha256, item.size)
                for item in sorted(manifest.files, key=lambda value: value.path)
            ),
            runtime_digest=verified.runtime_digest,
            wheel_set_digest=verified.wheel_set_digest,
            generation_digest=verified.generation_digest,
        )
        challenge_digest = f"{ordinal:x}" * 64
        process_id = 40_000 + ordinal
        temp_token_digest = f"{ordinal + 2:x}" * 64
        self.child_evidence = {
            "schemaVersion": 1,
            "challengeDigest": challenge_digest,
            "processId": process_id,
            "tempTokenDigest": temp_token_digest,
            "vectorDigest": PROVIDER_VECTOR_DIGEST,
            "providerOrigins": final_origins(),
            "pythonIsolation": _python_isolation(
                challenge_digest,
                process_id,
                temp_token_digest,
            ),
            "jobScope": "windows-job-kill-on-close",
        }

    @staticmethod
    def _vector(_text: str) -> tuple[float, ...]:
        return FIXTURE_VECTOR

    def embed_documents(self, texts: Iterable[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    def embed_documents_with_evidence(
        self, texts: Iterable[str]
    ) -> tuple[tuple[tuple[float, ...], ...], dict[str, object]]:
        return self.embed_documents(texts), dict(self.child_evidence)

    def embed_query_with_evidence(
        self, text: str
    ) -> tuple[tuple[float, ...], dict[str, object]]:
        return self.embed_query(text), dict(self.child_evidence)


def final_origins() -> list[dict[str, str]]:
    return [
        {
            "distribution": "fastembed",
            "path": "runtime/fastembed/__init__.py",
            "sha256": hashlib.sha256(FASTEMBED_RUNTIME_SOURCE).hexdigest(),
        },
        {
            "distribution": "onnxruntime",
            "path": "runtime/onnxruntime/__init__.py",
            "sha256": hashlib.sha256(ONNXRUNTIME_RUNTIME_SOURCE).hexdigest(),
        },
    ]


def _fixed_pipeline_evidence(tmp_path: Path) -> dict[str, Any]:
    fixture = {
        "schemaVersion": 1,
        "schemaId": "embedding-live-synthetic-v1",
        "sourceVersionId": "source-fixture",
        "chunkId": "chunk-fixture",
        "cardVersionId": "card-fixture",
        "query": "RFM",
    }
    challenge_digest = "e" * 64
    process_id = 40_001
    temp_token_digest = "f" * 64
    provider_evidence = {
        "schemaVersion": 1,
        "challengeDigest": challenge_digest,
        "processId": process_id,
        "tempTokenDigest": temp_token_digest,
        "vectorDigest": PROVIDER_VECTOR_DIGEST,
        "providerOrigins": final_origins(),
        "pythonIsolation": _python_isolation(
            challenge_digest,
            process_id,
            temp_token_digest,
        ),
        "jobScope": "windows-job-kill-on-close",
    }
    child_digest = hashlib.sha256(
        json.dumps(
            provider_evidence,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    retrieval = {
        "queryDigest": "b461dfa6fbbd8cea4af99936d42610f77517bce548fa9f3f1e73cc1c65b5d994",
        "filteredCandidateDigest": "292dbac55624c1f5a1c2cde046cd53108485e716820ed7325ded12e2f21a5b8c",
        "snapshotDigest": "3777d920335ed3632cc65b3a18fd3b8d42a6827996e0e0e1eb5cd61e83012369",
        "rrfK": 60,
        "hits": [
            {
                "cardVersionId": "card-fixture",
                "ftsRank": 1,
                "semanticRank": 1,
                "score": 2 / 61,
            }
        ],
    }
    return {
        "schemaVersion": 1,
        "modelManifestDigest": "a" * 64,
        "cacheDigest": "9" * 64,
        "runtimeDigest": "5" * 64,
        "wheelSetDigest": "6" * 64,
        "generationDigest": "7" * 64,
        "childEvidenceDigest": child_digest,
        "childLoadedOrigins": final_origins(),
        "fixture": fixture,
        "fixtureDigest": hashlib.sha256(
            json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "publication": {
            "cardVersionId": "card-fixture",
            "status": "published",
            "contentDigest": "40cb14e1e287eaf45e846d787ff877389cc21a0f5e198fb529d0c70ea8b6ac09",
        },
        "outbox": {
            "requestDigest": "a49bb364505f9e7284ad47aed7f6413c61fe64f52b370a84dceb9038da1bc978",
            "contentDigest": "69f2190c01d6f7551b11c438f9a4e74193e68d9a8960d7c9940d9eabcde3f890",
            "claimDigest": "82a63a5af42d69315f810e0ffa506ce6381495824b9a7bca43e711ccea4b1c0d",
            "claimStatus": "completed",
        },
        "indexVectorDigest": INDEX_VECTOR_DIGEST,
        "indexSnapshot": {
            "id": "index-snapshot-3777d920335ed3632cc65b3a18fd3b8d42a6827996e0e0e",
            "status": "ready",
            "retrievalMode": "hybrid",
            "candidateDigest": "b71281b4732b8660447e25f36985fca7b0a249db205a67432137f664c86d626d",
            "digest": "3777d920335ed3632cc65b3a18fd3b8d42a6827996e0e0e1eb5cd61e83012369",
        },
        "indexSnapshotDigest": "3777d920335ed3632cc65b3a18fd3b8d42a6827996e0e0e1eb5cd61e83012369",
        "retrieval": retrieval,
        "retrievalDigest": hashlib.sha256(
            json.dumps(retrieval, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "providerEvidence": provider_evidence,
        "zeroNetworkReplayDigest": "5" * 64,
        "allowedWriteLedger": {
            "allowedRoots": [
                str((tmp_path / "knowledge.db").resolve()),
                str((tmp_path / "child-temp").resolve()),
            ],
            "protectedRootWriteCount": 0,
            "unexpectedPathCount": 0,
            "digest": "6" * 64,
        },
    }


def _provider_factory(embeddings_module: Any, calls: list[Any]) -> Any:
    def factory(verified: Any, *, isolated_temp_parent: Path) -> Any:
        delegate = _EvidenceProvider(
            verified,
            isolated_temp_parent=isolated_temp_parent,
            embeddings_module=embeddings_module,
            ordinal=len(calls) + 1,
        )
        evidence_calls: list[tuple[str, ...]] = []

        def embed_with_evidence(values: Iterable[str]) -> Any:
            batch = tuple(values)
            vectors = delegate.embed_documents(batch)
            evidence = copy.deepcopy(delegate.child_evidence)
            evidence["vectorDigest"] = _canonical_digest(vectors)
            evidence_calls.append(batch)
            return vectors, evidence

        model = SimpleNamespace(
            embed=delegate.embed_documents,
            embed_with_evidence=embed_with_evidence,
        )
        provider = embeddings_module.FastEmbedProvider(
            verified,
            model_factory=lambda **_kwargs: model,
            package_version_getter=lambda _name: "0.8.0",
            isolated_temp_parent=isolated_temp_parent,
        )
        provider.child_evidence = delegate.child_evidence
        provider._authority_verified = verified
        provider._authority_temp_parent = isolated_temp_parent
        provider._test_evidence_calls = evidence_calls
        calls.append(provider)
        return provider

    return factory


def _expectation(live: Any, tmp_path: Path) -> tuple[Any, Any, Any, Any, Any]:
    authority = live.LiveEmbeddingAuthority.load()
    model_cache = authority.model_cache_module
    embeddings = authority.embeddings_module
    model_cache, embeddings, manifest, final_result = _authority(
        tmp_path,
        model_cache=model_cache,
        embeddings=embeddings,
    )
    model_cache._verify_promoted_generation = (
        lambda checked_manifest, generation_root: final_result.verified
        if checked_manifest is manifest
        and generation_root == final_result.verified.generation_root
        else (_ for _ in ()).throw(AssertionError("wrong reopen authority"))
    )
    expectation = live.FinalExpectation.from_authority(
        manifest,
        final_result,
        authority,
    )
    validator = lambda verified: verified
    model_cache.validate_verified_generation = validator
    embeddings.validate_verified_generation = validator
    return expectation, model_cache, embeddings, manifest, final_result


def _redirect_authority_sources(
    live: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    original = Path(live.__file__).resolve().parent
    source_root = tmp_path / "bound-authority-source"
    source_root.mkdir()
    for name in ("__init__.py", "model_cache.py", "embeddings.py"):
        (source_root / name).write_bytes((original / name).read_bytes())
    model_path = (source_root / "model_cache.py").resolve()
    embeddings_path = (source_root / "embeddings.py").resolve()
    monkeypatch.setattr(live, "_SOURCE_ROOT", source_root.resolve())
    monkeypatch.setattr(live, "_MODEL_CACHE_PATH", model_path)
    monkeypatch.setattr(live, "_EMBEDDINGS_PATH", embeddings_path)
    return source_root, model_path, embeddings_path


@pytest.mark.model_download
def test_authority_loader_executes_bound_bytes_and_rejects_source_swaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    source_root, model_path, embeddings_path = _redirect_authority_sources(
        live, tmp_path, monkeypatch
    )
    real_compile = builtins.compile
    compile_origins: list[Path] = []
    swap_blocked: list[bool] = []
    ancestor_blocked: list[bool] = []

    def guarded_compile(
        source: object,
        filename: str,
        mode: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        origin = Path(filename)
        if origin in {model_path, embeddings_path}:
            assert isinstance(source, bytes)
            compile_origins.append(origin)
            if origin == model_path:
                swapped_file = source_root / "model-cache-swapped.py"
                try:
                    model_path.rename(swapped_file)
                except OSError:
                    swap_blocked.append(True)
                else:
                    swap_blocked.append(False)
                    swapped_file.rename(model_path)
                swapped_parent = source_root.with_name("authority-source-swapped")
                try:
                    source_root.rename(swapped_parent)
                except OSError:
                    ancestor_blocked.append(True)
                else:
                    ancestor_blocked.append(False)
                    swapped_parent.rename(source_root)
        return real_compile(source, filename, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "compile", guarded_compile)
    monkeypatch.setattr(
        live.importlib.util,
        "spec_from_file_location",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("authority loader attempted a second path-based read")
        ),
    )

    authority = live.LiveEmbeddingAuthority.load()
    try:
        assert compile_origins == [model_path, embeddings_path]
        assert swap_blocked == [True]
        assert ancestor_blocked == [True]
        assert authority.source_digests == {
            "model_cache": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "embeddings": hashlib.sha256(embeddings_path.read_bytes()).hexdigest(),
        }

        original_bytes = embeddings_path.read_bytes()
        embeddings_path.write_bytes(original_bytes + b"\n")
        with pytest.raises(live.EmbeddingLiveError):
            authority.assert_valid()
        embeddings_path.write_bytes(original_bytes)
        authority.assert_valid()

        backup = source_root / "embeddings-original.py"
        embeddings_path.rename(backup)
        embeddings_path.write_bytes(original_bytes)
        try:
            with pytest.raises(live.EmbeddingLiveError):
                authority.assert_valid()
        finally:
            embeddings_path.unlink()
            backup.rename(embeddings_path)
    finally:
        authority.close()

    hardlink = source_root / "model-cache-hardlink.py"
    os.link(model_path, hardlink)
    try:
        with pytest.raises(live.EmbeddingLiveError):
            live.LiveEmbeddingAuthority.load()
    finally:
        hardlink.unlink()


def test_authority_loader_uses_owned_unique_namespace_and_ignores_canonical_poison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path
    live = _live_module()
    poison_model = ModuleType("course_helper.model_cache")
    poison_embeddings = ModuleType("course_helper.embeddings")
    monkeypatch.setitem(sys.modules, poison_model.__name__, poison_model)
    monkeypatch.setitem(sys.modules, poison_embeddings.__name__, poison_embeddings)
    source_root = Path(live.__file__).resolve().parent
    neighbor_name: str | None = None

    authority = live.LiveEmbeddingAuthority.load()
    try:
        authority.assert_valid()
        assert authority.package_name.startswith("_course_helper_embedding_live_")
        assert authority.model_cache_module.__name__ == (
            authority.package_name + ".model_cache"
        )
        assert authority.embeddings_module.__name__ == (
            authority.package_name + ".embeddings"
        )
        assert Path(authority.model_cache_module.__file__).resolve() == (
            source_root / "model_cache.py"
        )
        assert Path(authority.embeddings_module.__file__).resolve() == (
            source_root / "embeddings.py"
        )
        assert (
            authority.embeddings_module.VerifiedModelCache
            is authority.model_cache_module.VerifiedModelCache
        )
        assert sys.modules["course_helper.model_cache"] is poison_model
        assert sys.modules["course_helper.embeddings"] is poison_embeddings
        original_origin = authority.embeddings_module.__file__
        authority.embeddings_module.__file__ = str(source_root / "model_cache.py")
        with pytest.raises(live.EmbeddingLiveError):
            authority.assert_valid()
        authority.embeddings_module.__file__ = original_origin
        authority.assert_valid()
        neighbor_name = authority.package_name + ".foreign"
        neighbor = ModuleType(neighbor_name)
        sys.modules[neighbor_name] = neighbor
    finally:
        owned = set(authority.owned_module_names)
        authority.close()

    assert owned.isdisjoint(sys.modules)
    assert neighbor_name is not None and neighbor_name in sys.modules
    assert sys.modules["course_helper.model_cache"] is poison_model
    assert sys.modules["course_helper.embeddings"] is poison_embeddings
    sys.modules.pop(neighbor_name, None)

    token = "ab" * 16
    collision_name = "_course_helper_embedding_live_" + token
    collision = ModuleType(collision_name)
    sys.modules[collision_name] = collision
    monkeypatch.setattr(live.secrets, "token_hex", lambda _size: token)
    try:
        with pytest.raises(live.EmbeddingLiveError):
            live.LiveEmbeddingAuthority.load()
        assert sys.modules[collision_name] is collision
    finally:
        sys.modules.pop(collision_name, None)


@pytest.mark.model_download
def test_final_expectation_binds_exact_authority_reopens_and_rejects_wrong_types(
    tmp_path: Path,
) -> None:
    live = _live_module()
    authority = live.LiveEmbeddingAuthority.load()
    second_authority: Any | None = None
    try:
        model_cache, embeddings, manifest, final_result = _authority(
            tmp_path,
            model_cache=authority.model_cache_module,
            embeddings=authority.embeddings_module,
        )
        reopened: list[tuple[Any, Path]] = []

        def reopen(checked_manifest: Any, generation_root: Path) -> Any:
            reopened.append((checked_manifest, generation_root))
            return final_result.verified

        model_cache._verify_promoted_generation = reopen
        expectation = live.FinalExpectation.from_authority(
            manifest,
            final_result,
            authority,
        )
        assert reopened == [
            (manifest, final_result.verified.generation_root)
        ]
        assert expectation.authority is authority
        assert expectation.model_cache_source_digest == (
            authority.source_digests["model_cache"]
        )
        assert expectation.embeddings_source_digest == (
            authority.source_digests["embeddings"]
        )
        assert expectation.manifest_digest == manifest.aggregate_digest
        assert expectation.cache_digest == final_result.verified.cache_digest
        assert expectation.runtime_digest == final_result.verified.runtime_digest
        assert expectation.wheel_set_digest == final_result.verified.wheel_set_digest
        assert expectation.generation_digest == final_result.verified.generation_digest

        with pytest.raises(live.EmbeddingLiveError):
            live.FinalExpectation.from_authority(
                manifest,
                replace(final_result, verified=SimpleNamespace(**vars(final_result.verified))),
                authority,
            )

        baseline_verified = final_result.verified
        forged_values = (
            ("specific_model_path", tmp_path / "forged-model"),
            ("cache_digest", "0" * 64),
            ("generation_root", tmp_path / "forged-generation"),
            ("runtime_root", tmp_path / "forged-runtime"),
            ("runtime_digest", "0" * 64),
            ("wheel_set_digest", "0" * 64),
            ("generation_digest", "0" * 64),
        )
        for field, value in forged_values:
            forged_verified = replace(baseline_verified, **{field: value})
            forged_final = replace(final_result, verified=forged_verified)
            model_cache._verify_promoted_generation = (
                lambda _manifest, _generation_root: baseline_verified
            )
            with pytest.raises(live.EmbeddingLiveError):
                live.FinalExpectation.from_authority(
                    manifest,
                    forged_final,
                    authority,
                )
        model_cache._verify_promoted_generation = reopen

        for field in (
            "modelManifestDigest",
            "cacheDigest",
            "runtimeDigest",
            "wheelSetDigest",
            "generationDigest",
            "childEvidenceDigest",
        ):
            verification = copy.deepcopy(final_result.verification)
            if field == "childEvidenceDigest":
                verification[field] = "0" * 64
            else:
                verification["pipeline"][field] = "0" * 64
            forged = replace(final_result, verification=verification)
            with pytest.raises(live.EmbeddingLiveError):
                live.FinalExpectation.from_authority(manifest, forged, authority)

        for target in ("verification", "pipeline"):
            verification = copy.deepcopy(final_result.verification)
            if target == "verification":
                verification["extra"] = True
            else:
                verification["pipeline"]["extra"] = True
            forged = replace(final_result, verification=verification)
            with pytest.raises(live.EmbeddingLiveError):
                live.FinalExpectation.from_authority(manifest, forged, authority)

        verification = copy.deepcopy(final_result.verification)
        verification["pipelineDigest"] = "0" * 64
        with pytest.raises(live.EmbeddingLiveError):
            live.FinalExpectation.from_authority(
                manifest,
                replace(final_result, verification=verification),
                authority,
            )

        verification = copy.deepcopy(final_result.verification)
        verification["childEvidenceDigest"] = "0" * 64
        verification["pipeline"]["childEvidenceDigest"] = "0" * 64
        verification["pipelineDigest"] = _canonical_digest(verification["pipeline"])
        with pytest.raises(live.EmbeddingLiveError):
            live.FinalExpectation.from_authority(
                manifest,
                replace(final_result, verification=verification),
                authority,
            )

        for origins in (
            [{}],
            [final_origins()[0]],
            [*final_origins(), final_origins()[0]],
        ):
            verification = copy.deepcopy(final_result.verification)
            verification["childLoadedOrigins"] = origins
            verification["providerOrigins"] = origins
            verification["pipeline"]["childLoadedOrigins"] = origins
            verification["pipeline"]["providerEvidence"]["providerOrigins"] = origins
            verification["childEvidenceDigest"] = _canonical_digest(
                verification["pipeline"]["providerEvidence"]
            )
            verification["pipeline"]["childEvidenceDigest"] = verification[
                "childEvidenceDigest"
            ]
            verification["pipelineDigest"] = _canonical_digest(
                verification["pipeline"]
            )
            forged = replace(final_result, verification=verification)
            with pytest.raises(live.EmbeddingLiveError):
                live.FinalExpectation.from_authority(manifest, forged, authority)

        second_authority = live.LiveEmbeddingAuthority.load()
        second_model, second_embeddings, second_manifest, second_final = _authority(
            tmp_path / "cross-namespace",
            model_cache=second_authority.model_cache_module,
            embeddings=second_authority.embeddings_module,
        )
        second_model._verify_promoted_generation = (
            lambda _manifest, _generation_root: second_final.verified
        )
        with pytest.raises(live.EmbeddingLiveError):
            live.FinalExpectation.from_authority(
                second_manifest,
                second_final,
                authority,
            )

        providers: list[Any] = []
        temp_parent = tmp_path / "provider-temp"
        temp_parent.mkdir()
        validator = lambda verified: verified
        model_cache.validate_verified_generation = validator
        embeddings.validate_verified_generation = validator
        assert not hasattr(authority, "create_provider")
        with pytest.raises(TypeError):
            authority.provider_session(
                expectation,
                isolated_temp_parent=temp_parent,
                provider_factory=_provider_factory(embeddings, providers),
            )
        with authority._provider_session_for_test(
            expectation,
            isolated_temp_parent=temp_parent,
            provider_factory=_provider_factory(embeddings, providers),
        ) as provider:
            assert type(provider) is embeddings.FastEmbedProvider
            assert type(provider.identity) is embeddings.EmbeddingProviderIdentity

        with pytest.raises(live.EmbeddingLiveError):
            with authority._provider_session_for_test(
                expectation,
                isolated_temp_parent=temp_parent,
                provider_factory=lambda verified, **kwargs: _EvidenceProvider(
                    verified,
                    isolated_temp_parent=kwargs["isolated_temp_parent"],
                    embeddings_module=embeddings,
                    ordinal=8,
                ),
            ):
                pass

        def wrong_identity_factory(verified: Any, *, isolated_temp_parent: Path) -> Any:
            exact = _provider_factory(embeddings, [])(
                verified,
                isolated_temp_parent=isolated_temp_parent,
            )
            exact.identity = SimpleNamespace(**vars(exact.identity))
            return exact

        with pytest.raises(live.EmbeddingLiveError):
            with authority._provider_session_for_test(
                expectation,
                isolated_temp_parent=temp_parent,
                provider_factory=wrong_identity_factory,
            ):
                pass
    finally:
        if second_authority is not None:
            second_authority.close()
        authority.close()


def test_final_expectation_rejects_resigned_actual_origin_and_vector_mismatch(
    tmp_path: Path,
) -> None:
    live = _live_module()
    authority = live.LiveEmbeddingAuthority.load()
    try:
        model_cache, _embeddings, manifest, final_result = _authority(
            tmp_path,
            model_cache=authority.model_cache_module,
            embeddings=authority.embeddings_module,
        )
        model_cache._verify_promoted_generation = (
            lambda _manifest, _generation_root: final_result.verified
        )

        wrong_origins = final_origins()
        wrong_origins[0]["sha256"] = "0" * 64
        verification = copy.deepcopy(final_result.verification)
        verification["childLoadedOrigins"] = wrong_origins
        verification["providerOrigins"] = wrong_origins
        verification["pipeline"]["childLoadedOrigins"] = wrong_origins
        verification["pipeline"]["providerEvidence"]["providerOrigins"] = (
            wrong_origins
        )
        verification["childEvidenceDigest"] = _canonical_digest(
            verification["pipeline"]["providerEvidence"]
        )
        verification["pipeline"]["childEvidenceDigest"] = verification[
            "childEvidenceDigest"
        ]
        verification["pipelineDigest"] = _canonical_digest(
            verification["pipeline"]
        )
        with pytest.raises(live.EmbeddingLiveError):
            live.FinalExpectation.from_authority(
                manifest,
                replace(final_result, verification=verification),
                authority,
            )

        verification = copy.deepcopy(final_result.verification)
        verification["pipeline"]["providerEvidence"]["pythonIsolation"][
            "evidenceDigest"
        ] = "0" * 64
        verification["childEvidenceDigest"] = _canonical_digest(
            verification["pipeline"]["providerEvidence"]
        )
        verification["pipeline"]["childEvidenceDigest"] = verification[
            "childEvidenceDigest"
        ]
        verification["pipelineDigest"] = _canonical_digest(
            verification["pipeline"]
        )
        with pytest.raises(live.EmbeddingLiveError):
            live.FinalExpectation.from_authority(
                manifest,
                replace(final_result, verification=verification),
                authority,
            )
    finally:
        authority.close()


@pytest.mark.model_download
def test_final_expectation_detaches_evidence_before_bound_context_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    authority = live.LiveEmbeddingAuthority.load()
    try:
        model_cache, _embeddings, manifest, final_result = _authority(
            tmp_path,
            model_cache=authority.model_cache_module,
            embeddings=authority.embeddings_module,
        )
        model_cache._verify_promoted_generation = (
            lambda _manifest, _generation_root: final_result.verified
        )
        original_hold = model_cache._hold_regular_file_handles

        @contextmanager
        def mutating_hold(*args: Any, **kwargs: Any) -> Any:
            with original_hold(*args, **kwargs) as handles:
                yield handles
            final_result.verification["extra-after-validation"] = True

        monkeypatch.setattr(
            model_cache,
            "_hold_regular_file_handles",
            mutating_hold,
        )
        expectation = live.FinalExpectation.from_authority(
            manifest,
            final_result,
            authority,
        )
        assert "extra-after-validation" in final_result.verification
        assert "extra-after-validation" not in expectation.verification_evidence
        assert authority._expectation_verification(expectation) == (
            expectation.verification_evidence
        )
    finally:
        authority.close()


@pytest.mark.model_download
def test_provider_session_holds_generation_files_through_post_verification(
    tmp_path: Path,
) -> None:
    live = _live_module()
    authority = live.LiveEmbeddingAuthority.load()
    try:
        model_cache, embeddings, manifest, final_result = _authority(
            tmp_path,
            model_cache=authority.model_cache_module,
            embeddings=authority.embeddings_module,
        )
        runtime_file = (
            final_result.verified.runtime_root / "fastembed" / "__init__.py"
        )
        recomputed: list[str] = []

        def reopen(_manifest: Any, _generation_root: Path) -> Any:
            recomputed.append(hashlib.sha256(runtime_file.read_bytes()).hexdigest())
            return final_result.verified

        model_cache._verify_promoted_generation = reopen
        expectation = live.FinalExpectation.from_authority(
            manifest,
            final_result,
            authority,
        )
        validator = lambda verified: verified
        model_cache.validate_verified_generation = validator
        embeddings.validate_verified_generation = validator
        providers: list[Any] = []
        temp_parent = tmp_path / "provider-session-temp"
        temp_parent.mkdir()
        swapped = runtime_file.with_name("__init__.swapped.py")
        injected = runtime_file.parent / "injected.py"
        original = runtime_file.read_bytes()

        with authority._provider_session_for_test(
            expectation,
            isolated_temp_parent=temp_parent,
            provider_factory=_provider_factory(embeddings, providers),
        ) as provider:
            vector, evidence = provider.embed_query_with_evidence("RFM")
            assert len(vector) == 512
            assert evidence["providerOrigins"] == final_origins()
            with pytest.raises(OSError):
                runtime_file.rename(swapped)
            with pytest.raises(OSError):
                runtime_file.write_bytes(b"tampered during inference\n")
            try:
                injected.write_bytes(b"transient unverified module\n")
            except OSError:
                pass
            else:
                injected.unlink()
                pytest.fail("generation session allowed a transient new file")
            assert not injected.exists()

        assert recomputed == [
            hashlib.sha256(original).hexdigest(),
            hashlib.sha256(original).hexdigest(),
            hashlib.sha256(original).hexdigest(),
        ]
        runtime_file.rename(swapped)
        swapped.rename(runtime_file)
        restored_new = runtime_file.parent / "post-session-restored.tmp"
        restored_new.write_bytes(b"restored")
        restored_new.unlink()
    finally:
        authority.close()


@pytest.mark.model_download
def test_live_callback_binds_unique_verified_type_and_two_fresh_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    monkeypatch.setitem(sys.modules, "course_helper.model_cache", ModuleType("poison"))
    monkeypatch.setitem(sys.modules, "course_helper.embeddings", ModuleType("poison"))
    expectation, model_cache, embeddings, manifest, final_result = _expectation(
        live, tmp_path
    )
    with pytest.raises(live.EmbeddingLiveError):
        live.FinalExpectation.from_authority(
            manifest,
            SimpleNamespace(**vars(final_result)),
            expectation.authority,
        )

    wrong_provider_temp = tmp_path / "wrong-provider-temp"
    wrong_provider_temp.mkdir()

    def wrong_provider_factory(
        verified: Any, *, isolated_temp_parent: Path
    ) -> _EvidenceProvider:
        return _EvidenceProvider(
            verified,
            isolated_temp_parent=isolated_temp_parent,
            embeddings_module=embeddings,
            ordinal=9,
        )

    with pytest.raises(live.EmbeddingLiveError):
        live._run_fresh_pipeline_for_test(
            expectation,
            database_path=tmp_path / "wrong-provider.db",
            temp_parent=wrong_provider_temp,
            provider_factory=wrong_provider_factory,
            clock=lambda: PIPELINE_NOW,
        )

    wrong_identity_temp = tmp_path / "wrong-identity-temp"
    wrong_identity_temp.mkdir()
    wrong_identity_providers: list[Any] = []
    exact_factory = _provider_factory(embeddings, wrong_identity_providers)

    def wrong_identity_factory(verified: Any, *, isolated_temp_parent: Path) -> Any:
        provider = exact_factory(
            verified,
            isolated_temp_parent=isolated_temp_parent,
        )
        provider.identity = SimpleNamespace(**vars(provider.identity))
        return provider

    with pytest.raises(live.EmbeddingLiveError):
        live._run_fresh_pipeline_for_test(
            expectation,
            database_path=tmp_path / "wrong-identity.db",
            temp_parent=wrong_identity_temp,
            provider_factory=wrong_identity_factory,
            clock=lambda: PIPELINE_NOW,
        )

    providers: list[Any] = []
    factory = _provider_factory(embeddings, providers)
    first_temp = tmp_path / "child-temp-a"
    second_temp = tmp_path / "child-temp-b"
    first_temp.mkdir()
    second_temp.mkdir()

    first = live._run_fresh_pipeline_for_test(
        expectation,
        database_path=tmp_path / "pipeline-a.db",
        temp_parent=first_temp,
        provider_factory=factory,
        clock=lambda: PIPELINE_NOW,
    )
    second = live._run_fresh_pipeline_for_test(
        expectation,
        database_path=tmp_path / "pipeline-b.db",
        temp_parent=second_temp,
        provider_factory=factory,
        clock=lambda: PIPELINE_NOW,
    )

    assert len(providers) == 2
    assert all(
        type(provider) is embeddings.FastEmbedProvider
        and provider._authority_verified is final_result.verified
        for provider in providers
    )
    assert [provider._authority_temp_parent for provider in providers] == [
        first_temp,
        second_temp,
    ]
    assert all(
        len(provider._test_evidence_calls) == 1
        and provider._test_evidence_calls[0][-1] == "RFM"
        for provider in providers
    )
    for key in (
        "fixtureDigest",
        "indexVectorDigest",
        "indexSnapshotDigest",
        "retrievalDigest",
        "zeroNetworkReplayDigest",
    ):
        assert first[key] == second[key]
    for key in ("processId", "challengeDigest", "tempTokenDigest"):
        assert first["providerEvidence"][key] != second["providerEvidence"][key]
    assert first["generationDigest"] == final_result.verified.generation_digest
    assert first["childLoadedOrigins"] == final_origins()
    assert len(first["childEvidenceDigest"]) == 64
    assert first["allowedWriteLedger"]["allowedRoots"] != second[
        "allowedWriteLedger"
    ]["allowedRoots"]


def test_final_verification_callback_needs_no_fabricated_final_objects(
    tmp_path: Path,
) -> None:
    live = _live_module()
    authority = live.LiveEmbeddingAuthority.load()
    try:
        model_cache = authority.model_cache_module
        embeddings = authority.embeddings_module
        _model_cache, _embeddings, manifest, final_result = _authority(
            tmp_path,
            model_cache=model_cache,
            embeddings=embeddings,
        )
        validator = lambda verified: verified
        model_cache.validate_verified_generation = validator
        embeddings.validate_verified_generation = validator
        providers: list[Any] = []
        temp_parent = tmp_path / "callback-child-temp"
        temp_parent.mkdir()

        verification = live._run_final_verification_callback_for_test(
            authority,
            manifest,
            final_result.verified,
            database_path=tmp_path / "callback.db",
            temp_parent=temp_parent,
            provider_factory=_provider_factory(embeddings, providers),
            clock=lambda: PIPELINE_NOW,
        )

        assert set(verification) == {
            "generationDigest",
            "childEvidenceDigest",
            "childLoadedOrigins",
            "pipeline",
            "pipelineDigest",
        }
        assert "providerOrigins" not in verification
        assert verification["generationDigest"] == final_result.verified.generation_digest
        assert verification["childEvidenceDigest"] == verification["pipeline"][
            "childEvidenceDigest"
        ]
        assert verification["childLoadedOrigins"] == final_origins()
        assert verification["pipelineDigest"] == _canonical_digest(
            verification["pipeline"]
        )
        assert len(providers) == 1
        assert "provider_factory" not in inspect.signature(
            live.run_final_verification_callback
        ).parameters

        with pytest.raises(live.EmbeddingLiveError):
            live._run_final_verification_callback_for_test(
                authority,
                SimpleNamespace(**vars(manifest)),
                final_result.verified,
                database_path=tmp_path / "wrong-manifest.db",
                temp_parent=temp_parent,
                provider_factory=_provider_factory(embeddings, []),
                clock=lambda: PIPELINE_NOW,
            )
    finally:
        authority.close()


@pytest.mark.model_download
def test_fresh_pipeline_publishes_claims_completes_and_queries_fixed_fixture(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, _model_cache, embeddings, _manifest, _final_result = _expectation(
        live, tmp_path
    )
    providers: list[Any] = []
    temp_parent = tmp_path / "child-temp"
    temp_parent.mkdir()
    database_path = tmp_path / "knowledge.db"

    result = live._run_fresh_pipeline_for_test(
        expectation,
        database_path=database_path,
        temp_parent=temp_parent,
        provider_factory=_provider_factory(embeddings, providers),
        clock=lambda: PIPELINE_NOW,
    )

    assert result["schemaVersion"] == 1
    assert result["fixture"]["schemaId"] == "embedding-live-synthetic-v1"
    assert result["fixtureDigest"] == hashlib.sha256(
        json.dumps(
            result["fixture"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert result["publication"]["status"] == "published"
    assert result["publication"]["cardVersionId"] == "card-fixture"
    assert result["publication"]["contentDigest"] == (
        "40cb14e1e287eaf45e846d787ff877389cc21a0f5e198fb529d0c70ea8b6ac09"
    )
    assert result["outbox"]["claimStatus"] == "completed"
    assert result["outbox"]["requestDigest"] == (
        "a49bb364505f9e7284ad47aed7f6413c61fe64f52b370a84dceb9038da1bc978"
    )
    assert result["outbox"]["contentDigest"] == (
        "69f2190c01d6f7551b11c438f9a4e74193e68d9a8960d7c9940d9eabcde3f890"
    )
    assert result["outbox"]["claimDigest"] == (
        "82a63a5af42d69315f810e0ffa506ce6381495824b9a7bca43e711ccea4b1c0d"
    )
    assert result["indexSnapshot"]["status"] == "ready"
    assert result["indexSnapshot"]["retrievalMode"] == "hybrid"
    assert result["indexSnapshot"]["candidateDigest"] == (
        "b71281b4732b8660447e25f36985fca7b0a249db205a67432137f664c86d626d"
    )
    assert result["indexSnapshot"]["digest"] == (
        "3777d920335ed3632cc65b3a18fd3b8d42a6827996e0e0e1eb5cd61e83012369"
    )
    assert result["indexVectorDigest"] == (
        "eb64ce3d83e7c1cd395485f4451af923866d5faef84e01aed240f14560dfe3f0"
    )
    assert result["providerEvidence"]["vectorDigest"] != result[
        "indexVectorDigest"
    ]
    assert result["providerEvidence"]["pythonIsolation"]["scope"] == (
        "trusted-hash-locked-cpython-runtime"
    )
    assert result["retrieval"]["snapshotDigest"] == result["indexSnapshot"]["digest"]
    assert result["retrieval"]["queryDigest"] == (
        "b461dfa6fbbd8cea4af99936d42610f77517bce548fa9f3f1e73cc1c65b5d994"
    )
    assert result["retrieval"]["rrfK"] == 60
    assert len(result["retrieval"]["hits"]) >= 1
    assert result["retrieval"]["hits"][0]["ftsRank"] >= 1
    assert result["retrieval"]["hits"][0]["semanticRank"] >= 1
    assert result["providerEvidence"]["jobScope"] == "windows-job-kill-on-close"
    ledger = result["allowedWriteLedger"]
    assert ledger["nativeGlobalCoverage"] == "not-certified"
    assert ledger["scope"] == "pipeline-declared-roots"
    assert set(ledger["allowedRoots"]) == {
        str(database_path.resolve()),
        str(temp_parent.resolve()),
    }
    assert "Course_AIProduct" not in json.dumps(result, sort_keys=True)


def _resign(receipt: dict[str, Any]) -> None:
    receipt.pop("receiptDigest", None)
    receipt["receiptDigest"] = hashlib.sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _set_path(value: dict[str, Any], path: tuple[str, ...], replacement: str) -> None:
    target: dict[str, Any] = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


@pytest.mark.model_download
def test_receipt_requires_final_result_and_rejects_every_resigned_digest_tamper(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, _model_cache, _embeddings, _manifest, final_result = _expectation(
        live, tmp_path
    )
    pipeline = final_result.verification["pipeline"]
    receipt = live.build_receipt(
        expectation,
        final_result=final_result,
        pipeline_evidence=pipeline,
        started_at=NOW,
        finished_at=NOW,
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(TypeError):
        live.validate_receipt(receipt_path, expectation=expectation)
    assert live.validate_receipt(
        receipt_path,
        expectation=expectation,
        expected_final_result=final_result,
    ) == receipt

    mutations = (
        ("cache", ("cacheDigest",)),
        ("runtime", ("runtime", "runtimeDigest")),
        ("wheel", ("runtime", "wheelSetDigest")),
        ("generation", ("runtime", "generationDigest")),
        ("index", ("indexSnapshot", "digest")),
        ("retrieval", ("retrieval", "queryDigest")),
        ("replay", ("zeroNetworkReplayDigest",)),
    )
    for label, path in mutations:
        forged = copy.deepcopy(receipt)
        _set_path(forged, path, hashlib.sha256(label.encode("ascii")).hexdigest())
        _resign(forged)
        receipt_path.write_text(json.dumps(forged), encoding="utf-8")
        with pytest.raises(live.EmbeddingLiveError) as caught:
            live.validate_receipt(
                receipt_path,
                expectation=expectation,
                expected_final_result=final_result,
            )
        assert caught.value.code == "EMBEDDING_MODEL_RECEIPT_INVALID"


@pytest.mark.model_download
def test_atomic_seal_replace_failure_preserves_prior_then_success_reopens_and_validates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, _model_cache, _embeddings, _manifest, final_result = _expectation(
        live, tmp_path
    )
    receipt = live.build_receipt(
        expectation,
        final_result=final_result,
        pipeline_evidence=final_result.verification["pipeline"],
        started_at=NOW,
        finished_at=NOW,
    )
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed" / "receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-sealed-receipt"
    sealed.write_bytes(prior)
    original_replace = os.replace

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(live.os, "replace", fail_replace)
    with pytest.raises(live.EmbeddingLiveError):
        live.seal_receipt(
            temporary,
            sealed,
            expectation=expectation,
            expected_final_result=final_result,
            quarantine_root=quarantine,
        )
    assert sealed.read_bytes() == prior

    monkeypatch.setattr(live.os, "replace", original_replace)
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    reopened = live.seal_receipt(
        temporary,
        sealed,
        expectation=expectation,
        expected_final_result=final_result,
        quarantine_root=quarantine,
    )
    assert reopened == live.validate_receipt(
        sealed,
        expectation=expectation,
        expected_final_result=final_result,
    )


@pytest.mark.model_download
def test_deferred_seal_can_rollback_post_return_validation_failure(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed" / "receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-sealed-survives-post-return-failure"
    sealed.write_bytes(prior)

    transaction = live.seal_receipt(
        temporary,
        sealed,
        expectation,
        final_result,
        quarantine,
        defer_commit=True,
    )
    assert sealed.read_bytes() != prior
    transaction.rollback()
    assert sealed.read_bytes() == prior

    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    transaction = live.seal_receipt(
        temporary,
        sealed,
        expectation,
        final_result,
        quarantine,
        defer_commit=True,
    )
    revalidated = live.validate_receipt(sealed, expectation, final_result)
    assert transaction.commit() == revalidated
    assert transaction.finalize() == revalidated


@pytest.mark.model_download
def test_deferred_commit_blocks_same_identity_same_size_postvalidation_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed" / "receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-must-remain-recoverable"
    sealed.write_bytes(prior)
    transaction = live.seal_receipt(
        temporary,
        sealed,
        expectation,
        final_result,
        quarantine,
        defer_commit=True,
    )
    original = live._validate_receipt_bytes
    attempts: list[str] = []

    def tamper_after_validation(*args: object, **kwargs: object) -> object:
        validated = original(*args, **kwargs)
        try:
            with sealed.open("r+b") as stream:
                first = stream.read(1)
                stream.seek(0)
                stream.write(b"[" if first != b"[" else b"{")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            attempts.append("blocked")
        else:
            attempts.append("written")
        return validated

    monkeypatch.setattr(live, "_validate_receipt_bytes", tamper_after_validation)
    committed = transaction.commit()
    finalized = transaction.finalize()
    assert attempts == ["blocked", "blocked"]
    assert finalized == committed
    assert live.validate_receipt(sealed, expectation, final_result) == committed
    assert not list(quarantine.glob(".receipt-prior-*.bak"))


@pytest.mark.model_download
def test_deferred_final_verify_failure_releases_noexcept_then_recovers_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed/receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-final-verify-failure"
    sealed.write_bytes(prior)
    transaction = live.seal_receipt(
        temporary, sealed, expectation, final_result, quarantine, defer_commit=True
    )
    transaction.commit()

    def fail_verify(_self: object) -> None:
        raise live.EmbeddingLiveError("EMBEDDING_MODEL_RECEIPT_INVALID")

    monkeypatch.setattr(live._HeldReceiptLease, "verify_held", fail_verify)
    with pytest.raises(live.EmbeddingLiveError):
        transaction.finalize()
    assert sealed.read_bytes() == prior


@pytest.mark.model_download
def test_compare_mismatch_rollback_uses_recovery_after_same_size_prior_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed/receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-before-same-size-tamper"
    sealed.write_bytes(prior)
    original_replace = live._atomic_receipt_replace

    def tamper_prior_then_move(source: Path, destination: Path) -> None:
        if source == sealed:
            source.write_bytes(b"X" * len(prior))
        original_replace(source, destination)

    monkeypatch.setattr(live, "_atomic_receipt_replace", tamper_prior_then_move)
    transaction = live.seal_receipt(
        temporary, sealed, expectation, final_result, quarantine, defer_commit=True
    )
    transaction.commit()
    transaction.rollback()
    assert sealed.read_bytes() == prior


@pytest.mark.model_download
def test_backup_same_size_tamper_forces_failure_and_recovers_original_prior(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed/receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-before-backup-tamper"
    sealed.write_bytes(prior)
    transaction = live.seal_receipt(
        temporary, sealed, expectation, final_result, quarantine, defer_commit=True
    )
    backup = next(quarantine.glob(".receipt-prior-*.bak"))
    backup.write_bytes(b"Z" * len(prior))
    transaction.commit()
    with pytest.raises(live.EmbeddingLiveError):
        transaction.finalize()
    assert sealed.read_bytes() == prior


@pytest.mark.model_download
def test_destination_parent_swap_is_denied_while_held_then_prior_recovers(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed/receipt.json"
    sealed.parent.mkdir()
    prior = b"prior-parent-swap"
    sealed.write_bytes(prior)
    transaction = live.seal_receipt(
        temporary, sealed, expectation, final_result, quarantine, defer_commit=True
    )
    transaction.commit()
    with pytest.raises(OSError):
        os.replace(sealed.parent, tmp_path / "swapped-parent")
    transaction.rollback()
    assert sealed.read_bytes() == prior


@pytest.mark.model_download
def test_finalize_native_close_fault_is_noexcept_after_all_prior_decisions(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed = tmp_path / "sealed/receipt.json"
    sealed.parent.mkdir()
    sealed.write_bytes(b"prior-close-fault")
    transaction = live.seal_receipt(
        temporary, sealed, expectation, final_result, quarantine, defer_commit=True
    )
    committed = transaction.commit()
    manager = transaction._lease._manager

    class CloseFault:
        @staticmethod
        def __exit__(*args: object) -> object:
            manager.__exit__(*args)
            raise OSError("injected CloseHandle failure")

    transaction._lease._manager = CloseFault()
    assert transaction.finalize() == committed
    assert live.validate_receipt(sealed, expectation, final_result) == committed


def _receipt_case(live: Any, tmp_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    expectation, _model_cache, _embeddings, _manifest, final_result = _expectation(
        live, tmp_path
    )
    receipt = live.build_receipt(
        expectation,
        final_result=final_result,
        pipeline_evidence=final_result.verification["pipeline"],
        started_at=NOW,
        finished_at=NOW,
    )
    return expectation, final_result, receipt


@pytest.mark.model_download
def test_receipt_binds_exact_result_verification_pipeline_and_returns_detached_copy(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    pipeline = final_result.verification["pipeline"]

    with pytest.raises(live.EmbeddingLiveError):
        live.build_receipt(
            expectation,
            final_result=replace(final_result),
            pipeline_evidence=pipeline,
            started_at=NOW,
            finished_at=NOW,
        )
    with pytest.raises(live.EmbeddingLiveError):
        live.build_receipt(
            expectation,
            final_result=final_result,
            pipeline_evidence=copy.deepcopy(pipeline),
            started_at=NOW,
            finished_at=NOW,
        )

    receipt["model"]["id"] = "detached-mutation"
    rebuilt = live.build_receipt(
        expectation,
        final_result=final_result,
        pipeline_evidence=pipeline,
        started_at=NOW,
        finished_at=NOW,
    )
    assert rebuilt["model"]["id"] == "BAAI/bge-small-zh-v1.5"

    pipeline["retrieval"]["rrfK"] = 61
    with pytest.raises(live.EmbeddingLiveError):
        live.build_receipt(
            expectation,
            final_result=final_result,
            pipeline_evidence=pipeline,
            started_at=NOW,
            finished_at=NOW,
        )


@pytest.mark.model_download
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_receipt_rejects_nonfinite_json_numbers(tmp_path: Path, token: str) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    path = tmp_path / "receipt.json"
    raw = json.dumps(receipt)
    raw = raw.replace(str(2 / 61), token, 1)
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(live.EmbeddingLiveError):
        live.validate_receipt(path, expectation, final_result)


@pytest.mark.model_download
def test_receipt_rejects_nested_duplicate_key_oversize_and_bool_as_int(
    tmp_path: Path,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    path = tmp_path / "receipt.json"

    duplicate = json.dumps(receipt).replace(
        '"model": {', '"model": {"id": "duplicate", ', 1
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(live.EmbeddingLiveError):
        live.validate_receipt(path, expectation, final_result)

    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(live.EmbeddingLiveError):
        live.validate_receipt(path, expectation, final_result)

    forged = copy.deepcopy(receipt)
    forged["model"]["dimension"] = True
    _resign(forged)
    path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(live.EmbeddingLiveError):
        live.validate_receipt(path, expectation, final_result)


@pytest.mark.model_download
@pytest.mark.parametrize(
    ("started", "finished"),
    (
        ("2026-07-17T08:00:00Z", "2026-07-17T08:00:00+00:00"),
        ("2026-07-17T08:00:01+00:00", "2026-07-17T08:00:00+00:00"),
        ("2026-07-17T08:00:00+08:00", "2026-07-17T08:00:00+00:00"),
    ),
)
def test_receipt_rejects_noncanonical_or_reversed_utc_timestamps(
    tmp_path: Path,
    started: str,
    finished: str,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    receipt["startedAt"] = started
    receipt["finishedAt"] = finished
    _resign(receipt)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(live.EmbeddingLiveError):
        live.validate_receipt(path, expectation, final_result)


@pytest.mark.model_download
def test_seal_rejects_temp_hardlink_and_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    sealed_parent = tmp_path / "sealed"
    sealed_parent.mkdir()
    sealed = sealed_parent / "receipt.json"
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    os.link(temporary, quarantine / "receipt-hardlink.tmp")
    with pytest.raises(live.EmbeddingLiveError):
        live.seal_receipt(
            temporary, sealed, expectation, final_result, quarantine
        )
    temporary.unlink()
    target = quarantine / "actual.tmp"
    target.write_text(json.dumps(receipt), encoding="utf-8")
    try:
        temporary.symlink_to(target)
    except OSError:
        # Windows CI commonly lacks symlink privilege. Exercise the same
        # lstat reparse-bit branch deterministically without weakening it.
        temporary.write_text(json.dumps(receipt), encoding="utf-8")
        original_lstat = Path.lstat

        class ReparseStat:
            def __init__(self, delegate: os.stat_result) -> None:
                self._delegate = delegate
                self.st_file_attributes = (
                    getattr(delegate, "st_file_attributes", 0) | 0x400
                )

            def __getattr__(self, name: str) -> object:
                return getattr(self._delegate, name)

        def reparse_lstat(self: Path) -> object:
            result = original_lstat(self)
            return ReparseStat(result) if self == temporary else result

        monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(live.EmbeddingLiveError):
        live.seal_receipt(
            temporary, sealed, expectation, final_result, quarantine
        )


@pytest.mark.model_download
def test_seal_detects_temp_swap_and_restores_original_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed_parent = tmp_path / "sealed"
    sealed_parent.mkdir()
    sealed = sealed_parent / "receipt.json"
    original = live._atomic_receipt_replace

    def swap_then_replace(source: Path, destination: Path) -> None:
        if source == temporary:
            source.unlink()
            source.write_text("{}", encoding="utf-8")
        original(source, destination)

    monkeypatch.setattr(live, "_atomic_receipt_replace", swap_then_replace)
    with pytest.raises(live.EmbeddingLiveError):
        live.seal_receipt(
            temporary, sealed, expectation, final_result, quarantine
        )
    assert not sealed.exists()


@pytest.mark.model_download
def test_seal_postreplace_validation_failure_restores_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed_parent = tmp_path / "sealed"
    sealed_parent.mkdir()
    sealed = sealed_parent / "receipt.json"
    prior = b"verified-prior-by-identity"
    sealed.write_bytes(prior)
    original = live._validate_receipt_bytes
    calls = 0

    def fail_postreplace(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise live.EmbeddingLiveError("EMBEDDING_MODEL_RECEIPT_INVALID")
        return original(*args, **kwargs)

    monkeypatch.setattr(live, "_validate_receipt_bytes", fail_postreplace)
    with pytest.raises(live.EmbeddingLiveError):
        live.seal_receipt(
            temporary, sealed, expectation, final_result, quarantine
        )
    assert sealed.read_bytes() == prior


@pytest.mark.model_download
def test_seal_rollback_failure_keeps_one_identity_verified_prior_in_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _live_module()
    expectation, final_result, receipt = _receipt_case(live, tmp_path)
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    temporary = quarantine / "receipt.tmp"
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    sealed_parent = tmp_path / "sealed"
    sealed_parent.mkdir()
    sealed = sealed_parent / "receipt.json"
    prior = b"prior-survives-rollback-failure"
    sealed.write_bytes(prior)
    original = live._atomic_receipt_replace
    calls = 0

    def fail_candidate_and_rollback(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("injected transaction failure")
        original(source, destination)

    monkeypatch.setattr(live, "_atomic_receipt_replace", fail_candidate_and_rollback)
    with pytest.raises(live.EmbeddingLiveError):
        live.seal_receipt(
            temporary, sealed, expectation, final_result, quarantine
        )
    backups = list(quarantine.glob(".receipt-prior-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == prior
    assert not sealed.exists()
