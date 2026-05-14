"""Client that controls a booted iOS simulator by clicking on the
Simulator.app macOS window with pyautogui.

Use this instead of `SimulatorClient` (Appium/WDA) when:
  - You're targeting SwiftUI apps whose gestures don't recognize WDA's
    synthetic taps (iOS 26+ has this issue).
  - You want to keep the simulator's ground-truth machinery (`simctl`
    install/launch, log stream signals) but exercise the same vision +
    coordinate-translation stack the mirror-mode agent uses.

Coordinates everywhere are in **iOS points** (e.g. 402×874 for iPhone 17 Pro).
Screenshots are returned at full device pixel resolution (via `simctl io`).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time

import pyautogui


_TITLE_BAR = 28  # macOS Simulator window title bar height in px.
_DEFAULT_DEVELOPER_DIR = "/Applications/Xcode.app/Contents/Developer"

# (width_pt, height_pt) for known device types. Add entries as needed; or
# override at runtime via `SIMULATOR_DEVICE_POINTS=widthxheight` env var.
_KNOWN_DEVICE_POINTS: dict[str, tuple[int, int]] = {
    "iPhone 17 Pro": (402, 874),
    "iPhone 17 Pro Max": (440, 956),
    "iPhone 17": (402, 874),
    "iPhone 16 Pro": (402, 874),
    "iPhone 16 Pro Max": (440, 956),
    "iPhone 16": (393, 852),
    "iPhone 15 Pro": (393, 852),
    "iPhone 15": (393, 852),
    "iPhone SE (3rd generation)": (375, 667),
}


def _env() -> dict:
    env = os.environ.copy()
    env.setdefault("DEVELOPER_DIR", _DEFAULT_DEVELOPER_DIR)
    return env


def _simctl(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["xcrun", "simctl", *args], env=_env(), text=True, capture_output=True, **kw)


def _osascript(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()


def _booted_device() -> tuple[str, str]:
    """Return (udid, device_name) of the booted simulator."""
    out = _simctl(["list", "devices", "booted", "-j"]).stdout
    data = json.loads(out)
    for _, devices in data.get("devices", {}).items():
        for d in devices:
            if d.get("state") == "Booted":
                return d["udid"], d.get("name", "")
    raise RuntimeError("No booted simulator found.")


def _device_points(name: str) -> tuple[int, int]:
    """Resolve device size in iOS points. Honor SIMULATOR_DEVICE_POINTS env override."""
    override = os.environ.get("SIMULATOR_DEVICE_POINTS")
    if override:
        w, h = override.lower().split("x")
        return int(w), int(h)
    if name in _KNOWN_DEVICE_POINTS:
        return _KNOWN_DEVICE_POINTS[name]
    raise RuntimeError(
        f"Unknown device '{name}'. Add it to _KNOWN_DEVICE_POINTS or set "
        f"SIMULATOR_DEVICE_POINTS=widthxheight."
    )


def _sim_window_geometry() -> tuple[int, int, int, int]:
    """Return (x, y, w, h) of Simulator.app's window 1 in macOS screen pixels."""
    pos = _osascript(
        'tell application "System Events" to tell process "Simulator" to get position of window 1'
    )
    size = _osascript(
        'tell application "System Events" to tell process "Simulator" to get size of window 1'
    )
    if not pos or not size:
        raise RuntimeError(
            "Could not read Simulator window geometry. Is Simulator.app open and the device window visible?"
        )
    px, py = [int(v) for v in pos.split(", ")]
    sw, sh = [int(v) for v in size.split(", ")]
    return px, py, sw, sh


class SimulatorScreenClient:
    """Same surface as SimulatorClient, but taps land via the macOS window
    instead of WDA — required for SwiftUI on iOS 26 simulators."""

    def __init__(self, udid: str | None = None):
        self._udid = udid
        self._device_name: str = ""
        self._device_pts: tuple[int, int] = (0, 0)
        self._window: tuple[int, int, int, int] = (0, 0, 0, 0)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        pass

    def connect(self):
        if not self._udid:
            self._udid, self._device_name = _booted_device()
        else:
            _, self._device_name = _booted_device()
        self._device_pts = _device_points(self._device_name)
        # Intentionally do NOT activate Simulator.app — that would steal focus
        # from the user's foreground work. pyautogui clicks route by absolute
        # screen coords, so they reach the simulator window as long as it
        # isn't fully obscured. Caller must keep the simulator visible (but
        # not necessarily focused).
        self._window = _sim_window_geometry()

    def _refresh_window(self):
        self._window = _sim_window_geometry()

    def _scale(self) -> tuple[float, float]:
        """macOS pixels per iOS point, horizontally and vertically."""
        _, _, ww, wh = self._window
        dw, dh = self._device_pts
        return ww / dw, (wh - _TITLE_BAR) / dh

    def _abs(self, x: float, y: float) -> tuple[int, int]:
        """Convert iOS-point coords to absolute macOS screen pixel coords."""
        wx, wy, _, _ = self._window
        sx, sy = self._scale()
        return int(wx + x * sx), int(wy + _TITLE_BAR + y * sy)

    # --- public surface (matches SimulatorClient/MirrorClient) ---

    def get_screen_size(self) -> tuple[int, int]:
        return self._device_pts

    def screenshot(self) -> bytes:
        """Full device-pixel-resolution PNG via `simctl io screenshot`."""
        proc = subprocess.run(
            ["xcrun", "simctl", "io", self._udid, "screenshot", "-"],
            env=_env(),
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"simctl screenshot failed: {proc.stderr.decode(errors='replace')}")
        return proc.stdout

    def tap(self, x: float, y: float):
        self._refresh_window()
        ax, ay = self._abs(x, y)
        pyautogui.click(ax, ay)

    def swipe(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 500,
    ):
        self._refresh_window()
        sx, sy = self._abs(start_x, start_y)
        ex, ey = self._abs(end_x, end_y)
        pyautogui.moveTo(sx, sy)
        pyautogui.dragTo(ex, ey, duration=duration_ms / 1000.0, button="left")

    def type_text(self, text: str):
        self._refresh_window()
        # Typing genuinely needs keyboard focus — pyautogui.typewrite sends
        # keystrokes to whatever app has focus, so we steal it here.
        # Tap-only scenarios stay focus-preserving.
        subprocess.run(
            ["osascript", "-e", 'tell application "Simulator" to activate'],
            capture_output=True,
        )
        time.sleep(0.1)
        pyautogui.typewrite(text, interval=0.02)

    def get_source(self) -> str:
        raise NotImplementedError(
            "screen mode has no XCUITest element tree. Use --vision-only flows."
        )
