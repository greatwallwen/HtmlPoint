"""Exact authority binding for the opt-in live embedding verification flow."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

from course_helper.cards import VOCABULARY_VERSION_ID, publish_card, seed_vocabulary
from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    TagAssignment,
)
from course_helper.domain.sources import ChunkLocator, ExtractedChunk, SourceAssetVersion
from course_helper.embeddings import EmbeddingProviderIdentity, validate_embedding_vector
from course_helper.index_outbox import claim_next_index_outbox, complete_index_claim
from course_helper.operations import (
    IndexOutboxItem,
    OperationMutationResult,
    OperationRequest,
    run_operation,
)
from course_helper.retrieval import KnowledgeRetriever, RetrievalQuery


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_PREFIX = "_course_helper_embedding_live_"
_SOURCE_ROOT = Path(__file__).resolve().parent
_MODEL_CACHE_PATH = (_SOURCE_ROOT / "model_cache.py").resolve()
_EMBEDDINGS_PATH = (_SOURCE_ROOT / "embeddings.py").resolve()
_MAX_AUTHORITY_SOURCE_BYTES = 8 * 1024 * 1024
_PIPELINE_QUERY = "RFM"
_PIPELINE_SCHEMA_ID = "embedding-live-synthetic-v1"
_PIPELINE_ACTOR = ActorRef(actor_type="service", actor_id="index-tests")
_MAX_RECEIPT_BYTES = 2_000_000
_RECEIPT_ERROR = "EMBEDDING_MODEL_RECEIPT_INVALID"
_RECEIPT_KEYS = {
    "schemaVersion",
    "producer",
    "status",
    "policyId",
    "manifestDigest",
    "model",
    "provider",
    "modelFiles",
    "runtime",
    "cacheDigest",
    "fixtureFingerprint",
    "indexSnapshot",
    "retrieval",
    "osNetworkIsolation",
    "zeroNetworkReplayDigest",
    "zeroWriteProof",
    "checks",
    "startedAt",
    "finishedAt",
    "receiptDigest",
}
_RECEIPT_CHECKS = (
    "model-members-verified",
    "runtime-wheel-closure",
    "specific-model-path",
    "cpython-socket-denied-inference",
    "index-snapshot-consistent",
    "hybrid-retrieval",
    "cpython-socket-denied-replay",
    "generation-tree-write-barrier",
)


class EmbeddingLiveError(RuntimeError):
    """A live verification authority or evidence binding failed closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail() -> None:
    raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID")


def _receipt_fail() -> None:
    raise EmbeddingLiveError(_RECEIPT_ERROR)


def _is_reparse_or_link(path: Path) -> bool:
    try:
        information = path.lstat()
    except OSError as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
    return path.is_symlink() or bool(
        getattr(information, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _validate_source_file(path: Path, expected: Path) -> None:
    try:
        if (
            path.resolve(strict=True) != expected
            or _is_reparse_or_link(path)
            or not path.is_file()
        ):
            _fail()
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error


def _remove_owned_modules(owned: Mapping[str, ModuleType]) -> None:
    for name in sorted(owned, key=lambda value: (value.count("."), value), reverse=True):
        if sys.modules.get(name) is owned[name]:
            sys.modules.pop(name, None)


@dataclass(frozen=True)
class _BoundSource:
    name: str
    path: Path
    source: bytes
    digest: str
    identity: tuple[int, int, int]


def _ancestor_paths(path: Path) -> tuple[Path, ...]:
    if not path.is_absolute() or not path.anchor:
        _fail()
    current = Path(path.anchor)
    paths = [current]
    for part in path.parts[1:]:
        current = current / part
        paths.append(current)
    return tuple(paths)


def _source_layout(
    source_root: Path,
    model_cache_path: Path,
    embeddings_path: Path,
) -> tuple[tuple[str, Path], ...]:
    expected = (
        ("model_cache", source_root / "model_cache.py"),
        ("embeddings", source_root / "embeddings.py"),
    )
    supplied = (model_cache_path, embeddings_path)
    if (
        not source_root.is_absolute()
        or any(not path.is_absolute() for path in supplied)
        or any(
            os.path.normcase(os.path.abspath(path))
            != os.path.normcase(os.path.abspath(expected_path))
            for path, (_name, expected_path) in zip(supplied, expected)
        )
    ):
        _fail()
    return tuple(
        (name, path)
        for (name, _expected_path), path in zip(expected, supplied)
    )


@contextmanager
def _bound_authority_sources(
    source_root: Path,
    model_cache_path: Path,
    embeddings_path: Path,
):
    """Read exact source bytes while source files and all ancestors are bound."""

    layout = _source_layout(source_root, model_cache_path, embeddings_path)
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
        get_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        get_info.restype = wintypes.BOOL
        read_file = kernel32.ReadFile
        read_file.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        read_file.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        invalid_handle = wintypes.HANDLE(-1).value
        directory_handles: list[object] = []
        source_handles: list[object] = []

        def open_handle(
            path: Path,
            *,
            desired_access: int,
            share_mode: int,
            flags: int,
        ) -> object:
            handle = create_file(
                str(path),
                desired_access,
                share_mode,
                None,
                3,
                flags,
                None,
            )
            if handle is None or handle == invalid_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            return handle

        def information(handle: object) -> _ByHandleFileInformation:
            info = _ByHandleFileInformation()
            if not get_info(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            return info

        def file_identity(info: _ByHandleFileInformation) -> tuple[int, int, int]:
            return (
                int(info.dwVolumeSerialNumber),
                (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
                (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
            )

        def read_source(handle: object, size: int) -> bytes:
            remaining = size
            chunks: list[bytes] = []
            while remaining:
                requested = min(remaining, 1024 * 1024)
                buffer = ctypes.create_string_buffer(requested)
                count = wintypes.DWORD()
                if not read_file(
                    handle,
                    buffer,
                    requested,
                    ctypes.byref(count),
                    None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if count.value <= 0 or count.value > requested:
                    _fail()
                chunks.append(buffer.raw[: count.value])
                remaining -= count.value
            return b"".join(chunks)

        try:
            for ancestor in _ancestor_paths(source_root):
                handle = open_handle(
                    ancestor,
                    desired_access=0x00000080,
                    share_mode=0x00000001 | 0x00000002,
                    flags=0x02000000 | 0x00200000,
                )
                directory_handles.append(handle)
                info = information(handle)
                if not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400:
                    _fail()

            records: list[_BoundSource] = []
            for name, path in layout:
                handle = open_handle(
                    path,
                    desired_access=0x80000000,
                    share_mode=0x00000001,
                    flags=0x00200000 | 0x08000000,
                )
                source_handles.append(handle)
                info = information(handle)
                identity = file_identity(info)
                if (
                    info.dwFileAttributes & (0x10 | 0x400)
                    or int(info.nNumberOfLinks) != 1
                    or identity[2] <= 0
                    or identity[2] > _MAX_AUTHORITY_SOURCE_BYTES
                ):
                    _fail()
                source = read_source(handle, identity[2])
                records.append(
                    _BoundSource(
                        name=name,
                        path=path,
                        source=source,
                        digest=hashlib.sha256(source).hexdigest(),
                        identity=identity,
                    )
                )
            if records[0].identity[:2] == records[1].identity[:2]:
                _fail()
            yield tuple(records)

            for record in records:
                reopened = open_handle(
                    record.path,
                    desired_access=0x80000000,
                    share_mode=0x00000001,
                    flags=0x00200000 | 0x08000000,
                )
                try:
                    info = information(reopened)
                    identity = file_identity(info)
                    if (
                        info.dwFileAttributes & (0x10 | 0x400)
                        or int(info.nNumberOfLinks) != 1
                        or identity != record.identity
                        or hashlib.sha256(read_source(reopened, identity[2])).hexdigest()
                        != record.digest
                    ):
                        _fail()
                finally:
                    close_handle(reopened)
        except EmbeddingLiveError:
            raise
        except OSError as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        finally:
            for handle in reversed(source_handles):
                close_handle(handle)
            for handle in reversed(directory_handles):
                close_handle(handle)
        return

    directory_descriptors: list[int] = []
    source_descriptors: list[int] = []

    def descriptor_identity(descriptor: int) -> tuple[int, int, int]:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size <= 0
            or info.st_size > _MAX_AUTHORITY_SOURCE_BYTES
        ):
            _fail()
        return (info.st_dev, info.st_ino, info.st_size)

    def read_descriptor(descriptor: int, size: int) -> bytes:
        remaining = size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                _fail()
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for ancestor in _ancestor_paths(source_root):
            descriptor = os.open(ancestor, directory_flags)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                _fail()
            directory_descriptors.append(descriptor)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        records = []
        for name, path in layout:
            descriptor = os.open(path, file_flags)
            source_descriptors.append(descriptor)
            identity = descriptor_identity(descriptor)
            source = read_descriptor(descriptor, identity[2])
            records.append(
                _BoundSource(
                    name=name,
                    path=path,
                    source=source,
                    digest=hashlib.sha256(source).hexdigest(),
                    identity=identity,
                )
            )
        if records[0].identity[:2] == records[1].identity[:2]:
            _fail()
        yield tuple(records)
        for record in records:
            descriptor = os.open(record.path, file_flags)
            try:
                identity = descriptor_identity(descriptor)
                if (
                    identity != record.identity
                    or hashlib.sha256(read_descriptor(descriptor, identity[2])).hexdigest()
                    != record.digest
                ):
                    _fail()
            finally:
                os.close(descriptor)
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
    finally:
        for descriptor in reversed(source_descriptors):
            os.close(descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


class LiveEmbeddingAuthority:
    """One isolated model-cache/embedding module namespace with exact origins."""

    __slots__ = (
        "package_name",
        "model_cache_module",
        "embeddings_module",
        "_owned_modules",
        "_source_root",
        "_source_paths",
        "_source_digests",
        "_source_identities",
        "_closed",
    )

    def __init__(
        self,
        *,
        package_name: str,
        model_cache_module: ModuleType,
        embeddings_module: ModuleType,
        owned_modules: Mapping[str, ModuleType],
        source_root: Path,
        source_records: tuple[_BoundSource, ...],
    ) -> None:
        self.package_name = package_name
        self.model_cache_module = model_cache_module
        self.embeddings_module = embeddings_module
        self._owned_modules = dict(owned_modules)
        self._source_root = source_root
        self._source_paths = {record.name: record.path for record in source_records}
        self._source_digests = {
            record.name: record.digest for record in source_records
        }
        self._source_identities = {
            record.name: record.identity for record in source_records
        }
        self._closed = False

    @classmethod
    def load(cls) -> "LiveEmbeddingAuthority":
        token = secrets.token_hex(16)
        if re.fullmatch(r"[0-9a-f]{32}", token) is None:
            _fail()
        package_name = _PACKAGE_PREFIX + token
        names = (
            package_name,
            package_name + ".model_cache",
            package_name + ".embeddings",
        )
        if any(
            existing == package_name or existing.startswith(package_name + ".")
            for existing in sys.modules
        ):
            _fail()
        owned: dict[str, ModuleType] = {}
        try:
            source_root = _SOURCE_ROOT
            model_cache_path = _MODEL_CACHE_PATH
            embeddings_path = _EMBEDDINGS_PATH
            with _bound_authority_sources(
                source_root,
                model_cache_path,
                embeddings_path,
            ) as records:
                package = ModuleType(package_name)
                package.__file__ = str(source_root / "__init__.py")
                package.__package__ = package_name
                package.__path__ = [str(source_root)]
                package_spec = ModuleSpec(package_name, loader=None, is_package=True)
                package_spec.submodule_search_locations = [str(source_root)]
                package.__spec__ = package_spec
                sys.modules[package_name] = package
                owned[package_name] = package

                def load_module(record: _BoundSource) -> ModuleType:
                    qualified = package_name + "." + record.name
                    if qualified in sys.modules:
                        _fail()
                    specification = ModuleSpec(
                        qualified,
                        loader=None,
                        origin=str(record.path),
                    )
                    module = ModuleType(qualified)
                    module.__file__ = str(record.path)
                    module.__package__ = package_name
                    module.__loader__ = None
                    module.__spec__ = specification
                    sys.modules[qualified] = module
                    owned[qualified] = module
                    code = compile(
                        record.source,
                        str(record.path),
                        "exec",
                        dont_inherit=True,
                    )
                    exec(code, module.__dict__)
                    return module

                model_cache = load_module(records[0])
                embeddings = load_module(records[1])
                authority = cls(
                    package_name=package_name,
                    model_cache_module=model_cache,
                    embeddings_module=embeddings,
                    owned_modules=owned,
                    source_root=source_root,
                    source_records=records,
                )
            authority.assert_valid()
            return authority
        except EmbeddingLiveError:
            _remove_owned_modules(owned)
            raise
        except Exception as error:
            _remove_owned_modules(owned)
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error

    @property
    def owned_module_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._owned_modules))

    @property
    def source_digests(self) -> dict[str, str]:
        return dict(self._source_digests)

    def assert_valid(self) -> None:
        if type(self) is not LiveEmbeddingAuthority or self._closed:
            _fail()
        expected_modules = {
            self.package_name,
            self.package_name + ".model_cache",
            self.package_name + ".embeddings",
        }
        actual_namespace = {
            name
            for name in sys.modules
            if name == self.package_name or name.startswith(self.package_name + ".")
        }
        if (
            not self.package_name.startswith(_PACKAGE_PREFIX)
            or set(self._owned_modules) != expected_modules
            or actual_namespace != expected_modules
            or any(
                sys.modules.get(name) is not module
                for name, module in self._owned_modules.items()
            )
        ):
            _fail()
        model_cache = self.model_cache_module
        embeddings = self.embeddings_module
        if (
            model_cache is not self._owned_modules[self.package_name + ".model_cache"]
            or embeddings is not self._owned_modules[self.package_name + ".embeddings"]
            or model_cache.__name__ != self.package_name + ".model_cache"
            or embeddings.__name__ != self.package_name + ".embeddings"
            or model_cache.__package__ != self.package_name
            or embeddings.__package__ != self.package_name
        ):
            _fail()
        try:
            model_origin = Path(model_cache.__file__)
            embeddings_origin = Path(embeddings.__file__)
            model_spec_origin = Path(model_cache.__spec__.origin)
            embeddings_spec_origin = Path(embeddings.__spec__.origin)
        except (AttributeError, TypeError) as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        if (
            model_origin != self._source_paths["model_cache"]
            or embeddings_origin != self._source_paths["embeddings"]
            or model_spec_origin != self._source_paths["model_cache"]
            or embeddings_spec_origin != self._source_paths["embeddings"]
        ):
            _fail()
        with _bound_authority_sources(
            self._source_root,
            self._source_paths["model_cache"],
            self._source_paths["embeddings"],
        ) as records:
            if any(
                record.digest != self._source_digests.get(record.name)
                or record.identity != self._source_identities.get(record.name)
                for record in records
            ):
                _fail()
        required_model_types = (
            ("ModelManifest", model_cache),
            ("VerifiedModelCache", model_cache),
            ("FinalPhaseResult", model_cache),
        )
        required_embedding_types = (
            ("FastEmbedProvider", embeddings),
            ("EmbeddingProviderIdentity", embeddings),
        )
        for name, module in (*required_model_types, *required_embedding_types):
            value = getattr(module, name, None)
            if (
                not isinstance(value, type)
                or value.__name__ != name
                or value.__module__ != module.__name__
            ):
                _fail()
        if (
            getattr(embeddings, "VerifiedModelCache", None)
            is not model_cache.VerifiedModelCache
            or getattr(embeddings, "ModelCacheError", None)
            is not model_cache.ModelCacheError
            or getattr(embeddings, "validate_verified_generation", None)
            is not model_cache.validate_verified_generation
        ):
            _fail()

    def _expectation_verification(
        self,
        expectation: "FinalExpectation",
    ) -> dict[str, object]:
        if (
            type(expectation) is not FinalExpectation
            or expectation.authority is not self
            or expectation.manifest is not expectation.verified.manifest
            or expectation.final_result.verified is not expectation.verified
        ):
            _fail()
        verification = _canonical_copy(expectation.verification_evidence)
        if (
            type(verification) is not dict
            or _canonical_digest(verification) != expectation.verification_digest
        ):
            _fail()
        return verification

    @contextmanager
    def _bound_verified_generation(
        self,
        *,
        manifest: object,
        verified: object,
        verification: Mapping[str, object],
        verify_after: bool,
    ):
        """Hold the promoted tree from exact reopen through its permitted use."""

        self.assert_valid()
        model_cache = self.model_cache_module
        if (
            type(manifest) is not model_cache.ModelManifest
            or type(verified) is not model_cache.VerifiedModelCache
            or verified.manifest is not manifest
            or not isinstance(verified.generation_root, Path)
            or not verified.generation_root.is_absolute()
            or not verified.generation_root.anchor
            or type(verification) is not dict
        ):
            _fail()
        hold_directories = getattr(
            model_cache,
            "_hold_contained_directory_handles",
            None,
        )
        hold_files = getattr(model_cache, "_hold_regular_file_handles", None)
        deny_writes = getattr(model_cache, "_deny_generation_tree_writes", None)
        reopen_generation = getattr(model_cache, "_verify_promoted_generation", None)
        validate_evidence = getattr(
            model_cache,
            "_validate_final_verification_evidence",
            None,
        )
        if not all(
            callable(value)
            for value in (
                hold_directories,
                hold_files,
                deny_writes,
                reopen_generation,
                validate_evidence,
            )
        ):
            _fail()

        generation_root = verified.generation_root
        approved_root = Path(generation_root.anchor)

        def reopen_and_validate() -> object:
            reopened = reopen_generation(manifest, generation_root)
            if type(reopened) is not model_cache.VerifiedModelCache:
                _fail()
            if (
                reopened.manifest is not manifest
                or reopened.specific_model_path != verified.specific_model_path
                or reopened.cache_digest != verified.cache_digest
                or reopened.generation_root != generation_root
                or reopened.runtime_root != verified.runtime_root
                or reopened.runtime_digest != verified.runtime_digest
                or reopened.wheel_set_digest != verified.wheel_set_digest
                or reopened.generation_digest != verified.generation_digest
            ):
                _fail()
            raw_evidence = _canonical_copy(verification)
            if type(raw_evidence) is not dict or "providerOrigins" not in raw_evidence:
                _fail()
            raw_evidence.pop("providerOrigins")
            validated = validate_evidence(reopened, raw_evidence)
            if type(validated) is not dict or validated != verification:
                _fail()
            return reopened

        try:
            with hold_directories(
                approved_root,
                generation_root,
                code="MODEL_FINAL_VERIFICATION_FAILED",
            ):
                with hold_files(
                    generation_root,
                    code="MODEL_FINAL_VERIFICATION_FAILED",
                ):
                    with deny_writes(
                        generation_root,
                        code="MODEL_FINAL_VERIFICATION_FAILED",
                    ):
                        reopened = reopen_and_validate()
                        try:
                            yield reopened
                        finally:
                            if verify_after:
                                reopen_and_validate()
        except EmbeddingLiveError:
            raise
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        self.assert_valid()

    @contextmanager
    def provider_session(
        self,
        expectation: "FinalExpectation",
        *,
        isolated_temp_parent: Path,
    ):
        verification = self._expectation_verification(expectation)
        with self._bound_verified_generation(
            manifest=expectation.manifest,
            verified=expectation.verified,
            verification=verification,
            verify_after=True,
        ):
            provider = self._create_provider(
                expectation,
                isolated_temp_parent=isolated_temp_parent,
                provider_factory=self.embeddings_module.FastEmbedProvider,
            )
            yield provider

    @contextmanager
    def _provider_session_for_test(
        self,
        expectation: "FinalExpectation",
        *,
        isolated_temp_parent: Path,
        provider_factory: Callable[..., object],
    ):
        if not callable(provider_factory):
            _fail()
        verification = self._expectation_verification(expectation)
        with self._bound_verified_generation(
            manifest=expectation.manifest,
            verified=expectation.verified,
            verification=verification,
            verify_after=True,
        ):
            provider = self._create_provider(
                expectation,
                isolated_temp_parent=isolated_temp_parent,
                provider_factory=provider_factory,
            )
            yield provider

    @contextmanager
    def _final_callback_provider_session(
        self,
        binding: "_PipelineBinding",
        *,
        isolated_temp_parent: Path,
    ):
        """Create the one production provider while the caller owns final locks."""

        self.assert_valid()
        if type(binding) is not _PipelineBinding or binding.authority is not self:
            _fail()
        provider = self._create_provider(
            binding,
            isolated_temp_parent=isolated_temp_parent,
            provider_factory=self.embeddings_module.FastEmbedProvider,
        )
        try:
            yield provider
        finally:
            self.assert_valid()

    @contextmanager
    def _final_callback_provider_session_for_test(
        self,
        binding: "_PipelineBinding",
        *,
        isolated_temp_parent: Path,
        provider_factory: Callable[..., object],
    ):
        if (
            type(binding) is not _PipelineBinding
            or binding.authority is not self
            or not callable(provider_factory)
        ):
            _fail()
        provider = self._create_provider(
            binding,
            isolated_temp_parent=isolated_temp_parent,
            provider_factory=provider_factory,
        )
        try:
            yield provider
        finally:
            self.assert_valid()

    def _create_provider(
        self,
        expectation: "FinalExpectation | _PipelineBinding",
        *,
        isolated_temp_parent: Path,
        provider_factory: Callable[..., object],
    ) -> object:
        self.assert_valid()
        if (
            type(expectation) not in (FinalExpectation, _PipelineBinding)
            or expectation.authority is not self
            or not isinstance(isolated_temp_parent, Path)
        ):
            _fail()
        try:
            resolved_temp = isolated_temp_parent.resolve(strict=True)
            if _is_reparse_or_link(isolated_temp_parent) or not resolved_temp.is_dir():
                _fail()
        except EmbeddingLiveError:
            raise
        except OSError as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        try:
            provider = provider_factory(
                expectation.verified,
                isolated_temp_parent=resolved_temp,
            )
        except EmbeddingLiveError:
            raise
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        self.assert_valid()
        identity = getattr(provider, "identity", None)
        if (
            type(provider) is not self.embeddings_module.FastEmbedProvider
            or type(identity) is not self.embeddings_module.EmbeddingProviderIdentity
        ):
            _fail()
        manifest = expectation.manifest
        expected_identity = {
            "provider": manifest.package.name,
            "provider_version": manifest.package.version,
            "model_id": manifest.model.id,
            "model_revision": manifest.model.revision,
            "artifact_repository": manifest.model.artifact_repository,
            "artifact_revision": manifest.model.artifact_revision,
            "dimension": manifest.model.dimension,
            "encoding_policy": manifest.model.encoding_policy,
            "model_manifest_digest": expectation.manifest_digest,
            "cache_digest": expectation.cache_digest,
            "model_files": tuple(
                (item.path, item.sha256, item.size)
                for item in sorted(manifest.files, key=lambda value: value.path)
            ),
            "runtime_digest": expectation.runtime_digest,
            "wheel_set_digest": expectation.wheel_set_digest,
            "generation_digest": expectation.generation_digest,
        }
        if any(getattr(identity, key, None) != value for key, value in expected_identity.items()):
            _fail()
        return provider

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _remove_owned_modules(self._owned_modules)

    def __enter__(self) -> "LiveEmbeddingAuthority":
        self.assert_valid()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail()
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error


def _canonical_copy(value: object) -> object:
    try:
        return json.loads(_canonical_bytes(value))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


_VERIFICATION_KEYS = {
    "generationDigest",
    "childEvidenceDigest",
    "childLoadedOrigins",
    "providerOrigins",
    "pipeline",
    "pipelineDigest",
}
_PIPELINE_KEYS = {
    "schemaVersion",
    "modelManifestDigest",
    "cacheDigest",
    "runtimeDigest",
    "wheelSetDigest",
    "generationDigest",
    "childEvidenceDigest",
    "childLoadedOrigins",
    "fixture",
    "fixtureDigest",
    "publication",
    "outbox",
    "indexVectorDigest",
    "indexSnapshot",
    "indexSnapshotDigest",
    "retrieval",
    "retrievalDigest",
    "providerEvidence",
    "zeroNetworkReplayDigest",
    "allowedWriteLedger",
}
_PROVIDER_EVIDENCE_KEYS = {
    "schemaVersion",
    "challengeDigest",
    "processId",
    "tempTokenDigest",
    "vectorDigest",
    "providerOrigins",
    "pythonIsolation",
    "jobScope",
}
_PYTHON_ISOLATION_KEYS = {
    "scope",
    "preImportProbes",
    "postInferenceProbes",
    "evidenceDigest",
}
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


def _validate_loaded_origins(value: object) -> list[dict[str, str]]:
    if type(value) is not list or len(value) != 2:
        _fail()
    canonical: list[dict[str, str]] = []
    for item, distribution in zip(value, ("fastembed", "onnxruntime")):
        if type(item) is not dict or set(item) != {
            "distribution",
            "path",
            "sha256",
        }:
            _fail()
        path = item.get("path")
        if (
            item.get("distribution") != distribution
            or not isinstance(path, str)
            or not path.startswith(f"runtime/{distribution}/")
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            _fail()
        digest = _digest(item.get("sha256"))
        canonical.append(
            {
                "distribution": distribution,
                "path": path,
                "sha256": digest,
            }
        )
    return canonical


def _validate_python_isolation(provider_evidence: Mapping[str, object]) -> None:
    isolation = provider_evidence.get("pythonIsolation")
    expected_probes = {
        surface: "denied" for surface in sorted(_CPYTHON_GUARD_PROBES)
    }
    if (
        type(isolation) is not dict
        or set(isolation) != _PYTHON_ISOLATION_KEYS
        or isolation.get("scope") != "trusted-hash-locked-cpython-runtime"
        or isolation.get("preImportProbes") != expected_probes
        or isolation.get("postInferenceProbes") != expected_probes
    ):
        _fail()
    evidence_digest = _digest(isolation.get("evidenceDigest"))
    evidence_core = {
        "challengeDigest": provider_evidence.get("challengeDigest"),
        "processId": provider_evidence.get("processId"),
        "tempTokenDigest": provider_evidence.get("tempTokenDigest"),
        "scope": isolation["scope"],
        "preImportProbes": expected_probes,
        "postInferenceProbes": expected_probes,
    }
    if evidence_digest != _canonical_digest(evidence_core):
        _fail()


@dataclass(frozen=True)
class FinalExpectation:
    authority: LiveEmbeddingAuthority
    manifest: object
    final_result: object
    verified: object
    verification: object
    pipeline: object
    manifest_digest: str
    cache_digest: str
    runtime_digest: str
    wheel_set_digest: str
    generation_digest: str
    model_cache_source_digest: str
    embeddings_source_digest: str
    child_evidence_digest: str
    child_loaded_origins: tuple[Mapping[str, object], ...]
    pipeline_evidence: Mapping[str, object]
    verification_evidence: Mapping[str, object]
    verification_digest: str
    write_boundary: object
    write_boundary_evidence: Mapping[str, object]
    write_boundary_digest: str

    @classmethod
    def from_authority(
        cls,
        manifest: object,
        final_result: object,
        authority: LiveEmbeddingAuthority,
    ) -> "FinalExpectation":
        if type(authority) is not LiveEmbeddingAuthority:
            _fail()
        authority.assert_valid()
        model_cache = authority.model_cache_module
        if (
            type(manifest) is not model_cache.ModelManifest
            or type(final_result) is not model_cache.FinalPhaseResult
        ):
            _fail()
        verified = final_result.verified
        if (
            type(verified) is not model_cache.VerifiedModelCache
            or verified.manifest is not manifest
        ):
            _fail()
        manifest_digest = _digest(manifest.aggregate_digest)
        cache_digest = _digest(verified.cache_digest)
        runtime_digest = _digest(verified.runtime_digest)
        wheel_set_digest = _digest(verified.wheel_set_digest)
        generation_digest = _digest(verified.generation_digest)
        boundary_evidence_getter = getattr(model_cache, "_write_boundary_evidence", None)
        if not callable(boundary_evidence_getter):
            _fail()
        try:
            raw_boundary_evidence = boundary_evidence_getter(final_result.write_boundary)
            boundary_evidence = _canonical_copy(raw_boundary_evidence)
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        if type(boundary_evidence) is not dict:
            _fail()
        raw_verification = final_result.verification
        if type(raw_verification) is not dict:
            _fail()
        verification = _canonical_copy(raw_verification)
        if type(verification) is not dict or set(verification) != _VERIFICATION_KEYS:
            _fail()
        child_evidence_digest = _digest(verification.get("childEvidenceDigest"))
        child_loaded_origins = verification.get("childLoadedOrigins")
        provider_origins = verification.get("providerOrigins")
        pipeline = verification.get("pipeline")
        if (
            verification.get("generationDigest") != generation_digest
            or type(pipeline) is not dict
            or set(pipeline) != _PIPELINE_KEYS
            or pipeline.get("schemaVersion") != 1
        ):
            _fail()
        canonical_origins = _validate_loaded_origins(child_loaded_origins)
        canonical_provider_origins = _validate_loaded_origins(provider_origins)
        canonical_pipeline_origins = _validate_loaded_origins(
            pipeline.get("childLoadedOrigins")
        )
        if (
            canonical_provider_origins != canonical_origins
            or canonical_pipeline_origins != canonical_origins
        ):
            _fail()
        provider_evidence = pipeline.get("providerEvidence")
        if (
            type(provider_evidence) is not dict
            or set(provider_evidence) != _PROVIDER_EVIDENCE_KEYS
            or provider_evidence.get("schemaVersion") != 1
            or type(provider_evidence.get("processId")) is not int
            or provider_evidence["processId"] <= 0
            or provider_evidence.get("jobScope") != "windows-job-kill-on-close"
        ):
            _fail()
        if (
            _validate_loaded_origins(provider_evidence.get("providerOrigins"))
            != canonical_origins
        ):
            _fail()
        for key in ("challengeDigest", "tempTokenDigest", "vectorDigest"):
            _digest(provider_evidence.get(key))
        _validate_python_isolation(provider_evidence)
        _digest(pipeline.get("indexVectorDigest"))
        computed_child_digest = _canonical_digest(provider_evidence)
        pipeline_digest = _digest(verification.get("pipelineDigest"))
        expected_pipeline = {
            "modelManifestDigest": manifest_digest,
            "cacheDigest": cache_digest,
            "runtimeDigest": runtime_digest,
            "wheelSetDigest": wheel_set_digest,
            "generationDigest": generation_digest,
            "childEvidenceDigest": child_evidence_digest,
            "childLoadedOrigins": canonical_origins,
        }
        if (
            any(pipeline.get(key) != value for key, value in expected_pipeline.items())
            or child_evidence_digest != computed_child_digest
            or pipeline_digest != _canonical_digest(pipeline)
        ):
            _fail()
        try:
            with authority._bound_verified_generation(
                manifest=manifest,
                verified=verified,
                verification=verification,
                verify_after=False,
            ):
                pass
            canonical_pipeline = _canonical_copy(pipeline)
            if type(canonical_pipeline) is not dict:
                _fail()
            canonical_verification = _canonical_copy(verification)
            if type(canonical_verification) is not dict:
                _fail()
            canonical_origins_tuple = tuple(
                _canonical_copy(item) for item in canonical_origins
            )
        except EmbeddingLiveError:
            raise
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        authority.assert_valid()
        source_digests = authority.source_digests
        return cls(
            authority=authority,
            manifest=manifest,
            final_result=final_result,
            verified=verified,
            verification=raw_verification,
            pipeline=raw_verification["pipeline"],
            manifest_digest=manifest_digest,
            cache_digest=cache_digest,
            runtime_digest=runtime_digest,
            wheel_set_digest=wheel_set_digest,
            generation_digest=generation_digest,
            model_cache_source_digest=_digest(source_digests.get("model_cache")),
            embeddings_source_digest=_digest(source_digests.get("embeddings")),
            child_evidence_digest=child_evidence_digest,
            child_loaded_origins=canonical_origins_tuple,
            pipeline_evidence=canonical_pipeline,
            verification_evidence=canonical_verification,
            verification_digest=_canonical_digest(canonical_verification),
            write_boundary=final_result.write_boundary,
            write_boundary_evidence=boundary_evidence,
            write_boundary_digest=_canonical_digest(boundary_evidence),
        )


@dataclass(frozen=True)
class _PipelineBinding:
    """Minimum exact authority state needed before a FinalPhaseResult exists."""

    authority: LiveEmbeddingAuthority
    manifest: object
    verified: object
    manifest_digest: str
    cache_digest: str
    runtime_digest: str
    wheel_set_digest: str
    generation_digest: str
    child_loaded_origins: tuple[Mapping[str, object], ...] | None

    @classmethod
    def from_verified(
        cls,
        authority: LiveEmbeddingAuthority,
        manifest: object,
        verified: object,
    ) -> "_PipelineBinding":
        if type(authority) is not LiveEmbeddingAuthority:
            _fail()
        authority.assert_valid()
        model_cache = authority.model_cache_module
        if (
            type(manifest) is not model_cache.ModelManifest
            or type(verified) is not model_cache.VerifiedModelCache
            or verified.manifest is not manifest
        ):
            _fail()
        validator = getattr(model_cache, "validate_verified_generation", None)
        if not callable(validator):
            _fail()
        try:
            validator(verified)
        except EmbeddingLiveError:
            raise
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        authority.assert_valid()
        return cls(
            authority=authority,
            manifest=manifest,
            verified=verified,
            manifest_digest=_digest(manifest.aggregate_digest),
            cache_digest=_digest(verified.cache_digest),
            runtime_digest=_digest(verified.runtime_digest),
            wheel_set_digest=_digest(verified.wheel_set_digest),
            generation_digest=_digest(verified.generation_digest),
            child_loaded_origins=None,
        )


class _IndexProviderBridge:
    """Expose one authority provider through the catalog's path-free identity.

    Index construction and the fixed query share one evidence-bearing child
    inference.  The query vector is retained in memory so neither the writable
    nor the read-only retrieval pass can start an unbound second child.
    """

    __slots__ = (
        "identity",
        "_provider",
        "_expectation",
        "_document_vectors",
        "_query_vector",
        "_provider_evidence",
    )

    def __init__(
        self,
        provider: object,
        expectation: FinalExpectation | _PipelineBinding,
    ) -> None:
        authority = expectation.authority
        authority.assert_valid()
        unique_identity = getattr(provider, "identity", None)
        if (
            type(provider) is not authority.embeddings_module.FastEmbedProvider
            or type(unique_identity)
            is not authority.embeddings_module.EmbeddingProviderIdentity
        ):
            _fail()
        try:
            self.identity = EmbeddingProviderIdentity(
                provider=unique_identity.provider,
                provider_version=unique_identity.provider_version,
                model_id=unique_identity.model_id,
                model_revision=unique_identity.model_revision,
                artifact_repository=unique_identity.artifact_repository,
                artifact_revision=unique_identity.artifact_revision,
                dimension=unique_identity.dimension,
                encoding_policy=unique_identity.encoding_policy,
                model_manifest_digest=unique_identity.model_manifest_digest,
                cache_digest=unique_identity.cache_digest,
                model_files=tuple(tuple(item) for item in unique_identity.model_files),
                runtime_digest=unique_identity.runtime_digest,
                wheel_set_digest=unique_identity.wheel_set_digest,
                generation_digest=unique_identity.generation_digest,
            )
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        self._provider = provider
        self._expectation = expectation
        self._document_vectors: tuple[tuple[float, ...], ...] | None = None
        self._query_vector: tuple[float, ...] | None = None
        self._provider_evidence: dict[str, object] | None = None

    @property
    def document_vectors(self) -> tuple[tuple[float, ...], ...]:
        if self._document_vectors is None:
            _fail()
        return self._document_vectors

    @property
    def provider_evidence(self) -> dict[str, object]:
        if self._provider_evidence is None:
            _fail()
        copied = _canonical_copy(self._provider_evidence)
        if type(copied) is not dict:
            _fail()
        return copied

    def embed_documents(
        self,
        texts: object,
    ) -> tuple[tuple[float, ...], ...]:
        if self._document_vectors is not None:
            _fail()
        try:
            documents = tuple(texts)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError) as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        if not documents or len(documents) > 999 or any(
            not isinstance(value, str) for value in documents
        ):
            _fail()
        infer = getattr(self._provider, "embed_documents_with_evidence", None)
        if not callable(infer):
            _fail()
        try:
            raw_vectors, raw_evidence = infer((*documents, _PIPELINE_QUERY))
            vectors = tuple(
                validate_embedding_vector(value, dimension=512)
                for value in tuple(raw_vectors)
            )
        except EmbeddingLiveError:
            raise
        except Exception as error:
            raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
        if len(vectors) != len(documents) + 1:
            _fail()
        evidence = self._validated_evidence(raw_evidence, vectors=vectors)
        self._document_vectors = vectors[:-1]
        self._query_vector = vectors[-1]
        self._provider_evidence = evidence
        return self._document_vectors

    def embed_query(self, text: str) -> tuple[float, ...]:
        if text != _PIPELINE_QUERY or self._query_vector is None:
            _fail()
        return self._query_vector

    def _validated_evidence(
        self,
        value: object,
        *,
        vectors: tuple[tuple[float, ...], ...],
    ) -> dict[str, object]:
        copied = _canonical_copy(value)
        if (
            type(copied) is not dict
            or set(copied) != _PROVIDER_EVIDENCE_KEYS
            or copied.get("schemaVersion") != 1
            or type(copied.get("processId")) is not int
            or copied["processId"] <= 0
            or copied.get("jobScope") != "windows-job-kill-on-close"
            or copied.get("vectorDigest") != _canonical_digest(vectors)
        ):
            _fail()
        for key in ("challengeDigest", "tempTokenDigest", "vectorDigest"):
            _digest(copied.get(key))
        origins = _validate_loaded_origins(copied.get("providerOrigins"))
        expected = self._expectation.child_loaded_origins
        if expected is not None and origins != [dict(item) for item in expected]:
            _fail()
        _validate_python_isolation(copied)
        return copied


def _pipeline_time(clock: Callable[[], datetime]) -> datetime:
    if not callable(clock):
        _fail()
    try:
        value = clock()
    except Exception as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
    if not isinstance(value, datetime) or value.utcoffset() is None:
        _fail()
    return value.astimezone(timezone.utc)


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fixed_card(catalog: KnowledgeCatalog, *, now: datetime) -> KnowledgeCardVersion:
    seed_vocabulary(catalog)
    source = SourceAssetVersion(
        logical_id="logical-source-fixture",
        version_id="source-fixture",
        revision=1,
        content_digest=_text_digest("source-fixture"),
        created_at=now,
        created_by=_PIPELINE_ACTOR,
        locator=SourceLocator(
            root_id="fixture",
            relative_path="card-fixture.md",
        ),
        display_name="card-fixture.md",
        source_kind="markdown",
        media_type="text/markdown",
        byte_size=20,
        extraction_status="parsed",
    )
    chunk = ExtractedChunk(
        chunk_id="chunk-fixture",
        source_version_id=source.version_id,
        ordinal=0,
        modality="text",
        language="en",
        normalized_text="Evidence for RFM analysis",
        content_digest=_text_digest("chunk:card-fixture"),
        locator=ChunkLocator(kind="markdown-section", ast_path=(1,)),
    )
    catalog.insert_source(source)
    catalog.insert_chunk(chunk)
    candidate = KnowledgeCardVersion(
        logical_id="logical-card-fixture",
        version_id="card-fixture",
        revision=1,
        content_digest=_text_digest("card:card-fixture"),
        created_at=now,
        created_by=_PIPELINE_ACTOR,
        main_type_id="concept",
        title="RFM analysis",
        learning_objective="Understand RFM analysis",
        content_ast=(CardContentNode(type="paragraph", text="Body for RFM analysis"),),
        suggested_minutes=5,
        vocabulary_version_id=VOCABULARY_VERSION_ID,
        tag_assignments=tuple(
            TagAssignment(
                vocabulary_version_id=VOCABULARY_VERSION_ID,
                dimension_id=tag.split(":", 1)[0],
                tag_id=tag,
            )
            for tag in (
                "topic:data-analysis",
                "audience:analyst",
                "difficulty:beginner",
                "tool:spreadsheet",
            )
        ),
        chunk_citations=(
            ChunkCitation(
                chunk_id=chunk.chunk_id,
                source_version_id=source.version_id,
            ),
        ),
        status="review",
    )
    return publish_card(candidate, catalog)


def _enqueue_fixture(
    catalog: KnowledgeCatalog,
    card: KnowledgeCardVersion,
    *,
    now: datetime,
) -> tuple[OperationRequest, object]:
    request = OperationRequest(
        operation_id="operation-fixture",
        request_digest=_text_digest("request:fixture"),
        actor=_PIPELINE_ACTOR,
        session_id="index-session",
    )

    def mutation() -> OperationMutationResult:
        return OperationMutationResult(
            result_refs={"card_version_id": card.version_id},
            item_outcomes=(),
            index_outbox=(
                IndexOutboxItem(
                    outbox_id="outbox-fixture",
                    card_version_id=card.version_id,
                    action="upsert",
                ),
            ),
        )

    outcome = run_operation(catalog, request, mutation, clock=lambda: now)
    return request, outcome


def _retrieval_projection(result: object) -> dict[str, object]:
    query_digest = getattr(result, "query_digest", None)
    hits = getattr(result, "hits", None)
    evidence = getattr(result, "evidence", None)
    if (
        not isinstance(query_digest, str)
        or type(hits) is not tuple
        or evidence is None
    ):
        _fail()
    output = getattr(evidence, "output_summary", None)
    if not isinstance(output, Mapping):
        _fail()
    policy = output.get("policy")
    if not isinstance(policy, Mapping):
        _fail()
    projected_hits: list[dict[str, object]] = []
    for hit in hits:
        card = getattr(hit, "card", None)
        score = getattr(hit, "score_components", None)
        if card is None or score is None:
            _fail()
        projected_hits.append(
            {
                "cardVersionId": card.version_id,
                "ftsRank": score.fts_rank,
                "semanticRank": score.semantic_rank,
                "score": score.rrf_score,
            }
        )
    projection = {
        "queryDigest": query_digest,
        "filteredCandidateDigest": output.get("filtered_candidate_digest"),
        "snapshotDigest": output.get("index_snapshot_digest"),
        "rrfK": policy.get("k"),
        "hits": projected_hits,
    }
    copied = _canonical_copy(projection)
    if type(copied) is not dict:
        _fail()
    return copied


def _pipeline_paths(
    database_path: Path,
    temp_parent: Path,
) -> tuple[Path, Path]:
    if not isinstance(database_path, Path) or not isinstance(temp_parent, Path):
        _fail()
    try:
        resolved_database = database_path.resolve(strict=False)
        resolved_temp = temp_parent.resolve(strict=True)
        if (
            database_path.exists()
            or _is_reparse_or_link(temp_parent)
            or not resolved_temp.is_dir()
            or not resolved_database.is_absolute()
            or not resolved_temp.is_absolute()
        ):
            _fail()
        resolved_database.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse_or_link(resolved_database.parent):
            _fail()
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error
    return resolved_database, resolved_temp


def _execute_fresh_pipeline(
    expectation: FinalExpectation | _PipelineBinding,
    *,
    database_path: Path,
    temp_parent: Path,
    clock: Callable[[], datetime],
    provider_context: Callable[[], object],
) -> dict[str, object]:
    if (
        type(expectation) not in (FinalExpectation, _PipelineBinding)
        or expectation.authority is not getattr(expectation, "authority", None)
    ):
        _fail()
    expectation.authority.assert_valid()
    resolved_database, resolved_temp = _pipeline_paths(database_path, temp_parent)
    now = _pipeline_time(clock)
    fixture = {
        "schemaVersion": 1,
        "schemaId": _PIPELINE_SCHEMA_ID,
        "sourceVersionId": "source-fixture",
        "chunkId": "chunk-fixture",
        "cardVersionId": "card-fixture",
        "query": _PIPELINE_QUERY,
    }
    fixture_digest = _canonical_digest(fixture)
    try:
        with provider_context() as unique_provider:  # type: ignore[attr-defined]
            bridge = _IndexProviderBridge(unique_provider, expectation)
            with KnowledgeCatalog.open(resolved_database) as catalog:
                published = _fixed_card(catalog, now=now)
                request, operation = _enqueue_fixture(catalog, published, now=now)
                claim = claim_next_index_outbox(
                    catalog,
                    worker_id="worker-fixture",
                    now=now,
                    lease_seconds=30,
                )
                if claim is None:
                    _fail()
                snapshot = complete_index_claim(
                    catalog,
                    claim_id=claim.claim_id,
                    worker_id="worker-fixture",
                    embedding_provider=bridge,
                    now=now + timedelta(seconds=1),
                )
                first_retrieval = KnowledgeRetriever(
                    catalog,
                    embedding_provider=bridge,
                ).search(
                    RetrievalQuery(
                        text=_PIPELINE_QUERY,
                        index_snapshot_id=snapshot.index_snapshot_id,
                        limit=1,
                    )
                )
                first_projection = _retrieval_projection(first_retrieval)
                result_row = catalog.connection.execute(
                    "SELECT status FROM knowledge_index_outbox_results "
                    "WHERE claim_id = ?",
                    (claim.claim_id,),
                ).fetchone()
                if (
                    getattr(operation, "status", None) != "committed"
                    or result_row != ("succeeded",)
                ):
                    _fail()

            with KnowledgeCatalog.open_read_only(resolved_database) as catalog:
                reopened_retrieval = KnowledgeRetriever(
                    catalog,
                    embedding_provider=bridge,
                ).search(
                    RetrievalQuery(
                        text=_PIPELINE_QUERY,
                        index_snapshot_id=snapshot.index_snapshot_id,
                        limit=1,
                    )
                )
                reopened_projection = _retrieval_projection(reopened_retrieval)
            if reopened_projection != first_projection:
                _fail()

            provider_evidence = bridge.provider_evidence
            index_vector_digest = _canonical_digest(bridge.document_vectors[0])
    except EmbeddingLiveError:
        raise
    except Exception as error:
        raise EmbeddingLiveError("EMBEDDING_MODEL_AUTHORITY_INVALID") from error

    index_snapshot = {
        "id": snapshot.index_snapshot_id,
        "status": snapshot.status,
        "retrievalMode": snapshot.retrieval_mode,
        "candidateDigest": snapshot.candidate_digest,
        "digest": snapshot.snapshot_digest,
    }
    allowed_write_core = {
        "allowedRoots": [str(resolved_database), str(resolved_temp)],
        "nativeGlobalCoverage": "not-certified",
        "scope": "pipeline-declared-roots",
    }
    isolation = provider_evidence["pythonIsolation"]
    assert isinstance(isolation, dict)
    replay_core = {
        "fixtureDigest": fixture_digest,
        "generationDigest": expectation.generation_digest,
        "providerOrigins": provider_evidence["providerOrigins"],
        "scope": isolation["scope"],
        "preImportProbes": isolation["preImportProbes"],
        "postInferenceProbes": isolation["postInferenceProbes"],
    }
    pipeline = {
        "schemaVersion": 1,
        "modelManifestDigest": expectation.manifest_digest,
        "cacheDigest": expectation.cache_digest,
        "runtimeDigest": expectation.runtime_digest,
        "wheelSetDigest": expectation.wheel_set_digest,
        "generationDigest": expectation.generation_digest,
        "childEvidenceDigest": _canonical_digest(provider_evidence),
        "childLoadedOrigins": provider_evidence["providerOrigins"],
        "fixture": fixture,
        "fixtureDigest": fixture_digest,
        "publication": {
            "cardVersionId": published.version_id,
            "status": published.status,
            "contentDigest": published.content_digest,
        },
        "outbox": {
            "requestDigest": request.request_digest,
            "contentDigest": claim.outbox_content_digest,
            "claimDigest": claim.claim_digest,
            "claimStatus": "completed",
        },
        "indexVectorDigest": index_vector_digest,
        "indexSnapshot": index_snapshot,
        "indexSnapshotDigest": snapshot.snapshot_digest,
        "retrieval": first_projection,
        "retrievalDigest": _canonical_digest(first_projection),
        "providerEvidence": provider_evidence,
        "zeroNetworkReplayDigest": _canonical_digest(replay_core),
        "allowedWriteLedger": {
            **allowed_write_core,
            "digest": _canonical_digest(allowed_write_core),
        },
    }
    canonical = _canonical_copy(pipeline)
    if type(canonical) is not dict or set(canonical) != _PIPELINE_KEYS:
        _fail()
    return canonical


def _final_verification_evidence(
    binding: _PipelineBinding,
    pipeline: object,
) -> dict[str, object]:
    if type(binding) is not _PipelineBinding or type(pipeline) is not dict:
        _fail()
    if (
        set(pipeline) != _PIPELINE_KEYS
        or pipeline.get("generationDigest") != binding.generation_digest
        or pipeline.get("modelManifestDigest") != binding.manifest_digest
        or pipeline.get("cacheDigest") != binding.cache_digest
        or pipeline.get("runtimeDigest") != binding.runtime_digest
        or pipeline.get("wheelSetDigest") != binding.wheel_set_digest
    ):
        _fail()
    evidence = {
        "generationDigest": binding.generation_digest,
        "childEvidenceDigest": _digest(pipeline.get("childEvidenceDigest")),
        "childLoadedOrigins": _validate_loaded_origins(
            pipeline.get("childLoadedOrigins")
        ),
        "pipeline": pipeline,
        "pipelineDigest": _canonical_digest(pipeline),
    }
    canonical = _canonical_copy(evidence)
    if type(canonical) is not dict or "providerOrigins" in canonical:
        _fail()
    return canonical


def run_final_verification_callback(
    authority: LiveEmbeddingAuthority,
    manifest: object,
    verified: object,
    *,
    database_path: Path,
    temp_parent: Path,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Run the first fresh pipeline inside model_cache.run_final_phase locks."""

    binding = _PipelineBinding.from_verified(authority, manifest, verified)
    pipeline = _execute_fresh_pipeline(
        binding,
        database_path=database_path,
        temp_parent=temp_parent,
        clock=clock,
        provider_context=lambda: authority._final_callback_provider_session(
            binding,
            isolated_temp_parent=temp_parent,
        ),
    )
    return _final_verification_evidence(binding, pipeline)


def _run_final_verification_callback_for_test(
    authority: LiveEmbeddingAuthority,
    manifest: object,
    verified: object,
    *,
    database_path: Path,
    temp_parent: Path,
    provider_factory: Callable[..., object],
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Test seam for callback orchestration; production has no provider input."""

    if not callable(provider_factory):
        _fail()
    binding = _PipelineBinding.from_verified(authority, manifest, verified)
    pipeline = _execute_fresh_pipeline(
        binding,
        database_path=database_path,
        temp_parent=temp_parent,
        clock=clock,
        provider_context=lambda: authority._final_callback_provider_session_for_test(
            binding,
            isolated_temp_parent=temp_parent,
            provider_factory=provider_factory,
        ),
    )
    return _final_verification_evidence(binding, pipeline)


def run_fresh_pipeline(
    expectation: FinalExpectation,
    *,
    database_path: Path,
    temp_parent: Path,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Run the fixed pipeline through the exact public authority provider."""

    if type(expectation) is not FinalExpectation:
        _fail()
    return _execute_fresh_pipeline(
        expectation,
        database_path=database_path,
        temp_parent=temp_parent,
        clock=clock,
        provider_context=lambda: expectation.authority.provider_session(
            expectation,
            isolated_temp_parent=temp_parent,
        ),
    )


def _run_fresh_pipeline_for_test(
    expectation: FinalExpectation,
    *,
    database_path: Path,
    temp_parent: Path,
    provider_factory: Callable[..., object],
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Test seam; production callers cannot supply provider construction."""

    if type(expectation) is not FinalExpectation or not callable(provider_factory):
        _fail()
    return _execute_fresh_pipeline(
        expectation,
        database_path=database_path,
        temp_parent=temp_parent,
        clock=clock,
        provider_context=lambda: expectation.authority._provider_session_for_test(
            expectation,
            isolated_temp_parent=temp_parent,
            provider_factory=provider_factory,
        ),
    )


def _bound_receipt_inputs(
    expectation: FinalExpectation,
    final_result: object,
    pipeline_evidence: object,
) -> tuple[object, object, dict[str, object]]:
    """Return only the exact authority-owned final objects accepted by receipts."""

    if type(expectation) is not FinalExpectation:
        _receipt_fail()
    authority = expectation.authority
    try:
        authority.assert_valid()
        model_cache = authority.model_cache_module
        if (
            type(final_result) is not model_cache.FinalPhaseResult
            or final_result is not expectation.final_result
            or final_result.verified is not expectation.verified
            or final_result.verified.manifest is not expectation.manifest
            or final_result.verification is not expectation.verification
            or type(final_result.verification) is not dict
            or set(final_result.verification) != _VERIFICATION_KEYS
            or pipeline_evidence is not final_result.verification.get("pipeline")
            or pipeline_evidence is not expectation.pipeline
            or _canonical_bytes(final_result.verification)
            != _canonical_bytes(expectation.verification_evidence)
            or _canonical_bytes(pipeline_evidence)
            != _canonical_bytes(expectation.pipeline_evidence)
            or _canonical_digest(final_result.verification)
            != expectation.verification_digest
            or final_result.verification.get("pipelineDigest")
            != _canonical_digest(pipeline_evidence)
            or final_result.verification.get("generationDigest")
            != expectation.generation_digest
        ):
            _receipt_fail()
        verified = final_result.verified
        if (
            verified.cache_digest != expectation.cache_digest
            or verified.runtime_digest != expectation.runtime_digest
            or verified.wheel_set_digest != expectation.wheel_set_digest
            or verified.generation_digest != expectation.generation_digest
            or expectation.manifest.aggregate_digest != expectation.manifest_digest
        ):
            _receipt_fail()
        pipeline = _canonical_copy(pipeline_evidence)
        if type(pipeline) is not dict or set(pipeline) != _PIPELINE_KEYS:
            _receipt_fail()
        return expectation.manifest, verified, pipeline
    except EmbeddingLiveError as error:
        if error.code == _RECEIPT_ERROR:
            raise
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    except Exception as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def _canonical_receipt_time(value: object) -> tuple[str, datetime]:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        _receipt_fail()
    normalized = value.astimezone(timezone.utc)
    text = normalized.isoformat()
    if datetime.fromisoformat(text) != normalized:
        _receipt_fail()
    return text, normalized


def _receipt_unsigned(
    expectation: FinalExpectation,
    final_result: object,
    pipeline_evidence: object,
    started_at: object,
    finished_at: object,
) -> dict[str, object]:
    manifest, _verified, pipeline = _bound_receipt_inputs(
        expectation,
        final_result,
        pipeline_evidence,
    )
    started_text, started = _canonical_receipt_time(started_at)
    finished_text, finished = _canonical_receipt_time(finished_at)
    if finished < started:
        _receipt_fail()
    try:
        snapshot = pipeline["indexSnapshot"]
        publication = pipeline["publication"]
        retrieval = pipeline["retrieval"]
        ledger = pipeline["allowedWriteLedger"]
        provider_evidence = pipeline["providerEvidence"]
        if (
            type(snapshot) is not dict
            or type(publication) is not dict
            or type(retrieval) is not dict
            or type(ledger) is not dict
            or type(provider_evidence) is not dict
            or type(provider_evidence.get("pythonIsolation")) is not dict
        ):
            _receipt_fail()
        unsigned: dict[str, object] = {
            "schemaVersion": 1,
            "producer": "course-helper/embedding-model-live@1",
            "status": "verified",
            "policyId": "course-studio-rrf-v1",
            "manifestDigest": expectation.manifest_digest,
            "model": {
                "id": manifest.model.id,
                "revision": manifest.model.revision,
                "artifactRepository": manifest.model.artifact_repository,
                "artifactRevision": manifest.model.artifact_revision,
                "dimension": manifest.model.dimension,
                "encodingPolicy": manifest.model.encoding_policy,
            },
            "provider": {
                "name": manifest.package.name,
                "version": manifest.package.version,
            },
            "modelFiles": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in sorted(manifest.files, key=lambda item: item.path)
            ],
            "runtime": {
                "python": manifest.runtime.python,
                "os": manifest.runtime.os,
                "architecture": manifest.runtime.architecture,
                "runtimeDigest": expectation.runtime_digest,
                "wheelSetDigest": expectation.wheel_set_digest,
                "generationDigest": expectation.generation_digest,
                "wheels": [
                    {
                        "name": item.name,
                        "version": item.version,
                        "filename": item.filename,
                        "size": item.size,
                        "sha256": item.sha256,
                    }
                    for item in manifest.runtime.wheels
                ],
            },
            "cacheDigest": expectation.cache_digest,
            "fixtureFingerprint": pipeline["fixtureDigest"],
            "indexSnapshot": {
                "id": snapshot["id"],
                "digest": pipeline["indexSnapshotDigest"],
                "candidateDigest": snapshot["candidateDigest"],
                "publishedDigest": publication["contentDigest"],
            },
            "retrieval": retrieval,
            "osNetworkIsolation": {
                "status": "not-certified",
                "scope": provider_evidence["pythonIsolation"]["scope"],
                "pythonAuditHook": "verified",
                "cpythonSocketGuards": "verified",
                "nativeWinsockCoverage": "not-certified",
            },
            "zeroNetworkReplayDigest": pipeline["zeroNetworkReplayDigest"],
            "zeroWriteProof": {
                "scope": "verified-generation-tree",
                "status": "write-denied",
                "nativeGlobalCoverage": "not-certified",
                "evidenceDigest": expectation.write_boundary_digest,
            },
            "checks": [
                {"code": code, "status": "passed"} for code in _RECEIPT_CHECKS
            ],
            "startedAt": started_text,
            "finishedAt": finished_text,
        }
        copied = _canonical_copy(unsigned)
        if type(copied) is not dict:
            _receipt_fail()
        return copied
    except EmbeddingLiveError:
        raise
    except Exception as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def build_receipt(
    expectation: FinalExpectation,
    final_result: object,
    pipeline_evidence: object,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, object]:
    """Build the sole canonical receipt for one exact final authority result."""

    try:
        unsigned = _receipt_unsigned(
            expectation,
            final_result,
            pipeline_evidence,
            started_at,
            finished_at,
        )
        receipt = {**unsigned, "receiptDigest": _canonical_digest(unsigned)}
        detached = _canonical_copy(receipt)
        if type(detached) is not dict or set(detached) != _RECEIPT_KEYS:
            _receipt_fail()
        return detached
    except EmbeddingLiveError as error:
        if error.code == _RECEIPT_ERROR:
            raise
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    except Exception as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def _strict_receipt_json(raw: bytes) -> object:
    if type(raw) is not bytes or len(raw) > _MAX_RECEIPT_BYTES:
        _receipt_fail()

    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error

    def finite(item: object) -> None:
        if type(item) is float and not math.isfinite(item):
            _receipt_fail()
        if type(item) is list:
            for child in item:
                finite(child)
        elif type(item) is dict:
            for child in item.values():
                finite(child)

    finite(value)
    return value


def _parse_receipt_time(value: object) -> datetime:
    if not isinstance(value, str):
        _receipt_fail()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    canonical, normalized = _canonical_receipt_time(parsed)
    if value != canonical:
        _receipt_fail()
    return normalized


def _validate_receipt_bytes(
    raw: bytes,
    expectation: FinalExpectation,
    expected_final_result: object,
) -> dict[str, object]:
    try:
        payload = _strict_receipt_json(raw)
        if type(payload) is not dict or set(payload) != _RECEIPT_KEYS:
            _receipt_fail()
        digest = payload.get("receiptDigest")
        unsigned = dict(payload)
        unsigned.pop("receiptDigest", None)
        if (
            not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or _canonical_digest(unsigned) != digest
        ):
            _receipt_fail()
        started = _parse_receipt_time(payload.get("startedAt"))
        finished = _parse_receipt_time(payload.get("finishedAt"))
        expected = _receipt_unsigned(
            expectation,
            expected_final_result,
            expected_final_result.verification.get("pipeline"),
            started,
            finished,
        )
        # Canonical bytes preserve JSON scalar types, unlike Python equality
        # (where True == 1), and bind every nested field in one comparison.
        if _canonical_bytes(unsigned) != _canonical_bytes(expected):
            _receipt_fail()
        detached = _canonical_copy(payload)
        if type(detached) is not dict:
            _receipt_fail()
        return detached
    except EmbeddingLiveError as error:
        if error.code == _RECEIPT_ERROR:
            raise
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    except Exception as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def validate_receipt(
    path: Path,
    expectation: FinalExpectation,
    expected_final_result: object,
) -> dict[str, object]:
    """Strictly validate and detach one receipt from its authority result."""

    if not isinstance(path, Path):
        _receipt_fail()
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    return _validate_receipt_bytes(raw, expectation, expected_final_result)


@dataclass(frozen=True)
class _ReceiptFile:
    path: Path
    identity: tuple[int, int, int]
    data: bytes


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _no_reparse_ancestors(path: Path) -> None:
    try:
        for ancestor in _ancestor_paths(path):
            info = ancestor.lstat()
            if (
                ancestor.is_symlink()
                or not stat.S_ISDIR(info.st_mode)
                or bool(
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
            ):
                _receipt_fail()
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def _receipt_paths(
    temporary: Path,
    sealed: Path,
    quarantine_root: Path,
) -> tuple[Path, Path, Path]:
    if not all(isinstance(value, Path) for value in (temporary, sealed, quarantine_root)):
        _receipt_fail()
    try:
        if not all(value.is_absolute() for value in (temporary, sealed, quarantine_root)):
            _receipt_fail()
        quarantine = quarantine_root.resolve(strict=True)
        temp = temporary.resolve(strict=True)
        destination_parent = sealed.parent.resolve(strict=True)
        destination = destination_parent / sealed.name
        if (
            not quarantine.is_dir()
            or not sealed.name
            or sealed.name in {".", ".."}
            or not _same_path(quarantine_root, quarantine)
            or not _same_path(temporary, temp)
            or not _same_path(sealed.parent, destination_parent)
            or not _same_path(sealed, destination)
            or _same_path(temp, destination)
        ):
            _receipt_fail()
        try:
            temp.relative_to(quarantine)
        except ValueError as error:
            raise EmbeddingLiveError(_RECEIPT_ERROR) from error
        try:
            destination.relative_to(quarantine)
        except ValueError:
            pass
        else:
            _receipt_fail()
        _no_reparse_ancestors(quarantine)
        _no_reparse_ancestors(temp.parent)
        _no_reparse_ancestors(destination_parent)
        return temp, destination, quarantine
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def _flush_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
        if os.name == "nt":
            import ctypes
            import msvcrt

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            flush = kernel32.FlushFileBuffers
            flush.argtypes = [ctypes.c_void_p]
            flush.restype = ctypes.c_int
            handle = msvcrt.get_osfhandle(descriptor)
            if handle == -1 or not flush(handle):
                raise ctypes.WinError(ctypes.get_last_error())
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def _read_receipt_file(path: Path, *, flush: bool) -> _ReceiptFile:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > _MAX_RECEIPT_BYTES
            or bool(
                getattr(before, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            _receipt_fail()
        flags = (os.O_RDWR if flush else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino, opened.st_size)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or identity != (before.st_dev, before.st_ino, before.st_size)
        ):
            _receipt_fail()
        if flush:
            _flush_descriptor(descriptor)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                _receipt_fail()
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _receipt_fail()
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size) != identity:
            _receipt_fail()
        return _ReceiptFile(path=path, identity=identity, data=b"".join(chunks))
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _identity_at(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            return None
        return (info.st_dev, info.st_ino, info.st_size)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


def _atomic_receipt_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _remove_known_receipt(path: Path, identity: tuple[int, int, int]) -> bool:
    if _identity_at(path) != identity:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _write_prior_recovery(quarantine: Path, data: bytes) -> _ReceiptFile:
    path = quarantine / (".receipt-recovery-" + secrets.token_hex(16) + ".bak")
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                _receipt_fail()
            offset += written
        _flush_descriptor(descriptor)
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    recovery = _read_receipt_file(path, flush=False)
    if recovery.data != data:
        _receipt_fail()
    return recovery


def _restore_seal_state(
    *,
    temporary: Path,
    destination: Path,
    backup: Path,
    installed_identities: tuple[tuple[int, int, int], ...],
    prior_identity: tuple[int, int, int] | None,
    prior_moved: bool,
    prior_recovery: _ReceiptFile | None = None,
) -> None:
    # Never unlink an attacker-swapped or otherwise unidentified path.
    for identity in installed_identities:
        if _remove_known_receipt(destination, identity):
            break
    for identity in installed_identities:
        if _remove_known_receipt(temporary, identity):
            break
    if prior_moved and prior_identity is not None:
        source = prior_recovery
        if source is not None and not _path_present(destination):
            try:
                checked = _read_receipt_file(source.path, flush=False)
            except EmbeddingLiveError:
                checked = _write_prior_recovery(backup.parent, source.data)
                source = checked
            if checked.identity != source.identity or checked.data != source.data:
                source = _write_prior_recovery(backup.parent, source.data)
            try:
                _atomic_receipt_replace(source.path, destination)
            except OSError:
                # The identity/data-verified recovery remains quarantined.
                return
            restored = _read_receipt_file(destination, flush=False)
            if restored.data != source.data:
                return
        elif _identity_at(backup) == prior_identity and not _path_present(destination):
            return


@contextmanager
def _hold_sealed_receipt(path: Path):
    """Read one receipt while denying concurrent write/delete opens on Windows."""

    if os.name != "nt":
        record = _read_receipt_file(path, flush=True)

        def verify_held() -> None:
            current = _read_receipt_file(path, flush=False)
            if current.identity != record.identity or current.data != record.data:
                _receipt_fail()

        yield record, verify_held
        return
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
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    read_file.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value
    handle = invalid
    try:
        handle = create_file(
            str(path),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        if handle == invalid:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ByHandleFileInformation()
        if not get_info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
        native_identity = (
            int(info.dwVolumeSerialNumber),
            (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
            size,
        )
        if (
            info.dwFileAttributes & (0x10 | 0x400)
            or int(info.nNumberOfLinks) != 1
            or size < 0
            or size > _MAX_RECEIPT_BYTES
        ):
            _receipt_fail()
        identity = _identity_at(path)
        if identity is None or identity[2] != size:
            _receipt_fail()
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            requested = min(remaining, 1024 * 1024)
            buffer = ctypes.create_string_buffer(requested)
            count = wintypes.DWORD()
            if not read_file(handle, buffer, requested, ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if count.value <= 0 or count.value > requested:
                _receipt_fail()
            chunks.append(buffer.raw[: count.value])
            remaining -= count.value
        record = _ReceiptFile(path=path, identity=identity, data=b"".join(chunks))

        def verify_held() -> None:
            after = _ByHandleFileInformation()
            if not get_info(handle, ctypes.byref(after)):
                raise EmbeddingLiveError(_RECEIPT_ERROR)
            after_identity = (
                int(after.dwVolumeSerialNumber),
                (int(after.nFileIndexHigh) << 32) | int(after.nFileIndexLow),
                (int(after.nFileSizeHigh) << 32) | int(after.nFileSizeLow),
            )
            if after_identity != native_identity or _identity_at(path) != identity:
                _receipt_fail()

        yield record, verify_held
    except EmbeddingLiveError:
        raise
    except OSError as error:
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error
    finally:
        if handle != invalid:
            close_handle(handle)


class _HeldReceiptLease:
    __slots__ = ("receipt", "_manager", "_verify", "_closed")

    def __init__(self, path: Path) -> None:
        manager = _hold_sealed_receipt(path)
        receipt, verify = manager.__enter__()
        self.receipt = receipt
        self._manager = manager
        self._verify = verify
        self._closed = False

    def verify_held(self) -> None:
        if self._closed:
            _receipt_fail()
        self._verify()

    def close_noexcept(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._manager.__exit__(None, None, None)
        except Exception:
            pass


class _DeferredReceiptSeal:
    """Keep the prior receipt recoverable until the caller's post-seal check."""

    __slots__ = (
        "_temporary",
        "_destination",
        "_backup",
        "_candidate",
        "_prior",
        "_prior_recovery",
        "_installed_identity",
        "_expectation",
        "_final_result",
        "_validated",
        "_lease",
        "_state",
    )

    def __init__(
        self,
        *,
        temporary: Path,
        destination: Path,
        backup: Path,
        candidate: _ReceiptFile,
        prior: _ReceiptFile | None,
        prior_recovery: _ReceiptFile | None,
        installed_identity: tuple[int, int, int],
        expectation: FinalExpectation,
        final_result: object,
        validated: dict[str, object],
    ) -> None:
        self._temporary = temporary
        self._destination = destination
        self._backup = backup
        self._candidate = candidate
        self._prior = prior
        self._prior_recovery = prior_recovery
        self._installed_identity = installed_identity
        self._expectation = expectation
        self._final_result = final_result
        self._validated = validated
        self._lease = None
        self._state = "pending"

    def rollback(self) -> None:
        if self._state == "rolled-back":
            return
        if self._state not in {"pending", "validated"}:
            _receipt_fail()
        if self._lease is not None:
            self._lease.close_noexcept()
            self._lease = None
        prior_identity = None if self._prior is None else self._prior.identity
        _restore_seal_state(
            temporary=self._temporary,
            destination=self._destination,
            backup=self._backup,
            installed_identities=(
                self._candidate.identity,
                self._installed_identity,
            ),
            prior_identity=prior_identity,
            prior_moved=self._prior is not None,
            prior_recovery=self._prior_recovery,
        )
        self._state = "rolled-back"
        if self._prior is None:
            if _path_present(self._destination):
                _receipt_fail()
        else:
            restored = _read_receipt_file(self._destination, flush=False)
            if restored.data != self._prior.data:
                _receipt_fail()

    def commit(self) -> dict[str, object]:
        if self._state != "pending":
            _receipt_fail()
        try:
            lease = _HeldReceiptLease(self._destination)
            reopened = lease.receipt
            self._lease = lease
            if (
                reopened.identity != self._candidate.identity
                or reopened.data != self._candidate.data
            ):
                _receipt_fail()
            validated = _validate_receipt_bytes(
                reopened.data,
                self._expectation,
                self._final_result,
            )
            if _canonical_bytes(validated) != _canonical_bytes(self._validated):
                _receipt_fail()
            self._state = "validated"
            return validated
        except Exception:
            self.rollback()
            raise

    def finalize(self) -> dict[str, object]:
        if self._state != "validated" or self._lease is None:
            _receipt_fail()
        try:
            reopened = self._lease.receipt
            if type(reopened) is not _ReceiptFile:
                _receipt_fail()
            validated = _validate_receipt_bytes(
                reopened.data,
                self._expectation,
                self._final_result,
            )
            if _canonical_bytes(validated) != _canonical_bytes(self._validated):
                _receipt_fail()
            self._lease.verify_held()
            if self._prior is not None:
                checked_backup = _read_receipt_file(self._backup, flush=False)
                if (
                    checked_backup.data != self._prior.data
                    or not _remove_known_receipt(
                        self._backup, checked_backup.identity
                    )
                ):
                    _receipt_fail()
                if self._prior_recovery is not None:
                    _remove_known_receipt(
                        self._prior_recovery.path,
                        self._prior_recovery.identity,
                    )
            self._state = "committed"
            self._lease.close_noexcept()
            self._lease = None
            return validated
        except Exception:
            self.rollback()
            raise


def seal_receipt(
    temporary: Path,
    sealed: Path,
    expectation: FinalExpectation,
    expected_final_result: object,
    quarantine_root: Path,
    *,
    defer_commit: bool = False,
) -> dict[str, object] | _DeferredReceiptSeal:
    """Validate, atomically seal, reopen, and self-validate one receipt."""

    if type(defer_commit) is not bool:
        _receipt_fail()

    temp, destination, quarantine = _receipt_paths(
        temporary,
        sealed,
        quarantine_root,
    )
    candidate = _read_receipt_file(temp, flush=True)
    _validate_receipt_bytes(candidate.data, expectation, expected_final_result)

    prior: _ReceiptFile | None = None
    prior_recovery: _ReceiptFile | None = None
    if _path_present(destination):
        prior = _read_receipt_file(destination, flush=False)
        prior_recovery = _write_prior_recovery(quarantine, prior.data)
    backup = quarantine / (".receipt-prior-" + secrets.token_hex(16) + ".bak")
    if _path_present(backup) or _same_path(backup, temp) or _same_path(backup, destination):
        _receipt_fail()

    prior_moved = False
    installed_identity = candidate.identity
    try:
        # Recheck the pathname immediately before each pathname-based replace.
        if _identity_at(temp) != candidate.identity:
            _receipt_fail()
        if prior is not None:
            if _identity_at(destination) != prior.identity:
                _receipt_fail()
            _atomic_receipt_replace(destination, backup)
            prior_moved = True
            if _identity_at(backup) != prior.identity:
                _receipt_fail()
        elif _path_present(destination):
            _receipt_fail()
        if _identity_at(temp) != candidate.identity:
            _receipt_fail()
        _atomic_receipt_replace(temp, destination)
        observed_identity = _identity_at(destination)
        if observed_identity is not None:
            installed_identity = observed_identity
        reopened = _read_receipt_file(destination, flush=True)
        if reopened.identity != candidate.identity or reopened.data != candidate.data:
            _receipt_fail()
        validated = _validate_receipt_bytes(
            reopened.data,
            expectation,
            expected_final_result,
        )
        transaction = _DeferredReceiptSeal(
            temporary=temp,
            destination=destination,
            backup=backup,
            candidate=candidate,
            prior=prior,
            prior_recovery=prior_recovery,
            installed_identity=installed_identity,
            expectation=expectation,
            final_result=expected_final_result,
            validated=validated,
        )
        if defer_commit:
            return transaction
        transaction.commit()
        return transaction.finalize()
    except EmbeddingLiveError:
        _restore_seal_state(
            temporary=temp,
            destination=destination,
            backup=backup,
            installed_identities=(candidate.identity, installed_identity),
            prior_identity=None if prior is None else prior.identity,
            prior_moved=prior_moved,
            prior_recovery=prior_recovery,
        )
        raise
    except Exception as error:
        _restore_seal_state(
            temporary=temp,
            destination=destination,
            backup=backup,
            installed_identities=(candidate.identity, installed_identity),
            prior_identity=None if prior is None else prior.identity,
            prior_moved=prior_moved,
            prior_recovery=prior_recovery,
        )
        raise EmbeddingLiveError(_RECEIPT_ERROR) from error


__all__ = [
    "build_receipt",
    "EmbeddingLiveError",
    "FinalExpectation",
    "LiveEmbeddingAuthority",
    "run_final_verification_callback",
    "run_fresh_pipeline",
    "seal_receipt",
    "validate_receipt",
]
