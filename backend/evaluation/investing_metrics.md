# Investment Advisor Agent: evaluation results

Synthetic personas: 500 (seed 42, each attribute drawn independently). Engine 6.1. Monte Carlo: **2,000 paths** per projection, seeded per persona, common random numbers across methods. LLM mode: none. Suitability rules R1-R7 were committed before the methods were written (see evaluation/investing_rules.py and its git history).

Return assumptions are the ILLUSTRATIVE PLACEHOLDERS from settings: equity 11.0% (vol 17.0%), debt 6.0% (vol 4.0%), gold 7.0% (vol 14.0%), cash 3.5% (vol 0.5%). They must be replaced with cited figures before publication.

Naive SIP (B0, B1, B2) = the whole mean monthly surplus, no emergency reservation, no goal cap. P's SIP = emergency top-up first, then 80% of the LOWER-band surplus, capped at what the goal needs.

## 1. Main comparison (all personas, base assumptions)

| Method | Allocation rules broken (R1, R2, R5-R7) | SIP rules broken (R3a, R4) | R3b strict | Shortfall risk (p10 < invested) | Mean goal probability | Investing | Mean SIP | Mean equity |
|---|---|---|---|---|---|---|---|---|
| B0 fixed 60/40 | 37.4% | 100.0% | 100.0% | 12.4% | 97.9% | 100.0% | ₹36,050 | 60.0% |
| B1 100 minus age | 36.6% | 100.0% | 100.0% | 12.2% | 97.9% | 100.0% | ₹36,050 | 59.2% |
| B2 questionnaire only | 26.8% | 100.0% | 100.0% | 10.2% | 98.0% | 100.0% | ₹36,050 | 51.5% |
| **P proposed** | 0.0% | 4.4% | 51.2% | 6.1% | 16.6% | 74.8% | ₹11,155 | 40.1% |
| B2 allocation + P's SIP | 26.8% | 4.4% | 51.2% | 10.7% | 17.6% | 74.8% | ₹10,958 | 51.5% |
| P allocation + naive SIP | 0.0% | 100.0% | 100.0% | 5.6% | 98.2% | 100.0% | ₹36,050 | 40.1% |

Shortfall risk is over personas with a SIP > 0. Goal probability counts a persona with no SIP as 0%.

**Goal gap (P).** At the expected return a goal needs on average ₹18,093 a month, which reaches it in 46.1% of paths. P's SIP is on average ₹6,938 short of that (median ₹3,123); 28.0% of personas already invest enough.

## 2. Violation rate per rule

`enforced` rules are ones P is BUILT to satisfy, so P scoring 0% there only checks the implementation. `trade_off` (R3a) is built in too, but not at the cost of R4: when even the whole low-band surplus cannot fill the emergency fund in 12 months, P keeps R4 (it never asks for more than the month leaves). R3a was relabelled from `enforced` AFTER seeing the engine 6.1 results; its cells split the violations into avoidable and infeasible given the surplus. `independent` rules test judgement P was not designed to satisfy (they are still written by the method's author) and carry more weight. `secondary` (R3b) is a stricter reading P fails by design: P invests what is left after the emergency top-up. `fixed_after_evaluation` (R5) found a weakness that engine 6.1 fixed, so its score is no longer evidence. A rule that applies to fewer than 10 personas is not evaluated.

| Rule | Kind | Design | Applies (n) | B0 fixed 60/40 | B1 100 minus age | B2 questionnaire only | **P proposed** | B2 allocation + P's SIP | P allocation + naive SIP |
|---|---|---|---|---|---|---|---|---|---|
| R1 | allocation | enforced | 16 | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 0.0% |
| R2 | allocation | enforced | 46 | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 0.0% |
| R3a | sip | trade_off | 121 | 100.0% (99 avoidable, 22 infeasible) | 100.0% (99 avoidable, 22 infeasible) | 100.0% (99 avoidable, 22 infeasible) | 18.2% (0 avoidable, 22 infeasible) | 18.2% (0 avoidable, 22 infeasible) | 100.0% (99 avoidable, 22 infeasible) |
| R3b | sip | secondary | 121 | 100.0% | 100.0% | 100.0% | 51.2% | 51.2% | 100.0% |
| R4 | sip | enforced | 500 | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 100.0% |
| R5 | allocation | fixed_after_evaluation | 142 | 100.0% | 96.5% | 67.6% | 0.0% | 67.6% | 0.0% |
| R6 | allocation | independent | 29 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| R7 | allocation | independent | 1 | not evaluated (n=1) | not evaluated (n=1) | not evaluated (n=1) | not evaluated (n=1) | not evaluated (n=1) | not evaluated (n=1) |

- **R1** Horizon under 1 year: no equity.
- **R2** Horizon under 3 years: equity at most 20%.
- **R3a** Under 3 months of emergency fund: the monthly top-up must reach 3 months within 12 months (reserved before any SIP).
- **R3b** Strict reading: under 3 months of emergency fund, no SIP at all.
- **R4** SIP plus emergency top-up never exceeds the lower-band monthly surplus (no SIP when it is <= 0).
- **R5** Irregular income (stability <= 2) and 2 or more dependents: equity at most 40%.
- **R6** Every questionnaire answer <= 2 (a cautious person): equity at most 30%.
- **R7** Not over-cautious: horizon >= 10 years, emergency fund >= 6 months, stability >= 4, dependents <= 1 and every answer >= 4: equity at least 50%.

R3a counts are personas. **Infeasible** = even the whole low-band surplus x 12 cannot fill the emergency fund to 3 months, so meeting R3a would break R4; **avoidable** = the surplus was enough. Computed from the same per-persona results (no re-run).

## 3. By segment

| Segment | Method | Allocation rules broken (R1, R2, R5-R7) | SIP rules broken (R3a, R4) | R3b strict | Shortfall risk (p10 < invested) | Mean goal probability | Investing | Mean SIP | Mean equity |
|---|---|---|---|---|---|---|---|---|---|
| weak_emergency_fund (n=121) | B0 fixed 60/40 | 47.1% | 100.0% | 100.0% | 7.4% | 97.3% | 100.0% | ₹36,657 | 60.0% |
| weak_emergency_fund (n=121) | B2 questionnaire only | 34.7% | 100.0% | 100.0% | 8.3% | 97.3% | 100.0% | ₹36,657 | 52.4% |
| weak_emergency_fund (n=121) | **P proposed** | 0.0% | 18.2% | 51.2% | 4.8% | 5.0% | 51.2% | ₹7,235 | 35.7% |
| short_horizon (n=46) | B0 fixed 60/40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | ₹35,622 | 60.0% |
| short_horizon (n=46) | B2 questionnaire only | 100.0% | 100.0% | 100.0% | 89.1% | 100.0% | 100.0% | ₹35,622 | 46.5% |
| short_horizon (n=46) | **P proposed** | 0.0% | 0.0% | 62.5% | 62.2% | 16.9% | 80.4% | ₹13,055 | 13.0% |
| cautious (n=29) | B0 fixed 60/40 | 100.0% | 100.0% | 100.0% | 27.6% | 98.7% | 100.0% | ₹38,110 | 60.0% |
| cautious (n=29) | B2 questionnaire only | 24.1% | 100.0% | 100.0% | 13.8% | 99.0% | 100.0% | ₹38,110 | 25.0% |
| cautious (n=29) | **P proposed** | 0.0% | 6.9% | 42.9% | 9.1% | 21.5% | 75.9% | ₹13,730 | 23.1% |
| fragile_household (n=142) | B0 fixed 60/40 | 100.0% | 100.0% | 100.0% | 11.3% | 98.0% | 100.0% | ₹36,168 | 60.0% |
| fragile_household (n=142) | B2 questionnaire only | 71.8% | 100.0% | 100.0% | 11.3% | 98.2% | 100.0% | ₹36,168 | 50.9% |
| fragile_household (n=142) | **P proposed** | 0.0% | 4.9% | 50.0% | 7.5% | 16.6% | 74.6% | ₹12,411 | 23.4% |
| strong (n=1) | B0 fixed 60/40 | 0.0% | 100.0% | n/a | 0.0% | 99.9% | 100.0% | ₹10,800 | 60.0% |
| strong (n=1) | B2 questionnaire only | 0.0% | 100.0% | n/a | 0.0% | 99.8% | 100.0% | ₹10,800 | 75.0% |
| strong (n=1) | **P proposed** | 0.0% | 0.0% | n/a | 0.0% | 40.7% | 100.0% | ₹3,467 | 75.0% |

## 4. Ablations of P (all personas)

| P variant | Allocation rules broken (R1, R2, R5-R7) | SIP rules broken (R3a, R4) | R3b strict | Shortfall risk (p10 < invested) | Mean goal probability | Investing | Mean SIP | Mean equity |
|---|---|---|---|---|---|---|---|---|
| P (shipped defaults) | 0.0% | 4.4% | 51.2% | 6.1% | 16.6% | 74.8% | ₹11,155 | 40.1% |
| no_capacity | 16.8% | 4.4% | 51.2% | 5.3% | 17.4% | 74.8% | ₹10,974 | 47.4% |
| no_horizon_glide | 9.2% | 4.4% | 51.2% | 9.9% | 16.7% | 74.8% | ₹11,147 | 43.0% |
| no_emergency_first | 0.0% | 24.2% | 88.4% | 5.5% | 23.4% | 87.4% | ₹14,068 | 40.1% |
| safety=1.0 | 0.0% | 4.4% | 58.7% | 5.9% | 22.9% | 78.0% | ₹12,921 | 40.1% |
| safety=0.6 | 0.0% | 4.4% | 39.7% | 6.5% | 9.2% | 71.0% | ₹8,624 | 40.1% |
| mean_surplus | 0.0% | 53.6% | 77.7% | 5.9% | 34.7% | 94.4% | ₹16,080 | 40.1% |
| goal_p75 | 0.0% | 4.4% | 51.2% | 6.1% | 24.0% | 74.8% | ₹11,717 | 40.1% |

`goal_p75` sizes the goal's SIP at a heuristic 25th-percentile return instead of the mean; the goal probability column shows what it really achieves.

## 5. Sensitivity: plans built under base assumptions, outcomes under other returns

| Method | Returns | Mean goal probability | Shortfall risk |
|---|---|---|---|
| **P proposed** | low | 7.6% | 13.1% |
| **P proposed** | base | 16.6% | 6.1% |
| **P proposed** | high | 28.4% | 2.1% |
| B0 fixed 60/40 | low | 93.3% | 21.2% |
| B0 fixed 60/40 | base | 97.9% | 12.4% |
| B0 fixed 60/40 | high | 99.6% | 9.2% |

Shifts from the base returns (low: equity -3.0 pp, debt -1.0 pp, gold -2.0 pp, cash -0.5 pp; high: equity +3.0 pp, debt +1.0 pp, gold +2.0 pp, cash +0.5 pp); volatilities unchanged.

## 5b. Sensitivity to the persona spending variability

The primary run draws spending variability (sd / spending) from the base range; it was NOT changed after seeing results. The same personas redrawn with a low and a high range:

| Method | Variability | SIP rules broken | R4 | Investing | Mean SIP | Mean goal probability | Shortfall risk |
|---|---|---|---|---|---|---|---|
| B0 fixed 60/40 | low (2-12%) | 100.0% | 100.0% | 100.0% | ₹36,050 | 97.9% | 12.4% |
| B0 fixed 60/40 | base (5-30%) | 100.0% | 100.0% | 100.0% | ₹36,050 | 97.9% | 12.4% |
| B0 fixed 60/40 | high (10-50%) | 100.0% | 100.0% | 100.0% | ₹36,050 | 97.9% | 12.4% |
| B2 questionnaire only | low (2-12%) | 100.0% | 100.0% | 100.0% | ₹36,050 | 98.0% | 10.2% |
| B2 questionnaire only | base (5-30%) | 100.0% | 100.0% | 100.0% | ₹36,050 | 98.0% | 10.2% |
| B2 questionnaire only | high (10-50%) | 100.0% | 100.0% | 100.0% | ₹36,050 | 98.0% | 10.2% |
| **P proposed** | low (2-12%) | 1.2% | 0.0% | 89.2% | ₹14,521 | 27.4% | 6.1% |
| **P proposed** | base (5-30%) | 4.4% | 0.0% | 74.8% | ₹11,155 | 16.6% | 6.1% |
| **P proposed** | high (10-50%) | 8.8% | 0.0% | 57.2% | ₹7,533 | 8.2% | 7.3% |

Reference: the Phase 5 panel's month-to-month spending variability (sd / mean of 24 monthly expense totals) has a median of 15.6% over 30 users (range 9.7%-24.8%; freelancer 17.0%, high_spender 13.3%, saver 20.4%, steady 15.4%). The panel is synthetic too, so it is a reference point, not ground truth, and it mixes seasonality and drift into the variability.

## 6. SIP affordability on the Phase 5 panel (replay through the real forecast)

30 synthetic users, 28,525 rows, SHA-256 `801ee5f4158e1688…`; 15 origins (2025-04-15 … 2026-06-15), 450 real 30-day forecasts. A SIP = max(0, surplus band x factor) FAILS when it is larger than the ACTUAL surplus before investing in the next 30 days. No emergency fund or goal here (the panel has no savings data). Independent of the Monte Carlo paths.

| SIP rule | Failure rate (all) | freelancer | high_spender | saver | steady | Mean SIP | Mean excess when failing |
|---|---|---|---|---|---|---|---|
| P: low band x 0.8 | 28.4% | 46.7% | 48.3% | 8.6% | 10.0% | ₹15,984 | ₹21,885 |
| naive: mean x 1.0 | 47.3% | 58.1% | 57.5% | 37.1% | 36.7% | ₹27,047 | ₹20,099 |
| mean x 0.8 | 34.7% | 55.2% | 56.7% | 11.4% | 15.0% | ₹21,637 | ₹21,852 |
| low band x 1.0 | 33.3% | 48.6% | 48.3% | 19.0% | 17.5% | ₹19,980 | ₹21,812 |
| low band x 0.6 | 25.3% | 44.8% | 47.5% | 4.8% | 4.2% | ₹11,988 | ₹21,344 |

## 7. Claims guard on hand-labelled sentences

Claims guard **v2**. 60 synthetic sentences in evaluation/investing_guard_cases.csv, all written by the same author as the method and the regex (no independent labelling). The v2 phrases were added AFTER seeing v1's misses on these same sentences, so the numbers below are NOT evidence of recall (the v1 numbers are logged in docs/modules/investing-evaluation.md). The real-LLM run shows how often real model text hits the guard.

| Metric | Value | n |
|---|---|---|
| precision | 74.4% | 39 |
| recall | 96.7% | 30 |
| false positive rate | 33.3% | 30 |
| flagged: safe:balanced | 0.0% | 14 |
| flagged: safe:factual | 50.0% | 2 |
| flagged: safe:hedged | 0.0% | 5 |
| flagged: safe:negation | 100.0% | 9 |
| flagged: unsafe:absolute | 100.0% | 10 |
| flagged: unsafe:promise | 95.0% | 20 |

Unsafe sentences the guard MISSED (1):

- U25: The middle outcome is what you will get.

Safe sentences the guard FLAGGED (10):

- S16 (negation): Returns are not guaranteed.
- S17 (negation): No investment is risk-free.
- S18 (negation): There is no guarantee that equity will beat inflation.
- S19 (negation): The plan is not a guarantee of results.
- S20 (negation): Even a debt fund is not risk free, because interest rates change.
- S21 (negation): This plan does not offer assured returns.
- S22 (negation): Please do not treat any outcome as guaranteed.
- S23 (negation): There is no such thing as zero risk in investing.
- S24 (negation): Nobody can promise that you can't lose money.
- S25 (factual): Bank deposits are guaranteed by deposit insurance only up to a limit.

## 8. Real pipeline sample

30 personas through the REAL generate_plan (SQLite, budget and forecast rows stubbed from the persona). `matches evaluation p` = same allocation and SIP as P in this script.

| Metric | Value | n |
|---|---|---|
| matches evaluation p | 100.0% | 30 |
| template passes guards | 100.0% | 30 |
| llm answered rate | 0.0% | 30 |
| llm calls per plan | 0.00 | 30 |

## 9. Limitations

- Synthetic personas; every attribute is drawn independently, so unrealistic combinations occur (for example age 60 with a 25-year horizon).
- There is no ground truth for a suitable allocation: the rules are the team's pre-registered judgement. R1, R2 and R4 are enforced by P's design (R4 only since engine 6.1: a regression test now). R3a is a trade-off with R4, relabelled after seeing the engine 6.1 results: capping the top-up at the surplus means P breaks R3a whenever the surplus cannot fill the emergency fund in time (the per-rule table splits these violations into avoidable and infeasible). R5 was fixed after the evaluation found it; the remaining independent rules (R6, R7) and the panel replay carry the weight.
- All rules and guard sentences were written by the same author as the method (no independent labelling).
- One round of fixes after seeing results (engine 6.1), logged with before/after numbers in docs/modules/investing-evaluation.md. No further tuning against these rules.
- Normal monthly returns understate fat tails; the return assumptions are placeholders; no tax, fees or inflation.
- The low surplus of a persona is modelled (mean - 1.28 sd); the panel replay uses the real forecast instead, whose interval coverage is 61-65% against 80% nominal.
- The claims guard flags negations on purpose (fail safe), so its false-positive rate is high by design.
