"""Sweep runner: takes a JSON manifest, expands the parameter grid, invokes
the per-cell harness, appends machine-readable results to a JSONL file, and
prints a summary table.

Manifest schema (JSON):
    [
      {
        "name": "precision_sweep",            # group label
        "scenario": "tap_target",             # required, becomes -Scenario
        "prompt": "Tap the red circle.",      # shown to subagent / api agent
        "app_timeout_s": 30,
        "sweep": {                            # cartesian product
          "Size":  [200, 100, 60, 40, 20],
          "Seed":  [1, 2, 3]
        },
        "fixed": {                            # optional, applied to every cell
          "BgColor": "white"
        }
      },
      ...
    ]

For each cell the sweep:
  - constructs CLI args for `python -m eval.harness.runner`
  - waits for the runner to exit
  - reads the JSON_RESULT line, augments it with the group name + cell index,
    and appends one line to --results
  - moves to the next cell

Agent mode is set once for the whole sweep via --agent and passed through.
In subagent mode each cell prints a "READY:" banner so an outer driver can
spawn a subagent before the in-app timeout fires.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .runner import JSON_RESULT_PREFIX


def _expand_cells(group: dict) -> list[dict]:
    """Cartesian product of group['sweep'] merged with group['fixed']."""
    sweep = group.get("sweep", {}) or {}
    fixed = group.get("fixed", {}) or {}
    keys = list(sweep.keys())
    if not keys:
        return [dict(fixed)]
    cells: list[dict] = []
    for combo in itertools.product(*[sweep[k] for k in keys]):
        cell = dict(fixed)
        for k, v in zip(keys, combo):
            cell[k] = v
        cells.append(cell)
    return cells


def _build_runner_argv(
    group: dict,
    cell: dict,
    agent_mode: str,
    app_path: Path | None,
) -> list[str]:
    argv = [
        sys.executable, "-m", "eval.harness.runner",
        "--scenario", str(group["scenario"]),
        "--agent", agent_mode,
        "--json-result",
        "--prompt", str(group.get("prompt", "")),
        "--app-timeout-s", str(group.get("app_timeout_s", 30)),
    ]
    if app_path:
        argv.extend(["--app", str(app_path)])
    for k, v in cell.items():
        argv.extend(["--app-arg", f"{k}={v}"])
    return argv


def _parse_json_result(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        if line.startswith(JSON_RESULT_PREFIX):
            try:
                return json.loads(line[len(JSON_RESULT_PREFIX):])
            except json.JSONDecodeError:
                return None
    return None


def _run_cell(
    group: dict,
    cell: dict,
    cell_idx: int,
    total_cells: int,
    agent_mode: str,
    app_path: Path | None,
) -> dict:
    argv = _build_runner_argv(group, cell, agent_mode, app_path)
    cell_label = f"{group['name']}[{cell_idx + 1}/{total_cells}]"
    print(f"\n=== {cell_label}  {cell} ===")
    if agent_mode == "subagent":
        # NOTE for any coding agent driving this: spawn the subagent NOW and
        # wait for it to return before letting this script advance to the
        # next cell. The subprocess.run call below blocks for up to TimeoutS
        # — once it returns, the next cell launches a fresh app and the
        # window of opportunity for the prior subagent is gone. Running the
        # sweep continuously while spawning subagents async produces infra
        # failures, not model signal. See eval/CLAUDE.md.
        print(f"READY: spawn a subagent now to handle this cell. Prompt: {group.get('prompt')!r}")
    start = time.monotonic()
    proc = subprocess.run(argv, capture_output=True, text=True)
    wall = time.monotonic() - start
    if proc.stdout:
        # Echo runner stdout so the user sees launch/verdict in context.
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    parsed = _parse_json_result(proc.stdout) or {
        "verdict": "timeout",
        "scenario": group["scenario"],
        "scenario_args": {},
        "result_fields": {},
        "wall_s": round(wall, 2),
    }
    parsed["group"] = group["name"]
    parsed["cell_index"] = cell_idx
    parsed["cell"] = cell
    parsed["rc"] = proc.returncode
    return parsed


def _print_summary(results: list[dict]) -> None:
    print("\n=== Summary ===")
    groups: dict[str, list[dict]] = {}
    for r in results:
        groups.setdefault(r["group"], []).append(r)
    for name, rs in groups.items():
        passed = sum(1 for r in rs if r["verdict"] == "pass")
        total = len(rs)
        group_tokens = sum((r.get("subagent") or {}).get("total_tokens", 0) for r in rs)
        token_part = f"  tokens={group_tokens:,}" if group_tokens else ""
        print(f"  {name}: {passed}/{total} pass{token_part}")
        for r in rs:
            verdict = r["verdict"]
            cell_str = " ".join(f"{k}={v}" for k, v in r["cell"].items())
            rf = r.get("result_fields") or {}
            extras = f"  reason={rf['reason']}" if verdict != "pass" and rf.get("reason") else ""
            sub = r.get("subagent") or {}
            tok = f"  tokens={sub['total_tokens']:,}" if "total_tokens" in sub else ""
            tu = f" tools={sub['tool_uses']}" if "tool_uses" in sub else ""
            print(f"    [{verdict:>5}] {cell_str}{extras}  wall={r['wall_s']}s{tok}{tu}")


def main():
    parser = argparse.ArgumentParser(prog="eval-sweep")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--agent", choices=["subagent", "api", "none"], default="subagent")
    parser.add_argument("--results", type=Path, default=Path("/tmp/eval_results.jsonl"),
                        help="Append one JSON line per cell here")
    parser.add_argument("--app", type=Path, default=None,
                        help="Override the EvalApp.app bundle path")
    parser.add_argument("--group", default=None,
                        help="Run only the group whose name matches this filter")
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"ERR: manifest {args.manifest} not found", file=sys.stderr)
        sys.exit(2)

    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest, list):
        print("ERR: manifest must be a JSON array of group dicts", file=sys.stderr)
        sys.exit(2)

    if args.group:
        manifest = [g for g in manifest if g.get("name") == args.group]
        if not manifest:
            print(f"ERR: no group named {args.group!r}", file=sys.stderr)
            sys.exit(2)

    results: list[dict] = []
    args.results.parent.mkdir(parents=True, exist_ok=True)
    # Append-only — preserves prior sweeps in the same JSONL.
    with args.results.open("a") as f:
        for group in manifest:
            cells = _expand_cells(group)
            for i, cell in enumerate(cells):
                result = _run_cell(group, cell, i, len(cells), args.agent, args.app)
                f.write(json.dumps(result) + "\n")
                f.flush()
                results.append(result)

    _print_summary(results)
    print(f"\nResults appended to {args.results}")


if __name__ == "__main__":
    main()
