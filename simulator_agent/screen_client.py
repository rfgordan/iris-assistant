"""Client that controls a booted iOS simulator by clicking on the
Simulator.app macOS window with pyautogui.

Use this instead of `SimulatorClient` (Appium/WDA) when:
  - You're targeting SwiftUI apps whose gestures don't recognize WDA's
    synthetic taps (iOS 26+ has this issue).
  - You want to keep the simulator's ground-truth machinery (`simctl`
    install/launch, log stream signals) but exercise the same vision +
    coordinate-translation stack the mirror-mode agent uses.

Input model:
  Tap/swipe/scroll briefly activate Simulator.app, then post mouse events
  via pyautogui (which uses CGEventPost at the HID tap). Activation is
  needed because pyautogui posts at the SESSION level, where events are
  routed to the frontmost process at the click coords.

  We tried bypassing focus with `CGEventPostToPid` directly targeted at the
  Simulator process — that would have made input multi-sim-parallel-friendly
  and avoided focus theft. The events were posted successfully but did not
  reach SwiftUI gesture recognizers; the simulator (or AppKit) appears to
  filter out events delivered through that path. Quartz helpers are kept
  below for reference. A future attempt could explore: (a) HID-level event
  injection via IOHIDPost, (b) accessibility APIs (AXUIElementPerformAction)
  targeted at sim windows, or (c) a private Apple framework like SyntheticUI.

  Typing requires focus regardless of method — keystrokes always go to the
  app with keyboard focus.

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
import Quartz


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


# --- Quartz CGEventPostToPid helpers (kept for reference) -----------------
# These were intended to bypass macOS focus-routing by posting events
# directly to the Simulator.app process. In practice, events posted via
# CGEventPostToPid did not reach SwiftUI gesture recognizers — they appear
# to be filtered out somewhere between the per-process tap and the iOS
# touch dispatcher. Left here for the next attempt at focus-free input.
def _simulator_pid() -> int:
    proc = subprocess.run(["pgrep", "-x", "Simulator"], capture_output=True, text=True)
    pids = [int(p) for p in proc.stdout.split()]
    if not pids:
        raise RuntimeError("Simulator.app is not running.")
    return pids[0]


def _quartz_post_mouse(pid: int, event_type, x: float, y: float) -> None:
    event = Quartz.CGEventCreateMouseEvent(
        None, event_type, (x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPostToPid(pid, event)


def _quartz_click(pid: int, x: float, y: float, hold_ms: int = 50) -> None:
    _quartz_post_mouse(pid, Quartz.kCGEventLeftMouseDown, x, y)
    time.sleep(hold_ms / 1000.0)
    _quartz_post_mouse(pid, Quartz.kCGEventLeftMouseUp, x, y)


def _quartz_drag(pid: int, sx: float, sy: float, ex: float, ey: float, duration_s: float) -> None:
    duration_s = max(0.05, duration_s)
    steps = max(8, int(duration_s * 60))
    _quartz_post_mouse(pid, Quartz.kCGEventLeftMouseDown, sx, sy)
    dt = duration_s / steps
    for i in range(1, steps + 1):
        t = i / steps
        x = sx + (ex - sx) * t
        y = sy + (ey - sy) * t
        _quartz_post_mouse(pid, Quartz.kCGEventLeftMouseDragged, x, y)
        time.sleep(dt)
    _quartz_post_mouse(pid, Quartz.kCGEventLeftMouseUp, ex, ey)


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
        self._sim_pid: int = 0

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
        self._sim_pid = _simulator_pid()
        # connect() doesn't activate. Each input method activates before
        # sending — pyautogui events are session-level and would otherwise
        # land on whatever's frontmost at the click coords. See the module
        # docstring for the focus-free-injection follow-up.
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

    def _activate_simulator(self):
        """Bring Simulator.app to the front so pyautogui events land on it."""
        subprocess.run(
            ["osascript", "-e", 'tell application "Simulator" to activate'],
            capture_output=True,
        )
        time.sleep(0.1)

    def tap(self, x: float, y: float):
        self._refresh_window()
        self._activate_simulator()
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
        self._activate_simulator()
        sx, sy = self._abs(start_x, start_y)
        ex, ey = self._abs(end_x, end_y)
        pyautogui.moveTo(sx, sy)
        pyautogui.dragTo(ex, ey, duration=duration_ms / 1000.0, button="left")

    def scroll(self, amount: int, x: float | None = None, y: float | None = None):
        """Scroll content by simulating a swipe. macOS scroll-wheel events
        don't translate to iOS scrolls, so we use a vertical drag.

        Sign convention matches scroll wheels:
          amount > 0  → reveal content below (swipe up)
          amount < 0  → reveal content above (swipe down)

        Each "click" is roughly 30 points of swipe distance. Pass (x, y) in
        iOS points to center the swipe over a specific scrollable region.
        """
        self._refresh_window()
        self._activate_simulator()
        dw, dh = self._device_pts
        cx = x if x is not None else dw / 2
        cy = y if y is not None else dh / 2

        per_click = 30
        distance = max(40, min(dh - 100, abs(amount) * per_click))
        half = distance / 2

        if amount > 0:
            start = (cx, cy + half)
            end = (cx, cy - half)
        else:
            start = (cx, cy - half)
            end = (cx, cy + half)

        sx, sy = self._abs(start[0], start[1])
        ex, ey = self._abs(end[0], end[1])
        pyautogui.moveTo(sx, sy)
        # 0.18s: short enough to register as a flick with momentum, long
        # enough that iOS treats it as a swipe rather than a multi-tap.
        pyautogui.dragTo(ex, ey, duration=0.18, button="left")

    def type_text(self, text: str):
        self._refresh_window()
        # Typing needs keyboard focus — pyautogui.typewrite sends keystrokes
        # to whatever app has focus.
        self._activate_simulator()
        pyautogui.typewrite(text, interval=0.02)

    def get_source(self) -> str:
        raise NotImplementedError(
            "screen mode has no XCUITest element tree. Use --vision-only flows."
        )
