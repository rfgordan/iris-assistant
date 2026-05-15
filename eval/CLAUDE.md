# Driving the eval sweep — read this before running it

The harness only stages each scenario and waits for an in-app `RESULT`
line. The thing that actually exercises the app is a **subagent**: a
Claude Code Agent invocation that reads screenshots and issues input via
the `simulator-agent` CLI.

## Run cells sequentially. Do not run the sweep continuously.

`eval.harness.sweep` runs each cell synchronously:
  1. it prints `READY: spawn a subagent now`,
  2. then **blocks** waiting for the cell's app to emit a `RESULT` line or
     hit its in-app `TimeoutS` (typically 60–90s),
  3. then prints the verdict and moves to the next cell.

If you let the sweep run in the background while spawning subagents async,
you will get:
  - cells that time out because their subagent didn't run yet,
  - subagents that survive past the cell boundary and emit stray taps
    against a *different* scenario,
  - a results JSONL that looks like a model failure but is a sync bug.

This happened on 2026-05-15 — most of the 7-cell sweep produced infra
failures rather than real signal because a slow cell-3 subagent held up
the driver while sweep advanced through cells 4–7.

**Correct flow** — drive one cell at a time:

  1. Run ONE cell: either
     `python -m eval.harness.runner --scenario X --json-result ...`
     or `python -m eval.harness.sweep MANIFEST --group GROUP_NAME ...`
  2. When you see `READY: spawn a subagent now`, spawn **one** subagent
     and **wait** for it to return before doing anything else.
  3. Read the appended JSONL row. Augment with subagent
     `total_tokens` / `tool_uses` / `duration_ms` if you want cost data.
  4. Only after the row is written, start the next cell.

The harness has no idea your subagent exists. It will time out the cell on
its own schedule whether or not a subagent is running.

## Input substrate — use `--screen`

The runner's subagent prompt currently directs subagents to use
`--screen` (`SimulatorScreenClient`):

- `--screen` uses pyautogui on the Simulator.app window — the same
  substrate `--mirror` uses on iPhone Mirroring. Eval results on
  `--screen` predict mirror behavior.
- `--screen-ff` (focus-preserving Quartz) is currently **broken** on
  iOS 26. Its `sess_target_pid_restored` strategy used to deliver
  events to SwiftUI gesture recognizers but no longer does. Avoid for
  evals until fixed.
- `--background` (private SimulatorKit HID) works but bypasses macOS
  entirely. Not predictive of mirror; do not use for representativeness
  evals.

## Screenshot source — `window` (default) vs `framebuffer`

Both `--screen` and `--screen-ff` accept `--screenshot-source`:

- **`window`** (default): pyautogui capture of the Simulator.app window.
  Same pipeline `--mirror` uses on iPhone Mirroring, so observations are
  mirror-representative. Requires the Simulator window to be visible and
  not occluded — `connect()` activates it for you, but if the user has
  another app actively stealing focus during a run the capture may grab
  the wrong pixels.
- **`framebuffer`**: `simctl io screenshot`. Pristine device-resolution
  PNG straight from CoreSimulator. Works regardless of window state or
  focus. Useful for unattended / CI runs where the Mac may be doing other
  things, or when debugging the input substrate without the screenshot
  pipeline as a confound.

The eval default stays `window` because mirror-parity is the point of
the eval. Switch to `framebuffer` only when you're characterizing the
agent's capability ceiling independent of capture fragility.

CLI: `simulator-agent screenshot --screen --screenshot-source framebuffer -o /tmp/shot.png`
Programmatic: `SimulatorScreenClient(udid, screenshot_source="framebuffer")`

## Prefer `scroll` over `swipe`

`scroll(amount)` posts macOS scroll-wheel events, which both Simulator
and iPhone Mirroring translate to iOS swipe-scrolls. `swipe` (mouse
drag) only works on Simulator; iPhone Mirroring filters drag-as-swipe.
Any scenario that needs vertical content movement should use scroll, not
swipe, to stay mirror-compatible.

## When `--screen-ff` is fixed

If `simulator_agent.focus_free_client`'s focus-preserving strategy starts
delivering taps again on iOS 26, switch the runner.py subagent prompt
back to `--screen-ff`. The substrate is otherwise identical.
