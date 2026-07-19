import hashlib
from pathlib import Path
from uuid import uuid5

import pytest

from course_helper.domain.common import SourceLocator
from course_helper.domain.sources import ChunkLocator
from course_helper.source_roots import (
    COURSE_STUDIO_ID_NAMESPACE,
    SourceRootRegistry,
    SourceRootViolation,
    candidate_logical_id,
    candidate_version_id,
    chunk_logical_id,
    chunk_version_id,
    quick_fingerprint,
    source_logical_id,
    source_version_id,
    stream_sha256,
)


def test_registry_resolves_only_files_inside_registered_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    expected = root / "ok.md"
    expected.write_text("ok", encoding="utf-8")
    registry = SourceRootRegistry({"demo": root})

    assert registry.resolve(SourceLocator(root_id="demo", relative_path="ok.md")) == expected

    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    with pytest.raises(SourceRootViolation):
        registry.resolve(SourceLocator(root_id="demo", relative_path="linked.md"))


def test_registry_rejects_unknown_roots_missing_files_and_directories(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "folder").mkdir()
    registry = SourceRootRegistry({"demo": root})

    with pytest.raises(SourceRootViolation, match="not registered"):
        registry.resolve(SourceLocator(root_id="unknown", relative_path="file.md"))
    with pytest.raises(SourceRootViolation, match="does not exist"):
        registry.resolve(SourceLocator(root_id="demo", relative_path="missing.md"))
    with pytest.raises(SourceRootViolation, match="regular file"):
        registry.resolve(SourceLocator(root_id="demo", relative_path="folder"))


def test_registry_resolves_only_directories_inside_registered_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    source = root / "source.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    registry = SourceRootRegistry({"demo": root})

    assert registry.resolve_directory(
        SourceLocator(root_id="demo", relative_path=".")
    ) == root
    assert registry.resolve_directory(
        SourceLocator(root_id="demo", relative_path="nested")
    ) == nested
    with pytest.raises(SourceRootViolation, match="directory"):
        registry.resolve_directory(
            SourceLocator(root_id="demo", relative_path="source.csv")
        )
    with pytest.raises(SourceRootViolation, match="does not exist"):
        registry.resolve_directory(
            SourceLocator(root_id="demo", relative_path="missing")
        )
    with pytest.raises(SourceRootViolation, match="not registered"):
        registry.resolve_directory(
            SourceLocator(root_id="unknown", relative_path="nested")
        )


def test_registry_directory_resolution_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked-directory"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this Windows host")
    registry = SourceRootRegistry({"demo": root})

    with pytest.raises(SourceRootViolation, match="escapes"):
        registry.resolve_directory(
            SourceLocator(root_id="demo", relative_path="linked-directory")
        )


def test_registry_requires_existing_directory_roots(tmp_path: Path) -> None:
    file_root = tmp_path / "file.txt"
    file_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SourceRootViolation, match="does not exist"):
        SourceRootRegistry({"missing": tmp_path / "missing"})
    with pytest.raises(SourceRootViolation, match="directory"):
        SourceRootRegistry({"file": file_root})


def test_quick_fingerprint_uses_metadata_without_opening_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "fixture.bin"
    source.write_bytes(b"metadata-only")
    stat = source.stat()

    def fail_if_opened(*args: object, **kwargs: object) -> None:
        raise AssertionError("quick_fingerprint must not read file bytes")

    monkeypatch.setattr(Path, "open", fail_if_opened)

    fingerprint = quick_fingerprint(source)

    assert fingerprint.byte_size == stat.st_size
    assert fingerprint.modified_ns == stat.st_mtime_ns


def test_stream_sha256_hashes_more_than_one_chunk(tmp_path: Path) -> None:
    payload = (b"course-studio" * 100_000) + b"tail"
    source = tmp_path / "fixture.bin"
    source.write_bytes(payload)

    assert len(payload) > 1024 * 1024
    assert stream_sha256(source) == hashlib.sha256(payload).hexdigest()


def test_source_ids_follow_the_normalized_locator_and_digest_formula() -> None:
    locator = SourceLocator(root_id="demo", relative_path=r"slides\unit-1\AI.pptx")
    digest = "a" * 64

    logical_id = source_logical_id(locator)
    version_id = source_version_id(logical_id, digest)

    assert logical_id == str(
        uuid5(COURSE_STUDIO_ID_NAMESPACE, "demo\0slides/unit-1/AI.pptx")
    )
    assert version_id == str(uuid5(COURSE_STUDIO_ID_NAMESPACE, f"{logical_id}\0{digest}"))
    assert source_logical_id(
        SourceLocator(root_id="demo", relative_path="slides/unit-1/AI.pptx")
    ) == logical_id
    assert source_version_id(logical_id, "b" * 64) != version_id


def test_chunk_ids_use_canonical_locator_parent_version_and_digest() -> None:
    locator = ChunkLocator(kind="pptx-slide", slide_number=2, relationship_id="rId4")
    source_logical = "source-logical"
    source_version = "source-v2"

    logical_id = chunk_logical_id(source_logical, locator)
    version_id = chunk_version_id(logical_id, source_version, "c" * 64)

    assert chunk_logical_id(source_logical, locator.model_copy()) == logical_id
    assert chunk_logical_id(
        source_logical,
        locator.model_copy(update={"slide_number": 3}),
    ) != logical_id
    assert chunk_version_id(logical_id, "source-v1", "c" * 64) != version_id
    assert chunk_version_id(logical_id, source_version, "d" * 64) != version_id


def test_candidate_ids_are_typed_and_canonicalize_parent_order() -> None:
    digest = "e" * 64
    visual = candidate_logical_id("visual", "slide-2:rId4")
    dataset = candidate_logical_id("dataset", "slide-2:rId4")
    card = candidate_logical_id("card", "concept:model-boundaries")

    assert len({visual, dataset, card}) == 3
    assert candidate_version_id(visual, ("source-v2", "chunk-v2"), digest) == candidate_version_id(
        visual,
        ("chunk-v2", "source-v2"),
        digest,
    )
    assert candidate_version_id(visual, ("source-v3", "chunk-v2"), digest) != candidate_version_id(
        visual,
        ("source-v2", "chunk-v2"),
        digest,
    )
