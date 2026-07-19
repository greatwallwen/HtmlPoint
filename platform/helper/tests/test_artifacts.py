from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path

import pytest
from PIL import Image


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def png_bytes(*, width: int = 3, height: int = 2) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (width, height), color=(36, 99, 235)).save(
        stream, format="PNG"
    )
    return stream.getvalue()


class GuardedStream(BytesIO):
    def __init__(self, value: bytes, *, max_read: int) -> None:
        super().__init__(value)
        self.max_read = max_read
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self.max_read:
            raise AssertionError(f"unbounded read: {size}")
        self.read_sizes.append(size)
        return super().read(size)


def test_content_addressed_write_is_bounded_atomic_and_duplicate_safe(
    tmp_path: Path,
) -> None:
    from course_helper.artifacts import ArtifactStore

    payload = png_bytes()
    store = ArtifactStore(
        tmp_path / ".artifacts",
        max_bytes=1024 * 1024,
        block_size=7,
    )
    first_stream = GuardedStream(payload, max_read=7)
    first = store.put_stream(
        first_stream,
        declared_media_type="image/png",
        clock=lambda: NOW,
    )
    second = store.put_stream(
        GuardedStream(payload, max_read=7),
        declared_media_type="image/png",
        clock=lambda: NOW + timedelta(days=1),
    )

    assert first.reused is False
    assert second.reused is True
    assert second.metadata == first.metadata
    assert first.metadata.artifact_id.startswith("artifact-")
    assert first.metadata.byte_size == len(payload)
    assert first.metadata.media_type == "image/png"
    assert (first.metadata.width, first.metadata.height) == (3, 2)
    assert first_stream.read_sizes and set(first_stream.read_sizes) == {7}
    assert store.verify(first.metadata)
    serialized = first.metadata.model_dump(mode="json")
    assert not any("path" in key or "url" in key for key in serialized)
    files = tuple(path for path in (tmp_path / ".artifacts").rglob("*") if path.is_file())
    assert len(files) == 1
    assert not any("tmp" in path.name for path in files)


def test_oversized_or_midstream_failure_leaves_no_artifact_or_temporary_file(
    tmp_path: Path,
) -> None:
    from course_helper.artifacts import ArtifactStore, ArtifactTooLarge, ArtifactWriteError

    payload = png_bytes()
    root = tmp_path / "oversized"
    store = ArtifactStore(root, max_bytes=len(payload) - 1, block_size=8)
    with pytest.raises(ArtifactTooLarge):
        store.put_stream(
            GuardedStream(payload, max_read=8),
            declared_media_type="image/png",
            clock=lambda: NOW,
        )

    class BrokenStream(BytesIO):
        calls = 0

        def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls > 1:
                raise OSError("synthetic source failure")
            return super().read(size)

    second_root = tmp_path / "broken"
    with pytest.raises(ArtifactWriteError):
        ArtifactStore(second_root, max_bytes=1024, block_size=8).put_stream(
            BrokenStream(payload),
            declared_media_type="image/png",
            clock=lambda: NOW,
        )
    assert not any(path.is_file() for path in root.rglob("*"))
    assert not any(path.is_file() for path in second_root.rglob("*"))


@pytest.mark.parametrize(
    ("payload", "declared", "message"),
    (
        (b"not an image", "image/png", "unsupported or corrupt"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml", "SVG"),
        (png_bytes(), "image/jpeg", "media type"),
    ),
)
def test_rejects_corrupt_svg_and_declared_mime_mismatch(
    tmp_path: Path,
    payload: bytes,
    declared: str,
    message: str,
) -> None:
    from course_helper.artifacts import ArtifactValidationError, ArtifactStore

    with pytest.raises(ArtifactValidationError, match=message):
        ArtifactStore(tmp_path / declared.replace("/", "-")).put_stream(
            BytesIO(payload),
            declared_media_type=declared,
            clock=lambda: NOW,
        )


def test_trusted_generated_svg_is_strictly_validated_and_reused(tmp_path: Path) -> None:
    from course_helper.artifacts import ArtifactStore

    payload = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" '
        'viewBox="0 0 120 80" role="img" aria-labelledby="title desc">'
        '<title id="title">Chart</title><desc id="desc">Verified data.</desc>'
        '<rect x="0" y="0" width="120" height="80" fill="#F8FAFC"/>'
        '</svg>'
    ).encode("utf-8")
    store = ArtifactStore(tmp_path / "generated")

    first = store.put_generated_svg(payload, clock=lambda: NOW)
    second = store.put_generated_svg(
        payload, clock=lambda: NOW + timedelta(days=1)
    )

    assert first.metadata.media_type == "image/svg+xml"
    assert (first.metadata.width, first.metadata.height) == (120, 80)
    assert second.reused is True
    assert second.metadata == first.metadata
    assert store.verify(first.metadata)


def test_reparse_or_symlink_artifact_root_is_rejected(tmp_path: Path) -> None:
    from course_helper.artifacts import ArtifactRootError, ArtifactStore

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-artifacts"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(ArtifactRootError, match="reparse|symbolic"):
        ArtifactStore(link)


def test_detected_reparse_storage_component_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from course_helper import artifacts

    original = artifacts._is_reparse_or_symlink

    def detected(path: Path) -> bool:
        return path.name == "objects" or original(path)

    monkeypatch.setattr(artifacts, "_is_reparse_or_symlink", detected)
    with pytest.raises(artifacts.ArtifactRootError, match="reparse"):
        artifacts.ArtifactStore(tmp_path / "detected-reparse")


def test_naive_clock_and_invalid_size_hint_fail_before_writing(tmp_path: Path) -> None:
    from course_helper.artifacts import ArtifactStore, ArtifactValidationError

    store = ArtifactStore(tmp_path / "clock")
    with pytest.raises(ArtifactValidationError, match="timezone"):
        store.put_stream(
            BytesIO(png_bytes()),
            declared_media_type="image/png",
            clock=lambda: datetime(2026, 7, 18),
        )
    with pytest.raises(ArtifactValidationError, match="size hint"):
        store.put_stream(
            BytesIO(png_bytes()),
            declared_media_type="image/png",
            byte_size_hint=-1,
            clock=lambda: NOW,
        )
    assert not any(path.is_file() for path in (tmp_path / "clock").rglob("*"))
