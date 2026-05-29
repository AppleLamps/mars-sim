"""Deterministic physics regression: real loop + scripted LLM => exact world trace.

Guards world.py mechanics. If this test's expected values change, world dynamics
changed — intentionally (update the expected values) or by accident (a real bug).
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from unittest.mock import patch

from utils import reset_cost_tracker
from world import create_initial_state


FOCI = ["atmosphere", "geology", "sensors", "crew"]


class ScriptedCompletions:
    def __init__(self) -> None:
        self.call_count = 0

    def create(self, **kwargs):
        focus = FOCI[self.call_count % len(FOCI)]
        self.call_count += 1
        action_json = json.dumps({
            "action_type": "observe_environment",
            "parameters": {"focus": focus},
            "reasoning": "Routine scan of the habitat surroundings. Maintaining situational awareness.",
            "confidence": 0.5,
        })
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=action_json))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class ScriptedClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=ScriptedCompletions())


class PhysicsRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_cost_tracker("openai/gpt-4o-mini")

    def test_deterministic_world_trace_over_5_sols(self) -> None:
        import main as sim_main

        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(
                sols=5,
                model="openai/gpt-4o-mini",
                seed=123,
                checkpoint_dir=os.path.join(tmpdir, "checkpoints"),
                log_file=os.path.join(tmpdir, "logs", "sim.jsonl"),
                resume=None,
                fresh_log=True,
                fast_path=False,
            )
            with (
                patch("main.load_dotenv"),
                patch("main.setup_logging"),
                patch("main.create_openrouter_client", return_value=ScriptedClient()),
                patch("main.print_phase_summary"),
            ):
                sim_main.run_simulation(args)

            # Load the final checkpoint that run_simulation wrote.
            latest = os.path.join(args.checkpoint_dir, "latest.json")
            with open(latest, encoding="utf-8") as f:
                payload = json.load(f)
            world_state = payload["world"]

        # PIN exact values. First run will FAIL and print actuals — copy them in, then it locks.
        self.assertEqual(123, world_state["seed"])
        self.assertEqual(15, world_state["phase_index"])  # 5 sols * 3 phases
        # Resource values are deterministic given seed + all-observe actions.
        # Replace the None placeholders below with the printed actuals after first run.
        expected = {
            "oxygen": 81.4893,
            "water": 352.2268,
            "food_days": 40.3426,
            "habitat_integrity": 82.5813,
            "power_level": 85.0,
        }
        actuals = {k: round(world_state[k], 4) for k in expected}
        # On first run, print and fail so you can capture the golden values:
        if any(v is None for v in expected.values()):
            self.fail(f"Set expected values to: {actuals}")
        for key, exp in expected.items():
            self.assertAlmostEqual(exp, world_state[key], places=3,
                msg=f"{key} drifted: world dynamics changed (expected {exp}, got {world_state[key]})")


if __name__ == "__main__":
    unittest.main()
