from __future__ import annotations

import json as json_mod
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict

from .client import SimulatorClient


@dataclass
class Element:
    type: str
    label: str | None
    value: str | None
    enabled: bool
    visible: bool
    x: float  # center x in points
    y: float  # center y in points
    width: float
    height: float

    def to_dict(self) -> dict:
        return asdict(self)


def parse_elements(xml_source: str) -> list[Element]:
    root = ET.fromstring(xml_source)
    elements: list[Element] = []

    for node in root.iter():
        try:
            w = float(node.get("width", 0))
            h = float(node.get("height", 0))
        except (ValueError, TypeError):
            continue

        if w == 0 or h == 0:
            continue

        visible = node.get("visible", "false") == "true"
        if not visible:
            continue

        accessible = node.get("accessible", "false") == "true"
        if not accessible:
            continue

        raw_x = float(node.get("x", 0))
        raw_y = float(node.get("y", 0))

        elements.append(
            Element(
                type=node.get("type", ""),
                label=node.get("label") or node.get("name"),
                value=node.get("value"),
                enabled=node.get("enabled", "false") == "true",
                visible=True,
                x=raw_x + w / 2,
                y=raw_y + h / 2,
                width=w,
                height=h,
            )
        )

    return elements


def elements_to_json(elements: list[Element]) -> str:
    return json_mod.dumps([e.to_dict() for e in elements], indent=2)


@dataclass
class Observation:
    screenshot: bytes
    elements: list[Element] | None
    screen_size: tuple[int, int]


def observe(client: SimulatorClient, vision_only: bool = False) -> Observation:
    screenshot = client.screenshot()
    screen_size = client.get_screen_size()

    elements = None
    if not vision_only:
        source = client.get_source()
        elements = parse_elements(source)

    return Observation(
        screenshot=screenshot,
        elements=elements,
        screen_size=screen_size,
    )
