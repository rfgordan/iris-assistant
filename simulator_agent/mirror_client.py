"""Client that controls a real iPhone via the macOS iPhone Mirroring window."""

from __future__ import annotations

import subprocess
import time

import pyautogui


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
    # Output like: "497, 82, 326, 720"
    parts = [int(p.strip()) for p in result.stdout.strip().split(",")]
    return parts[0], parts[1], parts[2], parts[3]


class MirrorClient:
    """Controls iPhone through the macOS iPhone Mirroring app using pyautogui."""

    def __init__(self):
        self._window: tuple[int, int, int, int] | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        pass

    def connect(self):
        self._window = _get_mirror_window()
        # Bring window to front
        subprocess.run(
            ["osascript", "-e", 'tell application "iPhone Mirroring" to activate'],
            capture_output=True,
        )
        time.sleep(0.3)
        # Re-read position in case it moved
        self._window = _get_mirror_window()

    def _abs(self, x: float, y: float) -> tuple[int, int]:
        """Convert coordinates relative to window content into absolute screen coords."""
        wx, wy, ww, wh = self._window
        # The window has a title bar (~28px). Content starts below it.
        title_bar = 28
        abs_x = wx + int(x * ww / self.get_screen_size()[0])
        abs_y = wy + title_bar + int(y * (wh - title_bar) / self.get_screen_size()[1])
        return abs_x, abs_y

    def get_screen_size(self) -> tuple[int, int]:
        """Return the logical size of the iPhone screen as shown in the mirror window."""
        _, _, ww, wh = self._window
        title_bar = 28
        return ww, wh - title_bar

    def tap(self, x: float, y: float):
        abs_x, abs_y = self._abs(x, y)
        pyautogui.click(abs_x, abs_y)

    def swipe(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 500,
    ):
        sx, sy = self._abs(start_x, start_y)
        ex, ey = self._abs(end_x, end_y)
        pyautogui.moveTo(sx, sy)
        pyautogui.drag(ex - sx, ey - sy, duration=duration_ms / 1000.0, button="left")

    def type_text(self, text: str):
        pyautogui.typewrite(text, interval=0.02)

    def screenshot(self) -> bytes:
        """Capture the iPhone Mirroring window content as PNG bytes."""
        wx, wy, ww, wh = self._window
        title_bar = 28
        region = (wx, wy + title_bar, ww, wh - title_bar)
        img = pyautogui.screenshot(region=region)
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
