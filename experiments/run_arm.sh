#!/usr/bin/env bash
# Env-driven single-arm runner for the official local evaluator.
#
# Usage:
#   PHASE=a ARM=selective-hold2 \
#   TENFOLD_OVERRIDE=selective TENFOLD_HOLD=hold2 \
#   TENFOLD_ASK=other_first TENFOLD_RETRIEVAL=v1 \
#   experiments/run_arm.sh
#
# Output: results/<PHASE>-<ARM>.json (full per-session raw results kept),
# followed by the FULL/TUNE/TEST + per-scenario summary.
# Behavior switches are the TENFOLD_* env vars only; nothing is forked.
set -euo pipefail

# Keep .pyc files out of kit/ (and everywhere else): no bytecode caches.
export PYTHONDONTWRITEBYTECODE=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PHASE="${PHASE:-a}"
ARM="${ARM:-arm}"
OUT="${OUT:-$ROOT/results/${PHASE}-${ARM}.json}"

mkdir -p "$ROOT/results"
echo "arm=${PHASE}-${ARM} OVERRIDE=${TENFOLD_OVERRIDE:-selective} HOLD=${TENFOLD_HOLD:-hold2} ASK=${TENFOLD_ASK:-other_first} RETRIEVAL=${TENFOLD_RETRIEVAL:-v1}"
cd "$ROOT/run"
python3 -m evaluator.local_evaluator --output "$OUT"
python3 "$ROOT/experiments/summarize.py" "$OUT"
