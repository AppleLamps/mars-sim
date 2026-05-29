"""Pydantic models for agent actions and strict JSON validation."""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ActionType(str, Enum):
    REPAIR_SYSTEM = "repair_system"
    ALLOCATE_POWER = "allocate_power"
    SEND_MESSAGE = "send_message"
    PROPOSE_VOTE = "propose_vote"
    CAST_VOTE = "cast_vote"
    CONDUCT_EVA = "conduct_eva"
    ANALYZE_SAMPLE = "analyze_sample"
    ADJUST_GREENHOUSE = "adjust_greenhouse"
    WRITE_DIARY = "write_diary"
    REQUEST_RESOURCE = "request_resource"
    OBSERVE_ENVIRONMENT = "observe_environment"


ACTION_TYPES: list[str] = [a.value for a in ActionType]

AGENT_NAMES: list[str] = ["Commander", "Engineer", "Scientist", "Medic"]

Recipient = Literal["Commander", "Engineer", "Scientist", "Medic", "all"]
RepairSystem = Literal["habitat", "life_support", "power", "rover"]
RepairEffort = Literal["low", "medium", "high"]
PowerSubsystem = Literal["life_support", "greenhouse", "comms", "rover"]
ResourceType = Literal["oxygen", "water", "food", "fuel"]
ObserveFocus = Literal["atmosphere", "geology", "sensors", "crew"]


def count_sentences(text: str) -> int:
    """Count sentences via terminal punctuation."""
    parts = re.split(r"[.!?]+", text.strip())
    return len([p for p in parts if p.strip()])


class RepairSystemParams(BaseModel):
    system: RepairSystem
    effort: RepairEffort


class AllocatePowerParams(BaseModel):
    subsystem: PowerSubsystem
    percent: int = Field(ge=0, le=100)


class SendMessageParams(BaseModel):
    to: Recipient
    content: str = Field(max_length=500)


class ProposeVoteParams(BaseModel):
    topic: str = Field(min_length=3)
    options: list[str] = Field(min_length=2, max_length=4)

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, v: str) -> str:
        topic = v.strip()
        if len(topic) < 3:
            raise ValueError("topic must be at least 3 characters")
        return topic

    @field_validator("options")
    @classmethod
    def options_unique_and_nonblank(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for option in v:
            normalized = option.strip()
            if not normalized:
                raise ValueError("options must not be blank")
            key = normalized.casefold()
            if key in seen:
                raise ValueError("options must be unique")
            seen.add(key)
            cleaned.append(normalized)
        return cleaned


class CastVoteParams(BaseModel):
    topic: str = Field(min_length=3)
    choice: str = Field(min_length=1)

    @field_validator("topic")
    @classmethod
    def topic_trimmed(cls, v: str) -> str:
        topic = v.strip()
        if len(topic) < 3:
            raise ValueError("topic must be at least 3 characters")
        return topic

    @field_validator("choice")
    @classmethod
    def choice_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("choice must not be blank")
        return v.strip()


class ConductEvaParams(BaseModel):
    site: str = Field(min_length=1)
    duration_hours: int = Field(ge=1, le=8)


class AnalyzeSampleParams(BaseModel):
    sample_id: str = Field(min_length=1)
    method: str = Field(min_length=1)


class AdjustGreenhouseParams(BaseModel):
    target_efficiency: int = Field(ge=0, le=100)


class WriteDiaryParams(BaseModel):
    entry: str = Field(max_length=1000)
    belief_update: dict[str, str] | None = None


class RequestResourceParams(BaseModel):
    resource: ResourceType
    amount: float = Field(gt=0)


class ObserveEnvironmentParams(BaseModel):
    focus: ObserveFocus
    belief_update: dict[str, str] | None = None


class AgentAction(BaseModel):
    """Top-level action schema returned by each agent LLM call."""

    action_type: ActionType
    parameters: dict[str, Any]
    reasoning: str = Field(min_length=20)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning_sentences(cls, v: str) -> str:
        n = count_sentences(v)
        if n < 2:
            raise ValueError("reasoning must contain at least 2 sentences")
        if n > 5:
            raise ValueError("reasoning must contain at most 5 sentences")
        return v

    @model_validator(mode="after")
    def validate_parameters(self) -> AgentAction:
        """Validate parameters against the action-specific sub-schema."""
        validators: dict[ActionType, type[BaseModel]] = {
            ActionType.REPAIR_SYSTEM: RepairSystemParams,
            ActionType.ALLOCATE_POWER: AllocatePowerParams,
            ActionType.SEND_MESSAGE: SendMessageParams,
            ActionType.PROPOSE_VOTE: ProposeVoteParams,
            ActionType.CAST_VOTE: CastVoteParams,
            ActionType.CONDUCT_EVA: ConductEvaParams,
            ActionType.ANALYZE_SAMPLE: AnalyzeSampleParams,
            ActionType.ADJUST_GREENHOUSE: AdjustGreenhouseParams,
            ActionType.WRITE_DIARY: WriteDiaryParams,
            ActionType.REQUEST_RESOURCE: RequestResourceParams,
            ActionType.OBSERVE_ENVIRONMENT: ObserveEnvironmentParams,
        }
        model_cls = validators[self.action_type]
        validated = model_cls.model_validate(self.parameters)
        self.parameters = validated.model_dump(exclude_none=True)
        return self


def get_action_schema_json() -> str:
    """Return a compact JSON schema string (kept for tests/debug)."""
    schema = AgentAction.model_json_schema()
    return json.dumps(schema, indent=2)


def get_action_descriptions() -> str:
    """Human-readable action catalog for prompts."""
    return """
- repair_system: {system: habitat|life_support|power|rover, effort: low|medium|high}
- allocate_power: {subsystem: life_support|greenhouse|comms|rover, percent: 0-100}
- send_message: {to: Commander|Engineer|Scientist|Medic|all, content: string}
- propose_vote: {topic: string, options: [2-4 strings]}
- cast_vote: {topic: string, choice: string matching a pending vote option}
- conduct_eva: {site: string, duration_hours: 1-8}
- analyze_sample: {sample_id: string, method: string}
- adjust_greenhouse: {target_efficiency: 0-100}
- write_diary: {entry: string, belief_update?: {key: value}}
- request_resource: {resource: oxygen|water|food|fuel, amount: positive number}
- observe_environment: {focus: atmosphere|geology|sensors|crew, belief_update?: {key: value}}
""".strip()
