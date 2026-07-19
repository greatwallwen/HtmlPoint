"""Strict vector validation and deterministic hybrid ranking primitives."""

from __future__ import annotations

import math
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .model_cache import (
    ModelCacheError,
    VerifiedModelCache,
    validate_verified_generation,
)


RRF_POLICY_ID = "course-studio-rrf-v1"
RRF_K = 60
_SAFE_CARD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class EmbeddingVectorError(ValueError):
    """An embedding vector violated the pinned provider contract."""


class EmbeddingProviderError(RuntimeError):
    """A pinned provider could not be loaded or used without weakening policy."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EmbeddingProviderIdentity:
    provider: str
    provider_version: str
    model_id: str
    model_revision: str
    artifact_repository: str
    artifact_revision: str
    dimension: int
    encoding_policy: str
    model_manifest_digest: str
    cache_digest: str
    model_files: tuple[tuple[str, str, int], ...]
    runtime_digest: str
    wheel_set_digest: str
    generation_digest: str


@dataclass(frozen=True)
class RrfResult:
    card_version_id: str
    score: float
    fts_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True)
class WorkerLimits:
    timeout_seconds: float
    stdout_bytes: int
    stderr_bytes: int
    process_memory_bytes: int
    job_memory_bytes: int
    job_user_time_100ns: int


@dataclass(frozen=True)
class WorkerExecution:
    pid: int
    stdout: bytes
    stderr: bytes
    job_scope: str
    overflow_stream: str | None


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


def _strict_json_loads(raw: bytes) -> object:
    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        raw.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_pairs,
    )


def _validate_worker_response(
    raw: bytes,
    *,
    expected_challenge: str,
    expected_temp_token: str,
    actual_pid: int,
    runtime_root: Path,
    expected_vector_count: int,
) -> tuple[tuple[tuple[float, ...], ...], dict[str, object]]:
    try:
        if len(raw) > 16_000_000:
            raise ValueError("worker response too large")
        payload = _strict_json_loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH") from error
    expected_keys = {
        "schemaVersion",
        "challengeDigest",
        "processId",
        "tempTokenDigest",
        "vectorDigest",
        "vectors",
        "providerOrigins",
        "pythonIsolation",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
    challenge_digest = hashlib.sha256(expected_challenge.encode("ascii")).hexdigest()
    temp_token_digest = hashlib.sha256(expected_temp_token.encode("ascii")).hexdigest()
    raw_vectors = payload["vectors"]
    try:
        vector_bytes = json.dumps(
            raw_vectors,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH") from error
    if (
        type(payload["schemaVersion"]) is not int
        or payload["schemaVersion"] != 1
        or payload["challengeDigest"] != challenge_digest
        or type(payload["processId"]) is not int
        or payload["processId"] != actual_pid
        or payload["processId"] <= 0
        or payload["tempTokenDigest"] != temp_token_digest
        or payload["vectorDigest"] != hashlib.sha256(vector_bytes).hexdigest()
        or not isinstance(raw_vectors, list)
        or len(raw_vectors) != expected_vector_count
        or any(not isinstance(vector, list) for vector in raw_vectors)
    ):
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")

    raw_origins = payload["providerOrigins"]
    if not isinstance(raw_origins, list) or len(raw_origins) != 2:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
    origins: list[dict[str, str]] = []
    seen_distributions: set[str] = set()
    generation_root = runtime_root.parent.resolve(strict=True)
    runtime_resolved = runtime_root.resolve(strict=True)
    for raw_origin in raw_origins:
        if not isinstance(raw_origin, dict) or set(raw_origin) != {
            "distribution",
            "path",
            "sha256",
        }:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
        distribution = raw_origin["distribution"]
        relative = raw_origin["path"]
        claimed_digest = raw_origin["sha256"]
        if (
            distribution not in {"fastembed", "onnxruntime"}
            or distribution in seen_distributions
            or not isinstance(relative, str)
            or "\\" in relative
            or not relative.startswith(f"runtime/{distribution}/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not isinstance(claimed_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", claimed_digest) is None
        ):
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
        candidate = generation_root.joinpath(*relative.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(runtime_resolved)
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError("invalid provider origin")
            actual_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            raise EmbeddingProviderError(
                "EMBEDDING_PROVIDER_OUTPUT_MISMATCH"
            ) from error
        if actual_digest != claimed_digest:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
        seen_distributions.add(distribution)
        origins.append(
            {
                "distribution": distribution,
                "path": relative,
                "sha256": claimed_digest,
            }
        )
    if seen_distributions != {"fastembed", "onnxruntime"}:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")

    isolation = payload["pythonIsolation"]
    if not isinstance(isolation, dict) or set(isolation) != {
        "scope",
        "preImportProbes",
        "postInferenceProbes",
        "evidenceDigest",
    }:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
    expected_probes = {
        surface: "denied" for surface in sorted(_CPYTHON_GUARD_PROBES)
    }
    isolation_core = {
        "scope": "trusted-hash-locked-cpython-runtime",
        "preImportProbes": expected_probes,
        "postInferenceProbes": expected_probes,
    }
    evidence_core = {
        "challengeDigest": challenge_digest,
        "processId": actual_pid,
        "tempTokenDigest": temp_token_digest,
        **isolation_core,
    }
    evidence_digest = hashlib.sha256(
        json.dumps(
            evidence_core,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if isolation != {**isolation_core, "evidenceDigest": evidence_digest}:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
    vectors = tuple(tuple(vector) for vector in raw_vectors)
    evidence = {
        "schemaVersion": 1,
        "challengeDigest": challenge_digest,
        "processId": actual_pid,
        "tempTokenDigest": temp_token_digest,
        "vectorDigest": payload["vectorDigest"],
        "providerOrigins": origins,
        "pythonIsolation": dict(isolation),
    }
    return vectors, evidence


def _run_windows_bounded_child(
    command: list[str],
    *,
    input_bytes: bytes,
    cwd: Path,
    env: dict[str, str],
    limits: WorkerLimits,
) -> WorkerExecution:
    if (
        os.name != "nt"
        or not command
        or not isinstance(input_bytes, bytes)
        or len(input_bytes) > 16_000_000
        or not cwd.is_dir()
        or type(limits.timeout_seconds) not in (int, float)
        or limits.timeout_seconds <= 0
        or type(limits.stdout_bytes) is not int
        or limits.stdout_bytes < 0
        or type(limits.stderr_bytes) is not int
        or limits.stderr_bytes < 0
        or type(limits.process_memory_bytes) is not int
        or limits.process_memory_bytes <= 0
        or type(limits.job_memory_bytes) is not int
        or limits.job_memory_bytes < limits.process_memory_bytes
        or type(limits.job_user_time_100ns) is not int
        or limits.job_user_time_100ns <= 0
    ):
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_POLICY_MISMATCH")

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
    job_object_limit_process_time = 0x00000002
    job_object_limit_active_process = 0x00000008
    job_object_limit_process_memory = 0x00000100
    job_object_limit_job_memory = 0x00000200
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
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED")
    limits_struct = _JobObjectExtendedLimitInformation()
    limits_struct.BasicLimitInformation.LimitFlags = (
        job_object_limit_process_time
        | job_object_limit_active_process
        | job_object_limit_process_memory
        | job_object_limit_job_memory
        | job_object_limit_kill_on_job_close
    )
    limits_struct.BasicLimitInformation.PerProcessUserTimeLimit = (
        limits.job_user_time_100ns
    )
    limits_struct.BasicLimitInformation.ActiveProcessLimit = 1
    limits_struct.ProcessMemoryLimit = limits.process_memory_bytes
    limits_struct.JobMemoryLimit = limits.job_memory_bytes

    process: subprocess.Popen[bytes] | None = None
    streams: list[Any] = []
    readers: list[threading.Thread] = []
    writer: threading.Thread | None = None
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    overflow_stream: list[str] = []
    reader_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []
    job_lock = threading.Lock()
    job_open = True
    timed_out = False
    return_code: int | None = None
    pid = 0
    assigned_to_job = False

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

    def drain(stream: Any, destination: bytearray, maximum: int, label: str) -> None:
        read_size = max(1, min(64 * 1024, maximum + 1))
        try:
            while True:
                block = stream.read(read_size)
                if not block:
                    return
                remaining = maximum + 1 - len(destination)
                if remaining > 0:
                    destination.extend(block[:remaining])
                if len(destination) > maximum:
                    if not overflow_stream:
                        overflow_stream.append(label)
                    terminate_bound_job()
                    return
        except OSError as error:
            reader_errors.append(error)
            terminate_bound_job()

    def write_stdin(stream: Any) -> None:
        try:
            if input_bytes:
                stream.write(input_bytes)
                stream.flush()
        except (BrokenPipeError, OSError) as error:
            writer_errors.append(error)
            terminate_bound_job()
        finally:
            try:
                stream.close()
            except OSError as error:
                writer_errors.append(error)

    try:
        if not set_job_information(
            job_handle,
            job_object_extended_limit_information,
            ctypes.byref(limits_struct),
            ctypes.sizeof(limits_struct),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
            creationflags=create_suspended | create_no_window,
        )
        pid = process.pid
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise OSError("missing child pipe")
        streams.extend((process.stdout, process.stderr))
        raw_process_handle = getattr(process, "_handle", None)
        if raw_process_handle is None:
            raise OSError("missing suspended process handle")
        process_handle = wintypes.HANDLE(int(raw_process_handle))
        if not assign_to_job(job_handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())
        assigned_to_job = True
        readers = [
            threading.Thread(
                target=drain,
                args=(process.stdout, stdout_buffer, limits.stdout_bytes, "stdout"),
                name="embedding-worker-stream-stdout",
            ),
            threading.Thread(
                target=drain,
                args=(process.stderr, stderr_buffer, limits.stderr_bytes, "stderr"),
                name="embedding-worker-stream-stderr",
            ),
        ]
        for reader in readers:
            reader.start()
        writer = threading.Thread(
            target=write_stdin,
            args=(process.stdin,),
            name="embedding-worker-stdin",
        )
        writer.start()
        if resume_process(process_handle) != 0:
            raise OSError("unable to resume assigned process")
        deadline = time.monotonic() + float(limits.timeout_seconds)
        remaining = max(0.001, deadline - time.monotonic())
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_bound_job()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        cleanup_errors.append(error)
    finally:
        if process is not None and process.returncode is None:
            if assigned_to_job:
                terminate_bound_job()
            else:
                try:
                    process.kill()
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(error)
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(error)
        close_bound_job()
        if writer is not None:
            writer.join(timeout=5)
            if writer.is_alive():
                cleanup_errors.append(RuntimeError("stdin writer did not stop"))
        for reader in readers:
            reader.join(timeout=5)
            if reader.is_alive():
                cleanup_errors.append(RuntimeError("stream reader did not stop"))
        for stream in streams:
            try:
                stream.close()
            except OSError as error:
                cleanup_errors.append(error)
        if process is not None:
            raw_process_handle = getattr(process, "_handle", None)
            close_process_handle = getattr(raw_process_handle, "Close", None)
            if callable(close_process_handle):
                try:
                    close_process_handle()
                except OSError as error:
                    cleanup_errors.append(error)

    if overflow_stream:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_LIMIT")
    if timed_out:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_TIMEOUT")
    if reader_errors or writer_errors or cleanup_errors or return_code != 0:
        causes = reader_errors or writer_errors or cleanup_errors
        cause = causes[0] if causes else None
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED") from cause
    return WorkerExecution(
        pid=pid,
        stdout=bytes(stdout_buffer),
        stderr=bytes(stderr_buffer),
        job_scope="windows-job-kill-on-close",
        overflow_stream=None,
    )


def validate_embedding_vector(
    vector: Iterable[float], *, dimension: int = 512
) -> tuple[float, ...]:
    """Return one finite, unit-normalized vector or fail closed."""

    if type(dimension) is not int or dimension <= 0 or dimension > 4096:
        raise EmbeddingVectorError("embedding vector dimension mismatch")
    try:
        values = tuple(islice(iter(vector), dimension + 1))
    except (TypeError, ValueError, OverflowError) as error:
        raise EmbeddingVectorError("embedding vector could not be read") from error
    if len(values) != dimension:
        raise EmbeddingVectorError("embedding vector dimension mismatch")
    converted: list[float] = []
    for value in values:
        if type(value) not in (int, float):
            raise EmbeddingVectorError("embedding vector contains invalid values")
        try:
            converted_value = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise EmbeddingVectorError("embedding vector contains invalid values") from error
        if not math.isfinite(converted_value):
            raise EmbeddingVectorError("embedding vector contains non-finite values")
        converted.append(converted_value)
    floats = tuple(converted)
    norm = math.hypot(*floats)
    if norm == 0.0 or not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-6):
        raise EmbeddingVectorError("embedding vector is not normalized")
    return floats


def course_studio_rrf_v1(
    fts_ranked_ids: tuple[str, ...],
    semantic_ranked_ids: tuple[str, ...],
) -> tuple[RrfResult, ...]:
    """Fuse equal-weight FTS and semantic lanes with RRF k=60."""

    if len(set(fts_ranked_ids)) != len(fts_ranked_ids):
        raise ValueError("duplicate FTS lane member")
    if len(set(semantic_ranked_ids)) != len(semantic_ranked_ids):
        raise ValueError("duplicate semantic lane member")
    if any(
        not isinstance(item, str) or _SAFE_CARD_ID.fullmatch(item) is None
        for item in (*fts_ranked_ids, *semantic_ranked_ids)
    ):
        raise ValueError("ranked card IDs must be non-empty strings")
    fts_ranks = {card_id: rank for rank, card_id in enumerate(fts_ranked_ids, 1)}
    semantic_ranks = {
        card_id: rank for rank, card_id in enumerate(semantic_ranked_ids, 1)
    }
    results = []
    for card_id in set(fts_ranks) | set(semantic_ranks):
        fts_rank = fts_ranks.get(card_id)
        semantic_rank = semantic_ranks.get(card_id)
        score = (0.0 if fts_rank is None else 1.0 / (RRF_K + fts_rank)) + (
            0.0 if semantic_rank is None else 1.0 / (RRF_K + semantic_rank)
        )
        results.append(
            RrfResult(
                card_version_id=card_id,
                score=score,
                fts_rank=fts_rank,
                semantic_rank=semantic_rank,
            )
        )
    return tuple(sorted(results, key=lambda item: (-item.score, item.card_version_id)))


_ISOLATED_WORKER = r'''
import _socket
import asyncio
import hashlib
import importlib
import json
import os
import platform
import re
import socket
import ssl
import subprocess
import sys

try:
    import _winapi
except ImportError:
    _winapi = None

try:
    from asyncio import windows_utils as _asyncio_windows_utils
except ImportError:
    _asyncio_windows_utils = None

_platform_uname = platform.uname()

class GuardDenied(RuntimeError):
    pass

def denied(*args, **kwargs):
    raise GuardDenied("CPython runtime surface disabled")

def audit_guard(event, args):
    if (
        event.startswith("socket.")
        or event.startswith("subprocess.")
        or event == "os.system"
        or event.startswith("os.spawn")
    ):
        denied()

sys.addaudithook(audit_guard)
socket.socket = denied
socket.create_connection = denied
socket.getaddrinfo = denied
_socket.socket = denied
_socket.getaddrinfo = denied
subprocess.Popen = denied
if _asyncio_windows_utils is not None:
    _asyncio_windows_utils.Popen = denied
if _winapi is not None:
    _winapi.CreateProcess = denied

runtime_root, model_path, model_id = sys.argv[1:4]
sys.path.insert(0, runtime_root)
if hasattr(os, "add_dll_directory"):
    _dll_handles = []
    for relative in ("", "onnxruntime", "onnxruntime/capi"):
        candidate = os.path.join(runtime_root, relative)
        if os.path.isdir(candidate):
            _dll_handles.append(os.add_dll_directory(candidate))

def probe_guards():
    installed = {
        "socket.socket": socket.socket,
        "socket.create_connection": socket.create_connection,
        "socket.getaddrinfo": socket.getaddrinfo,
        "_socket.socket": _socket.socket,
        "_socket.getaddrinfo": _socket.getaddrinfo,
        "subprocess.Popen": subprocess.Popen,
        "asyncio.windows_utils.Popen": _asyncio_windows_utils.Popen,
        "_winapi.CreateProcess": _winapi.CreateProcess,
    }
    if any(operation is not denied for operation in installed.values()):
        raise RuntimeError("runtime replaced an installed guard")
    probes = {
        "audit.socket": lambda: sys.audit("socket.__new__"),
        "audit.subprocess": lambda: sys.audit("subprocess.Popen"),
        "socket.socket": lambda: socket.socket(),
        "socket.create_connection": lambda: socket.create_connection(("127.0.0.1", 9)),
        "socket.getaddrinfo": lambda: socket.getaddrinfo("localhost", 80),
        "_socket.socket": lambda: _socket.socket(),
        "_socket.getaddrinfo": lambda: _socket.getaddrinfo("localhost", 80),
        "subprocess.Popen": lambda: subprocess.Popen([sys.executable, "-I", "-c", "pass"]),
        "asyncio.windows_utils.Popen": lambda: _asyncio_windows_utils.Popen(
            [sys.executable, "-I", "-c", "pass"]
        ),
        "_winapi.CreateProcess": lambda: _winapi.CreateProcess(),
    }
    outcomes = {}
    for name, operation in probes.items():
        try:
            resource = operation()
        except GuardDenied:
            outcomes[name] = "denied"
        else:
            close = getattr(resource, "close", None)
            if callable(close):
                close()
            raise RuntimeError("guard probe unexpectedly succeeded")
    return outcomes

def reject_constant(value):
    raise ValueError("invalid numeric constant")

def reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result

request = json.loads(
    sys.stdin.buffer.read(16_000_001).decode("utf-8", errors="strict"),
    parse_constant=reject_constant,
    object_pairs_hook=reject_duplicate_pairs,
)
if (
    not isinstance(request, dict)
    or set(request) != {"schemaVersion", "challenge", "tempToken", "tempPath", "values"}
    or type(request["schemaVersion"]) is not int
    or request["schemaVersion"] != 1
    or not isinstance(request["challenge"], str)
    or re.fullmatch(r"[0-9a-f]{64}", request["challenge"]) is None
    or not isinstance(request["tempToken"], str)
    or re.fullmatch(r"[0-9a-f]{64}", request["tempToken"]) is None
    or not isinstance(request["tempPath"], str)
    or os.path.realpath(request["tempPath"]) != os.path.realpath(os.getcwd())
    or os.path.realpath(os.environ.get("TEMP", "")) != os.path.realpath(os.getcwd())
    or os.path.realpath(os.environ.get("TMP", "")) != os.path.realpath(os.getcwd())
    or not isinstance(request["values"], list)
    or not 1 <= len(request["values"]) <= 1000
    or any(not isinstance(value, str) for value in request["values"])
):
    raise ValueError("invalid request")

pre_import_probes = probe_guards()
from fastembed import TextEmbedding
provider_cache = os.path.join(request["tempPath"], "fastembed-cache")
if os.path.exists(provider_cache):
    raise RuntimeError("provider cache path already exists")
model = TextEmbedding(
    model_name=model_id,
    specific_model_path=model_path,
    cache_dir=provider_cache,
    local_files_only=True,
    providers=["CPUExecutionProvider"],
)
vectors = [
    [float(item) for item in vector]
    for vector in model.embed(request["values"])
]
if (
    os.path.realpath(os.path.dirname(provider_cache))
    != os.path.realpath(request["tempPath"])
    or os.path.islink(provider_cache)
    or not os.path.isdir(provider_cache)
    or os.listdir(provider_cache)
):
    raise RuntimeError("provider cache path is not empty and contained")
os.rmdir(provider_cache)
vector_bytes = json.dumps(
    vectors,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
).encode("utf-8")
provider_origins = []
for distribution_name in ("fastembed", "onnxruntime"):
    module = importlib.import_module(distribution_name)
    origin = os.path.realpath(module.__file__)
    relative = os.path.relpath(origin, runtime_root).replace(os.sep, "/")
    if relative.startswith("../") or relative == "..":
        raise RuntimeError("provider origin outside runtime")
    with open(origin, "rb") as source:
        digest = hashlib.sha256(source.read()).hexdigest()
    provider_origins.append(
        {
            "distribution": distribution_name,
            "path": "runtime/" + relative,
            "sha256": digest,
        }
    )
post_inference_probes = probe_guards()
challenge_digest = hashlib.sha256(request["challenge"].encode("ascii")).hexdigest()
temp_token_digest = hashlib.sha256(request["tempToken"].encode("ascii")).hexdigest()
isolation_core = {
    "scope": "trusted-hash-locked-cpython-runtime",
    "preImportProbes": pre_import_probes,
    "postInferenceProbes": post_inference_probes,
}
evidence_digest = hashlib.sha256(
    json.dumps(
        {
            "challengeDigest": challenge_digest,
            "processId": os.getpid(),
            "tempTokenDigest": temp_token_digest,
            **isolation_core,
        },
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
response = {
    "schemaVersion": 1,
    "challengeDigest": challenge_digest,
    "processId": os.getpid(),
    "tempTokenDigest": temp_token_digest,
    "vectorDigest": hashlib.sha256(vector_bytes).hexdigest(),
    "vectors": vectors,
    "providerOrigins": provider_origins,
    "pythonIsolation": {
        **isolation_core,
        "evidenceDigest": evidence_digest,
    },
}
json.dump(
    response,
    sys.stdout,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
)
'''


def _directory_identity(path: Path) -> tuple[int, int]:
    information = path.stat()
    return information.st_dev, information.st_ino


def _is_directory_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _directory_chain(path: Path) -> tuple[Path, ...]:
    return tuple(reversed((path, *path.parents)))


@contextmanager
def _hold_no_delete_directory_chain(
    path: Path,
    *,
    expected: tuple[tuple[Path, tuple[int, int]], ...] | None = None,
) -> Iterator[tuple[tuple[Path, tuple[int, int]], ...]]:
    """Pin every path component so a checked temp parent cannot be swapped."""

    chain = _directory_chain(path)
    if expected is not None and tuple(item[0] for item in expected) != chain:
        raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID")

    handles: list[tuple[Any, Any]] = []
    snapshot: list[tuple[Path, tuple[int, int]]] = []
    close_failed = False
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            invalid_handle = ctypes.c_void_p(-1).value

        for index, candidate in enumerate(chain):
            if not candidate.is_dir() or _is_directory_reparse_point(candidate):
                raise OSError("isolated temp path contains a reparse point")
            before = _directory_identity(candidate)
            if expected is not None and before != expected[index][1]:
                raise OSError("isolated temp path identity changed")
            if os.name == "nt":
                handle = create_file(
                    str(candidate),
                    0x1 | 0x80,  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES.
                    0x1 | 0x2,  # FILE_SHARE_READ | FILE_SHARE_WRITE
                    None,
                    3,  # OPEN_EXISTING
                    0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
                    None,
                )
                if not handle or int(handle) == invalid_handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append((close_handle, handle))
            after = _directory_identity(candidate)
            if before != after or _is_directory_reparse_point(candidate):
                raise OSError("isolated temp path changed while being pinned")
            snapshot.append((candidate, after))

        yield tuple(snapshot)

        for index, candidate in enumerate(chain):
            if (
                _is_directory_reparse_point(candidate)
                or _directory_identity(candidate) != snapshot[index][1]
            ):
                raise OSError("isolated temp path identity changed")
    except EmbeddingProviderError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID") from error
    finally:
        for close_handle, handle in reversed(handles):
            if not close_handle(handle):
                close_failed = True
        if close_failed and sys.exc_info()[0] is None:
            raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID")


class _IsolatedFastEmbedModel:
    def __init__(
        self,
        *,
        runtime_root: Path,
        model_name: str,
        specific_model_path: str,
        local_files_only: bool,
        providers: list[str],
        temp_parent: Path,
        worker_launcher: Callable[..., WorkerExecution] | None = None,
    ) -> None:
        if local_files_only is not True or providers != ["CPUExecutionProvider"]:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_POLICY_MISMATCH")
        try:
            resolved_runtime = runtime_root.resolve(strict=True)
            resolved_temp_parent = temp_parent.resolve(strict=True)
            if (
                runtime_root.is_symlink()
                or not resolved_runtime.is_dir()
                or temp_parent.is_symlink()
                or _is_directory_reparse_point(temp_parent)
                or not resolved_temp_parent.is_dir()
            ):
                raise ValueError("invalid isolated path")
        except (OSError, ValueError) as error:
            raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID") from error
        self._runtime_root = resolved_runtime
        self._model_name = model_name
        self._model_path = specific_model_path
        self._temp_parent = resolved_temp_parent
        with _hold_no_delete_directory_chain(self._temp_parent) as temp_parent_chain:
            self._temp_parent_chain = temp_parent_chain
        self._worker_launcher = worker_launcher or _run_windows_bounded_child
        self._limits = WorkerLimits(
            timeout_seconds=120.0,
            stdout_bytes=16_000_000,
            stderr_bytes=65_536,
            process_memory_bytes=2 * 1024 * 1024 * 1024,
            job_memory_bytes=3 * 1024 * 1024 * 1024,
            job_user_time_100ns=1_200_000_000,
        )

    def embed_with_evidence(
        self,
        values: list[str],
    ) -> tuple[tuple[tuple[float, ...], ...], dict[str, object]]:
        with _hold_no_delete_directory_chain(
            self._temp_parent,
            expected=self._temp_parent_chain,
        ):
            return self._embed_with_held_temp(values)

    def _embed_with_held_temp(
        self,
        values: list[str],
    ) -> tuple[tuple[tuple[float, ...], ...], dict[str, object]]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}
        }
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        challenge = secrets.token_hex(32)
        temp_token = secrets.token_hex(32)
        run_root: Path | None = None
        run_identity: tuple[int, int] | None = None
        try:
            run_root = Path(
                tempfile.mkdtemp(prefix="embedding-run-", dir=self._temp_parent)
            ).resolve(strict=True)
            run_stat = run_root.stat()
            run_identity = (run_stat.st_dev, run_stat.st_ino)
            if run_root.parent != self._temp_parent or run_root.is_symlink():
                raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID")
            environment.update({"TEMP": str(run_root), "TMP": str(run_root)})
            request = {
                "schemaVersion": 1,
                "challenge": challenge,
                "tempToken": temp_token,
                "tempPath": str(run_root),
                "values": values,
            }
            request_bytes = json.dumps(
                request,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(request_bytes) > 16_000_000:
                raise EmbeddingProviderError("EMBEDDING_INPUT_INVALID")
            execution = self._worker_launcher(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _ISOLATED_WORKER,
                    str(self._runtime_root),
                    self._model_path,
                    self._model_name,
                ],
                input_bytes=request_bytes,
                cwd=run_root,
                env=environment,
                limits=self._limits,
            )
            if not isinstance(execution, WorkerExecution):
                raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
            if execution.overflow_stream is not None:
                raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_LIMIT")
            if execution.stderr:
                raise EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED")
            post_stat = run_root.stat()
            if (post_stat.st_dev, post_stat.st_ino) != run_identity:
                raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID")
            vectors, evidence = _validate_worker_response(
                execution.stdout,
                expected_challenge=challenge,
                expected_temp_token=temp_token,
                actual_pid=execution.pid,
                runtime_root=self._runtime_root,
                expected_vector_count=len(values),
            )
            if tuple(run_root.iterdir()):
                raise EmbeddingProviderError("EMBEDDING_PROVIDER_TEMP_NOT_EMPTY")
            evidence["jobScope"] = execution.job_scope
            return vectors, evidence
        except EmbeddingProviderError:
            raise
        except (OSError, TypeError, ValueError, RecursionError) as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED") from error
        finally:
            if run_root is not None and run_root.exists():
                try:
                    current = run_root.stat()
                    if (
                        run_identity is None
                        or (current.st_dev, current.st_ino) != run_identity
                        or run_root.is_symlink()
                        or run_root.parent != self._temp_parent
                    ):
                        raise OSError("isolated temp identity changed")
                    shutil.rmtree(run_root)
                except OSError as error:
                    raise EmbeddingProviderError(
                        "EMBEDDING_PROVIDER_TEMP_CLEANUP_FAILED"
                    ) from error

    def embed(self, values: list[str]) -> tuple[tuple[float, ...], ...]:
        vectors, _evidence = self.embed_with_evidence(values)
        return vectors


def _isolated_package_version(runtime_root: Path, package_name: str) -> str:
    distributions = tuple(
        distribution
        for distribution in metadata.distributions(path=[str(runtime_root)])
        if (distribution.metadata.get("Name") or "").replace("_", "-").casefold()
        == package_name.replace("_", "-").casefold()
    )
    if len(distributions) != 1:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_IMPORT_FAILED")
    return distributions[0].version


def _normalize_input(value: str) -> str:
    if not isinstance(value, str):
        raise EmbeddingProviderError("EMBEDDING_INPUT_INVALID")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized.encode("utf-8")) > 100_000:
        raise EmbeddingProviderError("EMBEDDING_INPUT_INVALID")
    return normalized


class FastEmbedProvider:
    """The sole provider wrapper: exact package, verified local path, no fallback."""

    def __init__(
        self,
        verified_cache: VerifiedModelCache,
        *,
        model_factory: Callable[..., object] | None = None,
        package_version_getter: Callable[[str], str] = metadata.version,
        isolated_temp_parent: Path | None = None,
    ) -> None:
        if not isinstance(verified_cache, VerifiedModelCache):
            raise EmbeddingProviderError("EMBEDDING_CACHE_NOT_VERIFIED")
        if (
            verified_cache.generation_root is None
            or verified_cache.runtime_root is None
            or verified_cache.runtime_digest is None
            or verified_cache.wheel_set_digest is None
            or verified_cache.generation_digest is None
        ):
            raise EmbeddingProviderError("EMBEDDING_RUNTIME_NOT_VERIFIED")
        try:
            validate_verified_generation(verified_cache)
        except ModelCacheError as error:
            raise EmbeddingProviderError("EMBEDDING_GENERATION_PATH_INVALID") from error
        manifest = verified_cache.manifest
        try:
            installed_version = (
                package_version_getter("fastembed")
                if model_factory is not None
                else _isolated_package_version(verified_cache.runtime_root, "fastembed")
            )
        except Exception as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_IMPORT_FAILED") from error
        if installed_version != "0.8.0" or manifest.package.version != "0.8.0":
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_VERSION_MISMATCH")
        factory = model_factory
        if factory is None and isolated_temp_parent is None:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_TEMP_ROOT_REQUIRED")
        try:
            self._model = (
                _IsolatedFastEmbedModel(
                    runtime_root=verified_cache.runtime_root,
                    model_name=manifest.model.id,
                    specific_model_path=str(verified_cache.specific_model_path),
                    local_files_only=True,
                    providers=["CPUExecutionProvider"],
                    temp_parent=isolated_temp_parent,
                )
                if factory is None
                else factory(
                    model_name=manifest.model.id,
                    specific_model_path=str(verified_cache.specific_model_path),
                    local_files_only=True,
                    providers=["CPUExecutionProvider"],
                )
            )
        except EmbeddingProviderError:
            raise
        except Exception as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_LOAD_FAILED") from error
        self._dimension = manifest.model.dimension
        self.identity = EmbeddingProviderIdentity(
            provider="fastembed",
            provider_version="0.8.0",
            model_id=manifest.model.id,
            model_revision=manifest.model.revision,
            artifact_repository=manifest.model.artifact_repository,
            artifact_revision=manifest.model.artifact_revision,
            dimension=manifest.model.dimension,
            encoding_policy=manifest.model.encoding_policy,
            model_manifest_digest=manifest.aggregate_digest,
            cache_digest=verified_cache.cache_digest,
            model_files=tuple(
                (member.path, member.sha256, member.size)
                for member in sorted(manifest.files, key=lambda item: item.path)
            ),
            runtime_digest=verified_cache.runtime_digest,
            wheel_set_digest=verified_cache.wheel_set_digest,
            generation_digest=verified_cache.generation_digest,
        )

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed_documents((_normalize_input(text),))[0]

    def embed_documents(self, texts: Iterable[str]) -> tuple[tuple[float, ...], ...]:
        normalized = self._normalize_documents(texts)
        try:
            raw_vectors = tuple(islice(iter(self._model.embed(normalized)), len(normalized) + 1))  # type: ignore[attr-defined]
        except Exception as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED") from error
        return self._validate_vectors(raw_vectors, expected_count=len(normalized))

    def embed_query_with_evidence(
        self,
        text: str,
    ) -> tuple[tuple[float, ...], dict[str, object]]:
        vectors, evidence = self.embed_documents_with_evidence((text,))
        return vectors[0], evidence

    def embed_documents_with_evidence(
        self,
        texts: Iterable[str],
    ) -> tuple[tuple[tuple[float, ...], ...], dict[str, object]]:
        normalized = self._normalize_documents(texts)
        try:
            raw_vectors, raw_evidence = self._model.embed_with_evidence(normalized)  # type: ignore[attr-defined]
            bounded_vectors = tuple(
                islice(iter(raw_vectors), len(normalized) + 1)
            )
        except EmbeddingProviderError:
            raise
        except Exception as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_INFERENCE_FAILED") from error
        vectors = self._validate_vectors(
            bounded_vectors,
            expected_count=len(normalized),
        )
        evidence = self._copy_finite_evidence(raw_evidence)
        return vectors, evidence

    @staticmethod
    def _normalize_documents(texts: Iterable[str]) -> list[str]:
        try:
            bounded = tuple(islice(iter(texts), 1001))
        except (TypeError, ValueError, OverflowError) as error:
            raise EmbeddingProviderError("EMBEDDING_INPUT_INVALID") from error
        if not bounded or len(bounded) > 1000:
            raise EmbeddingProviderError("EMBEDDING_INPUT_INVALID")
        return [_normalize_input(value) for value in bounded]

    def _validate_vectors(
        self,
        raw_vectors: tuple[object, ...],
        *,
        expected_count: int,
    ) -> tuple[tuple[float, ...], ...]:
        if len(raw_vectors) != expected_count:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
        try:
            return tuple(
                validate_embedding_vector(vector, dimension=self._dimension)
                for vector in raw_vectors
            )
        except EmbeddingVectorError as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_VECTOR_INVALID") from error

    @staticmethod
    def _copy_finite_evidence(value: object) -> dict[str, object]:
        if type(value) is not dict:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > 1_000_000:
                raise ValueError("embedding evidence exceeds the bounded contract")
            copied = json.loads(encoded)
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as error:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH") from error
        if type(copied) is not dict:
            raise EmbeddingProviderError("EMBEDDING_PROVIDER_OUTPUT_MISMATCH")
        return copied


__all__ = [
    "EmbeddingVectorError",
    "EmbeddingProviderError",
    "EmbeddingProviderIdentity",
    "FastEmbedProvider",
    "RRF_K",
    "RRF_POLICY_ID",
    "RrfResult",
    "course_studio_rrf_v1",
    "validate_embedding_vector",
]
