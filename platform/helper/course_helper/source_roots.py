"""Registered source-root containment and deterministic artifact identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5

from course_helper.domain.common import SourceLocator
from course_helper.domain.sources import ChunkLocator


COURSE_STUDIO_ID_NAMESPACE = UUID("d8bc4ebb-fd7c-58ce-a2f9-93b4913564a6")
_SHA256_LENGTH = 64


class SourceRootViolation(ValueError):
    """A locator or registered root did not resolve to an allowlisted path."""


@dataclass(frozen=True)
class FileFingerprint:
    """Cheap inventory identity that deliberately does not read file bytes."""

    byte_size: int
    modified_ns: int


class SourceRootRegistry:
    """Resolve logical source locators within an explicit root allowlist."""

    def __init__(self, roots: Mapping[str, Path]) -> None:
        registered: dict[str, Path] = {}
        for root_id, root in roots.items():
            if not root_id:
                raise SourceRootViolation("source root IDs cannot be empty")
            candidate = Path(root)
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise SourceRootViolation(f"source root {root_id!r} does not exist") from error
            if not resolved.is_dir():
                raise SourceRootViolation(f"source root {root_id!r} is not a directory")
            registered[root_id] = resolved
        self._roots = registered

    def resolve(self, locator: SourceLocator) -> Path:
        """Return an existing regular file only when its final path stays in its root."""

        resolved = self._resolve_existing(locator)
        if not resolved.is_file():
            raise SourceRootViolation(
                f"source {locator.relative_path!r} is not a regular file"
            )
        return resolved

    def resolve_directory(self, locator: SourceLocator) -> Path:
        """Return an existing directory only when its final path stays in its root."""

        resolved = self._resolve_existing(locator)
        if not resolved.is_dir():
            raise SourceRootViolation(
                f"source {locator.relative_path!r} is not a directory"
            )
        return resolved

    def _resolve_existing(self, locator: SourceLocator) -> Path:
        root = self._roots.get(locator.root_id)
        if root is None:
            raise SourceRootViolation(f"source root {locator.root_id!r} is not registered")
        try:
            resolved_root = root.resolve(strict=True)
            resolved = (root / locator.relative_path).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise SourceRootViolation(
                f"source {locator.relative_path!r} does not exist in root {locator.root_id!r}"
            ) from error
        try:
            resolved.relative_to(resolved_root)
        except ValueError as error:
            raise SourceRootViolation(
                f"source {locator.relative_path!r} escapes root {locator.root_id!r}"
            ) from error
        return resolved


def quick_fingerprint(path: Path) -> FileFingerprint:
    """Return stat-only inventory metadata without hashing file contents."""

    stat = Path(path).stat()
    return FileFingerprint(byte_size=stat.st_size, modified_ns=stat.st_mtime_ns)


def stream_sha256(path: Path) -> str:
    """Hash a file explicitly in bounded chunks for an ingest operation."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_logical_id(locator: SourceLocator) -> str:
    """Derive stable source identity without clocks, randomness, or absolute paths."""

    return _uuid5(f"{locator.root_id}\0{locator.relative_path}")


def source_version_id(logical_id: str, content_digest: str) -> str:
    _validate_nonempty(logical_id, "logical_id")
    _validate_digest(content_digest)
    return _uuid5(f"{logical_id}\0{content_digest}")


def chunk_logical_id(source_logical_id: str, locator: ChunkLocator | str) -> str:
    _validate_nonempty(source_logical_id, "source_logical_id")
    canonical_locator = _canonical_locator(locator)
    return _uuid5(f"{source_logical_id}\0{canonical_locator}")


def chunk_version_id(
    logical_id: str,
    source_version_id: str,
    content_digest: str,
) -> str:
    _validate_nonempty(logical_id, "logical_id")
    _validate_nonempty(source_version_id, "source_version_id")
    _validate_digest(content_digest)
    return _uuid5(f"{logical_id}\0{source_version_id}\0{content_digest}")


def candidate_logical_id(
    kind: Literal["visual", "dataset", "card"],
    semantic_locator: str,
) -> str:
    """Derive typed logical identity for a visual, dataset, or card candidate."""

    _validate_nonempty(semantic_locator, "semantic_locator")
    normalized = semantic_locator.replace("\\", "/")
    return _uuid5(f"{kind}\0{normalized}")


def candidate_version_id(
    logical_id: str,
    parent_version_ids: Sequence[str],
    content_digest: str,
) -> str:
    """Derive candidate version identity from canonical parents and content."""

    _validate_nonempty(logical_id, "logical_id")
    _validate_digest(content_digest)
    parents = tuple(sorted(parent_version_ids))
    for parent_id in parents:
        _validate_nonempty(parent_id, "parent_version_id")
    parent_key = "\0".join(parents)
    return _uuid5(f"{logical_id}\0{parent_key}\0{content_digest}")


def _uuid5(name: str) -> str:
    return str(uuid5(COURSE_STUDIO_ID_NAMESPACE, name))


def _canonical_locator(locator: ChunkLocator | str) -> str:
    if isinstance(locator, ChunkLocator):
        return json.dumps(
            locator.model_dump(mode="json", by_alias=False, exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    _validate_nonempty(locator, "locator")
    return locator.replace("\\", "/")


def _validate_digest(content_digest: str) -> None:
    if len(content_digest) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in content_digest
    ):
        raise ValueError("content_digest must be a lowercase SHA-256 hex digest")


def _validate_nonempty(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
