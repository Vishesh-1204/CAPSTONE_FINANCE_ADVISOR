# Module: Stock Intelligence evaluation (research, Phase 7B)

**Files:** `backend/evaluation/stocks_rules.py` (pre-registration), `evaluate_stocks.py` (runner and report),
helpers `stocks_data.py` (the only DB reader), `stocks_sentiment_eval.py` (RQ1), `stocks_leadlag.py` (RQ2),
`stocks_direction.py` (RQ3), `stocks_sip.py` (RQ4), `stocks_robustness.py` (RQ5), guard sentence sets
`stocks_guard_cases.csv` (A) and `stocks_guard_cases_b.csv` (B), `scripts/fetch_sentiment_datasets.py`,
tests `tests/test_stocks_evaluation.py`. Generated tables: `backend/evaluation/stocks_metrics.md` / `.csv`
(committed, deterministic). Module: [stocks.md](stocks.md). Plan: [../phase7-spec.md](../phase7-spec.md) section 9.

## Why it exists

The paper needs honest, testable answers to five questions (spec section 9): does FinBERT beat a word list on
financial text (RQ1), does news sentiment lead returns (RQ2), does any model predict next-day direction better than
simple baselines out of sample (RQ3), does a historical bootstrap change a SIP projection compared with the normal
assumption (RQ4), and how robust is the system when its data source fails, and how safe is its LLM text (RQ5)?
Price direction is close to a random walk, so **negative results were expected and are reported as results**.

## Pre-registration

`evaluation/stocks_rules.py` and the 85 guard sentences of set A were committed **alone, before any evaluation
code** (commit `24103b7`). They fix the hypotheses, data windows, universe, models and their settings, metrics,
baselines, leakage controls, significance levels (Bonferroni) and the decision rule for every hypothesis. The script
only applies them. They were written after looking at DATA COVERAGE only (bar and headline counts), never at a model
output. The first full run, before any fix, is committed as `05bc8c9` (engine 7.0), so the "before" numbers are in
git history too.

| Hypothesis | Decision rule (pre-registered) |
|---|---|
| H1 | FinBERT macro-F1 > lexicon on `twitter_fin`: the 95% paired-bootstrap CI of the difference lies above 0 |
| H1-T | (exploratory) TF-IDF + LR trained in-domain vs FinBERT, same rule |
| H2 | Spearman between daily sentiment and the next 1/5-day return differs from 0 for at least one of 20 symbol-horizons at Bonferroni alpha 0.0025 (at least 30 sentiment days per symbol, else "not evaluable") |
| H3 | a model's pooled accuracy has binomial p < 0.025 against 50%, is above EVERY baseline, AND beats "always up" in an exact McNemar test (p < 0.025) |
| H3-S | a model's long/flat strategy beats buy-and-hold after 10 bps per trade in at least 6 of 10 stocks |
| H4 | bootstrap p10 < normal p10 for every block length (3, 6, 12) at 5 and 10 years, and by more than 1% at block 6 / 10 years |
| H5a | at least 99% of requests served in the failure scenarios S1-S4 |
| H5b | a 24 h cache TTL uses at most a quarter of the provider calls of 1 h, with mean staleness at most 1 trading day |

**Authorship.** Every rule, threshold and guard sentence was written by the method's author (Vishesh with Claude
Code). There is no independent labelling (decision 2026-10-23), stated as a limitation.

## Data

| Data | Source | Notes |
|---|---|---|
| Daily prices, 10 stocks + `^NSEI` | local cache (yfinance via `scripts/seed_market_cache.py`) | RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK, HINDUNILVR, ITC, SBIN, BHARTIARTL, LT (`.NS`): 2,464 bars each (`^NSEI` 2,459) from 2016-11-11 to the frozen `EVAL_END` 2026-10-30. **Hand-picked** large caps (selection and survivorship bias). No price data is committed: the report prints a SHA-256 of the (symbol, date, close) rows used (`43172c8f…`) |
| Headlines | the same cache (Google News RSS) | 25-30 per stock, published over 1 to 25 distinct days, most of them after `EVAL_END` |
| `twitter_fin` | `zeroshot/twitter-financial-news-sentiment`, revision `ccbe24de`, **MIT** | 9,543 train / 2,388 labelled holdout (the test set). Published 2022, after FinBERT, so FinBERT has not seen it: the **primary** RQ1 test |
| `phrasebank` | `takala/financial_phrasebank`, revision `8d3fe0c3`, **CC BY-NC-SA 3.0** (non-commercial, attribution Malo et al. 2014, share-alike) | `Sentences_AllAgree` (2,264), seeded stratified 80/20 split. **Contaminated:** ProsusAI/finbert was fine-tuned on Financial PhraseBank, so its score here is in-sample and never used as evidence |
| FiQA | not used | the MIT copy (`TheFinAI/fiqa-sentiment-classification`) is gated; the open copy (`pauri32/fiqa-2018`) states no licence (checked 2026-11-09) |
| Synthetic series | `stocks_data.py` | seeded random walks (placebo) and AR(1) phi = 0.10 (power control) for RQ3; synthetic bars for the RQ5 simulation |

Both datasets were reachable, so the spec's synthetic RQ1 fallback was not needed (it exists in the code, clearly
labelled, for an offline machine). The datasets live in `backend/evaluation/data/` (gitignored) and the report
records each file's SHA-256.

## Methods

- **RQ1:** majority class (M0), lexicon (L, the app's labelling rule: score above +0.15 positive, below -0.15
  negative), FinBERT (F, the same app rule; argmax as a secondary row), TF-IDF (1-2 grams) + logistic regression (T,
  trained on the dataset's own training split, fixed settings). Test sentences that also occur in the training split
  are dropped. 2,000 paired bootstrap resamples for the confidence intervals.
- **RQ2:** a headline counts for the first trading day whose 16:00 IST close comes after it (news after the close
  belongs to the next day: leakage control). Daily sentiment = the app's half-life aggregate (3 days; ablations 1 and
  7) over the last 7 calendar days, on days with a new headline. Spearman rank correlation with a 10,000-permutation
  p-value. A synthetic power analysis (1,000 replications per cell) asks how many days would be needed.
- **RQ3:** features from closes up to day t only (5 lagged returns, RSI 14, 20-day volatility, 5-day momentum,
  distance to the 50- and 200-day averages). Expanding-window walk-forward: the first 504 usable days train, refit
  every 21 days, about 1,743 test days per stock (2019-09 to 2026-10). Baselines: always up (B_up, = buy-and-hold),
  previous day (B_prev), 5-day momentum (B_mom5), SMA 50 > 200 (B_sma). Models: StandardScaler + logistic regression,
  LightGBM (fixed settings, deterministic, no tuning).
- **RQ4:** 119 month-end returns of the Nifty 50 price index (2016-11 to 2026-10). Normal: the Phase 6 `simulate_sip`
  with the SAME mean and standard deviation as the sample. Bootstrap: `block_bootstrap_sip` with blocks of 3, 6 and
  12 months. ₹10,000 a month for 5 and 10 years, 10,000 paths, seed 42, goal = the value at 10% a year.
- **RQ5:** the REAL `cache_service.get_prices` on SQLite with a scripted provider and a simulated clock: 10
  symbols, 60 weekdays, 4 requests per symbol per day (2,400 requests per scenario). Guards: G1 template on real
  facts (10 stocks x 12 month-ends), G2 claims guard on the sentence sets, G3 the configured LLM (Gemini) on
  10 stocks x 3 month-ends.

### Leakage controls (RQ3, all tested in `tests/test_stocks_evaluation.py`)

| Control | How |
|---|---|
| L1 features only from the past | a test rewrites every price after day t and checks the features at t do not change |
| L2 walk-forward only | every fold asserts that its training rows come before its test rows; nothing is shuffled |
| L3 scaler inside the fold | a new `Pipeline(StandardScaler, LogisticRegression)` per fold; a test checks the scaler's mean equals the training rows' mean |
| L4 no tuning | settings fixed in `stocks_rules.py` |
| L5 placebo | 3 synthetic random walks: every method must stay within 50% +/- 2.58 standard errors |
| L6 power | 3 synthetic AR(1) series: logistic regression must beat 50% at p < 0.025 |

## Results (RQ1-RQ4: the records of engine 7.0 and 7.1 are byte-identical, FinBERT included)

### RQ1: FinBERT beats the word list; an in-domain classifier beats both

`twitter_fin` (primary, 2,387 test tweets after 1 duplicate):

| Model | Accuracy | Macro-F1 (95% CI) | Recall neg / neu / pos |
|---|---|---|---|
| M0 majority | 0.656 | 0.264 (0.260-0.269) | 0.000 / 1.000 / 0.000 |
| L lexicon | 0.687 | 0.530 (0.506-0.555) | 0.283 / 0.881 / 0.341 |
| F FinBERT (app rule) | 0.654 | 0.621 (0.598-0.640) | 0.798 / 0.614 / 0.682 |
| F FinBERT (argmax) | 0.725 | 0.668 (0.646-0.688) | 0.760 / 0.756 / 0.598 |
| T TF-IDF + LR | **0.806** | **0.699** (0.674-0.723) | 0.422 / 0.962 / 0.573 |

- **H1 supported:** FinBERT - lexicon = **+0.091** macro-F1 (95% CI +0.059 to +0.120).
- **H1-T:** TF-IDF + LR - FinBERT = **+0.078** (+0.050 to +0.108). A cheap classifier trained on 9.5k in-domain
  tweets beats the zero-shot transformer.
- The app's ±0.15 labelling rule hurts FinBERT: it turns many neutral tweets positive or negative (neutral recall
  0.61 vs 0.76 with argmax; accuracy below the majority class). Found after seeing results, so NOT changed in the app
  (choosing a threshold on the test set would be overfitting); a future fix should pick it on the training split.
- `phrasebank` (contaminated, 451 test sentences): FinBERT 0.892 (argmax 0.952), TF-IDF + LR 0.800, lexicon 0.557.
  FinBERT's lead here is in-sample and is NOT evidence.

### RQ2: not evaluable with free headlines; the power analysis says why

No stock reached 30 sentiment days (maximum 24, HINDUNILVR; RELIANCE 0, because all 30 of its headlines are after
`EVAL_END`). **H2: not evaluable.** Power at Bonferroni alpha 0.0025:

| True correlation | n = 100 | n = 250 | n = 500 | n = 1,000 | n = 2,500 | Days for 80% power |
|---|---|---|---|---|---|---|
| 0.05 | 0.01 | 0.01 | 0.03 | 0.07 | 0.24 | more than 2,500 |
| 0.10 | 0.01 | 0.06 | 0.17 | 0.53 | 0.97 | about 2,500 (10 years of daily headlines) |
| 0.20 | 0.11 | 0.48 | 0.91 | 1.00 | 1.00 | about 500 |
| 0.30 | 0.43 | 0.93 | 1.00 | 1.00 | 1.00 | about 250 |

If the true daily effect is small (a correlation of 0.1 or less, which is plausible for public news; cite the
literature before publishing), testing RQ2 needs years of collected headlines, not a free feed's last 30. This is the
concrete case for Phase 9's scheduled daily collection.

### RQ3: nothing beats the baselines out of sample

Pooled over 10 stocks, 17,430 test days:

| Method | Accuracy | Binomial p vs 50% | McNemar p vs B_up |
|---|---|---|---|
| B_up (always up) | **0.5070** | 0.033 | - |
| B_prev | 0.4933 | 0.961 | 0.995 |
| B_mom5 | 0.5028 | 0.231 | 0.792 |
| B_sma | 0.5029 | 0.222 | 0.822 |
| LR | 0.5064 | 0.047 | 0.558 |
| LightGBM | 0.5010 | 0.395 | 0.876 |

- **H3 not supported** for either model: neither passes alpha 0.025, neither beats "always up" (LR is 0.06 points
  below it). One per-stock result passes the per-stock Bonferroni level: B_up on SBIN (0.534), i.e. drift, not skill.
- **H3-S not supported:** after 10 bps per trade, LR and LightGBM beat buy-and-hold in **0 of 10** stocks (LR makes
  about 280-680 trades over 7 years). At 0 bps LR beats it in 4 of 10 and LightGBM in 2; at 25 bps every model
  strategy loses heavily. Trading costs, not prediction, decide the outcome.
- **The harness is honest and has power:** on the random walks every method stays inside the placebo band (LR
  0.5018, band 0.482-0.518), and on the AR(1) series LR reaches 0.5179 (p 0.005) and B_prev 0.5288. A leak would have
  shown up in the placebo; a 1-2 point real edge would have been found.
- Sentiment features: not evaluable (needs 525 days of sentiment per stock; the maximum is 24).

### RQ4: the bootstrap does NOT give worse outcomes than the normal assumption

Nifty 50 monthly returns: mean 12.4% a year, volatility 16.2%, skewness **-0.98**, excess kurtosis **+5.23** (fat
tails, as expected). Yet:

| Years | Method | p10 | p50 | p90 | P(goal) | p10 vs normal |
|---|---|---|---|---|---|---|
| 5 | normal | ₹6,00,391 | ₹7,87,642 | ₹10,49,897 | 55.5% | |
| 5 | block 3 / 6 / 12 | ₹6,08,056 / ₹6,20,547 / ₹6,30,554 | | | 59.3% / 57.2% / 54.5% | +1.3% / +3.4% / +5.0% |
| 10 | normal | ₹14,12,833 | ₹21,23,392 | ₹32,61,919 | 57.2% | |
| 10 | block 3 / 6 / 12 | ₹14,29,945 / ₹14,87,214 / ₹15,16,642 | | | 59.8% / 59.5% / 58.9% | +1.2% / +5.3% / +7.3% |

- **H4 not supported, in the opposite direction:** the bootstrap p10 is HIGHER than the normal p10 in all 6 cells,
  and the gap grows with the block length; the bootstrap p90 is lower. The bootstrap range is narrower.
- **Exploratory explanation (computed after seeing the result, not pre-registered):** the history mean-reverts. The
  12-month variance ratio of monthly log returns is **0.73** (6-month 0.76, 3-month 0.90; lag-1 autocorrelation
  -0.07), and the worst month (-23.2%, March 2020) was followed by +14.7%. Blocks keep a crash together with its
  rebound; independent normal months do not, so they spread long-horizon outcomes wider. Fat tails in single months
  do not become fat tails in 5-10 year SIP outcomes in this sample. A formal variance-ratio test was not run.
- None of the 60 overlapping historical 5-year SIPs ended below any method's 5-year p10 (worst ₹6,50,771), which
  is in-sample and overlapping, so descriptive only.
- One 10-year history feeds the bootstrap AND contains one crash: the result may not hold for another decade.

## Changes after seeing results (engine 7.1, one round only)

The engine 7.0 run (`05bc8c9`) found two weaknesses in the APP. Both were fixed in one round; there is no second
round. Before changing the guard, a second sentence set (B, 40 sentences) was written and committed (`b76339c`) so
the fix could be measured on sentences its patterns were not written from.

| Change | Metric | Engine 7.0 | Engine 7.1 |
|---|---|---|---|
| Fix 1: claims guard v3 → v3.1 (patterns from v3's set-A misses + a "disclaiming frame" rule) | Recall, set A (85; NOT evidence after the fix) | 40.0% | 97.5% |
| | Hard-neutral false positives, set A | 15% | 5% |
| | **Recall, set B (40, written before the fix)** | **10%** | **20%** |
| | **Hard-neutral false positives, set B** | **40%** | **0%** |
| | Precision, set B | 33% | 100% |
| | G1 template on real facts | 100% | 100% |
| Fix 2: provider cooldown doubles per failure in a row (15 → 240 min cap; `STOCK_PROVIDER_COOLDOWN_MAX_MINUTES`) | S4 (down after day 1): failed calls per 100 requests | 58.2 | **9.5** |
| | S5 (down from the start): failed calls per 100 | 59.5 | 9.7 |
| | S2 (5-day outage): failed calls per 100 / mean staleness | 4.6 / 0.21 days | 0.9 / 0.21 days |
| | S1 (30% rate limits): served / stale share / mean staleness | 99.38% / 16.0% / 0.16 days | 99.33% / 21.4% / 0.22 days (the cost) |
| | Served rate in S1-S4 (H5a) | ≥ 99.38% | ≥ 99.33% (still supported) |
| Not changed (found after results, would need a training-split choice) | FinBERT label rule (±0.15 vs argmax) | - | - |

**What the fixes show.** Fix 2 cut wasted provider calls during long outages by about 6x (58 → 10 per 100 requests)
with no loss of service and no extra staleness after the 5-day outage; the price is a little more stale data
under frequent rate limits (S1: 16% → 21% of requests served stale, mean 0.16 → 0.22 trading days behind).
Fix 1's disclaiming-frame rule generalised (false positives on set B 40% → 0%), but the new advice and
prediction patterns barely did (recall on set B 10% → 20%): a hand-written regex fits the sentences it was written
from and little else. The narrow pre-formatted facts and the template fallback, not the regex, are what keep the
output safe; a learned classifier is the obvious next step.

## RQ5 results: the fallback chain keeps serving; a 24 h TTL saves calls, but not by 4x (engine 7.1)

| Scenario (TTL 24 h, cooldown 15 → 240 min) | Served | Stale share | Mean / max staleness (trading days) | Provider calls / failed per 100 requests |
|---|---|---|---|---|
| S0 healthy | 100.00% | 0.0% | 0.00 / 0 | 29.1 / 0.0 |
| S1 30% rate limited | 99.33% | 21.4% | 0.22 / 3 | 36.2 / 10.9 |
| S2 5-day outage | 100.00% | 7.4% | 0.21 / 5 | 27.9 / 0.9 |
| S3 20% bad responses | 99.83% | 6.0% | 0.06 / 2 | 34.4 / 6.2 |
| S4 down after day 1 | 100.00% | 97.5% | 29.22 / 60 | 10.2 / 9.5 |
| S5 down from the start | 0.00% | - | - | 9.7 / 9.7 |

- **H5a supported** (engine 7.0 and 7.1): at least 99.3% of requests are served in every failure scenario where the
  cache was filled once. The few errors are first requests that met a failure before anything was cached. S5
  (nothing ever cached) correctly serves nothing: that is when the CSV upload is needed.
- S4 shows the other side of "always serve": after 60 days of outage the data is 60 trading days old, but every
  response says so (`data_stale` and `data_as_of`).
- **H5b not supported:** TTL 24 h uses 35.0% of the calls of TTL 1 h (threshold 25%) at zero staleness. The
  threshold was mis-specified: freshness also needs a fetch after each day's 16:00 IST close, so one call per symbol
  per trading day is the floor whatever the TTL (25 calls per 100 requests at 4 requests a day). Reported as
  written, not re-defined. TTL 6 h sits between (47.8). Cooldown 0 vs 15 min under rate limits: 99.67% vs 99.33%
  served, 38.5 vs 36.2 calls per 100: the cooldown trades a few errors for politeness.
- **Guards:** G1 the template passes every guard on all 120 real fact sets (10 stocks x 12 month-ends). G2 and the
  fix: above. G3: below.

## Real-LLM sample (G3, `--g3-only`, one run on 2026-11-09)

Gemini (`gemini-3.5-flash-lite`) on 10 stocks x 3 month-ends (2026-08-31, 09-30, 10-30), public market aggregates only:

| Measure | Value |
|---|---|
| Analyses / answered | 30 / 28 (2 malformed JSON: the template was used) |
| Raw text with a prohibited claim | 0.0% by v3.1 and by v3 |
| Raw text with an unsupported number | 0.0% |
| Template fallback | 6.7% (both from malformed JSON) |
| LLM calls per analysis | 1.00 |

**Manual review of the 28 raw texts** (one rater, the author): none contains advice or a prediction, so the 0% is
not a guard miss. But **6 of 28 (21%) attach a correct number to the wrong quantity**, which no number guard can
see: five describe a 1-month change as "over the last 252 trading days", one calls the one-day VaR "relative to the
Nifty 50". Two more write "decreased by -5.2%" (a double negative) and two add a company name that is not in the
facts. The raw texts are in `evaluation/results/stocks_g3_llm_texts.json` (gitignored). Not part of the committed,
deterministic results (an LLM is not reproducible).

## How to read these numbers

- **A 50.6% hit rate is not a model.** With 17,430 days the standard error is 0.38 points, so only a lead of about
  a point over 50% is distinguishable from luck, and the right bar is "always up", not 50%: prices drifted up.
- **The placebo and power controls are what make the negative RQ3 result credible.** Without them, "nothing works"
  could mean a broken harness.
- **Guard recall on set A after v3.1 is not evidence.** The patterns were written from set A's misses. Set B is the
  honest check, and it was written by the same author, who had seen v3's misses.
- **G3's 0% is weak evidence of safety** on its own (the guard misses most new wordings); the manual review and the
  narrow, pre-formatted facts are what support it.

## Limitations (state them in the paper)

- Hand-picked universe of 10 of today's large caps (survivorship and selection bias); one market, one decade with one
  crash; the Nifty 50 price index excludes dividends (about 1-1.5% a year).
- yfinance is an unofficial source; prices are split-adjusted, not dividend-adjusted.
- No usable headline history (RQ2 and the sentiment features of RQ3 not evaluable).
- RQ1's datasets are English tweets and English press-release sentences, not Indian market news; regional languages
  are not covered.
- All rules, thresholds and guard sentences were written by the method's author; there is one manual rater for G3.
- RQ5 is a simulation with a scripted provider: real outages are not uniform, and holidays are not modelled.
- Costs are a flat 10 bps per position change: no taxes, slippage or bid-ask spread.

## Run it

```powershell
docker compose exec backend python -m scripts.seed_market_cache --symbols RELIANCE.NS TCS.NS HDFCBANK.NS INFY.NS ICICIBANK.NS HINDUNILVR.NS ITC.NS SBIN.NS BHARTIARTL.NS LT.NS
docker compose exec backend python -m scripts.fetch_sentiment_datasets
docker compose --profile ml run --rm backend-ml python -m evaluation.evaluate_stocks   # final (FinBERT), about 25 min
docker compose exec backend python -m evaluation.evaluate_stocks --quick               # development, results/ only
docker compose exec backend python -m evaluation.evaluate_stocks --g3-only             # 30 real LLM calls, results/ only
```

The committed tables are reproduced exactly as long as the cache holds the same bars (check the SHA-256 at the top of
`stocks_metrics.md`); the bars after `EVAL_END` are never read.

## Next research steps

- Collect headlines daily (Phase 9 Celery) for at least 2 years before testing RQ2 again.
- Choose the per-headline label threshold on a training split (RQ1 showed argmax beats the app rule for FinBERT).
- Replace or back up the regex claims guard with a classifier trained on labelled advice sentences, and check
  "number bound to the right quantity" (the G3 review found 21% mislabelled numbers).
- A formal variance-ratio test and a second decade (or other indices) for RQ4.
