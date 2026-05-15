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
from PIL import Image as _PILImage

from .window_chrome import detect_ios_surface


_TITLE_BAR = 28  # macOS Simulator window title bar height in px.
_DEFAULT_DEVELOPER_DIR = "/Applications/Xcode.app/Contents/Developer"

# Sleep after every state-changing action so the act → observe loop has the
# same temporal contract as MirrorClient. Mirror has ~200–500ms of encode/IPC
# lag before the next captured frame reflects the action; sim is naturally
# faster, so we slow it down to keep the agent's planner behavior comparable.
_POST_ACTION_SETTLE_S = 0.35

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
    instead of WDA — required for SwiftUI on iOS 26 simulators.

    screenshot_source controls how `screenshot()` produces bytes:
      "window"      — pyautogui capture of the Simulator.app window. Same
                      pipeline MirrorClient uses, so eval observations
                      match what an agent would see on real-iPhone mirror.
                      Requires the Simulator window to be visible and not
                      occluded; we activate it on connect to ensure that.
      "framebuffer" — `simctl io screenshot`. Pristine device-resolution
                      PNG straight from CoreSimulator. Works regardless
                      of window state or focus — useful for unattended /
                      CI runs where the Mac may be doing other things.
                      Less mirror-representative.
    """

    def __init__(self, udid: str | None = None, screenshot_source: str = "window"):
        if screenshot_source not in ("window", "framebuffer"):
            raise ValueError(
                f"screenshot_source must be 'window' or 'framebuffer', got {screenshot_source!r}"
            )
        self._udid = udid
        self._device_name: str = ""
        self._device_pts: tuple[int, int] = (0, 0)
        self._window: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._sim_pid: int = 0
        self._screenshot_source = screenshot_source
        # (left, top, right, bottom) pixel insets that locate the iOS surface
        # inside the macOS window-content (the window minus the title bar).
        # The Simulator renders the iOS device with a device bezel inset on
        # all sides — without these, taps land 30–60pt off vertically because
        # the coordinate translation assumes iOS fills the whole window.
        self._insets: tuple[int, int, int, int] = (0, 0, 0, 0)

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
        # Always activate Simulator at connect so the chrome-detection
        # capture grabs the right window pixels — without it the capture
        # may include an overlapping app. The activate also matters for
        # window-source screenshots later.
        self._activate_simulator()
        self._window = _sim_window_geometry()
        # Calibrate iOS-surface insets so input coords land correctly.
        self._calibrate_insets()

    def _refresh_window(self):
        new = _sim_window_geometry()
        # Re-calibrate if the window resized; otherwise insets are still valid.
        if new[2:] != self._window[2:] and new[2] > 0:
            self._window = new
            # Cheap re-calibration. Guard against running during a capture
            # that's already in flight by gating on framebuffer-mode if set.
            self._calibrate_insets()
        else:
            self._window = new

    def _calibrate_insets(self) -> None:
        """Capture the Simulator window once and detect where the iOS
        surface sits inside it. Run at connect and on window resize.

        Uses pyautogui to grab the window-content region (window minus
        title bar) and runs the generic detector. The same approach works
        for MirrorClient against the iPhone Mirroring window — different
        window, same detector.
        """
        wx, wy, ww, wh = self._window
        if ww <= 0 or wh <= _TITLE_BAR:
            return
        region = (wx, wy + _TITLE_BAR, ww, wh - _TITLE_BAR)
        try:
            img = pyautogui.screenshot(region=region)
            self._insets = detect_ios_surface(img)
        except Exception:
            # Detection is best-effort; on failure fall back to zero insets
            # (legacy behavior). The eval surface a wrong-tap rather than
            # crashing the whole agent.
            self._insets = (0, 0, 0, 0)

    def _scale(self) -> tuple[float, float]:
        """macOS pixels per iOS point, horizontally and vertically.

        Accounts for the chrome insets that locate the iOS surface inside
        the macOS window. Without insets the math implicitly stretches the
        iOS frame across the full window and taps land 30–60pt off vertically.
        """
        _, _, ww, wh = self._window
        dw, dh = self._device_pts
        il, it, ir, ib = self._insets
        ios_w_px = max(1, ww - il - ir)
        ios_h_px = max(1, (wh - _TITLE_BAR) - it - ib)
        return ios_w_px / dw, ios_h_px / dh

    def _abs(self, x: float, y: float) -> tuple[int, int]:
        """Convert iOS-point coords to absolute macOS screen pixel coords."""
        wx, wy, _, _ = self._window
        il, it, _, _ = self._insets
        sx, sy = self._scale()
        return int(wx + il + x * sx), int(wy + _TITLE_BAR + it + y * sy)

    # --- public surface (matches SimulatorClient/MirrorClient) ---

    def get_screen_size(self) -> tuple[int, int]:
        return self._device_pts

    def screenshot(self) -> bytes:
        """Capture the screen as PNG bytes via the configured source.

        See the class docstring for the framebuffer-vs-window trade-off.
        """
        if self._screenshot_source == "framebuffer":
            return self._screenshot_framebuffer()
        return self._screenshot_window()

    def _screenshot_window(self) -> bytes:
        self._refresh_window()
        wx, wy, ww, wh = self._window
        region = (wx, wy + _TITLE_BAR, ww, wh - _TITLE_BAR)
        img = pyautogui.screenshot(region=region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _screenshot_framebuffer(self) -> bytes:
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
        time.sleep(_POST_ACTION_SETTLE_S)

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
        time.sleep(_POST_ACTION_SETTLE_S)

    def scroll(self, amount: int, x: float | None = None, y: float | None = None):
        """Scroll content via a vertical drag (NOT scroll-wheel).

        The iOS Simulator does NOT translate macOS scroll-wheel events into
        iOS scrolls — only drag gestures register as scrolling. MirrorClient
        uses scroll-wheel because iPhone Mirroring filters drags but accepts
        wheel; on simulator, the substrate is reversed. There's no single
        scroll mechanism that's identical across both targets.

        Sign convention (matches scroll wheels):
          amount > 0  → reveal content below (swipe up)
          amount < 0  → reveal content above (swipe down)

        Each "click" is roughly 30 points of swipe distance.
        """
        self._refresh_window()
        self._activate_simulator()
        dw, dh = self._device_pts
        cx = x if x is not None else dw / 2
        cy = y if y is not None else dh / 2

        per_click = 50
        distance = max(60, min(dh - 100, abs(amount) * per_click))
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
        # Duration scaled to distance so velocity stays around 600–800 pt/s.
        # Above ~1000 pt/s the iOS Simulator treats the drag as a too-fast
        # flick and ignores it; below ~300 pt/s it works but feels slow.
        # 0.18s flat ignores ignores 50% of scroll calls. Verified by
        # comparing scroll vs `swipe` (which uses 0.8s for 600pt).
        drag_duration = max(0.4, distance / 700.0)
        pyautogui.dragTo(ex, ey, duration=drag_duration, button="left")
        time.sleep(_POST_ACTION_SETTLE_S)

    def type_text(self, text: str):
        self._refresh_window()
        # Typing needs keyboard focus — pyautogui.typewrite sends keystrokes
        # to whatever app has focus.
        self._activate_simulator()
        pyautogui.typewrite(text, interval=0.02)
        time.sleep(_POST_ACTION_SETTLE_S)

    def get_source(self) -> str:
        raise NotImplementedError(
            "screen mode has no XCUITest element tree. Use --vision-only flows."
        )
