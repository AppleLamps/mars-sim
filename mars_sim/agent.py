"""Agent dataclass and step logic."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from actions import ActionType, AgentAction
from prompts import build_user_prompt, get_system_prompt
from utils import call_with_retry, logger
from world import MarsBaseState, make_agent_rng

MAX_BELIEFS = 8


@dataclass
class DiaryEntry:
    sol: int
    phase: str
    entry: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sol": self.sol,
            "phase": self.phase,
            "entry": self.entry,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiaryEntry:
        return cls(
            sol=data["sol"],
            phase=data["phase"],
            entry=data["entry"],
            timestamp=data["timestamp"],
        )


@dataclass
class StepResult:
    agent_name: str
    action: AgentAction | None
    world_deltas: list[str]
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    skipped: bool = False
    reasoning: str | None = None


INITIAL_BELIEFS: dict[str, dict[str, str]] = {
    "Commander": {
        "mission_priority": "Complete 90-sol survival milestone with all crew alive.",
        "crew_assessment": "Team is capable but untested under sustained stress.",
        "resource_strategy": "Conservative reserves until dust season patterns are known.",
    },
    "Engineer": {
        "system_health": "Primary systems nominal but redundancy margins are thin.",
        "maintenance_plan": "Preventive repairs on power and habitat every 3 sols.",
        "dust_risk": "Solar array efficiency will drop sharply during storms.",
    },
    "Scientist": {
        "research_goal": "Confirm organic signatures in subsurface samples from Sector C.",
        "sample_priority": "Prioritize sediment layers over surface regolith.",
        "greenhouse_theory": "Raising efficiency above 65% could extend food autonomy.",
    },
    "Medic": {
        "crew_health": "No acute medical issues; chronic stress and isolation are emerging risks.",
        "life_support_floor": "Oxygen must stay above 30%; water above 100L is minimum safe.",
        "nutrition_concern": "Caloric reserves adequate but micronutrient diversity is limited.",
    },
}

TURN_ORDER: list[str] = ["Commander", "Engineer", "Scientist", "Medic"]


@dataclass
class Agent:
    name: str
    role: str
    short_term_memory: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    diary: list[DiaryEntry] = field(default_factory=list)
    private_beliefs: dict[str, str] = field(default_factory=dict)
    last_action_summary: str | None = None
    last_action_type: str | None = None
    last_reasoning: str | None = None

    def __post_init__(self) -> None:
        if not self.private_beliefs:
            self.private_beliefs = dict(INITIAL_BELIEFS.get(self.name, {}))

    def _remember(self, observation: str) -> None:
        self.short_term_memory.append(observation)

    def _cap_beliefs(self) -> None:
        """Keep at most MAX_BELIEFS keys; preserve last_reflection if present."""
        if len(self.private_beliefs) <= MAX_BELIEFS:
            return
        reflection = self.private_beliefs.pop("last_reflection", None)
        while len(self.private_beliefs) > MAX_BELIEFS - (1 if reflection else 0):
            oldest = next(iter(self.private_beliefs))
            del self.private_beliefs[oldest]
        if reflection is not None:
            self.private_beliefs["last_reflection"] = reflection

    def _update_beliefs(self, action: AgentAction) -> None:
        """Update private beliefs from optional belief_update in parameters."""
        params = action.parameters
        belief_update = params.get("belief_update")
        if isinstance(belief_update, dict):
            for key, value in belief_update.items():
                if key != "key" and key != "value":
                    self.private_beliefs[str(key)] = str(value)[:300]

        self.private_beliefs["last_reflection"] = action.reasoning[:300]
        self._cap_beliefs()

    def _handle_diary(self, world: MarsBaseState, action: AgentAction) -> None:
        if action.action_type == ActionType.WRITE_DIARY:
            entry = DiaryEntry(
                sol=world.sol_number,
                phase=world.current_phase.value,
                entry=action.parameters.get("entry", "")[:1000],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self.diary.append(entry)

    def step(
        self,
        world: MarsBaseState,
        client: OpenAI,
        model: str,
    ) -> StepResult:
        """Execute one agent turn: LLM call, parse, apply action."""
        rng = make_agent_rng(world, self.name)
        system_prompt = get_system_prompt(self.role)
        user_prompt = build_user_prompt(self, world)

        llm_result = call_with_retry(client, model, system_prompt, user_prompt)

        if llm_result.error or llm_result.parsed_action is None:
            error_msg = llm_result.error or "system error: invalid JSON"
            logger.warning("%s skipped turn: %s", self.name, error_msg)
            self._remember(
                f"Sol {world.sol_number} {world.current_phase.value}: system error, turn skipped."
            )
            return StepResult(
                agent_name=self.name,
                action=None,
                world_deltas=[],
                error=error_msg,
                prompt_tokens=llm_result.prompt_tokens,
                completion_tokens=llm_result.completion_tokens,
                skipped=True,
            )

        action = llm_result.parsed_action
        deltas = world.apply_action(action, self.name, rng)
        self._handle_diary(world, action)
        self._update_beliefs(action)

        self.last_action_type = action.action_type.value
        self.last_reasoning = action.reasoning

        summary = (
            f"{action.action_type.value} (conf={action.confidence:.2f}): "
            f"{'; '.join(deltas) if deltas else 'no measurable effect'}"
        )
        self.last_action_summary = summary
        self._remember(
            f"Sol {world.sol_number} {world.current_phase.value}: {summary}"
        )

        logger.info(
            "%s -> %s | %s",
            self.name,
            action.action_type.value,
            action.reasoning[:120],
        )

        return StepResult(
            agent_name=self.name,
            action=action,
            world_deltas=deltas,
            prompt_tokens=llm_result.prompt_tokens,
            completion_tokens=llm_result.completion_tokens,
            reasoning=action.reasoning,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "short_term_memory": list(self.short_term_memory),
            "diary": [d.to_dict() for d in self.diary],
            "private_beliefs": self.private_beliefs,
            "last_action_summary": self.last_action_summary,
            "last_action_type": self.last_action_type,
            "last_reasoning": self.last_reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        agent = cls(
            name=data["name"],
            role=data["role"],
            private_beliefs=data.get("private_beliefs", {}),
            last_action_summary=data.get("last_action_summary"),
            last_action_type=data.get("last_action_type"),
            last_reasoning=data.get("last_reasoning"),
        )
        agent.short_term_memory = deque(
            data.get("short_term_memory", []), maxlen=5
        )
        agent.diary = [DiaryEntry.from_dict(d) for d in data.get("diary", [])]
        return agent


def create_agents() -> list[Agent]:
    """Instantiate the four crew agents in turn order."""
    return [
        Agent(name="Commander", role="Commander"),
        Agent(name="Engineer", role="Engineer"),
        Agent(name="Scientist", role="Scientist"),
        Agent(name="Medic", role="Medic"),
    ]
