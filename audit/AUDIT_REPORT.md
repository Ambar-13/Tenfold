# TENFOLD: empirical uncertainty audit (evaluation half)

Date 2026-08-28 · Status: EVALUATION COMPLETE, single pass · The locked production agent was untouched (every artifact of this audit lives under `audit/`).

This is an **empirical uncertainty audit** of the locked TENFOLD agent's internal candidate ordering. In-family numbers are **empirically calibrated**; out-of-family numbers are **empirical transfer tests**. No promise-of-coverage language is used anywhere in this audit, deliberately.

## 1. Method

**Score.** For every session-turn under the hold-all sweep (agent holds all 10 turns, recommendations blanked evaluator-side so no hit ever ends a session; the agent's ask trajectory is exactly the production policy's), the score is the ground-truth ASIN's rank in the locked agent's full post-rerank internal ordering (+inf if outside the ~400-candidate pool). The top-10 prefix is asserted identical to the emitted slate on every logged turn, so this equals the slate rank whenever it is ≤ 10.

**Calibration (done first, then locked).** `audit/quantiles.json` was committed before any evaluation work existed. Per family, per exact turn bucket t=1..10, on the 85 non-override DEV sessions (one score per session per bucket), q̂_t = the ⌈(85+1)·0.9⌉ = 78th smallest score; if that index falls in the +inf mass, q̂_t = ∞ (set = whole pool).

**Evaluation (this document, once).** The EVAL half of the scenario-stratified dev/eval session split (`audit/split_manifest.json`, fixed before calibration), untouched by every prior analysis, was swept once per family with an instrumented copy of the calibration sweep driver, extended only inside the write-only `_log_turn` path with tie-group run-length encodings of the ordered pool. A session-turn counts as covered iff the target's rank is ≤ the **tie-extended** cutoff at q̂_t: min(q̂_t, pool length) plus every candidate tied at the boundary score, where the tie key is the full deterministic ordering score (ladder level, perfect flag, band, coverage component, bm25). q̂_t = ∞ means set = whole pool, and coverage still fails there when the target is outside the pool entirely.

**Integrity.** All 5×1000 eval turn rows: 0 prefix mismatches, 0 tie-order mismatches, 0 log errors, 0 tie-group-sum mismatches. Byte-determinism check (the single permitted re-run, after all numbers were final): the official-family eval sweep reproduces byte-identically (md5 match on both the turn log and the evaluator result). Agent and result-file checksums unchanged.

## 2. Assumptions

Within-family session exchangeability is an **assumption** about the fixed stratified public set, a quota sample (40 buying / 40 browsing / 15 override / 5 boundary per half), not an i.i.d. draw, and not a theorem. The calibrated statements are therefore empirical statements about this set under the locked six-switch configuration (v2 / selective / conf / other_first / robust / idf), and about nothing else: not the private test set, not any other perturbation distribution. Override sessions violate the exchangeability framing mid-session (the target changes), so they are excluded from calibration and reported descriptively only. Out-of-family rows apply official-calibrated cutoffs to families they were never calibrated on; they are empirical transfer tests and are labeled as such everywhere.

## 3. Results

THE table: `audit/AUDIT_TABLE.md`. Full per-turn JSON: `audit/eval_results.json`. Figure: `audit/uncertainty_audit.svg`.

Headline (nominal-90% calibrated sets, eval half, non-override n=85/family):

| Family | In-family pooled coverage | Transfer pooled (official q̂) |
|---|---|---|
| Official | **93.5%** | n/a |
| H1 | 94.8% | 75.4% *(empirical transfer test)* |
| H2 | 94.6% | 66.0% *(empirical transfer test)* |
| H3 | 90.8% | 60.2% *(empirical transfer test)* |
| Holdout | 97.1% | 72.1% *(empirical transfer test)* |

Every family's in-family coverage lands at or above the nominal 90% (the one-sided construction plus integer-rank ties makes the sets conservative). The transfer rows quantify the expected failure: official-calibrated cutoffs do not transfer to hardened families (60-75% pooled), which is why per-family calibration exists.

**Contraction (official).** q̂_t: 319 → 30 → 5 → 4 → 4 … 4. Eval median (= p90) tie-inflated set size follows identically: 319 → 30 → 5 → 4 → … → 4. This is the disclosure mechanism made visible: the simulated customer's card drains over turns 1-3 (the `other` ask is uniquely exhaustive on official), so by turn 3 the internal ordering has collapsed from uncertainty-over-a-third-of-the-pool to a 4-5 candidate set that fits the slate twice over. The turn-1 number reflects a scenario bimodality: browsing turn-1 ranks are near-uninformative while buying's are excellent, so the early quantile sits in the browsing tail. Hardened families contract slower and bottom out higher (H1 median 423 → 99; H2 → 89-95; H3 barely contracts, 425 → 374; holdout re-inflates late), an honest picture of the information tax those families impose.

**Tie inflation** (reported separately, primary bm25-inclusive key): essentially zero everywhere, median 0 on every family × turn, max 7 (official t=1). bm25 breaks nearly all boundary ties at these cutoffs. The bm25-ignoring sensitivity (whole ~400-row block at the boundary) is in `eval_results.json`.

**Override sessions (descriptive only, n=15/family, excluded from calibration):** pooled coverage under in-family q̂_t, official 82.7%, H1 98.0%, H2 94.7%, H3 98.7%, holdout 95.3%. The official number sits below nominal, as expected: the calibration population is non-override by construction and the target switches mid-session.

## 4. Pre-declared gates (official family, applied once)

- **(a)** In-family pooled eval coverage ≥ 0.85: observed **0.9353** (795/850) → PASS
- **(b)** q̂_t ≤ 50 for every t ≥ 3: observed q̂_3..10 = 5, 4, 4, 4, 4, 4, 4, 4 (max 5) → PASS
- **(c)** Median tie-inflated set size ≤ 20 for every t ≥ 3: observed medians 5, 4, 4, 4, 4, 4, 4, 4 (max 5) → PASS

## Verdict: **GO**

The empirical uncertainty audit ships. Naming discipline throughout: empirically calibrated in-family, empirical transfer tests out-of-family, never promise-of-coverage language. Out-of-family coverages never gated and do not gate now; they are reported above as empirical transfer tests. The locked production agent remains the submission; this audit changed none of its behavior.

## Artifacts

- `audit/eval_results.json`, every number in this report, per turn
- `audit/AUDIT_TABLE.md`, the concise table
- `audit/uncertainty_audit.svg`, the presentation figure
- `audit/split_manifest.json`, the dev/eval session split used for calibration and evaluation
- `audit/quantiles.json`, calibrated cutoffs, fixed before evaluation (md5 4182bebf99ef5aaab72949951ab4e087, unchanged)
