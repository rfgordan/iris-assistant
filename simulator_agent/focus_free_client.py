"""Focus-preserving iOS Simulator input client.

Goal: deliver tap/drag at absolute screen coordinates to the Simulator such
that SwiftUI gesture recognizers fire, WITHOUT permanently making
Simulator.app the frontmost window.

Constraint: actions originate as coordinates (x, y) — the same input model
the agent produces today. No targeting of UI elements by id / label / AX role.

## Findings

Tested 8 strategies. Results:

| Strategy                       | Delivered? | Focus theft?  |
|--------------------------------|------------|---------------|
| pid_default                    | ❌ no       | none          |
| pid_hid_source                 | ❌ no       | none          |
| pid_combined_src               | ❌ no       | none          |
| sess_then_pid                  | ❌ no       | n/a           |
| sess_with_target_pid           | ✅ yes      | permanent     |
| annot_sess_with_target_pid     | ❌ no       | none          |
| hid_with_target_pid            | ✅ yes      | permanent     |
| applescript_click              | ❌ no       | none          |
| applescript_window_click       | ❌ no       | none          |
| **sess_target_pid_restored**   | ✅ yes      | ~150ms blip   |

The key knob is `kCGEventTargetUnixProcessID`: setting it on an event before
posting via kCGSessionEventTap (or kCGHIDEventTap) lets the event reach the
target app's SwiftUI gesture recognizers. Without it, `CGEventPostToPid`
queues events but they're filtered somewhere between the per-process tap
and the NSEvent dispatcher, so gestures never fire.

But the moment the event reaches a SwiftUI gesture, macOS's click-to-front
behavior also fires: the window gets promoted to frontmost. There's no
known coordinate-level mouse-event API that lets you deliver a click
without triggering the click-to-front. (HID-level injection bypasses it
but requires entitlements that ordinary user processes don't have.)

## What we shipped: `sess_target_pid_restored`

The default strategy. Steps:
  1. Read the previously frontmost app via System Events.
  2. Post the click with `kCGEventTargetUnixProcessID = Simulator's PID` via
     `kCGSessionEventTap`. Simulator activates as a side effect.
  3. Re-activate the previous frontmost app.

Cost: ~150ms of Simulator activation per input call. The user sees a brief
flash; their working app returns. Multi-sim sweeps still serialize on
focus (only one app can be frontmost at a time), but each operation is
bounded — no permanent focus theft.

## 2026-05-15: pre-activation now explicit

While retesting on iOS 26.3, the `sess_target_pid_restored` strategy
silently stopped delivering events when Simulator wasn't already
frontmost — `handleTap` never fired. Adding an explicit
`activate Simulator → sleep 150ms → dispatch → restore prev` sequence
fixes it. The previous implementation relied on the click-to-front
side effect of a PID-targeted event to bring Simulator forward, which
is no longer happening reliably on the current macOS state. Whether
that's a permission, a Mac-state, or a macOS-policy change isn't fully
diagnosed — but the explicit-activate fix is robust either way.

## What didn't work, with notes

- `CGEventPostToPid` alone (3 variants): events arrive in the target's
  queue but never reach NSEvent dispatch. Likely filtered by AppKit for
  not having a system-tap origin.

- AppleScript `click at` (process and window scopes): System Events
  accessibility appears to route through the same focus-aware dispatcher.

- Annotated session tap with target PID: dropped silently.

## Possible follow-ups

- **IOKit HID injection (`IOHIDPostEvent`)**: events look like real
  hardware; might decouple from click-to-front. Requires either
  `HIDPosting` entitlement or root, so unlikely to work as a user process.

- **Off-screen window placement**: move the Simulator window outside the
  visible screen, deliver the click, restore position. Avoids visual
  flicker but is fragile and visually disruptive while in motion.

- **Disable click-to-front for Simulator**: there's a system Accessibility
  setting "Bring focus on click" — global, not Simulator-scoped, so not
  a viable scoped solution.

## Diagnostics

Use eval/EvalApp's tap_target scenario with TimeoutS=15 to test each
strategy. The os_log line `DEBUG handleTap point=…` confirms the SwiftUI
gesture fired. Absence of that line = strategy didn't deliver.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time
from typing import Literal

import pyautogui
import Quartz

from .window_chrome import detect_ios_surface


# Reuse helpers from screen_client; minimal duplication.
from .screen_client import (
    _TITLE_BAR,
    _POST_ACTION_SETTLE_S,
    _env,
    _booted_device,
    _device_points,
    _sim_window_geometry,
    _simulator_pid,
)


Strategy = Literal[
    "pid_default",
    "pid_hid_source",
    "pid_combined_src",
    "sess_then_pid",
    "sess_with_target_pid",
    "annot_sess_with_target_pid",
    "hid_with_target_pid",
    "sess_target_pid_restored",
    "applescript_click",
    "applescript_window_click",
    "ax_post",
]


# ── strategy implementations ─────────────────────────────────────────────


def _src(state: int) -> "Quartz.CGEventSource":
    return Quartz.CGEventSourceCreate(state)


def _mouse_event(source, kind, x: float, y: float):
    return Quartz.CGEventCreateMouseEvent(
        source, kind, (x, y), Quartz.kCGMouseButtonLeft
    )


def _post(strategy: Strategy, pid: int, event) -> None:
    if strategy in ("pid_default", "pid_hid_source", "pid_combined_src"):
        Quartz.CGEventPostToPid(pid, event)
    elif strategy == "sess_then_pid":
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, event)
    elif strategy == "sess_with_target_pid":
        # Tell the system who the event is targeted at, then post via the
        # session tap. macOS may still route by focus, but worth a shot —
        # some apps check kCGEventTargetUnixProcessID for delivery.
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventTargetUnixProcessID, pid)
        Quartz.CGEventPost(Quartz.kCGSessionEventTap, event)
    elif strategy == "annot_sess_with_target_pid":
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventTargetUnixProcessID, pid)
        Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, event)
    elif strategy == "hid_with_target_pid":
        # HID-level events look like real hardware. Adding TargetUnixProcessID
        # may route past the window-server's click-to-front coupling.
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventTargetUnixProcessID, pid)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    else:
        raise ValueError(f"_post not applicable for strategy={strategy}")


def _source_for(strategy: Strategy):
    if strategy == "pid_hid_source":
        return _src(Quartz.kCGEventSourceStateHIDSystemState)
    if strategy == "pid_combined_src":
        return _src(Quartz.kCGEventSourceStateCombinedSessionState)
    return None  # pid_default / sess_then_pid use the default source


def _click_quartz(strategy: Strategy, pid: int, x: float, y: float, hold_ms: int = 50):
    src = _source_for(strategy)
    down = _mouse_event(src, Quartz.kCGEventLeftMouseDown, x, y)
    up = _mouse_event(src, Quartz.kCGEventLeftMouseUp, x, y)
    _post(strategy, pid, down)
    time.sleep(hold_ms / 1000.0)
    _post(strategy, pid, up)


def _drag_quartz(strategy: Strategy, pid: int, sx: float, sy: float, ex: float, ey: float, duration_s: float):
    src = _source_for(strategy)
    duration_s = max(0.05, duration_s)
    steps = max(8, int(duration_s * 60))
    down = _mouse_event(src, Quartz.kCGEventLeftMouseDown, sx, sy)
    _post(strategy, pid, down)
    dt = duration_s / steps
    for i in range(1, steps + 1):
        t = i / steps
        x = sx + (ex - sx) * t
        y = sy + (ey - sy) * t
        ev = _mouse_event(src, Quartz.kCGEventLeftMouseDragged, x, y)
        _post(strategy, pid, ev)
        time.sleep(dt)
    up = _mouse_event(src, Quartz.kCGEventLeftMouseUp, ex, ey)
    _post(strategy, pid, up)


def _frontmost_app() -> str | None:
    proc = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to name of first process whose frontmost is true'],
        capture_output=True, text=True,
    )
    name = proc.stdout.strip()
    return name or None


def _activate_app(name: str) -> None:
    subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'],
                   capture_output=True)


def _click_applescript(x: float, y: float) -> None:
    """`click at {x, y}` scoped to process "Simulator" via System Events."""
    script = f'tell application "System Events" to tell process "Simulator" to click at {{{int(x)}, {int(y)}}}'
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def _click_applescript_window(x: float, y: float) -> None:
    """`click window 1 of process "Simulator" at {x, y}` — explicitly scoped
    to the window so AX has a concrete target."""
    script = (
        f'tell application "System Events" to tell process "Simulator" '
        f'to click window 1 at {{{int(x)}, {int(y)}}}'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


# ── public client ────────────────────────────────────────────────────────


class FocusFreeScreenClient:
    """Drop-in for SimulatorScreenClient; experiments with focus-free input
    strategies. Same iOS-point coordinate model and screenshot path.

    Usage:
        with FocusFreeScreenClient(strategy="pid_hid_source") as c:
            c.tap(201, 451)
    """

    def __init__(
        self,
        udid: str | None = None,
        strategy: Strategy = "sess_target_pid_restored",
        screenshot_source: str = "window",
    ):
        if screenshot_source not in ("window", "framebuffer"):
            raise ValueError(
                f"screenshot_source must be 'window' or 'framebuffer', got {screenshot_source!r}"
            )
        self._udid = udid
        self._device_name: str = ""
        self._device_pts: tuple[int, int] = (0, 0)
        self._window: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._sim_pid: int = 0
        self.strategy: Strategy = strategy
        self._screenshot_source = screenshot_source
        # iOS-surface insets inside the macOS window-content (window minus
        # title bar). See SimulatorScreenClient for rationale.
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
        # Activate briefly so the calibration capture grabs Simulator pixels,
        # then restore previous frontmost — preserves the focus-free promise.
        prev = _frontmost_app()
        if prev != "Simulator":
            _activate_app("Simulator")
            time.sleep(0.15)
        self._window = _sim_window_geometry()
        self._calibrate_insets()
        if prev and prev != "Simulator":
            _activate_app(prev)

    def _refresh_window(self):
        new = _sim_window_geometry()
        if new[2:] != self._window[2:] and new[2] > 0:
            self._window = new
            self._calibrate_insets()
        else:
            self._window = new

    def _calibrate_insets(self) -> None:
        wx, wy, ww, wh = self._window
        if ww <= 0 or wh <= _TITLE_BAR:
            return
        region = (wx, wy + _TITLE_BAR, ww, wh - _TITLE_BAR)
        try:
            img = pyautogui.screenshot(region=region)
            self._insets = detect_ios_surface(img)
        except Exception:
            self._insets = (0, 0, 0, 0)

    def _scale(self) -> tuple[float, float]:
        _, _, ww, wh = self._window
        dw, dh = self._device_pts
        il, it, ir, ib = self._insets
        ios_w_px = max(1, ww - il - ir)
        ios_h_px = max(1, (wh - _TITLE_BAR) - it - ib)
        return ios_w_px / dw, ios_h_px / dh

    def _abs(self, x: float, y: float) -> tuple[int, int]:
        wx, wy, _, _ = self._window
        il, it, _, _ = self._insets
        sx, sy = self._scale()
        return int(wx + il + x * sx), int(wy + _TITLE_BAR + it + y * sy)

    # ── public surface ──

    def get_screen_size(self) -> tuple[int, int]:
        return self._device_pts

    def screenshot(self) -> bytes:
        """Capture the screen as PNG bytes via the configured source.

        "window": briefly activates Simulator, captures the window pixels
        via pyautogui (matches MirrorClient — mirror-representative), then
        restores the previously frontmost app.
        "framebuffer": `simctl io screenshot`. Truly focus-free; ideal for
        unattended runs. Less mirror-representative.
        """
        if self._screenshot_source == "framebuffer":
            return self._screenshot_framebuffer()
        return self._screenshot_window()

    def _screenshot_window(self) -> bytes:
        self._refresh_window()
        prev = _frontmost_app()
        _activate_app("Simulator")
        # Wait for the window to actually be raised before grabbing pixels.
        time.sleep(0.15)
        wx, wy, ww, wh = self._window
        region = (wx, wy + _TITLE_BAR, ww, wh - _TITLE_BAR)
        img = pyautogui.screenshot(region=region)
        if prev and prev != "Simulator":
            _activate_app(prev)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _screenshot_framebuffer(self) -> bytes:
        proc = subprocess.run(
            ["xcrun", "simctl", "io", self._udid, "screenshot", "-"],
            env=_env(), capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"simctl screenshot failed: {proc.stderr.decode(errors='replace')}")
        return proc.stdout

    def tap(self, x: float, y: float):
        self._refresh_window()
        ax, ay = self._abs(x, y)
        if self.strategy == "applescript_click":
            _click_applescript(ax, ay)
        elif self.strategy == "applescript_window_click":
            _click_applescript_window(ax, ay)
        elif self.strategy == "ax_post":
            self._tap_ax(ax, ay)
        elif self.strategy == "sess_target_pid_restored":
            # Explicitly activate Simulator before dispatch — the original
            # implementation relied on click-to-front as a side effect of
            # the PID-targeted event, which isn't reliable on the current
            # Mac state (we hit silent non-delivery without pre-activation).
            # Activate → dispatch → restore preserves the focus-free promise
            # while ensuring the event reaches SwiftUI.
            prev = _frontmost_app()
            if prev != "Simulator":
                _activate_app("Simulator")
                time.sleep(0.15)
            _click_quartz("sess_with_target_pid", self._sim_pid, ax, ay)
            if prev and prev != "Simulator":
                _activate_app(prev)
        else:
            _click_quartz(self.strategy, self._sim_pid, ax, ay)
        time.sleep(_POST_ACTION_SETTLE_S)

    def swipe(self, start_x, start_y, end_x, end_y, duration_ms: int = 500):
        self._refresh_window()
        sx, sy = self._abs(start_x, start_y)
        ex, ey = self._abs(end_x, end_y)
        if self.strategy in ("applescript_click", "applescript_window_click", "ax_post"):
            raise NotImplementedError(f"swipe not implemented for strategy={self.strategy}")
        if self.strategy == "sess_target_pid_restored":
            prev = _frontmost_app()
            if prev != "Simulator":
                _activate_app("Simulator")
                time.sleep(0.15)
            _drag_quartz("sess_with_target_pid", self._sim_pid, sx, sy, ex, ey, duration_ms / 1000.0)
            if prev and prev != "Simulator":
                _activate_app(prev)
        else:
            _drag_quartz(self.strategy, self._sim_pid, sx, sy, ex, ey, duration_ms / 1000.0)
        time.sleep(_POST_ACTION_SETTLE_S)

    def scroll(self, amount: int, x: float | None = None, y: float | None = None):
        """Scroll via a vertical drag (NOT scroll-wheel), focus-preserving.

        The iOS Simulator does not translate scroll-wheel events into iOS
        scrolls — only drag gestures register. MirrorClient uses wheel
        because iPhone Mirroring is the opposite. No single substrate
        matches both. Wraps the drag in activate/restore to preserve focus.
        """
        self._refresh_window()
        dw, dh = self._device_pts
        cx = x if x is not None else dw / 2
        cy = y if y is not None else dh / 2
        per_click = 50
        distance = max(60, min(dh - 100, abs(amount) * per_click))
        half = distance / 2
        if amount > 0:
            start, end = (cx, cy + half), (cx, cy - half)
        else:
            start, end = (cx, cy - half), (cx, cy + half)
        sx, sy = self._abs(start[0], start[1])
        ex, ey = self._abs(end[0], end[1])
        if self.strategy in ("applescript_click", "applescript_window_click", "ax_post"):
            raise NotImplementedError(f"scroll not implemented for strategy={self.strategy}")
        # Duration scaled to distance so velocity stays ~700 pt/s. Faster
        # than that, the simulator treats the drag as a too-fast flick and
        # the scroll-view ignores it. Verified with scroll_find_message.
        drag_duration = max(0.4, distance / 700.0)
        if self.strategy == "sess_target_pid_restored":
            prev = _frontmost_app()
            if prev != "Simulator":
                _activate_app("Simulator")
                time.sleep(0.15)
            _drag_quartz("sess_with_target_pid", self._sim_pid, sx, sy, ex, ey, drag_duration)
            if prev and prev != "Simulator":
                _activate_app(prev)
        else:
            _drag_quartz(self.strategy, self._sim_pid, sx, sy, ex, ey, drag_duration)
        time.sleep(_POST_ACTION_SETTLE_S)

    def type_text(self, text: str):
        """Type text into whatever has keyboard focus inside the simulator.

        Briefly activates Simulator so keystrokes land there, then restores
        the previously frontmost app — same focus-restore pattern as tap.
        """
        self._refresh_window()
        prev = _frontmost_app()
        if prev != "Simulator":
            _activate_app("Simulator")
            time.sleep(0.15)
        pyautogui.typewrite(text, interval=0.02)
        if prev and prev != "Simulator":
            _activate_app(prev)
        time.sleep(_POST_ACTION_SETTLE_S)

    def _tap_ax(self, x: float, y: float):
        # Placeholder: AXUIElement-based posting. Hard to do purely from
        # coords (AX is element-centric), but worth a try via AXValue at a
        # point. Implement once we have a working baseline.
        raise NotImplementedError("ax_post not yet implemented")
