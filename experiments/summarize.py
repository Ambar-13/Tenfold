#!/usr/bin/env python3
"""Summarize evaluator result JSONs: FULL / TUNE / TEST + per-scenario metrics.

Usage: python3 summarize.py results/a-smoke-selective-hold2.json [more.json ...]

Split rule: sample_id numeric suffix even = TUNE, odd = TEST.
TechnicalScore = 0.50*HR@10 + 0.30*MRR + 0.20*Efficiency,
Efficiency = clip((11 - MTTC)/10, 0, 1); miss => MTTC contribution 11.
"""
from __future__ import annotations

import json
import re
import sys

MAX_TURNS = 10


def efficiency(mttc: float) -> float:
    return max(0.0, min(1.0, (11.0 - mttc) / 10.0))


def summarize(sessions: list[dict]) -> dict | None:
    if not sessions:
        return None
    n = len(sessions)
    hit_rate = sum(int(s["hit"]) for s in sessions) / n
    mrr = sum(float(s["reciprocal_rank"]) for s in sessions) / n
    mttc = sum(
        s["first_hit_turn"] if s["first_hit_turn"] is not None else MAX_TURNS + 1
        for s in sessions
    ) / n
    eff = efficiency(mttc)
    return {
        "n": n,
        "hit_rate": hit_rate,
        "mrr": mrr,
        "mttc": mttc,
        "efficiency": eff,
        "technical_score": 0.50 * hit_rate + 0.30 * mrr + 0.20 * eff,
    }


def split_of(sample_id: str) -> str:
    match = re.search(r"(\d+)\s*$", str(sample_id))
    if not match:
        return "TEST"
    return "TUNE" if int(match.group(1)) % 2 == 0 else "TEST"


def row(label: str, stats: dict | None) -> str:
    if stats is None:
        return f"{label:<28} (empty)"
    return (
        f"{label:<28} n={stats['n']:<4} TS={stats['technical_score']:.5f} "
        f"HR@10={stats['hit_rate']:.4f} MRR={stats['mrr']:.5f} "
        f"MTTC={stats['mttc']:.4f} Eff={stats['efficiency']:.4f}"
    )


def main() -> None:
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as handle:
            result = json.load(handle)
        sessions = result.get("sessions", [])
        print(f"== {path}")
        print(row("FULL", summarize(sessions)))
        for split in ("TUNE", "TEST"):
            print(row(split, summarize([s for s in sessions if split_of(s["sample_id"]) == split])))
        scenarios = sorted({s["scenario_type"] for s in sessions})
        for scenario in scenarios:
            print(row(f"  {scenario}", summarize([s for s in sessions if s["scenario_type"] == scenario])))
        print()


if __name__ == "__main__":
    main()
