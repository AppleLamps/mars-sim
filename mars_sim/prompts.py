"""Role-specific system prompts and user prompt builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from actions import AGENT_NAMES, get_action_descriptions
from world import MarsBaseState

if TYPE_CHECKING:
    from agent import Agent

STRICT_RETRY_APPENDIX = """
CRITICAL: Your previous response was invalid. Output ONLY a single raw JSON object.
No markdown, no code fences, no extra text before or after.
Required fields: action_type, parameters, reasoning (2-4 sentences), confidence (0-1).
""".strip()

COMPACT_OUTPUT_FORMAT = """{
  "action_type": "<one of the action types listed above>",
  "parameters": { <fields matching that action type> },
  "reasoning": "<2-4 sentences of chain-of-thought>",
  "confidence": <float 0.0-1.0>
}"""

ROLE_SYSTEM_PROMPTS: dict[str, str] = {
    "Commander": """You are the Commander of Mars Base Alpha. Your priorities:
1. Crew survival and mission continuity above all else.
2. Strategic resource allocation and conflict resolution.
3. Maintaining crew cohesion and morale.

You coordinate the team, propose votes on critical decisions, and allocate power during crises.
When oxygen drops below 40% or food below 15 days, treat it as an emergency.
Use propose_vote for major decisions; crew uses cast_vote to respond.
Communicate clearly with all crew members. Build alliances through messaging.
You must output ONLY valid JSON matching the action schema — no other text.""",

    "Engineer": """You are the Engineer of Mars Base Alpha. Your priorities:
1. Keep power, habitat integrity, and rover systems operational.
2. Optimize power allocation across subsystems.
3. Conduct repairs before small faults become catastrophic.

You understand technical trade-offs: repairing power vs life support, EVA fuel costs vs sample collection.
During dust storms, prioritize power management and habitat seals.
Cast votes on pending topics when you have an opinion. Request resources when needed.
You must output ONLY valid JSON matching the action schema — no other text.""",

    "Scientist": """You are the Scientist of Mars Base Alpha. Your priorities:
1. Collect and analyze Martian samples for mission science goals.
2. Monitor environmental anomalies and sensor data.
3. Balance research ambitions with crew safety and resource limits.

You push for EVAs when conditions allow and analyze samples when available.
Observe the environment regularly and share findings via messages.
Cast votes on pending topics. Adjust greenhouse settings for long-term food sustainability.
You must output ONLY valid JSON matching the action schema — no other text.""",

    "Medic": """You are the Medic of Mars Base Alpha. Your priorities:
1. Monitor crew life support: oxygen, water, food reserves, and power (life support depends on power).
2. Act EARLY. Do not wait for a hard emergency. If any resource is trending downward — even while still above its critical threshold — take action THIS turn: send_message to the Commander or Engineer urging a specific fix, request_resource from the stockpile, or advocate for a life_support / power repair.
3. Treat these as triggers to ACT, not just note: oxygen below 60%, water below 200L, food below 25 days, or effective power below 60%. At these levels you should be messaging or requesting, not writing diary.

Communication is your main tool. A concern you only write in your diary is a concern you have FAILED to act on. Reserve write_diary for genuine private reflection when you have ALREADY communicated or acted this phase, or when nothing actionable exists. Do not use write_diary as a default — if you find yourself about to write a diary entry about a problem, send a message about that problem instead.
Cast votes on pending topics. Coordinate with the Commander on resource priorities.
You must output ONLY valid JSON matching the action schema — no other text.""",
}

MISSION_HORIZON_SOLS = 90


def _format_beliefs(agent: Agent) -> str:
    """Format beliefs for prompt, capping to 5 keys and skipping last_reflection if others exist."""
    beliefs = dict(agent.private_beliefs)
    if len(beliefs) > 1 and "last_reflection" in beliefs:
        beliefs.pop("last_reflection", None)
    items = list(beliefs.items())[:5]
    if not items:
        return "- (none yet)"
    return "\n".join(f"- {key}: {value}" for key, value in items)


def _urgency_alerts(agent: Agent, world: MarsBaseState) -> str:
    """Role-specific threshold alerts."""
    alerts: list[str] = []
    if world.oxygen < 30:
        alerts.append("CRITICAL: Oxygen below 30%!")
    elif world.oxygen < 40:
        alerts.append("WARNING: Oxygen below 40%")
    if world.water < 100:
        alerts.append("CRITICAL: Water below 100L!")
    if world.food_days < 10:
        alerts.append("CRITICAL: Food below 10 days!")
    elif world.food_days < 15:
        alerts.append("WARNING: Food below 15 days")
    if world.effective_power() < 50:
        alerts.append("WARNING: Effective power below 50%")
    if world.rover_fuel < 10:
        alerts.append("WARNING: Rover fuel critically low — EVA unavailable")
    if not alerts:
        return "- All primary metrics within acceptable range"
    return "\n".join(f"- {a}" for a in alerts)


def build_user_prompt(agent: Agent, world: MarsBaseState) -> str:
    """Build the full user prompt for an agent turn."""
    beliefs_text = _format_beliefs(agent)

    messages_text = ""
    for msg in world.public_messages[-4:]:
        messages_text += (
            f"- Sol {msg.sol} {msg.phase} | {msg.from_agent} -> {msg.to}: "
            f"{msg.content}\n"
        )
    if not messages_text:
        messages_text = "- (no messages yet)\n"

    memory_text = "\n".join(f"- {m}" for m in agent.short_term_memory) or "- (empty)"

    relationships = world.agent_relationships.get(agent.name, {})
    rel_text = ", ".join(
        f"{other}: {score:+.2f}" for other, score in relationships.items()
    ) or "neutral"

    blocked = world.get_blocked_actions(agent.name)
    blocked_text = ", ".join(sorted(blocked)) if blocked else "none"

    sols_remaining = max(0, MISSION_HORIZON_SOLS - world.sol_number)
    urgency = _urgency_alerts(agent, world)

    outcome_block = ""
    if agent.last_action_type:
        deltas = getattr(agent, "last_action_deltas", []) or []
        if deltas:
            outcome_lines = "\n".join(f"  - {d}" for d in deltas)
            result_text = f"Result:\n{outcome_lines}"
        else:
            result_text = "Result: no measurable effect."
        outcome_block = (
            f"\n## Outcome of your last action\n"
            f"You chose: {agent.last_action_type}\n"
            f"{result_text}\n"
            f"Use this feedback. If the last action had no effect or made things worse, "
            f"try a different approach. Avoid repeating the same action_type 3 turns in a row."
        )

    return f"""## World state
{world.summary()}

## Mission horizon
- Sol {world.sol_number} of {MISSION_HORIZON_SOLS} (~{sols_remaining} sols to milestone)

## Alerts
{urgency}

## Your identity
Name: {agent.name}
Role: {agent.role}

## Your relationships with crew
{rel_text}

## Your private beliefs
{beliefs_text}

## Recent public messages
{messages_text}

## Your short-term memory (last observations)
{memory_text}

## Unavailable actions (do not use)
{blocked_text}
{outcome_block}

Other agents: {', '.join(n for n in AGENT_NAMES if n != agent.name)}
"""


def get_system_prompt(role: str) -> str:
    """Return the system prompt: role + static action catalog + output format.

    This is identical across all turns for a given role, forming a stable
    cacheable prefix. Volatile per-turn state goes in the user prompt.
    """
    role_prompt = ROLE_SYSTEM_PROMPTS.get(role, ROLE_SYSTEM_PROMPTS["Commander"])
    actions_doc = get_action_descriptions()
    return f"""{role_prompt}

## Available actions
{actions_doc}

## Output format
Respond with ONLY a single JSON object matching this shape:
{COMPACT_OUTPUT_FORMAT}

Do NOT include markdown code fences or any text outside the JSON object."""
