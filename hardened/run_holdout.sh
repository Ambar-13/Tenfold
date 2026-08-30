#!/usr/bin/env bash
# Runner for the HOLDOUT evaluator (independently rewritten paraphrase banks).
# Reuses the hardened/run overlay (evaluator -> hardened/, starter -> agent/,
# data -> kit/data); the holdout module lives at hardened/holdout_evaluator.py.
#
# Usage:
#   HARDENED_TIER=H1 PHASE=e ARM=fixed-holdout hardened/run_holdout.sh
#
# Defaults: HARDENED_TIER=H1 (paraphrase-only, the comparison tier), agent
# config = fixed production (selective/conf/other_first/v2, robust/idf via
# agent defaults unless overridden). Output: results/<PHASE>-<ARM>.json.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIER="${HARDENED_TIER:-H1}"
PHASE="${PHASE:-e}"
ARM="${ARM:-fixed-holdout}"
OUT="${OUT:-$ROOT/results/${PHASE}-${ARM}.json}"

export HARDENED_TIER="$TIER"
export TENFOLD_OVERRIDE="${TENFOLD_OVERRIDE:-selective}"
export TENFOLD_HOLD="${TENFOLD_HOLD:-conf}"
export TENFOLD_ASK="${TENFOLD_ASK:-other_first}"
export TENFOLD_RETRIEVAL="${TENFOLD_RETRIEVAL:-v2}"

mkdir -p "$ROOT/results"
echo "holdout arm=${PHASE}-${ARM} TIER=${HARDENED_TIER} OVERRIDE=${TENFOLD_OVERRIDE} HOLD=${TENFOLD_HOLD} ASK=${TENFOLD_ASK} RETRIEVAL=${TENFOLD_RETRIEVAL} PARSE=${TENFOLD_PARSE:-<default>} COVERAGE=${TENFOLD_COVERAGE:-<default>}"
cd "$ROOT/hardened/run"
python3 -m evaluator.holdout_evaluator --output "$OUT"
python3 "$ROOT/experiments/summarize.py" "$OUT"
