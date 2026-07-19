from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


def test_console_entry_and_cli_expose_no_host_or_command_option() -> None:
    from course_helper import __main__ as module_entry
    from course_helper.server import build_parser, main

    pyproject = tomllib.loads(
        Path("platform/helper/pyproject.toml").read_text(encoding="utf-8")
    )
    help_text = build_parser().format_help()

    assert pyproject["project"]["scripts"]["course-helper"] == "course_helper.server:main"
    assert module_entry.main is main
    assert "--host" not in help_text
    assert "--command" not in help_text


def test_module_and_console_target_help_share_the_restricted_cli() -> None:
    helper_root = Path("platform/helper").resolve()
    module_help = subprocess.run(
        [sys.executable, "-m", "course_helper", "--help"],
        cwd=helper_root,
        capture_output=True,
        text=True,
        check=False,
    )
    console_target_help = subprocess.run(
        [
            sys.executable,
            "-c",
            "from course_helper.server import main; main(['--help'])",
        ],
        cwd=helper_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert module_help.returncode == 0
    assert console_target_help.returncode == 0
    assert module_help.stdout == console_target_help.stdout
    assert "--host" not in module_help.stdout
    assert "--command" not in module_help.stdout
    assert module_help.stderr == console_target_help.stderr == ""


def test_server_fixes_loopback_host_and_opens_fragment_with_only_helper_and_nonce(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from course_helper import server

    reference_root = tmp_path / "references"
    reference_root.mkdir()
    opened: list[str] = []
    uvicorn_calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda app, **kwargs: uvicorn_calls.append((app, kwargs)),
    )

    result = server.main(
        [
            "--database",
            str(tmp_path / "knowledge.db"),
            "--app-data",
            str(tmp_path / "app-data"),
            "--reference-root",
            str(reference_root),
            "--web-origin",
            "http://127.0.0.1:4173",
            "--port",
            "8765",
        ]
    )

    assert result == 0
    assert uvicorn_calls[0][1]["host"] == "127.0.0.1"
    assert uvicorn_calls[0][1]["port"] == 8765
    fragment = parse_qs(urlsplit(opened[0]).fragment)
    assert set(fragment) == {"helper", "nonce"}
    assert fragment["helper"] == ["http://127.0.0.1:8765"]
    assert len(fragment["nonce"][0]) >= 43
    assert "token" not in opened[0].casefold()


def test_browser_launch_failure_aborts_with_redacted_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    from course_helper import server

    reference_root = tmp_path / "references"
    reference_root.mkdir()
    monkeypatch.setattr(server.webbrowser, "open", lambda _url: False)
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda *_args, **_kwargs: pytest.fail("uvicorn must not start after launch failure"),
    )
    launch_material = iter(
        (
            "secret-launch-material-nonce-that-must-never-appear-12345",
            "secret-launch-material-token-that-must-never-appear-67890",
        )
    )
    monkeypatch.setattr(
        "course_helper.session.secrets.token_urlsafe",
        lambda _size: next(launch_material),
    )

    result = server.main(
        [
            "--database",
            str(tmp_path / "knowledge.db"),
            "--app-data",
            str(tmp_path / "app-data"),
            "--reference-root",
            str(reference_root),
            "--web-origin",
            "http://127.0.0.1:4173",
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert result == 1
    assert captured.out == ""
    assert "browser launch failed" in captured.err
    assert "secret-launch-material" not in combined
    assert "helper=" not in combined
    assert "nonce=" not in combined
    assert str(tmp_path) not in combined
