"""Eval runner: one scenario, fixed config, end-to-end.

Flow:
    1. Find a booted simulator (raises if none).
    2. Uninstall+install EvalApp.app.
    3. Start tailing com.eval log stream.
    4. simctl launch the app with scenario args.
    5. Wait for a RESULT line OR overall timeout.
    6. Print outcome, exit 0 on pass / 1 otherwise.

Agent modes (controlled by --agent):
    subagent  (default)  Harness only stages the scenario and waits. The
                         agent is driven by a Claude Code subagent spawned
                         in the outer session: the outer Claude reads the
                         scenario context, spawns a subagent with a prompt
                         like "Tap the red circle. Use simulator-agent CLI
                         primitives with --background.", and the subagent
                         uses Bash to call `simulator-agent tap ... --background`
                         / `screenshot --background` / etc. No API key.
    api                  Harness spawns `python -m simulator_agent run`
                         which uses the Anthropic SDK directly. Requires
                         ANTHROPIC_API_KEY. Useful for CI / unattended runs.
    none                 Like `subagent`, but signals that nothing is
                         expected to drive the agent. The app will hit its
                         TimeoutS and emit `fail reason=timeout`. Use this
                         to smoke-test the harness pipeline itself.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import simctl
from .log_listener import LogListener


JSON_RESULT_PREFIX = "JSON_RESULT "


DEFAULT_APP = Path(__file__).resolve().parents[1] / "EvalApp" / "build" / "EvalApp.app"
BUNDLE_ID = "com.eval.app"


def _build_launch_args(scenario_args: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in scenario_args.items():
        args.append(f"-{key}")
        args.append(str(value))
    return args


def _spawn_agent(prompt: str, max_steps: int) -> subprocess.Popen:
    """Spawn `python -m simulator_agent run <prompt_file>` as a child.

    The agent connects to the booted simulator on its own. Stdout is
    inherited so the user sees the agent's step-by-step output.
    """
    prompt_path = Path("/tmp/eval_prompt.md")
    prompt_path.write_text(prompt)
    env = os.environ.copy()
    env["SIMULATOR_BUNDLE_ID"] = BUNDLE_ID
    return subprocess.Popen(
        [
            sys.executable, "-m", "simulator_agent", "run",
            str(prompt_path),
            "--background",
            "--vision-only",
            "--max-steps", str(max_steps),
        ],
        env=env,
    )


def run(
    app_path: Path,
    scenario_args: dict[str, str],
    agent_mode: str,
    prompt: str,
    timeout_s: float,
    max_steps: int,
    json_result: bool = False,
) -> int:
    if not app_path.exists():
        print(f"ERR: {app_path} not found. Run `make build` in eval/EvalApp first.", file=sys.stderr)
        return 2

    udid = simctl.booted_udid()
    print(f"  sim:      {udid}")
    print(f"  app:      {app_path}")
    print(f"  scenario: {scenario_args}")
    print(f"  mode:     {agent_mode}")

    simctl.terminate(BUNDLE_ID, udid)
    simctl.uninstall(BUNDLE_ID, udid)
    simctl.install(str(app_path), udid)

    with LogListener(udid=udid) as listener:
        launch_args = _build_launch_args(scenario_args)
        pid = simctl.launch(BUNDLE_ID, launch_args, udid)
        print(f"  launched pid={pid}")

        agent_proc: subprocess.Popen | None = None
        if agent_mode == "api":
            agent_proc = _spawn_agent(prompt, max_steps)
            print(f"  agent:    spawned via Anthropic SDK (pid={agent_proc.pid})")
        elif agent_mode == "subagent":
            print(f"  agent:    awaiting Claude Code subagent")
            driver_prompt = (
                f"{prompt} Use simulator-agent CLI primitives with the --background flag "
                "(for example `simulator-agent tap x y --background`) so input is delivered "
                "through CoreSimulator HID without desktop focus changes."
            )
            print(f"            prompt: {driver_prompt!r}")
        else:
            print("  agent:    none (smoke test, expect timeout)")

        start = time.monotonic()
        result = listener.wait_for_result(timeout=timeout_s)
        elapsed = time.monotonic() - start

        if agent_proc and agent_proc.poll() is None:
            agent_proc.terminate()
            try:
                agent_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                agent_proc.kill()

        print()
        if result is None:
            print(f"  [TIMEOUT] no RESULT line in {timeout_s:.0f}s")
            if json_result:
                _emit_json_result(scenario_args, "timeout", None, elapsed)
            return 1
        print(f"  [{result.verdict.upper()}] {result}")
        print(f"  wall:     {elapsed:.1f}s")
        if json_result:
            _emit_json_result(scenario_args, result.verdict, result.fields, elapsed)
        return 0 if result.passed else 1


def _emit_json_result(scenario_args: dict, verdict: str, fields: dict | None, wall_s: float) -> None:
    payload = {
        "verdict": verdict,
        "scenario": scenario_args.get("Scenario", "unknown"),
        "scenario_args": scenario_args,
        "result_fields": fields or {},
        "wall_s": round(wall_s, 2),
    }
    print(JSON_RESULT_PREFIX + json.dumps(payload, default=str))


def main():
    parser = argparse.ArgumentParser(prog="eval-runner")
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--scenario", default="tap_target")
    parser.add_argument("--size", type=int, default=80)
    parser.add_argument("--position-mode", default="fixed", choices=["fixed", "random"])
    parser.add_argument("--target-x", type=int, default=None)
    parser.add_argument("--target-y", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--bg-color", default="white", choices=["white", "gray"])
    parser.add_argument("--target-color", default="red", choices=["red", "blue", "green"])
    parser.add_argument("--app-timeout-s", type=float, default=30.0,
                        help="In-app auto-fail timeout passed to the scenario")
    parser.add_argument("--harness-timeout-s", type=float, default=None,
                        help="Outer wait; defaults to app-timeout + 10")
    parser.add_argument("--agent", choices=["subagent", "api", "none"], default="subagent",
                        help="subagent (default): outer Claude Code drives a subagent that calls simulator-agent CLI; "
                             "api: spawn standalone background-HID agent via Anthropic SDK (needs ANTHROPIC_API_KEY); "
                             "none: smoke-test the harness, expect timeout")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--prompt", default="Tap the red circle.")
    parser.add_argument("--app-arg", action="append", default=[], metavar="KEY=VALUE",
                        help="Extra launch arg forwarded as `-Key Value` to the app. Repeatable.")
    parser.add_argument("--json-result", action="store_true",
                        help="After the verdict block, emit a single JSON_RESULT line for machine parsing")

    args = parser.parse_args()

    scenario_args = {
        "Scenario": args.scenario,
        "Size": args.size,
        "PositionMode": args.position_mode,
        "Seed": args.seed,
        "BgColor": args.bg_color,
        "TargetColor": args.target_color,
        "TimeoutS": args.app_timeout_s,
    }
    if args.target_x is not None:
        scenario_args["TargetX"] = args.target_x
    if args.target_y is not None:
        scenario_args["TargetY"] = args.target_y
    # Generic pass-through args override / extend the named ones.
    for kv in args.app_arg:
        if "=" not in kv:
            print(f"ERR: --app-arg expects KEY=VALUE, got: {kv!r}", file=sys.stderr)
            sys.exit(2)
        k, v = kv.split("=", 1)
        scenario_args[k] = v

    harness_timeout = args.harness_timeout_s
    if harness_timeout is None:
        harness_timeout = args.app_timeout_s + 10

    rc = run(
        app_path=args.app,
        scenario_args=scenario_args,
        agent_mode=args.agent,
        prompt=args.prompt,
        timeout_s=harness_timeout,
        max_steps=args.max_steps,
        json_result=args.json_result,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
