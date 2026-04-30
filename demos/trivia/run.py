"""Play a trivia game autonomously."""

from pathlib import Path
from simulator_agent.agent import run

instructions = (Path(__file__).parent / "instructions.md").read_text()
run(instructions, bundle_id="iOSTrivia")
