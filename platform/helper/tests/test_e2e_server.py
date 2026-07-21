from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

from course_helper.e2e_server import _write_fixtures


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_e2e_pptx_fixture_is_byte_stable_and_has_canonical_zip_timestamps(
    tmp_path: Path,
) -> None:
    first = Path(_write_fixtures(tmp_path / "first")["pptx"])
    second = Path(_write_fixtures(tmp_path / "second")["pptx"])

    assert _sha256(first) == _sha256(second)
    with ZipFile(first) as archive:
        assert {member.date_time for member in archive.infolist()} == {
            (2000, 1, 1, 0, 0, 0)
        }
