"""Thin wrappers around `xcrun simctl`. All calls go through `_run`, which
sets DEVELOPER_DIR so the harness works even when xcode-select still points
at CommandLineTools."""

from __future__ import annotations

import json
import os
import subprocess

DEVELOPER_DIR = "/Applications/Xcode.app/Contents/Developer"


def _env() -> dict:
    env = os.environ.copy()
    env["DEVELOPER_DIR"] = DEVELOPER_DIR
    return env


def _run(args: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["xcrun", "simctl", *args],
        env=_env(),
        check=check,
        capture_output=capture,
        text=True,
    )


def booted_udid() -> str:
    """Return the UDID of the currently booted simulator. Raises if none."""
    out = _run(["list", "devices", "booted", "-j"]).stdout
    data = json.loads(out)
    for _, devices in data.get("devices", {}).items():
        for d in devices:
            if d.get("state") == "Booted":
                return d["udid"]
    raise RuntimeError("No booted simulator found. Boot one in Simulator.app first.")


def uninstall(bundle_id: str, udid: str = "booted") -> None:
    # Allowed to fail if app isn't installed.
    _run(["uninstall", udid, bundle_id], check=False)


def install(app_path: str, udid: str = "booted") -> None:
    _run(["install", udid, app_path])


def terminate(bundle_id: str, udid: str = "booted") -> None:
    _run(["terminate", udid, bundle_id], check=False)


def launch(bundle_id: str, args: list[str] | None = None, udid: str = "booted") -> int:
    """Launch the app with optional UserDefaults-style args. Returns PID."""
    cmd = ["launch", udid, bundle_id]
    if args:
        cmd.extend(args)
    out = _run(cmd).stdout.strip()
    # Output format: "<bundle_id>: <pid>"
    return int(out.rsplit(":", 1)[1].strip())


def log_stream_cmd(subsystem: str, udid: str = "booted") -> list[str]:
    """Return argv for streaming log lines filtered by subsystem.

    Returned as argv so the caller can wire stdout however it wants
    (Popen, asyncio subprocess, etc.).
    """
    return [
        "xcrun", "simctl", "spawn", udid, "log", "stream",
        "--predicate", f'subsystem == "{subsystem}"',
        "--style", "compact",
    ]
