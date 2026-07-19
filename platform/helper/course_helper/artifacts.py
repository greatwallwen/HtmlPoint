"""Bounded content-addressed storage for verified local media artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from io import BufferedReader, BytesIO
import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO, Callable, Literal

from defusedxml import ElementTree
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field


Clock = Callable[[], datetime]
SUPPORTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")
GENERATED_MEDIA_TYPES = ("image/svg+xml",)
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_SVG_ALLOWED_ATTRIBUTES = {
    "svg": {"width", "height", "viewBox", "role", "aria-labelledby"},
    "title": {"id"},
    "desc": {"id"},
    "rect": {"x", "y", "width", "height", "rx", "fill"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "stroke-width"},
    "circle": {"cx", "cy", "r", "fill"},
    "polyline": {"points", "fill", "stroke", "stroke-width"},
    "text": {
        "x",
        "y",
        "fill",
        "font-size",
        "font-family",
        "text-anchor",
    },
}


class ArtifactError(ValueError):
    """Base class for artifact validation and storage failures."""


class ArtifactRootError(ArtifactError):
    """The caller-supplied artifact root is unsafe or not a directory."""


class ArtifactValidationError(ArtifactError):
    """Artifact bytes or caller metadata fail the bounded media contract."""


class ArtifactTooLarge(ArtifactValidationError):
    """Artifact bytes exceed the configured maximum."""


class ArtifactWriteError(ArtifactError):
    """An atomic artifact write or existing-object verification failed."""


class ArtifactMetadata(BaseModel):
    """Path-free immutable metadata for one content-addressed artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^artifact-[0-9a-f]{64}$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=1)
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/svg+xml"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    created_at: datetime


@dataclass(frozen=True)
class ArtifactWrite:
    metadata: ArtifactMetadata
    reused: bool


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        value = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first)) == os.path.normcase(str(second))


def _ensure_regular_no_reparse(path: Path, *, label: str) -> None:
    if _is_reparse_or_symlink(path):
        raise ArtifactRootError(f"{label} cannot be a symbolic link or reparse point")
    if not path.is_file():
        raise ArtifactRootError(f"{label} must be a regular file")


class ArtifactStore:
    """Write verified media under one caller-owned ignored app-data root."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = 32 * 1024 * 1024,
        block_size: int = 1024 * 1024,
        max_pixels: int = 40_000_000,
    ) -> None:
        if max_bytes < 1 or block_size < 1 or max_pixels < 1:
            raise ArtifactValidationError("artifact limits must be positive")
        requested = Path(root)
        normalized = Path(os.path.abspath(requested))
        if requested.exists() and _is_reparse_or_symlink(requested):
            raise ArtifactRootError(
                "artifact root cannot be a symbolic link or reparse point"
            )
        try:
            requested.mkdir(parents=True, exist_ok=True)
            resolved = requested.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ArtifactRootError("artifact root could not be created") from error
        if not _same_path(normalized, resolved):
            raise ArtifactRootError(
                "artifact root cannot traverse a symbolic link or reparse point"
            )
        if _is_reparse_or_symlink(resolved) or not resolved.is_dir():
            raise ArtifactRootError("artifact root must be a safe directory")
        self._root = resolved
        self._objects = self._safe_directory(self._root / "objects")
        self._temporary = self._safe_directory(self._root / ".tmp")
        self._max_bytes = max_bytes
        self._block_size = block_size
        self._max_pixels = max_pixels
        self._known_metadata: dict[str, ArtifactMetadata] = {}

    def _safe_directory(self, path: Path) -> Path:
        try:
            path.mkdir(parents=False, exist_ok=True)
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ArtifactRootError("artifact storage directory could not be created") from error
        if (
            not _same_path(path, resolved)
            or _is_reparse_or_symlink(path)
            or not path.is_dir()
        ):
            raise ArtifactRootError(
                "artifact storage directory cannot be a symbolic link or reparse point"
            )
        try:
            resolved.relative_to(self._root)
        except ValueError as error:
            raise ArtifactRootError("artifact storage directory escaped its root") from error
        return resolved

    def _object_path(self, content_digest: str) -> Path:
        if len(content_digest) != 64 or any(
            character not in "0123456789abcdef" for character in content_digest
        ):
            raise ArtifactValidationError("artifact digest must be lowercase SHA-256")
        first = self._safe_directory(self._objects / content_digest[:2])
        second = self._safe_directory(first / content_digest[2:4])
        target = second / content_digest
        try:
            target.relative_to(self._objects)
        except ValueError as error:
            raise ArtifactRootError("artifact object escaped its root") from error
        return target

    def _validate_object_location(self, target: Path, *, must_exist: bool) -> None:
        directories = (self._objects, target.parent.parent, target.parent)
        for directory in directories:
            if _is_reparse_or_symlink(directory):
                raise ArtifactRootError(
                    "artifact object directory cannot be a reparse point"
                )
            try:
                resolved = directory.resolve(strict=True)
                resolved.relative_to(self._objects)
            except (OSError, RuntimeError, ValueError) as error:
                raise ArtifactRootError(
                    "artifact object directory escaped its root"
                ) from error
            if not _same_path(directory, resolved):
                raise ArtifactRootError(
                    "artifact object directory traverses a reparse point"
                )
        if must_exist:
            _ensure_regular_no_reparse(target, label="artifact object")
            try:
                resolved_target = target.resolve(strict=True)
                resolved_target.relative_to(self._objects)
            except (OSError, RuntimeError, ValueError) as error:
                raise ArtifactRootError("artifact object escaped its root") from error
            if not _same_path(target, resolved_target):
                raise ArtifactRootError("artifact object traverses a reparse point")

    def put_stream(
        self,
        source: BinaryIO,
        *,
        declared_media_type: str,
        clock: Clock,
        expected_digest: str | None = None,
        byte_size_hint: int | None = None,
        _allow_generated_svg: bool = False,
    ) -> ArtifactWrite:
        if byte_size_hint is not None and byte_size_hint < 0:
            raise ArtifactValidationError("artifact size hint cannot be negative")
        if byte_size_hint is not None and byte_size_hint > self._max_bytes:
            raise ArtifactTooLarge("artifact exceeds the configured byte limit")
        created_at = clock()
        if created_at.utcoffset() is None:
            raise ArtifactValidationError("artifact clock must be timezone-aware")
        if declared_media_type == "image/svg+xml" and not _allow_generated_svg:
            raise ArtifactValidationError("SVG artifacts are unsupported")
        if declared_media_type not in SUPPORTED_MEDIA_TYPES and not (
            _allow_generated_svg and declared_media_type in GENERATED_MEDIA_TYPES
        ):
            raise ArtifactValidationError("declared artifact media type is unsupported")

        self._temporary = self._safe_directory(self._temporary)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="artifact-", suffix=".tmp", dir=self._temporary
        )
        temporary_path = Path(temporary_name)
        try:
            temporary_resolved = temporary_path.resolve(strict=True)
            temporary_resolved.relative_to(self._root)
            if (
                not _same_path(temporary_path, temporary_resolved)
                or _is_reparse_or_symlink(temporary_path)
            ):
                raise ArtifactRootError(
                    "artifact temporary file traverses a reparse point"
                )
        except (OSError, RuntimeError, ValueError, ArtifactRootError) as error:
            os.close(descriptor)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(error, ArtifactRootError):
                raise
            raise ArtifactRootError(
                "artifact temporary file escaped its root"
            ) from error
        digest = hashlib.sha256()
        byte_size = 0
        try:
            try:
                with os.fdopen(descriptor, "wb") as target:
                    while True:
                        block = source.read(self._block_size)
                        if not isinstance(block, (bytes, bytearray, memoryview)):
                            raise ArtifactWriteError("artifact stream returned non-bytes")
                        if not block:
                            break
                        byte_size += len(block)
                        if byte_size > self._max_bytes:
                            raise ArtifactTooLarge(
                                "artifact exceeds the configured byte limit"
                            )
                        target.write(block)
                        digest.update(block)
                    target.flush()
                    os.fsync(target.fileno())
            except (ArtifactError, AssertionError):
                raise
            except Exception as error:
                raise ArtifactWriteError("artifact stream could not be written") from error
            if byte_size == 0:
                raise ArtifactValidationError("artifact cannot be empty")
            if byte_size_hint is not None and byte_size != byte_size_hint:
                raise ArtifactValidationError(
                    "artifact byte count does not match its size hint"
                )
            content_digest = digest.hexdigest()
            if expected_digest is not None and content_digest != expected_digest:
                raise ArtifactValidationError("artifact content digest does not match")
            if declared_media_type == "image/svg+xml":
                media_type, width, height = self._inspect_generated_svg(temporary_path)
            else:
                media_type, width, height = self._inspect_image(temporary_path)
            if media_type != declared_media_type:
                raise ArtifactValidationError(
                    "declared artifact media type does not match sniffed bytes"
                )
            metadata = ArtifactMetadata(
                artifact_id=f"artifact-{content_digest}",
                content_digest=content_digest,
                byte_size=byte_size,
                media_type=media_type,
                width=width,
                height=height,
                created_at=created_at,
            )
            final_path = self._object_path(content_digest)
            self._validate_object_location(final_path, must_exist=False)
            reused = False
            installed = False
            try:
                os.link(temporary_path, final_path)
                installed = True
                self._validate_object_location(final_path, must_exist=True)
            except FileExistsError:
                reused = True
                self._validate_object_location(final_path, must_exist=True)
                self._verify_file(final_path, metadata)
            except ArtifactRootError:
                if installed:
                    try:
                        final_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            except OSError as error:
                raise ArtifactWriteError("artifact could not be atomically installed") from error
            if not reused:
                self._verify_file(final_path, metadata)
            known = self._known_metadata.get(content_digest)
            if known is not None:
                if (
                    known.byte_size,
                    known.media_type,
                    known.width,
                    known.height,
                ) != (
                    metadata.byte_size,
                    metadata.media_type,
                    metadata.width,
                    metadata.height,
                ):
                    raise ArtifactWriteError(
                        "known artifact metadata does not match verified bytes"
                    )
                metadata = known
            else:
                self._known_metadata[content_digest] = metadata
            return ArtifactWrite(metadata=metadata, reused=reused)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def put_generated_svg(
        self,
        payload: bytes,
        *,
        clock: Clock,
        expected_digest: str | None = None,
    ) -> ArtifactWrite:
        """Store only a strict, inert SVG emitted by a trusted local generator."""

        if not isinstance(payload, bytes):
            raise ArtifactValidationError("generated SVG payload must be bytes")
        return self.put_stream(
            BytesIO(payload),
            declared_media_type="image/svg+xml",
            clock=clock,
            expected_digest=expected_digest,
            byte_size_hint=len(payload),
            _allow_generated_svg=True,
        )

    def _inspect_image(self, path: Path) -> tuple[str, int, int]:
        try:
            with path.open("rb") as source:
                prefix = source.read(512)
        except OSError as error:
            raise ArtifactWriteError("artifact bytes could not be inspected") from error
        stripped = prefix.lstrip().lower()
        if stripped.startswith(b"<svg") or b"<svg" in stripped[:256]:
            raise ArtifactValidationError("SVG artifacts are unsupported")
        sniffed: str | None
        if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            sniffed = "image/png"
        elif prefix.startswith(b"\xff\xd8\xff"):
            sniffed = "image/jpeg"
        elif len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
            sniffed = "image/webp"
        else:
            sniffed = None
        if sniffed is None:
            raise ArtifactValidationError("artifact is unsupported or corrupt image data")
        try:
            with Image.open(path) as image:
                width, height = image.size
                image_format = image.format
                if width < 1 or height < 1 or width * height > self._max_pixels:
                    raise ArtifactValidationError(
                        "artifact image dimensions exceed the pixel limit"
                    )
                image.verify()
        except ArtifactValidationError:
            raise
        except (
            Image.DecompressionBombError,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
        ) as error:
            raise ArtifactValidationError(
                "artifact is unsupported or corrupt image data"
            ) from error
        expected_format = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/webp": "WEBP",
        }[sniffed]
        if image_format != expected_format:
            raise ArtifactValidationError("artifact image format or dimensions are invalid")
        return sniffed, width, height

    def _inspect_generated_svg(self, path: Path) -> tuple[str, int, int]:
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ArtifactWriteError("generated SVG bytes could not be inspected") from error
        try:
            if payload.startswith(b"\xef\xbb\xbf"):
                raise ArtifactValidationError("generated SVG must use canonical UTF-8")
            root = ElementTree.fromstring(payload)
        except ArtifactValidationError:
            raise
        except Exception as error:
            raise ArtifactValidationError("generated SVG is invalid XML") from error
        if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
            raise ArtifactValidationError("generated SVG has an invalid root")
        try:
            width = int(root.attrib["width"])
            height = int(root.attrib["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactValidationError("generated SVG dimensions are invalid") from error
        if (
            width < 1
            or height < 1
            or width * height > self._max_pixels
            or root.attrib.get("viewBox") != f"0 0 {width} {height}"
            or root.attrib.get("role") != "img"
        ):
            raise ArtifactValidationError("generated SVG dimensions are invalid")
        title_ids: set[str] = set()
        for element in root.iter():
            if not isinstance(element.tag, str) or not element.tag.startswith(
                f"{{{_SVG_NAMESPACE}}}"
            ):
                raise ArtifactValidationError("generated SVG contains a foreign element")
            local_name = element.tag.rsplit("}", 1)[-1]
            allowed = _SVG_ALLOWED_ATTRIBUTES.get(local_name)
            if allowed is None or any(attribute not in allowed for attribute in element.attrib):
                raise ArtifactValidationError("generated SVG contains an unsafe element")
            for attribute, value in element.attrib.items():
                lowered = value.casefold()
                if (
                    len(value) > 2048
                    or attribute.casefold().startswith("on")
                    or "url(" in lowered
                    or "javascript:" in lowered
                    or "data:" in lowered
                    or "<" in value
                    or ">" in value
                ):
                    raise ArtifactValidationError("generated SVG contains an unsafe value")
            if element.text is not None and len(element.text) > 2048:
                raise ArtifactValidationError("generated SVG text is oversized")
            if local_name in {"title", "desc"} and element.attrib.get("id"):
                title_ids.add(element.attrib["id"])
        labelled = tuple(root.attrib.get("aria-labelledby", "").split())
        if not labelled or any(label not in title_ids for label in labelled):
            raise ArtifactValidationError("generated SVG accessible labels are invalid")
        return "image/svg+xml", width, height

    def _verify_file(self, path: Path, metadata: ArtifactMetadata) -> None:
        _ensure_regular_no_reparse(path, label="artifact object")
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(self._block_size), b""):
                    byte_size += len(block)
                    if byte_size > self._max_bytes:
                        raise ArtifactWriteError("stored artifact exceeds its byte limit")
                    digest.update(block)
        except ArtifactError:
            raise
        except OSError as error:
            raise ArtifactWriteError("stored artifact could not be verified") from error
        if byte_size != metadata.byte_size or digest.hexdigest() != metadata.content_digest:
            raise ArtifactWriteError("stored artifact bytes do not match metadata")

    def verify(self, metadata: ArtifactMetadata) -> bool:
        path = self._object_path(metadata.content_digest)
        if metadata.artifact_id != f"artifact-{metadata.content_digest}":
            raise ArtifactValidationError("artifact identity does not match its digest")
        self._validate_object_location(path, must_exist=True)
        self._verify_file(path, metadata)
        if metadata.media_type == "image/svg+xml":
            media_type, width, height = self._inspect_generated_svg(path)
        else:
            media_type, width, height = self._inspect_image(path)
        if (media_type, width, height) != (
            metadata.media_type,
            metadata.width,
            metadata.height,
        ):
            raise ArtifactWriteError("stored artifact media metadata does not match")
        return True

    def open_verified(self, metadata: ArtifactMetadata) -> BufferedReader:
        """Open verified server-side bytes without returning a path projection."""

        self.verify(metadata)
        return self._object_path(metadata.content_digest).open("rb")


__all__ = [
    "ArtifactError",
    "ArtifactMetadata",
    "ArtifactRootError",
    "ArtifactStore",
    "ArtifactTooLarge",
    "ArtifactValidationError",
    "ArtifactWrite",
    "ArtifactWriteError",
    "GENERATED_MEDIA_TYPES",
    "SUPPORTED_MEDIA_TYPES",
]
