from __future__ import annotations

import base64
import ctypes
import csv
import hashlib
import importlib
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


def _model_cache_module():
    return importlib.import_module("course_helper.model_cache")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_complete_manifest(root: Path, members: dict[str, bytes]) -> Path:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "manifestId": "bge-small-zh-v1.5-fastembed-0.8.0",
        "package": {
            "name": "fastembed",
            "version": "0.8.0",
            "wheelFilename": "fastembed-0.8.0-py3-none-any.whl",
            "wheelSha256": "40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0",
        },
        "model": {
            "id": "BAAI/bge-small-zh-v1.5",
            "revision": "7999e1d3359715c523056ef9478215996d62a620",
            "artifactRepository": "Qdrant/bge-small-zh-v1.5",
            "artifactRevision": "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
            "dimension": 512,
            "normalized": True,
            "encodingPolicy": "utf8-nfkc-no-prefix",
        },
        "files": [
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, content in sorted(members.items())
        ],
    }
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    path = root / "manifest.json"
    path.write_bytes(_canonical_bytes(payload))
    return path


def test_manifest_requires_every_exact_member_sha256_and_aggregate_digest(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    payload = _pinned_complete_manifest()
    payload["files"][0]["sha256"] = None
    unsigned = dict(payload)
    unsigned.pop("aggregateDigest")
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(_canonical_bytes(payload))

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.load_model_manifest(manifest_path)

    assert caught.value.code == "MODEL_MANIFEST_INCOMPLETE"


def test_verified_cache_rejects_missing_hash_mismatch_and_extra_members(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    members = {"config.json": b"{}", "tokenizer.json": b"tokenizer"}
    manifest = model_cache.ModelManifest(
        schema_version=1,
        manifest_id=model_cache.PINNED_MANIFEST_ID,
        package=model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename="fastembed-0.8.0-py3-none-any.whl",
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
        model=model_cache.ModelIdentity(
            id=model_cache.PINNED_MODEL_ID,
            revision=model_cache.PINNED_MODEL_REVISION,
            artifact_repository=model_cache.PINNED_ARTIFACT_REPOSITORY,
            artifact_revision=model_cache.PINNED_ARTIFACT_REVISION,
            dimension=512,
            normalized=True,
            encoding_policy="utf8-nfkc-no-prefix",
        ),
        files=tuple(
            model_cache.ModelMember(
                path=name,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            for name, content in sorted(members.items())
        ),
        aggregate_digest="a" * 64,
    )
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    for name, content in members.items():
        (cache_root / name).write_bytes(content)
    (cache_root / "unexpected.bin").write_bytes(b"unexpected")

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.verify_loaded_model_cache(manifest, cache_root)

    assert caught.value.code == "MODEL_CACHE_INVENTORY_MISMATCH"

    (cache_root / "unexpected.bin").unlink()
    (cache_root / "config.json").write_bytes(b"tampered")
    with pytest.raises(model_cache.ModelCacheError) as tampered:
        model_cache.verify_loaded_model_cache(manifest, cache_root)
    assert tampered.value.code == "MODEL_CACHE_MEMBER_MISMATCH"


def test_manifest_rejects_path_escape_before_any_cache_read(tmp_path: Path) -> None:
    model_cache = _model_cache_module()
    payload = _pinned_bootstrap_manifest()
    payload["files"][0]["path"] = "../outside.json"
    base = dict(payload)
    base.pop("aggregateDigest")
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(base)).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(_canonical_bytes(payload))

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.load_bootstrap_manifest(manifest_path)

    assert caught.value.code == "MODEL_MANIFEST_PATH_INVALID"


def test_cache_verification_is_zero_network_and_returns_specific_model_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    members = {"config.json": b"{}", "tokenizer.json": b"tokenizer"}
    manifest = model_cache.ModelManifest(
        schema_version=1,
        manifest_id=model_cache.PINNED_MANIFEST_ID,
        package=model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename="fastembed-0.8.0-py3-none-any.whl",
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
        model=model_cache.ModelIdentity(
            id=model_cache.PINNED_MODEL_ID,
            revision=model_cache.PINNED_MODEL_REVISION,
            artifact_repository=model_cache.PINNED_ARTIFACT_REPOSITORY,
            artifact_revision=model_cache.PINNED_ARTIFACT_REVISION,
            dimension=512,
            normalized=True,
            encoding_policy="utf8-nfkc-no-prefix",
        ),
        files=tuple(
            model_cache.ModelMember(
                path=name,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            for name, content in sorted(members.items())
        ),
        aggregate_digest="a" * 64,
    )
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    for name, content in members.items():
        (cache_root / name).write_bytes(content)

    def denied(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cache verification opened a socket")

    monkeypatch.setattr(socket, "socket", denied)
    verified = model_cache.verify_loaded_model_cache(manifest, cache_root)

    assert verified.specific_model_path == cache_root.resolve()
    assert verified.manifest.model.dimension == 512
    assert len(verified.cache_digest) == 64


def _pinned_bootstrap_manifest() -> dict[str, object]:
    revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    identities = (
        ("config.json", 739, "git-blob-sha1", "60938626ad1097a0c1a14be4f8340e32c714a056"),
        ("model_optimized.onnx", 94781076, "lfs-sha256", "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38"),
        ("special_tokens_map.json", 125, "git-blob-sha1", "a8b3208c2884c4efb86e49300fdd3dc877220cdf"),
        ("tokenizer.json", 439125, "git-blob-sha1", "cdb3043fc938fc918c06e66cf704c2ba58f88747"),
        ("tokenizer_config.json", 367, "git-blob-sha1", "3a59388f0fd1bd22dec2ce7902c1be8e1fb84107"),
    )
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "manifestId": "bge-small-zh-v1.5-fastembed-0.8.0",
        "phase": "bootstrap-required",
        "package": {
            "name": "fastembed",
            "version": "0.8.0",
            "wheelFilename": "fastembed-0.8.0-py3-none-any.whl",
            "wheelSize": 116572,
            "wheelSha256": "40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0",
        },
        "model": {
            "id": "BAAI/bge-small-zh-v1.5",
            "revision": "7999e1d3359715c523056ef9478215996d62a620",
            "artifactRepository": "Qdrant/bge-small-zh-v1.5",
            "artifactRevision": revision,
            "dimension": 512,
            "normalized": True,
            "encodingPolicy": "utf8-nfkc-no-prefix",
        },
        "files": [
            {
                "path": path,
                "size": size,
                "sha256": identity if kind == "lfs-sha256" else None,
                "officialIdentity": {"kind": kind, "digest": identity},
                "artifactUrl": (
                    "https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/"
                    f"{revision}/{path}?download=true"
                ),
            }
            for path, size, kind, identity in identities
        ],
        "runtime": {
            "python": "3.12",
            "os": "windows",
            "architecture": "x86_64",
            "wheels": "bootstrap-required",
        },
    }
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return payload


def _pinned_complete_manifest() -> dict[str, object]:
    payload = _pinned_bootstrap_manifest()
    payload["phase"] = "complete"
    for item in payload["files"]:
        if item["sha256"] is None:
            item["sha256"] = "e" * 64
    payload["runtime"]["wheels"] = [
        {
            "name": "fastembed",
            "version": "0.8.0",
            "filename": "fastembed-0.8.0-py3-none-any.whl",
            "size": 116572,
            "sha256": "40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0",
            "artifactUrl": "https://files.pythonhosted.org/packages/aa/bb/fastembed-0.8.0-py3-none-any.whl",
        },
        {
            "name": "onnxruntime",
            "version": "1.23.2",
            "filename": "onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
            "size": 100,
            "sha256": "d" * 64,
            "artifactUrl": "https://files.pythonhosted.org/packages/aa/bb/onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
        },
    ]
    unsigned = dict(payload)
    unsigned.pop("aggregateDigest")
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    return payload


def test_bootstrap_manifest_is_exactly_anchored_but_rejected_as_final(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    payload = _pinned_bootstrap_manifest()
    path = tmp_path / "manifest.json"
    path.write_bytes(_canonical_bytes(payload))

    bootstrap = model_cache.load_bootstrap_manifest(path)
    assert [member.path for member in bootstrap.files] == [
        "config.json",
        "model_optimized.onnx",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ]
    assert bootstrap.phase == "bootstrap-required"

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.load_model_manifest(path)
    assert caught.value.code == "MODEL_MANIFEST_BOOTSTRAP_REQUIRED"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        (lambda p: p.__setitem__("schemaVersion", True), "MODEL_MANIFEST_INVALID"),
        (lambda p: p.__setitem__("manifestId", "other"), "MODEL_MANIFEST_IDENTITY_MISMATCH"),
        (
            lambda p: p["package"].__setitem__("wheelSize", 1),
            "MODEL_MANIFEST_IDENTITY_MISMATCH",
        ),
        (
            lambda p: p["package"].__setitem__("wheelSha256", "f" * 64),
            "MODEL_MANIFEST_IDENTITY_MISMATCH",
        ),
        (
            lambda p: p["files"].pop(),
            "MODEL_MANIFEST_INVENTORY_MISMATCH",
        ),
        (
            lambda p: p["files"][0].__setitem__("size", 740),
            "MODEL_MANIFEST_IDENTITY_MISMATCH",
        ),
    ),
)
def test_bootstrap_manifest_rejects_every_identity_or_inventory_drift(
    tmp_path: Path,
    mutation,
    expected_code: str,
) -> None:
    model_cache = _model_cache_module()
    payload = _pinned_bootstrap_manifest()
    mutation(payload)
    unsigned = dict(payload)
    unsigned.pop("aggregateDigest")
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_bytes(_canonical_bytes(payload))

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.load_bootstrap_manifest(path)
    assert caught.value.code == expected_code


def test_manifest_json_duplicate_keys_fail_closed(tmp_path: Path) -> None:
    model_cache = _model_cache_module()
    path = tmp_path / "manifest.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.load_bootstrap_manifest(path)
    assert caught.value.code == "MODEL_MANIFEST_DUPLICATE_KEY"


def test_final_manifest_requires_complete_binary_win_x64_python312_wheel_lock(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    payload = _pinned_complete_manifest()
    path = tmp_path / "manifest.json"
    path.write_bytes(_canonical_bytes(payload))

    manifest = model_cache.load_model_manifest(path)
    assert manifest.runtime.python == "3.12"
    assert manifest.runtime.os == "windows"
    assert manifest.runtime.architecture == "x86_64"
    assert [wheel.name for wheel in manifest.runtime.wheels] == [
        "fastembed",
        "onnxruntime",
    ]

    payload["runtime"]["wheels"][1]["filename"] = "onnxruntime-1.23.2.tar.gz"
    unsigned = dict(payload)
    unsigned.pop("aggregateDigest")
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    path.write_bytes(_canonical_bytes(payload))
    with pytest.raises(model_cache.ModelCacheError) as rejected:
        model_cache.load_model_manifest(path)
    assert rejected.value.code == "MODEL_RUNTIME_WHEEL_LOCK_INVALID"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda wheel: wheel.__setitem__(
            "filename", "onnxruntime-1.23.2-cp27-abi3-win_amd64.whl"
        ),
        lambda wheel: wheel.__setitem__("name", "different-package"),
        lambda wheel: wheel.__setitem__("version", "1.23.1"),
        lambda wheel: wheel.__setitem__("size", 128_000_001),
    ),
)
def test_final_manifest_rejects_incompatible_or_identity_mismatched_wheel(
    tmp_path: Path,
    mutation: object,
) -> None:
    model_cache = _model_cache_module()
    payload = _pinned_complete_manifest()
    mutation(payload["runtime"]["wheels"][1])
    unsigned = dict(payload)
    unsigned.pop("aggregateDigest")
    payload["aggregateDigest"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_bytes(_canonical_bytes(payload))

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.load_model_manifest(path)
    assert caught.value.code == "MODEL_RUNTIME_WHEEL_LOCK_INVALID"


def test_cache_verification_rejects_reparse_ancestor_before_member_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    approved = tmp_path / "approved"
    cache_root = approved / "generation" / "model"
    cache_root.mkdir(parents=True)
    member_path = cache_root / "config.json"
    member_path.write_bytes(b"{}")
    manifest = model_cache.ModelManifest(
        schema_version=1,
        manifest_id=model_cache.PINNED_MANIFEST_ID,
        package=model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
        model=model_cache.ModelIdentity(
            id=model_cache.PINNED_MODEL_ID,
            revision=model_cache.PINNED_MODEL_REVISION,
            artifact_repository=model_cache.PINNED_ARTIFACT_REPOSITORY,
            artifact_revision=model_cache.PINNED_ARTIFACT_REVISION,
            dimension=512,
            normalized=True,
            encoding_policy="utf8-nfkc-no-prefix",
        ),
        files=(
            model_cache.ModelMember(
                path="config.json",
                size=2,
                sha256=hashlib.sha256(b"{}").hexdigest(),
            ),
        ),
        aggregate_digest="a" * 64,
    )
    original = model_cache._is_reparse_or_link
    visited: list[Path] = []

    def marked(path: Path) -> bool:
        visited.append(path)
        return path == cache_root.parent.absolute() or original(path)

    monkeypatch.setattr(model_cache, "_is_reparse_or_link", marked)
    monkeypatch.setattr(
        model_cache,
        "_sha256_file",
        lambda _path: (_ for _ in ()).throw(AssertionError("member was read")),
    )

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.verify_loaded_model_cache(
            manifest,
            cache_root,
            approved_parent=approved,
        )

    assert caught.value.code == "MODEL_CACHE_PATH_INVALID"
    assert cache_root.parent.absolute() in visited


def test_committed_manifest_is_complete_after_reviewed_phase_a_candidate() -> None:
    model_cache = _model_cache_module()
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = (
        repo_root
        / "platform/helper/model-manifests/bge-small-zh-v1.5.json"
    )

    manifest = model_cache.load_model_manifest(manifest_path)

    assert manifest.aggregate_digest == hashlib.sha256(
        _canonical_bytes(
            {
                key: value
                for key, value in json.loads(
                    manifest_path.read_text(encoding="utf-8")
                ).items()
                if key != "aggregateDigest"
            }
        )
    ).hexdigest()
    assert all(member.sha256 for member in manifest.files)
    assert len(manifest.runtime.wheels) == 30
    assert manifest.runtime.wheels[0].name == "anyio"
    assert manifest.runtime.wheels[-1].name == "win32-setctime"


def test_dependency_pin_and_local_generation_paths_are_explicit() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    pyproject = (repo_root / "platform/helper/pyproject.toml").read_text(
        encoding="utf-8"
    )
    ignore = (repo_root / ".gitignore").read_text(encoding="utf-8")

    assert pyproject.count('"fastembed==0.8.0"') == 1
    assert pyproject.count('"packaging==23.2"') == 1
    for required in (
        "platform/helper/.embedding-model/",
        "platform/helper/.embedding-bootstrap/",
        "platform/helper/.embedding-quarantine/",
    ):
        assert required in ignore
    assert "platform/helper/model-manifests/" not in ignore
    assert "platform/helper/evidence/embedding-model-live.json" not in ignore


def _synthetic_bootstrap(
    model_cache,
) -> tuple[object, dict[str, bytes]]:
    payloads = {
        "config.json": b"config",
        "special_tokens_map.json": b"special",
        "tokenizer.json": b"tokenizer",
        "tokenizer_config.json": b"tokenizer-config",
    }
    strict = model_cache.load_bootstrap_manifest_bytes(
        _canonical_bytes(_pinned_bootstrap_manifest())
    )
    members = []
    for member in strict.files:
        if member.path == "model_optimized.onnx":
            members.append(member)
            continue
        content = payloads[member.path]
        members.append(
            replace(
                member,
                size=len(content),
                official_identity=model_cache.OfficialIdentity(
                    "git-blob-sha1",
                    model_cache.git_blob_sha1(content),
                ),
            )
        )
    return replace(strict, files=tuple(members)), payloads


def _synthetic_runtime(model_cache):
    return model_cache.RuntimeIdentity(
        python="3.12",
        os="windows",
        architecture="x86_64",
        wheels=(
            model_cache.RuntimeWheel(
                name="fastembed",
                version="0.8.0",
                filename="fastembed-0.8.0-py3-none-any.whl",
                size=116572,
                sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
                artifact_url="https://files.pythonhosted.org/packages/aa/bb/fastembed-0.8.0-py3-none-any.whl",
            ),
            model_cache.RuntimeWheel(
                name="onnxruntime",
                version="1.23.2",
                filename="onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
                size=100,
                sha256="d" * 64,
                artifact_url="https://files.pythonhosted.org/packages/aa/bb/onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
            ),
        ),
    )


def _synthetic_model_metadata(manifest) -> bytes:
    return _canonical_bytes(
        {
            "sha": manifest.model.artifact_revision,
            "siblings": [
                {
                    "rfilename": member.path,
                    "size": member.size,
                    "blobId": (
                        member.official_identity.digest
                        if member.official_identity.kind == "git-blob-sha1"
                        else None
                    ),
                    "lfs": (
                        {
                            "sha256": member.official_identity.digest,
                            "size": member.size,
                        }
                        if member.official_identity.kind == "lfs-sha256"
                        else None
                    ),
                }
                for member in manifest.files
            ],
        }
    )


def test_phase_a_candidate_verifies_framed_git_blobs_and_excludes_onnx(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, payloads = _synthetic_bootstrap(model_cache)
    runtime = _synthetic_runtime(model_cache)

    candidate = model_cache.build_bootstrap_candidate(
        manifest,
        member_bytes=payloads,
        runtime=runtime,
        model_metadata_digest="1" * 64,
        wheel_metadata_digests={wheel.name: "2" * 64 for wheel in runtime.wheels},
        dependency_graph_digest="3" * 64,
    )

    assert candidate["status"] == "candidate-only"
    assert [item["path"] for item in candidate["files"]] == [
        member.path for member in manifest.files
    ]
    onnx = next(
        item for item in candidate["files"] if item["path"] == "model_optimized.onnx"
    )
    assert onnx["officialIdentity"]["kind"] == "lfs-sha256"
    assert onnx["sha256"] == onnx["officialIdentity"]["digest"]
    assert all(item["officialIdentity"]["digest"] for item in candidate["files"])
    assert model_cache.validate_bootstrap_candidate(candidate, manifest) == candidate

    prior = tmp_path / "candidate.json"
    prior.write_bytes(b"prior-candidate")
    tampered = dict(candidate)
    tampered["candidateDigest"] = "0" * 64
    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.write_bootstrap_candidate_atomic(prior, tampered, manifest)
    assert caught.value.code == "MODEL_BOOTSTRAP_CANDIDATE_INVALID"
    assert prior.read_bytes() == b"prior-candidate"


def test_phase_a_candidate_rereads_after_replace_and_restores_prior_on_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    manifest, payloads = _synthetic_bootstrap(model_cache)
    runtime = _synthetic_runtime(model_cache)
    candidate = model_cache.build_bootstrap_candidate(
        manifest,
        member_bytes=payloads,
        runtime=runtime,
        model_metadata_digest="1" * 64,
        wheel_metadata_digests={wheel.name: "2" * 64 for wheel in runtime.wheels},
        dependency_graph_digest="3" * 64,
    )
    destination = tmp_path / "bootstrap/candidate.json"
    destination.parent.mkdir()
    destination.write_bytes(b"prior-candidate")
    real_replace = model_cache.os.replace
    corrupted = False

    def replace_then_corrupt(source: object, target: object) -> None:
        nonlocal corrupted
        real_replace(source, target)
        if Path(target) == destination and not corrupted:
            corrupted = True
            destination.write_bytes(b"tampered-after-replace")

    monkeypatch.setattr(model_cache.os, "replace", replace_then_corrupt)
    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.write_bootstrap_candidate_atomic(
            destination,
            candidate,
            manifest,
            approved_root=tmp_path,
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_CANDIDATE_INVALID"
    assert destination.read_bytes() == b"prior-candidate"


@pytest.mark.skipif(os.name != "nt", reason="Windows directory share semantics")
def test_phase_a_candidate_parent_handle_blocks_final_check_rename_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    manifest, payloads = _synthetic_bootstrap(model_cache)
    runtime = _synthetic_runtime(model_cache)
    candidate = model_cache.build_bootstrap_candidate(
        manifest,
        member_bytes=payloads,
        runtime=runtime,
        model_metadata_digest="1" * 64,
        wheel_metadata_digests={wheel.name: "2" * 64 for wheel in runtime.wheels},
        dependency_graph_digest="3" * 64,
    )
    destination = tmp_path / "bootstrap/candidate.json"
    destination.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = tmp_path / "moved-bootstrap"
    real_replace = model_cache.os.replace
    attack_blocked = False

    def attack_then_replace(source: object, target: object) -> None:
        nonlocal attack_blocked
        try:
            model_cache.os.rename(destination.parent, moved)
        except OSError:
            attack_blocked = True
        else:
            model_cache.os.rename(moved, destination.parent)
            raise AssertionError("parent rename was not denied by held handle")
        real_replace(source, target)

    monkeypatch.setattr(model_cache.os, "replace", attack_then_replace)
    model_cache.write_bootstrap_candidate_atomic(
        destination,
        candidate,
        manifest,
        approved_root=tmp_path,
    )

    assert attack_blocked is True
    assert destination.is_file()
    assert list(outside.iterdir()) == []


def test_phase_a_runner_fetches_only_fixed_small_member_urls_and_writes_atomically(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, payloads = _synthetic_bootstrap(model_cache)
    runtime = _synthetic_runtime(model_cache)
    by_url = {
        member.artifact_url: payloads[member.path]
        for member in manifest.files
        if member.path in payloads
    }
    calls: list[tuple[str, int]] = []

    def fetch(url: str, expected_size: int) -> bytes:
        calls.append((url, expected_size))
        return by_url[url]

    candidate_path = tmp_path / "bootstrap/candidate.json"
    result = model_cache.run_bootstrap_phase(
        manifest,
        candidate_path=candidate_path,
        quarantine_root=tmp_path / "quarantine",
        approved_root=tmp_path,
        fetch_model_metadata=lambda url: (
            _synthetic_model_metadata(manifest)
            if url == model_cache.PINNED_MODEL_METADATA_URL
            else (_ for _ in ()).throw(AssertionError("unexpected metadata URL"))
        ),
        fetch_member=fetch,
        resolve_runtime=lambda _package: (
            runtime,
            {wheel.name: "2" * 64 for wheel in runtime.wheels},
            "3" * 64,
        ),
    )

    assert result == json.loads(candidate_path.read_text(encoding="utf-8"))
    assert [url for url, _size in calls] == [
        member.artifact_url
        for member in manifest.files
        if member.path != "model_optimized.onnx"
    ]
    assert all("model_optimized.onnx" not in url for url, _size in calls)
    assert not (tmp_path / "quarantine").exists()
    assert list(candidate_path.parent.iterdir()) == [candidate_path]


def test_phase_a_identity_failure_preserves_prior_candidate(tmp_path: Path) -> None:
    model_cache = _model_cache_module()
    manifest, payloads = _synthetic_bootstrap(model_cache)
    runtime = _synthetic_runtime(model_cache)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_bytes(b"old")

    def fetch(url: str, _expected_size: int) -> bytes:
        member = next(item for item in manifest.files if item.artifact_url == url)
        if member.path == "tokenizer.json":
            return b"tampered"
        return payloads[member.path]

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.run_bootstrap_phase(
            manifest,
            candidate_path=candidate_path,
            quarantine_root=tmp_path / "quarantine",
            approved_root=tmp_path,
            fetch_model_metadata=lambda _url: _synthetic_model_metadata(manifest),
            fetch_member=fetch,
            resolve_runtime=lambda _package: (
                runtime,
                {wheel.name: "2" * 64 for wheel in runtime.wheels},
                "3" * 64,
            ),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_MEMBER_MISMATCH"
    assert candidate_path.read_bytes() == b"old"


def test_phase_a_metadata_mismatch_stops_before_member_fetch_or_mutation(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, _payloads = _synthetic_bootstrap(model_cache)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_bytes(b"old")
    metadata = json.loads(_synthetic_model_metadata(manifest))
    metadata["siblings"][0]["size"] += 1

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.run_bootstrap_phase(
            manifest,
            candidate_path=candidate_path,
            quarantine_root=tmp_path / "quarantine",
            approved_root=tmp_path,
            fetch_model_metadata=lambda _url: _canonical_bytes(metadata),
            fetch_member=lambda *_args: (_ for _ in ()).throw(
                AssertionError("member fetch ran")
            ),
            resolve_runtime=lambda _package: (_ for _ in ()).throw(
                AssertionError("wheel resolution ran")
            ),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_METADATA_INVALID"
    assert candidate_path.read_bytes() == b"old"
    assert not (tmp_path / "quarantine").exists()


def test_phase_a_metadata_digest_uses_only_canonical_immutable_identities() -> None:
    model_cache = _model_cache_module()
    manifest, _payloads = _synthetic_bootstrap(model_cache)
    baseline = json.loads(_synthetic_model_metadata(manifest))
    baseline["author"] = "mutable-author-a"
    baseline["lastModified"] = "2026-07-17T00:00:00Z"
    baseline_digest = model_cache.verify_bootstrap_model_metadata(
        manifest,
        _canonical_bytes(baseline),
    )

    realistic = json.loads(json.dumps(baseline))
    realistic["author"] = "mutable-author-b"
    realistic["lastModified"] = "2099-01-01T00:00:00Z"
    realistic["downloads"] = 999999
    realistic["siblings"].reverse()
    for item in realistic["siblings"]:
        if item["rfilename"] == "model_optimized.onnx":
            item["blobId"] = "f" * 40
            item["lfs"]["pointerSize"] = 134
            item["mutableDisplayField"] = "ignored"
        else:
            item.pop("lfs")

    realistic_digest = model_cache.verify_bootstrap_model_metadata(
        manifest,
        _canonical_bytes(realistic),
    )

    assert realistic_digest == baseline_digest
    expected_identity = {
        "artifactRevision": manifest.model.artifact_revision,
        "members": [
            {
                "path": member.path,
                "size": member.size,
                "identity": {
                    "kind": member.official_identity.kind,
                    "digest": member.official_identity.digest,
                },
            }
            for member in manifest.files
        ],
    }
    assert baseline_digest == hashlib.sha256(
        _canonical_bytes(expected_identity)
    ).hexdigest()


def test_phase_a_metadata_accepts_unselected_repository_siblings_but_requires_manifest_subset() -> None:
    model_cache = _model_cache_module()
    manifest, _payloads = _synthetic_bootstrap(model_cache)
    baseline = json.loads(_synthetic_model_metadata(manifest))
    expected_digest = model_cache.verify_bootstrap_model_metadata(
        manifest,
        _canonical_bytes(baseline),
    )
    baseline["siblings"].extend(
        [
            {
                "rfilename": ".gitattributes",
                "size": 1519,
                "blobId": "a6344aac8c09253b3b630fb776ae94478aa0275b",
            },
            {
                "rfilename": "README.md",
                "size": 24,
                "blobId": "7be5fc7f47d5db027d120b8024982df93db95b74",
            },
            {
                "rfilename": "ort_config.json",
                "size": 1234,
                "blobId": "31d3edac186bcfc7fb617662b7e0f750c7fef47a",
            },
            {
                "rfilename": "vocab.txt",
                "size": 109540,
                "blobId": "ca4f9781030019ab9b253c6dcb8c7878b6dc87a5",
            },
        ]
    )

    assert model_cache.verify_bootstrap_model_metadata(
        manifest,
        _canonical_bytes(baseline),
    ) == expected_digest

    baseline["siblings"] = [
        item for item in baseline["siblings"] if item["rfilename"] != "config.json"
    ]
    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.verify_bootstrap_model_metadata(
            manifest,
            _canonical_bytes(baseline),
        )
    assert caught.value.code == "MODEL_BOOTSTRAP_METADATA_INVALID"


def test_phase_a_metadata_rejects_effective_lfs_on_small_member() -> None:
    model_cache = _model_cache_module()
    manifest, _payloads = _synthetic_bootstrap(model_cache)
    metadata = json.loads(_synthetic_model_metadata(manifest))
    small = next(
        item for item in metadata["siblings"] if item["rfilename"] == "config.json"
    )
    small["lfs"] = {"sha256": "0" * 64, "size": small["size"]}

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.verify_bootstrap_model_metadata(
            manifest,
            _canonical_bytes(metadata),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_METADATA_INVALID"


def test_phase_a_rejects_uncontained_output_before_network_or_filesystem_mutation(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, _payloads = _synthetic_bootstrap(model_cache)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-candidate.json"

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.run_bootstrap_phase(
            manifest,
            candidate_path=outside,
            quarantine_root=tmp_path / "quarantine",
            approved_root=tmp_path,
            fetch_model_metadata=lambda _url: (_ for _ in ()).throw(
                AssertionError("metadata fetch ran")
            ),
            fetch_member=lambda *_args: (_ for _ in ()).throw(
                AssertionError("member fetch ran")
            ),
            resolve_runtime=lambda _package: (_ for _ in ()).throw(
                AssertionError("wheel resolution ran")
            ),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_PATH_INVALID"
    assert not outside.exists()


def _pypi_release(
    name: str,
    version: str,
    filename: str,
    *,
    size: int,
    sha256: str,
    requires_dist: list[str] | None = None,
    requires_python: str | None = None,
    yanked: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    url = f"https://files.pythonhosted.org/packages/aa/bb/{filename}"
    file = {
        "filename": filename,
        "packagetype": "bdist_wheel",
        "url": url,
        "size": size,
        "digests": {"sha256": sha256},
        "yanked": yanked,
        "requires_python": requires_python,
    }
    version_payload = {
        "info": {
            "name": name,
            "version": version,
            "requires_dist": requires_dist or [],
            "requires_python": requires_python,
        },
        "urls": [file],
    }
    return file, version_payload


def test_phase_a_target_marker_environment_is_complete_and_fixed() -> None:
    model_cache = _model_cache_module()

    assert model_cache._TARGET_MARKER_ENVIRONMENT == {
        "implementation_name": "cpython",
        "implementation_version": "3.12.0",
        "os_name": "nt",
        "platform_machine": "AMD64",
        "platform_release": "",
        "platform_system": "Windows",
        "platform_version": "",
        "platform_python_implementation": "CPython",
        "python_full_version": "3.12.0",
        "python_version": "3.12",
        "sys_platform": "win32",
        "extra": "",
    }


def test_phase_a_resolver_builds_deterministic_binary_only_target_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    fastembed_file, fastembed_version = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        requires_dist=[
            "onnxruntime>=1.23,<1.24",
            "tokenizers>=0.20; python_version >= '3.12'",
            "ignored>=1; python_version < '3.12'",
            "optional[feature]>=1; extra == 'feature'",
        ],
    )
    onnx_file, onnx_version = _pypi_release(
        "onnxruntime",
        "1.23.2",
        "onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
        size=200,
        sha256="a" * 64,
    )
    tokenizer_file, tokenizer_version = _pypi_release(
        "tokenizers",
        "0.21.0",
        "tokenizers-0.21.0-cp39-abi3-win_amd64.whl",
        size=300,
        sha256="b" * 64,
    )
    tokenizer_sdist, _unused = _pypi_release(
        "tokenizers",
        "0.22.0",
        "tokenizers-0.22.0.tar.gz",
        size=1,
        sha256="c" * 64,
    )
    tokenizer_sdist["packagetype"] = "sdist"
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [fastembed_file]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": fastembed_version,
        "https://pypi.org/pypi/onnxruntime/json": {
            "info": {"name": "onnxruntime"},
            "releases": {"1.23.2": [onnx_file]},
        },
        "https://pypi.org/pypi/onnxruntime/1.23.2/json": onnx_version,
        "https://pypi.org/pypi/tokenizers/json": {
            "info": {"name": "tokenizers"},
            "releases": {
                "0.22.0": [tokenizer_sdist],
                "0.21.0": [tokenizer_file],
            },
        },
        "https://pypi.org/pypi/tokenizers/0.21.0/json": tokenizer_version,
    }
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return _canonical_bytes(responses[url])

    runtime, metadata_digests, graph_digest = model_cache.resolve_runtime_wheels_from_pypi(
        model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
        fetch_metadata=fetch,
    )

    assert [wheel.name for wheel in runtime.wheels] == [
        "fastembed",
        "onnxruntime",
        "tokenizers",
    ]
    assert all(wheel.filename.endswith(".whl") for wheel in runtime.wheels)
    assert "ignored" not in metadata_digests
    assert "optional" not in metadata_digests
    assert set(metadata_digests) == {"fastembed", "onnxruntime", "tokenizers"}
    assert len(graph_digest) == 64
    assert calls == [
        "https://pypi.org/pypi/fastembed/json",
        "https://pypi.org/pypi/fastembed/0.8.0/json",
        "https://pypi.org/pypi/onnxruntime/json",
        "https://pypi.org/pypi/onnxruntime/1.23.2/json",
        "https://pypi.org/pypi/tokenizers/json",
        "https://pypi.org/pypi/tokenizers/0.21.0/json",
    ]

    baseline_calls = list(calls)
    calls.clear()
    for response in responses.values():
        response["last_serial"] = 999999
        response["mutable_display_data"] = {"downloads": 123, "vulnerabilities": []}
    monkeypatch.setattr(
        model_cache,
        "default_environment",
        lambda: (_ for _ in ()).throw(AssertionError("host marker environment read")),
        raising=False,
    )
    repeated_runtime, repeated_digests, repeated_graph_digest = (
        model_cache.resolve_runtime_wheels_from_pypi(
        model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
            fetch_metadata=fetch,
        )
    )
    assert repeated_runtime == runtime
    assert repeated_digests == metadata_digests
    assert repeated_graph_digest == graph_digest
    assert calls == baseline_calls

    calls.clear()
    fastembed_file["requires_python"] = ">=3.12"
    fastembed_version["info"]["requires_python"] = ">=3.12"
    _same_runtime, changed_python_digests, changed_python_graph = (
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=fetch,
        )
    )
    assert changed_python_digests["fastembed"] != metadata_digests["fastembed"]
    assert changed_python_graph != graph_digest

    calls.clear()
    fastembed_version["info"]["requires_dist"][0] = "onnxruntime>=1.23.0,<1.24"
    _same_runtime, changed_requirement_digests, changed_requirement_graph = (
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=fetch,
        )
    )
    assert (
        changed_requirement_digests["fastembed"]
        != changed_python_digests["fastembed"]
    )
    assert changed_requirement_graph != changed_python_graph

    calls.clear()
    tokenizer_file["digests"]["sha256"] = "c" * 64
    changed_runtime, changed_wheel_digests, changed_wheel_graph = (
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=fetch,
        )
    )
    assert changed_runtime.wheels[-1].sha256 == "c" * 64
    assert changed_wheel_digests["tokenizers"] != metadata_digests["tokenizers"]
    assert changed_wheel_graph != graph_digest


def test_phase_a_resolver_rejects_alternate_artifact_host() -> None:
    model_cache = _model_cache_module()
    wheel, version_payload = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
    )
    wheel["url"] = "https://example.invalid/fastembed-0.8.0-py3-none-any.whl"
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [wheel]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": version_payload,
    }

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=lambda url: _canonical_bytes(responses[url]),
        )

    assert caught.value.code == "MODEL_RUNTIME_WHEEL_LOCK_INVALID"


def test_phase_a_resolver_rejects_an_active_dependency_extra() -> None:
    model_cache = _model_cache_module()
    wheel, version_payload = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        requires_dist=["tokenizers[feature]>=0.20"],
    )
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [wheel]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": version_payload,
    }

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=lambda url: _canonical_bytes(responses[url]),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_RESOLUTION_FAILED"


def test_phase_a_resolver_falls_back_when_project_python_constraint_excludes_312() -> None:
    model_cache = _model_cache_module()
    fastembed_file, fastembed_version = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        requires_dist=["onnxruntime>=1"],
        requires_python=">=3.12",
    )
    high_file, high_version = _pypi_release(
        "onnxruntime",
        "2.0.0",
        "onnxruntime-2.0.0-py3-none-any.whl",
        size=200,
        sha256="a" * 64,
        requires_python="<3.12",
    )
    high_file["requires_python"] = None
    low_file, low_version = _pypi_release(
        "onnxruntime",
        "1.23.2",
        "onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
        size=300,
        sha256="b" * 64,
        requires_python=">=3.12",
    )
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [fastembed_file]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": fastembed_version,
        "https://pypi.org/pypi/onnxruntime/json": {
            "info": {"name": "onnxruntime"},
            "releases": {"2.0.0": [high_file], "1.23.2": [low_file]},
        },
        "https://pypi.org/pypi/onnxruntime/2.0.0/json": high_version,
        "https://pypi.org/pypi/onnxruntime/1.23.2/json": low_version,
    }
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return _canonical_bytes(responses[url])

    runtime, _metadata, _graph = model_cache.resolve_runtime_wheels_from_pypi(
        model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
        fetch_metadata=fetch,
    )

    assert {wheel.name: wheel.version for wheel in runtime.wheels}["onnxruntime"] == "1.23.2"
    assert calls[-2:] == [
        "https://pypi.org/pypi/onnxruntime/2.0.0/json",
        "https://pypi.org/pypi/onnxruntime/1.23.2/json",
    ]


def test_phase_a_resolver_has_hard_total_search_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    wheel, version_payload = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
    )
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [wheel]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": version_payload,
    }
    monkeypatch.setattr(model_cache, "_MAX_RUNTIME_RESOLUTION_STEPS", 1)

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=lambda url: _canonical_bytes(responses[url]),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_RESOLUTION_LIMIT"


@pytest.mark.parametrize("budget", ("requests", "bytes", "deadline"))
def test_phase_a_resolver_enforces_shared_transport_budgets(
    monkeypatch: pytest.MonkeyPatch,
    budget: str,
) -> None:
    model_cache = _model_cache_module()
    wheel, version_payload = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
    )
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [wheel]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": version_payload,
    }
    if budget == "requests":
        monkeypatch.setattr(model_cache, "_MAX_RUNTIME_METADATA_REQUESTS", 1)
    elif budget == "bytes":
        monkeypatch.setattr(model_cache, "_MAX_RUNTIME_METADATA_BYTES", 1)
    else:
        ticks = iter((0.0, 0.0, 999.0))
        monkeypatch.setattr(model_cache.time, "monotonic", lambda: next(ticks, 999.0))

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=lambda url: _canonical_bytes(responses[url]),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_RESOLUTION_LIMIT"


@pytest.mark.parametrize("invalid_requires_dist", ("", 0, {}))
def test_phase_a_resolver_rejects_non_list_non_null_requires_dist(
    invalid_requires_dist: object,
) -> None:
    model_cache = _model_cache_module()
    wheel, version_payload = _pypi_release(
        "fastembed",
        "0.8.0",
        "fastembed-0.8.0-py3-none-any.whl",
        size=116572,
        sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
    )
    version_payload["info"]["requires_dist"] = invalid_requires_dist
    responses = {
        "https://pypi.org/pypi/fastembed/json": {
            "info": {"name": "fastembed"},
            "releases": {"0.8.0": [wheel]},
        },
        "https://pypi.org/pypi/fastembed/0.8.0/json": version_payload,
    }

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.resolve_runtime_wheels_from_pypi(
            model_cache.PackageIdentity(
                name="fastembed",
                version="0.8.0",
                wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
                wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
            ),
            fetch_metadata=lambda url: _canonical_bytes(responses[url]),
        )

    assert caught.value.code == "MODEL_BOOTSTRAP_RESOLUTION_FAILED"


def _synthetic_wheel(
    name: str,
    version: str,
    *,
    requires: tuple[str, ...] = (),
    extra_metadata_headers: tuple[str, ...] = (),
    extra_members: tuple[tuple[zipfile.ZipInfo | str, bytes], ...] = (),
) -> bytes:
    distribution = name.replace("-", "_")
    dist_info = f"{distribution}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        "Requires-Python: >=3.12",
        *(f"Requires-Dist: {requirement}" for requirement in requires),
        *extra_metadata_headers,
        "",
        "",
    ]
    entries: list[tuple[zipfile.ZipInfo | str, bytes]] = [
        (f"{distribution}/__init__.py", b""),
        (f"{dist_info}/METADATA", "\n".join(metadata).encode("utf-8")),
        (
            f"{dist_info}/WHEEL",
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        ),
        *extra_members,
    ]
    record_output = io.StringIO(newline="")
    writer = csv.writer(record_output, lineterminator="\n")
    for member, content in entries:
        member_name = member.filename if isinstance(member, zipfile.ZipInfo) else member
        encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((member_name, f"sha256={encoded.decode('ascii')}", len(content)))
    writer.writerow((f"{dist_info}/RECORD", "", ""))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in entries:
            archive.writestr(member, content)
        archive.writestr(f"{dist_info}/RECORD", record_output.getvalue().encode("utf-8"))
    return output.getvalue()


def _synthetic_phase_b_fixture(model_cache):
    model_payloads = {
        "config.json": b"config",
        "model_optimized.onnx": b"onnx",
        "special_tokens_map.json": b"special",
        "tokenizer.json": b"tokenizer",
        "tokenizer_config.json": b"tokenizer-config",
    }
    wheel_payloads = {
        "fastembed-0.8.0-py3-none-any.whl": _synthetic_wheel(
            "fastembed",
            "0.8.0",
            requires=(
                "onnxruntime>=1.23,<2",
                "optional[feature]>=1; extra == 'feature'",
            ),
        ),
        "onnxruntime-1.23.2-py3-none-any.whl": _synthetic_wheel(
            "onnxruntime",
            "1.23.2",
        ),
    }
    runtime = model_cache.RuntimeIdentity(
        python="3.12",
        os="windows",
        architecture="x86_64",
        wheels=tuple(
            model_cache.RuntimeWheel(
                name=name,
                version=version,
                filename=filename,
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                artifact_url=f"https://files.pythonhosted.org/packages/aa/bb/{filename}",
            )
            for name, version, filename, payload in (
                (
                    "fastembed",
                    "0.8.0",
                    "fastembed-0.8.0-py3-none-any.whl",
                    wheel_payloads["fastembed-0.8.0-py3-none-any.whl"],
                ),
                (
                    "onnxruntime",
                    "1.23.2",
                    "onnxruntime-1.23.2-py3-none-any.whl",
                    wheel_payloads["onnxruntime-1.23.2-py3-none-any.whl"],
                ),
            )
        ),
    )
    manifest = model_cache.ModelManifest(
        schema_version=1,
        manifest_id=model_cache.PINNED_MANIFEST_ID,
        package=model_cache.PackageIdentity(
            name="fastembed",
            version="0.8.0",
            wheel_filename=model_cache.PINNED_FASTEMBED_WHEEL,
            wheel_sha256=model_cache.PINNED_FASTEMBED_WHEEL_SHA256,
        ),
        model=model_cache.ModelIdentity(
            id=model_cache.PINNED_MODEL_ID,
            revision=model_cache.PINNED_MODEL_REVISION,
            artifact_repository=model_cache.PINNED_ARTIFACT_REPOSITORY,
            artifact_revision=model_cache.PINNED_ARTIFACT_REVISION,
            dimension=512,
            normalized=True,
            encoding_policy="utf8-nfkc-no-prefix",
        ),
        files=tuple(
            model_cache.ModelMember(
                path=path,
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                artifact_url=model_cache._model_artifact_url(path),
            )
            for path, payload in sorted(model_payloads.items())
        ),
        aggregate_digest="a" * 64,
        runtime=runtime,
    )
    return manifest, model_payloads, wheel_payloads


def _install_synthetic_runtime(
    runtime: object,
    wheelhouse: Path,
    runtime_root: Path,
) -> None:
    for wheel in runtime.wheels:
        with zipfile.ZipFile(wheelhouse / wheel.filename) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            dist_info_name = next(
                name.rsplit("/", 1)[0]
                for name in names
                if name.endswith(".dist-info/METADATA")
            )
            owned: list[Path] = []
            for name in names:
                destination = runtime_root.joinpath(*Path(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
                owned.append(destination)
        dist_info = runtime_root.joinpath(*Path(dist_info_name).parts)
        installer = dist_info / "INSTALLER"
        installer.write_bytes(b"pip\n")
        requested = dist_info / "REQUESTED"
        requested.write_bytes(b"")
        owned.extend((installer, requested))
        record = dist_info / "RECORD"
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        for path in sorted(owned):
            relative = path.relative_to(runtime_root).as_posix()
            if path == record:
                writer.writerow((relative, "", ""))
                continue
            content = path.read_bytes()
            encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(
                b"="
            )
            writer.writerow(
                (relative, f"sha256={encoded.decode('ascii')}", len(content))
            )
        record.write_text(output.getvalue(), encoding="utf-8", newline="")


def _synthetic_console_script_install(
    model_cache: object,
    tmp_path: Path,
    *,
    record_script_path: str,
) -> tuple[object, Path, Path]:
    name = "scripted-runtime"
    version = "1.0.0"
    distribution = name.replace("-", "_")
    dist_info = f"{distribution}-{version}.dist-info"
    wheel_filename = f"{distribution}-{version}-py3-none-any.whl"
    wheel_payload = _synthetic_wheel(
        name,
        version,
        extra_members=(
            (
                f"{dist_info}/entry_points.txt",
                b"[console_scripts]\ntool=scripted_runtime:main\n",
            ),
        ),
    )
    runtime = model_cache.RuntimeIdentity(
        python="3.12",
        os="windows",
        architecture="x86_64",
        wheels=(
            model_cache.RuntimeWheel(
                name=name,
                version=version,
                filename=wheel_filename,
                size=len(wheel_payload),
                sha256=hashlib.sha256(wheel_payload).hexdigest(),
                artifact_url=f"https://files.pythonhosted.org/packages/aa/bb/{wheel_filename}",
            ),
        ),
    )
    wheelhouse = tmp_path / "wheelhouse"
    runtime_root = tmp_path / "runtime"
    wheelhouse.mkdir()
    runtime_root.mkdir()
    (wheelhouse / wheel_filename).write_bytes(wheel_payload)
    _install_synthetic_runtime(runtime, wheelhouse, runtime_root)
    script = runtime_root / "bin" / "tool.exe"
    script.parent.mkdir()
    script.write_bytes(b"synthetic console launcher")
    encoded = base64.urlsafe_b64encode(hashlib.sha256(script.read_bytes()).digest()).rstrip(
        b"="
    )
    record = runtime_root / dist_info / "RECORD"
    with record.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            (
                record_script_path,
                f"sha256={encoded.decode('ascii')}",
                script.stat().st_size,
            )
        )
    return runtime, wheelhouse, runtime_root


def test_phase_b_installed_runtime_accepts_declared_windows_target_script_record(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    runtime, wheelhouse, runtime_root = _synthetic_console_script_install(
        model_cache,
        tmp_path,
        record_script_path="../../bin/tool.exe",
    )

    model_cache._validate_installed_runtime_closure(
        runtime,
        runtime_root,
        wheelhouse,
    )


@pytest.mark.parametrize(
    "record_script_path",
    (
        "../../../outside.exe",
        "../../bin/../escape.exe",
        "../../bin/evil.exe",
        "../../bin/tool",
        "../../bin/tool.exe/extra",
        "../../Scripts/tool.exe",
        "../bin/tool.exe",
        "C:/outside.exe",
        "../../bin\\tool.exe",
    ),
)
def test_phase_b_installed_runtime_rejects_untrusted_script_record_paths(
    tmp_path: Path,
    record_script_path: str,
) -> None:
    model_cache = _model_cache_module()
    runtime, wheelhouse, runtime_root = _synthetic_console_script_install(
        model_cache,
        tmp_path,
        record_script_path=record_script_path,
    )

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._validate_installed_runtime_closure(
            runtime,
            runtime_root,
            wheelhouse,
        )

    assert caught.value.code == "MODEL_RUNTIME_INSTALL_FAILED"


def _verification_evidence(verified: object, **extra: object) -> dict[str, object]:
    origins = []
    for distribution in ("fastembed", "onnxruntime"):
        path = verified.runtime_root / distribution / "__init__.py"
        origins.append(
            {
                "distribution": distribution,
                "path": path.relative_to(verified.generation_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {
        "generationDigest": verified.generation_digest,
        "childEvidenceDigest": hashlib.sha256(b"synthetic-child-evidence").hexdigest(),
        "childLoadedOrigins": origins,
        **extra,
    }


def _expected_verification_evidence(
    verified: object,
    **extra: object,
) -> dict[str, object]:
    evidence = _verification_evidence(verified, **extra)
    evidence["providerOrigins"] = list(evidence["childLoadedOrigins"])
    return evidence


def test_phase_b_validates_offline_wheel_metadata_closure(tmp_path: Path) -> None:
    model_cache = _model_cache_module()
    manifest, _model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    for filename, payload in wheel_payloads.items():
        (wheelhouse / filename).write_bytes(payload)

    assert model_cache.validate_offline_wheel_closure(
        manifest.runtime,
        wheelhouse,
    ) == ("fastembed", "onnxruntime")

    missing_runtime = replace(
        manifest.runtime,
        wheels=(manifest.runtime.wheels[0],),
    )
    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.validate_offline_wheel_closure(missing_runtime, wheelhouse)
    assert caught.value.code == "MODEL_RUNTIME_WHEEL_CLOSURE_INVALID"

    active_extra = _synthetic_wheel(
        "fastembed",
        "0.8.0",
        requires=("onnxruntime[feature]>=1.23,<2",),
    )
    fastembed_path = wheelhouse / manifest.runtime.wheels[0].filename
    fastembed_path.write_bytes(active_extra)
    active_extra_runtime = replace(
        manifest.runtime,
        wheels=(
            replace(
                manifest.runtime.wheels[0],
                size=len(active_extra),
                sha256=hashlib.sha256(active_extra).hexdigest(),
            ),
            manifest.runtime.wheels[1],
        ),
    )
    with pytest.raises(model_cache.ModelCacheError) as active:
        model_cache.validate_offline_wheel_closure(
            active_extra_runtime,
            wheelhouse,
        )
    assert active.value.code == "MODEL_RUNTIME_WHEEL_CLOSURE_INVALID"


@pytest.mark.parametrize(
    "extra_header",
    (
        "Name: alternate-name",
        "Version: 9.9.9",
        "Requires-Python: <3.12",
    ),
)
def test_phase_b_rejects_duplicate_singleton_metadata_headers(
    tmp_path: Path,
    extra_header: str,
) -> None:
    model_cache = _model_cache_module()
    wheel = _synthetic_wheel(
        "fastembed",
        "0.8.0",
        extra_metadata_headers=(extra_header,),
    )
    path = tmp_path / "fastembed-0.8.0-py3-none-any.whl"
    path.write_bytes(wheel)

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._read_locked_wheel_metadata(path)

    assert caught.value.code == "MODEL_RUNTIME_WHEEL_CLOSURE_INVALID"


def test_phase_b_rejects_invalid_metadata_version_as_typed_failure(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, _model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    invalid = _synthetic_wheel("fastembed", "not a version")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / manifest.runtime.wheels[0].filename).write_bytes(invalid)
    (wheelhouse / manifest.runtime.wheels[1].filename).write_bytes(
        wheel_payloads[manifest.runtime.wheels[1].filename]
    )
    runtime = replace(
        manifest.runtime,
        wheels=(
            replace(
                manifest.runtime.wheels[0],
                size=len(invalid),
                sha256=hashlib.sha256(invalid).hexdigest(),
            ),
            manifest.runtime.wheels[1],
        ),
    )

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.validate_offline_wheel_closure(runtime, wheelhouse)

    assert caught.value.code == "MODEL_RUNTIME_WHEEL_CLOSURE_INVALID"


@pytest.mark.parametrize("archive_case", ("duplicate", "symlink", "compression-ratio"))
def test_phase_b_rejects_unsafe_wheel_archive_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_case: str,
) -> None:
    model_cache = _model_cache_module()
    if archive_case == "duplicate":
        members: tuple[tuple[zipfile.ZipInfo | str, bytes], ...] = (
            ("fastembed/duplicate.py", b"one"),
            ("fastembed/duplicate.py", b"two"),
        )
    elif archive_case == "symlink":
        link = zipfile.ZipInfo("fastembed/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        members = ((link, b"target"),)
    else:
        monkeypatch.setattr(model_cache, "_MAX_WHEEL_COMPRESSION_RATIO", 10)
        members = (("fastembed/compression-bomb.bin", b"0" * 1_000_000),)
    wheel = _synthetic_wheel(
        "fastembed",
        "0.8.0",
        extra_members=members,
    )
    path = tmp_path / "fastembed-0.8.0-py3-none-any.whl"
    path.write_bytes(wheel)

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._read_locked_wheel_metadata(path)

    assert caught.value.code == "MODEL_RUNTIME_WHEEL_CLOSURE_INVALID"


@pytest.mark.model_download
def test_phase_b_redownloads_every_locked_byte_into_fresh_generation(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    generation_parent = tmp_path / ".embedding-model"
    quarantine_parent = tmp_path / ".embedding-quarantine"
    generation_parent.mkdir()
    quarantine_parent.mkdir()
    prior = generation_parent / "prior-generation"
    prior.mkdir()
    (prior / "sealed.bin").write_bytes(b"prior")
    phase_a = tmp_path / ".embedding-bootstrap/candidate.json"
    phase_a.parent.mkdir()
    phase_a.write_bytes(b"phase-a-must-not-be-reused")
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }
    calls: list[str] = []

    def fetch(url: str, expected_size: int) -> bytes:
        calls.append(url)
        payload = bytes(by_url[url])
        assert len(payload) == expected_size
        return payload

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        assert {path.name for path in wheelhouse.iterdir()} == set(wheel_payloads)
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    result = model_cache.run_final_phase(
        manifest,
        generation_parent=generation_parent,
        quarantine_root=quarantine_parent,
        approved_root=tmp_path,
        fetch_artifact=fetch,
        install_runtime=install,
        verify_generation=_verification_evidence,
    )

    assert calls == [
        *(member.artifact_url for member in manifest.files),
        *(wheel.artifact_url for wheel in manifest.runtime.wheels),
    ]
    assert result.verified.generation_root.parent == generation_parent.resolve()
    assert result.verified.specific_model_path.name == "model"
    assert result.verified.runtime_root.name == "runtime"
    assert result.verification == _expected_verification_evidence(result.verified)
    assert (prior / "sealed.bin").read_bytes() == b"prior"
    assert phase_a.read_bytes() == b"phase-a-must-not-be-reused"


@pytest.mark.parametrize(
    "failure_stage",
    ("download", "install", "promotion", "verify"),
)
def test_phase_b_failure_preserves_prior_cache_and_removes_unsealed_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    generation_parent = tmp_path / ".embedding-model"
    quarantine_parent = tmp_path / ".embedding-quarantine"
    generation_parent.mkdir()
    quarantine_parent.mkdir()
    prior = generation_parent / "prior-generation"
    prior.mkdir()
    (prior / "sealed.bin").write_bytes(b"prior")
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def fetch(url: str, _expected_size: int) -> bytes:
        if failure_stage == "download":
            raise RuntimeError("injected download failure")
        return by_url[url]

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        if failure_stage == "install":
            raise RuntimeError("injected install failure")
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    def verify(verified: object) -> dict[str, object]:
        if failure_stage == "verify":
            raise RuntimeError("injected verification failure")
        return _verification_evidence(verified)

    if failure_stage == "promotion":
        real_replace = model_cache.os.replace

        def fail_promotion(source: object, destination: object) -> None:
            if Path(destination).parent == generation_parent:
                raise OSError("injected promotion failure")
            real_replace(source, destination)

        monkeypatch.setattr(
            model_cache.os,
            "replace",
            fail_promotion,
        )

    with pytest.raises(model_cache.ModelCacheError):
        model_cache.run_final_phase(
            manifest,
            generation_parent=generation_parent,
            quarantine_root=quarantine_parent,
            approved_root=tmp_path,
            fetch_artifact=fetch,
            install_runtime=install,
            verify_generation=verify,
        )

    assert (prior / "sealed.bin").read_bytes() == b"prior"
    assert [path.name for path in generation_parent.iterdir()] == ["prior-generation"]
    assert list(quarantine_parent.iterdir()) == []


@pytest.mark.model_download
def test_phase_b_existing_generation_reuse_removes_fresh_staging(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    arguments = {
        "generation_parent": tmp_path / ".embedding-model",
        "quarantine_root": tmp_path / ".embedding-quarantine",
        "approved_root": tmp_path,
        "fetch_artifact": lambda url, _size: by_url[url],
        "install_runtime": install,
        "verify_generation": _verification_evidence,
    }
    first = model_cache.run_final_phase(manifest, **arguments)
    calls: list[str] = []
    arguments["fetch_artifact"] = lambda url, _size: (
        calls.append(url) or by_url[url]
    )
    second = model_cache.run_final_phase(manifest, **arguments)

    assert first.promoted_new is True
    assert second.promoted_new is False
    assert calls == [
        *(member.artifact_url for member in manifest.files),
        *(wheel.artifact_url for wheel in manifest.runtime.wheels),
    ]
    assert second.verified.generation_root == first.verified.generation_root
    assert list(second.quarantine_root.iterdir()) == []
    assert len(tuple((tmp_path / ".embedding-model").iterdir())) == 1


@pytest.mark.model_download
def test_phase_b_concurrent_same_generation_promotion_reuses_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    real_replace = model_cache.os.replace

    def racing_replace(source: object, destination: object) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.name == "generation" and destination_path.parent == (
            tmp_path / ".embedding-model"
        ):
            shutil.copytree(source_path, destination_path)
            raise FileExistsError("concurrent winner")
        real_replace(source, destination)

    monkeypatch.setattr(model_cache.os, "replace", racing_replace)

    result = model_cache.run_final_phase(
        manifest,
        generation_parent=tmp_path / ".embedding-model",
        quarantine_root=tmp_path / ".embedding-quarantine",
        approved_root=tmp_path,
        fetch_artifact=lambda url, _size: by_url[url],
        install_runtime=install,
        verify_generation=_verification_evidence,
    )

    assert result.promoted_new is False
    assert result.verified.generation_root.is_dir()
    assert list(result.quarantine_root.iterdir()) == []
    assert len(tuple((tmp_path / ".embedding-model").iterdir())) == 1


@pytest.mark.model_download
def test_phase_b_binds_ancestor_handles_during_stage_promote_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }
    generation_parent = (tmp_path / ".embedding-model").absolute()
    quarantine_parent = (tmp_path / ".embedding-quarantine").absolute()
    active: set[Path] = set()

    @contextmanager
    def tracked_handles(
        _approved_root: Path,
        target: Path,
        **_kwargs: object,
    ):
        absolute = target.absolute()
        active.add(absolute)
        try:
            yield ()
        finally:
            active.remove(absolute)

    monkeypatch.setattr(
        model_cache,
        "_hold_contained_directory_handles",
        tracked_handles,
    )
    real_replace = model_cache.os.replace

    def checked_replace(source: object, destination: object) -> None:
        assert generation_parent in active
        assert quarantine_parent in active
        assert any(path.name.startswith("phase-b-") for path in active)
        real_replace(source, destination)

    monkeypatch.setattr(model_cache.os, "replace", checked_replace)

    def fetch(url: str, _size: int) -> bytes:
        assert generation_parent in active
        assert quarantine_parent in active
        assert any(path.name.startswith("phase-b-") for path in active)
        return by_url[url]

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        assert generation_parent in active
        assert quarantine_parent in active
        assert runtime_root.parent in active
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    def verify(verified: object) -> dict[str, object]:
        assert generation_parent in active
        assert quarantine_parent in active
        assert verified.generation_root in active
        return _verification_evidence(verified)

    model_cache.run_final_phase(
        manifest,
        generation_parent=generation_parent,
        quarantine_root=quarantine_parent,
        approved_root=tmp_path,
        fetch_artifact=fetch,
        install_runtime=install,
        verify_generation=verify,
    )


@pytest.mark.model_download
@pytest.mark.parametrize("runtime_fault", ("extra", "missing", "tampered"))
def test_phase_b_runtime_files_must_match_verified_wheel_record_closure(
    tmp_path: Path,
    runtime_fault: str,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)
        target = runtime_root / "fastembed/__init__.py"
        if runtime_fault == "extra":
            (runtime_root / "attacker.py").write_bytes(b"unexpected")
        elif runtime_fault == "missing":
            target.unlink()
        else:
            target.write_bytes(b"tampered")

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.run_final_phase(
            manifest,
            generation_parent=tmp_path / ".embedding-model",
            quarantine_root=tmp_path / ".embedding-quarantine",
            approved_root=tmp_path,
            fetch_artifact=lambda url, _size: by_url[url],
            install_runtime=install,
            verify_generation=lambda _verified: {"status": "verified"},
        )

    assert caught.value.code == "MODEL_RUNTIME_INSTALL_FAILED"
    assert list((tmp_path / ".embedding-model").iterdir()) == []
    assert list((tmp_path / ".embedding-quarantine").iterdir()) == []


@pytest.mark.model_download
def test_phase_b_binds_verified_generation_files_across_provider_hook(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    def verify(verified: object) -> dict[str, object]:
        original = verified.runtime_root / "fastembed/__init__.py"
        replacement = verified.runtime_root / "onnxruntime/__init__.py"
        blocked = False
        try:
            os.replace(replacement, original)
        except OSError:
            blocked = True
        return _verification_evidence(verified, replacementBlocked=blocked)

    result = model_cache.run_final_phase(
        manifest,
        generation_parent=tmp_path / ".embedding-model",
        quarantine_root=tmp_path / ".embedding-quarantine",
        approved_root=tmp_path,
        fetch_artifact=lambda url, _size: by_url[url],
        install_runtime=install,
        verify_generation=verify,
    )

    assert result.verification == _expected_verification_evidence(
        result.verified,
        replacementBlocked=True,
    )
    assert (result.verified.runtime_root / "fastembed/__init__.py").read_bytes() == b""


@pytest.mark.model_download
def test_phase_b_generation_tree_denies_transient_new_files_during_hook(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    def verify(verified: object) -> dict[str, object]:
        blocked: list[str] = []
        attempts = (
            verified.runtime_root / "fastembed/__init__.cp312-win_amd64.pyd",
            verified.runtime_root / "onnxruntime/injected.dll",
            verified.specific_model_path / "transient-new-file.bin",
        )
        for path in attempts:
            try:
                path.write_bytes(b"transient-malicious-content")
            except OSError:
                blocked.append(path.name)
            else:
                path.unlink()
        return _verification_evidence(verified, createBlocked=blocked)

    result = model_cache.run_final_phase(
        manifest,
        generation_parent=tmp_path / ".embedding-model",
        quarantine_root=tmp_path / ".embedding-quarantine",
        approved_root=tmp_path,
        fetch_artifact=lambda url, _size: by_url[url],
        install_runtime=install,
        verify_generation=verify,
    )

    assert result.verification["createBlocked"] == [
        "__init__.cp312-win_amd64.pyd",
        "injected.dll",
        "transient-new-file.bin",
    ]
    restored_smoke = result.verified.runtime_root / "fastembed/dacl-restored.tmp"
    restored_smoke.write_bytes(b"restored")
    restored_smoke.unlink()


@pytest.mark.model_download
def test_phase_b_provider_origins_are_parent_bound_not_callback_ordered(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    def verify(verified: object) -> dict[str, object]:
        evidence = _verification_evidence(verified)
        evidence["childLoadedOrigins"] = list(
            reversed(evidence["childLoadedOrigins"])
        )
        return evidence

    result = model_cache.run_final_phase(
        manifest,
        generation_parent=tmp_path / ".embedding-model",
        quarantine_root=tmp_path / ".embedding-quarantine",
        approved_root=tmp_path,
        fetch_artifact=lambda url, _size: by_url[url],
        install_runtime=install,
        verify_generation=verify,
    )

    assert [
        origin["distribution"] for origin in result.verification["providerOrigins"]
    ] == ["fastembed", "onnxruntime"]


@pytest.mark.model_download
@pytest.mark.parametrize("fault_stage", ("after-apply", "after-restore"))
def test_phase_b_generation_dacl_failure_restores_all_directory_writes(
    tmp_path: Path,
    fault_stage: str,
) -> None:
    model_cache = _model_cache_module()
    generation = tmp_path / "generation"
    nested = generation / "runtime/fastembed"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_bytes(b"bound")
    injected = False

    def fault(stage: str, index: int) -> None:
        nonlocal injected
        if not injected and stage == fault_stage and index == 0:
            injected = True
            raise model_cache.ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")

    with pytest.raises(model_cache.ModelCacheError) as caught:
        with model_cache._deny_generation_tree_writes(
            generation,
            code="MODEL_FINAL_VERIFICATION_FAILED",
            _fault=fault,
        ):
            if fault_stage == "after-apply":
                raise AssertionError("partial apply fault must prevent callback")

    assert caught.value.code == "MODEL_FINAL_VERIFICATION_FAILED"
    assert injected is True
    restored = nested / f"{fault_stage}-restored.tmp"
    restored.write_bytes(b"restored")
    restored.unlink()


@pytest.mark.model_download
def test_generation_write_boundary_reports_actual_deny_identity_and_restore(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    generation = tmp_path / "generation"
    nested = generation / "runtime/fastembed"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_bytes(b"bound")

    with model_cache._deny_generation_tree_writes(
        generation,
        code="MODEL_FINAL_VERIFICATION_FAILED",
    ) as boundary:
        assert boundary.completed is False
        with pytest.raises(PermissionError):
            (nested / "denied.tmp").write_bytes(b"denied")

    evidence = model_cache._write_boundary_evidence(boundary)
    assert evidence["scope"] == "verified-generation-tree"
    assert evidence["nativeGlobalCoverage"] == "not-certified"
    assert evidence["appliedDirectoryCount"] >= 1
    assert evidence["deniedProbeCount"] == 3
    assert evidence["restoredDirectoryCount"] == evidence["deniedProbeCount"]
    restored = nested / "restored.tmp"
    restored.write_bytes(b"restored")
    restored.unlink()


@pytest.mark.model_download
def test_generation_write_boundary_preserves_existing_file_acl_bytes(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    generation = tmp_path / "generation"
    nested = generation / "runtime/fastembed"
    nested.mkdir(parents=True)
    existing = nested / "__init__.py"
    existing.write_bytes(b"bound")
    acl_before = subprocess.run(
        ["icacls", str(existing)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout

    with model_cache._hold_regular_file_handles(
        generation,
        code="MODEL_FINAL_VERIFICATION_FAILED",
    ):
        with model_cache._deny_generation_tree_writes(
            generation,
            code="MODEL_FINAL_VERIFICATION_FAILED",
        ):
            with pytest.raises(PermissionError):
                existing.write_bytes(b"tampered")

    acl_after = subprocess.run(
        ["icacls", str(existing)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    assert acl_after == acl_before
    existing.write_bytes(b"restored")


@pytest.mark.model_download
@pytest.mark.parametrize(
    "fault_target",
    ("GetFileInformationByHandle", "GetAclInformation", "acl-bytes"),
)
def test_phase_b_generation_dacl_state_capture_failure_releases_unowned_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_target: str,
) -> None:
    model_cache = _model_cache_module()
    generation = tmp_path / "generation"
    nested = generation / "runtime/fastembed"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_bytes(b"bound")
    acl_before = subprocess.run(
        ["icacls", str(generation)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout

    real_win_dll = ctypes.WinDLL
    real_string_at = ctypes.string_at
    cleanup_kernel32 = real_win_dll("kernel32", use_last_error=True)
    cleanup_close_handle = cleanup_kernel32.CloseHandle
    cleanup_close_handle.argtypes = [ctypes.c_void_p]
    cleanup_close_handle.restype = ctypes.c_int
    cleanup_local_free = cleanup_kernel32.LocalFree
    cleanup_local_free.argtypes = [ctypes.c_void_p]
    cleanup_local_free.restype = ctypes.c_void_p
    resources: dict[str, set[int] | bool] = {
        "handles": set(),
        "closed": set(),
        "security_descriptors": set(),
        "new_acls": set(),
        "freed": set(),
        "faulted": False,
    }

    def pointer_value(value: object) -> int:
        raw = getattr(value, "value", value)
        if raw is None:
            return 0
        return int(raw)

    class FunctionProxy:
        def __init__(self, name: str, function: object) -> None:
            object.__setattr__(self, "_name", name)
            object.__setattr__(self, "_function", function)

        def __getattr__(self, name: str) -> object:
            return getattr(self._function, name)

        def __setattr__(self, name: str, value: object) -> None:
            setattr(self._function, name, value)

        def __call__(self, *args: object) -> object:
            name = str(self._name)
            if name == fault_target and not resources["faulted"]:
                resources["faulted"] = True
                ctypes.set_last_error(5)
                return 0
            result = self._function(*args)
            if name == "CreateFileW":
                handle = pointer_value(result)
                if handle != ctypes.c_void_p(-1).value:
                    assert isinstance(resources["handles"], set)
                    resources["handles"].add(handle)
            elif name == "CloseHandle" and result:
                assert isinstance(resources["closed"], set)
                resources["closed"].add(pointer_value(args[0]))
            elif name == "GetSecurityInfo" and result == 0:
                descriptor = pointer_value(getattr(args[7], "_obj"))
                assert isinstance(resources["security_descriptors"], set)
                resources["security_descriptors"].add(descriptor)
            elif name == "SetEntriesInAclW" and result == 0:
                new_acl = pointer_value(getattr(args[3], "_obj"))
                assert isinstance(resources["new_acls"], set)
                resources["new_acls"].add(new_acl)
            elif name == "LocalFree":
                assert isinstance(resources["freed"], set)
                resources["freed"].add(pointer_value(args[0]))
            return result

    class DllProxy:
        def __init__(self, dll: object) -> None:
            self._dll = dll
            self._functions: dict[str, FunctionProxy] = {}

        def __getattr__(self, name: str) -> object:
            if name not in self._functions:
                self._functions[name] = FunctionProxy(name, getattr(self._dll, name))
            return self._functions[name]

    def traced_win_dll(name: str, *args: object, **kwargs: object) -> object:
        return DllProxy(real_win_dll(name, *args, **kwargs))

    def failing_string_at(pointer: object, size: int = -1) -> bytes:
        if fault_target == "acl-bytes" and not resources["faulted"]:
            resources["faulted"] = True
            raise OSError("injected ACL byte copy failure")
        return real_string_at(pointer, size)

    monkeypatch.setattr(ctypes, "WinDLL", traced_win_dll)
    monkeypatch.setattr(ctypes, "string_at", failing_string_at)
    try:
        with pytest.raises(model_cache.ModelCacheError) as caught:
            with model_cache._deny_generation_tree_writes(
                generation,
                code="MODEL_FINAL_VERIFICATION_FAILED",
            ):
                raise AssertionError("state capture fault must prevent callback")

        assert caught.value.code == "MODEL_FINAL_VERIFICATION_FAILED"
        assert resources["faulted"] is True
        handles = resources["handles"]
        closed = resources["closed"]
        security_descriptors = resources["security_descriptors"]
        new_acls = resources["new_acls"]
        freed = resources["freed"]
        assert isinstance(handles, set)
        assert isinstance(closed, set)
        assert isinstance(security_descriptors, set)
        assert isinstance(new_acls, set)
        assert isinstance(freed, set)
        assert handles and handles <= closed
        assert security_descriptors and security_descriptors <= freed
        assert new_acls and new_acls <= freed
        monkeypatch.undo()
        acl_after = subprocess.run(
            ["icacls", str(generation)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        assert acl_after == acl_before
        renamed = tmp_path / "renamed-after-capture-failure"
        os.replace(generation, renamed)
        shutil.rmtree(renamed)
        assert renamed.exists() is False
    finally:
        monkeypatch.undo()
        handles = resources["handles"]
        closed = resources["closed"]
        security_descriptors = resources["security_descriptors"]
        new_acls = resources["new_acls"]
        freed = resources["freed"]
        assert isinstance(handles, set)
        assert isinstance(closed, set)
        assert isinstance(security_descriptors, set)
        assert isinstance(new_acls, set)
        assert isinstance(freed, set)
        for pointer in (security_descriptors | new_acls) - freed:
            cleanup_local_free(ctypes.c_void_p(pointer))
        for handle in handles - closed:
            cleanup_close_handle(ctypes.c_void_p(handle))


@pytest.mark.parametrize(
    ("attestation", "value"),
    (
        ("pointer-bits", 4),
        ("platform-tag", "win32"),
        ("cache-tag", "cpython-311"),
        ("extension-suffixes", (".pyd",)),
    ),
)
def test_phase_b_host_attestation_rejects_abi_or_bitness_mismatch_before_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attestation: str,
    value: object,
) -> None:
    model_cache = _model_cache_module()
    manifest, _model_payloads, _wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    if attestation == "pointer-bits":
        monkeypatch.setattr(
            model_cache,
            "struct",
            SimpleNamespace(calcsize=lambda _format: value),
            raising=False,
        )
    elif attestation == "platform-tag":
        monkeypatch.setattr(
            model_cache,
            "sysconfig",
            SimpleNamespace(get_platform=lambda: value),
            raising=False,
        )
    elif attestation == "cache-tag":
        monkeypatch.setattr(
            model_cache.sys,
            "implementation",
            SimpleNamespace(name="cpython", cache_tag=value),
        )
    else:
        monkeypatch.setattr(
            model_cache,
            "EXTENSION_SUFFIXES",
            value,
            raising=False,
        )
    called = False

    def fetch(_url: str, _size: int) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("fetch must not run")

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.run_final_phase(
            manifest,
            generation_parent=tmp_path / ".embedding-model",
            quarantine_root=tmp_path / ".embedding-quarantine",
            approved_root=tmp_path,
            fetch_artifact=fetch,
            install_runtime=lambda *_args: None,
            verify_generation=lambda _verified: {},
        )

    assert caught.value.code == "MODEL_FINAL_HOST_MISMATCH"
    assert called is False


@pytest.mark.model_download
def test_phase_b_verification_hook_runs_with_all_socket_entrypoints_denied(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    def verify(verified: object) -> dict[str, object]:
        denied_codes: list[str] = []
        for operation in (
            lambda: socket.socket(),
            lambda: socket.create_connection(("example.invalid", 443)),
            lambda: socket.getaddrinfo("example.invalid", 443),
        ):
            with pytest.raises(model_cache.ModelCacheError) as caught:
                operation()
            denied_codes.append(caught.value.code)
        return _verification_evidence(verified, socketDenied=denied_codes)

    result = model_cache.run_final_phase(
        manifest,
        generation_parent=tmp_path / ".embedding-model",
        quarantine_root=tmp_path / ".embedding-quarantine",
        approved_root=tmp_path,
        fetch_artifact=lambda url, _size: by_url[url],
        install_runtime=install,
        verify_generation=verify,
    )

    assert result.verification["socketDenied"] == [
        "MODEL_FINAL_NETWORK_FORBIDDEN"
    ] * 3


def test_phase_b_local_installer_is_hash_locked_binary_only_and_no_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    manifest, _model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    wheelhouse = tmp_path / "wheelhouse"
    runtime_root = tmp_path / "runtime"
    wheelhouse.mkdir()
    runtime_root.mkdir()
    for filename, payload in wheel_payloads.items():
        (wheelhouse / filename).write_bytes(payload)
    captured: dict[str, object] = {}

    def run_bounded(command: list[str], **kwargs: object) -> None:
        captured.update(command=command, kwargs=kwargs)

    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("HTTPS_PROXY", "http://example.invalid:8080")
    monkeypatch.setattr(model_cache, "_run_bounded_subprocess", run_bounded)

    model_cache.install_locked_runtime(
        manifest.runtime,
        wheelhouse,
        runtime_root,
    )

    command = captured["command"]
    assert all(
        flag in command
        for flag in (
            "--isolated",
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-compile",
        )
    )
    environment = captured["kwargs"]["env"]
    assert environment["PIP_NO_INDEX"] == "1"
    assert "PIP_INDEX_URL" not in environment
    assert "HTTPS_PROXY" not in environment
    assert captured["kwargs"]["cwd"] == wheelhouse.parent
    assert captured["kwargs"]["active_process_limit"] == 1
    lock = (tmp_path / "requirements.lock").read_text(encoding="utf-8")
    for wheel in manifest.runtime.wheels:
        assert f"{wheel.name}=={wheel.version} --hash=sha256:{wheel.sha256}" in lock


@pytest.mark.model_download
@pytest.mark.parametrize("failure_mode", ("output-limit", "timeout"))
def test_phase_b_local_installer_bounds_output_and_cleans_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    model_cache = _model_cache_module()
    monkeypatch.setattr(model_cache, "_MAX_RUNTIME_INSTALL_OUTPUT_BYTES", 128)
    monkeypatch.setattr(model_cache, "_RUNTIME_INSTALL_TIMEOUT_SECONDS", 0.05)
    snippet = (
        "import os; os.write(1, b'x' * 65536)"
        if failure_mode == "output-limit"
        else "import time; time.sleep(30)"
    )

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._run_bounded_subprocess(
            [sys.executable, "-I", "-c", snippet],
            cwd=tmp_path,
            env={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
        )

    assert caught.value.code == "MODEL_RUNTIME_INSTALL_FAILED"
    assert not tuple(tmp_path.glob(".pip-output-*"))
    assert not tuple(
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("bounded-subprocess-reader-")
    )


def test_phase_b_cleanup_refuses_swapped_directory_identity(
    tmp_path: Path,
) -> None:
    model_cache = _model_cache_module()
    parent = tmp_path / "parent"
    target = parent / "phase-b-created"
    parent.mkdir()
    target.mkdir()
    (target / "owned.bin").write_bytes(b"owned")
    identity = model_cache._directory_identity(target)
    original = parent / "original-moved"
    os.replace(target, original)
    target.mkdir()
    (target / "attacker.bin").write_bytes(b"must-survive")

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._remove_contained_tree(
            tmp_path,
            parent,
            target,
            code="MODEL_FINAL_ROLLBACK_FAILED",
            expected_identity=identity,
        )

    assert caught.value.code == "MODEL_FINAL_ROLLBACK_FAILED"
    assert (target / "attacker.bin").read_bytes() == b"must-survive"
    assert (original / "owned.bin").read_bytes() == b"owned"


@pytest.mark.model_download
def test_phase_b_rollback_attempts_target_and_quarantine_cleanup_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    manifest, model_payloads, wheel_payloads = _synthetic_phase_b_fixture(model_cache)
    generation_parent = tmp_path / ".embedding-model"
    quarantine_parent = tmp_path / ".embedding-quarantine"
    by_url = {
        **{
            member.artifact_url: model_payloads[member.path]
            for member in manifest.files
        },
        **{
            wheel.artifact_url: wheel_payloads[wheel.filename]
            for wheel in manifest.runtime.wheels
        },
    }

    def install(runtime: object, wheelhouse: Path, runtime_root: Path) -> None:
        _install_synthetic_runtime(runtime, wheelhouse, runtime_root)

    calls: list[Path] = []
    real_remove = model_cache._remove_contained_tree

    def remove(
        approved_root: Path,
        parent: Path,
        target: Path,
        **kwargs: object,
    ) -> None:
        calls.append(target)
        if parent == generation_parent:
            raise model_cache.ModelCacheError("MODEL_FINAL_ROLLBACK_FAILED")
        real_remove(approved_root, parent, target, **kwargs)

    monkeypatch.setattr(model_cache, "_remove_contained_tree", remove)

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache.run_final_phase(
            manifest,
            generation_parent=generation_parent,
            quarantine_root=quarantine_parent,
            approved_root=tmp_path,
            fetch_artifact=lambda url, _size: by_url[url],
            install_runtime=install,
            verify_generation=lambda _verified: (_ for _ in ()).throw(
                RuntimeError("verification failure")
            ),
        )

    assert caught.value.code == "MODEL_FINAL_ROLLBACK_FAILED"
    assert len(calls) == 2
    assert calls[0].parent == generation_parent
    assert calls[1].parent == quarantine_parent
    assert list(quarantine_parent.iterdir()) == []


@pytest.mark.model_download
def test_phase_b_pip_output_limit_rejects_one_unbuffered_burst(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    monkeypatch.setattr(model_cache, "_MAX_RUNTIME_INSTALL_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(model_cache, "_RUNTIME_INSTALL_TIMEOUT_SECONDS", 5.0)

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._run_bounded_subprocess(
            [
                sys.executable,
                "-I",
                "-c",
                "import os; os.write(1, b'x' * 1048576)",
            ],
            cwd=tmp_path,
            env={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
        )

    assert caught.value.code == "MODEL_RUNTIME_INSTALL_FAILED"


@pytest.mark.model_download
def test_phase_b_pip_failure_terminates_suspended_job_grandchild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    marker = tmp_path / "grandchild-survived.txt"
    monkeypatch.setattr(model_cache, "_MAX_RUNTIME_INSTALL_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(model_cache, "_RUNTIME_INSTALL_TIMEOUT_SECONDS", 5.0)
    grandchild = (
        "import time; from pathlib import Path; time.sleep(0.5); "
        f"Path({str(marker)!r}).write_bytes(b'survived')"
    )
    parent = (
        "import os, subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-I', '-c', {grandchild!r}]); "
        "os.write(1, b'x' * 1048576); time.sleep(30)"
    )

    with pytest.raises(model_cache.ModelCacheError) as caught:
        model_cache._run_bounded_subprocess(
            [sys.executable, "-I", "-c", parent],
            cwd=tmp_path,
            env={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
        )

    assert caught.value.code == "MODEL_RUNTIME_INSTALL_FAILED"
    time.sleep(0.8)
    assert marker.exists() is False


@pytest.mark.model_download
def test_phase_b_pip_job_limit_one_rejects_child_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = _model_cache_module()
    blocked = tmp_path / "child-blocked.txt"
    child_ran = tmp_path / "child-ran.txt"
    monkeypatch.setattr(model_cache, "_MAX_RUNTIME_INSTALL_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(model_cache, "_RUNTIME_INSTALL_TIMEOUT_SECONDS", 5.0)
    child = f"from pathlib import Path; Path({str(child_ran)!r}).write_bytes(b'ran')"
    parent = "\n".join(
        (
            "import subprocess, sys",
            "from pathlib import Path",
            "try:",
            f"    subprocess.Popen([sys.executable, '-I', '-c', {child!r}])",
            "except OSError:",
            f"    Path({str(blocked)!r}).write_bytes(b'blocked')",
        )
    )

    model_cache._run_bounded_subprocess(
        [sys.executable, "-I", "-c", parent],
        cwd=tmp_path,
        env={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
        active_process_limit=1,
    )

    assert blocked.read_bytes() == b"blocked"
    time.sleep(0.2)
    assert child_ran.exists() is False
