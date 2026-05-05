# iClaude

Autonomous AI agent for iOS — Simulator and real devices via Appium.

## Setup

```bash
# Install iClaude
pip install -e .

# Install Appium + iOS driver
npm install -g appium && appium driver install xcuitest

# Start the Appium server
appium --relaxed-security --port 4723
```

## Usage

Set your device env vars:
```bash
export SIMULATOR_UDID=booted
export SIMULATOR_BUNDLE_ID=<your-app-bundle-id>
```

Then ask Claude Code to interact with your app. Claude uses the `simulator_agent` CLI under the hood to observe the screen, tap elements, type text, and swipe — all autonomously.

## Modes

iClaude supports two observation modes:

- **Vision + Elements** (default) — takes a screenshot and reads the accessibility element tree. The agent gets exact element labels, types, and coordinates. More reliable.
- **Vision-only** (`--vision-only`) — screenshot only, no element tree. The agent must interpret the image and estimate tap coordinates from pixels. Useful for benchmarking visual reasoning.

## Agent

The current agent implementation is a naive agentic loop: observe → send elements to Claude → parse action → execute → repeat. It calls the Anthropic API with task-specific instructions provided by the user.

```bash
# Write your instructions in a markdown file, then:
python demos/trivia/run.py
```

See [demos/](./demos/) for examples.

## Demo

Tested with [TriviaQuizApp](https://github.com/nealarch01/TriviaQuizApp) — a SwiftUI trivia game played fully autonomously.

## Credits

`apps/TriviaQuizApp/` is vendored from [TriviaQuizApp](https://github.com/nealarch01/TriviaQuizApp) by Neal Archival, used under the MIT License. See [`apps/TriviaQuizApp/LICENSE`](./apps/TriviaQuizApp/LICENSE).
