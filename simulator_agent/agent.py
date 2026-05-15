"""Generic autonomous agent runner for iOS apps."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time

import anthropic

from .client import SimulatorClient
from .observation import observe, Observation


SYSTEM_ELEMENTS = """You are an autonomous agent controlling an iOS app.

You receive a structured list of UI elements on screen. Each element has:
- Type (Button, StaticText, TextField, etc.)
- Label (the visible text or accessibility label)
- Coordinates (center point in screen points)
- Enabled state

Identify the element you want to interact with by its label, then use its coordinates to act.

You must respond with a single action in one of these formats:
  TAP x y
  SWIPE x1 y1 x2 y2
  TYPE text here
  DONE

Rules:
- Find the right element by its label, then tap its coordinates
- Respond with ONLY the action, nothing else
- Respond DONE when the goal is complete

## Your goal

{instructions}"""

SYSTEM_VISION = """You are an autonomous agent controlling an iOS app.

You receive a screenshot of the current screen. The screen coordinate system is {width}x{height} points.
You must respond with a single action in one of these formats:
  TAP x y
  SWIPE x1 y1 x2 y2
  TYPE text here
  DONE

Rules:
- Estimate tap coordinates from what you see in the screenshot
- Respond with ONLY the action, nothing else
- Respond DONE when the goal is complete

## Your goal

{instructions}"""


def format_elements(obs: Observation) -> str:
    if not obs.elements:
        return "No elements found"
    lines = []
    for el in obs.elements:
        label = el.label or "(no label)"
        short_type = el.type.replace("XCUIElementType", "")
        lines.append(f'  [{short_type}] "{label}" @ ({el.x:.0f}, {el.y:.0f}) enabled={el.enabled}')
    return "\n".join(lines)


def _resolve_booted_udid() -> str | None:
    """Get the UDID of the currently booted simulator."""
    result = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "booted", "-j"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    import json as json_mod2
    data = json_mod2.loads(result.stdout)
    for runtime, devices in data.get("devices", {}).items():
        for device in devices:
            if device.get("state") == "Booted":
                return device["udid"]
    return None


def ask_claude(
    system_prompt: str,
    user_message: str,
    history: list[dict],
    image_data: bytes | None = None,
    model: str = "claude-sonnet-4-6",
) -> str:
    """Ask Claude for the next action via the Anthropic SDK. Mutates history in place."""
    client = anthropic.Anthropic()
    if image_data:
        screenshot_b64 = base64.b64encode(image_data).decode()
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
            {"type": "text", "text": user_message},
        ]
    else:
        user_content = user_message

    history.append({"role": "user", "content": user_content})
    resp = client.messages.create(
        model=model,
        max_tokens=100,
        system=system_prompt,
        messages=history,
    )
    action = resp.content[0].text.strip()
    history.append({"role": "assistant", "content": action})
    return action


def _run_loop(client, instructions: str, vision_only: bool, max_steps: int):
    """Core agent loop that works with any client (SimulatorClient or MirrorClient)."""
    screen_size = client.get_screen_size()

    if vision_only:
        system_prompt = SYSTEM_VISION.format(
            instructions=instructions,
            width=screen_size[0],
            height=screen_size[1],
        )
    else:
        system_prompt = SYSTEM_ELEMENTS.format(instructions=instructions)

    history: list[dict] = []

    for step in range(1, max_steps + 1):
        if vision_only:
            screenshot = client.screenshot()
            print(f"  [{step}/{max_steps}] observing (vision)...", end=" ", flush=True)
            action = ask_claude(system_prompt, "What action should I take?", history, image_data=screenshot)
        else:
            obs = observe(client, vision_only=False)
            elements_text = format_elements(obs)
            print(f"  [{step}/{max_steps}] observing ({len(obs.elements or [])} elements)...", end=" ", flush=True)
            action = ask_claude(system_prompt, f"Current screen elements:\n{elements_text}", history)

        # Extract just the action line (Claude may add extra text)
        for line in action.splitlines():
            line = line.strip()
            if re.match(r"(TAP|SWIPE|TYPE|DONE)\b", line, re.IGNORECASE):
                action = line
                break

        print(f"-> {action}")

        if action.strip().upper() == "DONE":
            return True

        tap_match = re.match(r"TAP\s+([\d.]+)[,\s]+([\d.]+)", action, re.IGNORECASE)
        if tap_match:
            client.tap(float(tap_match.group(1)), float(tap_match.group(2)))
            time.sleep(0.3)
            continue

        swipe_match = re.match(r"SWIPE\s+([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)", action, re.IGNORECASE)
        if swipe_match:
            client.swipe(
                float(swipe_match.group(1)), float(swipe_match.group(2)),
                float(swipe_match.group(3)), float(swipe_match.group(4)),
            )
            time.sleep(0.3)
            continue

        type_match = re.match(r"TYPE\s+(.+)", action, re.IGNORECASE)
        if type_match:
            client.type_text(type_match.group(1))
            time.sleep(0.2)
            continue

        print(f"  [?] Unknown action: {action}")
        break

    return False


def run(
    instructions: str,
    udid: str | None = None,
    bundle_id: str | None = None,
    vision_only: bool = False,
    max_steps: int = 50,
    mirror: bool = False,
    screen: bool = False,
    screen_ff: bool = False,
    background: bool = False,
    screenshot_source: str = "window",
):
    if mirror:
        from .mirror_client import MirrorClient
        with MirrorClient() as client:
            return _run_loop(client, instructions, vision_only=True, max_steps=max_steps)
    elif background:
        from .background_client import BackgroundSimulatorClient
        udid = udid or os.environ.get("SIMULATOR_UDID") or _resolve_booted_udid()
        if not udid:
            raise ValueError("No booted simulator found. Set SIMULATOR_UDID or boot a simulator.")
        with BackgroundSimulatorClient(udid=udid) as client:
            return _run_loop(client, instructions, vision_only=True, max_steps=max_steps)
    elif screen_ff:
        from .focus_free_client import FocusFreeScreenClient
        udid = udid or os.environ.get("SIMULATOR_UDID") or _resolve_booted_udid()
        if not udid:
            raise ValueError("No booted simulator found. Set SIMULATOR_UDID or boot a simulator.")
        with FocusFreeScreenClient(udid=udid, screenshot_source=screenshot_source) as client:
            return _run_loop(client, instructions, vision_only=True, max_steps=max_steps)
    elif screen:
        from .screen_client import SimulatorScreenClient
        udid = udid or os.environ.get("SIMULATOR_UDID") or _resolve_booted_udid()
        if not udid:
            raise ValueError("No booted simulator found. Set SIMULATOR_UDID or boot a simulator.")
        with SimulatorScreenClient(udid=udid, screenshot_source=screenshot_source) as client:
            return _run_loop(client, instructions, vision_only=True, max_steps=max_steps)
    else:
        udid = udid or os.environ.get("SIMULATOR_UDID") or _resolve_booted_udid()
        if not udid:
            raise ValueError("No booted simulator found. Set SIMULATOR_UDID or boot a simulator.")
        bundle_id = bundle_id or os.environ.get("SIMULATOR_BUNDLE_ID")
        if not bundle_id:
            raise ValueError("bundle_id is required (or set SIMULATOR_BUNDLE_ID)")
        with SimulatorClient(udid=udid, bundle_id=bundle_id) as client:
            return _run_loop(client, instructions, vision_only=vision_only, max_steps=max_steps)
