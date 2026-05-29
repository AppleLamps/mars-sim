"""Mars base world state, passive events, and action application."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from random import Random
from typing import Any

from actions import AGENT_NAMES, ActionType, AgentAction
from config import SimConfig

DEFAULT_STOCKPILE: dict[str, float] = {
    "oxygen": 20.0,
    "water": 100.0,
    "food": 10.0,
    "fuel": 30.0,
}

VOTE_EXPIRY_SOLS = 3


class Phase(str, Enum):
    MORNING = "morning"
    MIDDAY = "midday"
    EVENING = "evening"

    @classmethod
    def cycle(cls) -> list[Phase]:
        return [cls.MORNING, cls.MIDDAY, cls.EVENING]

    def next_phase(self) -> Phase:
        order = self.cycle()
        idx = order.index(self)
        return order[(idx + 1) % len(order)]


@dataclass
class PublicMessage:
    sol: int
    phase: str
    from_agent: str
    to: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sol": self.sol,
            "phase": self.phase,
            "from_agent": self.from_agent,
            "to": self.to,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublicMessage:
        return cls(
            sol=data["sol"],
            phase=data["phase"],
            from_agent=data["from_agent"],
            to=data["to"],
            content=data["content"],
        )


@dataclass
class PendingVote:
    topic: str
    options: list[str]
    proposer: str
    sol: int
    phase: str
    votes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "options": self.options,
            "proposer": self.proposer,
            "sol": self.sol,
            "phase": self.phase,
            "votes": self.votes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingVote:
        return cls(
            topic=data["topic"],
            options=data["options"],
            proposer=data["proposer"],
            sol=data["sol"],
            phase=data["phase"],
            votes=data.get("votes", {}),
        )


@dataclass
class DustStorm:
    power_multiplier: float
    phases_remaining: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "power_multiplier": self.power_multiplier,
            "phases_remaining": self.phases_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DustStorm:
        return cls(
            power_multiplier=data["power_multiplier"],
            phases_remaining=data["phases_remaining"],
        )


@dataclass
class MarsBaseState:
    """Persistent simulation state for the Mars habitat."""

    power_level: float = 85.0
    oxygen: float = 92.0
    water: float = 400.0
    food_days: float = 45.0
    habitat_integrity: float = 88.0
    rover_fuel: float = 70.0
    greenhouse_efficiency: float = 55.0
    sol_number: int = 1
    current_phase: Phase = Phase.MORNING
    phase_index: int = 0
    recent_events: list[str] = field(default_factory=list)
    agent_relationships: dict[str, dict[str, float]] = field(default_factory=dict)
    public_messages: list[PublicMessage] = field(default_factory=list)
    pending_votes: list[PendingVote] = field(default_factory=list)
    active_dust_storm: DustStorm | None = None
    power_allocations: dict[str, float] = field(
        default_factory=lambda: {
            "life_support": 40.0,
            "greenhouse": 25.0,
            "comms": 15.0,
            "rover": 20.0,
        }
    )
    collected_samples: list[str] = field(default_factory=list)
    resource_stockpile: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_STOCKPILE))
    last_vote_proposal_phase: dict[str, int] = field(default_factory=dict)
    seed: int = 42
    config: SimConfig = field(default_factory=SimConfig)

    def _clamp(self) -> None:
        """Keep all metrics within valid bounds."""
        self.power_level = max(0.0, min(100.0, self.power_level))
        self.oxygen = max(0.0, min(100.0, self.oxygen))
        self.water = max(0.0, self.water)
        self.food_days = max(0.0, self.food_days)
        self.habitat_integrity = max(0.0, min(100.0, self.habitat_integrity))
        self.rover_fuel = max(0.0, min(100.0, self.rover_fuel))
        self.greenhouse_efficiency = max(0.0, min(100.0, self.greenhouse_efficiency))
        for agent in self.agent_relationships:
            for other in self.agent_relationships[agent]:
                self.agent_relationships[agent][other] = max(
                    -1.0, min(1.0, self.agent_relationships[agent][other])
                )
        for key in self.resource_stockpile:
            self.resource_stockpile[key] = max(0.0, self.resource_stockpile[key])

    def _append_event(self, message: str) -> None:
        self.recent_events.append(message)
        if len(self.recent_events) > 20:
            self.recent_events = self.recent_events[-20:]

    def _append_message(self, msg: PublicMessage) -> None:
        self.public_messages.append(msg)
        if len(self.public_messages) > 12:
            self.public_messages = self.public_messages[-12:]

    def _adjust_relationship(self, from_agent: str, to_agent: str, delta: float) -> None:
        if from_agent not in self.agent_relationships:
            return
        if to_agent not in self.agent_relationships[from_agent]:
            return
        self.agent_relationships[from_agent][to_agent] += delta
        if to_agent in self.agent_relationships and from_agent in self.agent_relationships[to_agent]:
            self.agent_relationships[to_agent][from_agent] += delta * 0.5

    def _normalize_power_allocations(self) -> None:
        """Ensure power allocation percentages sum to 100."""
        total = sum(self.power_allocations.values())
        if total <= 0:
            n = len(self.power_allocations)
            for key in self.power_allocations:
                self.power_allocations[key] = 100.0 / n
            return
        if abs(total - 100.0) > 0.01:
            factor = 100.0 / total
            for key in self.power_allocations:
                self.power_allocations[key] *= factor

    def effective_power(self) -> float:
        """Power level after dust storm penalty."""
        if self.active_dust_storm:
            return self.power_level * self.active_dust_storm.power_multiplier
        return self.power_level

    def _format_pending_votes(self) -> str:
        if not self.pending_votes:
            return "none"
        lines: list[str] = []
        for vote in self.pending_votes:
            votes_str = vote.votes if vote.votes else "{}"
            lines.append(
                f'"{vote.topic}" options={vote.options} votes={votes_str} (proposed sol {vote.sol})'
            )
        return "; ".join(lines)

    def get_blocked_actions(self, agent_name: str) -> set[str]:
        """Return action types unavailable to this agent in the current state."""
        blocked: set[str] = set()

        if self.rover_fuel < self.config.rover_fuel_eva_floor:
            blocked.add(ActionType.CONDUCT_EVA.value)
        if (
            self.active_dust_storm
            and self.active_dust_storm.power_multiplier
            < self.config.dust_eva_severity_floor
        ):
            blocked.add(ActionType.CONDUCT_EVA.value)

        if not self.collected_samples:
            blocked.add(ActionType.ANALYZE_SAMPLE.value)

        if not self.pending_votes:
            blocked.add(ActionType.CAST_VOTE.value)
        else:
            # Block if agent already voted on all pending topics
            all_voted = all(
                agent_name in vote.votes for vote in self.pending_votes
            )
            if all_voted:
                blocked.add(ActionType.CAST_VOTE.value)

        return blocked

    def is_action_blocked(self, action_type: ActionType, agent_name: str) -> str | None:
        """Return block reason if action cannot be performed."""
        if action_type.value in self.get_blocked_actions(agent_name):
            if action_type == ActionType.CONDUCT_EVA:
                if self.rover_fuel < self.config.rover_fuel_eva_floor:
                    return "EVA aborted: insufficient rover fuel"
                return "EVA aborted: dust storm too severe"
            if action_type == ActionType.ANALYZE_SAMPLE:
                return "No samples in inventory"
            if action_type == ActionType.CAST_VOTE:
                return "No pending votes to cast"
        return None

    def summary(self) -> str:
        """Compact text summary for LLM prompts."""
        storm = "active" if self.active_dust_storm else "none"
        storm_detail = ""
        if self.active_dust_storm:
            storm_detail = (
                f" (power x{self.active_dust_storm.power_multiplier:.2f}, "
                f"{self.active_dust_storm.phases_remaining} phases left)"
            )
        return (
            f"Sol {self.sol_number}, phase {self.current_phase.value}\n"
            f"- Power: {self.power_level:.1f}% (effective {self.effective_power():.1f}%)"
            f", dust storm: {storm}{storm_detail}\n"
            f"- Oxygen: {self.oxygen:.1f}%, Water: {self.water:.0f}L, "
            f"Food: {self.food_days:.1f} days\n"
            f"- Habitat integrity: {self.habitat_integrity:.1f}%, "
            f"Rover fuel: {self.rover_fuel:.1f}%, "
            f"Greenhouse: {self.greenhouse_efficiency:.1f}%\n"
            f"- Power allocations: {self.power_allocations}\n"
            f"- Resource stockpile: {self.resource_stockpile}\n"
            f"- Pending votes: {self._format_pending_votes()}\n"
            f"- Samples collected: {self.collected_samples or ['none']}\n"
            f"- Recent events: {self.recent_events[-5:]}"
        )

    def _power_coupling_factor(self) -> float:
        """Efficiency multiplier for power-dependent systems.

        Returns 1.0 when effective power >= threshold. Below threshold, ramps
        linearly down to coupling_min_factor at zero effective power. This makes
        low power degrade greenhouse output and ISRU recovery, forcing real
        allocation trade-offs.
        """
        eff = self.effective_power()
        threshold = self.config.coupling_power_threshold
        if eff >= threshold:
            return 1.0
        min_factor = self.config.coupling_min_factor
        ratio = eff / threshold if threshold > 0 else 1.0
        return min_factor + (1.0 - min_factor) * ratio

    def is_quiet_phase(self, agent_name: str) -> bool:
        """True if the world is calm enough that an agent turn can be safely skipped.

        Reads state only — no RNG, no mutation. Used by the opt-in fast-path.
        """
        c = self.config
        if self.active_dust_storm is not None:
            return False
        if self.effective_power() < c.fast_path_power_floor:
            return False
        if self.oxygen < c.fast_path_o2_floor:
            return False
        if self.water < c.fast_path_water_floor:
            return False
        if self.food_days < c.fast_path_food_floor:
            return False
        if self.habitat_integrity < c.fast_path_integrity_floor:
            return False
        # Any pending vote this agent has NOT yet cast on => not quiet (they should vote).
        for vote in self.pending_votes:
            if agent_name not in vote.votes:
                return False
        return True

    def apply_phase_events(self, rng: Random) -> list[str]:
        """Apply passive world events at the start of each phase."""
        deltas: list[str] = []

        if self.active_dust_storm is None and rng.random() < self.config.dust_storm_chance:
            multiplier = rng.uniform(
                self.config.dust_multiplier_min, self.config.dust_multiplier_max
            )
            duration = rng.randint(
                self.config.dust_duration_min, self.config.dust_duration_max
            )
            self.active_dust_storm = DustStorm(
                power_multiplier=multiplier,
                phases_remaining=duration,
            )
            msg = (
                f"DUST STORM: solar panels at {multiplier:.0%} efficiency "
                f"for {duration} phase(s)"
            )
            self._append_event(msg)
            deltas.append(msg)
        elif self.active_dust_storm is not None:
            self.active_dust_storm.phases_remaining -= 1
            if self.active_dust_storm.phases_remaining <= 0:
                self._append_event("Dust storm clearing; solar efficiency recovering.")
                deltas.append("Dust storm ended.")
                self.active_dust_storm = None

        power_factor = self.effective_power() / 100.0
        o2_loss = rng.uniform(self.config.o2_loss_min, self.config.o2_loss_max) * (
            2.0 - power_factor
        )
        water_loss = rng.uniform(self.config.water_loss_min, self.config.water_loss_max)
        food_loss = rng.uniform(self.config.food_loss_min, self.config.food_loss_max)
        integrity_loss = rng.uniform(
            self.config.integrity_loss_min, self.config.integrity_loss_max
        )

        self.oxygen -= o2_loss
        self.water -= water_loss
        self.food_days -= food_loss
        self.habitat_integrity -= integrity_loss

        # ISRU recovery (power-dependent)
        ls_alloc = self.power_allocations.get("life_support", 0)
        factor = self._power_coupling_factor()
        if (
            self.effective_power() > self.config.isru_power_floor
            and ls_alloc > self.config.isru_ls_alloc_floor
        ):
            isru_o2 = self.config.isru_o2_base * power_factor * factor
            isru_water = self.config.isru_water_base * power_factor * factor
            self.oxygen += isru_o2
            self.water += isru_water
            deltas.append(f"ISRU recovery: O2 +{isru_o2:.2f}%, water +{isru_water:.1f}L")

        # Greenhouse food production
        gh_alloc = self.power_allocations.get("greenhouse", 0) / 100.0
        food_gain = (
            (self.greenhouse_efficiency / 100.0)
            * gh_alloc
            * self.config.greenhouse_food_factor
            * factor
        )
        self.food_days += food_gain

        deltas.append(
            f"Passive drain: O2 -{o2_loss:.2f}%, water -{water_loss:.1f}L, "
            f"food -{food_loss:.2f}d (+{food_gain:.3f} greenhouse), "
            f"integrity -{integrity_loss:.2f}%"
        )

        if rng.random() < self.config.anomaly_chance:
            anomalies = [
                "Anomalous methane spike detected near Sector C.",
                "Seismic sensor registered micro-tremor under habitat pad.",
                "Radiation monitor flagged brief spike during midday.",
                "Atmospheric pressure reading drifted 0.3% from baseline.",
                "Comms array picked up unexplained narrowband signal.",
            ]
            msg = rng.choice(anomalies)
            self._append_event(msg)
            deltas.append(msg)

        self._clamp()
        return deltas

    def advance_phase(self) -> None:
        """Move to the next phase; roll sol on evening -> morning."""
        next_phase = self.current_phase.next_phase()
        if self.current_phase == Phase.EVENING and next_phase == Phase.MORNING:
            self.sol_number += 1
        self.current_phase = next_phase
        self.phase_index += 1
        self._resolve_votes()

    def _resolve_votes(self) -> None:
        """Resolve votes with >=2 cast; expire votes older than VOTE_EXPIRY_SOLS."""
        remaining: list[PendingVote] = []
        for vote in self.pending_votes:
            age = self.sol_number - vote.sol
            if age > VOTE_EXPIRY_SOLS:
                self._append_event(f"Vote expired without resolution: '{vote.topic}'")
                continue
            if len(vote.votes) >= 2:
                counts: dict[str, int] = {opt: 0 for opt in vote.options}
                for choice in vote.votes.values():
                    if choice in counts:
                        counts[choice] += 1
                winner = max(counts, key=counts.get)
                msg = f"Vote resolved: '{vote.topic}' -> {winner} ({counts})"
                self._append_event(msg)
            else:
                remaining.append(vote)
        self.pending_votes = remaining

    def apply_action(self, action: AgentAction, agent_name: str, rng: Random) -> list[str]:
        """Apply a validated agent action and return human-readable deltas."""
        block_reason = self.is_action_blocked(action.action_type, agent_name)
        if block_reason:
            return [block_reason]

        params = action.parameters
        deltas: list[str] = []

        if action.action_type == ActionType.REPAIR_SYSTEM:
            system = params["system"]
            effort = params["effort"]
            effort_map = {
                "low": self.config.repair_boost_low,
                "medium": self.config.repair_boost_medium,
                "high": self.config.repair_boost_high,
            }
            boost = effort_map[effort]
            power_cost = {
                "low": self.config.repair_power_low,
                "medium": self.config.repair_power_medium,
                "high": self.config.repair_power_high,
            }[effort]
            self.power_level -= power_cost

            if system == "habitat":
                self.habitat_integrity += boost * 2
                deltas.append(f"Habitat integrity +{boost * 2:.1f}%")
            elif system == "life_support":
                self.oxygen += boost * 1.5
                self.water += boost * 3
                deltas.append(
                    f"Life support boosted: O2 +{boost * 1.5:.1f}%, water +{boost * 3:.0f}L"
                )
            elif system == "power":
                self.power_level += boost * 3
                deltas.append(f"Power systems +{boost * 3:.1f}%")
            elif system == "rover":
                self.rover_fuel += boost * 2
                deltas.append(f"Rover systems +{boost * 2:.1f}% fuel efficiency")

        elif action.action_type == ActionType.ALLOCATE_POWER:
            subsystem = params["subsystem"]
            percent = float(params["percent"])
            old = self.power_allocations[subsystem]
            delta = percent - old
            self.power_allocations[subsystem] = percent

            others = [k for k in self.power_allocations if k != subsystem]
            other_total = sum(self.power_allocations[k] for k in others)
            if other_total > 0 and delta != 0:
                for key in others:
                    share = self.power_allocations[key] / other_total
                    self.power_allocations[key] = max(0.0, self.power_allocations[key] - delta * share)
            self._normalize_power_allocations()
            deltas.append(
                f"Power allocation {subsystem}: {old:.0f}% -> "
                f"{self.power_allocations[subsystem]:.0f}% (rebalanced)"
            )
            if subsystem == "life_support":
                self.oxygen += (percent - old) * 0.05
            elif subsystem == "greenhouse":
                self.greenhouse_efficiency += (percent - old) * 0.1
            elif subsystem == "rover":
                self.rover_fuel += (percent - old) * 0.03

        elif action.action_type == ActionType.SEND_MESSAGE:
            to = params["to"]
            content = params["content"][:500]
            msg = PublicMessage(
                sol=self.sol_number,
                phase=self.current_phase.value,
                from_agent=agent_name,
                to=to,
                content=content,
            )
            self._append_message(msg)
            deltas.append(f"Message sent to {to}: {content[:80]}...")
            if to in AGENT_NAMES:
                self._adjust_relationship(agent_name, to, 0.05)
            elif to == "all":
                for other in AGENT_NAMES:
                    if other != agent_name:
                        self._adjust_relationship(agent_name, other, 0.02)

        elif action.action_type == ActionType.PROPOSE_VOTE:
            topic = params["topic"]
            last_phase = self.last_vote_proposal_phase.get(agent_name)
            cooldown = self.config.vote_proposal_cooldown_phases
            if last_phase is not None and (self.phase_index - last_phase) < cooldown:
                phases_left = cooldown - (self.phase_index - last_phase)
                return [
                    f"Vote proposal on cooldown for {agent_name} "
                    f"({phases_left} more phase(s))"
                ]
            topic_key = topic.casefold()
            if any(vote.topic.casefold() == topic_key for vote in self.pending_votes):
                return [f"Vote already pending for topic '{topic}'"]
            vote = PendingVote(
                topic=topic,
                options=params["options"],
                proposer=agent_name,
                sol=self.sol_number,
                phase=self.current_phase.value,
            )
            self.pending_votes.append(vote)
            deltas.append(f"Vote proposed: {topic} options={params['options']}")
            self.last_vote_proposal_phase[agent_name] = self.phase_index

        elif action.action_type == ActionType.CAST_VOTE:
            topic = params["topic"]
            choice = params["choice"]
            matched = False
            for vote in self.pending_votes:
                if vote.topic.casefold() == topic.casefold():
                    if choice not in vote.options:
                        return [f"Invalid choice '{choice}' for vote '{topic}'"]
                    if agent_name in vote.votes:
                        return [f"{agent_name} already voted on '{topic}'"]
                    vote.votes[agent_name] = choice
                    deltas.append(f"{agent_name} voted '{choice}' on '{topic}'")
                    matched = True
                    break
            if not matched:
                return [f"No pending vote found for topic '{topic}'"]

        elif action.action_type == ActionType.CONDUCT_EVA:
            site = params["site"]
            hours = int(params["duration_hours"])
            fuel_cost = hours * self.config.eva_fuel_per_hour
            if self.rover_fuel < fuel_cost:
                return [f"EVA aborted: insufficient rover fuel for {hours}h EVA"]
            self.rover_fuel -= fuel_cost
            self.power_level -= hours * self.config.eva_power_per_hour
            deltas.append(f"EVA at {site} for {hours}h (-{fuel_cost:.1f}% rover fuel)")

            if rng.random() < self.config.eva_sample_chance:
                sample_id = f"S{self.sol_number}-{rng.randint(100, 999)}"
                self.collected_samples.append(sample_id)
                self._append_event(f"EVA recovered sample {sample_id} from {site}.")
                deltas.append(f"Sample collected: {sample_id}")
            if rng.random() < self.config.eva_incident_chance:
                damage = rng.uniform(1.0, 4.0)
                self.habitat_integrity -= damage
                deltas.append(f"EVA incident: habitat integrity -{damage:.1f}%")

        elif action.action_type == ActionType.ANALYZE_SAMPLE:
            sample_id = params["sample_id"]
            method = params["method"]
            if sample_id not in self.collected_samples:
                return [f"Sample {sample_id} not in inventory"]
            self.power_level -= 3.0
            self._append_event(
                f"{agent_name} analyzed {sample_id} via {method}: trace organics detected."
            )
            deltas.append(f"Analysis of {sample_id} complete ({method})")

        elif action.action_type == ActionType.ADJUST_GREENHOUSE:
            target = float(params["target_efficiency"])
            delta = (target - self.greenhouse_efficiency) * 0.3
            self.greenhouse_efficiency += delta
            self.power_level -= abs(delta) * 0.2
            self.food_days += delta * 0.05
            deltas.append(
                f"Greenhouse adjusted toward {target:.0f}% "
                f"(now {self.greenhouse_efficiency:.1f}%)"
            )

        elif action.action_type == ActionType.WRITE_DIARY:
            deltas.append(f"Diary entry recorded ({len(params['entry'])} chars)")

        elif action.action_type == ActionType.REQUEST_RESOURCE:
            resource = params["resource"]
            amount = float(params["amount"])
            stock = self.resource_stockpile.get(resource, 0.0)
            granted = min(amount, stock)
            if granted <= 0:
                self._append_event(
                    f"Resource request DENIED: {agent_name} requested {amount} {resource}, "
                    f"stockpile empty."
                )
                return [f"Request denied: no {resource} in stockpile"]
            self.resource_stockpile[resource] = stock - granted
            if resource == "oxygen":
                self.oxygen += granted * 0.1
                deltas.append(f"Oxygen request granted: +{granted * 0.1:.1f}%")
            elif resource == "water":
                self.water += granted
                deltas.append(f"Water request granted: +{granted:.0f}L")
            elif resource == "food":
                self.food_days += granted * 0.1
                deltas.append(f"Food request granted: +{granted * 0.1:.1f} days")
            elif resource == "fuel":
                self.rover_fuel += granted * 0.1
                deltas.append(f"Fuel request granted: +{granted * 0.1:.1f}%")
            self._adjust_relationship(agent_name, "Commander", 0.03)

        elif action.action_type == ActionType.OBSERVE_ENVIRONMENT:
            focus = params["focus"]
            observations = {
                "atmosphere": "Thin CO2 atmosphere; dust opacity elevated near horizon.",
                "geology": "Basaltic regolith with layered sediment deposits visible.",
                "sensors": "All primary sensors nominal; minor calibration drift on pressure.",
                "crew": "Crew fatigue moderate; morale stable but resource anxiety rising.",
            }
            obs = observations[focus]
            self._append_event(f"{agent_name} observed {focus}: {obs}")
            deltas.append(f"Observation ({focus}): {obs}")

        self._clamp()
        return deltas

    def to_dict(self) -> dict[str, Any]:
        return {
            "power_level": self.power_level,
            "oxygen": self.oxygen,
            "water": self.water,
            "food_days": self.food_days,
            "habitat_integrity": self.habitat_integrity,
            "rover_fuel": self.rover_fuel,
            "greenhouse_efficiency": self.greenhouse_efficiency,
            "sol_number": self.sol_number,
            "current_phase": self.current_phase.value,
            "phase_index": self.phase_index,
            "recent_events": self.recent_events,
            "agent_relationships": self.agent_relationships,
            "public_messages": [m.to_dict() for m in self.public_messages],
            "pending_votes": [v.to_dict() for v in self.pending_votes],
            "active_dust_storm": (
                self.active_dust_storm.to_dict() if self.active_dust_storm else None
            ),
            "power_allocations": self.power_allocations,
            "collected_samples": self.collected_samples,
            "resource_stockpile": self.resource_stockpile,
            "last_vote_proposal_phase": self.last_vote_proposal_phase,
            "seed": self.seed,
            "config": asdict(self.config),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarsBaseState:
        storm_data = data.get("active_dust_storm")
        config_data = data.get("config")
        if config_data:
            valid_keys = {f.name for f in fields(SimConfig)}
            filtered = {k: v for k, v in config_data.items() if k in valid_keys}
            config = SimConfig(**filtered)
        else:
            config = SimConfig()
        return cls(
            power_level=data["power_level"],
            oxygen=data["oxygen"],
            water=data["water"],
            food_days=data["food_days"],
            habitat_integrity=data["habitat_integrity"],
            rover_fuel=data["rover_fuel"],
            greenhouse_efficiency=data["greenhouse_efficiency"],
            sol_number=data["sol_number"],
            current_phase=Phase(data["current_phase"]),
            phase_index=data["phase_index"],
            recent_events=data.get("recent_events", []),
            agent_relationships=data.get("agent_relationships", {}),
            public_messages=[
                PublicMessage.from_dict(m) for m in data.get("public_messages", [])
            ],
            pending_votes=[
                PendingVote.from_dict(v) for v in data.get("pending_votes", [])
            ],
            active_dust_storm=DustStorm.from_dict(storm_data) if storm_data else None,
            power_allocations=data.get(
                "power_allocations",
                {"life_support": 40, "greenhouse": 25, "comms": 15, "rover": 20},
            ),
            collected_samples=data.get("collected_samples", []),
            resource_stockpile=data.get("resource_stockpile", dict(DEFAULT_STOCKPILE)),
            last_vote_proposal_phase=data.get("last_vote_proposal_phase", {}),
            seed=data.get("seed", 42),
            config=config,
        )


def create_initial_state(seed: int = 42) -> MarsBaseState:
    """Create a fresh Mars base state with neutral relationships."""
    relationships: dict[str, dict[str, float]] = {}
    for agent in AGENT_NAMES:
        relationships[agent] = {other: 0.0 for other in AGENT_NAMES if other != agent}

    state = MarsBaseState(
        agent_relationships=relationships,
        seed=seed,
        resource_stockpile=dict(DEFAULT_STOCKPILE),
        recent_events=[
            "Sol 1: Mars Base Alpha online. All crew accounted for. Mission clock started."
        ],
    )
    state._clamp()
    return state


def make_phase_rng(state: MarsBaseState) -> Random:
    """Deterministic RNG for passive phase events."""
    return Random(state.seed + state.phase_index)


def make_agent_rng(state: MarsBaseState, agent_name: str) -> Random:
    """Deterministic per-agent RNG to avoid correlated rolls."""
    agent_idx = AGENT_NAMES.index(agent_name)
    return Random(state.seed + state.phase_index * 10 + agent_idx)
