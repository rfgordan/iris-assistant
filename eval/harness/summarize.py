"""Print a summary table from a results JSONL file.

Each line is one cell result (as produced by sweep.py / runner.py
--json-result). Optionally augmented with a `subagent` field carrying
{ total_tokens, tool_uses, duration_ms, ... } when the cell was driven
by a Claude Code subagent — that field is appended by the outer driver
after the harness exits, since subagent usage isn't visible to the
harness itself.

Usage:
    python3 -m eval.harness.summarize results.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _read(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"WARN: skipping malformed line: {e}", file=sys.stderr)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval-summarize")
    parser.add_argument("results", type=Path)
    args = parser.parse_args()

    if not args.results.exists():
        print(f"ERR: {args.results} not found", file=sys.stderr)
        return 2

    rows = _read(args.results)
    if not rows:
        print("(no results)")
        return 0

    cols = ("#", "scenario", "verdict", "wall_s", "tokens", "tools", "reason")
    widths = {"#": 3, "scenario": 22, "verdict": 7, "wall_s": 7,
              "tokens": 8, "tools": 6, "reason": 14}

    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * (sum(widths.values()) + 2 * (len(cols) - 1)))

    total_tokens = 0
    total_tool_uses = 0
    pass_count = 0
    for i, r in enumerate(rows, 1):
        sub = r.get("subagent") or {}
        rf = r.get("result_fields") or {}
        reason = rf.get("reason", "") if r["verdict"] != "pass" else ""
        tokens = sub.get("total_tokens")
        tool_uses = sub.get("tool_uses")
        wall = r.get("wall_s", 0)
        if r["verdict"] == "pass":
            pass_count += 1
        if tokens:
            total_tokens += tokens
        if tool_uses:
            total_tool_uses += tool_uses
        cells = [
            str(i),
            (r.get("scenario") or "")[: widths["scenario"]],
            r.get("verdict", ""),
            f"{wall:.1f}",
            f"{tokens:,}" if tokens else "—",
            str(tool_uses) if tool_uses else "—",
            reason,
        ]
        print("  ".join(c.ljust(widths[col]) for c, col in zip(cells, cols)))

    print()
    print(f"  pass:        {pass_count}/{len(rows)} ({pass_count / len(rows) * 100:.0f}%)")
    if total_tokens:
        print(f"  tokens:      {total_tokens:,}")
        print(f"  avg/cell:    {total_tokens // len(rows):,}")
    if total_tool_uses:
        print(f"  tool calls:  {total_tool_uses}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
