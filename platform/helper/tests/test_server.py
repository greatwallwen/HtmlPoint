from __future__ import annotations

import hashlib
import json
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

    assert (
        pyproject["project"]["scripts"]["course-helper"] == "course_helper.server:main"
    )
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
    monkeypatch.setattr(
        server.webbrowser, "open", lambda url: opened.append(url) or True
    )
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
        lambda *_args, **_kwargs: pytest.fail(
            "uvicorn must not start after launch failure"
        ),
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


@pytest.mark.parametrize("present", ["policy", "executable"])
def test_partial_projection_install_fails_closed_without_starting_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    present: str,
) -> None:
    from course_helper import server

    reference_root = tmp_path / "references"
    reference_root.mkdir()
    app_data = tmp_path / "app-data"
    app_data.mkdir()
    executable = app_data / "projection-host" / "CourseStudio.ProjectionHost.exe"
    if present == "executable":
        executable.parent.mkdir()
        executable.write_bytes(b"host")
    else:
        (app_data / "projection-host-policy.json").write_text(
            json.dumps({"schemaVersion": 1, "hostSha256": "a" * 64}),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        server.uvicorn,
        "run",
        lambda *_args, **_kwargs: pytest.fail("partial install must not start server"),
    )

    result = server.main(
        [
            "--database",
            str(tmp_path / "knowledge.db"),
            "--app-data",
            str(app_data),
            "--reference-root",
            str(reference_root),
            "--web-origin",
            "http://127.0.0.1:4173",
        ]
    )

    assert result == 1
    assert str(tmp_path) not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("termination", "expected_result"),
    [("normal", 0), ("browser", 1), ("server", 1), ("interrupt", None)],
)
def test_server_owns_projection_supervisor_and_shuts_it_down_on_every_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    termination: str,
    expected_result: int | None,
) -> None:
    from course_helper import server

    reference_root = tmp_path / "references"
    reference_root.mkdir()
    app_data = tmp_path / "app-data"
    executable = app_data / "projection-host" / "CourseStudio.ProjectionHost.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"verified-host")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (app_data / "projection-host-policy.json").write_text(
        json.dumps({"schemaVersion": 1, "hostSha256": digest}),
        encoding="utf-8",
    )

    class FakeSupervisor:
        shutdown_count = 0

        def shutdown(self) -> None:
            self.shutdown_count += 1

    fake = FakeSupervisor()
    monkeypatch.setattr(
        server,
        "_create_projection_supervisor",
        lambda **_kwargs: fake,
    )
    monkeypatch.setattr(
        server.webbrowser,
        "open",
        lambda _url: termination != "browser",
    )

    def run_server(app: object, **_kwargs: object) -> None:
        assert app.state.runtime.projection_supervisor is fake
        if termination == "server":
            raise RuntimeError("private runtime detail")
        if termination == "interrupt":
            raise KeyboardInterrupt
        if termination == "browser":
            pytest.fail("server must not start after browser launch failure")

    monkeypatch.setattr(server.uvicorn, "run", run_server)
    arguments = [
        "--database",
        str(tmp_path / "knowledge.db"),
        "--app-data",
        str(app_data),
        "--reference-root",
        str(reference_root),
        "--web-origin",
        "http://127.0.0.1:4173",
    ]
    if termination == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            server.main(arguments)
        result = None
    else:
        result = server.main(arguments)

    captured = capsys.readouterr()
    assert result == expected_result
    assert fake.shutdown_count == 1
    assert "private runtime detail" not in captured.err
    assert str(tmp_path) not in captured.err


@pytest.mark.parametrize(
    "policy",
    [
        {"schemaVersion": 1, "hostSha256": "b" * 64},
        {"schemaVersion": 2, "hostSha256": "a" * 64},
        {"schemaVersion": 1, "hostSha256": "A" * 64},
        {"schemaVersion": 1, "hostSha256": "a" * 64, "extra": True},
    ],
)
def test_projection_policy_rejects_mismatch_or_nonexact_schema(
    tmp_path: Path,
    policy: dict[str, object],
) -> None:
    from course_helper import server

    app_data = tmp_path / "app-data"
    executable = app_data / "projection-host" / "CourseStudio.ProjectionHost.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"host")
    (app_data / "projection-host-policy.json").write_text(
        json.dumps(policy),
        encoding="utf-8",
    )

    with pytest.raises((ValueError, RuntimeError)):
        server._projection_policy_from_app_data(app_data)
