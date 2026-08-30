#!/usr/bin/env bash
# Env-driven single-arm runner for the HARDENED evaluator fork.
#
# Usage:
#   HARDENED_TIER=H2 PHASE=d ARM=smoke-h2 \
#   TENFOLD_OVERRIDE=selective TENFOLD_HOLD=conf \
#   TENFOLD_ASK=other_first TENFOLD_RETRIEVAL=v2 \
#   hardened/run_hardened.sh
#
# Defaults: HARDENED_TIER=H0 (official-equivalent), agent config =
# selective/conf/other_first/v2. Output: results/<PHASE>-<ARM>.json.
# Optional HARDENED_LOG=<path> writes the per-session dialog transcript JSONL.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIER="${HARDENED_TIER:-H0}"
PHASE="${PHASE:-d}"
ARM="${ARM:-smoke-$(printf '%s' "$TIER" | tr '[:upper:]' '[:lower:]')}"
OUT="${OUT:-$ROOT/results/${PHASE}-${ARM}.json}"

export HARDENED_TIER="$TIER"
export TENFOLD_OVERRIDE="${TENFOLD_OVERRIDE:-selective}"
export TENFOLD_HOLD="${TENFOLD_HOLD:-conf}"
export TENFOLD_ASK="${TENFOLD_ASK:-other_first}"
export TENFOLD_RETRIEVAL="${TENFOLD_RETRIEVAL:-v2}"

mkdir -p "$ROOT/results"
echo "hardened arm=${PHASE}-${ARM} TIER=${HARDENED_TIER} OVERRIDE=${TENFOLD_OVERRIDE} HOLD=${TENFOLD_HOLD} ASK=${TENFOLD_ASK} RETRIEVAL=${TENFOLD_RETRIEVAL}"
cd "$ROOT/hardened/run"
python3 -m evaluator.local_evaluator --output "$OUT"
python3 "$ROOT/experiments/summarize.py" "$OUT"
