"""Client that controls a real iPhone via the macOS iPhone Mirroring window."""

from __future__ import annotations

import io
import subprocess
import time

import pyautogui
from PIL import Image

from .window_chrome import detect_ios_surface


_TITLE_BAR = 28


def _get_mirror_window() -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the iPhone Mirroring window."""
    result = subprocess.run(
        [
            "osascript", "-e",
            'tell application "System Events" to get {position, size} '
            'of window 1 of (first process whose name is "iPhone Mirroring")',
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not find iPhone Mirroring window. Is it open?\n{result.stderr}"
        )
    parts = [int(p.strip()) for p in result.stdout.strip().split(",")]
    return parts[0], parts[1], parts[2], parts[3]


# Chrome detection lives in window_chrome.detect_ios_surface so the same
# principled algorithm handles both the iPhone Mirroring window (light chrome
# → iOS content) and the Simulator window (window bg → device bezel → iOS
# content). See window_chrome.py for the algorithm.


class MirrorClient:
    """Controls iPhone through the macOS iPhone Mirroring app using pyautogui."""

    def __init__(self):
        self._window: tuple[int, int, int, int] | None = None
        self._inset: tuple[int, int, int, int] = (0, 0, 0, 0)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        pass

    def connect(self):
        self._window = _get_mirror_window()
        subprocess.run(
            ["osascript", "-e", 'tell application "iPhone Mirroring" to activate'],
            capture_output=True,
        )
        time.sleep(0.3)
        self._window = _get_mirror_window()
        # Calibrate chrome insets from a fresh screenshot. Keep self._inset at
        # (0,0,0,0) for this capture so we get the raw window content.
        raw_png = self._capture_png()
        self._inset = detect_ios_surface(Image.open(io.BytesIO(raw_png)))

    def _refresh_window(self):
        """Re-read window position. Insets stay cached (size hasn't changed)."""
        new = _get_mirror_window()
        if new[2:] != self._window[2:]:
            # Window resized — insets may be stale. Recalibrate.
            self._window = new
            raw_png = self._capture_png_raw()
            self._inset = detect_ios_surface(Image.open(io.BytesIO(raw_png)))
        else:
            self._window = new

    def _capture_png_raw(self) -> bytes:
        """Capture the window content with no chrome crop. Internal use only."""
        wx, wy, ww, wh = self._window
        region = (wx, wy + _TITLE_BAR, ww, wh - _TITLE_BAR)
        img = pyautogui.screenshot(region=region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _capture_png(self) -> bytes:
        """Capture the iPhone Mirroring window content with chrome cropped out."""
        wx, wy, ww, wh = self._window
        il, it, ir, ib = self._inset
        region = (
            wx + il,
            wy + _TITLE_BAR + it,
            ww - il - ir,
            (wh - _TITLE_BAR) - it - ib,
        )
        img = pyautogui.screenshot(region=region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _abs(self, x: float, y: float) -> tuple[int, int]:
        """Convert iOS-frame coords to absolute screen coords."""
        wx, wy, _, _ = self._window
        il, it, _, _ = self._inset
        return wx + il + int(x), wy + _TITLE_BAR + it + int(y)

    def get_screen_size(self) -> tuple[int, int]:
        """Return the iOS content area size (post chrome-crop)."""
        _, _, ww, wh = self._window
        il, it, ir, ib = self._inset
        return ww - il - ir, (wh - _TITLE_BAR) - it - ib

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
        pyautogui.drag(ex - sx, ey - sy, duration=duration_ms / 1000.0, button="left")

    def scroll(self, amount: int, x: float | None = None, y: float | None = None):
        """Scroll the iOS content via the macOS scroll wheel.

        iPhone Mirroring translates scroll-wheel events into iOS swipe-scrolls,
        unlike mouse drags (which don't register as swipes). Positive `amount`
        scrolls content down (page goes up); negative scrolls content up.
        Optional (x, y) positions the cursor over a specific iOS coordinate
        before scrolling — important when there are multiple scrollable views.
        """
        self._refresh_window()
        if x is not None and y is not None:
            ax, ay = self._abs(x, y)
            pyautogui.moveTo(ax, ay)
        pyautogui.scroll(amount)

    def type_text(self, text: str):
        self._refresh_window()
        pyautogui.typewrite(text, interval=0.02)

    def screenshot(self) -> bytes:
        """Capture the iOS content area as PNG bytes (chrome cropped out)."""
        self._refresh_window()
        return self._capture_png()
