from __future__ import annotations

import hashlib
import http.client
import importlib
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    details: str


@dataclass(frozen=True)
class EmbeddingManifestContext:
    phase: str
    manifest_digest: str
    model_cache: object
    manifest: object
    authority: object | None = None


@dataclass(frozen=True)
class EmbeddingFinalPhaseArtifacts:
    temporary_receipt: Path
    final_result: object
    expectation: object
    first_pipeline: Mapping[str, object]
    replay_pipeline: Mapping[str, object]


EXPECTED_TOKENS = {
    "--color-page": "#f7f8fa",
    "--color-surface": "#ffffff",
    "--color-surface-muted": "#f4f6f8",
    "--color-text": "#172033",
    "--color-brand": "#1463ff",
}

WORKFLOW_LABELS = (
    "导入资料",
    "生成课程",
    "编辑验证",
    "双屏授课",
)

SOURCE_IMAGE = Path("docs/product/assets/course-studio-light-reference.png")
SOURCE_IMAGE_SHA256 = (
    "36A5A9E54C863A326B98CA7082ACF16293EA423D442495E0317D85E91121B3B3"
)

REQUIRED_DOMAIN_FILES = (
    Path("platform/web/src/domain/course.ts"),
    Path("platform/web/src/domain/course-schema.ts"),
    Path("platform/web/src/domain/teaching.ts"),
)

DESIGN_QA_REPORT = Path("platform/web/design-qa.md")
ACCEPTANCE_RECEIPT = Path("platform/web/evidence/acceptance-receipt.json")
KNOWLEDGE_DEMO_RECEIPT = Path(
    "platform/helper/evidence/reference-demo-receipt.json"
)
KNOWLEDGE_DEMO_RECEIPT_SHA256 = (
    "80DA851DF173F14972D0BF07F76FC334B0D735257CABCCA4403FD5DD568385C1"
)
HELPER_DESIGN_QA_REPORT = Path("platform/helper/design-qa.md")
EMBEDDING_MODEL_MANIFEST = Path(
    "platform/helper/model-manifests/bge-small-zh-v1.5.json"
)
EMBEDDING_MODEL_CACHE = Path("platform/helper/.embedding-model")
EMBEDDING_BOOTSTRAP_CANDIDATE = Path(
    "platform/helper/.embedding-bootstrap/bge-small-zh-v1.5-candidate.json"
)
EMBEDDING_QUARANTINE_ROOT = Path("platform/helper/.embedding-quarantine")
EMBEDDING_MODEL_RECEIPT = Path(
    "platform/helper/evidence/embedding-model-live.json"
)
NETWORK_VISUAL_QUARANTINE_ROOT = Path("platform/helper/.network-visual-live")
NETWORK_VISUAL_RECEIPT = Path(
    "platform/helper/evidence/network-visual-acquisition-live.json"
)
COURSE_COMPOSITION_RECEIPT = Path(
    "platform/web/evidence/course-composition-browser-e2e.json"
)
COURSE_BROWSER_POLICY = Path("platform/web/e2e/browser-policy.json")
COURSE_COMPOSITION_SCREENSHOTS = (
    Path("platform/web/output/playwright/published-editor.png"),
    Path("platform/web/output/playwright/stage.png"),
    Path("platform/web/output/playwright/presenter.png"),
)
DESIGN_QA_EVIDENCE = (
    Path("platform/web/evidence/design-qa-edit.png"),
    Path("platform/web/evidence/design-qa-comparison.png"),
    ACCEPTANCE_RECEIPT,
)
ACCEPTANCE_HASHED_ENTRIES = {
    "reference": SOURCE_IMAGE,
    "implementation": Path("platform/web/evidence/design-qa-edit.png"),
    "comparison": Path("platform/web/evidence/design-qa-comparison.png"),
    "stage": Path("platform/web/evidence/teaching-stage.png"),
    "presenter": Path("platform/web/evidence/teaching-presenter.png"),
    "fixture": Path("platform/web/evidence/browser-flow-fixture.md"),
}

_GRADIENT_RE = re.compile(r"(?:linear|radial|conic)-gradient", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_SURFACE_DECLARATION_RE = re.compile(
    r"(?<![\w-])"
    r"(?P<property>--color-(?:page|surface(?:-muted)?)|background(?:-color)?|fill)"
    r"\s*:\s*(?P<value>[^;{}]+)",
    re.IGNORECASE,
)
_DARK_HEX_RE = re.compile(
    r"(?<![0-9a-f])#(?:000000|000|111827|0f172a|020617)\b",
    re.IGNORECASE,
)
_RGB_RE = re.compile(r"rgba?\((?P<channels>[^)]*)\)", re.IGNORECASE)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_FORBIDDEN_RGB = {
    (0, 0, 0),
    (17, 24, 39),
    (15, 23, 42),
    (2, 6, 23),
}
_PROTECTED_ROOTS = ("course_aiproduct", "dataset", "references")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_DEMO_SOURCE_PATHS = (
    "AI.pptx",
    "AIGC实操 -数据分析.md",
    "AIGC实操-Prompt工程.md",
    "dataset/1-train.csv",
    "AIExcelData/ex-17-RFM.xlsx",
)
_DEMO_INVENTORY_ROOTS = ("dataset", "AIExcelData")
_DEMO_MARKDOWN_UNITS = frozenset(("自行车共享需求", "Prompt概论", "正确提问"))
_DEMO_DATASETS = frozenset(
    ("dataset/1-train.csv", "AIExcelData/ex-17-RFM.xlsx")
)
_DEMO_PARSER_VERSIONS = {
    "AI.pptx": "python-pptx@1.0.2",
    "AIGC实操 -数据分析.md": "markdown-it-py@2.2.0",
    "AIGC实操-Prompt工程.md": "markdown-it-py@2.2.0",
    "dataset/1-train.csv": "course-helper/dataset-profiler@1",
    "AIExcelData/ex-17-RFM.xlsx": "course-helper/dataset-profiler@1",
}
_DEMO_QUARANTINED_EXTENSIONS = frozenset((".pth", ".pt", ".tmp", ".whl"))
_DEMO_OBJECT_DIGEST_KEYS = frozenset(
    ("sources", "chunks", "visuals", "datasets", "cards", "evidence")
)
_DEMO_CHECK_CODES = frozenset(
    (
        "deep-read-allowlist",
        "inventory-integrity-scope",
        "parser-digest-recomputation",
        "known-phrase-retrieval",
        "forbidden-source-write",
    )
)
_DEMO_RETRIEVAL_QUERIES = ("人工智能", "自行车共享需求", "正确提问")
_DEMO_RECEIPT_KEYS = frozenset(
    (
        "schema_version",
        "command_version",
        "status",
        "root_id",
        "manifest_digest",
        "deep_read_source_count",
        "hash_verified_source_count",
        "inventory_root_count",
        "inventory_integrity_scope",
        "inventory_item_count",
        "quarantined_extension_counts",
        "source_integrity",
        "inventory_integrity",
        "pptx_slide_chunks",
        "pptx_chunks_with_notes",
        "markdown_units",
        "profiled_datasets",
        "parser_versions",
        "object_digests",
        "checks",
        "published_card_count",
        "review_decision_count",
        "retrievals",
        "new_source_versions",
        "new_card_count",
        "new_evidence_count",
        "duplicate_card_count",
        "forbidden_source_writes",
        "idempotence",
    )
)


def _read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _ReceiptValidationError(ValueError):
    pass


class EmbeddingLiveFailure(RuntimeError):
    """Sanitized live-producer failure with one stable public exit code."""

    def __init__(self, symbol: str, exit_code: int) -> None:
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", symbol) is None:
            raise ValueError("invalid embedding live failure symbol")
        if exit_code not in {3, 4, 5, 6}:
            raise ValueError("invalid embedding live failure exit code")
        self.symbol = symbol
        self.exit_code = exit_code
        super().__init__(symbol)


def _embedding_transport_failure(symbol: str) -> EmbeddingLiveFailure:
    return EmbeddingLiveFailure(symbol, 4)


class _EmbeddingTransportReason(RuntimeError):
    _ALLOWED = {"dns", "connect", "tls", "http-policy", "unknown"}

    def __init__(self, reason: str) -> None:
        if reason not in self._ALLOWED:
            reason = "unknown"
        self.reason = reason
        super().__init__(reason)


@contextmanager
def _bound_embedding_file(
    approved_root: Path,
    file_path: Path,
    *,
    max_bytes: int,
) -> Iterator[bytes]:
    """Read one no-follow file while ancestor rename/delete remains denied."""

    approved = approved_root.absolute()
    target = file_path.absolute()
    try:
        relative = target.parent.relative_to(approved)
    except ValueError as error:
        raise OSError("uncontained file") from error
    directories = [approved]
    current = approved
    for part in relative.parts:
        current = current / part
        directories.append(current)
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
        handles: list[object] = []
        volume: int | None = None
        try:
            for directory in directories:
                handle = create_file(
                    str(directory),
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
                    raise OSError("reparse directory")
                if volume is None:
                    volume = int(info.dwVolumeSerialNumber)
                elif volume != int(info.dwVolumeSerialNumber):
                    raise OSError("volume changed")
            file_handle = create_file(
                str(target),
                0x80000000,
                0x00000001,
                None,
                3,
                0x00200000 | 0x08000000,
                None,
            )
            if file_handle == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(file_handle)
            file_info = _ByHandleFileInformation()
            if not get_info(file_handle, ctypes.byref(file_info)):
                raise ctypes.WinError(ctypes.get_last_error())
            size = (int(file_info.nFileSizeHigh) << 32) | int(file_info.nFileSizeLow)
            if (
                file_info.dwFileAttributes & (0x10 | 0x400)
                or file_info.nNumberOfLinks != 1
                or int(file_info.dwVolumeSerialNumber) != volume
                or size > max_bytes
            ):
                raise OSError("invalid bound file")
            chunks: list[bytes] = []
            remaining = size
            while remaining:
                requested = min(64 * 1024, remaining)
                buffer = ctypes.create_string_buffer(requested)
                read = wintypes.DWORD()
                if not read_file(
                    file_handle,
                    buffer,
                    requested,
                    ctypes.byref(read),
                    None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if read.value == 0:
                    raise OSError("short bound read")
                chunks.append(buffer.raw[: read.value])
                remaining -= read.value
            yield b"".join(chunks)
        finally:
            for handle in reversed(handles):
                close_handle(handle)
        return
    descriptors: list[int] = []
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        for directory in directories:
            descriptor = os.open(directory, directory_flags)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("not a directory")
            descriptors.append(descriptor)
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_bytes:
            raise OSError("invalid bound file")
        chunks = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        yield b"".join(chunks)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _strict_embedding_https_url(value: str) -> object:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(character) < 0x21 for character in value)
    ):
        raise ValueError("invalid URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("invalid HTTPS URL")
    return parsed


def _public_embedding_addresses(
    host: str,
    port: int,
    *,
    resolver: Callable[..., object],
) -> tuple[tuple[int, str], ...]:
    records = resolver(
        host,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    if not isinstance(records, (list, tuple)) or not records:
        raise ValueError("DNS resolution failed")
    addresses: list[tuple[int, str]] = []
    for record in records:
        if not isinstance(record, tuple) or len(record) != 5:
            raise ValueError("invalid DNS record")
        family, socktype, protocol, _canonical, sockaddr = record
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or socktype != socket.SOCK_STREAM
            or protocol not in {0, socket.IPPROTO_TCP}
            or not isinstance(sockaddr, tuple)
            or not sockaddr
            or not isinstance(sockaddr[0], str)
        ):
            raise ValueError("invalid DNS record")
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise ValueError("non-public DNS address")
        item = (family, str(address))
        if item not in addresses:
            addresses.append(item)
    if not addresses:
        raise ValueError("DNS resolution failed")
    return tuple(addresses)


@contextmanager
def _open_embedding_https_response(
    url: str,
    addresses: tuple[tuple[int, str], ...],
) -> Iterator[http.client.HTTPResponse]:
    parsed = _strict_embedding_https_url(url)
    host = parsed.hostname
    if not isinstance(host, str):
        raise OSError("missing host")
    raw_socket: socket.socket | None = None
    tls_socket: ssl.SSLSocket | None = None
    last_error: OSError | None = None
    for family, address in addresses:
        candidate: socket.socket | None = None
        try:
            candidate = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            candidate.settimeout(20.0)
            destination: tuple[object, ...]
            if family == socket.AF_INET6:
                destination = (address, 443, 0, 0)
            else:
                destination = (address, 443)
            candidate.connect(destination)
            raw_socket = candidate
            break
        except OSError as error:
            last_error = error
            if candidate is not None:
                candidate.close()
    if raw_socket is None:
        raise _EmbeddingTransportReason("connect") from last_error
    response: http.client.HTTPResponse | None = None
    try:
        try:
            tls_socket = ssl.create_default_context().wrap_socket(
                raw_socket,
                server_hostname=host,
            )
        except ssl.SSLError as error:
            raise _EmbeddingTransportReason("tls") from error
        raw_socket = None
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Accept: application/json, application/octet-stream\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "User-Agent: course-helper-embedding-bootstrap/1\r\n\r\n"
        ).encode("ascii")
        try:
            tls_socket.sendall(request)
            response = http.client.HTTPResponse(tls_socket)
            response.begin()
        except ssl.SSLError as error:
            raise _EmbeddingTransportReason("tls") from error
        except (OSError, http.client.HTTPException) as error:
            raise _EmbeddingTransportReason("http-policy") from error
        yield response
    finally:
        if response is not None:
            response.close()
        if tls_socket is not None:
            tls_socket.close()
        if raw_socket is not None:
            raw_socket.close()


def _embedding_https_fetch(
    url: str,
    *,
    url_policy: Callable[[str, int], bool],
    max_bytes: int,
    failure_symbol: str,
    reason_symbols: Mapping[str, str] | None = None,
    expected_size: int | None = None,
    resolver: Callable[..., object] | None = None,
    opener: Callable[[str, tuple[tuple[int, str], ...]], object] | None = None,
) -> bytes:
    """Fetch one pinned HTTPS resource without proxies or a second DNS lookup."""

    if (
        type(max_bytes) is not int
        or max_bytes <= 0
        or max_bytes > 128_000_000
        or (
            expected_size is not None
            and (type(expected_size) is not int or expected_size <= 0 or expected_size > max_bytes)
        )
    ):
        raise _embedding_transport_failure(failure_symbol)
    resolve = socket.getaddrinfo if resolver is None else resolver
    open_response = _open_embedding_https_response if opener is None else opener

    def fail(reason: str, error: BaseException | None = None) -> None:
        symbol = (
            reason_symbols.get(reason, failure_symbol)
            if reason_symbols is not None
            else failure_symbol
        )
        if error is None:
            raise _embedding_transport_failure(symbol)
        raise _embedding_transport_failure(symbol) from error

    current = url
    for redirect_depth in range(3):
        try:
            parsed = _strict_embedding_https_url(current)
            if url_policy(current, redirect_depth) is not True:
                raise _EmbeddingTransportReason("http-policy")
        except _EmbeddingTransportReason as error:
            fail(error.reason, error)
        except Exception as error:
            fail("http-policy", error)
        try:
            addresses = _public_embedding_addresses(
                parsed.hostname,
                443,
                resolver=resolve,
            )
        except Exception as error:
            fail("dns", error)
        try:
            response_context = open_response(current, addresses)
            with response_context as response:
                status = getattr(response, "status", None)
                if status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if redirect_depth >= 2 or not isinstance(location, str):
                        raise _EmbeddingTransportReason("http-policy")
                    current = urljoin(current, location)
                    continue
                if status != 200:
                    raise _EmbeddingTransportReason("http-policy")
                encoding = response.getheader("Content-Encoding")
                if encoding not in (None, "", "identity"):
                    raise _EmbeddingTransportReason("http-policy")
                content_length = response.getheader("Content-Length")
                if content_length is not None:
                    if not content_length.isascii() or not content_length.isdigit():
                        raise _EmbeddingTransportReason("http-policy")
                    declared = int(content_length)
                    if declared > max_bytes or (
                        expected_size is not None and declared != expected_size
                    ):
                        raise _EmbeddingTransportReason("http-policy")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, max_bytes - total + 1))
                    if not isinstance(chunk, bytes):
                        raise _EmbeddingTransportReason("http-policy")
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise _EmbeddingTransportReason("http-policy")
                    chunks.append(chunk)
                if expected_size is not None and total != expected_size:
                    raise _EmbeddingTransportReason("http-policy")
                return b"".join(chunks)
        except EmbeddingLiveFailure:
            raise
        except _EmbeddingTransportReason as error:
            fail(error.reason, error)
        except ssl.SSLError as error:
            fail("tls", error)
        except socket.gaierror as error:
            fail("dns", error)
        except (TimeoutError, ConnectionError, OSError) as error:
            fail("connect", error)
        except Exception as error:
            fail("unknown", error)
    fail("http-policy")
    raise AssertionError("unreachable")


def _invalid_receipt(code: str) -> None:
    raise _ReceiptValidationError(code)


def _exact_mapping(
    value: object,
    expected_keys: frozenset[str],
    code: str,
) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != expected_keys:
        _invalid_receipt(code)
    return value


def _is_count(value: object, *, positive: bool = False) -> bool:
    return type(value) is int and value >= (1 if positive else 0)


def _is_exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _iter_json_strings(value: object) -> Sequence[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            strings.extend(_iter_json_strings(key))
            strings.extend(_iter_json_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_iter_json_strings(item))
    return strings


def _contains_secret_path(value: object) -> bool:
    for text in _iter_json_strings(value):
        normalized = text.replace("\\", "/")
        if (
            re.search(r"(?i)\b[a-z]:/", normalized)
            or normalized.startswith("/")
            or normalized.startswith("//")
            or "file://" in normalized.casefold()
            or "course_aiproduct" in normalized.casefold()
        ):
            return True
    return False


def _validate_demo_source_integrity(value: object) -> None:
    if not isinstance(value, list) or len(value) != len(_DEMO_SOURCE_PATHS):
        _invalid_receipt("source-integrity")
    source_keys = frozenset(
        (
            "root_id",
            "relative_path",
            "before_metadata",
            "before_sha256",
            "after_metadata",
            "after_sha256",
        )
    )
    metadata_keys = frozenset(("byte_size", "modified_ns"))
    paths: list[str] = []
    for item in value:
        source = _exact_mapping(item, source_keys, "source-structure")
        if source.get("root_id") != "reference-demo":
            _invalid_receipt("source-root")
        path = source.get("relative_path")
        if not isinstance(path, str):
            _invalid_receipt("source-path")
        paths.append(path)
        before_metadata = _exact_mapping(
            source.get("before_metadata"), metadata_keys, "source-metadata"
        )
        after_metadata = _exact_mapping(
            source.get("after_metadata"), metadata_keys, "source-metadata"
        )
        if (
            not _is_count(before_metadata.get("byte_size"))
            or not _is_count(before_metadata.get("modified_ns"))
            or not _is_count(after_metadata.get("byte_size"))
            or not _is_count(after_metadata.get("modified_ns"))
            or before_metadata != after_metadata
        ):
            _invalid_receipt("source-metadata-mismatch")
        before_sha256 = source.get("before_sha256")
        after_sha256 = source.get("after_sha256")
        if (
            not _is_sha256(before_sha256)
            or not _is_sha256(after_sha256)
            or before_sha256 != after_sha256
        ):
            _invalid_receipt("source-hash-mismatch")
    if tuple(paths) != _DEMO_SOURCE_PATHS:
        _invalid_receipt("source-allowlist")


def _validate_demo_inventory(value: object, expected_total: object) -> None:
    if not isinstance(value, list) or len(value) != len(_DEMO_INVENTORY_ROOTS):
        _invalid_receipt("inventory-integrity")
    keys = frozenset(
        (
            "root_id",
            "relative_path",
            "integrity_scope",
            "before_item_count",
            "before_metadata_digest",
            "after_item_count",
            "after_metadata_digest",
            "changed_item_count",
        )
    )
    paths: list[str] = []
    total = 0
    for item in value:
        inventory = _exact_mapping(item, keys, "inventory-structure")
        if (
            inventory.get("root_id") != "reference-demo"
            or inventory.get("integrity_scope") != "metadata-only"
        ):
            _invalid_receipt("inventory-scope")
        path = inventory.get("relative_path")
        if not isinstance(path, str):
            _invalid_receipt("inventory-path")
        paths.append(path)
        before_count = inventory.get("before_item_count")
        after_count = inventory.get("after_item_count")
        before_digest = inventory.get("before_metadata_digest")
        after_digest = inventory.get("after_metadata_digest")
        if (
            not _is_count(before_count)
            or not _is_count(after_count)
            or before_count != after_count
            or not _is_sha256(before_digest)
            or before_digest != after_digest
            or not _is_exact_int(inventory.get("changed_item_count"), 0)
        ):
            _invalid_receipt("inventory-mismatch")
        total += before_count
    if tuple(paths) != _DEMO_INVENTORY_ROOTS:
        _invalid_receipt("inventory-roots")
    if not _is_count(expected_total) or expected_total != total:
        _invalid_receipt("inventory-total")


def _validate_demo_checks(value: object) -> None:
    if not isinstance(value, list) or not value:
        _invalid_receipt("checks")
    keys = frozenset(("code", "status", "message", "details"))
    codes: list[str] = []
    for item in value:
        check = _exact_mapping(item, keys, "check-structure")
        code = check.get("code")
        status = check.get("status")
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(status, str)
            or status not in {"passed", "warning", "skipped"}
            or not isinstance(check.get("message"), str)
            or not check.get("message")
            or not isinstance(check.get("details"), dict)
        ):
            _invalid_receipt("check-status")
        codes.append(code)
    if frozenset(codes) != _DEMO_CHECK_CODES or len(codes) != len(set(codes)):
        _invalid_receipt("check-codes")


def _validate_demo_retrievals(value: object) -> None:
    if not isinstance(value, list) or len(value) != len(_DEMO_RETRIEVAL_QUERIES):
        _invalid_receipt("retrievals")
    keys = frozenset(
        (
            "query",
            "query_digest",
            "hit_count",
            "hit_version_ids",
            "evidence_id",
            "evidence_status",
        )
    )
    queries: list[str] = []
    for item in value:
        retrieval = _exact_mapping(item, keys, "retrieval-structure")
        query = retrieval.get("query")
        hit_ids = retrieval.get("hit_version_ids")
        evidence_status = retrieval.get("evidence_status")
        if (
            not isinstance(query, str)
            or not query
            or not _is_sha256(retrieval.get("query_digest"))
            or not _is_count(retrieval.get("hit_count"), positive=True)
            or not isinstance(hit_ids, list)
            or not hit_ids
            or any(not isinstance(hit, str) or not hit for hit in hit_ids)
            or retrieval.get("hit_count") != len(hit_ids)
            or len(hit_ids) != len(set(hit_ids))
            or not isinstance(retrieval.get("evidence_id"), str)
            or not retrieval.get("evidence_id")
            or not isinstance(evidence_status, str)
            or evidence_status != "degraded"
        ):
            _invalid_receipt("retrieval-status")
        queries.append(query)
    if tuple(queries) != _DEMO_RETRIEVAL_QUERIES:
        _invalid_receipt("retrieval-queries")


def _validate_pass_counts(value: object, *, first: bool) -> None:
    keys = frozenset(
        (
            "new_source_versions",
            "new_card_count",
            "new_evidence_count",
            "duplicate_card_count",
            "forbidden_source_writes",
        )
    )
    counts = _exact_mapping(value, keys, "idempotence-counts")
    if any(not _is_count(counts.get(key)) for key in keys):
        _invalid_receipt("idempotence-counts")
    if first:
        if (
            counts.get("new_source_versions") != 5
            or not _is_count(counts.get("new_card_count"), positive=True)
            or not _is_count(counts.get("new_evidence_count"), positive=True)
            or counts.get("duplicate_card_count") != 0
            or counts.get("forbidden_source_writes") != 0
        ):
            _invalid_receipt("idempotence-first-pass")
    elif any(counts.get(key) != 0 for key in keys):
        _invalid_receipt("idempotence-second-pass")


def _validate_knowledge_demo_receipt(receipt: object) -> Mapping[str, Any]:
    payload = _exact_mapping(receipt, _DEMO_RECEIPT_KEYS, "top-level-structure")
    if _contains_secret_path(payload):
        _invalid_receipt("path-leak")
    if (
        not _is_exact_int(payload.get("schema_version"), 1)
        or payload.get("command_version") != "course-helper/demo@1"
        or payload.get("status") != "degraded"
        or payload.get("root_id") != "reference-demo"
        or not _is_sha256(payload.get("manifest_digest"))
    ):
        _invalid_receipt("identity")
    if (
        not _is_exact_int(payload.get("deep_read_source_count"), 5)
        or not _is_exact_int(payload.get("hash_verified_source_count"), 5)
        or not _is_exact_int(payload.get("inventory_root_count"), 2)
        or payload.get("inventory_integrity_scope") != "metadata-only"
        or not _is_exact_int(payload.get("pptx_slide_chunks"), 16)
        or not _is_exact_int(payload.get("pptx_chunks_with_notes"), 16)
    ):
        _invalid_receipt("required-counts")

    _validate_demo_source_integrity(payload.get("source_integrity"))
    _validate_demo_inventory(
        payload.get("inventory_integrity"), payload.get("inventory_item_count")
    )

    quarantine = payload.get("quarantined_extension_counts")
    if (
        not isinstance(quarantine, dict)
        or frozenset(quarantine) != _DEMO_QUARANTINED_EXTENSIONS
        or any(not _is_count(count) for count in quarantine.values())
        or not _is_count(quarantine.get(".pth"), positive=True)
    ):
        _invalid_receipt("quarantine")
    markdown_units = payload.get("markdown_units")
    datasets = payload.get("profiled_datasets")
    if (
        not isinstance(markdown_units, list)
        or any(not isinstance(unit, str) or not unit for unit in markdown_units)
        or frozenset(markdown_units) != _DEMO_MARKDOWN_UNITS
        or len(markdown_units) != len(_DEMO_MARKDOWN_UNITS)
        or not isinstance(datasets, list)
        or any(not isinstance(dataset, str) or not dataset for dataset in datasets)
        or frozenset(datasets) != _DEMO_DATASETS
        or len(datasets) != len(_DEMO_DATASETS)
    ):
        _invalid_receipt("selected-content")
    parser_versions = payload.get("parser_versions")
    if (
        not isinstance(parser_versions, dict)
        or frozenset(parser_versions) != frozenset(_DEMO_PARSER_VERSIONS)
        or any(
            not isinstance(version, str) or not version
            for version in parser_versions.values()
        )
        or parser_versions != _DEMO_PARSER_VERSIONS
    ):
        _invalid_receipt("parser-versions")
    object_digests = payload.get("object_digests")
    if (
        not isinstance(object_digests, dict)
        or frozenset(object_digests) != _DEMO_OBJECT_DIGEST_KEYS
        or any(not _is_sha256(digest) for digest in object_digests.values())
    ):
        _invalid_receipt("object-digests")

    _validate_demo_checks(payload.get("checks"))
    _validate_demo_retrievals(payload.get("retrievals"))
    if (
        not _is_count(payload.get("published_card_count"), positive=True)
        or not _is_count(payload.get("review_decision_count"), positive=True)
        or any(
            not _is_exact_int(payload.get(key), 0)
            for key in (
                "new_source_versions",
                "new_card_count",
                "new_evidence_count",
                "duplicate_card_count",
                "forbidden_source_writes",
            )
        )
    ):
        _invalid_receipt("publication-counts")
    idempotence = _exact_mapping(
        payload.get("idempotence"),
        frozenset(("pass_count", "first_pass", "second_pass", "verified")),
        "idempotence-structure",
    )
    if (
        not _is_exact_int(idempotence.get("pass_count"), 2)
        or idempotence.get("verified") is not True
    ):
        _invalid_receipt("idempotence-status")
    _validate_pass_counts(idempotence.get("first_pass"), first=True)
    _validate_pass_counts(idempotence.get("second_pass"), first=False)
    return payload


def check_knowledge_demo_receipt(
    repo_root: Path,
    receipt_path: Path | None = None,
    *,
    require_canonical: bool = True,
) -> CheckResult:
    selected_path = (
        repo_root / KNOWLEDGE_DEMO_RECEIPT
        if receipt_path is None
        else Path(receipt_path)
    )
    try:
        raw_receipt = selected_path.read_bytes()
        receipt = json.loads(raw_receipt)
        payload = _validate_knowledge_demo_receipt(receipt)
        if (
            require_canonical
            and hashlib.sha256(raw_receipt).hexdigest().upper()
            != KNOWLEDGE_DEMO_RECEIPT_SHA256
        ):
            _invalid_receipt("canonical-sha256")
    except _ReceiptValidationError as exc:
        return CheckResult(
            "knowledge demo receipt",
            False,
            f"invalid knowledge demo receipt: {exc}",
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        return CheckResult(
            "knowledge demo receipt",
            False,
            "invalid knowledge demo receipt: unreadable-or-malformed",
        )
    return CheckResult(
        "knowledge demo receipt",
        True,
        "validated 5 hash-verified sources, "
        f"{payload['inventory_item_count']} metadata items, and "
        f"{payload['published_card_count']} published cards",
    )


def check_helper_design_qa(repo_root: Path) -> CheckResult:
    report_path = repo_root / HELPER_DESIGN_QA_REPORT
    if not report_path.is_file():
        return CheckResult("helper design QA", False, "missing Helper design QA report")
    try:
        lines = [line.strip() for line in _read_utf8(report_path).splitlines() if line.strip()]
    except (OSError, UnicodeError):
        return CheckResult("helper design QA", False, "cannot read Helper design QA report")
    physical_lines = [
        line for line in lines if line.casefold().startswith("physical dual-screen:")
    ]
    if physical_lines != ["physical dual-screen: NOT CERTIFIED"]:
        return CheckResult(
            "helper design QA",
            False,
            "report must state 'physical dual-screen: NOT CERTIFIED' exactly once",
        )
    if not lines or lines[-1] != "final result: passed":
        return CheckResult(
            "helper design QA",
            False,
            "final non-empty line must be exactly 'final result: passed'",
        )
    return CheckResult(
        "helper design QA",
        True,
        "physical dual-screen: NOT CERTIFIED; final result: passed",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _rgb_triplet(value: str) -> tuple[int, int, int] | None:
    raw_channels = re.findall(r"\d+(?:\.\d+)?%?", value)
    if len(raw_channels) < 3:
        return None

    channels: list[int] = []
    for raw in raw_channels[:3]:
        if raw.endswith("%"):
            channel = round(float(raw[:-1]) * 2.55)
        else:
            channel = round(float(raw))
        channels.append(channel)
    return channels[0], channels[1], channels[2]


def _load_css(
    gate_name: str,
    css_paths: Sequence[Path],
) -> tuple[str | None, CheckResult | None]:
    if not css_paths:
        return None, CheckResult(gate_name, False, "no CSS paths supplied")

    chunks: list[str] = []
    for path in css_paths:
        try:
            chunks.append(_read_utf8(path))
        except OSError as exc:
            return None, CheckResult(gate_name, False, f"cannot read {path}: {exc}")
    return "\n".join(chunks), None


def scan_light_theme(*css_paths: Path) -> CheckResult:
    css, error = _load_css("light theme", css_paths)
    if error is not None:
        return error
    assert css is not None
    css = _CSS_COMMENT_RE.sub("", css)

    violations: list[str] = []

    gradient = _GRADIENT_RE.search(css)
    if gradient is not None:
        violations.append(f"forbidden gradient: {gradient.group(0)}")

    for declaration in _SURFACE_DECLARATION_RE.finditer(css):
        property_name = declaration.group("property")
        value = declaration.group("value")
        dark_hex = _DARK_HEX_RE.search(value)
        if dark_hex is not None:
            violations.append(
                f"dark surface {dark_hex.group(0)} in {property_name}"
            )

        for rgb in _RGB_RE.finditer(value):
            triplet = _rgb_triplet(rgb.group("channels"))
            if triplet in _FORBIDDEN_RGB:
                violations.append(f"dark rgb surface {rgb.group(0)} in {property_name}")

    if violations:
        return CheckResult("light theme", False, "; ".join(violations))
    return CheckResult(
        "light theme",
        True,
        f"{len(css_paths)} CSS files match the light-theme contract",
    )


def check_light_tokens(*css_paths: Path) -> CheckResult:
    css, error = _load_css("light tokens", css_paths)
    if error is not None:
        return error
    assert css is not None
    css = _CSS_COMMENT_RE.sub("", css)

    violations: list[str] = []
    for token, expected in EXPECTED_TOKENS.items():
        values = re.findall(
            rf"(?:^|[;{{])\s*{re.escape(token)}\s*:\s*([^;}}]+)",
            css,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        normalized = [value.strip().lower() for value in values]
        if not normalized or any(value != expected for value in normalized):
            found = ", ".join(normalized) if normalized else "missing"
            violations.append(f"{token} must be {expected}; found {found}")

    if violations:
        return CheckResult("light tokens", False, "; ".join(violations))
    return CheckResult("light tokens", True, "required light tokens are exact")


def _normalized_git_path(path: str) -> str:
    parts = [
        part
        for part in path.strip().replace("\\", "/").split("/")
        if part not in ("", ".")
    ]
    return "/".join(parts).casefold()


def protected_path_violations(changed_paths: Sequence[str]) -> list[str]:
    violations: list[str] = []
    for original in changed_paths:
        normalized = _normalized_git_path(original)
        protected = any(
            normalized == root or normalized.startswith(f"{root}/")
            for root in _PROTECTED_ROOTS
        )
        if protected or normalized == "agents.md":
            violations.append(original)
    return violations


def committed_changed_paths(
    repo_root: Path,
    baseline: str = "e6cd08d59",
) -> list[str]:
    args = [
        "git",
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=ACDMR",
        f"{baseline}...HEAD",
        "--",
    ]
    try:
        completed = subprocess.run(
            args,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start git changed-path query: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"git changed-path query exited {completed.returncode}: {detail}"
        )
    return [path for path in completed.stdout.split("\0") if path]


def check_source_image(image_path: Path, expected_sha256: str) -> CheckResult:
    if not image_path.is_file():
        return CheckResult("source image", False, f"missing {image_path}")

    try:
        actual = _sha256_file(image_path)
    except OSError as exc:
        return CheckResult("source image", False, f"cannot hash {image_path}: {exc}")

    expected = expected_sha256.upper()
    if actual != expected:
        return CheckResult(
            "source image",
            False,
            f"expected {expected}, got {actual}",
        )
    return CheckResult("source image", True, f"SHA-256 {actual}")


def _acceptance_receipt_error(repo_root: Path) -> str | None:
    receipt_path = repo_root / ACCEPTANCE_RECEIPT
    try:
        receipt = json.loads(_read_utf8(receipt_path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"invalid acceptance receipt: {exc}"

    if not isinstance(receipt, dict) or receipt.get("schemaVersion") != 1:
        return "acceptance receipt must be a schemaVersion 1 object"
    if receipt.get("designQa") != "passed":
        return "acceptance receipt designQa must be 'passed'"
    if receipt.get("physicalDualScreenCertified") is not False:
        return "acceptance receipt must not certify unverified physical dual-screen"
    if not re.fullmatch(
        r"[0-9a-f]{40}",
        str(receipt.get("currentCommitBeforeEvidenceCommit", "")),
        re.IGNORECASE,
    ):
        return "acceptance receipt implementation commit must be a full Git hash"

    protected = receipt.get("protectedChangedPathGuard")
    if not isinstance(protected, dict) or protected.get("forbiddenPathCount") != 0:
        return "acceptance receipt protected-path guard must report zero violations"

    commands = receipt.get("commands")
    if (
        not isinstance(commands, list)
        or not commands
        or any(
            not isinstance(command, dict) or command.get("exitCode") != 0
            for command in commands
        )
    ):
        return "acceptance receipt commands must be non-empty and all pass"

    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict):
        return "acceptance receipt evidence must be an object"

    entries: dict[str, object] = {"reference": receipt.get("reference")}
    entries.update({name: evidence.get(name) for name in ACCEPTANCE_HASHED_ENTRIES if name != "reference"})
    for name, expected_path in ACCEPTANCE_HASHED_ENTRIES.items():
        entry = entries.get(name)
        if not isinstance(entry, dict):
            return f"acceptance receipt entry {name!r} must be an object"
        if entry.get("path") != expected_path.as_posix():
            return f"acceptance receipt entry {name!r} has an unexpected path"
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash, re.IGNORECASE
        ):
            return f"acceptance receipt entry {name!r} needs a SHA-256 digest"
        asset_path = repo_root / expected_path
        if not asset_path.is_file():
            return f"acceptance receipt entry {name!r} is missing {expected_path}"
        try:
            actual_hash = _sha256_file(asset_path)
        except OSError as exc:
            return f"cannot hash acceptance evidence {expected_path}: {exc}"
        if actual_hash != expected_hash.upper():
            return (
                f"acceptance receipt SHA-256 mismatch for {expected_path}: "
                f"expected {expected_hash.upper()}, got {actual_hash}"
            )

    return None


def check_workflow_order(workflow_path: Path) -> CheckResult:
    try:
        source = _read_utf8(workflow_path)
    except OSError as exc:
        return CheckResult("workflow order", False, f"cannot read {workflow_path}: {exc}")

    cursor = -1
    for label in WORKFLOW_LABELS:
        position = source.find(label, cursor + 1)
        if position < 0:
            return CheckResult(
                "workflow order",
                False,
                f"workflow label missing or out of order: {label}",
            )
        cursor = position
    return CheckResult("workflow order", True, "four workflow labels are ordered")


def check_required_domain_files(repo_root: Path) -> CheckResult:
    missing = [str(path) for path in REQUIRED_DOMAIN_FILES if not (repo_root / path).is_file()]
    if missing:
        return CheckResult("durable domain", False, f"missing: {', '.join(missing)}")
    return CheckResult(
        "durable domain",
        True,
        "course schema, course domain, and teaching domain exist",
    )


def check_design_qa(repo_root: Path) -> CheckResult:
    report_path = repo_root / DESIGN_QA_REPORT
    if not report_path.exists():
        return CheckResult(
            "design QA",
            True,
            "PENDING: platform/web/design-qa.md is not present before Task 7",
        )
    if not report_path.is_file():
        return CheckResult(
            "design QA",
            False,
            "platform/web/design-qa.md exists but is not a file",
        )

    try:
        lines = [line.strip() for line in _read_utf8(report_path).splitlines() if line.strip()]
    except OSError as exc:
        return CheckResult("design QA", False, f"cannot read {report_path}: {exc}")

    if not lines or lines[-1] != "final result: passed":
        final_line = lines[-1] if lines else "<empty>"
        return CheckResult(
            "design QA",
            False,
            "final non-empty line must be exactly "
            f"'final result: passed'; found {final_line!r}",
        )

    missing = [str(path) for path in DESIGN_QA_EVIDENCE if not (repo_root / path).is_file()]
    if missing:
        return CheckResult("design QA", False, f"missing evidence: {', '.join(missing)}")
    receipt_error = _acceptance_receipt_error(repo_root)
    if receipt_error:
        return CheckResult("design QA", False, receipt_error)
    return CheckResult(
        "design QA",
        True,
        "final result: passed with hash-verified acceptance evidence",
    )


def _compact_output(value: str, limit: int = 800) -> str:
    without_ansi = _ANSI_ESCAPE_RE.sub("", value)
    single_line = " | ".join(
        line.strip() for line in without_ansi.splitlines() if line.strip()
    )
    console_safe = single_line.encode("ascii", errors="backslashreplace").decode(
        "ascii"
    )
    if len(console_safe) <= limit:
        return console_safe

    marker = " ... [output truncated] ... "
    if limit <= len(marker):
        return console_safe[:limit]
    available = limit - len(marker)
    head_length = available // 2
    tail_length = available - head_length
    return (
        console_safe[:head_length]
        + marker
        + console_safe[-tail_length:]
    )


def run_command(name: str, args: Sequence[str], cwd: Path) -> CheckResult:
    command = list(args)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
    except OSError as exc:
        return CheckResult(name, False, f"could not start command: {exc}")

    stdout = _compact_output(completed.stdout)
    stderr = _compact_output(completed.stderr)
    parts = [f"exit {completed.returncode}"]
    if stdout:
        parts.append(f"stdout: {stdout}")
    if stderr:
        parts.append(f"stderr: {stderr}")
    return CheckResult(name, completed.returncode == 0, "; ".join(parts))


def _redact_source_root(details: str, source_root: str) -> str:
    redacted = details
    variants = {
        source_root,
        source_root.replace("\\", "/"),
        source_root.replace("/", "\\"),
    }
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            redacted = re.sub(
                re.escape(variant),
                "[source root redacted]",
                redacted,
                flags=re.IGNORECASE,
            )
    return redacted


def run_knowledge_demo_gate(
    repo_root: Path,
    require_source_root: bool,
) -> CheckResult:
    source_root = os.environ.get("COURSE_REFERENCE_ROOT", "")
    if not source_root:
        details = "NOT CERTIFIED: COURSE_REFERENCE_ROOT unset"
        return CheckResult("knowledge demo", not require_source_root, details)

    try:
        with tempfile.TemporaryDirectory(
            prefix="course-studio-knowledge-demo-"
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            database_path = temporary_root / "knowledge.db"
            receipt_path = temporary_root / "receipt.json"
            command_result = run_command(
                "knowledge demo",
                [
                    sys.executable,
                    "-m",
                    "course_helper.demo",
                    "--database",
                    str(database_path),
                    "--evidence",
                    str(receipt_path),
                    "--verify-idempotence",
                ],
                repo_root / "platform/helper",
            )
            if not command_result.ok:
                return CheckResult(
                    "knowledge demo",
                    False,
                    _redact_source_root(command_result.details, source_root),
                )
            receipt_result = check_knowledge_demo_receipt(repo_root, receipt_path)
            return CheckResult(
                "knowledge demo",
                receipt_result.ok,
                receipt_result.details,
            )
    except OSError:
        return CheckResult(
            "knowledge demo",
            False,
            "knowledge demo temporary execution failed",
        )


def _protected_paths_gate(repo_root: Path) -> CheckResult:
    try:
        changed_paths = committed_changed_paths(repo_root)
    except RuntimeError as exc:
        return CheckResult("protected paths", False, str(exc))

    violations = protected_path_violations(changed_paths)
    if violations:
        return CheckResult(
            "protected paths",
            False,
            f"protected committed paths: {', '.join(violations)}",
        )
    return CheckResult(
        "protected paths",
        True,
        f"{len(changed_paths)} committed path names checked from Git metadata",
    )


def _course_composition_receipt_error(repo_root: Path) -> str | None:
    receipt_path = repo_root / COURSE_COMPOSITION_RECEIPT
    policy_path = repo_root / COURSE_BROWSER_POLICY
    try:
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        policy_bytes = policy_path.read_bytes()
        policy = json.loads(policy_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "browser receipt or browser policy is unreadable"
    if not isinstance(receipt, dict) or frozenset(receipt) != frozenset(
        ("schemaVersion", "status", "mode", "browserPolicySha256", "operations", "published", "checks")
    ):
        return "browser receipt structure is invalid"
    if (
        receipt.get("schemaVersion") != 1
        or receipt.get("status") != "verified"
        or receipt.get("mode") != "fixture-backed-loopback"
        or receipt.get("browserPolicySha256")
        != hashlib.sha256(policy_bytes).hexdigest()
    ):
        return "browser receipt identity or policy digest is invalid"
    if not isinstance(policy, dict) or (
        policy.get("schemaVersion") != 1
        or policy.get("channel") != "chrome"
        or policy.get("productName") != "Google Chrome"
        or policy.get("allowedBasename") != "chrome.exe"
        or not _is_sha256(policy.get("executableSha256"))
        or not isinstance(policy.get("publisher"), str)
        or "Google LLC" not in policy.get("publisher", "")
    ):
        return "browser policy is invalid"
    checks = receipt.get("checks")
    if not isinstance(checks, dict) or checks != {
        "exactOperationReplay": True,
        "byteBoundReopen": True,
        "stagePresenterSharedProjection": True,
        "physicalDualScreenCertified": False,
        "liveNetworkAuthorizationCertified": False,
        "protectedSourceAccessed": False,
    }:
        return "browser receipt certification boundaries are invalid"
    published = receipt.get("published")
    if not isinstance(published, dict) or frozenset(published) != frozenset(
        ("courseVersionId", "slideDeckId", "runtimeManifestId", "runtimeManifestDigest", "courseProjectionId")
    ) or not _is_sha256(published.get("runtimeManifestDigest")):
        return "published projection identity is invalid"
    if any(not isinstance(value, str) or not value for value in published.values()):
        return "published projection IDs are invalid"
    operations = receipt.get("operations")
    if not isinstance(operations, list) or not operations:
        return "browser receipt has no operations"
    required_types = {
        "knowledge_import_start",
        "knowledge_review_resolve",
        "knowledge_card_publish",
        "knowledge_index",
        "course_compose",
        "course_outline_confirm",
        "chart_build",
        "visual_search",
        "visual_acquire",
        "visual_revalidate",
        "course_visual_attach",
        "course_validate",
        "course_publish",
    }
    operation_types: list[str] = []
    publish_operations: list[Mapping[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict) or frozenset(operation) != frozenset(
            ("type", "operationId", "resultIds")
        ):
            return "browser operation envelope is invalid"
        operation_type = operation.get("type")
        if not isinstance(operation_type, str):
            return "browser operation type is invalid"
        operation_types.append(operation_type)
        if operation_type == "course_publish":
            publish_operations.append(operation)
    if not required_types.issubset(operation_types):
        return "browser operation chain is incomplete"
    if len(publish_operations) != 2 or publish_operations[0] != publish_operations[1]:
        return "course publish replay is not byte-identical"
    if receipt_bytes.lower().find(b"course_aiproduct") >= 0 or receipt_bytes.lower().find(b"references/") >= 0:
        return "browser receipt leaks a protected source path"
    for screenshot in COURSE_COMPOSITION_SCREENSHOTS:
        try:
            payload = (repo_root / screenshot).read_bytes()
        except OSError:
            return f"missing browser screenshot: {screenshot.name}"
        if len(payload) < 1024 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return f"invalid browser screenshot: {screenshot.name}"
    return None


def run_course_composition_gate(repo_root: Path) -> CheckResult:
    error = _course_composition_receipt_error(repo_root)
    return CheckResult(
        "course composition",
        error is None,
        error or "fixture-backed loopback publish, replay, reopen, Stage, and Presenter verified",
    )


def run_authentic_visuals_gate(repo_root: Path) -> CheckResult:
    error = _course_composition_receipt_error(repo_root)
    if error is not None:
        return CheckResult("authentic visuals", False, error)
    return CheckResult(
        "authentic visuals",
        True,
        "HISTORICAL RECEIPT VERIFIED — CURRENT NETWORK AUTHORIZATION NOT CERTIFIED",
    )


def run_focused(repo_root: Path) -> list[CheckResult]:
    return [
        run_command(
            "python QA tests",
            [sys.executable, "-m", "pytest", "platform/qa/test_run.py", "-q"],
            repo_root,
        ),
        scan_light_theme(
            repo_root / "platform/web/src/app/tokens.css",
            repo_root / "platform/web/src/app/app.css",
        ),
        check_light_tokens(
            repo_root / "platform/web/src/app/tokens.css",
            repo_root / "platform/web/src/app/app.css",
        ),
        check_workflow_order(
            repo_root / "platform/web/src/components/WorkflowHeader.tsx"
        ),
        check_required_domain_files(repo_root),
        check_source_image(repo_root / SOURCE_IMAGE, SOURCE_IMAGE_SHA256),
        _protected_paths_gate(repo_root),
        check_design_qa(repo_root),
        check_knowledge_demo_receipt(repo_root),
        check_helper_design_qa(repo_root),
        run_course_composition_gate(repo_root),
        run_authentic_visuals_gate(repo_root),
    ]


def npm_executable(platform: str | None = None) -> str:
    active_platform = sys.platform if platform is None else platform
    return "npm.cmd" if active_platform == "win32" else "npm"


def run_all(repo_root: Path) -> list[CheckResult]:
    results = list(run_focused(repo_root))
    results.append(
        run_command(
            "helper tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "platform/helper/tests",
                "-m",
                "not reference_demo and not network_visual and not model_download",
                "-q",
            ],
            repo_root,
        )
    )
    results.append(run_knowledge_demo_gate(repo_root, require_source_root=False))
    npm = npm_executable()
    commands = (
        (
            "web tests",
            [npm, "--prefix", "platform/web", "test", "--", "--run"],
        ),
        (
            "web typecheck",
            [npm, "--prefix", "platform/web", "run", "typecheck"],
        ),
        ("web build", [npm, "--prefix", "platform/web", "run", "build"]),
        ("web browser E2E", [npm, "--prefix", "platform/web", "run", "test:e2e"]),
    )
    for name, args in commands:
        results.append(run_command(name, args, repo_root))
    return results


def _print_results(results: Sequence[CheckResult]) -> None:
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        name = _compact_output(result.name, limit=80)
        details = _compact_output(result.details)
        print(f"{status} {name}: {details}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _embedding_live_preflight(repo_root: Path, receipt_value: str) -> int | None:
    if os.environ.get("COURSE_EMBEDDING_MODEL_DOWNLOAD") != "1":
        print("EMBEDDING_MODEL_OPT_IN_REQUIRED", file=sys.stderr)
        return 2
    conflicting = (
        "COURSE_NETWORK_VISUAL_TEST",
        "COURSE_REFERENCE_ROOT",
        "COURSE_PROJECTION_RESTORE",
        "COURSE_PROJECTION_INTEGRATION_TEST",
        "COURSE_PROJECTION_HARDWARE_TEST",
    )
    if any(name in os.environ for name in conflicting):
        print("EMBEDDING_MODEL_OPT_IN_CONFLICT", file=sys.stderr)
        return 2
    expected = (repo_root / EMBEDDING_MODEL_RECEIPT).resolve()
    try:
        supplied = Path(receipt_value).resolve()
    except OSError:
        supplied = Path(receipt_value).absolute()
    if supplied != expected:
        print("EMBEDDING_MODEL_PATH_POLICY_MISMATCH", file=sys.stderr)
        return 3
    return None


class NetworkVisualQaFailure(RuntimeError):
    def __init__(self, symbol: str, exit_code: int) -> None:
        self.symbol = symbol
        self.exit_code = exit_code
        super().__init__(symbol)


def _network_visual_live_preflight(repo_root: Path, receipt_value: str) -> int | None:
    if os.environ.get("COURSE_NETWORK_VISUAL_TEST") != "1":
        print("NETWORK_VISUAL_OPT_IN_REQUIRED", file=sys.stderr)
        return 2
    conflicting = (
        "COURSE_EMBEDDING_MODEL_DOWNLOAD",
        "COURSE_REFERENCE_ROOT",
        "COURSE_PROJECTION_RESTORE",
        "COURSE_PROJECTION_INTEGRATION_TEST",
        "COURSE_PROJECTION_HARDWARE_TEST",
    )
    if any(name in os.environ for name in conflicting):
        print("NETWORK_VISUAL_OPT_IN_CONFLICT", file=sys.stderr)
        return 2
    expected = (repo_root / NETWORK_VISUAL_RECEIPT).resolve()
    try:
        supplied = Path(receipt_value).resolve()
    except OSError:
        supplied = Path(receipt_value).absolute()
    if supplied != expected:
        print("NETWORK_VISUAL_PATH_POLICY_MISMATCH", file=sys.stderr)
        return 3
    return None


def _network_visual_receipt_path_preflight(repo_root: Path, receipt_path: Path) -> None:
    approved = repo_root.absolute()
    expected = (approved / NETWORK_VISUAL_RECEIPT).absolute()
    if receipt_path.absolute() != expected:
        raise NetworkVisualQaFailure("NETWORK_VISUAL_PATH_POLICY_MISMATCH", 3)
    try:
        if os.path.lexists(expected):
            target_info = os.lstat(expected)
            target_attributes = getattr(target_info, "st_file_attributes", 0)
            if (
                not stat.S_ISREG(target_info.st_mode)
                or target_info.st_nlink != 1
                or bool(
                    target_attributes
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
            ):
                raise NetworkVisualQaFailure("NETWORK_VISUAL_PROTECTED_BOUNDARY", 6)
        current = expected.parent
        while True:
            if current.exists():
                info = os.lstat(current)
                attributes = getattr(info, "st_file_attributes", 0)
                if current.is_symlink() or bool(
                    attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                ):
                    raise NetworkVisualQaFailure("NETWORK_VISUAL_PROTECTED_BOUNDARY", 6)
            if current == approved:
                break
            parent = current.parent
            if parent == current:
                raise NetworkVisualQaFailure("NETWORK_VISUAL_PROTECTED_BOUNDARY", 6)
            current = parent
        expected.relative_to(approved)
    except NetworkVisualQaFailure:
        raise
    except (OSError, ValueError) as error:
        raise NetworkVisualQaFailure("NETWORK_VISUAL_PROTECTED_BOUNDARY", 6) from error


def _network_visual_live_module(repo_root: Path) -> object:
    helper_root = (repo_root / "platform/helper").absolute()
    module_path = helper_root / "course_helper/network_visual_live.py"
    try:
        with _bound_embedding_file(helper_root, module_path, max_bytes=2_000_000):
            helper_text = str(helper_root)
            if helper_text not in sys.path:
                sys.path.insert(0, helper_text)
            module = importlib.import_module("course_helper.network_visual_live")
            module_file = getattr(module, "__file__", None)
            live_error = getattr(module, "NetworkVisualLiveError", None)
            if (
                not isinstance(module_file, str)
                or Path(module_file).resolve(strict=True) != module_path.resolve(strict=True)
                or not isinstance(live_error, type)
                or live_error.__module__ != module.__name__
            ):
                raise ImportError("network visual live authority origin mismatch")
            return module
    except (OSError, ImportError) as error:
        raise NetworkVisualQaFailure("NETWORK_VISUAL_PROTECTED_BOUNDARY", 6) from error


def produce_network_visual_live(repo_root: Path, receipt_path: Path) -> int:
    """Acquire one provider visual and atomically seal only a validated receipt."""
    transaction: object | None = None
    temporary: Path | None = None
    work_root: Path | None = None
    live: object | None = None
    quarantine = repo_root / NETWORK_VISUAL_QUARANTINE_ROOT
    try:
        _network_visual_receipt_path_preflight(repo_root, receipt_path)
        live = _network_visual_live_module(repo_root)
        quarantine.mkdir(parents=True, exist_ok=True)
        work_root = quarantine / f"run-{uuid.uuid4().hex}"
        receipt = live.build_live_receipt(work_root)
        temporary = live.write_temporary_receipt(receipt, quarantine)
        live.validate_receipt(temporary)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        transaction = live.seal_receipt(temporary, receipt_path, defer_commit=True)
        committed = transaction.commit()
        revalidated = live.validate_receipt(receipt_path)
        if committed != revalidated:
            raise NetworkVisualQaFailure("NETWORK_VISUAL_RECEIPT_INVALID", 5)
        transaction.finalize()
    except NetworkVisualQaFailure as error:
        if transaction is not None:
            transaction.rollback()
        print(error.symbol, file=sys.stderr)
        return error.exit_code
    except Exception as error:
        if transaction is not None:
            transaction.rollback()
        live_error = getattr(live, "NetworkVisualLiveError", ()) if live is not None else ()
        if isinstance(live_error, type) and isinstance(error, live_error):
            symbol = getattr(error, "code", "")
            exit_code = {
                "NETWORK_VISUAL_ACQUISITION_FAILED": 4,
                "NETWORK_VISUAL_RECEIPT_INVALID": 5,
                "NETWORK_VISUAL_PROTECTED_BOUNDARY": 6,
            }.get(symbol, 6)
            print(symbol if symbol in {
                "NETWORK_VISUAL_ACQUISITION_FAILED",
                "NETWORK_VISUAL_RECEIPT_INVALID",
                "NETWORK_VISUAL_PROTECTED_BOUNDARY",
            } else "NETWORK_VISUAL_PROTECTED_BOUNDARY", file=sys.stderr)
            return exit_code
        print("NETWORK_VISUAL_ACQUISITION_FAILED", file=sys.stderr)
        return 4
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if work_root is not None:
            shutil.rmtree(work_root, ignore_errors=True)
        try:
            quarantine.rmdir()
        except OSError:
            pass
    print("NETWORK_VISUAL_LIVE_VERIFIED")
    print("COURSE PUBLICATION NOT CERTIFIED")
    return 0


def _embedding_manifest_path_preflight(repo_root: Path, manifest_path: Path) -> None:
    expected = (repo_root / EMBEDDING_MODEL_MANIFEST).absolute()
    supplied = manifest_path.absolute()
    if supplied != expected:
        raise EmbeddingLiveFailure("MODEL_MANIFEST_PATH_POLICY_MISMATCH", 3)
    approved = repo_root.absolute()
    try:
        supplied.relative_to(approved)
        current = supplied
        reached_approved = False
        while True:
            info = os.lstat(current)
            attributes = getattr(info, "st_file_attributes", 0)
            if current.is_symlink() or bool(
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise EmbeddingLiveFailure(
                    "EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6
                )
            if current == approved:
                reached_approved = True
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        if not reached_approved or not supplied.is_file():
            raise EmbeddingLiveFailure("MODEL_MANIFEST_PATH_POLICY_MISMATCH", 3)
        if supplied.resolve(strict=True).parent != expected.parent.resolve(strict=True):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6)
    except EmbeddingLiveFailure:
        raise
    except (OSError, ValueError) as error:
        raise EmbeddingLiveFailure("MODEL_MANIFEST_PATH_POLICY_MISMATCH", 3) from error


def _embedding_live_module(repo_root: Path) -> object:
    helper_root = (repo_root / "platform/helper").absolute()
    module_path = helper_root / "course_helper/embedding_live.py"
    try:
        with _bound_embedding_file(helper_root, module_path, max_bytes=8_000_000):
            helper_text = str(helper_root)
            if helper_text not in sys.path:
                sys.path.insert(0, helper_text)
            module = importlib.import_module("course_helper.embedding_live")
            module_file = getattr(module, "__file__", None)
            authority_type = getattr(module, "LiveEmbeddingAuthority", None)
            if (
                not isinstance(module_file, str)
                or Path(module_file).resolve(strict=True) != module_path.resolve(strict=True)
                or not isinstance(authority_type, type)
                or authority_type.__module__ != module.__name__
            ):
                raise ImportError("embedding live authority origin mismatch")
            return module
    except (OSError, ImportError) as error:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6) from error


def embedding_manifest_phase(
    manifest_path: Path,
    authority: object,
) -> EmbeddingManifestContext:
    try:
        repo_root = manifest_path.parents[3]
    except IndexError as error:
        raise EmbeddingLiveFailure("MODEL_MANIFEST_PATH_POLICY_MISMATCH", 3) from error
    try:
        with _bound_embedding_file(repo_root, manifest_path, max_bytes=2_000_000) as raw_bytes:
            raw = json.loads(
                raw_bytes,
                object_pairs_hook=lambda pairs: (
                    dict(pairs)
                    if len({key for key, _value in pairs}) == len(pairs)
                    else (_ for _ in ()).throw(ValueError("duplicate manifest key"))
                ),
            )
            phase = raw.get("phase") if isinstance(raw, dict) else None
            model_cache = getattr(authority, "model_cache_module", None)
            if model_cache is None:
                raise EmbeddingLiveFailure("EMBEDDING_MODEL_AUTHORITY_INVALID", 6)
            if phase == "bootstrap-required":
                manifest = model_cache.load_bootstrap_manifest_bytes(raw_bytes)
                if type(manifest) is not model_cache.BootstrapModelManifest:
                    raise EmbeddingLiveFailure("MODEL_MANIFEST_INVALID", 3)
            elif phase == "complete":
                manifest = model_cache.load_model_manifest_bytes(raw_bytes)
                if type(manifest) is not model_cache.ModelManifest:
                    raise EmbeddingLiveFailure("MODEL_MANIFEST_INVALID", 3)
            else:
                raise EmbeddingLiveFailure("MODEL_MANIFEST_PHASE_INVALID", 3)
    except EmbeddingLiveFailure:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise EmbeddingLiveFailure("MODEL_MANIFEST_INVALID", 3) from error
    except Exception as error:
        code = getattr(error, "code", "MODEL_MANIFEST_INVALID")
        if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", code) is None:
            code = "MODEL_MANIFEST_INVALID"
        raise EmbeddingLiveFailure(code, 3) from error
    return EmbeddingManifestContext(
        phase=phase,
        manifest_digest=manifest.aggregate_digest,
        model_cache=model_cache,
        manifest=manifest,
        authority=authority,
    )


_PHASE_A_SMALL_MEMBERS = {
    "config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
}
_PHASE_A_SMALL_MEMBER_BLOBS = {
    "config.json": "60938626ad1097a0c1a14be4f8340e32c714a056",
    "special_tokens_map.json": "a8b3208c2884c4efb86e49300fdd3dc877220cdf",
    "tokenizer.json": "cdb3043fc938fc918c06e66cf704c2ba58f88747",
    "tokenizer_config.json": "3a59388f0fd1bd22dec2ce7902c1be8e1fb84107",
}
_PHASE_B_ONNX_XET_PATH = (
    "/xet-bridge-us/676a9a3040be8b8a518ccd4e/"
    "9eedf0673c9aa300264fe51ef8df7c22e09538e5512f8132f3c2b65ef8143076"
)
_PHASE_B_ONNX_XET_QUERY_KEYS = {
    "Expires",
    "Key-Pair-Id",
    "Policy",
    "Signature",
    "X-Xet-Cas-Uid",
    "response-content-disposition",
    "user_id",
}
_METADATA_TRANSPORT_SYMBOLS = {
    "dns": "EMBEDDING_MODEL_METADATA_DNS_FAILED",
    "connect": "EMBEDDING_MODEL_METADATA_CONNECT_FAILED",
    "tls": "EMBEDDING_MODEL_METADATA_TLS_FAILED",
    "http-policy": "EMBEDDING_MODEL_METADATA_HTTP_POLICY_FAILED",
}


def _phase_a_pypi_metadata_url_policy(url: str, redirect_depth: int) -> bool:
    if redirect_depth != 0:
        return False
    try:
        parsed = _strict_embedding_https_url(url)
    except ValueError:
        return False
    segments = parsed.path.split("/")
    if (
        parsed.hostname != "pypi.org"
        or parsed.query
        or len(segments) not in {4, 5}
        or segments[1] != "pypi"
        or segments[-1] != "json"
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?", segments[2]) is None
    ):
        return False
    if len(segments) == 5 and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}", segments[3]
    ) is None:
        return False
    return True


def _phase_a_member_url_policy(
    initial_url: str,
    candidate_url: str,
    redirect_depth: int,
) -> bool:
    try:
        initial = _strict_embedding_https_url(initial_url)
        candidate = _strict_embedding_https_url(candidate_url)
    except ValueError:
        return False
    revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    initial_prefix = f"/Qdrant/bge-small-zh-v1.5/resolve/{revision}/"
    if (
        initial.hostname != "huggingface.co"
        or not initial.path.startswith(initial_prefix)
        or initial.query != "download=true"
    ):
        return False
    member = initial.path.removeprefix(initial_prefix)
    if member not in _PHASE_A_SMALL_MEMBERS:
        return False
    if redirect_depth == 0:
        return candidate_url == initial_url
    cache_path = (
        f"/api/resolve-cache/models/Qdrant/bge-small-zh-v1.5/{revision}/{member}"
    )
    return (
        candidate.hostname == "huggingface.co"
        and candidate.path in {initial.path, cache_path}
        and len(candidate.query) <= 2048
    )


def _phase_b_artifact_url_policy(
    initial_url: str,
    candidate_url: str,
    redirect_depth: int,
) -> bool:
    """Allow only the pinned origin and its exact current immutable redirects."""

    try:
        initial = _strict_embedding_https_url(initial_url)
        candidate = _strict_embedding_https_url(candidate_url)
    except ValueError:
        return False
    if initial.hostname == "files.pythonhosted.org":
        return redirect_depth == 0 and candidate_url == initial_url
    revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    prefix = f"/Qdrant/bge-small-zh-v1.5/resolve/{revision}/"
    if (
        initial.hostname != "huggingface.co"
        or not initial.path.startswith(prefix)
        or initial.query != "download=true"
    ):
        return False
    member = initial.path.removeprefix(prefix)
    if member not in {*_PHASE_A_SMALL_MEMBERS, "model_optimized.onnx"}:
        return False
    if redirect_depth == 0:
        return candidate_url == initial_url
    if redirect_depth != 1:
        return False
    try:
        query_pairs = parse_qsl(
            candidate.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except ValueError:
        return False
    if any(not key or not value for key, value in query_pairs):
        return False
    query = dict(query_pairs)
    if len(query) != len(query_pairs):
        return False
    if member in _PHASE_A_SMALL_MEMBERS:
        cache_path = (
            f"/api/resolve-cache/models/Qdrant/bge-small-zh-v1.5/"
            f"{revision}/{member}"
        )
        return (
            candidate.hostname == "huggingface.co"
            and candidate.path == cache_path
            and set(query) == {"download", "etag"}
            and query["download"] == "true"
            and query["etag"].strip('"') == _PHASE_A_SMALL_MEMBER_BLOBS[member]
            and len(candidate.query) <= 256
        )
    return (
        candidate.hostname == "us.aws.cdn.hf.co"
        and candidate.path == _PHASE_B_ONNX_XET_PATH
        and set(query) == _PHASE_B_ONNX_XET_QUERY_KEYS
        and len(candidate.query) <= 2048
    )


def run_embedding_bootstrap_phase(
    repo_root: Path,
    context: EmbeddingManifestContext,
    candidate_path: Path,
    quarantine_root: Path,
) -> None:
    """Invoke the real Phase A implementation; never promote or seal."""

    model_cache = context.model_cache
    manifest = context.manifest

    def fetch_model_metadata(url: str) -> bytes:
        expected = model_cache.PINNED_MODEL_METADATA_URL
        try:
            return _embedding_https_fetch(
                url,
                url_policy=lambda candidate, depth: (
                    depth == 0 and candidate == expected
                ),
                max_bytes=1_000_000,
                failure_symbol="EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED",
                reason_symbols=_METADATA_TRANSPORT_SYMBOLS,
            )
        except EmbeddingLiveFailure as error:
            symbol_codes = {
                "EMBEDDING_MODEL_METADATA_DNS_FAILED": (
                    "MODEL_BOOTSTRAP_METADATA_DNS_FAILED"
                ),
                "EMBEDDING_MODEL_METADATA_CONNECT_FAILED": (
                    "MODEL_BOOTSTRAP_METADATA_CONNECT_FAILED"
                ),
                "EMBEDDING_MODEL_METADATA_TLS_FAILED": (
                    "MODEL_BOOTSTRAP_METADATA_TLS_FAILED"
                ),
                "EMBEDDING_MODEL_METADATA_HTTP_POLICY_FAILED": (
                    "MODEL_BOOTSTRAP_METADATA_HTTP_POLICY_FAILED"
                ),
                "EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED": (
                    "MODEL_BOOTSTRAP_METADATA_TRANSPORT_FAILED"
                ),
            }
            raise model_cache.ModelCacheError(
                symbol_codes.get(
                    error.symbol,
                    "MODEL_BOOTSTRAP_METADATA_TRANSPORT_FAILED",
                )
            ) from error

    def fetch_member(url: str, expected_size: int) -> bytes:
        try:
            return _embedding_https_fetch(
                url,
                url_policy=lambda candidate, depth: _phase_a_member_url_policy(
                    url,
                    candidate,
                    depth,
                ),
                max_bytes=expected_size,
                expected_size=expected_size,
                failure_symbol="EMBEDDING_MODEL_MEMBER_TRANSPORT_FAILED",
            )
        except EmbeddingLiveFailure as error:
            raise model_cache.ModelCacheError(
                "MODEL_BOOTSTRAP_MEMBER_TRANSPORT_FAILED"
            ) from error

    def resolve_runtime(package: object) -> object:
        return model_cache.resolve_runtime_wheels_from_pypi(
            package,
            fetch_metadata=lambda url: _embedding_https_fetch(
                url,
                url_policy=_phase_a_pypi_metadata_url_policy,
                max_bytes=8_000_000,
                failure_symbol="EMBEDDING_MODEL_RESOLUTION_FAILED",
            ),
        )

    try:
        model_cache.run_bootstrap_phase(
            manifest,
            candidate_path=candidate_path,
            quarantine_root=quarantine_root,
            approved_root=repo_root,
            fetch_model_metadata=fetch_model_metadata,
            fetch_member=fetch_member,
            resolve_runtime=resolve_runtime,
        )
    except EmbeddingLiveFailure:
        raise
    except Exception as error:
        code = getattr(error, "code", "")
        stage_symbols = {
            "MODEL_BOOTSTRAP_METADATA_TRANSPORT_FAILED": (
                "EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED"
            ),
            "MODEL_BOOTSTRAP_METADATA_DNS_FAILED": (
                "EMBEDDING_MODEL_METADATA_DNS_FAILED"
            ),
            "MODEL_BOOTSTRAP_METADATA_CONNECT_FAILED": (
                "EMBEDDING_MODEL_METADATA_CONNECT_FAILED"
            ),
            "MODEL_BOOTSTRAP_METADATA_TLS_FAILED": (
                "EMBEDDING_MODEL_METADATA_TLS_FAILED"
            ),
            "MODEL_BOOTSTRAP_METADATA_HTTP_POLICY_FAILED": (
                "EMBEDDING_MODEL_METADATA_HTTP_POLICY_FAILED"
            ),
            "MODEL_BOOTSTRAP_METADATA_INVALID": (
                "EMBEDDING_MODEL_METADATA_IDENTITY_FAILED"
            ),
            "MODEL_BOOTSTRAP_MEMBER_TRANSPORT_FAILED": (
                "EMBEDDING_MODEL_MEMBER_TRANSPORT_FAILED"
            ),
            "MODEL_BOOTSTRAP_MEMBER_MISMATCH": (
                "EMBEDDING_MODEL_MEMBER_IDENTITY_FAILED"
            ),
        }
        if code in stage_symbols:
            raise EmbeddingLiveFailure(stage_symbols[code], 4) from error
        if isinstance(code, str) and (
            "RESOLUTION" in code or "WHEEL_LOCK" in code
        ):
            raise EmbeddingLiveFailure(
                "EMBEDDING_MODEL_RESOLUTION_FAILED", 4
            ) from error
        if isinstance(code, str) and "PATH" in code:
            raise EmbeddingLiveFailure(
                "EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6
            ) from error
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_ACQUISITION_FAILED", 4) from error


def _fresh_embedding_pipeline_paths(
    quarantine_root: Path,
    *,
    prefix: str,
) -> tuple[Path, Path]:
    try:
        root = Path(tempfile.mkdtemp(prefix=prefix, dir=quarantine_root)).resolve(
            strict=True
        )
        child_temp = root / "child-temp"
        child_temp.mkdir()
        return root / "knowledge.db", child_temp
    except OSError as error:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6) from error


def _write_embedding_quarantine_receipt(
    quarantine_root: Path,
    receipt: object,
) -> Path:
    if type(receipt) is not dict or set(receipt) != _EMBEDDING_RECEIPT_KEYS:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    try:
        raw = json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        path = quarantine_root / f"receipt-{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("receipt write incomplete")
                offset += written
            os.fsync(descriptor)
            observed = os.fstat(descriptor)
            if observed.st_size != len(raw) or observed.st_nlink != 1:
                raise OSError("receipt identity invalid")
        finally:
            os.close(descriptor)
        return path
    except (OSError, TypeError, ValueError, OverflowError) as error:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5) from error


def _verify_embedding_replay(
    first: object,
    replay: object,
    *,
    first_database: Path,
    first_temp: Path,
    replay_database: Path,
    replay_temp: Path,
) -> None:
    if type(first) is not dict or type(replay) is not dict:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    stable = (
        "fixtureDigest",
        "indexVectorDigest",
        "indexSnapshotDigest",
        "retrievalDigest",
        "zeroNetworkReplayDigest",
    )
    first_provider = first.get("providerEvidence")
    replay_provider = replay.get("providerEvidence")
    first_ledger = first.get("allowedWriteLedger")
    replay_ledger = replay.get("allowedWriteLedger")
    try:
        first_database_info = first_database.stat()
        replay_database_info = replay_database.stat()
        first_temp_info = first_temp.stat()
        replay_temp_info = replay_temp.stat()
        identity_reused = (
            (first_database_info.st_dev, first_database_info.st_ino)
            == (replay_database_info.st_dev, replay_database_info.st_ino)
            or (first_temp_info.st_dev, first_temp_info.st_ino)
            == (replay_temp_info.st_dev, replay_temp_info.st_ino)
        )
    except OSError as error:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5) from error
    if (
        any(first.get(key) != replay.get(key) for key in stable)
        or type(first_provider) is not dict
        or type(replay_provider) is not dict
        or any(
            first_provider.get(key) == replay_provider.get(key)
            for key in ("processId", "challengeDigest", "tempTokenDigest")
        )
        or type(first_ledger) is not dict
        or type(replay_ledger) is not dict
        or first_ledger.get("allowedRoots") == replay_ledger.get("allowedRoots")
        or first_database.resolve(strict=True) == replay_database.resolve(strict=True)
        or first_temp.resolve(strict=True) == replay_temp.resolve(strict=True)
        or identity_reused
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)


def run_embedding_final_phase(
    repo_root: Path,
    context: EmbeddingManifestContext,
    generation_parent: Path,
    quarantine_root: Path,
    *,
    embedding_live: object | None = None,
) -> EmbeddingFinalPhaseArtifacts:
    model_cache = context.model_cache
    manifest = context.manifest
    authority = context.authority
    try:
        runtime = manifest.runtime
        model_members = tuple(manifest.files)
        runtime_wheels = tuple(runtime.wheels)
    except (AttributeError, TypeError) as error:
        raise EmbeddingLiveFailure("MODEL_MANIFEST_POLICY_MISMATCH", 3) from error
    artifact_sizes: dict[str, int] = {}
    try:
        artifacts = tuple(
            (item.artifact_url, item.size)
            for item in (*model_members, *runtime_wheels)
        )
    except (AttributeError, TypeError) as error:
        raise EmbeddingLiveFailure("MODEL_MANIFEST_POLICY_MISMATCH", 3) from error
    for artifact_url, artifact_size in artifacts:
        if (
            not isinstance(artifact_url, str)
            or type(artifact_size) is not int
            or artifact_size <= 0
            or artifact_url in artifact_sizes
        ):
            raise EmbeddingLiveFailure("MODEL_MANIFEST_POLICY_MISMATCH", 3)
        artifact_sizes[artifact_url] = artifact_size
    remaining_artifacts = dict(artifact_sizes)
    attempted_artifacts: set[str] = set()

    def assert_fetch_complete() -> None:
        if remaining_artifacts:
            raise EmbeddingLiveFailure(
                "EMBEDDING_MODEL_ARTIFACT_LEDGER_INVALID", 4
            )

    def fetch_artifact(url: str, expected_size: int) -> bytes:
        if (
            remaining_artifacts.get(url) != expected_size
            or url in attempted_artifacts
        ):
            raise EmbeddingLiveFailure(
                "EMBEDDING_MODEL_ARTIFACT_LEDGER_INVALID", 4
            )
        attempted_artifacts.add(url)
        payload = _embedding_https_fetch(
            url,
            url_policy=lambda candidate, depth: _phase_b_artifact_url_policy(
                url,
                candidate,
                depth,
            ),
            max_bytes=expected_size,
            expected_size=expected_size,
            failure_symbol="EMBEDDING_MODEL_ACQUISITION_FAILED",
        )
        remaining_artifacts.pop(url)
        return payload

    def install_runtime(
        runtime_identity: object,
        wheelhouse: Path,
        runtime_root: Path,
    ) -> None:
        installer = getattr(model_cache, "install_locked_runtime", None)
        if not callable(installer):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RUNTIME_UNAVAILABLE", 4)
        installer(runtime_identity, wheelhouse, runtime_root)

    def verify_generation(_verified: object) -> Mapping[str, object]:
        assert_fetch_complete()
        nonlocal first_database, first_temp, first_verification
        if embedding_live is None or authority is None:
            failure = getattr(model_cache, "ModelCacheError", RuntimeError)
            raise failure("MODEL_FINAL_VERIFICATION_UNAVAILABLE")
        first_database, first_temp = _fresh_embedding_pipeline_paths(
            quarantine_root,
            prefix="fresh-first-",
        )
        callback = getattr(
            embedding_live,
            "run_final_verification_callback",
            None,
        )
        if not callable(callback):
            failure = getattr(model_cache, "ModelCacheError", RuntimeError)
            raise failure("MODEL_FINAL_VERIFICATION_UNAVAILABLE")
        raw = callback(
            authority,
            manifest,
            _verified,
            database_path=first_database,
            temp_parent=first_temp,
            clock=lambda: pipeline_now,
        )
        if type(raw) is not dict or "providerOrigins" in raw:
            failure = getattr(model_cache, "ModelCacheError", RuntimeError)
            raise failure("MODEL_FINAL_VERIFICATION_FAILED")
        first_verification = raw
        return raw

    runner = getattr(model_cache, "run_final_phase", None)
    if not callable(runner):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RUNTIME_UNAVAILABLE", 4)
    first_database: Path | None = None
    first_temp: Path | None = None
    first_verification: dict[str, object] | None = None
    pipeline_now = datetime.now(timezone.utc)
    started_at = pipeline_now
    try:
        final_result = runner(
            manifest,
            generation_parent=generation_parent,
            quarantine_root=quarantine_root,
            approved_root=repo_root,
            fetch_artifact=fetch_artifact,
            install_runtime=install_runtime,
            verify_generation=verify_generation,
        )
        assert_fetch_complete()
        final_result_type = getattr(model_cache, "FinalPhaseResult", None)
        if (
            not isinstance(final_result_type, type)
            or type(final_result) is not final_result_type
        ):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
        if (
            embedding_live is None
            or authority is None
            or first_verification is None
            or first_database is None
            or first_temp is None
            or final_result.verification.get("pipeline")
            is not first_verification.get("pipeline")
        ):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
        expectation_type = getattr(embedding_live, "FinalExpectation", None)
        if not isinstance(expectation_type, type):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RUNTIME_UNAVAILABLE", 4)
        expectation = expectation_type.from_authority(
            manifest,
            final_result,
            authority,
        )
        first_pipeline = final_result.verification["pipeline"]
        if expectation.pipeline is not first_pipeline:
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)

        replay_database, replay_temp = _fresh_embedding_pipeline_paths(
            quarantine_root,
            prefix="fresh-replay-",
        )
        deny_sockets = getattr(model_cache, "_socket_denied_verification", None)
        replay_runner = getattr(embedding_live, "run_fresh_pipeline", None)
        if not callable(deny_sockets) or not callable(replay_runner):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RUNTIME_UNAVAILABLE", 4)
        with deny_sockets():
            replay_pipeline = replay_runner(
                expectation,
                database_path=replay_database,
                temp_parent=replay_temp,
                clock=lambda: pipeline_now,
            )
        _verify_embedding_replay(
            first_pipeline,
            replay_pipeline,
            first_database=first_database,
            first_temp=first_temp,
            replay_database=replay_database,
            replay_temp=replay_temp,
        )
        builder = getattr(embedding_live, "build_receipt", None)
        if not callable(builder):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RUNTIME_UNAVAILABLE", 4)
        receipt = builder(
            expectation,
            final_result,
            first_pipeline,
            started_at,
            datetime.now(timezone.utc),
        )
        temporary_receipt = _write_embedding_quarantine_receipt(
            final_result.quarantine_root,
            receipt,
        )
        return EmbeddingFinalPhaseArtifacts(
            temporary_receipt=temporary_receipt,
            final_result=final_result,
            expectation=expectation,
            first_pipeline=first_pipeline,
            replay_pipeline=replay_pipeline,
        )
    except EmbeddingLiveFailure:
        raise
    except Exception as error:
        live_error = getattr(embedding_live, "EmbeddingLiveError", ())
        if isinstance(live_error, type) and isinstance(error, live_error):
            code = getattr(error, "code", "")
            if code == "EMBEDDING_MODEL_RECEIPT_INVALID":
                raise EmbeddingLiveFailure(code, 5) from error
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6) from error
        model_error = getattr(model_cache, "ModelCacheError", ())
        if isinstance(model_error, type) and isinstance(error, model_error):
            code = getattr(error, "code", str(error))
            if code == "MODEL_FINAL_VERIFICATION_UNAVAILABLE":
                raise EmbeddingLiveFailure(
                    "EMBEDDING_MODEL_RUNTIME_UNAVAILABLE", 4
                ) from error
            if code == "MODEL_FINAL_VERIFICATION_FAILED":
                raise EmbeddingLiveFailure(
                    "EMBEDDING_MODEL_RECEIPT_INVALID", 5
                ) from error
            if isinstance(code, str) and "PATH" in code:
                raise EmbeddingLiveFailure(
                    "EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6
                ) from error
            raise EmbeddingLiveFailure(
                "EMBEDDING_MODEL_ACQUISITION_FAILED", 4
            ) from error
        raise


_EMBEDDING_RECEIPT_KEYS = {
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
_EMBEDDING_FILE_SIZES = {
    "config.json": 739,
    "model_optimized.onnx": 94781076,
    "special_tokens_map.json": 125,
    "tokenizer.json": 439125,
    "tokenizer_config.json": 367,
}
_EMBEDDING_CHECKS = {
    "model-members-verified",
    "runtime-wheel-closure",
    "specific-model-path",
    "cpython-socket-denied-inference",
    "index-snapshot-consistent",
    "hybrid-retrieval",
    "cpython-socket-denied-replay",
    "generation-tree-write-barrier",
}


def _embedding_mapping(value: object, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    return value


def _strict_embedding_json(raw: bytes) -> object:
    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_pairs,
    )

    def require_finite(item: object) -> None:
        if type(item) is float and not math.isfinite(item):
            raise ValueError("non-finite JSON number")
        if type(item) is list:
            for child in item:
                require_finite(child)
        elif type(item) is dict:
            for child in item.values():
                require_finite(child)

    require_finite(value)
    return value


def _canonical_embedding_receipt_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("receipt timestamp is not text")
    parsed = datetime.fromisoformat(value)
    if (
        parsed.utcoffset() != timedelta(0)
        or parsed.astimezone(timezone.utc).isoformat() != value
    ):
        raise ValueError("receipt timestamp is not canonical UTC")
    return parsed.astimezone(timezone.utc)


def _read_embedding_receipt_bytes(path: Path) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > 2_000_000
            or bool(
                getattr(before, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            raise ValueError("unsafe receipt path")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino, opened.st_size)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or identity != (before.st_dev, before.st_ino, before.st_size)
        ):
            raise ValueError("receipt identity changed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("short receipt read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("receipt grew while reading")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            (after.st_dev, after.st_ino, after.st_size) != identity
            or (current.st_dev, current.st_ino, current.st_size) != identity
        ):
            raise ValueError("receipt changed while reading")
        return b"".join(chunks)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def validate_embedding_model_receipt(
    path: Path,
    *,
    expected_manifest_digest: str,
    expected_manifest: object,
) -> Mapping[str, Any]:
    try:
        raw = _read_embedding_receipt_bytes(path)
        payload = _strict_embedding_json(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5) from error
    receipt = _embedding_mapping(payload, _EMBEDDING_RECEIPT_KEYS)
    digest = receipt["receiptDigest"]
    unsigned = dict(receipt)
    unsigned.pop("receiptDigest")
    if (
        type(receipt["schemaVersion"]) is not int
        or receipt["schemaVersion"] != 1
        or receipt["producer"] != "course-helper/embedding-model-live@1"
        or receipt["status"] != "verified"
        or receipt["policyId"] != "course-studio-rrf-v1"
        or receipt["manifestDigest"] != expected_manifest_digest
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        != digest
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    model = _embedding_mapping(
        receipt["model"],
        {
            "id",
            "revision",
            "artifactRepository",
            "artifactRevision",
            "dimension",
            "encodingPolicy",
        },
    )
    if model != {
        "id": "BAAI/bge-small-zh-v1.5",
        "revision": "7999e1d3359715c523056ef9478215996d62a620",
        "artifactRepository": "Qdrant/bge-small-zh-v1.5",
        "artifactRevision": "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
        "dimension": 512,
        "encodingPolicy": "utf8-nfkc-no-prefix",
    }:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    if _embedding_mapping(receipt["provider"], {"name", "version"}) != {
        "name": "fastembed",
        "version": "0.8.0",
    }:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    model_files = receipt["modelFiles"]
    if not isinstance(model_files, list) or len(model_files) != 5:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    for expected_path, raw_file in zip(sorted(_EMBEDDING_FILE_SIZES), model_files):
        item = _embedding_mapping(raw_file, {"path", "size", "sha256"})
        if (
            item["path"] != expected_path
            or item["size"] != _EMBEDDING_FILE_SIZES[expected_path]
            or not isinstance(item["sha256"], str)
            or _SHA256_RE.fullmatch(item["sha256"]) is None
        ):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    if model_files[1]["sha256"] != "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38":
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    runtime = _embedding_mapping(
        receipt["runtime"],
        {
            "python",
            "os",
            "architecture",
            "runtimeDigest",
            "wheelSetDigest",
            "generationDigest",
            "wheels",
        },
    )
    if (
        runtime["python"] != "3.12"
        or runtime["os"] != "windows"
        or runtime["architecture"] != "x86_64"
        or any(
            not isinstance(runtime[key], str) or _SHA256_RE.fullmatch(runtime[key]) is None
            for key in ("runtimeDigest", "wheelSetDigest", "generationDigest")
        )
        or not isinstance(runtime["wheels"], list)
        or not runtime["wheels"]
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    wheel_names: list[str] = []
    receipt_wheels: list[tuple[str, str, str, int, str]] = []
    for raw_wheel in runtime["wheels"]:
        wheel = _embedding_mapping(
            raw_wheel, {"name", "version", "filename", "size", "sha256"}
        )
        if (
            not isinstance(wheel["name"], str)
            or not isinstance(wheel["version"], str)
            or not isinstance(wheel["filename"], str)
            or not wheel["filename"].endswith(".whl")
            or type(wheel["size"]) is not int
            or wheel["size"] <= 0
            or not isinstance(wheel["sha256"], str)
            or _SHA256_RE.fullmatch(wheel["sha256"]) is None
        ):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
        wheel_names.append(wheel["name"].replace("_", "-").casefold())
        receipt_wheels.append(
            (
                wheel["name"],
                wheel["version"],
                wheel["filename"],
                wheel["size"],
                wheel["sha256"],
            )
        )
    if len(set(wheel_names)) != len(wheel_names) or not {"fastembed", "onnxruntime"}.issubset(wheel_names):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    try:
        expected_model = expected_manifest.model
        expected_package = expected_manifest.package
        expected_runtime = expected_manifest.runtime
        expected_model_identity = {
            "id": expected_model.id,
            "revision": expected_model.revision,
            "artifactRepository": expected_model.artifact_repository,
            "artifactRevision": expected_model.artifact_revision,
            "dimension": expected_model.dimension,
            "encodingPolicy": expected_model.encoding_policy,
        }
        expected_files = tuple(
            (item.path, item.size, item.sha256)
            for item in expected_manifest.files
        )
        expected_wheels = tuple(
            (
                item.name,
                item.version,
                item.filename,
                item.size,
                item.sha256,
            )
            for item in expected_runtime.wheels
        )
        expected_runtime_identity = (
            expected_runtime.python,
            expected_runtime.os,
            expected_runtime.architecture,
        )
        expected_provider_identity = (
            expected_package.name,
            expected_package.version,
        )
        expected_aggregate_digest = expected_manifest.aggregate_digest
    except (AttributeError, TypeError) as error:
        raise EmbeddingLiveFailure(
            "EMBEDDING_MODEL_RECEIPT_INVALID", 5
        ) from error
    receipt_files = tuple(
        (item["path"], item["size"], item["sha256"])
        for item in model_files
    )
    if (
        expected_aggregate_digest != expected_manifest_digest
        or model != expected_model_identity
        or (receipt["provider"]["name"], receipt["provider"]["version"])
        != expected_provider_identity
        or receipt_files != expected_files
        or (
            runtime["python"],
            runtime["os"],
            runtime["architecture"],
        )
        != expected_runtime_identity
        or tuple(receipt_wheels) != expected_wheels
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    isolation = _embedding_mapping(
        receipt["osNetworkIsolation"],
        {
            "status",
            "scope",
            "pythonAuditHook",
            "cpythonSocketGuards",
            "nativeWinsockCoverage",
        },
    )
    if isolation != {
        "status": "not-certified",
        "scope": "trusted-hash-locked-cpython-runtime",
        "pythonAuditHook": "verified",
        "cpythonSocketGuards": "verified",
        "nativeWinsockCoverage": "not-certified",
    }:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    for key in (
        "cacheDigest",
        "fixtureFingerprint",
        "zeroNetworkReplayDigest",
    ):
        if not isinstance(receipt[key], str) or _SHA256_RE.fullmatch(receipt[key]) is None:
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    snapshot = _embedding_mapping(
        receipt["indexSnapshot"], {"id", "digest", "candidateDigest", "publishedDigest"}
    )
    if (
        not isinstance(snapshot["id"], str)
        or not snapshot["id"]
        or any(
            not isinstance(snapshot[key], str) or _SHA256_RE.fullmatch(snapshot[key]) is None
            for key in ("digest", "candidateDigest", "publishedDigest")
        )
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    retrieval = _embedding_mapping(
        receipt["retrieval"],
        {"queryDigest", "filteredCandidateDigest", "snapshotDigest", "rrfK", "hits"},
    )
    if (
        retrieval["snapshotDigest"] != snapshot["digest"]
        or type(retrieval["rrfK"]) is not int
        or retrieval["rrfK"] != 60
        or any(
            not isinstance(retrieval[key], str) or _SHA256_RE.fullmatch(retrieval[key]) is None
            for key in ("queryDigest", "filteredCandidateDigest", "snapshotDigest")
        )
        or not isinstance(retrieval["hits"], list)
        or not retrieval["hits"]
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    for raw_hit in retrieval["hits"]:
        hit = _embedding_mapping(
            raw_hit, {"cardVersionId", "ftsRank", "semanticRank", "score"}
        )
        if (
            not isinstance(hit["cardVersionId"], str)
            or type(hit["ftsRank"]) is not int
            or hit["ftsRank"] <= 0
            or type(hit["semanticRank"]) is not int
            or hit["semanticRank"] <= 0
            or type(hit["score"]) not in (int, float)
            or abs(hit["score"] - (1 / (60 + hit["ftsRank"]) + 1 / (60 + hit["semanticRank"]))) > 1e-12
        ):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    proof = _embedding_mapping(
        receipt["zeroWriteProof"],
        {"scope", "status", "nativeGlobalCoverage", "evidenceDigest"},
    )
    if (
        proof["scope"] != "verified-generation-tree"
        or proof["status"] != "write-denied"
        or proof["nativeGlobalCoverage"] != "not-certified"
        or not isinstance(proof["evidenceDigest"], str)
        or _SHA256_RE.fullmatch(proof["evidenceDigest"]) is None
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    checks = receipt["checks"]
    if not isinstance(checks, list) or len(checks) != len(_EMBEDDING_CHECKS):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    check_codes: set[str] = set()
    for raw_check in checks:
        check = _embedding_mapping(raw_check, {"code", "status"})
        if check["status"] != "passed" or not isinstance(check["code"], str):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
        check_codes.add(check["code"])
    if check_codes != _EMBEDDING_CHECKS:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    try:
        started_at = _canonical_embedding_receipt_time(receipt["startedAt"])
        finished_at = _canonical_embedding_receipt_time(receipt["finishedAt"])
    except (TypeError, ValueError, OverflowError) as error:
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5) from error
    if (
        finished_at < started_at
        or _contains_secret_path(receipt)
        or any(
            "http://" in text.casefold() or "https://" in text.casefold()
            for text in _iter_json_strings(receipt)
        )
    ):
        raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
    return receipt


def produce_embedding_model_live(repo_root: Path, receipt_path: Path) -> int:
    """Dispatch one strict phase and seal only a self-validated Phase B receipt."""

    manifest_path = repo_root / EMBEDDING_MODEL_MANIFEST
    embedding_live: object | None = None
    try:
        _embedding_manifest_path_preflight(repo_root, manifest_path)
        embedding_live = _embedding_live_module(repo_root)
        authority_type = getattr(embedding_live, "LiveEmbeddingAuthority", None)
        if not isinstance(authority_type, type):
            raise EmbeddingLiveFailure("EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6)
        authority = authority_type.load()
        with authority:
            context = embedding_manifest_phase(manifest_path, authority)
            if context.phase == "bootstrap-required":
                run_embedding_bootstrap_phase(
                    repo_root,
                    context,
                    repo_root / EMBEDDING_BOOTSTRAP_CANDIDATE,
                    repo_root / EMBEDDING_QUARANTINE_ROOT,
                )
                print("MODEL_MANIFEST_BOOTSTRAP_REQUIRED", file=sys.stderr)
                return 3
            artifacts = run_embedding_final_phase(
                repo_root,
                context,
                repo_root / EMBEDDING_MODEL_CACHE,
                repo_root / EMBEDDING_QUARANTINE_ROOT,
                embedding_live=embedding_live,
            )
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            seal_transaction = embedding_live.seal_receipt(
                artifacts.temporary_receipt,
                receipt_path,
                artifacts.expectation,
                artifacts.final_result,
                artifacts.final_result.quarantine_root,
                defer_commit=True,
            )
            commit = getattr(seal_transaction, "commit", None)
            finalize = getattr(seal_transaction, "finalize", None)
            rollback = getattr(seal_transaction, "rollback", None)
            if not callable(commit) or not callable(finalize) or not callable(rollback):
                raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
            try:
                revalidated = embedding_live.validate_receipt(
                    receipt_path,
                    artifacts.expectation,
                    artifacts.final_result,
                )
                sealed = commit()
                if json.dumps(
                    sealed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ) != json.dumps(
                    revalidated,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ):
                    raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
                finalized = finalize()
                if json.dumps(
                    finalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ) != json.dumps(
                    sealed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ):
                    raise EmbeddingLiveFailure("EMBEDDING_MODEL_RECEIPT_INVALID", 5)
            except Exception:
                rollback()
                raise
    except EmbeddingLiveFailure as error:
        print(error.symbol, file=sys.stderr)
        return error.exit_code
    except Exception as error:
        live_error = getattr(embedding_live, "EmbeddingLiveError", ())
        if isinstance(live_error, type) and isinstance(error, live_error):
            code = getattr(error, "code", "")
            if code == "EMBEDDING_MODEL_RECEIPT_INVALID":
                print(code, file=sys.stderr)
                return 5
            print("EMBEDDING_MODEL_PROTECTED_BOUNDARY", file=sys.stderr)
            return 6
        print("EMBEDDING_MODEL_ACQUISITION_FAILED", file=sys.stderr)
        return 4
    print("EMBEDDING_MODEL_LIVE_VERIFIED: CPYTHON SOCKET-DENIED VERIFIED")
    print("OS NETWORK ISOLATION NOT CERTIFIED")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["network-visual-acquisition-live"]:
        if len(args) != 3 or args[1] != "--receipt":
            print(
                "usage: python platform/qa/run.py network-visual-acquisition-live --receipt "
                "platform/helper/evidence/network-visual-acquisition-live.json",
                file=sys.stderr,
            )
            return 2
        repo_root = _repo_root()
        preflight = _network_visual_live_preflight(repo_root, args[2])
        if preflight is not None:
            return preflight
        return produce_network_visual_live(repo_root, repo_root / NETWORK_VISUAL_RECEIPT)
    if args[:1] == ["embedding-model-live"]:
        if len(args) != 3 or args[1] != "--receipt":
            print(
                "usage: python platform/qa/run.py embedding-model-live --receipt "
                "platform/helper/evidence/embedding-model-live.json",
                file=sys.stderr,
            )
            return 2
        repo_root = _repo_root()
        preflight = _embedding_live_preflight(repo_root, args[2])
        if preflight is not None:
            return preflight
        return produce_embedding_model_live(repo_root, repo_root / EMBEDDING_MODEL_RECEIPT)
    if any(
        name in os.environ
        for name in (
            "COURSE_EMBEDDING_MODEL_DOWNLOAD",
            "COURSE_NETWORK_VISUAL_TEST",
            "COURSE_REFERENCE_ROOT",
            "COURSE_E2E_FIXTURE",
        )
    ):
        print("OFFLINE_GATE_LIVE_OPT_IN_SET", file=sys.stderr)
        return 2
    if len(args) != 1 or args[0] not in {
        "focused",
        "all",
        "knowledge-demo",
        "course-composition",
        "authentic-visuals",
    }:
        print(
            "usage: python platform/qa/run.py {focused|all|knowledge-demo|course-composition|authentic-visuals|"
            "embedding-model-live|network-visual-acquisition-live}",
            file=sys.stderr,
        )
        return 2

    repo_root = _repo_root()
    if args[0] == "focused":
        results = run_focused(repo_root)
    elif args[0] == "all":
        results = run_all(repo_root)
    elif args[0] == "course-composition":
        results = [run_course_composition_gate(repo_root)]
    elif args[0] == "authentic-visuals":
        results = [run_authentic_visuals_gate(repo_root)]
        if results[0].ok:
            reconfigure = getattr(sys.stdout, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8")
            print("HISTORICAL RECEIPT VERIFIED — CURRENT NETWORK AUTHORIZATION NOT CERTIFIED")
    else:
        results = [run_knowledge_demo_gate(repo_root, require_source_root=True)]
    _print_results(results)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
