# Verification

Everything in the README can be checked from this repository. This page says how,
and records what we tested and rejected along the way.

## Reproduce the headline score

All commands on this page are run from the repository root. With the organizer kit
cloned into `kit/` (see the README setup steps):

```bash
make eval
```

That runs the official harness with no configuration and compares the output against the
stored result. The long form, if you would rather see it:

```bash
cd run
python3 -m evaluator.local_evaluator --output /tmp/out.json
cmp /tmp/out.json ../results/e-fixed-official.json && echo BYTE-IDENTICAL
```

No environment variables, no flags, no network. On a laptop this takes about nine
seconds and prints `recommended_technical_score` 0.902435. The `cmp` line is the point:
the run reproduces the stored result byte for byte, not approximately.

Environment used for the numbers in this repository: Python 3.13.14, SQLite 3.53.4 with
FTS5, macOS on arm64. Python 3.10 or newer with FTS5 is sufficient.

## What is actually shipped

The agent is these six files and nothing else. Their MD5 checksums:

| File | MD5 |
| --- | --- |
| `agent/__init__.py` | `ebf3e0f48b3475889a02a4e2e4161f18` |
| `agent/agent.py` | `7d5ad68842619479c026d2be65f46f1a` |
| `agent/override.py` | `e8fc2d6df58b5611c9903a61097e9dd4` |
| `agent/policy.py` | `cc8a400acc2070ed97ae1afdfdc82141` |
| `agent/retrieval.py` | `e913fc83f649db212f8c664f2d6a2742` |
| `agent/state.py` | `e8dd96e9727d8cbd28945c74b3cab101` |

```bash
md5 agent/*.py        # or: md5sum agent/*.py
```

The official harness imports this package as `starter` and constructs it with
`Agent(catalog_path)`, taking no configuration. The environment switches documented in
the README exist only so that earlier configurations stay reproducible from the same
code; every one of them defaults to the shipped production value.

## Checks we ran on the shipped agent

Run them yourself with `make test`, which covers the contract, determinism and
stored-result consistency checks in `tests/`.

| Check | Result |
| --- | --- |
| Bare run vs stored result | byte-identical |
| Explicit-configuration run vs bare run | byte-identical |
| Two clean runs from fresh processes | byte-identical |
| Legacy configuration (`TENFOLD_RETRIEVAL=v1`) | reproduces its stored result byte-for-byte |
| Response contract, 2,000 turns across all 200 sessions | 0 violations, 0 escaped exceptions |
| Recommendation payloads | exactly 10 unique catalog-valid ASINs whenever recommending |
| Fresh extraction: agent package alone, beside the organizer's evaluator and data | byte-identical result |

The contract check validates every response independently of the evaluator: `message` is
a string, `ask_attribute` is an allowed value or null, recommendations are unique and
present in the catalog, ordering is deterministic, and no exception escapes `respond`.

## What we tested and did not ship

Late in the project we designed an adaptive stopping policy: rather than opening
recommendations on the current rule, the agent would decide each turn from a calibrated
estimate of its own uncertainty. Before building it we wrote down what it had to beat,
because a threshold chosen after seeing results is not a threshold. It had to gain at
least 0.03 TechnicalScore in the worst of four paraphrase families.

We then measured the ceiling: for each session, the best score any same-ask stopping
rule could achieve with perfect foresight of its own future rankings. That ceiling is an
upper bound on every variant in the family, so if the ceiling misses the bar, no
implementation can clear it.

| Family | Shipped agent | Perfect-foresight ceiling | Gain |
| --- | --- | --- | --- |
| H1 | 0.676987 | 0.697733 | +0.020746 |
| H2 | 0.669704 | 0.695912 | +0.026208 |
| H3 | 0.529065 | 0.553562 | +0.024496 |
| Holdout | 0.653529 | 0.673350 | +0.019821 |

Worst family +0.019821 against a +0.03 requirement, on 100 development sessions per
family. The ceiling itself missed the bar, so the idea was rejected rather than tuned
into looking better. The full analysis, including the per-session detail and the
bootstrap bound, is in [`../audit/rejected-variant-oracle.json`](../audit/rejected-variant-oracle.json).

Two details worth stating plainly. The oracle finds almost no *new* hits, only better
ranks, and it sometimes reaches them by waiting longer, which no real controller can do
without foresight. And the measurement used a development half only; the evaluation half
recorded in [`../audit/split_manifest.json`](../audit/split_manifest.json) was untouched.

The uncertainty audit described in the README is what survived from that line of work,
in report-only form: it measures the agent, and changes nothing about how it behaves.

## Final evaluation

The organizer's final-evaluation FAQ sets out the protocol: the 800 final sessions are
released after the submission deadline, and teams run the unmodified official evaluator
themselves, in their own environment, using the Git commit submitted before the deadline.
That commit is frozen; the agent, its configuration and its indexes must not change once
the package is released. Teams retain the generated `results.json` with its per-session
rows, the commit hash, and the environment details, and the organizer may ask for them.

This repository is built for that protocol. The submitted commit is the frozen artifact,
`make eval` runs the unmodified evaluator with no configuration, the run is deterministic,
and `results/` already carries the per-session rows of every run we report. To score the
final package, drop it into `kit/data/` and run:

```bash
make eval OUT=results-final.json
```

The same FAQ confirms two things this submission relies on: the final evaluation uses the
same deterministic customer-message templates as the public set, with no undisclosed
paraphrases, and a non-LLM approach is explicitly permitted.

## What none of this shows

These are all public-set and self-built-evaluator results. They show the agent is
deterministic, contract-clean, reproducible from this repository, and that it degrades
gracefully under paraphrases we wrote. They do not predict the private evaluation, and
no number here should be read as a private-set estimate.
