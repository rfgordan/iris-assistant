"""Background iOS Simulator input client.

This client injects UI input through CoreSimulator's private SimulatorKit HID
client instead of clicking the macOS Simulator.app window. The practical
benefit is that taps, drags, scrolls, and paste-based text entry are delivered
to the booted simulator without activating Simulator.app or changing the
desktop's frontmost application.

Coordinates are in iOS points, matching SimulatorClient and
SimulatorScreenClient. Screenshots still come from `simctl io screenshot`.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from typing import Any


_CORE_SIMULATOR_FRAMEWORK = "/Library/Developer/PrivateFrameworks/CoreSimulator.framework"
_SIMULATOR_KIT_FRAMEWORK = (
    "/Applications/Xcode.app/Contents/Developer/Library/PrivateFrameworks/SimulatorKit.framework"
)
_SIMULATOR_KIT_DYLIB = f"{_SIMULATOR_KIT_FRAMEWORK}/Versions/A/SimulatorKit"

_BOOTED_STATE = 3
_PRIMARY_SCREEN_TARGET = 0x40000000

_NSEVENT_LEFT_MOUSE_DOWN = 1
_NSEVENT_LEFT_MOUSE_UP = 2
_NSEVENT_LEFT_MOUSE_DRAGGED = 6

_HID_BUTTON_DOWN = 1
_HID_BUTTON_UP = 2
_USB_KEY_LEFT_COMMAND = 0xE3
_USB_KEY_V = 0x19

_DEFAULT_DEVELOPER_DIR = "/Applications/Xcode.app/Contents/Developer"

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


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _SimulatorKitBindings:
    """Lazily-loaded private framework bindings.

    Keeping the ctypes/PyObjC setup in one place makes the public client easier
    to read and gives callers a clear error if private framework loading fails.
    """

    def __init__(self):
        try:
            import objc  # type: ignore
            from Foundation import NSBundle  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "background simulator input requires PyObjC (`objc` and Foundation)."
            ) from exc

        core_bundle = NSBundle.bundleWithPath_(_CORE_SIMULATOR_FRAMEWORK)
        simkit_bundle = NSBundle.bundleWithPath_(_SIMULATOR_KIT_FRAMEWORK)
        if not core_bundle or not core_bundle.load():
            raise RuntimeError(f"Could not load CoreSimulator framework: {_CORE_SIMULATOR_FRAMEWORK}")
        if not simkit_bundle or not simkit_bundle.load():
            raise RuntimeError(f"Could not load SimulatorKit framework: {_SIMULATOR_KIT_FRAMEWORK}")

        self.objc = objc
        self.SimServiceContext = objc.lookUpClass("SimServiceContext")
        self.LegacyHIDClient = objc.lookUpClass("SimulatorKit.SimDeviceLegacyHIDClient")

        self.simkit = ctypes.CDLL(_SIMULATOR_KIT_DYLIB)
        self.simkit.IndigoHIDMessageForMouseNSEvent.restype = ctypes.c_void_p
        self.simkit.IndigoHIDMessageForMouseNSEvent.argtypes = [
            ctypes.POINTER(CGPoint),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_ulong,
            CGSize,
            ctypes.c_ulong,
        ]
        self.simkit.IndigoHIDMessageForKeyboardArbitrary.restype = ctypes.c_void_p
        self.simkit.IndigoHIDMessageForKeyboardArbitrary.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]

        objc_lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc_lib.sel_registerName.restype = ctypes.c_void_p
        objc_lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self._objc_msg_send = objc_lib.objc_msgSend
        self._objc_msg_send.restype = None
        self._objc_msg_send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._send_selector = objc_lib.sel_registerName(
            b"sendWithMessage:freeWhenDone:completionQueue:completion:"
        )

    def send_message(self, hid_client: Any, message: int) -> None:
        self._objc_msg_send(
            self.objc.pyobjc_id(hid_client),
            self._send_selector,
            message,
            True,
            None,
            None,
        )


_bindings: _SimulatorKitBindings | None = None


def _get_bindings() -> _SimulatorKitBindings:
    global _bindings
    if _bindings is None:
        _bindings = _SimulatorKitBindings()
    return _bindings


def _developer_dir() -> str:
    return os.environ.get("DEVELOPER_DIR", _DEFAULT_DEVELOPER_DIR)


def _env() -> dict:
    env = os.environ.copy()
    env.setdefault("DEVELOPER_DIR", _DEFAULT_DEVELOPER_DIR)
    return env


def _booted_device() -> tuple[str, str]:
    out = _simctl(["list", "devices", "booted", "-j"]).stdout
    data = json.loads(out)
    for _, devices in data.get("devices", {}).items():
        for device in devices:
            if device.get("state") == "Booted":
                return device["udid"], device.get("name", "")
    raise RuntimeError("No booted simulator found.")


def _device_points(name: str) -> tuple[int, int]:
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


def _simctl(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["xcrun", "simctl", *args],
        env=_env(),
        input=input_text,
        text=True,
        capture_output=True,
    )


class BackgroundSimulatorClient:
    """Simulator client that sends UI input without desktop focus changes."""

    def __init__(self, udid: str | None = None):
        self._udid = udid
        self._device_name = ""
        self._device_pts: tuple[int, int] = (0, 0)
        self._device = None
        self._hid_client = None
        self._bindings: _SimulatorKitBindings | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self._hid_client = None
        self._device = None

    def connect(self):
        if not self._udid:
            self._udid, self._device_name = _booted_device()
        else:
            _, self._device_name = _booted_device()

        self._device_pts = _device_points(self._device_name)
        self._bindings = _get_bindings()
        self._device = self._resolve_device(self._udid)
        self._hid_client = self._bindings.LegacyHIDClient.alloc().initWithDevice_error_(
            self._device,
            None,
        )
        if self._hid_client is None:
            raise RuntimeError(f"Could not create SimulatorKit HID client for {self._udid}.")

    def _resolve_device(self, udid: str):
        assert self._bindings is not None
        context = self._bindings.SimServiceContext.sharedServiceContextForDeveloperDir_error_(
            _developer_dir(),
            None,
        )
        device_set = context.defaultDeviceSetWithError_(None)
        for device in device_set.devices():
            if str(device.UDID()) == udid:
                if device.state() != _BOOTED_STATE:
                    raise RuntimeError(f"Simulator {udid} is not booted.")
                return device
        raise RuntimeError(f"Simulator {udid} was not found in CoreSimulator's default device set.")

    def _send(self, message: int | None) -> None:
        if not message:
            raise RuntimeError("SimulatorKit did not create a HID message.")
        assert self._bindings is not None
        assert self._hid_client is not None
        self._bindings.send_message(self._hid_client, message)

    def _mouse_message(self, x: float, y: float, event_type: int) -> int:
        assert self._bindings is not None
        width, height = self._device_pts
        point = CGPoint(float(x), float(y))
        size = CGSize(float(width), float(height))
        return self._bindings.simkit.IndigoHIDMessageForMouseNSEvent(
            ctypes.byref(point),
            None,
            _PRIMARY_SCREEN_TARGET,
            event_type,
            size,
            0,
        )

    def _key_message(self, key_code: int, op: int) -> int:
        assert self._bindings is not None
        return self._bindings.simkit.IndigoHIDMessageForKeyboardArbitrary(key_code, op)

    def _key(self, key_code: int, hold_s: float = 0.02) -> None:
        self._send(self._key_message(key_code, _HID_BUTTON_DOWN))
        time.sleep(hold_s)
        self._send(self._key_message(key_code, _HID_BUTTON_UP))

    # --- public surface (matches SimulatorClient/MirrorClient) ---

    def get_screen_size(self) -> tuple[int, int]:
        return self._device_pts

    def screenshot(self) -> bytes:
        proc = subprocess.run(
            ["xcrun", "simctl", "io", self._udid, "screenshot", "-"],
            env=_env(),
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"simctl screenshot failed: {proc.stderr.decode(errors='replace')}")
        return proc.stdout

    def tap(self, x: float, y: float):
        self._send(self._mouse_message(x, y, _NSEVENT_LEFT_MOUSE_DOWN))
        time.sleep(0.05)
        self._send(self._mouse_message(x, y, _NSEVENT_LEFT_MOUSE_UP))

    def swipe(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 500,
    ):
        duration_s = max(0.05, duration_ms / 1000.0)
        steps = max(8, int(duration_s * 60))
        self._send(self._mouse_message(start_x, start_y, _NSEVENT_LEFT_MOUSE_DOWN))
        dt = duration_s / steps
        for i in range(1, steps + 1):
            t = i / steps
            x = start_x + (end_x - start_x) * t
            y = start_y + (end_y - start_y) * t
            self._send(self._mouse_message(x, y, _NSEVENT_LEFT_MOUSE_DRAGGED))
            time.sleep(dt)
        self._send(self._mouse_message(end_x, end_y, _NSEVENT_LEFT_MOUSE_UP))

    def scroll(self, amount: int, x: float | None = None, y: float | None = None):
        """Scroll content by delivering a short vertical drag through HID."""
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

        self.swipe(start[0], start[1], end[0], end[1], duration_ms=180)

    def type_text(self, text: str):
        """Paste text into the simulator's focused field without desktop focus.

        `simctl pbcopy` handles arbitrary text/unicode. The HID Command-V is
        sent directly to the simulator, not to the frontmost macOS app.
        """
        proc = _simctl(["pbcopy", self._udid], input_text=text)
        if proc.returncode != 0:
            raise RuntimeError(f"simctl pbcopy failed: {proc.stderr}")

        self._send(self._key_message(_USB_KEY_LEFT_COMMAND, _HID_BUTTON_DOWN))
        time.sleep(0.02)
        self._key(_USB_KEY_V)
        time.sleep(0.02)
        self._send(self._key_message(_USB_KEY_LEFT_COMMAND, _HID_BUTTON_UP))

    def get_source(self) -> str:
        raise NotImplementedError(
            "background simulator mode has no XCUITest element tree. Use --vision-only flows."
        )
