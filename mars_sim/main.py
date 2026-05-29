"""CLI entrypoint for the Mars base multi-agent simulation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from agent import Agent, TURN_ORDER, create_agents
from utils import (
    cost_tracker,
    create_openrouter_client,
    log_jsonl,
    reset_cost_tracker,
    setup_logging,
)
from world import MarsBaseState, create_initial_state, make_phase_rng


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mars Base Alpha — multi-agent simulation via OpenRouter"
    )
    parser.add_argument(
        "--sols",
        type=int,
        default=10,
        help="Number of sols (Martian days) to simulate (default: 10)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="anthropic/claude-sonnet-4",
        help="OpenRouter model id (default: anthropic/claude-sonnet-4)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=str,
        default="checkpoints",
        help="Directory for auto-save checkpoints (default: checkpoints)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="logs/simulation.jsonl",
        help="JSONL log file path (default: logs/simulation.jsonl)",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Resume from a checkpoint JSON file",
    )
    parser.add_argument(
        "--fresh-log",
        action="store_true",
        help="Truncate log file at run start instead of appending",
    )
    return parser.parse_args()


def save_checkpoint(
    world: MarsBaseState,
    agents: list[Agent],
    checkpoint_dir: str,
) -> str:
    """Save world + agents to a checkpoint file."""
    os.makedirs(checkpoint_dir, exist_ok=True)
    filename = (
        f"state_sol{world.sol_number}_phase{world.current_phase.value}"
        f"_idx{world.phase_index}.json"
    )
    path = os.path.join(checkpoint_dir, filename)
    payload = {
        "world": world.to_dict(),
        "agents": [a.to_dict() for a in agents],
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    latest_path = os.path.join(checkpoint_dir, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return path


def load_checkpoint(path: str) -> tuple[MarsBaseState, list[Agent]]:
    """Load world and agents from a checkpoint file."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    world = MarsBaseState.from_dict(payload["world"])
    agents = [Agent.from_dict(a) for a in payload["agents"]]
    return world, agents


def print_phase_summary(world: MarsBaseState, agents: list[Agent]) -> None:
    """Simple text visualization after each phase."""
    storm = "YES" if world.active_dust_storm else "no"
    print("\n" + "=" * 60)
    print(
        f"  SOL {world.sol_number} | {world.current_phase.value.upper()} "
        f"| phase #{world.phase_index}"
    )
    print("=" * 60)
    print(f"  Power: {world.power_level:5.1f}%  (effective: {world.effective_power():5.1f}%)")
    print(f"  O2:    {world.oxygen:5.1f}%   Water: {world.water:6.0f}L   Food: {world.food_days:.1f}d")
    print(
        f"  Habitat: {world.habitat_integrity:5.1f}%  "
        f"Rover: {world.rover_fuel:5.1f}%  "
        f"Greenhouse: {world.greenhouse_efficiency:5.1f}%"
    )
    print(f"  Dust storm: {storm}")
    if world.recent_events:
        print(f"  Latest event: {world.recent_events[-1][:80]}")
    print("  --- Last 3 messages ---")
    for msg in world.public_messages[-3:]:
        print(f"    [{msg.from_agent} -> {msg.to}] {msg.content[:100]}")
    if not world.public_messages:
        print("    (none)")
    print("  --- Last decisions ---")
    for agent in agents:
        if agent.last_action_summary:
            reasoning_snip = ""
            if agent.last_reasoning:
                reasoning_snip = f" — {agent.last_reasoning[:80]}"
            print(f"    {agent.name}: {agent.last_action_summary[:80]}{reasoning_snip}")
    print("=" * 60 + "\n")


def run_simulation(args: argparse.Namespace) -> None:
    """Main simulation loop."""
    load_dotenv()
    setup_logging()
    reset_cost_tracker(args.model)

    if args.fresh_log and os.path.exists(args.log_file):
        os.makedirs(os.path.dirname(args.log_file) or ".", exist_ok=True)
        open(args.log_file, "w", encoding="utf-8").close()

    client = create_openrouter_client()

    if args.resume:
        print(f"Resuming from checkpoint: {args.resume}")
        world, agents = load_checkpoint(args.resume)
        target_phase_index = world.phase_index + args.sols * 3
    else:
        world = create_initial_state(seed=args.seed)
        agents = create_agents()
        target_phase_index = args.sols * 3

    agent_map = {a.name: a for a in agents}
    ordered_agents = [agent_map[name] for name in TURN_ORDER]

    print(f"\nMars Base Alpha simulation")
    print(f"  Model: {args.model}")
    print(f"  Seed:  {world.seed}")
    print(f"  Sols:  {args.sols} (from sol {world.sol_number}, phase {world.current_phase.value})")
    print(f"  Target phase index: {target_phase_index}")
    print(f"  Log:   {args.log_file}\n")

    phases_run = 0

    while world.phase_index < target_phase_index:
        rng = make_phase_rng(world)

        passive_deltas = world.apply_phase_events(rng)
        for delta in passive_deltas:
            log_jsonl(args.log_file, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sol": world.sol_number,
                "phase": world.current_phase.value,
                "agent": "SYSTEM",
                "event": "passive",
                "world_delta": delta,
            })

        for agent in ordered_agents:
            result = agent.step(world, client, args.model)

            record: dict = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sol": world.sol_number,
                "phase": world.current_phase.value,
                "agent": result.agent_name,
                "action": (
                    result.action.model_dump() if result.action else None
                ),
                "reasoning": result.reasoning,
                "world_delta": result.world_deltas,
                "error": result.error,
                "skipped": result.skipped,
                "tokens": {
                    "prompt": result.prompt_tokens,
                    "completion": result.completion_tokens,
                },
            }
            log_jsonl(args.log_file, record)

        print_phase_summary(world, ordered_agents)

        phases_run += 1

        world.advance_phase()

        if phases_run % 5 == 0:
            ckpt_path = save_checkpoint(world, agents, args.checkpoint_dir)
            print(f"  [checkpoint saved: {ckpt_path}]")

        if world.oxygen <= 0 or world.water <= 0 or world.food_days <= 0:
            print("\n*** CRITICAL: Life support failure. Simulation ended. ***")
            break

    final_path = save_checkpoint(world, agents, args.checkpoint_dir)
    print(f"\nFinal checkpoint: {final_path}")
    print(cost_tracker.summary())
    print(f"\nSimulation complete after {phases_run} phases ({phases_run / 3:.1f} sols).")


def main() -> None:
    args = parse_args()
    try:
        run_simulation(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
