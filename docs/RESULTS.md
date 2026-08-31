# TENFOLD Results

The evidence behind every number we claim. Each section is one experiment; every headline and table number comes from a raw evaluator JSON in `results/` (a few attribution diagnostics, noted where they appear, reproduce from the documented switch settings instead). Result files are prefixed by experiment: `a-` state and invalidation, `b-` retrieval v2, `c-` confidence controller, `d-` robustness matrix, `e-` the parser and coverage fix.

Protocol, common to all experiments:

- Official runs: the organizer's unmodified local evaluator, all 200 public sessions, invoked as `cd run && python3 -m evaluator.local_evaluator --output <path>` through the `run/` symlink overlay. `git -C kit status --porcelain` was empty after every run; `PYTHONDONTWRITEBYTECODE=1` throughout.
- Every arm is an environment-switch configuration (`TENFOLD_*`, read once at agent init); nothing is forked.
- Split discipline: sessions with even numeric sample-id suffix form TUNE, odd form TEST. All tuning was fit on TUNE only; TUNE, TEST, and FULL are reported. `experiments/summarize.py` recomputes TUNE/TEST/FULL from the raw sessions, and its FULL TS matches the evaluator's `recommended_technical_score` on every arm.
- Determinism: headline arms were rerun independently and compared with `cmp`; every such rerun was byte-identical. Specific checks are noted per section.
- All numbers are public-set numbers. Hardened and holdout numbers are reported alongside official numbers, never blended. No private-set claims anywhere.

## 0. Baseline

The kit's weak BM25 starter, run through the `run_kit/` overlay: TS 0.10671, HitRate@10 0.125, MRR 0.06803, MTTC 9.810 (`results/d-kitstarter-official.json`).

## 1. Typed state and selective invalidation (retrieval v1)

Method: typed dialogue state with per-slot KEEP/SET/DELETE/UNKNOWN ops, cue-battery plus slot-conflict override detection, retrieval v1 (a faithful port of the kit-style FTS5/BM25 query builder, so this experiment isolates state and invalidation effects). Ablation axis: `TENFOLD_OVERRIDE` in {erase, keep_category, selective} at fixed hold2, plus a hold-depth axis for selective. `erase` drops the whole state including the category anchor; `keep_category` erases everything but the anchor.

| Arm | TS (FULL) | HR@10 | MRR | MTTC | TS (TUNE) | TS (TEST) |
|---|---|---|---|---|---|---|
| override-erase (hold2) | 0.71281 | 0.790 | 0.64203 | 4.740 | 0.69378 | 0.73184 |
| override-keep_category (hold2) | 0.73556 | 0.820 | 0.65288 | 4.515 | 0.71428 | 0.75685 |
| **override-selective (hold2)** | **0.77766** | **0.865** | 0.69619 | 4.185 | 0.76734 | 0.78797 |
| selective-hold0 | 0.74823 | 0.865 | 0.55444 | 3.530 | 0.74680 | 0.74967 |
| selective-hold1 | 0.75646 | 0.865 | 0.58853 | 3.630 | 0.75674 | 0.75617 |
| selective-hold3 | 0.76906 | 0.865 | 0.71820 | 4.945 | 0.76086 | 0.77727 |

Per-scenario, override modes (the three modes are identical on buying/browsing/boundary; the whole effect lives in intent_override, as designed):

| intent_override (n=30) | erase | keep_category | selective |
|---|---|---|---|
| TS | 0.29833 | 0.45005 | 0.73066 |
| HR@10 | 0.3333 | 0.5333 | **0.8333** |
| MRR | 0.28333 | 0.35571 | 0.64443 |
| MTTC | 8.667 | 7.167 | 4.967 |

Findings:

- Override hit rate: erase 0.333, keep_category 0.533, selective 0.833. The mechanism is exactly the hypothesized one: the simulator's disclosed set never resets, so hard-erased evidence is unrecoverable.
- FULL TS effect of the override policy alone: +0.065 (0.71281 to 0.77766) at fixed hold2.
- Hold axis: HR@10 is invariant at 0.865 for holds 0 through 3; holding trades the 0.20-weight efficiency term for the 0.30-weight MRR term, and TS peaks at hold2.
- Determinism: `a-override-selective.json` is byte-identical to the independent smoke run `a-smoke-selective-hold2.json`.

## 2. Conjunctive field-aware retrieval (v2)

Method: `RetrieverV2` behind `TENFOLD_RETRIEVAL=v2`, with v1 untouched. Active typed constraints compile to a query plan: a category hard filter from the opener anchor (FTS5 `categories:` column filter), a numeric price BETWEEN when a budget interval exists, NOT clauses from negative-polarity constraints, and an OR-of-terms query for ranking. Ranking is field-weighted BM25 (title > features > description) followed by a constraint-coverage rerank; a relaxation ladder (drop price, then category, then fall back to the v1 query) keeps the pool from going empty. Catalog facts that make the ladder load-bearing: 1,115 distinct anchor classes, median class pool 8 products, 588 classes under 10. Tunables were fit on TUNE only over an 8-config grid (`results/b-tune-v2-cfg0..7.json`); the winning config was baked as defaults, and the official smoke with baked defaults is byte-identical to the winning tuning run.

| Split | TS | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| FULL (v1 reference) | 0.77766 | 0.865 | 0.69619 | 4.185 |
| **FULL (v2)** | **0.89284** | **0.970** | **0.85048** | **3.365** |
| TUNE (n=100) | 0.88813 | 0.970 | 0.83710 | 3.400 |
| TEST (n=100) | 0.89756 | 0.970 | 0.86386 | 3.330 |

TEST is at or above TUNE on every metric: no visible overfit. Rank-1 share among hits: 159/194 = 0.820 (v1: 124/173 = 0.717).

Component ablation (`TENFOLD_V2_ABLATE`, one switch per arm, everything else fixed at selective/hold2):

| Arm | TS (FULL) | HR@10 | MRR | MTTC | rank-1 share |
|---|---|---|---|---|---|
| v1 reference | 0.77766 | 0.865 | 0.69619 | 4.185 | 0.717 |
| **v2 (full)** | **0.89284** | **0.970** | 0.85048 | 3.365 | 0.820 |
| v2 no_rerank (coverage rerank off) | 0.71785 | 0.800 | 0.64016 | 4.710 | 0.719 |
| v2 no_filters (hard filters off) | 0.86246 | 0.945 | 0.80287 | 3.545 | 0.772 |
| v2 hold0 | 0.85731 | 0.970 | 0.67938 | 2.575 | 0.582 |
| v2 hold1 | 0.87245 | 0.970 | 0.73918 | 2.715 | 0.655 |
| v2 hold3 | 0.87914 | 0.970 | 0.86114 | 4.210 | 0.835 |

Attribution:

- **The coverage rerank is the engine: +0.175 TS.** Removing it costs 0.89284 to 0.71785, and filters plus tuned field weights without the rerank land 0.060 TS below the v1 baseline. Inside a category-filtered pool the anchor tokens no longer discriminate, so without the rerank the within-class BM25 ordering is driven by ubiquitous boilerplate terms.
- **Hard filters are a precision guard worth +0.030 with the rerank present.** Their marginal value concentrates where it should: intent_override HR 0.9667 with filters versus 0.900 without.
- **Field weighting contributes roughly nothing by itself**, consistent with the near-flat weight axis in the tuning grid. Ordering: rerank >> filters > weights, and the two main components are super-additive.
- Hold axis: TS peaks at hold2 again; HR@10 is hold-invariant at 0.970.

Miss analysis: v1 misses 27 sessions, v2 misses 6, and the 6 are a strict subset of the v1 miss set (zero new misses). Of the 6: four sessions (public_0083, 0087, 0144, 0174) have intent cards made entirely of boilerplate ("100% Cotton", "Imported", closure types) that dozens of in-class products contain verbatim (32/691, 60/288, 20/101, 12/179 respectively), so no retrieval signal separates the target from its tie class. Two sessions (public_0020, 0145) have evaluator-synthesized "color: X" strings that the target's own text does not contain (verified at token level), which forfeits coverage credit on the target while awarding it to look-alike competitors.

## 3. Confidence controller

Method: `TENFOLD_HOLD=conf` replaces the fixed hold depth with a per-turn decision. Features per turn: number of active constraints, whether the top candidate token-contains every active constraint at relaxation-ladder level 0 (price in range when an interval is active), the top1-to-top2 coverage gap, and whether the card is exhausted. Decision: recommend when the top candidate saturates with a positive gap and enough constraints are active, when the card is exhausted, or at a fallback turn; otherwise hold and ask. Tunables were fit on TUNE only over an 8-config grid (`results/c-tune-conf-cfg0..7.json`); winner: gap_min 0.0, sat_min_constraints 2, fallback_turn 3, baked as defaults. The decision rule for the shipped default was fixed in advance: argmax on TUNE between conf and hold2.

| Arm | TS (FULL) | TS (TUNE) | TS (TEST) | HR@10 | MRR | MTTC |
|---|---|---|---|---|---|---|
| v2 hold0 | 0.85731 | 0.85402 | 0.86061 | 0.970 | 0.67938 | 2.575 |
| v2 hold1 | 0.87245 | 0.87024 | 0.87467 | 0.970 | 0.73918 | 2.715 |
| v2 hold2 | 0.89284 | 0.88813 | 0.89756 | 0.970 | 0.85048 | 3.365 |
| v2 hold3 | 0.87914 | 0.87818 | 0.88011 | 0.970 | 0.86114 | 4.210 |
| **conf (controller)** | **0.89984** | **0.89473** | **0.90496** | 0.970 | 0.85048 | **3.015** |

The controller beats every fixed hold on TS on all three splits. Against hold2 it is a per-session Pareto improvement on the public set:

- Identical miss set (the six residuals from the retrieval experiment), zero rank changes on any hit, so MRR is exactly equal, not merely to six decimals.
- 66 sessions hit strictly earlier at identical rank (62 saved one turn, 4 saved two; 70 turns total); zero sessions got slower. 65 of the 66 hit at rank 1.
- The entire +0.0070 FULL TS is bought on the 0.20-weight MTTC axis at zero MRR/HR cost.

Open-turn distribution (from the instrumented run, `results/c-conf-log.jsonl`): 15 sessions open at turn 1, 72 at turn 2, 113 at the turn-3 fallback; mean 2.490. All 87 pre-fallback opens were saturation-certificate opens; gap at the open turn had minimum 0.471 and median 1.833. Boundary sessions never open early (the deflected first ask keeps the constraint count below threshold, as designed), and one session re-held after an early open when a disclosure de-saturated the top candidate.

Determinism and isolation checks: the benchmark rerun is byte-identical to the builder's smoke and the tuning run; post-implementation reruns of the fixed-hold arms are byte-identical to their stored files; logging is byte-non-interfering (logged and unlogged runs match).

## 4. Robustness matrix (hardened simulators)

Method: `hardened/local_evaluator.py` is a fork of the official evaluator whose scoring, protocol, turn limits, first-hit semantics, override gating, and behavior RNG seed are identical to the official evaluator; only the user-side message text (and, at H2+, what a reply reveals) is perturbed. Tiers are cumulative, seeded, and deterministic, and H0 was verified byte-identical to the official evaluator. Every paraphrase is derived only from the original card string, so it remains true of the target by construction.

- **H1 paraphrase:** card strings paraphrased wherever quoted, 6 reply frames, 4 no-preference frames, 3 deflection frames, 3 nudge frames, 5 opener frames per scenario (anchor tokens kept verbatim), 7 override templates with no shared prefix.
- **H2 = H1 + restriction/reorder:** the broad "other" ask reveals at most one constraint and sometimes behaves as a random specific attribute; disclosure order shuffled; the boundary deflection lands on a random early ask.
- **H3 = H2 + degradation:** one card entry dropped from the disclosure pool (never the override value), filler noise, and seeded typos in template words only, so constraint content is never corrupted.

Matrix (TS, with HR@10 / MRR / MTTC underneath; 200 sessions per cell; agent arms at their stored configurations, pre-fix parse/coverage):

| Arm | official | H1 | H2 | H3 |
|---|---|---|---|---|
| kit weak_bm25 baseline | **0.10671**<br>.125 / .06803 / 9.810 | **0.15265**<br>.175 / .11115 / 9.410 | **0.14515**<br>.165 / .10850 / 9.495 | **0.12833**<br>.145 / .09878 / 9.690 |
| v1/selective/hold2 | **0.77766**<br>.865 / .69619 / 4.185 | **0.73585**<br>.830 / .63650 / 4.505 | **0.68048**<br>.795 / .55561 / 5.185 | **0.57116**<br>.670 / .46719 / 6.200 |
| v2/selective/conf (pre-fix headline) | **0.89984**<br>.970 / .85048 / 3.015 | **0.44931**<br>.530 / .33804 / 6.855 | **0.40376**<br>.485 / .30555 / 7.520 | **0.30611**<br>.365 / .22870 / 8.250 |
| v2/selective/hold2 no_rerank | **0.71785**<br>.800 / .64016 / 4.710 | **0.71488**<br>.810 / .61360 / 4.710 | **0.66172**<br>.775 / .53507 / 5.315 | **0.56454**<br>.660 / .46812 / 6.295 |
| v2/selective/conf, verbatim boost 0 | **0.87858**<br>.965 / .79528 / 3.125 | **0.44910**<br>.530 / .33733 / 6.855 | **0.40470**<br>.490 / .29999 / 7.515 | **0.30111**<br>.360 / .22238 / 8.280 |
| v2/selective/hold2 | 0.89284<br>.970 / .85048 / 3.365 | **0.45040**<br>.530 / .34233 / 6.865 | **0.40376**<br>.485 / .30555 / 7.520 | **0.30611**<br>.365 / .22870 / 8.250 |

TS retention versus each arm's own official cell (H1 / H2 / H3): baseline 1.43 / 1.36 / 1.20 (paraphrase frames feed its bag-of-words more tokens, at a useless absolute level), v1 0.946 / 0.875 / 0.734, pre-fix headline 0.499 / 0.449 / 0.340, no_rerank 0.996 / 0.922 / 0.786.

Attribution of the collapse (H1 is the whole story: -0.451 TS):

- **The controller is exonerated:** hold2 at H1 scores 0.45040 versus conf 0.44931, and is identical at H2/H3. The confidence certificate simply stops firing under paraphrase and falls back to turn-3 opens.
- **The verbatim-phrase boost is exonerated as the cause:** zeroing it changes H1 by -0.0002. Its official-tier value (+0.021 TS) is fully neutralized at H1, but it is not what breaks.
- **The coverage rerank is the brittle component:** same filters, same pool, rerank off retains 99.6 percent of its official score at H1 (0.71488) while rerank on scores 0.44931. At H1 the pre-fix headline misses 94 sessions and no_rerank misses 38; 62 sessions miss under the headline while no_rerank hits.
- **Mechanism, from the H1 transcript log:** of 1077 non-opener user turns, only 64 matched the one reply frame the parser knew. 490 were unrecognized no-preference frames ("Nothing else comes to mind about other.") ingested as free-text constraints, 324 were paraphrased disclosures parsed with frame words attached ("good question - let's say..."), and 13 were unrecognized nudge/deflection frames. The state fills with polluted text, and the rerank scores candidates by exact token containment of that raw text, sorted above BM25 order. Products never contain "nothing else comes to mind", so coverage stops discriminating and actively reorders good BM25 pools. v1 survives (0.946 retention) because the same polluted text just adds OR-terms to its query; the rerank turns parse noise into rank inversions.
- The H2 and H3 steps are arm-independent information taxes, not component failures: H2-H1 costs -0.045 for the headline, -0.055 for v1, -0.053 for no_rerank (slower disclosure for every agent); H3-H2 costs -0.098 / -0.109 / -0.097 (strictly less evidence for every retrieval arm).
- Override detection itself survives paraphrase respectably: v1-arm override HR at H1 is 0.867, above its own official 0.833. The residual override loss in the headline arm is retrieval, not detection.

Determinism: the fresh headline H1/H2/H3 runs are byte-identical to the stored smoke runs.

## 5. The fix: frame-agnostic parsing and IDF coverage

Two coupled repairs in `agent/` only, behind two new switches with production-first defaults. Legacy arms reproduce byte-identically with `TENFOLD_PARSE=frame TENFOLD_COVERAGE=contain` (verified: the pre-fix headline rerun under the final code is `cmp`-clean against `results/c-conf.json`). We fixed the acceptance bars before running the fix: official FULL TS at least 0.895, and hardened H1 FULL TS at least 0.72 (the no_rerank floor).

1. `TENFOLD_PARSE=robust` (`agent/state.py`): every user message is classified semantically before extraction. A generic-English no-preference battery split into exhaustion versus deflection sub-cues, with a residual-content check so mixed messages still parse as disclosures and a repeat-deflection escalation; a nudge battery; and frame/filler stripping (discourse heads, content-introducer frames, hedge tails) before disclosure text is segmented. No-info classification runs before override detection. Card-exhaustion detection comes from the semantic class, restoring the ask rotation and exhaustion-opens under any phrasing. Verbatim official frames short-circuit to the legacy paths, so robust mode is behaviorally identical on the official evaluator.
2. `TENFOLD_COVERAGE=idf` (`agent/retrieval.py`): coverage is scored per constraint as the candidate's share of the constraint's IDF mass (document frequencies from the catalog FTS corpus, built once at init); slot-label tokens ("color:", "size:") are dropped when value tokens exist; zero-df tokens are excluded as non-discriminative. Ordering is two-regime: only a candidate that conjunctively saturates every active constraint may be hoisted across the pool; everything sub-perfect keeps BM25 order, with coverage available as a tie-breaker within BM25 bands (TUNE-selected default: fully distrust sub-perfect coverage).

| Arm | official | H1 | H2 | H3 |
|---|---|---|---|---|
| v1/selective/hold2 | **0.77766**<br>.865 / .69619 / 4.185 | **0.73585**<br>.830 / .63650 / 4.505 | **0.68048**<br>.795 / .55561 / 5.185 | **0.57116**<br>.670 / .46719 / 6.200 |
| v2/selective/conf, pre-fix | **0.89984**<br>.970 / .85048 / 3.015 | **0.44931**<br>.530 / .33804 / 6.855 | **0.40376**<br>.485 / .30555 / 7.520 | **0.30611**<br>.365 / .22870 / 8.250 |
| **v2/selective/conf/robust/idf (production)** | **0.90243**<br>.975 / .84578 / 2.940 | **0.72428**<br>.825 / .61194 / 4.590 | **0.69437**<br>.815 / .56723 / 5.165 | **0.58189**<br>.680 / .47297 / 6.000 |

Retention (H1 / H2 / H3): v1 0.946 / 0.875 / 0.734; pre-fix 0.499 / 0.449 / 0.340; **production 0.803 / 0.769 / 0.645**. The production config is the best arm in the table on official (+0.0026 over the pre-fix headline), H2 (+0.0139 over v1), and H3 (+0.0107 over v1), and trails v1 at H1 by only 0.0116, versus the pre-fix gap of 0.287 at the same tier.

Both pre-set bars passed: official 0.90243 (TUNE 0.89236 / TEST 0.91251) against 0.895, and H1 0.72428 against 0.72.

Per-scenario, production config:

| Scenario (n) | official TS / HR / MRR / MTTC | H1 TS / HR / MRR / MTTC |
|---|---|---|
| buying (80) | 0.91120 / .9625 / .88317 / 2.750 | 0.70038 / .8125 / .54959 / 4.538 |
| browsing (80) | 0.91254 / .9875 / .84179 / 2.688 | 0.74053 / .8250 / .66178 / 4.525 |
| intent_override (30) | 0.85560 / .9667 / .77643 / 4.033 | 0.71644 / .8333 / .60370 / 5.067 |
| boundary (10) | 0.89200 / 1.000 / .78667 / 3.200 | 0.80900 / .9000 / .73667 / 4.100 |

Override HR at H1 is 0.8333 (was 0.633 pre-fix), and boundary, the scenario the deflection misparse hit hardest (0.321 at H1 pre-fix), recovers to 0.809.

Attribution and caveats:

- The parse fix alone (robust parse, rerank off, hold2) lifts H1 from 0.71488 to 0.72548: the state is clean. The official-score recovery came from the perfect-saturation hoist plus slot-label dropping (the sessions lost to strict saturation were exactly the synthesized "color: X" constraints flagged in the retrieval miss analysis).
- Partial-overlap coverage is actively harmful under paraphrase even on clean state (H1 0.517 with linear IDF coverage), while hoisting only perfect candidates costs about 0.001 at H1 and re-earns the full official rerank margin. Anyone re-tuning the coverage band upward should re-check H1.
- Integrity: the hardened evaluator was run strictly as a black box during the fix; its template banks and transcript logs were not opened while the fix was written, and no hardened or official template sentence is encoded in agent code. The parser batteries and strip lists are generic conversational English.

## 6. Unseen-paraphrase holdout

To test whether the fix generalizes beyond the phrasing we had already measured against, we wrote a second evaluator, `hardened/holdout_evaluator.py`, after locking the agent implementation: every template bank (constraint paraphrase rules per card-string class, opener frames, 7 new override frames with no shared prefix, reply frames, no-preference, deflection, and nudge frames) rewritten in novel wording that shares no sentence frame with the hardened banks. Paraphrases are still derived only from the original card string, so they stay true of the target; scoring code is untouched; the RNG seed suffix is independent; strength is paraphrase-only (H1-equivalent).

| Arm | holdout TS (HR / MRR / MTTC) | retention vs own official | H1 retention (reference) |
|---|---|---|---|
| **production (robust/idf)** | **0.67218** (.750 / .59694 / 5.095) | **0.745** | 0.803 |
| v1/selective/hold2 (calibration arm) | 0.68786 (.770 / .60754 / 4.970) | 0.885 | 0.946 |
| pre-fix headline (calibration arm) | 0.36333 (.415 / .30110 / 7.725) | 0.404 | 0.499 |

The production run is stored as `results/e-fixed-holdout.json`; the two calibration rows reproduce via `hardened/run_holdout.sh` with the corresponding switches.

Reading: no memorization of the hardened phrasing. The production config's holdout retention (0.745) sits below its H1 retention (0.803), but the drop (-0.058) matches the v1 calibration arm's (-0.061), whose parser predates every robust-mode battery; the pre-fix arm drops more (-0.095). The holdout tier is simply harder than H1 in an arm-independent way, and the production config degrades with the tier, not with the phrasing. On identical text the production config retains 74.5 percent versus the pre-fix agent's 40.4 percent. Per-scenario holdout HR for the production config: buying .7625, browsing .725, intent_override .733, boundary .900; the override and boundary batteries generalize to the unseen frames.

## 7. Uncertainty audit (report-only)

An empirical uncertainty audit of the locked agent's internal candidate ordering; it changed no agent behavior. Full method and tables: `audit/AUDIT_REPORT.md` and `audit/AUDIT_TABLE.md`; figure: `audit/uncertainty_audit.svg`.

Design in brief: under a hold-all sweep (no recommendation ever ends a session), the score of a session-turn is the target's rank in the agent's full post-rerank internal ordering. Per family and per turn, a 90 percent cutoff was calibrated on the dev half of a stratified session split (`audit/split_manifest.json`) and then evaluated once on the untouched eval half (`audit/quantiles.json` was fixed before any evaluation work). Override sessions are excluded from calibration (the target changes mid-session) and reported descriptively.

Headline (nominal-90% calibrated sets, eval half, non-override n=85 per family):

| Family | In-family pooled coverage | Transfer pooled (official cutoffs) |
|---|---|---|
| Official | **93.5%** | n/a |
| H1 | 94.8% | 75.4% (empirical transfer test) |
| H2 | 94.6% | 66.0% (empirical transfer test) |
| H3 | 90.8% | 60.2% (empirical transfer test) |
| Holdout | 97.1% | 72.1% (empirical transfer test) |

- Every family's in-family coverage lands at or above the nominal 90 percent. Official cutoffs do not transfer to hardened families (60 to 75 percent pooled), which is why calibration is per-family.
- Contraction (official): calibrated cutoffs go 319, 30, 5, 4, then hold at 4 across turns. This is the disclosure mechanism made visible: by turn 3 the internal ordering has collapsed from uncertainty over a third of the pool to a 4-to-5 candidate set. Hardened families contract slower and bottom out higher (H1 median 423 to 99; H3 barely contracts, 425 to 374), an honest picture of the information tax those tiers impose.
- Tie inflation at the cutoffs is essentially zero everywhere (median 0 on every family and turn, max 7).
- Override sessions, descriptive only: pooled in-family coverage official 82.7%, H1 98.0%, H2 94.7%, H3 98.7%, holdout 95.3%.
- Three acceptance gates were set on the official family before evaluation, and all passed: pooled coverage at least 0.85 (observed 0.9353), cutoffs at most 50 for every turn from 3 on (max observed 5), median set size at most 20 for every turn from 3 on (max observed 5).

We also designed audit-driven behavioral variants and shipped neither, by measurement: uncertainty-based stopping had a measured development-split ceiling of +0.0198 TS against a +0.03 bar fixed in advance, and uncertainty-based question selection was declined on recorded development-split evidence. The production agent's behavior is exactly the six-switch configuration measured above.

## Determinism and hygiene summary

- The organizer kit was never modified: `git -C kit status --porcelain` was empty after every run.
- Every headline arm was independently rerun and compared byte-for-byte (`cmp`); all matched. The production official run reproduces `results/e-fixed-official.json` byte-identically from the six switches.
- The hardened fork at H0 is byte-identical to the official evaluator on the full public set.
- All logging paths (controller decision log, hardened transcript log) are write-only and verified byte-non-interfering.
- Every number in this document recomputes from the raw per-session rows in `results/` via `experiments/summarize.py`.
