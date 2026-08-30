# Hardened simulator

A fork of the official local evaluator with seeded, tiered user-side perturbations,
for measuring robustness of any agent arm without touching `kit/` or `agent/`.

## What is here

- `local_evaluator.py`, COPY of `kit/evaluator/local_evaluator.py` plus a perturbation
  layer (`Perturber`, `paraphrase_constraint`, template banks). **Scoring, protocol,
  MAX_TURNS, first-hit semantics, override gating, `disclosed`-set semantics, and the
  behavior rng seed (`sample_id\0scenario_type`) are byte-identical to the official
  evaluator**, only the user-side message TEXT (and, at H2+, which/how many card
  strings a reply reveals) is perturbed. The kit stays pristine.
- `__init__.py`, copy of the kit evaluator package init (makes this dir importable
  as the `evaluator` package).
- `run/`, harness overlay: `evaluator -> ..` (this fork), `data -> ../../kit/data`,
  `starter -> ../../agent`. Invoke: `cd hardened/run && python3 -m evaluator.local_evaluator`.
- `run_hardened.sh`, env-driven runner (mirrors `experiments/run_arm.sh`); defaults to
  the headline agent config (selective/conf/other_first/v2). Output goes to
  `results/<PHASE>-<ARM>.json`.
- `compare_dialogs.py`, diff two `HARDENED_LOG` transcript files: count of sessions whose
  user-side dialog changed (overall + per scenario), optional full side-by-side
  transcripts with `--show <sample_id> ...`.

## Tiers (`HARDENED_TIER` env, cumulative)

- **H0** (default), perturbations off. Byte-identical output to the official evaluator
  (verified: `results/d-smoke-h0.json` is `cmp`-clean vs `results/c-conf.json`).
- **H1, paraphrase.** Card constraint strings are paraphrased whenever quoted in
  dialogue (opener, disclosure replies, override message) via an authored deterministic
  rule table (`paraphrase_constraint`): e.g. `100% Cotton -> made of pure cotton`,
  `color: black -> comes in black`, `Machine Wash -> machine washable`,
  `Buckle closure -> done up with a buckle`, `key: value -> the key being value`, and a
  reword-frame fallback for free text. **Every paraphrase is derived only from the
  original card string (synonym/reword/reformat), so it remains TRUE of the target by
  construction**, no attribute values are invented; the per-session logs record every
  `(original -> paraphrase)` pair for audit. Also: 6 disclosure-reply frames instead of
  the single official one, 4 no-preference frames, 3 deflection frames, 3 null-ask
  nudge frames, 5 opener frames per scenario (category anchor tokens kept VERBATIM,
  sentence rewritten), and 7 override templates sharing no fixed prefix ("You know
  what...", "Change of plans:", "On second thought...", "Let's go a different
  direction", "I've been rethinking.", "Wait,", "Different idea entirely:").
- **H2 = H1 + restrictions/reorder.** `other` reveals at most 1 undisclosed constraint
  per ask, and with probability 0.35 (seeded) instead behaves as a uniformly random
  specific attribute (which can answer "no additional preference" while the card is NOT
  exhausted, killing the 'other-is-uniquely-exhaustive' assumption). Disclosure order
  is shuffled per ask (soft-before-hard possible). The boundary deflection hits the
  k-th non-null ask, k seeded-uniform in {1,2,3}, not always the first.
- **H3 = H2 + degradation.** One card entry (seeded) is dropped from the disclosure
  pool, never the override `new_value` (the opener/override quotes are untouched, so a
  dropped opener constraint just means it is never re-disclosable). Light noise: filler
  heads/tails ("Hmm,", "Well,", "I guess.") with p=0.35/0.25, and with p=0.30 one
  seeded adjacent-character swap applied to a TEMPLATE word only (chosen before
  placeholder substitution, so constraint content and category anchors are never
  corrupted).

## Seeding / determinism

Every perturbation draws from `random.Random(f"{sample_id}\0{scenario_type}\0{tier}\0hardened")`,
one rng per session, consumed in fixed order, runs are fully deterministic per
(sample_id, scenario_type, tier). Verified: an H2 rerun is byte-identical (results JSON
and transcript log) to `results/d-smoke-h2.json`. The official behavior rng (override
turn choice) keeps its original seed, so override turns match the official evaluator
at every tier.

## Logging

`HARDENED_LOG=<abs path>` writes one JSON line per session: intent card, override
block, dropped card entry, and the full dialog (user text per turn, agent
`ask_attribute`, recommendation count, paraphrase traces). Write-only; never affects
behavior or the results JSON. The results JSON gains a `"hardened_tier"` key at
H1/H2/H3 only (omitted at H0 to preserve byte-equivalence).

## Usage

```bash
# headline agent config, one tier:
HARDENED_TIER=H2 ARM=smoke-h2 HARDENED_LOG=$PWD/results/d-smoke-h2-log.jsonl hardened/run_hardened.sh

# any other arm: pass the TENFOLD_* switches, e.g.
HARDENED_TIER=H1 ARM=erase-h1 TENFOLD_OVERRIDE=erase TENFOLD_HOLD=hold2 hardened/run_hardened.sh

# dialog-change counts + side-by-side transcripts:
python3 hardened/compare_dialogs.py results/d-smoke-h0-log.jsonl results/d-smoke-h2-log.jsonl --show public_0002
```

Note: `HARDENED_LOG`/`OUT` must be absolute paths (the runner cd's into `hardened/run`).

## Harness sanity (2026-08-28, arm selective/conf/other_first/v2, pre-fix parse/coverage)

End-to-end check that the harness works; the full robustness measurement with
per-scenario breakdowns is in `RESULTS.md`:

| Tier | TS | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| H0 (`results/d-smoke-h0.json`, `cmp`-identical to `c-conf.json`) | 0.89984 | 0.970 | 0.85048 | 3.015 |
| H1 (`results/d-smoke-h1.json`) | 0.44931 | 0.530 | 0.33804 | 6.855 |
| H2 (`results/d-smoke-h2.json`) | 0.40376 | 0.485 | 0.30555 | 7.520 |
| H3 (`results/d-smoke-h3.json`) | 0.30611 | 0.365 | 0.22870 | 8.250 |

Dialog changed vs H0 in 200/200 sessions at every tier (the opener itself is
paraphrased from H1 up); transcript logs at `results/d-smoke-h{0,1,2,3}-log.jsonl`.
`git -C kit status --porcelain` empty after all runs; `PYTHONDONTWRITEBYTECODE=1`
throughout.
