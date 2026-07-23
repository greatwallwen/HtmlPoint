from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"


def _port_is_open() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((HOST, PORT)) == 0


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_page(process: subprocess.Popen[str], timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"launcher exited before HTTP readiness ({process.returncode})\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        try:
            with urllib.request.urlopen(URL, timeout=0.5) as response:
                return response.status
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"launcher did not serve {URL} within {timeout:.0f}s")


def _terminate(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    os.killpg(process.pid, signal.SIGINT)
    try:
        stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        raise RuntimeError("launcher did not exit after SIGINT")
    return process.returncode, stdout, stderr


def run_smoke(repo_root: Path) -> dict[str, object]:
    if _port_is_open():
        raise RuntimeError(f"{URL} is already in use")
    launcher = repo_root / "platform" / "start-course-studio.sh"
    if not launcher.is_file():
        raise RuntimeError("launcher is missing")
    with tempfile.TemporaryDirectory(prefix="课程 Studio smoke ") as temporary:
        home = Path(temporary) / "Home with spaces"
        home.mkdir()
        environment = {
            **os.environ,
            "HOME": str(home),
            # Python's existing webbrowser integration treats this as a successful no-GUI opener.
            "BROWSER": "/usr/bin/true",
        }
        process = subprocess.Popen(
            [str(launcher)],
            cwd="/",
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            http_status = _wait_for_page(process, 30)
            exit_code, _stdout, stderr = _terminate(process)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while _port_is_open() and time.monotonic() < deadline:
            time.sleep(0.1)
        port_released = not _port_is_open()
        process_group_released = not _process_group_exists(process.pid)
        if exit_code != 0 or http_status != 200 or not port_released or not process_group_released:
            raise RuntimeError(
                "smoke verification failed: "
                f"exit={exit_code}, http={http_status}, "
                f"port_released={port_released}, "
                f"process_group_released={process_group_released}, stderr={stderr!r}"
            )
        return {
            "schemaVersion": 1,
            "status": "verified",
            "platform": "darwin",
            "url": URL,
            "httpStatus": http_status,
            "terminationSignal": "SIGINT",
            "exitCode": exit_code,
            "portReleased": port_released,
            "processGroupReleased": process_group_released,
            "browserMode": "suppressed-with-standard-BROWSER-opener",
            "physicalDualScreenCertified": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    receipt = run_smoke(repo_root)
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.evidence is not None:
        evidence = args.evidence.resolve()
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
