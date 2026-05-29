from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from random import Random
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

import main as sim_main
from actions import AgentAction
from agent import StepResult
from utils import call_agent_llm, reset_cost_tracker
from world import create_initial_state


class RetryableError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeCompletions:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.role = name
        self.last_action_summary = None
        self.last_reasoning = None

    def step(self, world, client, model):
        return StepResult(
            agent_name=self.name,
            action=None,
            world_deltas=[],
            skipped=True,
            error="test step skipped",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "role": self.role,
            "short_term_memory": [],
            "diary": [],
            "private_beliefs": {},
            "last_action_summary": self.last_action_summary,
            "last_action_type": None,
            "last_reasoning": self.last_reasoning,
        }


def make_response(raw_text: str, prompt_tokens: int = 12, completion_tokens: int = 6):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw_text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


class RegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_cost_tracker("openai/gpt-4o-mini")

    def test_propose_vote_options_must_be_unique(self) -> None:
        with self.assertRaises(ValidationError):
            AgentAction.model_validate(
                {
                    "action_type": "propose_vote",
                    "parameters": {
                        "topic": "Power rationing",
                        "options": ["Yes", " yes "],
                    },
                    "reasoning": "We need a clear decision. Voting keeps the crew aligned.",
                    "confidence": 0.72,
                }
            )

    def test_duplicate_pending_vote_topic_is_rejected(self) -> None:
        world = create_initial_state()
        action = AgentAction.model_validate(
            {
                "action_type": "propose_vote",
                "parameters": {
                    "topic": "Power rationing",
                    "options": ["Yes", "No"],
                },
                "reasoning": "We need a clear decision. Voting keeps the crew aligned.",
                "confidence": 0.84,
            }
        )

        first = world.apply_action(action, "Commander", Random(0))
        second = world.apply_action(action, "Engineer", Random(0))

        self.assertIn("Vote proposed", first[0])
        self.assertEqual(1, len(world.pending_votes))
        self.assertEqual(
            ["Vote already pending for topic 'Power rationing'"],
            second,
        )

    def test_conduct_eva_requires_enough_fuel_for_duration(self) -> None:
        world = create_initial_state()
        world.rover_fuel = 11.0
        action = AgentAction.model_validate(
            {
                "action_type": "conduct_eva",
                "parameters": {
                    "site": "Sector C",
                    "duration_hours": 4,
                },
                "reasoning": "The sample site is important. We should only go if the rover can complete the trip safely.",
                "confidence": 0.66,
            }
        )

        deltas = world.apply_action(action, "Scientist", Random(0))

        self.assertEqual(
            ["EVA aborted: insufficient rover fuel for 4h EVA"],
            deltas,
        )
        self.assertEqual(11.0, world.rover_fuel)
        self.assertEqual([], world.collected_samples)

    def test_call_agent_llm_retries_retryable_errors(self) -> None:
        raw_json = (
            '{"action_type":"write_diary","parameters":{"entry":"Holding steady."},'
            '"reasoning":"We should keep a concise log. This records the current state.",'
            '"confidence":0.81}'
        )
        client = FakeClient(
            [
                Exception("json mode unsupported"),
                RetryableError("503 overloaded", 503),
                Exception("json mode unsupported"),
                RetryableError("503 overloaded", 503),
                Exception("json mode unsupported"),
                make_response(raw_json),
            ]
        )

        with patch("utils.time.sleep", return_value=None) as sleep_mock:
            result = call_agent_llm(
                client,
                "openai/gpt-4o-mini",
                "system",
                "user",
            )

        self.assertIsNone(result.error)
        self.assertEqual(raw_json, result.raw_text)
        self.assertEqual(12, result.prompt_tokens)
        self.assertEqual(6, result.completion_tokens)
        self.assertEqual(6, len(client.chat.completions.calls))
        self.assertEqual(2, sleep_mock.call_count)

    def test_periodic_checkpoint_saves_next_phase_cursor(self) -> None:
        args = argparse.Namespace(
            sols=2,
            model="openai/gpt-4o-mini",
            seed=7,
            checkpoint_dir="",
            log_file="",
            resume=None,
            fresh_log=True,
            fast_path=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            args.checkpoint_dir = os.path.join(tmpdir, "checkpoints")
            args.log_file = os.path.join(tmpdir, "logs", "simulation.jsonl")
            fake_agents = [
                FakeAgent("Commander"),
                FakeAgent("Engineer"),
                FakeAgent("Scientist"),
                FakeAgent("Medic"),
            ]

            with (
                patch("main.load_dotenv"),
                patch("main.setup_logging"),
                patch("main.create_openrouter_client", return_value=object()),
                patch("main.create_agents", return_value=fake_agents),
                patch("main.print_phase_summary"),
            ):
                sim_main.run_simulation(args)

            checkpoint_files = set(os.listdir(args.checkpoint_dir))

        self.assertIn("state_sol2_phaseevening_idx5.json", checkpoint_files)
        self.assertNotIn("state_sol2_phasemidday_idx4.json", checkpoint_files)

    def test_power_coupling_throttles_downstream_at_low_power(self) -> None:
        from world import create_initial_state

        # Full power: factor should be 1.0
        high = create_initial_state()
        high.power_level = 100.0
        self.assertAlmostEqual(1.0, high._power_coupling_factor())
        # Zero power: factor should equal coupling_min_factor
        low = create_initial_state()
        low.power_level = 0.0
        self.assertAlmostEqual(low.config.coupling_min_factor, low._power_coupling_factor())
        # At threshold exactly: still 1.0
        edge = create_initial_state()
        edge.power_level = edge.config.coupling_power_threshold
        self.assertAlmostEqual(1.0, edge._power_coupling_factor())
        # Halfway below threshold: linear midpoint
        mid = create_initial_state()
        mid.power_level = mid.config.coupling_power_threshold / 2
        expected = mid.config.coupling_min_factor + (1.0 - mid.config.coupling_min_factor) * 0.5
        self.assertAlmostEqual(expected, mid._power_coupling_factor())


    def test_propose_vote_cooldown_blocks_rapid_reproposal(self) -> None:
        from world import create_initial_state
        from actions import AgentAction
        from random import Random

        world = create_initial_state()

        def make_vote(topic: str) -> AgentAction:
            return AgentAction.model_validate({
                "action_type": "propose_vote",
                "parameters": {"topic": topic, "options": ["Yes", "No"]},
                "reasoning": "We need a decision. A vote keeps the crew aligned.",
                "confidence": 0.8,
            })

        # First proposal at phase_index 0 succeeds.
        first = world.apply_action(make_vote("Power rationing"), "Commander", Random(0))
        self.assertIn("Vote proposed", first[0])

        # Same agent proposes a DIFFERENT topic immediately (still phase 0) -> blocked by cooldown.
        second = world.apply_action(make_vote("EVA schedule"), "Commander", Random(0))
        self.assertEqual(1, len(second))
        self.assertIn("cooldown", second[0].lower())

        # Advance phase_index past the cooldown window, then it should succeed again.
        world.phase_index = world.config.vote_proposal_cooldown_phases
        third = world.apply_action(make_vote("EVA schedule"), "Commander", Random(0))
        self.assertIn("Vote proposed", third[0])


    def test_fast_path_skips_only_when_enabled_and_quiet(self) -> None:
        from world import create_initial_state
        from dataclasses import replace

        # Fresh state is comfortably above all floors and has no storm/votes => quiet.
        world = create_initial_state()
        self.assertTrue(world.is_quiet_phase("Commander"))

        # A dust storm makes it non-quiet.
        from world import DustStorm
        world.active_dust_storm = DustStorm(power_multiplier=0.4, phases_remaining=2)
        self.assertFalse(world.is_quiet_phase("Commander"))
        world.active_dust_storm = None

        # Low oxygen makes it non-quiet.
        world.oxygen = 50.0
        self.assertFalse(world.is_quiet_phase("Commander"))
        world.oxygen = 92.0

        # A pending vote the agent hasn't cast on makes it non-quiet for that agent.
        from world import PendingVote
        world.pending_votes.append(
            PendingVote(topic="Test topic", options=["Yes", "No"], proposer="Engineer", sol=1, phase="morning")
        )
        self.assertFalse(world.is_quiet_phase("Commander"))
        # But an agent who already voted sees it as quiet (assuming nothing else fails).
        world.pending_votes[0].votes["Commander"] = "Yes"
        self.assertTrue(world.is_quiet_phase("Commander"))


if __name__ == "__main__":
    unittest.main()
