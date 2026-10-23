# Module: Investment evaluation (research, Phase 6B)

**Files:** `backend/evaluation/investing_rules.py` (pre-registered rules), `investing_personas.py`,
`investing_methods.py`, `evaluate_investing.py`, `investing_guard_cases.csv`, results
`backend/evaluation/investing_metrics.csv` + `investing_metrics.md` (committed, 2,000 Monte Carlo paths),
tests `backend/tests/test_investing_evaluation.py`.

## Why it exists

The paper's Phase 6 claim is *budget-aware, capacity-gated, explainable investment planning with a compliance guard
on LLM text*. There is **no ground truth** for a "suitable" allocation, so this harness does not score methods
against labels. It scores them against **suitability rules written down before any method was evaluated**, and
against a replay of real (synthetic) cash flows from Phase 5. It is built to show trade-offs honestly, including
where P loses.

## Pre-registration

`investing_rules.py` was committed **on its own** (commit `d869e27`) before `investing_methods.py` existed; the git
history is the evidence. Rules read only raw persona facts and what a method recommends, never P's own scores.

| Rule | Kind | Design | Rule |
|---|---|---|---|
| R1 | allocation | enforced | horizon < 1 year: no equity |
| R2 | allocation | enforced | horizon < 3 years: equity ≤ 20% |
| R3a | SIP | enforced | emergency fund < 3 months: the monthly top-up reaches 3 months within 12 months |
| R3b | SIP | secondary | strict reading: emergency fund < 3 months, no SIP at all (P fails this by design) |
| R4 | SIP | enforced | SIP + top-up ≤ the lower-band monthly surplus |
| R5 | allocation | independent | income stability ≤ 2 and ≥ 2 dependents: equity ≤ 40% |
| R6 | allocation | independent | every answer ≤ 2: equity ≤ 30% |
| R7 | allocation | independent | long horizon, ≥ 6 months fund, stable, ≤ 1 dependent, every answer ≥ 4: equity ≥ 50% (not over-cautious) |

The table shows the labels **as pre-registered**. Two labels changed AFTER seeing results (the conditions did
not): R5 `independent` → `fixed_after_evaluation`, and R3a `enforced` → `trade_off`. See
[Changes after seeing results](#changes-after-seeing-results-engine-61-one-round-only).

**How to weigh them.** `enforced` rules (R1, R2, R4) are what P is *built* to satisfy, so 0% there only checks the
code. `trade_off` (R3a) is built in too, but not at the cost of R4: when the surplus cannot fill the emergency fund
in 12 months, P keeps R4. `independent` rules test judgement P was not tuned for and carry more weight, together
with the panel replay. R3b is reported to show the trade-off P makes (it invests what is left after the emergency
top-up). All rules were written by the method's author: no teammate-written rules or independent labelling
(decision 2026-10-23), stated as a limitation.

## Data

- **500 synthetic personas** (`investing_personas.py`, seed 42, `random.Random(seed × 1000 + i)` per persona):
  age 22-60, income ₹20k-₹3L (log-uniform), essentials 30-60% and discretionary 5-35% of income, spending
  variability 5-30%, emergency fund 0-12 months, stability 1-5, dependents 0-4, horizon 1-25 years (5% at 6 months
  so R1 is exercised), answers 1-5, an existing SIP, and a goal sized from 20-80% of the mean surplus at 8%.
  **Each attribute is drawn independently**, so unrealistic combinations occur (age 60 with a 25-year horizon).
- Low surplus = mean surplus - 1.2816 × spending sd (the forecast's lower band uses the 90th-percentile spending).
- **The Phase 5 panel** (30 users, 4 personas, 24 months, SHA-256 `801ee5f4158e1688…`) for the affordability replay.
- **60 hand-labelled sentences** for the claims guard (30 unsafe promises, 30 safe, including 9 negations).
  All 60 were written by the same side that wrote the regex, so recall is optimistic; ≥ 20 teammate-written
  sentences are still to come (the `author` column records it).

## Methods

A method = an **allocation rule** + a **SIP rule**, so the two halves can be separated.

| Method | Allocation | SIP |
|---|---|---|
| B0 | fixed 60/40 equity/debt | naive: the whole mean surplus, nothing reserved |
| B1 | (100 - age)% equity, rest debt | naive |
| B2 | questionnaire only (tolerance category, no capacity, no horizon cap) | naive |
| **P** | the shipped pipeline (tolerance vs capacity, horizon glide) | emergency top-up first, low band × 0.8, goal cap |
| B2 allocation + P's SIP | B2 | P |
| P allocation + naive SIP | P | naive |

P here calls the same pure functions in the same order as `investment_service`; the pipeline check (below) runs the
real `generate_plan` on 30 personas and confirms identical allocations and SIPs.

**Ablations of P:** no capacity score, no horizon glide, no emergency-first reservation, safety factor 1.0 / 0.6,
mean surplus instead of the lower band, goal sized at a heuristic 25th-percentile return (`goal_p75`).
**Sensitivity:** plans are built under the base assumptions; outcomes are simulated under returns 3 / 1 / 2 / 0.5 pp
lower or higher (equity / debt / gold / cash): "what if the assumptions are wrong?".

## Metrics

1. **Rule violation rate**, per rule (over the personas it applies to) and "any allocation rule" / "any primary SIP
   rule (R3a, R4)" over all personas.
2. **Shortfall risk:** share of personas with a SIP whose p10 corpus is below the money they put in.
3. **Mean goal probability** at the recommended SIP (no SIP counts as 0%).
4. **SIP affordability failure** on the Phase 5 panel: at each of 15 origins the REAL 30-day forecast is generated;
   a SIP from each surplus rule fails when it exceeds the ACTUAL surplus before investing in the next 30 days.
5. **Explanation quality:** the template passes its own guards on every sampled plan; claims-guard precision,
   recall and false-positive rate on the labelled sentences; with `--llm real`, the raw faithful and compliant
   rates of real LLM text, the fallback rate and calls per plan.

Monte Carlo: seeded per persona with **common random numbers** (every method sees the same return paths for a
persona), so method differences are not sampling noise. Development runs used 500 paths; the committed tables use
**2,000 paths**. Metrics 1, 4 and 5 and the SIP sizes do not depend on the path count.

## Changes after seeing results (engine 6.1, one round only)

The first run (engine 6.0, tables in the next section) found two weaknesses. Both were fixed in ONE round
(spec section 14.8); there is no second round of tuning against these rules. Current numbers: 500 personas,
seed 42, 2,000 paths; full tables in `backend/evaluation/investing_metrics.md`.

| Change | Metric (P proposed) | Engine 6.0 | Engine 6.1 |
|---|---|---|---|
| Fix 1: cap the emergency top-up at the low-band surplus | R4 (SIP + top-up ≤ low surplus) | 15.6% | **0.0%** (by design: a regression test, not evidence) |
| | R3a (fund reaches 3 months within 12 months) | 0.0% | **18.2%** (22 of 121; the trade-off below) |
| Fix 2: fragile household caps capacity (`CAPACITY_CAP_FRAGILE_HOUSEHOLD`) | R5 (irregular income + ≥ 2 dependents: equity ≤ 40%) | 58.5% | **0.0%** (relabelled `fixed_after_evaluation`) |
| Both | Allocation rules broken (R1, R2, R5-R7) | 16.6% | 0.0% |
| | SIP rules broken (R3a, R4) | 15.6% | 4.4% |
| | Mean equity | 45.5% | 40.1% |
| | Shortfall risk / mean goal probability | 5.9% / 17.3% | 6.1% / 16.6% |
| | Mean SIP | ₹11,004 | ₹11,155 |
| Claims guard v2 (surely, locked in, fully protected, …) | Recall / precision on the 60 sentences | 66.7% / 66.7% (v1) | 96.7% / 74.4% (v2; NOT evidence: the phrases were added after seeing v1's misses on the same sentences) |

Reporting only (no method change): R7 is "not evaluated (n=1)" in the main run; the goal gap; the spending
variability sensitivity (primary 5-30% range unchanged).

**R3a: the direct cost of Fix 1.** P went from 0.0% to 18.2% on R3a: 22 of the 121 personas with under 3 months of
emergency fund. All 22 have SIP = 0 (the whole low-band surplus already goes to the fund) and none break R4. Their
surplus × 12 cannot fill the fund to 3 months, so **no plan can satisfy both R3a and R4 for them**: meeting R3a would
ask for more money than the month leaves. Engine 6.1 keeps R4 and warns `emergency_fill_slow` instead.

- **Re-label, made after seeing these results:** R3a `enforced` → `trade_off`. "Enforced" claimed P always meets
  it, which engine 6.1 no longer does on purpose. The rule's condition and every number are unchanged.
- **Split of the violations** (computed from the same per-persona results, no re-run; the personas are rebuilt
  from seed 42 and checked against the saved rows). *Infeasible* = even the whole low-band surplus × 12 cannot
  fill the fund to 3 months; *avoidable* = the surplus was enough:

| Method | R3a broken | Avoidable | Infeasible given the surplus |
|---|---|---|---|
| B0 / B1 / B2 (naive SIP) | 100.0% | 99 | 22 |
| P allocation + naive SIP | 100.0% | 99 | 22 |
| **P proposed** | **18.2%** | **0** | **22** |
| B2 allocation + P's SIP | 18.2% | 0 | 22 |

  The same 22 people are infeasible for every method; the baselines' other 99 violations are avoidable.

## Results before the fixes (engine 6.0; offline, seed 42, 2,000 paths)

These are the numbers that motivated the one round of fixes above. They are kept as the "before" record; the
current (engine 6.1) results are in `backend/evaluation/investing_metrics.md` and the table above.
Return assumptions are the illustrative placeholders.

### Main comparison (500 personas)

| Method | Allocation rules broken | SIP rules broken (R3a, R4) | R3b strict | Shortfall risk | Mean goal probability | Mean SIP | Mean equity |
|---|---|---|---|---|---|---|---|
| B0 fixed 60/40 | 37.4% | 100.0% | 100.0% | 12.4% | 97.9% | ₹36,050 | 60.0% |
| B1 100 minus age | 36.6% | 100.0% | 100.0% | 12.2% | 97.9% | ₹36,050 | 59.2% |
| B2 questionnaire only | 26.8% | 100.0% | 100.0% | 10.2% | 98.0% | ₹36,050 | 51.5% |
| **P proposed** | **16.6%** | **15.6%** | 51.2% | **5.9%** | 17.3% | ₹11,004 | 45.5% |
| B2 allocation + P's SIP | 26.8% | 15.6% | 51.2% | 10.7% | 17.6% | ₹10,958 | 51.5% |
| P allocation + naive SIP | 16.6% | 100.0% | 100.0% | 5.4% | 98.1% | ₹36,050 | 45.5% |

Per rule (violation rate over the personas the rule applies to; path-independent):

| Rule | Design | n | B0 | B1 | B2 | P |
|---|---|---|---|---|---|---|
| R1 | enforced | 16 | 100% | 100% | 100% | 0% |
| R2 | enforced | 46 | 100% | 100% | 100% | 0% |
| R3a | enforced | 121 | 100% | 100% | 100% | 0% |
| R3b | secondary | 121 | 100% | 100% | 100% | 51.2% |
| R4 | enforced | 500 | 100% | 100% | 100% | 15.6% |
| **R5** | **independent** | 142 | 100% | 96.5% | 67.6% | **58.5%** |
| **R6** | **independent** | 29 | 100% | 100% | 0% | **0%** |
| **R7** | **independent** | **1** | 0% | 100% | 0% | 0% |

### SIP affordability on the Phase 5 panel (450 real 30-day forecasts)

| SIP rule | Failure rate | freelancer | high_spender | saver | steady | Mean SIP |
|---|---|---|---|---|---|---|
| **P: low band × 0.8** | **28.4%** | 46.7% | 48.3% | 8.6% | 10.0% | ₹15,984 |
| naive: mean × 1.0 | 47.3% | 58.1% | 57.5% | 37.1% | 36.7% | ₹27,047 |
| mean × 0.8 | 34.7% | 55.2% | 56.7% | 11.4% | 15.0% | ₹21,637 |
| low band × 1.0 | 33.3% | 48.6% | 48.3% | 19.0% | 17.5% | ₹19,980 |
| low band × 0.6 | 25.3% | 44.8% | 47.5% | 4.8% | 4.2% | ₹11,988 |

### Ablations and sensitivity (headlines)

- **No horizon glide:** allocation violations 16.6% → 26.4%, shortfall risk 5.9% → 10.4%. The glide is the part of
  P's allocation that matters most.
- **No capacity score:** almost no change (16.8% violations, equity 45.5% → 47.4%). On these personas the
  questionnaire is usually the binding side; capacity rarely lowers the category further.
- **No emergency-first:** SIP violations 15.6% → 24.2%, R3b 51.2% → 88.4%; goal probability 17.3% → 24.2%.
- **Mean surplus instead of the lower band:** SIP violations 15.6% → 52.4%, goal probability → 34.9%.
- **Safety factor** 0.6 / 0.8 / 1.0: goal probability 10.1% / 17.3% / 23.4%; R4 is unchanged (15.6%) because its
  P violations come from the top-up, not the SIP.
- **goal_p75:** goal probability 17.3% → 25.2% for ₹695 more a month on average; capacity, not the goal cap, binds
  for most personas.
- **Sensitivity** (plans from base returns): P's goal probability 8.2% / 17.3% / 29.1% and shortfall 13.9% / 5.9% /
  1.9% under low / base / high returns; B0's shortfall 21.2% / 12.4% / 9.2%.

### Explanations

- Real pipeline sample: P in the harness = the real `generate_plan` on **30 / 30** personas; the template passes
  every guard on 30 / 30. Real-LLM rates: not measured yet (`--llm real`, approved, pending).
- Claims guard on 60 sentences (none independent): precision 66.7%, recall 66.7%, false-positive rate 33.3%.
  All 9 negations are flagged (by design) plus one factual sentence about deposit insurance; the 10 misses are
  promises without the trigger words ("surely", "locked in", "fully protected", "cannot fail", "no downside").

## How to read these numbers

Written for the engine 6.0 run above. After the one round of fixes, P's R4 and R5 are 0%, R3a is 18.2% (all
infeasible given the surplus) and allocation violations are 0%; the trade-offs below still hold.

- **The core trade-off.** The baselines put the whole mean surplus into a SIP, so they reach goals (~98%) but break
  the affordability rule for every persona and, on real cash flows, ask for more than the month leaves 47% of the
  time. P invests about a third as much (₹11k vs ₹36k), breaks R4 for 15.6%, and fails the panel replay 28.4% of the
  time, at the price of a much lower goal probability (17.3%). Neither extreme is "right"; the paper should present
  the frontier (safety factor and band ablations trace it) rather than a winner.
- **Both halves matter, separately.** The mixed variants show P's allocation alone cuts allocation violations from
  26.8% to 16.6% (B2 allocation + P's SIP vs P), and P's SIP rule alone cuts SIP violations from 100% to 15.6%
  (P allocation + naive SIP vs P). Neither result depends on the other.
- **Weight the independent rules.** R6 (cautious people) P passes, like B2. **R5 P fails 58.5%**: its capacity
  score averages stability and dependents with the emergency fund and horizon, so a long horizon offsets an
  irregular income with dependents. R7 is effectively untested (n = 1).
- **Goal probability is low by construction**: the goal's SIP is sized at the expected return (≈50% chance), and
  for most personas the affordable SIP is below even that.

## Findings from the engine 6.0 run

1. **Cap the emergency top-up at the available surplus.** All of P's R4 violations came from reserving a top-up
   when the low surplus was too small (or ≤ 0). **Done in engine 6.1** (R4 0%; cost: R3a 18.2%, see above).
2. **R5 weakness.** A hard rule in the capacity score (irregular income + dependents caps the category).
   **Done in engine 6.1** (R5 0%, relabelled `fixed_after_evaluation`).
3. **Irregular earners.** Freelancer and high-spender SIPs fail the replay 47-48% of the time at P's low band × 0.8
   and still 45-48% at low band × 0.6:
   the forecast treats income as known (a Phase 5 gap), so no safety factor on spending fixes it.
4. **Claims guard recall.** Add the missed phrasings, or keep the regex as a cheap first filter and measure recall on
   independent sentences before claiming anything.

## Limitations (state them in the paper)

- Synthetic personas with independently drawn attributes (unrealistic combinations; R7 almost never applies).
  The spending-variability range (5-30%) is a modelling choice that drives the gap between low and mean surplus
  (median low/mean = 0.61; 12.6% of personas have a low surplus ≤ 0).
- No ground truth: the rules are the team's pre-registered judgement; three of eight (R1, R2, R4) are enforced
  by P's design, and R3a is a trade-off with R4 (relabelled after seeing the engine 6.1 results). All rules were
  written by the method's author (no independent labelling).
- Normal monthly returns understate fat tails; return assumptions are placeholders; no tax, fees or inflation.
- The panel replay has no savings data, so it tests surplus sizing only (no emergency fund, no goal).
- The 60 guard sentences were written by the same side as the regex; recall is optimistic.
- `goal_p75` uses a heuristic return adjustment; its achieved probability is measured, not assumed.

## Run it

```powershell
docker compose exec backend python -m evaluation.evaluate_investing                 # final, 2,000 paths (~20 min)
docker compose exec backend python -m evaluation.evaluate_investing --paths 500     # development (~5 min)
docker compose exec backend python -m evaluation.evaluate_investing --skip-panel    # without the panel replay
docker compose exec backend python -m evaluation.evaluate_investing --llm real      # synthetic data only, 30 LLM calls
```

Only the default run (2,000 paths, 500 personas, seed 42, no LLM, with the panel) overwrites the committed
`investing_metrics.csv` / `.md`; every other run writes to `evaluation/results/` (gitignored), as does
`investing_persona_rows_<label>.csv` (one row per persona × method × config × assumption, for significance tests).

## Next research steps

- Teammate-written rules and ≥ 20 independent guard sentences, each in its own marked commit, then re-run.
- `--llm real` on the 30-persona sample: raw faithful / compliant rates, fallback rate, calls per plan.
- Paired significance tests on `investing_persona_rows_*.csv` (same personas, common random numbers).
- Correlated persona sampling (or a targeted subset) so R7 and the "strong" segment are tested.
- Replace the placeholder returns with cited figures and re-run the sensitivity table; Phase 7 index history
  enables a historical-bootstrap Monte Carlo (normal vs bootstrap).
