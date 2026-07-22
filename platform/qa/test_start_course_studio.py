from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


SCRIPT = Path("platform/start-course-studio.ps1")
DOUBLE_CLICK_ENTRY = Path("platform/启动课程平台.cmd")
UNIX_SCRIPT = Path("platform/start-course-studio.sh")
MACOS_ENTRY = Path("platform/启动课程平台.command")


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


def _unix_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo_root = tmp_path / "中文 workspace with spaces"
    platform_root = repo_root / "platform"
    (platform_root / "web").mkdir(parents=True)
    (platform_root / "helper").mkdir()
    vite = platform_root / "web" / "node_modules" / ".bin" / "vite"
    vite.parent.mkdir(parents=True)
    vite.write_text("fixture", encoding="utf-8")
    vite.chmod(0o755)
    launcher = platform_root / UNIX_SCRIPT.name
    shutil.copyfile(UNIX_SCRIPT, launcher)

    bin_root = tmp_path / "fake-bin"
    bin_root.mkdir()
    log = tmp_path / "launcher.log"
    for name in ("npm", "uname"):
        stub = bin_root / name
        stub.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"{name}:$PWD:$*\" >> \"$LAUNCHER_LOG\"\n"
            + ("printf '%s\\n' \"$TEST_UNAME\"\n" if name == "uname" else ""),
            encoding="utf-8",
        )
        stub.chmod(0o755)
    for name in ("dirname", "mkdir"):
        executable = shutil.which(name)
        assert executable is not None
        (bin_root / name).symlink_to(executable)

    python = repo_root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = '-c' ]; then\n"
        "  printf '%s\\n' 'python-check' >> \"$LAUNCHER_LOG\"\n"
        "  [ \"${TEST_PYTHON_COMPAT:-1}\" = '1' ]\n"
        "  exit\n"
        "fi\n"
        "if [ \"${1:-}\" = '--version' ]; then\n"
        "  printf '%s\\n' \"${TEST_PYTHON_VERSION:-Python 3.11.9}\"\n"
        "  exit\n"
        "fi\n"
        "printf '%s\\n' \"python:$PWD:$*\" >> \"$LAUNCHER_LOG\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return launcher, bin_root, log


def _run_unix_launcher(
    tmp_path: Path, *, uname: str = "Darwin", arguments: tuple[str, ...] = ()
) -> tuple[subprocess.CompletedProcess[str], list[str], Path, Path]:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    home = tmp_path / "home with spaces"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_root}{os.pathsep}{os.environ.get('PATH', '')}",
        "LAUNCHER_LOG": str(log),
        "TEST_UNAME": uname,
        "TEST_PYTHON_COMPAT": "1",
    }
    result = subprocess.run(
        ["sh", str(launcher), *arguments],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, lines, home, launcher.parent


def test_unix_launcher_builds_missing_web_and_starts_helper_on_macos(
    tmp_path: Path,
) -> None:
    result, calls, home, platform_root = _run_unix_launcher(tmp_path)
    app_data = home / "Library" / "Application Support" / "CourseStudio"

    assert result.returncode == 0, result.stderr
    assert calls == [
        "python-check",
        "uname:" + str(tmp_path) + ":-s",
        f"npm:{tmp_path}:--prefix {platform_root / 'web'} run build",
        "python:"
        + str(platform_root / "helper")
        + ":-m course_helper"
        + f" --database {app_data / 'knowledge.db'}"
        + f" --app-data {app_data}"
        + f" --reference-root {app_data / 'sources'}"
        + " --web-origin http://127.0.0.1:8765"
        + f" --web-root {platform_root / 'web' / 'dist'}"
        + " --port 8765",
    ]
    assert (app_data / "sources").is_dir()
    assert "中文 workspace with spaces" in calls[-1]


def test_unix_launcher_skips_build_when_manifest_exists(tmp_path: Path) -> None:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    manifest = launcher.parent / "web" / "dist" / ".vite" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["/bin/sh", str(launcher)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_root}{os.pathsep}{os.environ.get('PATH', '')}",
            "LAUNCHER_LOG": str(log),
            "TEST_UNAME": "Darwin",
            "TEST_PYTHON_COMPAT": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("npm:") for call in log.read_text().splitlines())


def test_unix_launcher_rejects_parameters_without_running_commands(
    tmp_path: Path,
) -> None:
    result, calls, _home, _platform_root = _run_unix_launcher(
        tmp_path, arguments=("--command", "anything")
    )

    assert result.returncode == 64
    assert "usage: platform/start-course-studio.sh" in result.stderr
    assert calls == []


def test_unix_launcher_uses_xdg_data_home_on_linux(tmp_path: Path) -> None:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    home = tmp_path / "home"
    xdg_data = tmp_path / "xdg data"
    home.mkdir()
    result = subprocess.run(
        ["/bin/sh", str(launcher)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(home),
            "XDG_DATA_HOME": str(xdg_data),
            "PATH": f"{bin_root}{os.pathsep}{os.environ.get('PATH', '')}",
            "LAUNCHER_LOG": str(log),
            "TEST_UNAME": "Linux",
            "TEST_PYTHON_COMPAT": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (xdg_data / "CourseStudio" / "sources").is_dir()
    helper_call = next(
        call for call in log.read_text().splitlines() if call.startswith("python:")
    )
    assert f"--app-data {xdg_data / 'CourseStudio'}" in helper_call


def test_macos_double_click_entry_invokes_only_fixed_shell_launcher() -> None:
    assert MACOS_ENTRY.is_file(), "macOS double-click entry must exist"
    assert MACOS_ENTRY.stat().st_mode & 0o111, ".command entry must be executable"
    entry = MACOS_ENTRY.read_text(encoding="utf-8")

    assert 'exec "$script_dir/start-course-studio.sh"' in entry
    assert '"$@"' not in entry
    assert "eval" not in entry
    assert "source" not in entry


def test_unix_launcher_fails_with_install_steps_when_venv_is_missing(
    tmp_path: Path,
) -> None:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    (launcher.parent.parent / ".venv" / "bin" / "python").unlink()

    result = subprocess.run(
        ["/bin/sh", str(launcher)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": str(bin_root),
            "LAUNCHER_LOG": str(log),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Python environment not found" in result.stderr
    assert "python3 -m venv .venv" in result.stderr
    assert "pip install -e" in result.stderr
    assert not log.exists()


def test_unix_launcher_rejects_incompatible_venv_python(tmp_path: Path) -> None:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    result = subprocess.run(
        ["/bin/sh", str(launcher)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": str(bin_root),
            "LAUNCHER_LOG": str(log),
            "TEST_PYTHON_COMPAT": "0",
            "TEST_PYTHON_VERSION": "Python 3.11.9",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "must use Python 3.12" in result.stderr
    assert "Python 3.11.9" in result.stderr
    assert "python3.12 -m venv .venv" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["python-check"]


def test_unix_launcher_fails_closed_with_install_step_when_npm_is_missing(
    tmp_path: Path,
) -> None:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    (bin_root / "npm").unlink()
    result = subprocess.run(
        ["/bin/sh", str(launcher)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": str(bin_root),
            "LAUNCHER_LOG": str(log),
            "TEST_PYTHON_COMPAT": "1",
            "TEST_UNAME": "Darwin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "npm was not found on PATH" in result.stderr
    assert "npm --prefix platform/web ci" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "python-check",
        f"uname:{tmp_path}:-s",
    ]


def test_unix_launcher_fails_closed_when_web_dependencies_are_missing(
    tmp_path: Path,
) -> None:
    launcher, bin_root, log = _unix_workspace(tmp_path)
    (launcher.parent / "web" / "node_modules" / ".bin" / "vite").unlink()
    result = subprocess.run(
        ["/bin/sh", str(launcher)],
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": str(bin_root),
            "LAUNCHER_LOG": str(log),
            "TEST_PYTHON_COMPAT": "1",
            "TEST_UNAME": "Darwin",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Web dependencies are not installed" in result.stderr
    assert "npm --prefix platform/web ci" in result.stderr
    assert not any(
        call.startswith(("npm:", "python:"))
        for call in log.read_text(encoding="utf-8").splitlines()
    )
