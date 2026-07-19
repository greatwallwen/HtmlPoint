"""Pinned manifest parsing and zero-network model-cache verification."""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy as email_policy
from email.parser import BytesParser
from importlib.machinery import EXTENSION_SUFFIXES, PathFinder
from importlib import metadata as importlib_metadata
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version


PINNED_MANIFEST_ID = "bge-small-zh-v1.5-fastembed-0.8.0"
PINNED_MODEL_ID = "BAAI/bge-small-zh-v1.5"
PINNED_MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
PINNED_ARTIFACT_REPOSITORY = "Qdrant/bge-small-zh-v1.5"
PINNED_ARTIFACT_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
PINNED_FASTEMBED_WHEEL = "fastembed-0.8.0-py3-none-any.whl"
PINNED_FASTEMBED_WHEEL_SIZE = 116572
PINNED_FASTEMBED_WHEEL_SHA256 = (
    "40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0"
)
PINNED_MODEL_METADATA_URL = (
    "https://huggingface.co/api/models/Qdrant/bge-small-zh-v1.5/revision/"
    f"{PINNED_ARTIFACT_REVISION}?blobs=true"
)

_PINNED_MEMBERS = {
    "config.json": (
        739,
        "git-blob-sha1",
        "60938626ad1097a0c1a14be4f8340e32c714a056",
    ),
    "model_optimized.onnx": (
        94781076,
        "lfs-sha256",
        "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
    ),
    "special_tokens_map.json": (
        125,
        "git-blob-sha1",
        "a8b3208c2884c4efb86e49300fdd3dc877220cdf",
    ),
    "tokenizer.json": (
        439125,
        "git-blob-sha1",
        "cdb3043fc938fc918c06e66cf704c2ba58f88747",
    ),
    "tokenizer_config.json": (
        367,
        "git-blob-sha1",
        "3a59388f0fd1bd22dec2ce7902c1be8e1fb84107",
    ),
}
_PINNED_MEMBER_ORDER = tuple(sorted(_PINNED_MEMBERS))
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}$")
_MAX_RUNTIME_RESOLUTION_STEPS = 4096
_MAX_RUNTIME_METADATA_REQUESTS = 512
_MAX_RUNTIME_METADATA_BYTES = 128_000_000
_MAX_RUNTIME_RESOLUTION_SECONDS = 120.0
_MAX_RUNTIME_WHEEL_BYTES = 128_000_000
_MAX_RUNTIME_WHEEL_SET_BYTES = 512_000_000
_MAX_WHEEL_ARCHIVE_MEMBERS = 20_000
_MAX_WHEEL_UNCOMPRESSED_BYTES = 1_073_741_824
_MAX_WHEEL_MEMBER_UNCOMPRESSED_BYTES = 536_870_912
_MAX_WHEEL_COMPRESSION_RATIO = 200
_MAX_WHEEL_SET_MEMBERS = 50_000
_MAX_WHEEL_SET_UNCOMPRESSED_BYTES = 2_147_483_648
_MAX_RUNTIME_INSTALL_OUTPUT_BYTES = 2_000_000
_RUNTIME_INSTALL_TIMEOUT_SECONDS = 300.0
_TARGET_WHEEL_TAGS = frozenset(
    (
        *cpython_tags(
            (3, 12),
            abis=("cp312", "abi3", "none"),
            platforms=("win_amd64",),
        ),
        *compatible_tags(
            (3, 12),
            interpreter="cp312",
            platforms=("win_amd64",),
        ),
    )
)
_TARGET_MARKER_ENVIRONMENT = {
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


def _wheel_filename_targets_runtime(filename: str) -> bool:
    try:
        _name, _version, _build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return False
    return not tags.isdisjoint(_TARGET_WHEEL_TAGS)


class ModelCacheError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PackageIdentity:
    name: str
    version: str
    wheel_filename: str
    wheel_sha256: str
    wheel_size: int = PINNED_FASTEMBED_WHEEL_SIZE


@dataclass(frozen=True)
class ModelIdentity:
    id: str
    revision: str
    artifact_repository: str
    artifact_revision: str
    dimension: int
    normalized: bool
    encoding_policy: str


@dataclass(frozen=True)
class OfficialIdentity:
    kind: Literal["git-blob-sha1", "lfs-sha256"]
    digest: str


@dataclass(frozen=True)
class BootstrapModelMember:
    path: str
    size: int
    sha256: str | None
    official_identity: OfficialIdentity
    artifact_url: str


@dataclass(frozen=True)
class ModelMember:
    path: str
    size: int
    sha256: str
    official_identity: OfficialIdentity | None = None
    artifact_url: str | None = None


@dataclass(frozen=True)
class RuntimeWheel:
    name: str
    version: str
    filename: str
    size: int
    sha256: str
    artifact_url: str
    requires_python: str | None = None


@dataclass(frozen=True)
class RuntimeIdentity:
    python: str
    os: str
    architecture: str
    wheels: tuple[RuntimeWheel, ...]


@dataclass(frozen=True)
class BootstrapModelManifest:
    schema_version: int
    manifest_id: str
    phase: Literal["bootstrap-required"]
    package: PackageIdentity
    model: ModelIdentity
    files: tuple[BootstrapModelMember, ...]
    aggregate_digest: str


@dataclass(frozen=True)
class ModelManifest:
    schema_version: int
    manifest_id: str
    package: PackageIdentity
    model: ModelIdentity
    files: tuple[ModelMember, ...]
    aggregate_digest: str
    phase: Literal["complete"] = "complete"
    runtime: RuntimeIdentity | None = None


@dataclass(frozen=True)
class VerifiedModelCache:
    manifest: ModelManifest
    specific_model_path: Path
    cache_digest: str
    generation_root: Path | None = None
    runtime_root: Path | None = None
    runtime_digest: str | None = None
    wheel_set_digest: str | None = None
    generation_digest: str | None = None


@dataclass(frozen=True)
class FinalPhaseResult:
    verified: VerifiedModelCache
    quarantine_root: Path
    verification: Mapping[str, object]
    promoted_new: bool
    write_boundary: object | None = None


@dataclass(frozen=True)
class _WheelInstallContract:
    name: str
    version: str
    requires_python: str | None
    requirements: tuple[str, ...]
    dist_info: str
    record_path: str
    expected_files: tuple[tuple[str, str | None, int | None], ...]
    generated_scripts: tuple[str, ...]
    member_count: int
    uncompressed_size: int


@dataclass(frozen=True)
class _DirectoryIdentity:
    volume: int
    file_id: int


@dataclass
class _WriteBoundaryResult:
    root_identity: _DirectoryIdentity
    applied_directory_count: int
    denied_probe_count: int
    restored_directory_count: int = 0
    identities_verified: bool = False
    acl_restored: bool = False
    completed: bool = False


def _write_boundary_evidence(value: object) -> dict[str, object]:
    if (
        type(value) is not _WriteBoundaryResult
        or not value.completed
        or not value.acl_restored
        or not value.identities_verified
        or value.applied_directory_count <= 0
        or value.denied_probe_count <= 0
        or value.restored_directory_count != value.denied_probe_count
    ):
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    identity_digest = hashlib.sha256(
        _canonical_bytes(
            {"volume": value.root_identity.volume, "fileId": value.root_identity.file_id}
        )
    ).hexdigest()
    return {
        "schemaVersion": 1,
        "scope": "verified-generation-tree",
        "status": "write-denied",
        "nativeGlobalCoverage": "not-certified",
        "appliedDirectoryCount": value.applied_directory_count,
        "deniedProbeCount": value.denied_probe_count,
        "restoredDirectoryCount": value.restored_directory_count,
        "rootIdentityDigest": identity_digest,
        "identitiesVerified": True,
        "aclRestored": True,
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelCacheError("MODEL_MANIFEST_DUPLICATE_KEY")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ModelCacheError("MODEL_MANIFEST_INVALID") from error
    return _decode_manifest_bytes(raw)


def _decode_manifest_bytes(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > 2_000_000:
        raise ModelCacheError("MODEL_MANIFEST_INVALID")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except ModelCacheError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ModelCacheError("MODEL_MANIFEST_INVALID") from error
    if not isinstance(payload, dict):
        raise ModelCacheError("MODEL_MANIFEST_INVALID")
    return payload


def _exact_mapping(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ModelCacheError(code)
    return value


def _validate_aggregate(payload: dict[str, Any]) -> str:
    aggregate = payload.get("aggregateDigest")
    if not isinstance(aggregate, str) or _SHA256.fullmatch(aggregate) is None:
        raise ModelCacheError("MODEL_MANIFEST_INCOMPLETE")
    unsigned = dict(payload)
    unsigned.pop("aggregateDigest", None)
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != aggregate:
        raise ModelCacheError("MODEL_MANIFEST_DIGEST_MISMATCH")
    return aggregate


def _safe_member_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ModelCacheError("MODEL_MANIFEST_PATH_INVALID")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or ":" in value
        or value.startswith("//")
    ):
        raise ModelCacheError("MODEL_MANIFEST_PATH_INVALID")
    return value


def _model_artifact_url(path: str) -> str:
    return (
        "https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/"
        f"{PINNED_ARTIFACT_REVISION}/{path}?download=true"
    )


def _parse_package(value: object) -> PackageIdentity:
    package = _exact_mapping(
        value,
        {"name", "version", "wheelFilename", "wheelSize", "wheelSha256"},
        "MODEL_MANIFEST_INVALID",
    )
    if (
        package["name"] != "fastembed"
        or package["version"] != "0.8.0"
        or package["wheelFilename"] != PINNED_FASTEMBED_WHEEL
        or type(package["wheelSize"]) is not int
        or package["wheelSize"] != PINNED_FASTEMBED_WHEEL_SIZE
        or package["wheelSha256"] != PINNED_FASTEMBED_WHEEL_SHA256
    ):
        raise ModelCacheError("MODEL_MANIFEST_IDENTITY_MISMATCH")
    return PackageIdentity(
        name="fastembed",
        version="0.8.0",
        wheel_filename=PINNED_FASTEMBED_WHEEL,
        wheel_size=PINNED_FASTEMBED_WHEEL_SIZE,
        wheel_sha256=PINNED_FASTEMBED_WHEEL_SHA256,
    )


def _parse_model(value: object) -> ModelIdentity:
    model = _exact_mapping(
        value,
        {
            "id",
            "revision",
            "artifactRepository",
            "artifactRevision",
            "dimension",
            "normalized",
            "encodingPolicy",
        },
        "MODEL_MANIFEST_INVALID",
    )
    if (
        model["id"] != PINNED_MODEL_ID
        or model["revision"] != PINNED_MODEL_REVISION
        or model["artifactRepository"] != PINNED_ARTIFACT_REPOSITORY
        or model["artifactRevision"] != PINNED_ARTIFACT_REVISION
        or type(model["dimension"]) is not int
        or model["dimension"] != 512
        or model["normalized"] is not True
        or model["encodingPolicy"] != "utf8-nfkc-no-prefix"
    ):
        raise ModelCacheError("MODEL_MANIFEST_IDENTITY_MISMATCH")
    return ModelIdentity(
        id=PINNED_MODEL_ID,
        revision=PINNED_MODEL_REVISION,
        artifact_repository=PINNED_ARTIFACT_REPOSITORY,
        artifact_revision=PINNED_ARTIFACT_REVISION,
        dimension=512,
        normalized=True,
        encoding_policy="utf8-nfkc-no-prefix",
    )


def _parse_bootstrap_files(value: object) -> tuple[BootstrapModelMember, ...]:
    if not isinstance(value, list) or len(value) != len(_PINNED_MEMBER_ORDER):
        raise ModelCacheError("MODEL_MANIFEST_INVENTORY_MISMATCH")
    members: list[BootstrapModelMember] = []
    for expected_path, raw in zip(_PINNED_MEMBER_ORDER, value):
        item = _exact_mapping(
            raw,
            {"path", "size", "sha256", "officialIdentity", "artifactUrl"},
            "MODEL_MANIFEST_INVALID",
        )
        path = _safe_member_path(item["path"])
        expected_size, expected_kind, expected_digest = _PINNED_MEMBERS[expected_path]
        identity = _exact_mapping(
            item["officialIdentity"], {"kind", "digest"}, "MODEL_MANIFEST_INVALID"
        )
        if (
            path != expected_path
            or type(item["size"]) is not int
            or item["size"] != expected_size
            or identity["kind"] != expected_kind
            or identity["digest"] != expected_digest
            or item["artifactUrl"] != _model_artifact_url(expected_path)
        ):
            raise ModelCacheError("MODEL_MANIFEST_IDENTITY_MISMATCH")
        sha256 = item["sha256"]
        if expected_kind == "lfs-sha256":
            if sha256 != expected_digest:
                raise ModelCacheError("MODEL_MANIFEST_IDENTITY_MISMATCH")
        elif sha256 is not None and (
            not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None
        ):
            raise ModelCacheError("MODEL_MANIFEST_INCOMPLETE")
        members.append(
            BootstrapModelMember(
                path=path,
                size=expected_size,
                sha256=sha256,
                official_identity=OfficialIdentity(expected_kind, expected_digest),  # type: ignore[arg-type]
                artifact_url=item["artifactUrl"],
            )
        )
    return tuple(members)


def _parse_runtime(value: object) -> RuntimeIdentity:
    runtime = _exact_mapping(
        value, {"python", "os", "architecture", "wheels"}, "MODEL_RUNTIME_WHEEL_LOCK_INVALID"
    )
    if (
        runtime["python"] != "3.12"
        or runtime["os"] != "windows"
        or runtime["architecture"] != "x86_64"
        or not isinstance(runtime["wheels"], list)
        or not runtime["wheels"]
        or len(runtime["wheels"]) > 128
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    wheels: list[RuntimeWheel] = []
    total_wheel_bytes = 0
    for raw in runtime["wheels"]:
        item = _exact_mapping(
            raw,
            {"name", "version", "filename", "size", "sha256", "artifactUrl"},
            "MODEL_RUNTIME_WHEEL_LOCK_INVALID",
        )
        if (
            not isinstance(item["name"], str)
            or _SAFE_PACKAGE.fullmatch(item["name"]) is None
            or not isinstance(item["version"], str)
            or _SAFE_VERSION.fullmatch(item["version"]) is None
            or not isinstance(item["filename"], str)
            or not item["filename"].endswith(".whl")
            or type(item["size"]) is not int
            or item["size"] <= 0
            or item["size"] > _MAX_RUNTIME_WHEEL_BYTES
            or not isinstance(item["sha256"], str)
            or _SHA256.fullmatch(item["sha256"]) is None
            or not isinstance(item["artifactUrl"], str)
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
        parsed = urlsplit(item["artifactUrl"])
        if (
            parsed.scheme != "https"
            or parsed.hostname != "files.pythonhosted.org"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or PurePosixPath(unquote(parsed.path)).name != item["filename"]
            or not parsed.path.startswith("/packages/")
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
        if not _wheel_filename_targets_runtime(item["filename"]):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
        try:
            filename_name, filename_version, _build, _tags = parse_wheel_filename(
                item["filename"]
            )
            declared_version = Version(item["version"])
        except (InvalidWheelFilename, InvalidVersion) as error:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID") from error
        if (
            canonicalize_name(str(filename_name))
            != canonicalize_name(item["name"])
            or filename_version != declared_version
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
        total_wheel_bytes += item["size"]
        if total_wheel_bytes > _MAX_RUNTIME_WHEEL_SET_BYTES:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
        wheels.append(
            RuntimeWheel(
                name=item["name"],
                version=item["version"],
                filename=item["filename"],
                size=item["size"],
                sha256=item["sha256"],
                artifact_url=item["artifactUrl"],
            )
        )
    normalized_names = [wheel.name.replace("_", "-").casefold() for wheel in wheels]
    if normalized_names != sorted(normalized_names) or len(set(normalized_names)) != len(wheels):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    by_name = {name: wheel for name, wheel in zip(normalized_names, wheels)}
    fastembed = by_name.get("fastembed")
    onnxruntime = by_name.get("onnxruntime")
    if (
        fastembed is None
        or fastembed.version != "0.8.0"
        or fastembed.filename != PINNED_FASTEMBED_WHEEL
        or fastembed.size != PINNED_FASTEMBED_WHEEL_SIZE
        or fastembed.sha256 != PINNED_FASTEMBED_WHEEL_SHA256
        or onnxruntime is None
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    return RuntimeIdentity("3.12", "windows", "x86_64", tuple(wheels))


def _parse_root_payload(
    payload: dict[str, Any],
) -> tuple[
    dict[str, Any],
    str,
    PackageIdentity,
    ModelIdentity,
    tuple[BootstrapModelMember, ...],
]:
    root = _exact_mapping(
        payload,
        {
            "schemaVersion",
            "manifestId",
            "phase",
            "package",
            "model",
            "files",
            "runtime",
            "aggregateDigest",
        },
        "MODEL_MANIFEST_INVALID",
    )
    if type(root["schemaVersion"]) is not int or root["schemaVersion"] != 1:
        raise ModelCacheError("MODEL_MANIFEST_INVALID")
    if root["manifestId"] != PINNED_MANIFEST_ID:
        raise ModelCacheError("MODEL_MANIFEST_IDENTITY_MISMATCH")
    aggregate = _validate_aggregate(root)
    package = _parse_package(root["package"])
    model = _parse_model(root["model"])
    files = _parse_bootstrap_files(root["files"])
    return root, aggregate, package, model, files


def _parse_root(path: Path) -> tuple[dict[str, Any], str, PackageIdentity, ModelIdentity, tuple[BootstrapModelMember, ...]]:
    return _parse_root_payload(_read_json(path))


def _bootstrap_manifest_from_root(
    root: dict[str, Any],
    aggregate: str,
    package: PackageIdentity,
    model: ModelIdentity,
    files: tuple[BootstrapModelMember, ...],
) -> BootstrapModelManifest:
    if root["phase"] != "bootstrap-required":
        raise ModelCacheError("MODEL_MANIFEST_PHASE_INVALID")
    runtime = _exact_mapping(
        root["runtime"],
        {"python", "os", "architecture", "wheels"},
        "MODEL_RUNTIME_WHEEL_LOCK_INVALID",
    )
    if runtime != {
        "python": "3.12",
        "os": "windows",
        "architecture": "x86_64",
        "wheels": "bootstrap-required",
    }:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    for member in files:
        if member.official_identity.kind == "git-blob-sha1" and member.sha256 is not None:
            raise ModelCacheError("MODEL_MANIFEST_PHASE_INVALID")
    return BootstrapModelManifest(1, PINNED_MANIFEST_ID, "bootstrap-required", package, model, files, aggregate)


def load_bootstrap_manifest(path: Path) -> BootstrapModelManifest:
    root, aggregate, package, model, files = _parse_root(path)
    return _bootstrap_manifest_from_root(root, aggregate, package, model, files)


def load_bootstrap_manifest_bytes(raw: bytes) -> BootstrapModelManifest:
    return _bootstrap_manifest_from_root(
        *_parse_root_payload(_decode_manifest_bytes(raw))
    )


def load_model_manifest(path: Path) -> ModelManifest:
    root, aggregate, package, model, bootstrap_files = _parse_root(path)
    return _model_manifest_from_root(
        root, aggregate, package, model, bootstrap_files
    )


def load_model_manifest_bytes(raw: bytes) -> ModelManifest:
    return _model_manifest_from_root(
        *_parse_root_payload(_decode_manifest_bytes(raw))
    )


def _model_manifest_from_root(
    root: dict[str, Any],
    aggregate: str,
    package: PackageIdentity,
    model: ModelIdentity,
    bootstrap_files: tuple[BootstrapModelMember, ...],
) -> ModelManifest:
    if root["phase"] == "bootstrap-required":
        raise ModelCacheError("MODEL_MANIFEST_BOOTSTRAP_REQUIRED")
    if root["phase"] != "complete":
        raise ModelCacheError("MODEL_MANIFEST_PHASE_INVALID")
    files: list[ModelMember] = []
    for member in bootstrap_files:
        if member.sha256 is None or _SHA256.fullmatch(member.sha256) is None:
            raise ModelCacheError("MODEL_MANIFEST_INCOMPLETE")
        files.append(
            ModelMember(
                path=member.path,
                size=member.size,
                sha256=member.sha256,
                official_identity=member.official_identity,
                artifact_url=member.artifact_url,
            )
        )
    runtime = _parse_runtime(root["runtime"])
    return ModelManifest(
        schema_version=1,
        manifest_id=PINNED_MANIFEST_ID,
        package=package,
        model=model,
        files=tuple(files),
        aggregate_digest=aggregate,
        runtime=runtime,
    )


def _is_reparse_or_link(path: Path) -> bool:
    info = os.lstat(path)
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


@contextmanager
def _hold_contained_directory_handles(
    approved_root: Path,
    target_directory: Path,
    *,
    code: str = "MODEL_BOOTSTRAP_PATH_INVALID",
):
    """Bind every existing directory component and deny rename/delete swaps."""

    approved = approved_root.absolute()
    target = target_directory.absolute()
    try:
        relative = target.relative_to(approved)
    except ValueError as error:
        raise ModelCacheError(code) from error
    paths = [approved]
    current = approved
    for part in relative.parts:
        current = current / part
        paths.append(current)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handles: list[object] = []
        volume: int | None = None
        try:
            for path in paths:
                handle = create_file(
                    str(path),
                    0x80000000,
                    0x00000001 | 0x00000002,
                    None,
                    3,
                    0x02000000 | 0x00200000,
                    None,
                )
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
                info = _ByHandleFileInformation()
                if not get_info(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400:
                    raise ModelCacheError(code)
                if volume is None:
                    volume = int(info.dwVolumeSerialNumber)
                elif volume != int(info.dwVolumeSerialNumber):
                    raise ModelCacheError(code)
            yield tuple(handles)
        except ModelCacheError:
            raise
        except OSError as error:
            raise ModelCacheError(code) from error
        finally:
            for handle in reversed(handles):
                close_handle(handle)
        return
    descriptors: list[int] = []
    snapshots: list[tuple[int, int]] = []
    try:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for path in paths:
            descriptor = os.open(path, flags)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise ModelCacheError(code)
            descriptors.append(descriptor)
            snapshots.append((info.st_dev, info.st_ino))
        yield tuple(descriptors)
        for descriptor, snapshot in zip(descriptors, snapshots):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != snapshot:
                raise ModelCacheError(code)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _hold_regular_file_handles(
    root: Path,
    *,
    code: str,
):
    """Bind every current regular file and deny write/delete replacement."""

    try:
        if _is_reparse_or_link(root) or not root.is_dir():
            raise ModelCacheError(code)
        files = tuple(
            sorted(
                (entry for entry in root.rglob("*") if entry.is_file()),
                key=lambda entry: entry.as_posix(),
            )
        )
        if not files or len(files) > 200_000:
            raise ModelCacheError(code)
        if any(_is_reparse_or_link(entry) for entry in files):
            raise ModelCacheError(code)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handles: list[object] = []
        identities: list[tuple[int, int, int]] = []
        try:
            for path in files:
                handle = create_file(
                    str(path),
                    0x80000000,
                    0x00000001,
                    None,
                    3,
                    0x00200000 | 0x08000000,
                    None,
                )
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
                info = _ByHandleFileInformation()
                if not get_info(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.dwFileAttributes & (0x10 | 0x400):
                    raise ModelCacheError(code)
                identities.append(
                    (
                        int(info.dwVolumeSerialNumber),
                        (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
                        (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
                    )
                )
            yield tuple(handles)
            for handle, identity in zip(handles, identities):
                info = _ByHandleFileInformation()
                if not get_info(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                current = (
                    int(info.dwVolumeSerialNumber),
                    (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
                    (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
                )
                if current != identity:
                    raise ModelCacheError(code)
        except ModelCacheError:
            raise
        except OSError as error:
            raise ModelCacheError(code) from error
        finally:
            for handle in reversed(handles):
                close_handle(handle)
        return
    descriptors: list[int] = []
    identities: list[tuple[int, int, int]] = []
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        for path in files:
            descriptor = os.open(path, flags)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ModelCacheError(code)
            descriptors.append(descriptor)
            identities.append((info.st_dev, info.st_ino, info.st_size))
        yield tuple(descriptors)
        for descriptor, identity in zip(descriptors, identities):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino, info.st_size) != identity:
                raise ModelCacheError(code)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _deny_generation_tree_writes(
    root: Path,
    *,
    code: str,
    _fault: Callable[[str, int], None] | None = None,
):
    """Temporarily deny create/write/DACL changes on every bound directory."""

    if os.name != "nt":
        raise ModelCacheError(code)
    import ctypes
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _Trustee(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", ctypes.c_void_p),
        ]

    class _ExplicitAccess(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _Trustee),
        ]

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    get_file_info = kernel32.GetFileInformationByHandle
    get_file_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    get_file_info.restype = wintypes.BOOL
    get_security_info = advapi32.GetSecurityInfo
    get_security_info.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security_info.restype = wintypes.DWORD
    set_security_info = advapi32.SetSecurityInfo
    set_security_info.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    set_security_info.restype = wintypes.DWORD
    set_kernel_object_security = advapi32.SetKernelObjectSecurity
    set_kernel_object_security.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    set_kernel_object_security.restype = wintypes.BOOL
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_control.restype = wintypes.BOOL
    get_acl_information = advapi32.GetAclInformation
    get_acl_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    get_acl_information.restype = wintypes.BOOL
    create_well_known_sid = advapi32.CreateWellKnownSid
    create_well_known_sid.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    create_well_known_sid.restype = wintypes.BOOL
    set_entries = advapi32.SetEntriesInAclW
    set_entries.argtypes = [
        wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccess),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    set_entries.restype = wintypes.DWORD

    def identity(handle: object) -> _DirectoryIdentity:
        info = _ByHandleFileInformation()
        if not get_file_info(handle, ctypes.byref(info)):
            raise ModelCacheError(code)
        if not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400:
            raise ModelCacheError(code)
        return _DirectoryIdentity(
            volume=int(info.dwVolumeSerialNumber),
            file_id=(int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        )

    def acl_bytes(acl: ctypes.c_void_p) -> bytes | None:
        if not acl:
            return None
        information = _AclSizeInformation()
        if not get_acl_information(
            acl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            2,
        ):
            raise ModelCacheError(code)
        return ctypes.string_at(acl, int(information.AclBytesInUse))

    try:
        if _is_reparse_or_link(root) or not root.is_dir():
            raise ModelCacheError(code)
        directories = tuple(
            sorted(
                (root, *(entry for entry in root.rglob("*") if entry.is_dir())),
                key=lambda entry: (len(entry.parts), entry.as_posix()),
            )
        )
        if not directories or len(directories) > 100_000:
            raise ModelCacheError(code)
        if any(_is_reparse_or_link(entry) for entry in directories):
            raise ModelCacheError(code)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error

    sid_size = wintypes.DWORD(68)
    everyone_sid = ctypes.create_string_buffer(int(sid_size.value))
    if not create_well_known_sid(
        1,
        None,
        everyone_sid,
        ctypes.byref(sid_size),
    ):
        raise ModelCacheError(code)
    states: list[dict[str, object]] = []
    applied: list[dict[str, object]] = []
    operation_error: ModelCacheError | None = None
    boundary_result: _WriteBoundaryResult | None = None
    try:
        for directory in directories:
            invalid_handle = wintypes.HANDLE(-1).value
            handle = invalid_handle
            security_descriptor = ctypes.c_void_p()
            dacl = ctypes.c_void_p()
            new_acl = ctypes.c_void_p()
            transferred = False
            try:
                handle = create_file(
                    str(directory),
                    0x00020000 | 0x00040000 | 0x00000080,
                    0x00000001 | 0x00000002,
                    None,
                    3,
                    0x02000000 | 0x00200000,
                    None,
                )
                if handle == invalid_handle:
                    raise ModelCacheError(code)
                result = get_security_info(
                    handle,
                    1,
                    0x00000004,
                    None,
                    None,
                    ctypes.byref(dacl),
                    None,
                    ctypes.byref(security_descriptor),
                )
                if result != 0:
                    raise ModelCacheError(code)
                control = wintypes.WORD()
                revision = wintypes.DWORD()
                if not get_control(
                    security_descriptor,
                    ctypes.byref(control),
                    ctypes.byref(revision),
                ) or not control.value & 0x0004:
                    raise ModelCacheError(code)
                explicit = _ExplicitAccess(
                    grfAccessPermissions=0x000D0156,
                    grfAccessMode=3,
                    grfInheritance=0,
                    Trustee=_Trustee(
                        pMultipleTrustee=None,
                        MultipleTrusteeOperation=0,
                        TrusteeForm=0,
                        TrusteeType=5,
                        ptstrName=ctypes.cast(
                            everyone_sid,
                            ctypes.c_void_p,
                        ).value,
                    ),
                )
                result = set_entries(
                    1,
                    ctypes.byref(explicit),
                    dacl,
                    ctypes.byref(new_acl),
                )
                if result != 0:
                    raise ModelCacheError(code)
                try:
                    state_identity = identity(handle)
                    state_acl_bytes = acl_bytes(dacl)
                except ModelCacheError:
                    raise
                except (OSError, ValueError) as error:
                    raise ModelCacheError(code) from error
                states.append(
                    {
                        "path": directory,
                        "handle": handle,
                        "identity": state_identity,
                        "security_descriptor": security_descriptor,
                        "dacl": dacl,
                        "acl_bytes": state_acl_bytes,
                        "control": int(control.value),
                        "new_acl": new_acl,
                    }
                )
                transferred = True
            finally:
                if not transferred:
                    cleanup_failed = False
                    if new_acl and local_free(new_acl):
                        cleanup_failed = True
                    if security_descriptor and local_free(security_descriptor):
                        cleanup_failed = True
                    if handle != invalid_handle and not close_handle(handle):
                        cleanup_failed = True
                    if cleanup_failed:
                        raise ModelCacheError(code)
        for index, state in enumerate(states):
            result = set_security_info(
                state["handle"],
                1,
                0x00000004,
                None,
                None,
                state["new_acl"],
                None,
            )
            if result != 0:
                raise ModelCacheError(code)
            applied.append(state)
            if _fault is not None:
                _fault("after-apply", len(applied) - 1)
        denied_probe_count = 0
        for state in states:
            probe = create_file(
                str(state["path"]),
                0x00000002,
                0x00000001 | 0x00000002,
                None,
                3,
                0x02000000 | 0x00200000,
                None,
            )
            if probe != wintypes.HANDLE(-1).value:
                close_handle(probe)
                raise ModelCacheError(code)
            if ctypes.get_last_error() != 5:
                raise ModelCacheError(code)
            denied_probe_count += 1
        boundary_result = _WriteBoundaryResult(
            root_identity=states[0]["identity"],
            applied_directory_count=len(applied),
            denied_probe_count=denied_probe_count,
        )
        yield boundary_result
    except ModelCacheError as error:
        operation_error = error
    except Exception:
        raise
    finally:
        restore_error: ModelCacheError | None = None
        for index, state in enumerate(states):
            try:
                restored = set_kernel_object_security(
                    state["handle"],
                    0x00000004,
                    state["security_descriptor"],
                )
                if not restored:
                    raise ModelCacheError(code)
                if _fault is not None:
                    _fault("after-restore", index)
            except ModelCacheError as error:
                if restore_error is None:
                    restore_error = error
        for state in states:
            try:
                if identity(state["handle"]) != state["identity"]:
                    raise ModelCacheError(code)
                current_descriptor = ctypes.c_void_p()
                current_dacl = ctypes.c_void_p()
                result = get_security_info(
                    state["handle"],
                    1,
                    0x00000004,
                    None,
                    None,
                    ctypes.byref(current_dacl),
                    None,
                    ctypes.byref(current_descriptor),
                )
                if result != 0:
                    raise ModelCacheError(code)
                try:
                    current_control = wintypes.WORD()
                    revision = wintypes.DWORD()
                    if (
                        not get_control(
                            current_descriptor,
                            ctypes.byref(current_control),
                            ctypes.byref(revision),
                        )
                        or acl_bytes(current_dacl) != state["acl_bytes"]
                        or (
                            int(current_control.value) & 0x1004
                            != int(state["control"]) & 0x1004
                        )
                    ):
                        raise ModelCacheError(code)
                finally:
                    if current_descriptor:
                        local_free(current_descriptor)
            except ModelCacheError as error:
                if restore_error is None:
                    restore_error = error
        if restore_error is None and boundary_result is not None:
            boundary_result.restored_directory_count = len(states)
            boundary_result.identities_verified = True
            boundary_result.acl_restored = True
            boundary_result.completed = True
        for state in reversed(states):
            if state["new_acl"]:
                local_free(state["new_acl"])
            if state["security_descriptor"]:
                local_free(state["security_descriptor"])
            close_handle(state["handle"])
        if restore_error is not None:
            raise restore_error
        if operation_error is not None:
            raise operation_error


def verify_loaded_model_cache(
    manifest: ModelManifest,
    cache_root: Path,
    *,
    approved_parent: Path | None = None,
) -> VerifiedModelCache:
    try:
        absolute_root = cache_root.absolute()
        absolute_approved = (
            absolute_root.parent
            if approved_parent is None
            else approved_parent.absolute()
        )
        try:
            absolute_root.relative_to(absolute_approved)
        except ValueError as error:
            raise ModelCacheError("MODEL_CACHE_PATH_INVALID") from error
        current = absolute_root
        reached_approved = False
        while True:
            if current.exists() and _is_reparse_or_link(current):
                raise ModelCacheError("MODEL_CACHE_PATH_INVALID")
            if current == absolute_approved:
                reached_approved = True
            parent = current.parent
            if parent == current:
                break
            current = parent
        if not reached_approved:
            raise ModelCacheError("MODEL_CACHE_PATH_INVALID")
        resolved_root = absolute_root.resolve(strict=True)
        resolved_approved = absolute_approved.resolve(strict=True)
        try:
            resolved_root.relative_to(resolved_approved)
        except ValueError as error:
            raise ModelCacheError("MODEL_CACHE_PATH_INVALID") from error
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_CACHE_MISSING") from error
    actual: set[str] = set()
    try:
        for entry in resolved_root.rglob("*"):
            if _is_reparse_or_link(entry):
                raise ModelCacheError("MODEL_CACHE_PATH_INVALID")
            if entry.is_file():
                actual.add(entry.relative_to(resolved_root).as_posix())
            elif not entry.is_dir():
                raise ModelCacheError("MODEL_CACHE_PATH_INVALID")
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_CACHE_READ_FAILED") from error
    expected = {member.path for member in manifest.files}
    if actual != expected:
        raise ModelCacheError("MODEL_CACHE_INVENTORY_MISMATCH")
    verified_members: list[dict[str, object]] = []
    for member in manifest.files:
        candidate = resolved_root.joinpath(*PurePosixPath(member.path).parts)
        try:
            resolved_candidate = candidate.resolve(strict=True)
            if resolved_root not in resolved_candidate.parents:
                raise ModelCacheError("MODEL_CACHE_PATH_INVALID")
            if _is_reparse_or_link(candidate) or not candidate.is_file():
                raise ModelCacheError("MODEL_CACHE_PATH_INVALID")
            size = candidate.stat().st_size
            digest = _sha256_file(candidate)
        except ModelCacheError:
            raise
        except OSError as error:
            raise ModelCacheError("MODEL_CACHE_READ_FAILED") from error
        if size != member.size or digest != member.sha256:
            raise ModelCacheError("MODEL_CACHE_MEMBER_MISMATCH")
        verified_members.append({"path": member.path, "size": size, "sha256": digest})
    cache_digest = hashlib.sha256(_canonical_bytes(verified_members)).hexdigest()
    return VerifiedModelCache(manifest, resolved_root, cache_digest)


def verify_model_cache(manifest_path: Path, cache_root: Path) -> VerifiedModelCache:
    return verify_loaded_model_cache(load_model_manifest(manifest_path), cache_root)


def validate_verified_generation(verified: VerifiedModelCache) -> None:
    """Reject mixed or redirected model/runtime paths before provider loading."""

    generation = verified.generation_root
    model = verified.specific_model_path
    runtime = verified.runtime_root
    digests = (
        verified.cache_digest,
        verified.runtime_digest,
        verified.wheel_set_digest,
        verified.generation_digest,
    )
    if (
        generation is None
        or runtime is None
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in digests)
    ):
        raise ModelCacheError("MODEL_GENERATION_INVALID")
    absolute_generation = generation.absolute()
    absolute_model = model.absolute()
    absolute_runtime = runtime.absolute()
    if (
        absolute_model.parent != absolute_generation
        or absolute_runtime.parent != absolute_generation
        or absolute_model.name != "model"
        or absolute_runtime.name != "runtime"
    ):
        raise ModelCacheError("MODEL_GENERATION_PATH_INVALID")
    try:
        current = absolute_generation
        while True:
            if not current.exists() or _is_reparse_or_link(current):
                raise ModelCacheError("MODEL_GENERATION_PATH_INVALID")
            parent = current.parent
            if parent == current:
                break
            current = parent
        for child in (absolute_model, absolute_runtime):
            if not child.is_dir() or _is_reparse_or_link(child):
                raise ModelCacheError("MODEL_GENERATION_PATH_INVALID")
            resolved_child = child.resolve(strict=True)
            resolved_generation = absolute_generation.resolve(strict=True)
            if resolved_child.parent != resolved_generation:
                raise ModelCacheError("MODEL_GENERATION_PATH_INVALID")
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_GENERATION_PATH_INVALID") from error


def git_blob_sha1(content: bytes) -> str:
    """Compute the Git object identity used to anchor Phase A small members."""

    framed = f"blob {len(content)}\0".encode("ascii") + content
    return hashlib.sha1(framed, usedforsecurity=False).hexdigest()


def _runtime_payload(runtime: RuntimeIdentity) -> dict[str, object]:
    return {
        "python": runtime.python,
        "os": runtime.os,
        "architecture": runtime.architecture,
        "wheels": [
            {
                "name": wheel.name,
                "version": wheel.version,
                "filename": wheel.filename,
                "size": wheel.size,
                "sha256": wheel.sha256,
                "artifactUrl": wheel.artifact_url,
            }
            for wheel in runtime.wheels
        ],
    }


def build_bootstrap_candidate(
    manifest: BootstrapModelManifest,
    *,
    member_bytes: Mapping[str, bytes],
    runtime: RuntimeIdentity,
    model_metadata_digest: str,
    wheel_metadata_digests: Mapping[str, str],
    dependency_graph_digest: str,
) -> dict[str, object]:
    """Create a candidate from framed Git identities; never include model bytes."""

    if not isinstance(manifest, BootstrapModelManifest):
        raise ModelCacheError("MODEL_BOOTSTRAP_MANIFEST_INVALID")
    expected_members = tuple(
        member
        for member in manifest.files
        if member.official_identity.kind == "git-blob-sha1"
    )
    if set(member_bytes) != {member.path for member in expected_members}:
        raise ModelCacheError("MODEL_BOOTSTRAP_MEMBER_MISMATCH")
    if not isinstance(model_metadata_digest, str) or _SHA256.fullmatch(model_metadata_digest) is None:
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    if (
        not isinstance(dependency_graph_digest, str)
        or _SHA256.fullmatch(dependency_graph_digest) is None
    ):
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    validated_runtime = _parse_runtime(_runtime_payload(runtime))
    normalized_metadata = {
        str(key): str(value) for key, value in wheel_metadata_digests.items()
    }
    if (
        set(normalized_metadata) != {wheel.name for wheel in validated_runtime.wheels}
        or any(_SHA256.fullmatch(value) is None for value in normalized_metadata.values())
    ):
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    calculated_sha256: dict[str, str] = {}
    for member in expected_members:
        content = member_bytes[member.path]
        if not isinstance(content, bytes):
            raise ModelCacheError("MODEL_BOOTSTRAP_MEMBER_MISMATCH")
        blob_digest = git_blob_sha1(content)
        if len(content) != member.size or blob_digest != member.official_identity.digest:
            raise ModelCacheError("MODEL_BOOTSTRAP_MEMBER_MISMATCH")
        calculated_sha256[member.path] = hashlib.sha256(content).hexdigest()
    files: list[dict[str, object]] = []
    for member in manifest.files:
        sha256 = calculated_sha256.get(member.path, member.sha256)
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise ModelCacheError("MODEL_BOOTSTRAP_MEMBER_MISMATCH")
        files.append(
            {
                "path": member.path,
                "size": member.size,
                "officialIdentity": {
                    "kind": member.official_identity.kind,
                    "digest": member.official_identity.digest,
                },
                "sha256": sha256,
                "artifactUrl": member.artifact_url,
            }
        )
    candidate: dict[str, object] = {
        "schemaVersion": 1,
        "producer": "course-helper/embedding-model-bootstrap@1",
        "phase": "bootstrap-candidate",
        "status": "candidate-only",
        "manifestDigest": manifest.aggregate_digest,
        "modelMetadataDigest": model_metadata_digest,
        "files": files,
        "runtime": _runtime_payload(validated_runtime),
        "wheelMetadataDigests": {
            key: normalized_metadata[key] for key in sorted(normalized_metadata)
        },
        "dependencyGraphDigest": dependency_graph_digest,
    }
    candidate["candidateDigest"] = hashlib.sha256(_canonical_bytes(candidate)).hexdigest()
    return candidate


def verify_bootstrap_model_metadata(
    manifest: BootstrapModelManifest,
    metadata_bytes: bytes,
) -> str:
    """Verify the immutable tree identities before any member download."""

    if not isinstance(metadata_bytes, bytes) or len(metadata_bytes) > 1_000_000:
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    try:
        payload = json.loads(
            metadata_bytes,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except ModelCacheError as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID") from error
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID") from error
    if (
        not isinstance(payload, dict)
        or len(payload) > 128
        or "sha" not in payload
        or "siblings" not in payload
    ):
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    siblings = payload["siblings"]
    if (
        payload["sha"] != manifest.model.artifact_revision
        or not isinstance(siblings, list)
        or len(siblings) < len(manifest.files)
        or len(siblings) > 256
    ):
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    by_path: dict[str, dict[str, Any]] = {}
    for raw in siblings:
        if (
            not isinstance(raw, dict)
            or len(raw) > 32
            or not isinstance(raw.get("rfilename"), str)
            or raw["rfilename"] in by_path
        ):
            raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
        by_path[raw["rfilename"]] = raw
    expected_paths = {member.path for member in manifest.files}
    if not expected_paths.issubset(by_path):
        raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    for member in manifest.files:
        item = by_path[member.path]
        if item.get("size") != member.size or type(item.get("size")) is not int:
            raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
        if member.official_identity.kind == "git-blob-sha1":
            if (
                item.get("blobId") != member.official_identity.digest
                or item.get("lfs") is not None
            ):
                raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
        else:
            lfs = item.get("lfs")
            if (
                not isinstance(lfs, dict)
                or len(lfs) > 16
                or lfs["sha256"] != member.official_identity.digest
                or lfs["size"] != member.size
                or type(lfs["size"]) is not int
            ):
                raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
            blob_id = item.get("blobId")
            if blob_id is not None and (
                not isinstance(blob_id, str) or _SHA1.fullmatch(blob_id) is None
            ):
                raise ModelCacheError("MODEL_BOOTSTRAP_METADATA_INVALID")
    canonical_identity = {
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
    return hashlib.sha256(_canonical_bytes(canonical_identity)).hexdigest()


def _pypi_project_url(name: str) -> str:
    return f"https://pypi.org/pypi/{canonicalize_name(name)}/json"


def _pypi_version_url(name: str, version: str) -> str:
    return f"https://pypi.org/pypi/{canonicalize_name(name)}/{version}/json"


def _read_pypi_metadata(
    url: str,
    fetch_metadata: Callable[[str], bytes],
) -> tuple[dict[str, Any], str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "pypi.org"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/pypi/")
        or not parsed.path.endswith("/json")
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    try:
        raw = fetch_metadata(url)
    except ModelCacheError:
        raise
    except Exception as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED") from error
    if not isinstance(raw, bytes) or len(raw) > 8_000_000:
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (ModelCacheError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED") from error
    if not isinstance(payload, dict):
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
    return payload, hashlib.sha256(raw).hexdigest()


def _canonical_python_constraint(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
    try:
        constraint = SpecifierSet(value)
    except InvalidSpecifier as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED") from error
    if not constraint.contains(Version("3.12.0"), prereleases=False):
        raise ModelCacheError("MODEL_RUNTIME_PYTHON_INCOMPATIBLE")
    return str(constraint)


def _compatible_target_wheel(raw: object) -> RuntimeWheel | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("packagetype") != "bdist_wheel" or raw.get("yanked") is not False:
        return None
    artifact_python = raw.get("requires_python")
    try:
        canonical_artifact_python = _canonical_python_constraint(artifact_python)
    except ModelCacheError:
        return None
    filename = raw.get("filename")
    url = raw.get("url")
    size = raw.get("size")
    digests = raw.get("digests")
    if (
        not isinstance(filename, str)
        or not isinstance(url, str)
        or type(size) is not int
        or size <= 0
        or size > _MAX_RUNTIME_WHEEL_BYTES
        or not isinstance(digests, dict)
        or not isinstance(digests.get("sha256"), str)
        or _SHA256.fullmatch(digests["sha256"]) is None
    ):
        return None
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/packages/")
        or PurePosixPath(unquote(parsed.path)).name != filename
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    try:
        wheel_name, wheel_version, _build, _tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return None
    if not _wheel_filename_targets_runtime(filename):
        return None
    return RuntimeWheel(
        name=str(wheel_name),
        version=str(wheel_version),
        filename=filename,
        size=size,
        sha256=digests["sha256"],
        artifact_url=url,
        requires_python=canonical_artifact_python,
    )


def resolve_runtime_wheels_from_pypi(
    root_package: PackageIdentity,
    *,
    fetch_metadata: Callable[[str], bytes],
) -> tuple[RuntimeIdentity, dict[str, str], str]:
    """Resolve a bounded Windows/Python 3.12 binary lock from fixed PyPI JSON."""

    if (
        root_package.name != "fastembed"
        or root_package.version != "0.8.0"
        or root_package.wheel_filename != PINNED_FASTEMBED_WHEEL
        or root_package.wheel_size != PINNED_FASTEMBED_WHEEL_SIZE
        or root_package.wheel_sha256 != PINNED_FASTEMBED_WHEEL_SHA256
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
    project_cache: dict[str, dict[str, Any]] = {}
    version_cache: dict[tuple[str, str], dict[str, Any]] = {}
    target_environment = dict(_TARGET_MARKER_ENVIRONMENT)
    resolution_steps = 0
    metadata_requests = 0
    metadata_bytes = 0
    resolution_started = time.monotonic()

    def bounded_fetch_metadata(url: str) -> bytes:
        nonlocal metadata_requests, metadata_bytes
        if (
            metadata_requests >= _MAX_RUNTIME_METADATA_REQUESTS
            or time.monotonic() - resolution_started > _MAX_RUNTIME_RESOLUTION_SECONDS
        ):
            raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_LIMIT")
        metadata_requests += 1
        raw = fetch_metadata(url)
        if not isinstance(raw, bytes):
            raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
        metadata_bytes += len(raw)
        if (
            metadata_bytes > _MAX_RUNTIME_METADATA_BYTES
            or time.monotonic() - resolution_started > _MAX_RUNTIME_RESOLUTION_SECONDS
        ):
            raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_LIMIT")
        return raw

    def consume_step() -> None:
        nonlocal resolution_steps
        resolution_steps += 1
        if resolution_steps > _MAX_RUNTIME_RESOLUTION_STEPS:
            raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_LIMIT")

    def project(name: str) -> dict[str, Any]:
        normalized = canonicalize_name(name)
        if normalized not in project_cache:
            payload, _digest = _read_pypi_metadata(
                _pypi_project_url(normalized), bounded_fetch_metadata
            )
            info = payload.get("info")
            releases = payload.get("releases")
            if (
                not isinstance(info, dict)
                or canonicalize_name(str(info.get("name", ""))) != normalized
                or not isinstance(releases, dict)
                or len(releases) > 10_000
            ):
                raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
            project_cache[normalized] = payload
        return project_cache[normalized]

    def version_metadata(name: str, version: Version) -> dict[str, Any]:
        normalized = canonicalize_name(name)
        key = (normalized, str(version))
        if key not in version_cache:
            payload, _raw_digest = _read_pypi_metadata(
                _pypi_version_url(normalized, str(version)), bounded_fetch_metadata
            )
            info = payload.get("info")
            if (
                not isinstance(info, dict)
                or canonicalize_name(str(info.get("name", ""))) != normalized
                or str(info.get("version", "")) != str(version)
                or not isinstance(payload.get("urls"), list)
            ):
                raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
            version_cache[key] = payload
        return version_cache[key]

    def candidates(name: str, constraints: tuple[SpecifierSet, ...]) -> list[tuple[Version, RuntimeWheel]]:
        releases = project(name)["releases"]
        values: list[tuple[Version, RuntimeWheel]] = []
        for raw_version, raw_files in releases.items():
            consume_step()
            try:
                parsed_version = Version(str(raw_version))
            except InvalidVersion:
                continue
            if parsed_version.is_prerelease or any(
                not specifier.contains(parsed_version, prereleases=False)
                for specifier in constraints
            ):
                continue
            if not isinstance(raw_files, list):
                continue
            wheels = [
                wheel
                for raw_file in raw_files
                if (wheel := _compatible_target_wheel(raw_file)) is not None
            ]
            if not wheels:
                continue
            wheels.sort(
                key=lambda wheel: (
                    0 if "win_amd64" in wheel.filename.casefold() else 1,
                    wheel.filename.casefold(),
                )
            )
            selected = wheels[0]
            if canonicalize_name(selected.name) != canonicalize_name(name) or Version(selected.version) != parsed_version:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_LOCK_INVALID")
            values.append((parsed_version, selected))
        values.sort(key=lambda item: item[0], reverse=True)
        return values[:200]

    initial_constraints = {
        "fastembed": (SpecifierSet("==0.8.0"),),
    }

    def parse_active_requirements(
        raw_requirements: object,
    ) -> tuple[tuple[dict[str, object], ...], tuple[tuple[str, SpecifierSet], ...]] | None:
        if raw_requirements is None:
            requirements: list[object] = []
        elif isinstance(raw_requirements, list):
            requirements = raw_requirements
        else:
            return None
        if len(requirements) > 256:
            return None
        canonical: list[dict[str, object]] = []
        dependencies: list[tuple[str, SpecifierSet]] = []
        for raw_requirement in requirements:
            if not isinstance(raw_requirement, str) or len(raw_requirement) > 2048:
                return None
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement:
                return None
            if requirement.marker is not None and not requirement.marker.evaluate(
                target_environment
            ):
                continue
            if requirement.url is not None or requirement.extras:
                return None
            dependency = canonicalize_name(requirement.name)
            canonical.append(
                {
                    "name": dependency,
                    "specifier": str(requirement.specifier),
                    "marker": (
                        str(requirement.marker)
                        if requirement.marker is not None
                        else None
                    ),
                }
            )
            dependencies.append((dependency, requirement.specifier))
        canonical.sort(key=_canonical_bytes)
        dependencies.sort(key=lambda item: (item[0], str(item[1])))
        return tuple(canonical), tuple(dependencies)

    ResolvedValue = tuple[
        Version,
        RuntimeWheel,
        str,
        tuple[dict[str, object], ...],
    ]

    def solve(
        selected: dict[str, ResolvedValue],
        constraints: dict[str, tuple[SpecifierSet, ...]],
    ) -> dict[str, ResolvedValue] | None:
        consume_step()
        if len(constraints) > 128:
            raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
        for name, (version, _wheel, _digest, _requirements) in selected.items():
            if any(
                not specifier.contains(version, prereleases=False)
                for specifier in constraints.get(name, ())
            ):
                return None
        unresolved = sorted(set(constraints) - set(selected))
        if not unresolved:
            return selected
        name = unresolved[0]
        for candidate_version, project_wheel in candidates(name, constraints[name]):
            consume_step()
            payload = version_metadata(name, candidate_version)
            info = payload["info"]
            try:
                package_requires_python = _canonical_python_constraint(
                    info.get("requires_python")
                )
            except ModelCacheError:
                continue
            matching = [
                wheel
                for raw_file in payload["urls"]
                if (wheel := _compatible_target_wheel(raw_file)) is not None
                and wheel.filename == project_wheel.filename
                and wheel.sha256 == project_wheel.sha256
                and wheel.size == project_wheel.size
                and wheel.artifact_url == project_wheel.artifact_url
                and wheel.requires_python == project_wheel.requires_python
            ]
            if len(matching) != 1:
                continue
            parsed_requirements = parse_active_requirements(info.get("requires_dist"))
            if parsed_requirements is None:
                continue
            canonical_requirements, dependencies = parsed_requirements
            new_constraints = dict(constraints)
            for dependency, specifier in dependencies:
                existing = new_constraints.get(dependency, ())
                new_constraints[dependency] = (*existing, specifier)
            metadata_identity = {
                "canonicalName": canonicalize_name(name),
                "version": str(candidate_version),
                "requiresPython": package_requires_python,
                "selectedWheel": {
                    "filename": matching[0].filename,
                    "size": matching[0].size,
                    "sha256": matching[0].sha256,
                    "artifactUrl": matching[0].artifact_url,
                    "requiresPython": matching[0].requires_python,
                },
                "activeRequiresDist": list(canonical_requirements),
            }
            metadata_digest = hashlib.sha256(
                _canonical_bytes(metadata_identity)
            ).hexdigest()
            next_selected = dict(selected)
            next_selected[name] = (
                candidate_version,
                matching[0],
                metadata_digest,
                canonical_requirements,
            )
            resolved = solve(next_selected, new_constraints)
            if resolved is not None:
                return resolved
        return None

    resolved = solve({}, initial_constraints)
    if resolved is None:
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED")
    wheels = tuple(resolved[name][1] for name in sorted(resolved))
    metadata_digests = {name: resolved[name][2] for name in sorted(resolved)}
    runtime = RuntimeIdentity("3.12", "windows", "x86_64", wheels)
    _parse_runtime(_runtime_payload(runtime))
    dependency_graph = {
        "root": {"name": "fastembed", "version": "0.8.0"},
        "target": target_environment,
        "packages": [
            {
                "name": name,
                "version": str(resolved[name][0]),
                "metadataDigest": resolved[name][2],
                "activeRequiresDist": list(resolved[name][3]),
            }
            for name in sorted(resolved)
        ],
    }
    dependency_graph_digest = hashlib.sha256(
        _canonical_bytes(dependency_graph)
    ).hexdigest()
    return runtime, metadata_digests, dependency_graph_digest


def _validate_bootstrap_output_paths(
    *,
    approved_root: Path,
    candidate_path: Path,
    quarantine_root: Path,
) -> None:
    approved = approved_root.absolute()
    if not approved.is_dir():
        raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID")
    for target, is_file in ((candidate_path.absolute(), True), (quarantine_root.absolute(), False)):
        try:
            target.relative_to(approved)
        except ValueError as error:
            raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID") from error
        current = target.parent if is_file else target
        reached_approved = False
        while True:
            if current.exists() and _is_reparse_or_link(current):
                raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID")
            if current == approved:
                reached_approved = True
            parent = current.parent
            if parent == current:
                break
            current = parent
        if not reached_approved:
            raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID")


def validate_bootstrap_candidate(
    value: object,
    manifest: BootstrapModelManifest,
) -> dict[str, object]:
    candidate = _exact_mapping(
        value,
        {
            "schemaVersion",
            "producer",
            "phase",
            "status",
            "manifestDigest",
            "modelMetadataDigest",
            "files",
            "runtime",
            "wheelMetadataDigests",
            "dependencyGraphDigest",
            "candidateDigest",
        },
        "MODEL_BOOTSTRAP_CANDIDATE_INVALID",
    )
    digest = candidate["candidateDigest"]
    unsigned = dict(candidate)
    unsigned.pop("candidateDigest")
    if (
        type(candidate["schemaVersion"]) is not int
        or candidate["schemaVersion"] != 1
        or candidate["producer"] != "course-helper/embedding-model-bootstrap@1"
        or candidate["phase"] != "bootstrap-candidate"
        or candidate["status"] != "candidate-only"
        or candidate["manifestDigest"] != manifest.aggregate_digest
        or not isinstance(candidate["modelMetadataDigest"], str)
        or _SHA256.fullmatch(candidate["modelMetadataDigest"]) is None
        or not isinstance(candidate["dependencyGraphDigest"], str)
        or _SHA256.fullmatch(candidate["dependencyGraphDigest"]) is None
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != digest
    ):
        raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID")
    expected = manifest.files
    raw_files = candidate["files"]
    if not isinstance(raw_files, list) or len(raw_files) != len(expected):
        raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID")
    for member, raw in zip(expected, raw_files):
        item = _exact_mapping(
            raw,
            {"path", "size", "officialIdentity", "sha256", "artifactUrl"},
            "MODEL_BOOTSTRAP_CANDIDATE_INVALID",
        )
        identity = _exact_mapping(
            item["officialIdentity"],
            {"kind", "digest"},
            "MODEL_BOOTSTRAP_CANDIDATE_INVALID",
        )
        if (
            item["path"] != member.path
            or item["size"] != member.size
            or identity["kind"] != member.official_identity.kind
            or identity["digest"] != member.official_identity.digest
            or not isinstance(item["sha256"], str)
            or _SHA256.fullmatch(item["sha256"]) is None
            or (
                member.official_identity.kind == "lfs-sha256"
                and item["sha256"] != member.official_identity.digest
            )
            or item["artifactUrl"] != member.artifact_url
        ):
            raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID")
    try:
        runtime = _parse_runtime(candidate["runtime"])
    except ModelCacheError as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID") from error
    metadata = candidate["wheelMetadataDigests"]
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {wheel.name for wheel in runtime.wheels}
        or any(not isinstance(item, str) or _SHA256.fullmatch(item) is None for item in metadata.values())
    ):
        raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID")
    return dict(candidate)


def _write_bootstrap_candidate_atomic_locked(
    path: Path,
    candidate: object,
    manifest: BootstrapModelManifest,
    *,
    approved_root: Path | None = None,
) -> None:
    validated = validate_bootstrap_candidate(candidate, manifest)
    payload = _canonical_bytes(validated)
    if approved_root is not None:
        _validate_bootstrap_output_paths(
            approved_root=approved_root,
            candidate_path=path,
            quarantine_root=path.parent,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if approved_root is not None:
        _validate_bootstrap_output_paths(
            approved_root=approved_root,
            candidate_path=path,
            quarantine_root=path.parent,
        )
    prior_bytes: bytes | None = None
    if path.exists():
        try:
            if _is_reparse_or_link(path) or not path.is_file():
                raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID")
            prior_bytes = path.read_bytes()
            if len(prior_bytes) > 2_000_000:
                raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID")
        except ModelCacheError:
            raise
        except OSError as error:
            raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID") from error
    temporary_name: str | None = None
    replaced = False
    sealed = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        try:
            temporary_value = json.loads(
                temporary_path.read_bytes(),
                object_pairs_hook=_reject_duplicate_pairs,
            )
            validate_bootstrap_candidate(temporary_value, manifest)
            if temporary_path.read_bytes() != payload:
                raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID")
        except ModelCacheError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID") from error
        if approved_root is not None:
            _validate_bootstrap_output_paths(
                approved_root=approved_root,
                candidate_path=path,
                quarantine_root=path.parent,
            )
        os.replace(temporary_name, path)
        temporary_name = None
        replaced = True
        try:
            sealed_bytes = path.read_bytes()
            sealed_value = json.loads(
                sealed_bytes,
                object_pairs_hook=_reject_duplicate_pairs,
            )
            validate_bootstrap_candidate(sealed_value, manifest)
            if sealed_bytes != payload:
                raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID")
            sealed = True
        except ModelCacheError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ModelCacheError("MODEL_BOOTSTRAP_CANDIDATE_INVALID") from error
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
        if replaced and not sealed:
            try:
                if prior_bytes is None:
                    path.unlink(missing_ok=True)
                else:
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=path.parent,
                        prefix=f".{path.name}.rollback.",
                        suffix=".tmp",
                        delete=False,
                    ) as rollback:
                        rollback.write(prior_bytes)
                        rollback.flush()
                        os.fsync(rollback.fileno())
                        rollback_name = rollback.name
                    os.replace(rollback_name, path)
            except OSError:
                pass


def write_bootstrap_candidate_atomic(
    path: Path,
    candidate: object,
    manifest: BootstrapModelManifest,
    *,
    approved_root: Path | None = None,
) -> None:
    if approved_root is None:
        _write_bootstrap_candidate_atomic_locked(path, candidate, manifest)
        return
    _validate_bootstrap_output_paths(
        approved_root=approved_root,
        candidate_path=path,
        quarantine_root=path.parent,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_PATH_INVALID") from error
    _validate_bootstrap_output_paths(
        approved_root=approved_root,
        candidate_path=path,
        quarantine_root=path.parent,
    )
    with _hold_contained_directory_handles(approved_root, path.parent):
        _write_bootstrap_candidate_atomic_locked(
            path,
            candidate,
            manifest,
            approved_root=approved_root,
        )


def run_bootstrap_phase(
    manifest: BootstrapModelManifest,
    *,
    candidate_path: Path,
    quarantine_root: Path,
    approved_root: Path,
    fetch_model_metadata: Callable[[str], bytes],
    fetch_member: Callable[[str, int], bytes],
    resolve_runtime: Callable[
        [PackageIdentity], tuple[RuntimeIdentity, Mapping[str, str], str]
    ],
) -> dict[str, object]:
    """Run Phase A only: four small blobs and wheel metadata, never ONNX/runtime."""

    if not isinstance(manifest, BootstrapModelManifest):
        raise ModelCacheError("MODEL_BOOTSTRAP_MANIFEST_INVALID")
    _validate_bootstrap_output_paths(
        approved_root=approved_root,
        candidate_path=candidate_path,
        quarantine_root=quarantine_root,
    )
    try:
        metadata_bytes = fetch_model_metadata(PINNED_MODEL_METADATA_URL)
    except ModelCacheError:
        raise
    except Exception as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_ACQUISITION_FAILED") from error
    model_metadata_digest = verify_bootstrap_model_metadata(
        manifest,
        metadata_bytes,
    )
    payloads: dict[str, bytes] = {}
    for member in manifest.files:
        if member.official_identity.kind != "git-blob-sha1":
            continue
        try:
            content = fetch_member(member.artifact_url, member.size)
        except ModelCacheError:
            raise
        except Exception as error:
            raise ModelCacheError("MODEL_BOOTSTRAP_ACQUISITION_FAILED") from error
        if (
            not isinstance(content, bytes)
            or len(content) != member.size
            or git_blob_sha1(content) != member.official_identity.digest
        ):
            raise ModelCacheError("MODEL_BOOTSTRAP_MEMBER_MISMATCH")
        payloads[member.path] = content
    try:
        runtime, wheel_metadata, dependency_graph_digest = resolve_runtime(
            manifest.package
        )
    except ModelCacheError:
        raise
    except Exception as error:
        raise ModelCacheError("MODEL_BOOTSTRAP_RESOLUTION_FAILED") from error
    candidate = build_bootstrap_candidate(
        manifest,
        member_bytes=payloads,
        runtime=runtime,
        model_metadata_digest=model_metadata_digest,
        wheel_metadata_digests=wheel_metadata,
        dependency_graph_digest=dependency_graph_digest,
    )
    _validate_bootstrap_output_paths(
        approved_root=approved_root,
        candidate_path=candidate_path,
        quarantine_root=quarantine_root,
    )
    write_bootstrap_candidate_atomic(
        candidate_path,
        candidate,
        manifest,
        approved_root=approved_root,
    )
    return candidate


def _directory_inventory(root: Path, code: str) -> tuple[tuple[dict[str, object], ...], str]:
    if not root.is_dir() or _is_reparse_or_link(root):
        raise ModelCacheError(code)
    entries: list[dict[str, object]] = []
    try:
        for candidate in sorted(root.rglob("*"), key=lambda path: path.as_posix()):
            if _is_reparse_or_link(candidate):
                raise ModelCacheError(code)
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise ModelCacheError(code)
            relative = candidate.relative_to(root).as_posix()
            entries.append(
                {
                    "path": relative,
                    "size": candidate.stat().st_size,
                    "sha256": _sha256_file(candidate),
                }
            )
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error
    if not entries:
        raise ModelCacheError(code)
    frozen = tuple(entries)
    return frozen, hashlib.sha256(_canonical_bytes(list(frozen))).hexdigest()


_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _safe_wheel_member_parts(name: str) -> tuple[str, ...]:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 4096
        or name.startswith(("/", "\\"))
        or "\\" in name
        or "\x00" in name
        or ":" in name
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    normalized = name[:-1] if name.endswith("/") else name
    parts = PurePosixPath(normalized).parts
    if not parts or any(
        part in {"", ".", ".."}
        or len(part) > 255
        or part.endswith((" ", "."))
        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        for part in parts
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    return parts


def _validate_wheel_archive(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, ...]:
    infos = tuple(archive.infolist())
    if not infos or len(infos) > _MAX_WHEEL_ARCHIVE_MEMBERS:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    seen: set[str] = set()
    files: set[str] = set()
    total_uncompressed = 0
    for info in infos:
        parts = _safe_wheel_member_parts(info.filename)
        key = "/".join(part.casefold() for part in parts)
        if key in seen or any(
            "/".join(part.casefold() for part in parts[:index]) in files
            for index in range(1, len(parts))
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        if not info.is_dir() and any(existing.startswith(f"{key}/") for existing in seen):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        seen.add(key)
        if not info.is_dir():
            files.add(key)
        if info.flag_bits & 0x1:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        if (
            info.file_size < 0
            or info.compress_size < 0
            or info.file_size > _MAX_WHEEL_MEMBER_UNCOMPRESSED_BYTES
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_WHEEL_UNCOMPRESSED_BYTES:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size > info.compress_size * _MAX_WHEEL_COMPRESSION_RATIO
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        if info.create_system == 3:
            mode = (info.external_attr >> 16) & 0xFFFF
            kind = stat.S_IFMT(mode)
            allowed = {0, stat.S_IFREG, stat.S_IFDIR}
            if kind not in allowed or (info.is_dir() and kind == stat.S_IFREG) or (
                not info.is_dir() and kind == stat.S_IFDIR
            ):
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    return infos


def _parse_record_rows(raw: bytes) -> dict[str, tuple[str | None, int | None]]:
    if len(raw) > 8_000_000:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""))
        rows: dict[str, tuple[str | None, int | None]] = {}
        for index, row in enumerate(reader):
            if index >= _MAX_WHEEL_ARCHIVE_MEMBERS or len(row) != 3:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            name, raw_digest, raw_size = row
            _safe_wheel_member_parts(name)
            if name in rows:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            if not raw_digest and not raw_size:
                rows[name] = (None, None)
                continue
            algorithm, separator, encoded = raw_digest.partition("=")
            if algorithm != "sha256" or separator != "=" or not encoded:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            try:
                decoded = base64.b64decode(
                    encoded + "=" * (-len(encoded) % 4),
                    altchars=b"-_",
                    validate=True,
                )
                size = int(raw_size)
            except (ValueError, TypeError) as error:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
            if len(decoded) != 32 or size < 0:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            rows[name] = (decoded.hex(), size)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
    if not rows:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    return rows


def _wheel_member_install_path(name: str) -> str:
    parts = _safe_wheel_member_parts(name)
    if len(parts) >= 3 and parts[0].endswith(".data"):
        scheme = parts[1].casefold()
        remainder = parts[2:]
        if scheme in {"purelib", "platlib", "data"}:
            parts = remainder
        elif scheme == "scripts":
            parts = ("Scripts", *remainder)
        elif scheme == "headers":
            parts = ("include", *remainder)
        else:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    return "/".join(parts)


def _wheel_install_contract(path: Path) -> _WheelInstallContract:
    try:
        if _is_reparse_or_link(path) or not path.is_file():
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        filename_name, filename_version, _build, _tags = parse_wheel_filename(path.name)
        with zipfile.ZipFile(path) as archive:
            infos = _validate_wheel_archive(archive)
            metadata_members = [
                info
                for info in infos
                if info.filename.endswith(".dist-info/METADATA")
            ]
            wheel_members = [
                info for info in infos if info.filename.endswith(".dist-info/WHEEL")
            ]
            record_members = [
                info for info in infos if info.filename.endswith(".dist-info/RECORD")
            ]
            if (
                len(metadata_members) != 1
                or len(wheel_members) != 1
                or len(record_members) != 1
                or metadata_members[0].file_size > 1_000_000
                or record_members[0].file_size > 8_000_000
            ):
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            dist_info = PurePosixPath(metadata_members[0].filename).parent.as_posix()
            expected_dist_info = (
                f"{canonicalize_name(str(filename_name)).replace('-', '_')}-"
                f"{str(filename_version).replace('-', '_')}.dist-info"
            )
            if (
                dist_info.casefold() != expected_dist_info.casefold()
                or PurePosixPath(wheel_members[0].filename).parent.as_posix()
                != dist_info
                or PurePosixPath(record_members[0].filename).parent.as_posix()
                != dist_info
            ):
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            raw = archive.read(metadata_members[0])
            record_raw = archive.read(record_members[0])
            record_rows = _parse_record_rows(record_raw)
            expected_files: dict[str, tuple[str | None, int | None]] = {}
            entry_points_raw: bytes | None = None
            archive_files = {info.filename: info for info in infos if not info.is_dir()}
            for info in archive_files.values():
                with archive.open(info) as stream:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                if size != info.file_size:
                    raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
                if info.filename == record_members[0].filename:
                    if record_rows.get(info.filename) != (None, None):
                        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
                    expected = (None, None)
                else:
                    expected = record_rows.get(info.filename)
                    if expected != (digest.hexdigest(), size) and not info.filename.endswith(
                        (".dist-info/RECORD.jws", ".dist-info/RECORD.p7s")
                    ):
                        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
                    expected = (digest.hexdigest(), size)
                installed = _wheel_member_install_path(info.filename)
                folded = installed.casefold()
                if any(key.casefold() == folded for key in expected_files):
                    raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
                expected_files[installed] = expected
                if info.filename == f"{dist_info}/entry_points.txt":
                    entry_points_raw = archive.read(info)
            unlisted = set(record_rows) - set(archive_files)
            if unlisted:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    except ModelCacheError:
        raise
    except (
        OSError,
        KeyError,
        zipfile.BadZipFile,
        RuntimeError,
        InvalidWheelFilename,
    ) as error:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
    message = BytesParser(policy=email_policy.compat32).parsebytes(raw)
    metadata_versions = message.get_all("Metadata-Version", [])
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    python_constraints = message.get_all("Requires-Python", [])
    if (
        message.defects
        or len(metadata_versions) != 1
        or metadata_versions[0] not in {"2.1", "2.2", "2.3", "2.4"}
        or len(names) != 1
        or len(versions) != 1
        or len(python_constraints) > 1
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    name = names[0]
    version = versions[0]
    requires_python = python_constraints[0] if python_constraints else None
    requirements = tuple(message.get_all("Requires-Dist", []))
    if not isinstance(name, str) or not isinstance(version, str):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    try:
        parsed_version = Version(version)
    except InvalidVersion as error:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
    if (
        canonicalize_name(name) != canonicalize_name(str(filename_name))
        or parsed_version != filename_version
    ):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    if requires_python is not None:
        try:
            if not SpecifierSet(requires_python).contains(
                Version("3.12.0"), prereleases=False
            ):
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        except InvalidSpecifier as error:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
    generated_scripts: set[str] = set()
    if entry_points_raw is not None:
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.optionxform = str
            parser.read_string(entry_points_raw.decode("utf-8"))
            if parser.has_section("console_scripts"):
                for script_name in parser["console_scripts"]:
                    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", script_name) is None:
                        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
                    generated_scripts.update(
                        {
                            f"Scripts/{script_name}.exe",
                            f"Scripts/{script_name}-script.py",
                            f"Scripts/{script_name}.exe.manifest",
                            f"bin/{script_name}",
                            f"bin/{script_name}.exe",
                        }
                    )
        except (UnicodeDecodeError, configparser.Error) as error:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
    return _WheelInstallContract(
        name=name,
        version=version,
        requires_python=requires_python,
        requirements=requirements,
        dist_info=dist_info,
        record_path=f"{dist_info}/RECORD",
        expected_files=tuple(
            (installed, digest, size)
            for installed, (digest, size) in sorted(expected_files.items())
        ),
        generated_scripts=tuple(sorted(generated_scripts)),
        member_count=len(infos),
        uncompressed_size=sum(info.file_size for info in infos),
    )


def _read_locked_wheel_metadata(path: Path) -> tuple[str, str, str | None, tuple[str, ...]]:
    contract = _wheel_install_contract(path)
    return (
        contract.name,
        contract.version,
        contract.requires_python,
        contract.requirements,
    )


def validate_offline_wheel_closure(
    runtime: RuntimeIdentity,
    wheelhouse: Path,
) -> tuple[str, ...]:
    if not isinstance(runtime, RuntimeIdentity) or not runtime.wheels:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    expected_files = {wheel.filename for wheel in runtime.wheels}
    try:
        actual_files = {
            path.name
            for path in wheelhouse.iterdir()
            if path.is_file() and not _is_reparse_or_link(path)
        }
        if any(path.is_dir() or _is_reparse_or_link(path) for path in wheelhouse.iterdir()):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
    if actual_files != expected_files:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    by_name: dict[str, tuple[Version, tuple[str, ...]]] = {}
    total_size = 0
    total_members = 0
    total_uncompressed = 0
    for wheel in runtime.wheels:
        path = wheelhouse / wheel.filename
        try:
            filename_name, filename_version, _build, tags = parse_wheel_filename(
                wheel.filename
            )
            declared_version = Version(wheel.version)
            actual_size = path.stat().st_size
            actual_digest = _sha256_file(path)
        except (InvalidWheelFilename, InvalidVersion, OSError) as error:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
        if (
            tags.isdisjoint(_TARGET_WHEEL_TAGS)
            or canonicalize_name(str(filename_name)) != canonicalize_name(wheel.name)
            or filename_version != declared_version
            or wheel.size <= 0
            or wheel.size > _MAX_RUNTIME_WHEEL_BYTES
            or actual_size != wheel.size
            or actual_digest != wheel.sha256
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        total_size += wheel.size
        if total_size > _MAX_RUNTIME_WHEEL_SET_BYTES:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        contract = _wheel_install_contract(path)
        total_members += contract.member_count
        total_uncompressed += contract.uncompressed_size
        if (
            total_members > _MAX_WHEEL_SET_MEMBERS
            or total_uncompressed > _MAX_WHEEL_SET_UNCOMPRESSED_BYTES
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        metadata_name = contract.name
        metadata_version = contract.version
        raw_requirements = contract.requirements
        normalized = canonicalize_name(wheel.name)
        try:
            parsed_metadata_version = Version(metadata_version)
        except InvalidVersion as error:
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
        if (
            canonicalize_name(metadata_name) != normalized
            or parsed_metadata_version != declared_version
            or normalized in by_name
        ):
            raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
        by_name[normalized] = (declared_version, raw_requirements)
    if "fastembed" not in by_name or "onnxruntime" not in by_name:
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    graph: dict[str, set[str]] = {name: set() for name in by_name}
    for name, (_version, raw_requirements) in by_name.items():
        for raw_requirement in raw_requirements:
            if not isinstance(raw_requirement, str) or len(raw_requirement) > 2048:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as error:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID") from error
            if requirement.marker is not None and not requirement.marker.evaluate(
                dict(_TARGET_MARKER_ENVIRONMENT)
            ):
                continue
            if requirement.url is not None or requirement.extras:
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            dependency = canonicalize_name(requirement.name)
            locked = by_name.get(dependency)
            if locked is None or not requirement.specifier.contains(
                locked[0], prereleases=False
            ):
                raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
            graph[name].add(dependency)
    reachable = {"fastembed"}
    pending = ["fastembed"]
    while pending:
        current = pending.pop()
        for dependency in sorted(graph[current]):
            if dependency not in reachable:
                reachable.add(dependency)
                pending.append(dependency)
    if reachable != set(by_name):
        raise ModelCacheError("MODEL_RUNTIME_WHEEL_CLOSURE_INVALID")
    return tuple(sorted(reachable))


def _requirements_lock_bytes(runtime: RuntimeIdentity) -> bytes:
    return (
        "\n".join(
            f"{canonicalize_name(wheel.name)}=={wheel.version} --hash=sha256:{wheel.sha256}"
            for wheel in runtime.wheels
        )
        + "\n"
    ).encode("utf-8")


def _write_requirements_lock(runtime: RuntimeIdentity, path: Path) -> str:
    payload = _requirements_lock_bytes(runtime)
    try:
        if path.exists() and (_is_reparse_or_link(path) or not path.is_file()):
            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
        path.write_bytes(payload)
        if path.read_bytes() != payload:
            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED") from error
    return hashlib.sha256(payload).hexdigest()


def _validate_installed_runtime_closure(
    runtime: RuntimeIdentity,
    runtime_root: Path,
    wheelhouse: Path,
) -> None:
    inventory, _digest = _directory_inventory(
        runtime_root,
        "MODEL_RUNTIME_INSTALL_FAILED",
    )
    actual_files = {
        str(entry["path"]): (str(entry["sha256"]), int(entry["size"]))
        for entry in inventory
    }
    expected = {
        canonicalize_name(wheel.name): Version(wheel.version)
        for wheel in runtime.wheels
    }
    installed_versions: dict[str, Version] = {}
    contracts = {
        canonicalize_name(wheel.name): _wheel_install_contract(
            wheelhouse / wheel.filename
        )
        for wheel in runtime.wheels
    }
    expected_base: dict[str, tuple[str | None, int | None]] = {}
    for contract in contracts.values():
        for relative, digest, size in contract.expected_files:
            folded = relative.casefold()
            if any(existing.casefold() == folded for existing in expected_base):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            expected_base[relative] = (digest, size)
            if digest is not None and actual_files.get(relative) != (digest, size):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            if digest is None and relative not in actual_files:
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
    recorded_ownership: set[str] = set()
    try:
        distributions = tuple(importlib_metadata.distributions(path=[str(runtime_root)]))
        if len(distributions) != len(expected):
            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
        for distribution in distributions:
            raw_name = distribution.metadata.get("Name")
            raw_version = distribution.version
            if not isinstance(raw_name, str) or not isinstance(raw_version, str):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            name = canonicalize_name(raw_name)
            version = Version(raw_version)
            if name in installed_versions:
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            root_resolved = runtime_root.resolve(strict=True)
            origin = Path(distribution.locate_file(".")).resolve(strict=True)
            origin.relative_to(root_resolved)
            contract = contracts.get(name)
            if contract is None or version != Version(contract.version):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            record = runtime_root.joinpath(
                *PurePosixPath(contract.record_path).parts
            )
            raw_record = record.read_bytes()
            if len(raw_record) > 8_000_000:
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            try:
                rows = csv.reader(
                    io.StringIO(raw_record.decode("utf-8"), newline="")
                )
                owned: set[str] = set()
                for index, row in enumerate(rows):
                    if index >= _MAX_WHEEL_ARCHIVE_MEMBERS or len(row) != 3:
                        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                    raw_path, raw_hash, raw_size = row
                    if (
                        not raw_path
                        or len(raw_path) > 4096
                        or raw_path.startswith(("/", "\\"))
                        or "\\" in raw_path
                        or "\x00" in raw_path
                        or ":" in raw_path
                    ):
                        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                    raw_parts = raw_path.split("/")
                    if raw_parts[:3] == ["..", "..", "bin"]:
                        if (
                            runtime.os != "windows"
                            or len(raw_parts) != 4
                            or re.fullmatch(
                                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.exe",
                                raw_parts[3],
                            )
                            is None
                        ):
                            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                        normalized_script = f"bin/{raw_parts[3]}"
                        if normalized_script.casefold() not in {
                            script.casefold() for script in contract.generated_scripts
                        }:
                            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                        normalized_path = normalized_script
                    else:
                        if any(part in {"", ".", ".."} for part in raw_parts):
                            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                        normalized_path = raw_path
                    candidate = runtime_root.joinpath(
                        *PurePosixPath(normalized_path).parts
                    ).resolve(strict=True)
                    relative = candidate.relative_to(root_resolved).as_posix()
                    if relative in owned or relative in recorded_ownership:
                        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                    owned.add(relative)
                    recorded_ownership.add(relative)
                    actual_entry = actual_files.get(relative)
                    if actual_entry is None:
                        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                    if relative == contract.record_path:
                        if raw_hash or raw_size:
                            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                    else:
                        algorithm, separator, encoded = raw_hash.partition("=")
                        if algorithm != "sha256" or separator != "=" or not encoded:
                            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
                        decoded = base64.b64decode(
                            encoded + "=" * (-len(encoded) % 4),
                            altchars=b"-_",
                            validate=True,
                        ).hex()
                        if len(decoded) != 64 or actual_entry != (decoded, int(raw_size)):
                            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            except (
                UnicodeDecodeError,
                csv.Error,
                ValueError,
                TypeError,
                OSError,
            ) as error:
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED") from error
            generated = {
                f"{contract.dist_info}/INSTALLER",
                f"{contract.dist_info}/REQUESTED",
                f"{contract.dist_info}/direct_url.json",
                *contract.generated_scripts,
            }
            expected_owned = {
                relative for relative, _digest, _size in contract.expected_files
            }
            expected_owned.update(path for path in generated if path in actual_files)
            if owned != expected_owned:
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            installer = f"{contract.dist_info}/INSTALLER"
            if installer in actual_files and (
                runtime_root.joinpath(*PurePosixPath(installer).parts).read_bytes()
                != b"pip\n"
            ):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            requested = f"{contract.dist_info}/REQUESTED"
            if requested in actual_files and (
                runtime_root.joinpath(*PurePosixPath(requested).parts).read_bytes()
                != b""
            ):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
            installed_versions[name] = version
    except ModelCacheError:
        raise
    except (InvalidVersion, OSError, ValueError) as error:
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED") from error
    if installed_versions != expected:
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
    if recorded_ownership != set(actual_files):
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")


def _run_bounded_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    active_process_limit: int = 16,
) -> None:
    """Run a suspended Windows process in a kill-on-close bounded Job."""

    if (
        os.name != "nt"
        or not command
        or _MAX_RUNTIME_INSTALL_OUTPUT_BYTES < 0
        or _RUNTIME_INSTALL_TIMEOUT_SECONDS <= 0
        or not 1 <= active_process_limit <= 64
    ):
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")

    import ctypes
    from ctypes import wintypes

    class _JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JobObjectExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JobObjectBasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_extended_limit_information = 9
    job_object_limit_active_process = 0x00000008
    job_object_limit_kill_on_job_close = 0x00002000
    create_suspended = 0x00000004
    create_no_window = 0x08000000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    set_job_information = kernel32.SetInformationJobObject
    set_job_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    set_job_information.restype = wintypes.BOOL
    assign_to_job = kernel32.AssignProcessToJobObject
    assign_to_job.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign_to_job.restype = wintypes.BOOL
    terminate_job = kernel32.TerminateJobObject
    terminate_job.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate_job.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    resume_process = ntdll.NtResumeProcess
    resume_process.argtypes = [wintypes.HANDLE]
    resume_process.restype = wintypes.LONG

    job_handle = create_job(None, None)
    if not job_handle:
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")

    limits = _JobObjectExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = (
        job_object_limit_active_process | job_object_limit_kill_on_job_close
    )
    limits.BasicLimitInformation.ActiveProcessLimit = active_process_limit
    process: subprocess.Popen[bytes] | None = None
    streams: list[Any] = []
    readers: list[threading.Thread] = []
    assigned = False
    job_open = True
    execution_errors: list[BaseException] = []
    reader_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []
    output_limit_reached = threading.Event()
    output_lock = threading.Lock()
    job_lock = threading.Lock()
    total_output = 0
    max_output = int(_MAX_RUNTIME_INSTALL_OUTPUT_BYTES)
    read_size = max(1, min(64 * 1024, max_output + 1))

    def terminate_bound_job() -> None:
        with job_lock:
            if job_open and not terminate_job(job_handle, 1):
                cleanup_errors.append(ctypes.WinError(ctypes.get_last_error()))

    def close_bound_job() -> None:
        nonlocal job_open
        with job_lock:
            if not job_open:
                return
            job_open = False
            if not close_handle(job_handle):
                cleanup_errors.append(ctypes.WinError(ctypes.get_last_error()))

    def drain(stream: Any) -> None:
        nonlocal total_output
        try:
            while True:
                block = stream.read(read_size)
                if not block:
                    return
                with output_lock:
                    total_output += len(block)
                    over_limit = total_output > max_output
                if over_limit:
                    output_limit_reached.set()
                    terminate_bound_job()
                    return
        except OSError as error:
            reader_errors.append(error)
            terminate_bound_job()

    try:
        if not set_job_information(
            job_handle,
            job_object_extended_limit_information,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
            creationflags=create_suspended | create_no_window,
        )
        if process.stdout is None or process.stderr is None:
            raise OSError("missing subprocess output pipes")
        streams.extend((process.stdout, process.stderr))
        raw_process_handle = getattr(process, "_handle", None)
        if raw_process_handle is None:
            raise OSError("missing suspended process handle")
        process_handle = wintypes.HANDLE(int(raw_process_handle))
        if not assign_to_job(job_handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())
        assigned = True
        for index, stream in enumerate(streams):
            reader = threading.Thread(
                target=drain,
                args=(stream,),
                name=f"bounded-subprocess-reader-{index}",
            )
            reader.start()
            readers.append(reader)
        if resume_process(process_handle) != 0:
            raise OSError("unable to resume assigned process")
        try:
            return_code = process.wait(timeout=_RUNTIME_INSTALL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            execution_errors.append(error)
            terminate_bound_job()
        else:
            if return_code != 0:
                execution_errors.append(subprocess.SubprocessError("nonzero exit"))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        execution_errors.append(error)
    finally:
        if process is not None and process.returncode is None:
            if assigned:
                terminate_bound_job()
            else:
                try:
                    process.kill()
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(error)
        close_bound_job()
        if process is not None and process.returncode is None:
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(error)
        for reader in readers:
            reader.join(timeout=5)
        for stream in streams:
            try:
                stream.close()
            except OSError as error:
                cleanup_errors.append(error)
        for reader in readers:
            if reader.is_alive():
                reader.join(timeout=1)
            if reader.is_alive():
                cleanup_errors.append(RuntimeError("subprocess reader did not stop"))
        if process is not None:
            raw_process_handle = getattr(process, "_handle", None)
            close_process_handle = getattr(raw_process_handle, "Close", None)
            if callable(close_process_handle):
                try:
                    close_process_handle()
                except OSError as error:
                    cleanup_errors.append(error)

    if (
        execution_errors
        or reader_errors
        or cleanup_errors
        or output_limit_reached.is_set()
    ):
        causes = execution_errors or reader_errors or cleanup_errors
        cause = causes[0] if causes else None
        raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED") from cause


def install_locked_runtime(
    runtime: RuntimeIdentity,
    wheelhouse: Path,
    runtime_root: Path,
) -> None:
    validate_offline_wheel_closure(runtime, wheelhouse)
    requirements = wheelhouse.parent / "requirements.lock"
    _write_requirements_lock(runtime, requirements)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PIP_", "HTTP_", "HTTPS_", "ALL_PROXY", "NO_PROXY"))
    }
    environment.update({"PIP_NO_INDEX": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    command = [
        sys.executable,
        "-I",
        "-m",
        "pip",
        "install",
        "--isolated",
        "--disable-pip-version-check",
        "--no-input",
        "--no-index",
        "--no-deps",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-compile",
        "--find-links",
        str(wheelhouse),
        "--target",
        str(runtime_root),
        "--requirement",
        str(requirements),
    ]
    _run_bounded_subprocess(
        command,
        cwd=wheelhouse.parent,
        env=environment,
        active_process_limit=1,
    )


def _validate_phase_b_roots(
    approved_root: Path,
    generation_parent: Path,
    quarantine_root: Path,
) -> None:
    approved = approved_root.absolute()
    generation = generation_parent.absolute()
    quarantine = quarantine_root.absolute()
    if generation == quarantine or generation in quarantine.parents or quarantine in generation.parents:
        raise ModelCacheError("MODEL_FINAL_PATH_INVALID")
    for target in (generation, quarantine):
        try:
            target.relative_to(approved)
        except ValueError as error:
            raise ModelCacheError("MODEL_FINAL_PATH_INVALID") from error
        current = target
        reached = False
        while True:
            if current.exists() and _is_reparse_or_link(current):
                raise ModelCacheError("MODEL_FINAL_PATH_INVALID")
            if current == approved:
                reached = True
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        if not reached:
            raise ModelCacheError("MODEL_FINAL_PATH_INVALID")


def _validate_phase_b_host(runtime: RuntimeIdentity) -> None:
    if (
        sys.implementation.name != "cpython"
        or sys.implementation.cache_tag != "cpython-312"
        or sys.version_info[:2] != (3, 12)
        or os.name != "nt"
        or platform.system() != "Windows"
        or platform.machine().casefold() not in {"amd64", "x86_64"}
        or struct.calcsize("P") * 8 != 64
        or sysconfig.get_platform().replace("_", "-").casefold() != "win-amd64"
        or ".cp312-win_amd64.pyd" not in EXTENSION_SUFFIXES
        or runtime.python != "3.12"
        or runtime.os != "windows"
        or runtime.architecture != "x86_64"
    ):
        raise ModelCacheError("MODEL_FINAL_HOST_MISMATCH")


def _validate_contained_tree(
    approved_root: Path,
    parent: Path,
    target: Path,
    code: str,
) -> bool:
    approved = approved_root.absolute()
    parent_absolute = parent.absolute()
    target_absolute = target.absolute()
    try:
        parent_absolute.relative_to(approved)
    except ValueError as error:
        raise ModelCacheError(code) from error
    if target_absolute.parent != parent_absolute:
        raise ModelCacheError(code)
    if not target_absolute.exists():
        return False
    try:
        if _is_reparse_or_link(target_absolute) or not target_absolute.is_dir():
            raise ModelCacheError(code)
        for entry in target_absolute.rglob("*"):
            if _is_reparse_or_link(entry) or not (entry.is_dir() or entry.is_file()):
                raise ModelCacheError(code)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error
    return True


def _directory_identity(
    path: Path,
    *,
    code: str = "MODEL_FINAL_PATH_INVALID",
) -> _DirectoryIdentity:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = create_file(
            str(path),
            0x80000000,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ModelCacheError(code)
        try:
            info = _ByHandleFileInformation()
            if not get_info(handle, ctypes.byref(info)):
                raise ModelCacheError(code)
            if not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400:
                raise ModelCacheError(code)
            return _DirectoryIdentity(
                volume=int(info.dwVolumeSerialNumber),
                file_id=(int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
            )
        finally:
            close_handle(handle)
    try:
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ModelCacheError(code)
        return _DirectoryIdentity(volume=info.st_dev, file_id=info.st_ino)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error


def _remove_contained_tree(
    approved_root: Path,
    parent: Path,
    target: Path,
    *,
    code: str,
    expected_identity: _DirectoryIdentity,
) -> None:
    if not _validate_contained_tree(approved_root, parent, target, code):
        return
    try:
        with _hold_contained_directory_handles(
            approved_root,
            parent,
            code=code,
        ):
            if not _validate_contained_tree(approved_root, parent, target, code):
                return
            if _directory_identity(target, code=code) != expected_identity:
                raise ModelCacheError(code)
            trash = parent / f".rollback-{secrets.token_hex(16)}"
            if trash.exists():
                raise ModelCacheError(code)
            os.replace(target, trash)
            if _directory_identity(trash, code=code) != expected_identity:
                try:
                    if not target.exists():
                        os.replace(trash, target)
                except OSError:
                    pass
                raise ModelCacheError(code)
            _validate_contained_tree(approved_root, parent, trash, code)
            shutil.rmtree(trash)
            if trash.exists():
                raise ModelCacheError(code)
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError(code) from error


@contextmanager
def _socket_denied_verification():
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def denied(*_args: object, **_kwargs: object) -> object:
        raise ModelCacheError("MODEL_FINAL_NETWORK_FORBIDDEN")

    socket.socket = denied  # type: ignore[assignment]
    socket.create_connection = denied  # type: ignore[assignment]
    socket.getaddrinfo = denied  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]


def _wheel_set_digest(runtime: RuntimeIdentity) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            [
                {
                    "name": wheel.name,
                    "version": wheel.version,
                    "filename": wheel.filename,
                    "size": wheel.size,
                    "sha256": wheel.sha256,
                }
                for wheel in runtime.wheels
            ]
        )
    ).hexdigest()


def _validate_final_verification_evidence(
    verified: VerifiedModelCache,
    value: object,
) -> dict[str, object]:
    if not isinstance(value, dict) or not {
        "generationDigest",
        "childEvidenceDigest",
        "childLoadedOrigins",
    }.issubset(value):
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    if "providerOrigins" in value:
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    if (
        value["generationDigest"] != verified.generation_digest
        or not isinstance(value["childEvidenceDigest"], str)
        or _SHA256.fullmatch(value["childEvidenceDigest"]) is None
        or not isinstance(value["childLoadedOrigins"], list)
    ):
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    generation_root = verified.generation_root
    runtime_root = verified.runtime_root
    if generation_root is None or runtime_root is None:
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    parent_origins: list[dict[str, str]] = []
    for distribution in ("fastembed", "onnxruntime"):
        try:
            specification = PathFinder.find_spec(distribution, [str(runtime_root)])
            if specification is None or not isinstance(specification.origin, str):
                raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
            path = Path(specification.origin)
            if _is_reparse_or_link(path) or not path.is_file():
                raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
            resolved = path.resolve(strict=True)
            resolved.relative_to(runtime_root.resolve(strict=True))
            relative = resolved.relative_to(generation_root.resolve(strict=True)).as_posix()
        except ModelCacheError:
            raise
        except (OSError, ValueError, ImportError) as error:
            raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED") from error
        parent_origins.append(
            {
                "distribution": distribution,
                "path": relative,
                "sha256": _sha256_file(resolved),
            }
        )
    child_origins = value["childLoadedOrigins"]
    if not 2 <= len(child_origins) <= 256:
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    canonical_child: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for raw in child_origins:
        if not isinstance(raw, dict) or set(raw) != {
            "distribution",
            "path",
            "sha256",
        }:
            raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
        distribution = raw["distribution"]
        relative = raw["path"]
        digest = raw["sha256"]
        if (
            not isinstance(distribution, str)
            or not isinstance(relative, str)
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or relative in seen_paths
        ):
            raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
        parts = _safe_wheel_member_parts(relative)
        if parts[0] != "runtime":
            raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
        path = generation_root.joinpath(*parts)
        try:
            if _is_reparse_or_link(path) or not path.is_file():
                raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
            path.resolve(strict=True).relative_to(runtime_root.resolve(strict=True))
        except ModelCacheError:
            raise
        except (OSError, ValueError) as error:
            raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED") from error
        normalized = canonicalize_name(distribution)
        if normalized not in {"fastembed", "onnxruntime"} or _sha256_file(path) != digest:
            raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
        seen_paths.add(relative)
        canonical_child.append(
            {
                "distribution": normalized,
                "path": relative,
                "sha256": digest,
            }
        )
    if sorted(canonical_child, key=_canonical_bytes) != sorted(
        parent_origins,
        key=_canonical_bytes,
    ):
        raise ModelCacheError("MODEL_FINAL_VERIFICATION_FAILED")
    validated = dict(value)
    validated["providerOrigins"] = parent_origins
    return validated


def _verify_promoted_generation(
    manifest: ModelManifest,
    generation_root: Path,
) -> VerifiedModelCache:
    try:
        if _is_reparse_or_link(generation_root) or not generation_root.is_dir():
            raise ModelCacheError("MODEL_GENERATION_INVALID")
        if {entry.name for entry in generation_root.iterdir()} != {
            "generation-manifest.json",
            "model",
            "requirements.lock",
            "runtime",
            "wheelhouse",
        }:
            raise ModelCacheError("MODEL_GENERATION_INVALID")
        generation_payload = _decode_manifest_bytes(
            (generation_root / "generation-manifest.json").read_bytes()
        )
    except ModelCacheError as error:
        raise ModelCacheError("MODEL_GENERATION_INVALID") from error
    except OSError as error:
        raise ModelCacheError("MODEL_GENERATION_INVALID") from error
    expected_keys = {
        "schemaVersion",
        "manifestDigest",
        "cacheDigest",
        "requirementsLockDigest",
        "runtimeDigest",
        "wheelSetDigest",
        "generationDigest",
    }
    if not isinstance(generation_payload, dict) or set(generation_payload) != expected_keys:
        raise ModelCacheError("MODEL_GENERATION_INVALID")
    verified_model = verify_loaded_model_cache(
        manifest,
        generation_root / "model",
        approved_parent=generation_root,
    )
    validate_offline_wheel_closure(manifest.runtime, generation_root / "wheelhouse")
    expected_lock = _requirements_lock_bytes(manifest.runtime)
    try:
        lock_path = generation_root / "requirements.lock"
        if _is_reparse_or_link(lock_path) or lock_path.read_bytes() != expected_lock:
            raise ModelCacheError("MODEL_GENERATION_INVALID")
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_GENERATION_INVALID") from error
    _validate_installed_runtime_closure(
        manifest.runtime,
        generation_root / "runtime",
        generation_root / "wheelhouse",
    )
    _runtime_entries, runtime_digest = _directory_inventory(
        generation_root / "runtime",
        "MODEL_GENERATION_INVALID",
    )
    wheel_digest = _wheel_set_digest(manifest.runtime)
    core = {
        "manifestDigest": manifest.aggregate_digest,
        "cacheDigest": verified_model.cache_digest,
        "requirementsLockDigest": hashlib.sha256(expected_lock).hexdigest(),
        "runtimeDigest": runtime_digest,
        "wheelSetDigest": wheel_digest,
    }
    generation_digest = hashlib.sha256(_canonical_bytes(core)).hexdigest()
    if generation_payload != {
        "schemaVersion": 1,
        **core,
        "generationDigest": generation_digest,
    } or generation_root.name != generation_digest:
        raise ModelCacheError("MODEL_GENERATION_INVALID")
    verified = VerifiedModelCache(
        manifest=manifest,
        specific_model_path=(generation_root / "model").resolve(strict=True),
        cache_digest=verified_model.cache_digest,
        generation_root=generation_root.resolve(strict=True),
        runtime_root=(generation_root / "runtime").resolve(strict=True),
        runtime_digest=runtime_digest,
        wheel_set_digest=wheel_digest,
        generation_digest=generation_digest,
    )
    validate_verified_generation(verified)
    return verified


def _install_runtime_from_locked_wheels(
    runtime: RuntimeIdentity,
    wheelhouse: Path,
    runtime_root: Path,
    staging: Path,
    installer: Callable[[RuntimeIdentity, Path, Path], None],
) -> str:
    with _hold_regular_file_handles(
        wheelhouse,
        code="MODEL_RUNTIME_INSTALL_FAILED",
    ):
        validate_offline_wheel_closure(runtime, wheelhouse)
        requirements_lock_digest = _write_requirements_lock(
            runtime,
            staging / "requirements.lock",
        )
        try:
            installer(runtime, wheelhouse, runtime_root)
        except ModelCacheError:
            raise
        except Exception as error:
            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED") from error
        validate_offline_wheel_closure(runtime, wheelhouse)
        try:
            if (
                (staging / "requirements.lock").read_bytes()
                != _requirements_lock_bytes(runtime)
            ):
                raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED")
        except ModelCacheError:
            raise
        except OSError as error:
            raise ModelCacheError("MODEL_RUNTIME_INSTALL_FAILED") from error
        _validate_installed_runtime_closure(runtime, runtime_root, wheelhouse)
        return requirements_lock_digest


def run_final_phase(
    manifest: ModelManifest,
    *,
    generation_parent: Path,
    quarantine_root: Path,
    approved_root: Path,
    fetch_artifact: Callable[[str, int], bytes],
    install_runtime: Callable[[RuntimeIdentity, Path, Path], None],
    verify_generation: Callable[[VerifiedModelCache], Mapping[str, object]],
) -> FinalPhaseResult:
    if not isinstance(manifest, ModelManifest) or manifest.runtime is None:
        raise ModelCacheError("MODEL_FINAL_MANIFEST_INVALID")
    _validate_phase_b_host(manifest.runtime)
    _validate_phase_b_roots(
        approved_root,
        generation_parent,
        quarantine_root,
    )
    try:
        generation_parent.mkdir(parents=True, exist_ok=True)
        quarantine_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ModelCacheError("MODEL_FINAL_PATH_INVALID") from error
    _validate_phase_b_roots(
        approved_root,
        generation_parent,
        quarantine_root,
    )
    phase_root: Path | None = None
    phase_identity: _DirectoryIdentity | None = None
    staging: Path | None = None
    staging_identity: _DirectoryIdentity | None = None
    target: Path | None = None
    target_identity: _DirectoryIdentity | None = None
    promoted_new = False
    completed = False
    try:
        with _hold_contained_directory_handles(
            approved_root,
            generation_parent,
            code="MODEL_FINAL_PATH_INVALID",
        ):
            with _hold_contained_directory_handles(
                approved_root,
                quarantine_root,
                code="MODEL_FINAL_PATH_INVALID",
            ):
                try:
                    phase_root = Path(
                        tempfile.mkdtemp(prefix="phase-b-", dir=quarantine_root)
                    )
                    phase_identity = _directory_identity(phase_root)
                except OSError as error:
                    raise ModelCacheError("MODEL_FINAL_PATH_INVALID") from error
                with _hold_contained_directory_handles(
                    approved_root,
                    phase_root,
                    code="MODEL_FINAL_PATH_INVALID",
                ):
                    staging = phase_root / "generation"
                    model_root = staging / "model"
                    wheelhouse = staging / "wheelhouse"
                    runtime_root = staging / "runtime"
                    try:
                        model_root.mkdir(parents=True)
                        wheelhouse.mkdir()
                        runtime_root.mkdir()
                        staging_identity = _directory_identity(staging)
                    except OSError as error:
                        raise ModelCacheError("MODEL_FINAL_PATH_INVALID") from error
                    with _hold_contained_directory_handles(
                        approved_root,
                        staging,
                        code="MODEL_FINAL_PATH_INVALID",
                    ):
                        with _hold_contained_directory_handles(
                            approved_root,
                            model_root,
                            code="MODEL_FINAL_PATH_INVALID",
                        ):
                            with _hold_contained_directory_handles(
                                approved_root,
                                wheelhouse,
                                code="MODEL_FINAL_PATH_INVALID",
                            ):
                                with _hold_contained_directory_handles(
                                    approved_root,
                                    runtime_root,
                                    code="MODEL_FINAL_PATH_INVALID",
                                ):
                                    for member in manifest.files:
                                        if not isinstance(member.artifact_url, str):
                                            raise ModelCacheError(
                                                "MODEL_FINAL_MANIFEST_INVALID"
                                            )
                                        try:
                                            payload = fetch_artifact(
                                                member.artifact_url,
                                                member.size,
                                            )
                                        except ModelCacheError:
                                            raise
                                        except Exception as error:
                                            raise ModelCacheError(
                                                "MODEL_FINAL_ACQUISITION_FAILED"
                                            ) from error
                                        if (
                                            not isinstance(payload, bytes)
                                            or len(payload) != member.size
                                            or hashlib.sha256(payload).hexdigest()
                                            != member.sha256
                                        ):
                                            raise ModelCacheError(
                                                "MODEL_FINAL_ARTIFACT_MISMATCH"
                                            )
                                        destination = model_root.joinpath(
                                            *PurePosixPath(member.path).parts
                                        )
                                        destination.parent.mkdir(
                                            parents=True,
                                            exist_ok=True,
                                        )
                                        destination.write_bytes(payload)
                                    for wheel in manifest.runtime.wheels:
                                        try:
                                            payload = fetch_artifact(
                                                wheel.artifact_url,
                                                wheel.size,
                                            )
                                        except ModelCacheError:
                                            raise
                                        except Exception as error:
                                            raise ModelCacheError(
                                                "MODEL_FINAL_ACQUISITION_FAILED"
                                            ) from error
                                        if (
                                            not isinstance(payload, bytes)
                                            or len(payload) != wheel.size
                                            or hashlib.sha256(payload).hexdigest()
                                            != wheel.sha256
                                        ):
                                            raise ModelCacheError(
                                                "MODEL_FINAL_ARTIFACT_MISMATCH"
                                            )
                                        (wheelhouse / wheel.filename).write_bytes(
                                            payload
                                        )
                                    requirements_lock_digest = (
                                        _install_runtime_from_locked_wheels(
                                        manifest.runtime,
                                        wheelhouse,
                                        runtime_root,
                                        staging,
                                        install_runtime,
                                        )
                                    )
                                    verified_model = verify_loaded_model_cache(
                                        manifest,
                                        model_root,
                                        approved_parent=staging,
                                    )
                                    _runtime_entries, runtime_digest = (
                                        _directory_inventory(
                                            runtime_root,
                                            "MODEL_RUNTIME_INSTALL_FAILED",
                                        )
                                    )
                                    wheel_digest = _wheel_set_digest(
                                        manifest.runtime
                                    )
                                    core = {
                                        "manifestDigest": manifest.aggregate_digest,
                                        "cacheDigest": verified_model.cache_digest,
                                        "requirementsLockDigest": requirements_lock_digest,
                                        "runtimeDigest": runtime_digest,
                                        "wheelSetDigest": wheel_digest,
                                    }
                                    generation_digest = hashlib.sha256(
                                        _canonical_bytes(core)
                                    ).hexdigest()
                                    generation_payload = {
                                        "schemaVersion": 1,
                                        **core,
                                        "generationDigest": generation_digest,
                                    }
                                    (
                                        staging / "generation-manifest.json"
                                    ).write_bytes(_canonical_bytes(generation_payload))
                    target = generation_parent / generation_digest
                    reused = target.exists()
                    if not reused:
                        try:
                            os.replace(staging, target)
                            promoted_new = True
                            promoted_identity = _directory_identity(target)
                            if promoted_identity != staging_identity:
                                raise ModelCacheError("MODEL_FINAL_PROMOTION_FAILED")
                            target_identity = promoted_identity
                        except OSError as error:
                            if not target.exists():
                                raise ModelCacheError(
                                    "MODEL_FINAL_PROMOTION_FAILED"
                                ) from error
                            reused = True
                    if _is_reparse_or_link(target) or not target.is_dir():
                        raise ModelCacheError("MODEL_GENERATION_INVALID")
                    with _hold_contained_directory_handles(
                        approved_root,
                        target,
                        code="MODEL_FINAL_PATH_INVALID",
                    ):
                        with _hold_regular_file_handles(
                            target,
                            code="MODEL_FINAL_VERIFICATION_FAILED",
                        ):
                            verified = _verify_promoted_generation(manifest, target)
                            with _deny_generation_tree_writes(
                                target,
                                code="MODEL_FINAL_VERIFICATION_FAILED",
                            ) as write_boundary:
                                with _socket_denied_verification():
                                    try:
                                        raw_verification = verify_generation(verified)
                                    except ModelCacheError:
                                        raise
                                    except Exception as error:
                                        raise ModelCacheError(
                                            "MODEL_FINAL_VERIFICATION_FAILED"
                                        ) from error
                            post_verified = _verify_promoted_generation(
                                manifest,
                                target,
                            )
                            if post_verified != verified:
                                raise ModelCacheError(
                                    "MODEL_FINAL_VERIFICATION_FAILED"
                                )
                            verification = _validate_final_verification_evidence(
                                post_verified,
                                raw_verification,
                            )
                            verified = post_verified
                    if reused:
                        if staging_identity is None:
                            raise ModelCacheError("MODEL_FINAL_PATH_INVALID")
                        _remove_contained_tree(
                            approved_root,
                            phase_root,
                            staging,
                            code="MODEL_FINAL_PATH_INVALID",
                            expected_identity=staging_identity,
                        )
                completed = True
                return FinalPhaseResult(
                    verified=verified,
                    quarantine_root=phase_root,
                    verification=dict(verification),
                    promoted_new=promoted_new,
                    write_boundary=write_boundary,
                )
    except ModelCacheError:
        raise
    except OSError as error:
        raise ModelCacheError("MODEL_FINAL_STAGING_FAILED") from error
    finally:
        if not completed:
            cleanup_error: ModelCacheError | None = None
            if (
                promoted_new
                and target is not None
                and target_identity is not None
            ):
                try:
                    _remove_contained_tree(
                        approved_root,
                        generation_parent,
                        target,
                        code="MODEL_FINAL_ROLLBACK_FAILED",
                        expected_identity=target_identity,
                    )
                except ModelCacheError as error:
                    cleanup_error = error
            if phase_root is not None and phase_identity is not None:
                try:
                    _remove_contained_tree(
                        approved_root,
                        quarantine_root,
                        phase_root,
                        code="MODEL_FINAL_ROLLBACK_FAILED",
                        expected_identity=phase_identity,
                    )
                except ModelCacheError as error:
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None:
                raise cleanup_error


__all__ = [
    "BootstrapModelManifest",
    "BootstrapModelMember",
    "ModelCacheError",
    "ModelIdentity",
    "ModelManifest",
    "ModelMember",
    "OfficialIdentity",
    "PackageIdentity",
    "RuntimeIdentity",
    "RuntimeWheel",
    "VerifiedModelCache",
    "git_blob_sha1",
    "build_bootstrap_candidate",
    "load_bootstrap_manifest",
    "load_model_manifest",
    "verify_bootstrap_model_metadata",
    "run_bootstrap_phase",
    "resolve_runtime_wheels_from_pypi",
    "validate_bootstrap_candidate",
    "verify_loaded_model_cache",
    "verify_model_cache",
    "validate_verified_generation",
    "write_bootstrap_candidate_atomic",
]
