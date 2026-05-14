"""Tails `simctl spawn log stream` in a background thread and parses RESULT
lines emitted by the EvalApp's Signal.swift helper."""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import simctl


# Line format produced by Signal.swift:
#   RESULT pass scenario=tap_target size=80 ... elapsed_s=2.34
#   RESULT fail scenario=tap_target ... reason=timeout
_RESULT_RE = re.compile(r"RESULT\s+(pass|fail)\s+(.+)$")
_KV_RE = re.compile(r"(\w+)=(\S+)")


@dataclass
class Result:
    verdict: str            # "pass" | "fail"
    scenario: str
    fields: dict[str, str] = field(default_factory=dict)
    raw_line: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def __str__(self) -> str:
        kv = " ".join(f"{k}={v}" for k, v in sorted(self.fields.items()))
        return f"RESULT {self.verdict} scenario={self.scenario} {kv}"


def _parse(line: str) -> Result | None:
    m = _RESULT_RE.search(line)
    if not m:
        return None
    verdict = m.group(1)
    kv = dict(_KV_RE.findall(m.group(2)))
    scenario = kv.pop("scenario", "unknown")
    return Result(verdict=verdict, scenario=scenario, fields=kv, raw_line=line.rstrip())


class LogListener:
    """Background tail of the eval log stream.

    Usage:
        with LogListener() as listener:
            ...
            result = listener.wait_for_result(timeout=35)
    """

    def __init__(self, subsystem: str = "com.eval", udid: str = "booted"):
        self.subsystem = subsystem
        self.udid = udid
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[Result] = queue.Queue()
        self._raw_lines: list[str] = []
        self._stop = threading.Event()

    def __enter__(self) -> "LogListener":
        self._proc = subprocess.Popen(
            simctl.log_stream_cmd(self.subsystem, self.udid),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=simctl._env(),
            text=True,
            bufsize=1,
        )
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        # Give the stream a moment to attach so we don't miss the very first
        # log line emitted right after app launch.
        time.sleep(0.5)
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            if self._stop.is_set():
                return
            self._raw_lines.append(line)
            result = _parse(line)
            if result:
                self._queue.put(result)

    def wait_for_result(self, timeout: float) -> Result | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def raw_lines(self) -> list[str]:
        return list(self._raw_lines)
