# mars-sim

Multi-agent Mars habitat simulation: four crew roles (Commander, Engineer, Scientist, Medic) decide each phase via [OpenRouter](https://openrouter.ai/) while life support degrades, dust storms hit, and votes and EVAs play out.

## Quick start

```bash
cd mars_sim
pip install -r requirements.txt
copy .env.example .env    # Windows — then add OPENROUTER_API_KEY
python main.py --sols 1 --seed 42 --fresh-log
```

## Repository layout

| Path | Description |
|------|-------------|
| [`mars_sim/`](mars_sim/) | Python application — `main.py`, `world.py`, agents, tests |
| [`mars_sim/README.md`](mars_sim/README.md) | Full install, CLI, actions, troubleshooting |
| [`AGENTS.md`](AGENTS.md) | Notes for coding agents working in this repo |

## Requirements

- Python 3.11+
- OpenRouter API key (see [`mars_sim/.env.example`](mars_sim/.env.example))

## Tests

```bash
cd mars_sim
python -m unittest test_regressions -v
```

## Configuration

Simulation tunables live in [`mars_sim/config.py`](mars_sim/config.py) as a frozen `SimConfig` dataclass. `MarsBaseState` holds a `config` instance (not serialized in checkpoints).

## License

Add a license file if you plan to open-source this project publicly.
