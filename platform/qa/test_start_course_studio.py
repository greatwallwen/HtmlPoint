from __future__ import annotations

import re
from pathlib import Path


SCRIPT = Path("platform/start-course-studio.ps1")
DOUBLE_CLICK_ENTRY = Path("platform/启动课程平台.cmd")


def _script() -> str:
    assert SCRIPT.is_file(), "personal product launcher must exist"
    return SCRIPT.read_text(encoding="utf-8-sig")


def test_launcher_is_fixed_to_workspace_and_local_app_data() -> None:
    script = _script()

    assert "$PSScriptRoot" in script
    assert "$env:LOCALAPPDATA" in script
    assert "Join-Path $platformRoot 'web'" in script
    assert "Join-Path $platformRoot 'helper'" in script
    assert "-LiteralPath" in script


def test_launcher_builds_web_and_starts_same_origin_helper() -> None:
    script = _script()

    assert "npm.cmd --prefix $webRoot run build" in script
    assert "python -m course_helper" in script
    assert "--web-origin 'http://127.0.0.1:8765'" in script
    assert "--web-root $dist" in script
    assert "--port 8765" in script


def test_launcher_accepts_no_command_or_secret_parameters() -> None:
    script = _script()

    assert re.search(r"(?im)^\s*param\s*\(", script) is None
    assert "Invoke-Expression" not in script
    assert "--command" not in script
    assert "--host" not in script
    assert "nonce" not in script.casefold()
    assert "token" not in script.casefold()


def test_double_click_entry_invokes_only_the_fixed_launcher() -> None:
    assert DOUBLE_CLICK_ENTRY.is_file(), "Win11 double-click entry must exist"
    entry = DOUBLE_CLICK_ENTRY.read_text(encoding="utf-8-sig")

    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File" in entry
    assert '"%~dp0start-course-studio.ps1"' in entry
    assert "%*" not in entry
    assert "Invoke-Expression" not in entry
