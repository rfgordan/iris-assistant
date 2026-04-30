# Trivia Demo

Autonomous trivia game player using iClaude.

## App

[TriviaQuizApp](https://github.com/nealarch01/TriviaQuizApp) by Neal Archival — a SwiftUI trivia game with multiple categories, difficulty levels, and question types.

Clone and run it in the iOS Simulator before starting.

## How to run

1. Make sure Appium is running: `appium --relaxed-security --port 4723`
2. Set your env vars:
   ```bash
   export SIMULATOR_UDID=booted
   export SIMULATOR_BUNDLE_ID=iOSTrivia
   export ANTHROPIC_API_KEY=<your-key>
   ```
3. Run the autonomous player:
   ```bash
   python demos/trivia/run.py
   ```

## Files

- `run.py` — loads instructions and runs the agent
- `instructions.md` — task-specific goal and rules for the trivia game
