"""Analyze Mars sim JSONL logs: per-run verdicts and aggregate stats from SUMMARY records."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def _load_summaries(path: Path) -> list[dict]:
    summaries: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("agent") == "SUMMARY":
                summaries.append(record)
    return summaries


def _sols_for_run(summary: dict) -> float:
    if "sols_run" in summary and summary["sols_run"] is not None:
        return float(summary["sols_run"])
    final_sol = summary.get("final_sol")
    if final_sol is not None:
        return round(float(final_sol) / 3, 1)
    phases = summary.get("phases_run", 0)
    return round(float(phases) / 3, 1)


def _format_verdict(summary: dict) -> str:
    seed = summary.get("seed", "?")
    survived = summary.get("survived", False)
    sols_run = summary.get("sols_run")
    final_sol = summary.get("final_sol", "?")
    cause = summary.get("cause_of_death") or "unknown"
    phases_run = summary.get("phases_run", 0)

    if survived:
        sols_label = sols_run if sols_run is not None else _sols_for_run(summary)
        outcome = f"SURVIVED {sols_label} sols"
    else:
        outcome = f"DIED sol {final_sol} ({cause})"

    metrics = summary.get("metrics_range") or {}
    o2_min = metrics.get("oxygen", {}).get("min", 0.0)
    water_min = metrics.get("water", {}).get("min", 0.0)
    food_min = metrics.get("food_days", {}).get("min", 0.0)

    vote_count = summary.get("vote_count", 0)
    estimated_usd = summary.get("estimated_usd", 0.0)
    parse_failures = summary.get("parse_failures", 0)

    return (
        f"[seed {seed}] {outcome} | {phases_run} phases | "
        f"O2 min {o2_min} / Water min {water_min:.0f} / Food min {food_min} | "
        f"votes {vote_count} | ${estimated_usd:.4f} | {parse_failures} parse fails"
    )


def _format_aggregate(summaries: list[dict]) -> str:
    total = len(summaries)
    survived_count = sum(1 for s in summaries if s.get("survived"))
    died_count = total - survived_count
    survival_pct = (survived_count / total * 100) if total else 0.0

    sols_values = sorted(_sols_for_run(s) for s in summaries)
    median_sols = statistics.median(sols_values) if sols_values else 0.0

    costs = [float(s.get("estimated_usd", 0.0)) for s in summaries]
    avg_cost = statistics.mean(costs) if costs else 0.0
    total_cost = sum(costs)
    total_parse = sum(int(s.get("parse_failures", 0)) for s in summaries)

    death_causes = Counter(
        s.get("cause_of_death")
        for s in summaries
        if not s.get("survived") and s.get("cause_of_death")
    )
    if death_causes:
        cause_parts = [
            f"{cause} {death_causes[cause]}"
            for cause in ("oxygen", "water", "food")
            if death_causes.get(cause, 0) > 0
        ]
        for cause, count in sorted(death_causes.items()):
            if cause not in ("oxygen", "water", "food"):
                cause_parts.append(f"{cause} {count}")
        deaths_line = "Deaths by cause: " + ", ".join(cause_parts)
    else:
        deaths_line = "Deaths by cause: none"

    lines = [
        "",
        f"=== Aggregate ({total} runs) ===",
        f"Survival rate: {survival_pct:.1f}% ({survived_count} survived / {died_count} died)",
        f"Median sols survived: {median_sols:.1f}",
        f"Avg cost/run: ${avg_cost:.4f}   Total: ${total_cost:.4f}",
        f"Total parse failures: {total_parse}",
        deaths_line,
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Mars sim JSONL logs (SUMMARY records)"
    )
    parser.add_argument(
        "log_path",
        nargs="?",
        default="logs/simulation.jsonl",
        help="Path to simulation JSONL log (default: logs/simulation.jsonl)",
    )
    args = parser.parse_args()
    path = Path(args.log_path)

    if not path.exists():
        print(f"Log not found: {path}")
        sys.exit(0)

    summaries = _load_summaries(path)
    if not summaries:
        print(f"No run summaries found in {path}. Run a simulation first.")
        sys.exit(0)

    for summary in summaries:
        print(_format_verdict(summary))
    print(_format_aggregate(summaries))


if __name__ == "__main__":
    main()
