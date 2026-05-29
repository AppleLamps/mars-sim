# Agent guide — townie

Instructions for the **entire repository** (workspace root: `townie/`). Apply everywhere under this tree unless a subfolder doc says otherwise.

## Repository layout

| Path | Role |
|------|------|
| `AGENTS.md` | Project-wide agent instructions (this file) |
| `mars_sim/` | Mars habitat multi-agent simulation (Python 3.11+) |

When adding top-level packages later, extend this table; keep one canonical `AGENTS.md` at the repo root.

### `mars_sim/` (Python app)

- Entry: `main.py` — run and test from **`mars_sim/`** (`cd mars_sim` before `python …`).
- Core: `world.py`, `actions.py`, `agent.py`, `prompts.py`.
- Tunables: `config.py` — frozen `SimConfig` dataclass (defaults only until wired into `world.py`).
- Tests: `test_regressions.py` — `python test_regressions.py` from `mars_sim/`.
- Do not commit `.env`; use `.env.example` as the template.

## Searching the codebase (Windows)

On this machine, **Grep with `glob` on a directory path often fails** with:

```text
rg: : IO error for operation on : The system cannot find the path specified.
```

That is a **tool/path bug**, not a missing repo or bad pattern. Do not treat it as “no matches” or stop investigating.

**Do instead (any path in the repo):**

1. Grep a **specific file**: e.g. `path: mars_sim/world.py` — **no `glob` parameter**.
2. Or grep a **subdirectory** without `glob`: e.g. `path: mars_sim/`.
3. On IO error, **retry** with (1) or (2), or use **Glob** (no `target_directory` if that errors) + **Read**, or **Shell**: `rg "pattern" mars_sim/world.py`.

**Avoid:** `path: <repo root>` + `glob: **/filename` — reproduces the empty-path IO error on Windows here.

After a failed search, **do not** assume constants or symbols are absent; open the file or retry with a working method.

## Code changes and verification

- Match existing style in the file you edit; keep diffs minimal unless asked for a broader refactor.
- **Confirm behavior in source** (read/grep the real file), not only user-pasted snippets, before claiming values or APIs match.
- Run the **smallest check the user gives** (import one-liner, test file, CLI) from the correct working directory for that package.
- **Commit only when the user explicitly asks.** Never commit secrets (`.env`, API keys).

### Example: `mars_sim` config

```bash
cd mars_sim
python -c "from config import SimConfig; c = SimConfig(); print(c.dust_storm_chance)"
```

Wiring `SimConfig` into `world.py` is a separate step unless the user requests it.
