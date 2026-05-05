from __future__ import annotations

import argparse
import os
import sys

from .client import SimulatorClient
from .coords import pixels_to_points
from .observation import observe, elements_to_json


def _make_client(args) -> SimulatorClient:
    from .agent import _resolve_booted_udid
    udid = args.udid or os.environ.get("SIMULATOR_UDID") or _resolve_booted_udid()
    if not udid:
        print("Error: No booted simulator found. Use --udid or set SIMULATOR_UDID", file=sys.stderr)
        sys.exit(1)
    bundle_id = args.bundle_id or os.environ.get("SIMULATOR_BUNDLE_ID")
    if not bundle_id:
        print("Error: --bundle-id or SIMULATOR_BUNDLE_ID is required", file=sys.stderr)
        sys.exit(1)
    return SimulatorClient(
        udid=udid,
        bundle_id=bundle_id,
        host=args.host,
        port=args.port,
    )


def cmd_screenshot(args):
    output = args.output or "/tmp/simulator_screenshot.png"
    with _make_client(args) as client:
        client.screenshot_to_file(output)
    print(output)


def cmd_tap(args):
    x, y = args.x, args.y
    if args.pixels:
        x, y = pixels_to_points(x, y)
    with _make_client(args) as client:
        client.tap(x, y)
    print(f"Tapped ({x:.0f}, {y:.0f})")


def cmd_swipe(args):
    with _make_client(args) as client:
        client.swipe(args.x1, args.y1, args.x2, args.y2, duration_ms=args.duration)
    print(f"Swiped ({args.x1}, {args.y1}) → ({args.x2}, {args.y2})")


def cmd_type(args):
    with _make_client(args) as client:
        client.type_text(args.text)
    print(f"Typed: {args.text}")


def cmd_source(args):
    with _make_client(args) as client:
        source = client.get_source()
        if args.format == "raw":
            print(source)
        else:
            from .observation import parse_elements
            elements = parse_elements(source)
            print(elements_to_json(elements))


def cmd_observe(args):
    output = args.output or "/tmp/simulator_observation.png"
    with _make_client(args) as client:
        obs = observe(client, vision_only=args.vision_only)
        with open(output, "wb") as f:
            f.write(obs.screenshot)
        print(f"Screenshot: {output}")
        print(f"Screen size: {obs.screen_size[0]}x{obs.screen_size[1]} points")
        if obs.elements is not None:
            print(f"Elements: {len(obs.elements)}")
            for el in obs.elements:
                label = el.label or "(no label)"
                print(f"  [{el.type.replace('XCUIElementType', '')}] {label} @ ({el.x:.0f}, {el.y:.0f})")
        else:
            print("Elements: skipped (vision-only mode)")


def cmd_run(args):
    from pathlib import Path
    from .agent import run

    instructions_path = Path(args.instructions)
    if not instructions_path.exists():
        print(f"Error: {instructions_path} not found", file=sys.stderr)
        sys.exit(1)

    instructions = instructions_path.read_text()
    if args.mirror:
        mode = "mirror (vision-only)"
        bundle = "iPhone Mirroring"
    else:
        mode = "vision-only" if args.vision_only else "elements"
        bundle = args.bundle_id or os.environ.get("SIMULATOR_BUNDLE_ID", "?")

    print()
    print(f"  App:   {bundle}")
    print(f"  Mode:  {mode}")
    print(f"  Steps: {args.max_steps}")
    print()

    result = run(
        instructions,
        bundle_id=args.bundle_id or os.environ.get("SIMULATOR_BUNDLE_ID"),
        vision_only=args.vision_only,
        max_steps=args.max_steps,
        mirror=args.mirror,
    )

    print()
    if result:
        print("  [OK] Done")
    else:
        print("  [FAIL] Did not finish")


def main():
    parser = argparse.ArgumentParser(
        prog="simulator-agent",
        description="Interact with iOS Simulator via Appium",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=4723)
    parser.add_argument("--udid", default=None, help="Simulator UDID (or set SIMULATOR_UDID)")
    parser.add_argument("--bundle-id", default=None, help="App bundle ID (or set SIMULATOR_BUNDLE_ID)")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("screenshot", help="Take a screenshot")
    p.add_argument("--output", "-o", help="Output file path")
    p.set_defaults(func=cmd_screenshot)

    p = sub.add_parser("tap", help="Tap at coordinates")
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("--pixels", action="store_true", help="Coordinates are in pixels (will convert to points)")
    p.set_defaults(func=cmd_tap)

    p = sub.add_parser("swipe", help="Swipe between two points")
    p.add_argument("x1", type=float)
    p.add_argument("y1", type=float)
    p.add_argument("x2", type=float)
    p.add_argument("y2", type=float)
    p.add_argument("--duration", type=int, default=800, help="Duration in ms")
    p.set_defaults(func=cmd_swipe)

    p = sub.add_parser("type", help="Type text")
    p.add_argument("text")
    p.set_defaults(func=cmd_type)

    p = sub.add_parser("source", help="Get element tree")
    p.add_argument("--format", choices=["raw", "json"], default="json")
    p.set_defaults(func=cmd_source)

    p = sub.add_parser("observe", help="Take a full observation")
    p.add_argument("--vision-only", action="store_true", help="Skip element tree, screenshot only")
    p.add_argument("--output", "-o", help="Screenshot output path")
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("run", help="Run the autonomous agent on an app")
    p.add_argument("instructions", help="Path to instructions file (markdown)")
    p.add_argument("--vision-only", action="store_true", help="Use screenshots only, no element tree")
    p.add_argument("--mirror", action="store_true", help="Control real iPhone via iPhone Mirroring (vision-only)")
    p.add_argument("--max-steps", type=int, default=50, help="Max agent steps")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
