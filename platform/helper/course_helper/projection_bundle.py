"""Resolve one immutable published course projection for the native Host."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from course_helper.artifacts import ArtifactStore
from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.composition import course_version_content_digest
from course_helper.domain.projection import ProjectionCommand
from course_helper.domain.slide_ast import (
    SlideAssetBinding,
    SlideNode,
    runtime_manifest_content_digest,
    slide_deck_content_digest,
)
from course_helper.projection_host import (
    ProjectionAssetSource,
    ProjectionHostError,
    ProjectionSessionBundle,
)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_BOOTSTRAP_BYTES = 40 * 1024


class PublishedProjectionBundleResolver:
    """Reopen and revalidate every immutable dependency on each session open."""

    def __init__(self, *, database_path: Path, artifact_root: Path) -> None:
        requested_database = Path(os.path.abspath(database_path))
        try:
            with _catalog_layout_lease(
                requested_database,
                include_sidecars=True,
            ) as binding:
                self._database_path = requested_database
                self._database_identity = binding.database_identity
                self._database_parent_identity = binding.parent_identity
            self._artifact_store = ArtifactStore(artifact_root)
        except Exception as error:
            raise ProjectionHostError("published_bundle_unavailable") from error

    def __call__(self, command: ProjectionCommand) -> ProjectionSessionBundle:
        try:
            return self._resolve(command)
        except ProjectionHostError:
            raise
        except Exception as error:
            raise ProjectionHostError("published_bundle_unavailable") from error

    def _resolve(self, command: ProjectionCommand) -> ProjectionSessionBundle:
        if (
            not isinstance(command, ProjectionCommand)
            or command.command != "open_projection_session"
            or command.session_id is None
            or set(command.payload)
            != {"courseVersionId", "slideDeckId", "runtimeManifestId"}
        ):
            raise ProjectionHostError("published_bundle_unavailable")
        identities = tuple(
            command.payload[key]
            for key in ("courseVersionId", "slideDeckId", "runtimeManifestId")
        )
        if any(
            not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None
            for value in identities
        ):
            raise ProjectionHostError("published_bundle_unavailable")
        course_id, deck_id, manifest_id = identities

        with _catalog_read_snapshot(
            self._database_path,
            expected_identity=self._database_identity,
            expected_parent_identity=self._database_parent_identity,
        ) as catalog:
            course = catalog.get_course_version(course_id)
            deck = catalog.get_slide_deck(deck_id)
            manifest = catalog.get_runtime_manifest(manifest_id)
            if course is None or deck is None or manifest is None:
                raise ProjectionHostError("published_bundle_unavailable")
            outline = catalog.get_course_outline(course.payload.outline_version_id)
            requirement = catalog.get_course_requirement(course.payload.requirement_id)
            if outline is None or requirement is None:
                raise ProjectionHostError("published_bundle_unavailable")
            if (
                course.payload.status != "published"
                or course.payload.content_digest
                != course_version_content_digest(course.payload)
                or deck.payload.course_version_id != course.payload.version_id
                or deck.payload.content_digest
                != slide_deck_content_digest(deck.payload)
                or manifest.payload.course_version_id != course.payload.version_id
                or manifest.payload.slide_deck_version_id != deck.payload.version_id
                or manifest.payload.slide_deck_digest != deck.payload.content_digest
                or manifest.payload.content_digest
                != runtime_manifest_content_digest(manifest.payload)
                or outline.payload.requirement_id != requirement.payload.requirement_id
                or outline.payload.version_id != course.payload.outline_version_id
                or outline.payload.content_digest != course.payload.outline_digest
            ):
                raise ProjectionHostError("published_bundle_unavailable")

            nodes = _walk_nodes(deck.payload.nodes)
            bindings = tuple(
                binding for node in nodes for binding in node.asset_bindings
            )
            required_artifacts = tuple(
                dict.fromkeys(binding.artifact_id for binding in bindings)
            )
            required_visuals = tuple(
                binding.visual_placement_id for binding in bindings
            )
            required_placements = tuple(
                dict.fromkeys(
                    placement_id
                    for node in nodes
                    for placement_id in node.placement_ids
                )
            )
            if (
                manifest.payload.artifact_ids != required_artifacts
                or course.payload.visual_placement_ids != required_visuals
                or course.payload.placement_ids != required_placements
            ):
                raise ProjectionHostError("published_bundle_unavailable")

            assets = _projection_asset_sources(
                catalog,
                self._artifact_store,
                bindings,
                manifest.payload.artifact_ids,
            )

            bootstrap = {
                "schemaVersion": 1,
                "courseDigest": course.payload.content_digest,
                "course": _teaching_course_projection(
                    course.payload.version_id,
                    requirement.payload,
                    outline.payload,
                    deck.payload,
                ),
                "projection": {
                    "courseVersion": _camelize(course.payload.model_dump(mode="json")),
                    "requirement": _camelize(
                        requirement.payload.model_dump(mode="json")
                    ),
                    "outline": _camelize(outline.payload.model_dump(mode="json")),
                    "slideDeck": _camelize(deck.payload.model_dump(mode="json")),
                    "runtimeManifest": _camelize(
                        manifest.payload.model_dump(mode="json")
                    ),
                },
            }
            if len(_canonical_json(bootstrap)) > _MAX_BOOTSTRAP_BYTES:
                raise ProjectionHostError("published_bundle_unavailable")
            navigation_identity = hashlib.sha256(
                _canonical_json(
                    {
                        "schemaVersion": 1,
                        "courseVersionId": course.payload.version_id,
                        "courseDigest": course.payload.content_digest,
                        "slideDeckId": deck.payload.version_id,
                        "slideDeckDigest": deck.payload.content_digest,
                        "runtimeManifestId": manifest.payload.version_id,
                        "runtimeManifestDigest": manifest.payload.content_digest,
                    }
                )
            ).hexdigest()
            return ProjectionSessionBundle(
                course_version_id=course.payload.version_id,
                runtime_manifest_digest=manifest.payload.content_digest,
                navigation_identity=navigation_identity,
                bootstrap=bootstrap,
                assets=assets,
            )


def _teaching_course_projection(
    course_version_id: str,
    requirement: Any,
    outline: Any,
    deck: Any,
) -> dict[str, Any]:
    nodes = _walk_nodes(deck.nodes)
    source_ids = tuple(
        dict.fromkeys(
            source_id
            for node in nodes
            for source_id in node.source_version_ids
        )
    )
    purpose_labels = {
        "core": "核心讲解",
        "example": "真实示例",
        "exercise": "实践练习",
        "evidence": "证据解析",
        "warning": "风险提示",
    }
    chapters: list[dict[str, Any]] = []
    for chapter in outline.chapters:
        lessons: list[dict[str, Any]] = []
        for index, placement in enumerate(chapter.placements):
            related = tuple(
                node
                for node in nodes
                if placement.placement_id in node.placement_ids
            )
            title = next(
                (
                    node.text.strip()
                    for node in related
                    if node.node_type in {"heading", "title"}
                    and isinstance(node.text, str)
                    and node.text.strip()
                ),
                f"知识单元 {index + 1}",
            )
            lesson_sources = tuple(
                dict.fromkeys(
                    source_id
                    for node in related
                    for source_id in node.source_version_ids
                )
            )
            lessons.append(
                {
                    "id": placement.placement_id,
                    "title": title,
                    "summary": purpose_labels[placement.purpose],
                    "durationMinutes": min(90, placement.allocated_minutes),
                    "sourceIds": list(lesson_sources),
                    "status": "grounded",
                }
            )
        if lessons:
            chapters.append(
                {
                    "id": chapter.chapter_id,
                    "title": chapter.title,
                    "objective": chapter.objective,
                    "lessons": lessons,
                }
            )
    if not chapters:
        raise ProjectionHostError("published_bundle_unavailable")
    created_at = deck.created_at.isoformat()
    return {
        "schemaVersion": 1,
        "id": course_version_id,
        "title": requirement.title,
        "audience": requirement.audience,
        "goal": "；".join(requirement.learning_goals),
        "durationMinutes": requirement.duration_minutes,
        "chapters": chapters,
        "sources": [
            {
                "id": source_id,
                "name": f"已治理来源 {index + 1}",
                "kind": "note",
                "size": 0,
                "status": "ready",
                "addedAt": created_at,
            }
            for index, source_id in enumerate(source_ids)
        ],
        "updatedAt": created_at,
    }


def _projection_asset_sources(
    catalog: KnowledgeCatalog,
    artifact_store: ArtifactStore,
    bindings: tuple[SlideAssetBinding, ...],
    artifact_ids: tuple[str, ...],
) -> tuple[ProjectionAssetSource, ...]:
    """Validate every binding, then transfer each immutable artifact only once."""

    if len(set(artifact_ids)) != len(artifact_ids):
        raise ProjectionHostError("published_bundle_unavailable")
    metadata_by_id = {}
    for artifact_id in artifact_ids:
        stored_artifact = catalog.get_artifact(artifact_id)
        if stored_artifact is None:
            raise ProjectionHostError("published_bundle_unavailable")
        metadata = stored_artifact.payload
        if metadata.artifact_id != artifact_id or metadata.byte_size < 1:
            raise ProjectionHostError("published_bundle_unavailable")
        metadata_by_id[artifact_id] = metadata
    if {binding.artifact_id for binding in bindings} != set(artifact_ids):
        raise ProjectionHostError("published_bundle_unavailable")
    for binding in bindings:
        metadata = metadata_by_id.get(binding.artifact_id)
        if (
            metadata is None
            or metadata.content_digest != binding.artifact_digest
            or metadata.media_type != binding.media_type
        ):
            raise ProjectionHostError("published_bundle_unavailable")
    return tuple(
        ProjectionAssetSource(
            opaque_id=metadata_by_id[artifact_id].artifact_id,
            media_type=metadata_by_id[artifact_id].media_type,
            byte_size=metadata_by_id[artifact_id].byte_size,
            sha256=metadata_by_id[artifact_id].content_digest,
            open_verified=lambda metadata=metadata_by_id[
                artifact_id
            ]: artifact_store.open_verified(metadata),
        )
        for artifact_id in artifact_ids
    )


@contextmanager
def _catalog_read_snapshot(
    database_path: Path,
    *,
    expected_identity: tuple[int, int],
    expected_parent_identity: tuple[int, int],
) -> Iterator[KnowledgeCatalog]:
    catalog: KnowledgeCatalog | None = None
    with _catalog_layout_lease(database_path, include_sidecars=True) as binding:
        if (
            binding.database_identity != expected_identity
            or binding.parent_identity != expected_parent_identity
        ):
            raise ProjectionHostError("published_bundle_unavailable")
        try:
            catalog = KnowledgeCatalog.open_read_only(database_path)
            catalog.connection.execute("BEGIN")
            catalog.connection.execute(
                "SELECT name FROM sqlite_master LIMIT 1"
            ).fetchone()
            _validate_bound_layout(database_path, binding)
            yield catalog
        finally:
            if catalog is not None:
                try:
                    catalog.connection.rollback()
                finally:
                    catalog.close()


@dataclass(frozen=True)
class _CatalogLayoutBinding:
    parent_identity: tuple[int, int]
    database_identity: tuple[int, int]
    sidecar_identities: tuple[tuple[str, tuple[int, int]], ...]


@dataclass(frozen=True)
class _PathLease:
    descriptor: int
    identity: tuple[int, int]
    windows: bool


@contextmanager
def _catalog_layout_lease(
    database_path: Path,
    *,
    include_sidecars: bool,
) -> Iterator[_CatalogLayoutBinding]:
    leases: list[_PathLease] = []
    try:
        parent = _open_path_lease(database_path.parent, directory=True)
        leases.append(parent)
        database = _open_path_lease(database_path, directory=False)
        leases.append(database)
        _validate_exact_bound_path(database_path.parent, directory=True)
        _validate_exact_bound_path(database_path, directory=False)
        sidecars: list[tuple[str, tuple[int, int]]] = []
        if include_sidecars:
            for suffix, sidecar in _present_sidecars(database_path):
                lease = _open_path_lease(sidecar, directory=False)
                leases.append(lease)
                _validate_exact_bound_path(sidecar, directory=False)
                sidecars.append((suffix, lease.identity))
        binding = _CatalogLayoutBinding(
            parent_identity=parent.identity,
            database_identity=database.identity,
            sidecar_identities=tuple(sidecars),
        )
        _validate_bound_layout(database_path, binding)
        yield binding
    finally:
        for lease in reversed(leases):
            _close_path_lease(lease)


def _validate_bound_layout(
    database_path: Path,
    binding: _CatalogLayoutBinding,
) -> None:
    current: list[tuple[str, tuple[int, int]]] = []
    checks = (
        (database_path.parent, True, binding.parent_identity),
        (database_path, False, binding.database_identity),
    )
    for path, directory, expected in checks:
        lease = _open_path_lease(path, directory=directory)
        try:
            _validate_exact_bound_path(path, directory=directory)
            if lease.identity != expected:
                raise ProjectionHostError("published_bundle_unavailable")
        finally:
            _close_path_lease(lease)
    for suffix, sidecar in _present_sidecars(database_path):
        lease = _open_path_lease(sidecar, directory=False)
        try:
            _validate_exact_bound_path(sidecar, directory=False)
            current.append((suffix, lease.identity))
        finally:
            _close_path_lease(lease)
    if tuple(current) != binding.sidecar_identities:
        raise ProjectionHostError("published_bundle_unavailable")


def _present_sidecars(database_path: Path) -> tuple[tuple[str, Path], ...]:
    present: list[tuple[str, Path]] = []
    for suffix in ("-wal", "-shm"):
        path = Path(str(database_path) + suffix)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        present.append((suffix, path))
    return tuple(present)


def _open_path_lease(path: Path, *, directory: bool) -> _PathLease:
    if os.name == "nt":
        handle = _lock_windows_catalog_path(path, directory=directory)
        try:
            identity = _windows_handle_identity(handle, directory=directory)
        except Exception:
            _close_windows_handle(handle)
            raise
        return _PathLease(handle, identity, True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if (
            (directory and not stat.S_ISDIR(information.st_mode))
            or (not directory and not stat.S_ISREG(information.st_mode))
            or (not directory and information.st_nlink != 1)
        ):
            raise ProjectionHostError("published_bundle_unavailable")
        return _PathLease(
            descriptor,
            (information.st_dev, information.st_ino),
            False,
        )
    except Exception:
        os.close(descriptor)
        raise


def _close_path_lease(lease: _PathLease) -> None:
    if lease.windows:
        _close_windows_handle(lease.descriptor)
    else:
        os.close(lease.descriptor)


def _validate_exact_bound_path(path: Path, *, directory: bool) -> None:
    requested = Path(os.path.abspath(path))
    resolved = requested.resolve(strict=True)
    if (
        os.path.normcase(str(requested)) != os.path.normcase(str(resolved))
        or resolved.is_symlink()
        or _is_reparse(resolved)
        or (directory and not resolved.is_dir())
        or (not directory and not resolved.is_file())
    ):
        raise ProjectionHostError("published_bundle_unavailable")


def _lock_windows_catalog_path(path: Path, *, directory: bool) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        0x00000080 if directory else 0x80000000,
        0x00000001 | 0x00000002,
        None,
        3,
        (0x02000000 if directory else 0x08000000) | 0x00200000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or int(handle) == invalid:
        raise ProjectionHostError("published_bundle_unavailable")
    return int(handle)


def _windows_handle_identity(handle: int, *, directory: bool) -> tuple[int, int]:
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
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    information = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise ProjectionHostError("published_bundle_unavailable")
    is_directory = bool(information.dwFileAttributes & 0x10)
    if (
        bool(information.dwFileAttributes & 0x400)
        or is_directory != directory
        or (not directory and information.nNumberOfLinks != 1)
    ):
        raise ProjectionHostError("published_bundle_unavailable")
    return (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
    )


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def _walk_nodes(roots: tuple[SlideNode, ...]) -> tuple[SlideNode, ...]:
    ordered: list[SlideNode] = []
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(node.children))
    return tuple(ordered)


def _camelize(value: Any) -> Any:
    if isinstance(value, dict):
        return {_lower_camel(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_camelize(item) for item in value]
    return value


def _lower_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except AttributeError:
        return False


__all__ = ["PublishedProjectionBundleResolver"]
