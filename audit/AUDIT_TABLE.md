# Empirical uncertainty audit: evaluation-half table

Calibrated on the dev half (`audit/quantiles.json`, frozen before any evaluation work); evaluated ONCE on the untouched eval half. In-family rows are empirically calibrated; rows marked *empirical transfer test* apply the official-calibrated cutoffs out-of-family. Non-override sessions (n=85/family); override sessions reported separately below. Within-family session exchangeability is an assumption of the fixed stratified public set, not a theorem.

## Coverage of the nominal-90% calibrated sets (eval half)

| Family | Basis | Pooled (t=1-10) | t=1 | t=2 | t=3 | t=5 | t=10 |
|---|---|---|---|---|---|---|---|
| Official | in-family (empirically calibrated) | 94% | 82% | 92% | 94% | 95% | 95% |
| H1 | in-family (empirically calibrated) | 95% | 76% | 98% | 96% | 96% | 98% |
| H2 | in-family (empirically calibrated) | 95% | 78% | 91% | 93% | 95% | 99% |
| H3 | in-family (empirically calibrated) | 91% | 74% | 84% | 93% | 93% | 96% |
| Holdout | in-family (empirically calibrated) | 97% | 76% | 99% | 99% | 99% | 99% |
| H1 | official-calibrated (*empirical transfer test*) | 75% | 73% | 79% | 72% | 75% | 76% |
| H2 | official-calibrated (*empirical transfer test*) | 66% | 72% | 47% | 48% | 67% | 74% |
| H3 | official-calibrated (*empirical transfer test*) | 60% | 68% | 55% | 49% | 59% | 62% |
| Holdout | official-calibrated (*empirical transfer test*) | 72% | 73% | 72% | 72% | 73% | 71% |

## Calibrated cutoffs q̂_t and tie-inflated set sizes (eval half, in-family)

| Family | Metric | t=1 | t=2 | t=3 | t=5 | t=10 |
|---|---|---|---|---|---|---|
| Official | q̂_t | 319 | 30 | 5 | 4 | 4 |
| | set size median | 319 | 30 | 5 | 4 | 4 |
| | set size p90 | 319 | 30 | 5 | 4 | 4 |
| | tie inflation med/max | 0/7 | 0/0 | 0/1 | 0/0 | 0/0 |
| H1 | q̂_t | ∞ | 258 | 133 | 99 | 141 |
| | set size median | 423 | 258 | 133 | 99 | 141 |
| | set size p90 | 450 | 258 | 133 | 99 | 141 |
| | tie inflation med/max | 0/0 | 0/1 | 0/1 | 0/2 | 0/1 |
| H2 | q̂_t | ∞ | ∞ | 375 | 149 | 95 |
| | set size median | 427 | 422 | 375 | 149 | 95 |
| | set size p90 | 448 | 442 | 375 | 149 | 95 |
| | tie inflation med/max | 0/0 | 0/0 | 0/0 | 0/1 | 0/1 |
| H3 | q̂_t | ∞ | ∞ | ∞ | 385 | 374 |
| | set size median | 425 | 419 | 419 | 385 | 374 |
| | set size p90 | 441 | 444 | 444 | 385 | 374 |
| | tie inflation med/max | 0/0 | 0/0 | 0/0 | 0/0 | 0/1 |
| Holdout | q̂_t | ∞ | ∞ | 258 | 250 | 401 |
| | set size median | 424 | 418 | 258 | 250 | 401 |
| | set size p90 | 442 | 445 | 258 | 250 | 401 |
| | tie inflation med/max | 0/0 | 0/0 | 0/1 | 0/0 | 0/1 |

q̂_t = ∞ means the calibrated set is the whole retrieved pool at that turn (the dev-half 90% order statistic fell in the outside-pool mass); coverage there can still fail when the target is outside the pool entirely. Tie inflation uses the full deterministic ordering score (bm25 included); the coarser bm25-ignoring sensitivity is in `eval_results.json`.

## Override sessions (descriptive only, excluded from calibration; n=15/family)

| Family | Pooled coverage under in-family q̂_t |
|---|---|
| Official | 83% |
| H1 | 98% |
| H2 | 95% |
| H3 | 99% |
| Holdout | 95% |

## Pre-declared gates (official family, applied once)

- Gate (a) pooled eval coverage ≥ 0.85: **94%** → PASS
- Gate (b) q̂_t ≤ 50 for all t ≥ 3: max = 5 → PASS
- Gate (c) median tie-inflated set size ≤ 20 for all t ≥ 3: max = 5 → PASS
- **Verdict: GO**

