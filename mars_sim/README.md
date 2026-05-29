# Mars-sim

Multi-agent Mars base simulation using OpenRouter. Four crew members — Commander, Engineer, Scientist, and Medic — make strategic decisions each phase while life support slowly degrades and random events (dust storms, sensor anomalies) stress the habitat.

## Requirements

- Python 3.11+
- [OpenRouter](https://openrouter.ai/) API key

## Install

```bash
cd mars_sim
pip install -r requirements.txt
copy .env.example .env    # Windows
# cp .env.example .env    # macOS/Linux
```

Edit `.env` and set your key:

```
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

## Run

```bash
python main.py --sols 20 --model anthropic/claude-sonnet-4 --seed 42 --fresh-log
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--sols` | 10 | Number of Martian days to simulate |
| `--model` | `anthropic/claude-sonnet-4` | OpenRouter model id |
| `--seed` | 42 | Random seed for reproducibility |
| `--checkpoint-dir` | `checkpoints` | Auto-save directory |
| `--log-file` | `logs/simulation.jsonl` | Decision log (JSONL) |
| `--resume` | — | Resume from a checkpoint JSON file |
| `--fresh-log` | off | Truncate log file at run start |

### Example commands

```bash
# Quick 1-sol smoke test
python main.py --sols 1 --seed 1 --fresh-log

# Cheaper model for testing
python main.py --sols 20 --model openai/gpt-4o-mini --seed 42 --fresh-log

# Resume from last checkpoint (runs N more sols from saved state)
python main.py --sols 10 --resume checkpoints/latest.json --fresh-log
```

## Swapping models

Pass any [OpenRouter model id](https://openrouter.ai/models) via `--model`:

```bash
python main.py --model openai/gpt-4o-mini --sols 10
python main.py --model google/gemini-2.0-flash-001 --sols 20
python main.py --model anthropic/claude-sonnet-4 --sols 20
```

Note: `anthropic/claude-3.5-sonnet` is currently unavailable on OpenRouter; use `anthropic/claude-sonnet-4` instead.

Cost estimates at the end of each run are approximate and based on static per-model pricing in `utils.py`.

## Output

- **Console**: phase summaries with metrics, last 3 messages, and last agent decisions with reasoning snippets.
- **`logs/simulation.jsonl`**: one JSON object per line — timestamp, sol, phase, agent, action, reasoning, world deltas, tokens.
- **`logs/parse_failures.jsonl`**: raw LLM output when JSON parsing fails (for debugging).
- **`checkpoints/`**: auto-save every 5 phases plus a final save. `latest.json` always points to the most recent state.

## Simulation design

- **Time**: 3 phases per sol — morning, midday, evening. Each phase, every agent gets one turn (Commander → Engineer → Scientist → Medic).
- **World state**: power, oxygen, water, food days, habitat integrity, rover fuel, greenhouse efficiency, resource stockpile, relationships, public messages, pending votes.
- **Events**: 15% dust storm chance per phase, passive life-support drain, ISRU recovery, greenhouse food production, ~8% sensor anomalies.
- **Agents**: short-term memory (last 5 observations), private beliefs (capped at 8), diary entries via `write_diary`.
- **Actions**: 11 strict Pydantic-validated action types including `cast_vote` for pending votes.
- **Guards**: EVA blocked when rover fuel < 10% or severe dust storm; sample analysis requires collected samples.

## Actions (11)

| Action | Purpose |
|--------|---------|
| `repair_system` | Fix habitat, life support, power, or rover |
| `allocate_power` | Rebalance subsystem power (always sums to 100%) |
| `send_message` | Message another agent or all |
| `propose_vote` | Start a crew vote (Commander typically) |
| `cast_vote` | Vote on a pending topic |
| `conduct_eva` | Extra-vehicular activity for samples |
| `analyze_sample` | Analyze a collected sample |
| `adjust_greenhouse` | Tune greenhouse efficiency |
| `write_diary` | Private diary entry + optional belief update |
| `request_resource` | Request from finite stockpile |
| `observe_environment` | Observe atmosphere, geology, sensors, or crew |

## Expected behavior (10–20 sols)

Over a longer run you should see:

- **Resource trade-offs**: Engineer prioritizes power during dust storms; Medic pushes for O2/water; Scientist requests EVA windows.
- **Votes**: Commander proposes votes; crew casts votes; majority resolves after 2+ votes.
- **Messaging and alliances**: targeted messages; relationship scores drift.
- **Gradual stress**: passive degradation + ISRU balance means active management is required by sol 15–20.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `OPENROUTER_API_KEY not set` | Create `.env` from `.env.example` and add your key |
| Invalid JSON / skipped turns | Check `logs/parse_failures.jsonl`; try `openai/gpt-4o-mini` |
| Rate limits | Simulation retries 429/5xx automatically; reduce `--sols` if persistent |
| High cost | Use `openai/gpt-4o-mini`; compact prompts reduce tokens ~40% |
| Resume wrong sol count | `--sols N` means N additional sols from checkpoint phase_index |

## Project layout

```
mars_sim/
  main.py          # CLI entrypoint
  world.py         # MarsBaseState + events + apply_action
  agent.py         # Agent dataclass + step()
  prompts.py       # Role system prompts + user prompt builder
  actions.py       # Pydantic action schema (11 actions)
  utils.py         # OpenRouter client, JSON parse/retry, cost tracking
  requirements.txt
  .env.example
  README.md
```
