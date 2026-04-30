from __future__ import annotations

import base64
import logging

import requests

logger = logging.getLogger(__name__)


class SessionError(Exception):
    pass


class SimulatorClient:
    def __init__(
        self,
        udid: str,
        bundle_id: str,
        host: str = "localhost",
        port: int = 4723,
        command_timeout: int = 600,
        scale_factor: int = 3,
    ):
        self.udid = udid
        self.bundle_id = bundle_id
        self.host = host
        self.port = port
        self.command_timeout = command_timeout
        self.scale_factor = scale_factor
        self._base_url = f"http://{host}:{port}"
        self._session_id: str | None = None
        self._http = requests.Session()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    def connect(self):
        resp = self._http.post(
            f"{self._base_url}/session",
            json={
                "capabilities": {
                    "alwaysMatch": {
                        "platformName": "iOS",
                        "appium:automationName": "XCUITest",
                        "appium:udid": self.udid,
                        "appium:bundleId": self.bundle_id,
                        "appium:noReset": True,
                        "appium:autoLaunch": False,
                        "appium:shouldTerminateApp": False,
                        "appium:forceAppLaunch": False,
                        "appium:newCommandTimeout": self.command_timeout,
                    }
                }
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._session_id = data["value"]["sessionId"]
        logger.info("Connected: session %s", self._session_id)

    def disconnect(self):
        if self._session_id:
            try:
                self._http.delete(f"{self._base_url}/session/{self._session_id}")
            except requests.RequestException:
                pass
            self._session_id = None

    def _ensure_session(self):
        if self._session_id is None:
            self.connect()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        self._ensure_session()
        url = f"{self._base_url}/session/{self._session_id}{path}"
        resp = self._http.request(method, url, **kwargs)
        data = resp.json()

        # Auto-reconnect on expired session
        if isinstance(data.get("value"), dict) and data["value"].get("error") == "invalid session id":
            logger.info("Session expired, reconnecting...")
            self._session_id = None
            self.connect()
            url = f"{self._base_url}/session/{self._session_id}{path}"
            resp = self._http.request(method, url, **kwargs)
            data = resp.json()

        return data

    # --- Actions ---

    def screenshot(self) -> bytes:
        data = self._request("GET", "/screenshot")
        return base64.b64decode(data["value"])

    def screenshot_to_file(self, path: str):
        png = self.screenshot()
        with open(path, "wb") as f:
            f.write(png)
        logger.info("Screenshot saved to %s", path)

    def tap(self, x: float, y: float):
        self._request(
            "POST",
            "/execute/sync",
            json={"script": "mobile: tap", "args": [{"x": x, "y": y}]},
        )

    def swipe(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 800,
    ):
        self._request(
            "POST",
            "/actions",
            json={
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": [
                            {"type": "pointerMove", "duration": 0, "x": int(start_x), "y": int(start_y)},
                            {"type": "pointerDown", "button": 0},
                            {"type": "pause", "duration": 100},
                            {"type": "pointerMove", "duration": duration_ms, "x": int(end_x), "y": int(end_y)},
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ]
            },
        )

    def type_text(self, text: str):
        self._request(
            "POST",
            "/execute/sync",
            json={"script": "mobile: keys", "args": [{"keys": list(text)}]},
        )

    def get_source(self) -> str:
        data = self._request("GET", "/source")
        return data["value"]

    def get_screen_size(self) -> tuple[int, int]:
        data = self._request("GET", "/window/rect")
        rect = data["value"]
        return rect["width"], rect["height"]
