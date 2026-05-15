#!/usr/bin/env python3
"""Claude Code PostToolUse hook for git commits.

Fires after every Bash tool call. If the command was a `git commit` and the
new HEAD changed files referenced by the README mermaid diagram, emits
`additionalContext` so Claude sees a system reminder to review/update the
diagram. Exits silently otherwise.

Wired up in .claude/settings.json under hooks.PostToolUse with matcher=Bash.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys


# Patterns of paths that, when added or significantly modified, may
# require an update to the README's '### Architecture' mermaid diagram.
DIAGRAM_PATHS = [
    r"^simulator_agent/[a-z_]+_client\.py$",
    r"^simulator_agent/__main__\.py$",
    r"^simulator_agent/agent\.py$",
    r"^eval/harness/[a-z_]+\.py$",
    r"^eval/EvalApp/EvalApp/Scenarios/[A-Z][a-zA-Z]+\.swift$",
    r"^eval/EvalApp/EvalApp/ScenarioRouter\.swift$",
]
SIGNIFICANT_LINE_THRESHOLD = 30


def _matches(path: str) -> bool:
    return any(re.match(p, path) for p in DIAGRAM_PATHS)


def _git_lines(*args: str) -> list[str]:
    try:
        out = subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return []
    return [ln for ln in out.splitlines() if ln]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    if data.get("tool_name") != "Bash":
        return 0
    cmd = data.get("tool_input", {}).get("command", "")
    if not re.search(r"\bgit\s+commit\b", cmd):
        return 0

    added = set(_git_lines("diff-tree", "--no-commit-id", "--name-only",
                           "--diff-filter=A", "-r", "HEAD"))
    significant: set[str] = set()
    for line in _git_lines("diff-tree", "--no-commit-id", "--numstat", "-r", "HEAD"):
        parts = line.split("\t")
        if len(parts) != 3 or "-" in (parts[0], parts[1]):
            continue
        try:
            if int(parts[0]) + int(parts[1]) >= SIGNIFICANT_LINE_THRESHOLD:
                significant.add(parts[2])
        except ValueError:
            continue

    changed = added | significant
    hits = sorted(p for p in changed if _matches(p))
    if not hits:
        return 0

    added_hits = sorted(p for p in hits if p in added)
    modified_hits = sorted(p for p in hits if p not in added)

    parts = [
        "Diagram-staleness check: the commit you just made touches files "
        "referenced by the README's '### Architecture' mermaid diagram. "
        "Please review the diagram and update it if these changes affect "
        "the flow or component shape."
    ]
    if added_hits:
        parts.append("")
        parts.append("New files (likely need a new node/edge):")
        parts.extend(f"  + {f}" for f in added_hits)
    if modified_hits:
        parts.append("")
        parts.append("Significantly changed files (review for diagram impact):")
        parts.extend(f"  ~ {f}" for f in modified_hits)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
