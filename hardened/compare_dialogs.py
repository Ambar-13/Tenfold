#!/usr/bin/env python3
"""Compare HARDENED_LOG transcript files between tiers.

Usage: python3 hardened/compare_dialogs.py BASE.jsonl OTHER.jsonl [--show sample_id ...]

Prints the count of sessions whose user-side dialog text differs from BASE
(overall and by scenario), and optionally full side-by-side transcripts.
"""
import json
import sys
from collections import Counter
from pathlib import Path


def load(path):
    rows = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[row["sample_id"]] = row
    return rows


def user_msgs(row):
    return tuple(d["user"] for d in row["dialog"])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show = []
    if "--show" in sys.argv:
        show = sys.argv[sys.argv.index("--show") + 1:]
    base, other = load(args[0]), load(args[1])
    changed = Counter()
    total = Counter()
    changed_ids = []
    for sid, row in base.items():
        if sid not in other:
            continue
        total[row["scenario_type"]] += 1
        if user_msgs(row) != user_msgs(other[sid]):
            changed[row["scenario_type"]] += 1
            changed_ids.append(sid)
    n_total = sum(total.values())
    n_changed = sum(changed.values())
    print(f"sessions compared: {n_total}; dialog changed: {n_changed}")
    for scen in sorted(total):
        print(f"  {scen}: {changed[scen]}/{total[scen]} changed")
    for sid in show:
        for name, rows in (("BASE", base), ("OTHER", other)):
            row = rows.get(sid)
            if not row:
                continue
            print(f"\n=== {sid} [{name}] tier={row.get('tier')} scenario={row['scenario_type']} "
                  f"hit_turn={row.get('hit_turn')} rank={row.get('best_rank')} "
                  f"dropped={row.get('dropped_card_entry')!r}")
            print(f"    card: hard={row['intent_card'].get('hard_constraints')} "
                  f"soft={row['intent_card'].get('soft_preferences')}")
            for d in row["dialog"]:
                print(f"  t{d['turn']:>2} USER: {d['user']}")
                print(f"      AGENT ask={d.get('ask')!r} n_recs={d.get('n_recs')}")
                for orig, para in d.get("paraphrase_trace") or []:
                    print(f"      [para] {orig!r} -> {para!r}")


if __name__ == "__main__":
    main()
