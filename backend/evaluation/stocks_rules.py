# backend/evaluation/stocks_rules.py
#
# Purpose: PRE-REGISTRATION for the Stock Intelligence evaluation (Phase 7B, research questions
# RQ1-RQ5 in docs/phase7-spec.md section 9). It fixes the hypotheses, the data, the metrics, the
# baselines, the model settings, the leakage controls and the decision rules BEFORE any evaluation
# or model code exists. This file (and evaluation/stocks_guard_cases.csv) were committed on their
# own, before evaluation/evaluate_stocks.py and its helpers; the git history is the proof.
#
# What may change after seeing results (one round only): labels and notes in CHANGES_AFTER_RESULTS
# below, and fixes to the APP (logged in docs/modules/stocks-evaluation.md with before/after numbers).
# The hypotheses, thresholds, data windows and model settings below may NOT change; if one turns out
# to be wrong it is reported as wrong, not quietly edited.
#
# Authors: written by Vishesh with Claude Code on 2026-11-09, after reading the Phase 7 spec and the
# 7A-0 provider spike, and after looking only at DATA COVERAGE (bar counts, headline counts), never at
# any model output. The guard cases were written by the same author as the claims guard: there is no
# independent labelling (decision of 2026-10-23), stated as a limitation.
#
# Pure Python: no database, no app imports, no third-party imports.

from dataclasses import dataclass
from datetime import date

# ---------------------------------------------------------------------------------------------
# Shared settings
# ---------------------------------------------------------------------------------------------
SEED = 42
ALPHA = 0.05                          # family-wise level; Bonferroni divides it by the family size
EVAL_END = date(2026, 10, 30)         # last bar used (a Friday, end of October). Frozen so a later
                                      # cache refresh does not change the results. A SHA-256 of the
                                      # (symbol, date, close) rows used is written to the results.

# Hand-picked large caps from different sectors (selection and survivorship bias: they are today's
# winners, chosen by name, not by a rule applied in 2016). Prices come from the local cache
# (yfinance via scripts/seed_market_cache.py); no price data is committed to the repo.
UNIVERSE: tuple[str, ...] = (
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "LT.NS",
)
BENCHMARK = "^NSEI"                   # Nifty 50 PRICE index (no dividends)

# ---------------------------------------------------------------------------------------------
# RQ1: does FinBERT beat the word-list baseline on labelled financial sentences?
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SentimentDataset:
    key: str
    hf_id: str
    revision: str                     # HuggingFace dataset commit, pinned
    licence: str
    role: str                         # "primary" | "contaminated" (see notes)
    note: str


RQ1_DATASETS: tuple[SentimentDataset, ...] = (
    SentimentDataset(
        "twitter_fin", "zeroshot/twitter-financial-news-sentiment", "ccbe24de388e287beb92dd393a335c376b350ac3",
        "MIT", "primary",
        "Financial news tweets, labels 0 bearish / 1 bullish / 2 neutral -> negative / positive / neutral. "
        "sent_train.csv trains TF-IDF+LR; sent_valid.csv (the only labelled holdout) is the TEST set for every "
        "model. Published 2022, after ProsusAI/finbert, so FinBERT has not seen it."),
    SentimentDataset(
        "phrasebank", "takala/financial_phrasebank", "8d3fe0c36d5feec6b3cc5e455b0fcb4820fb9964",
        "CC BY-NC-SA 3.0 (non-commercial, attribution: Malo et al. 2014, share-alike)", "contaminated",
        "Sentences_AllAgree.txt (all annotators agree). A seeded stratified 80/20 split; TF-IDF+LR trains on "
        "the 80%, every model is tested on the 20%. ProsusAI/finbert was FINE-TUNED on Financial PhraseBank, "
        "so its score here is in-sample: reported, never used as evidence for H1."),
)
# FiQA (spec's second test set) is NOT used: the MIT-licensed copy (TheFinAI/fiqa-sentiment-classification)
# is gated, and the open copy (pauri32/fiqa-2018) states no licence. Checked 2026-11-09.
# Fallback (spec): if NO dataset file can be obtained, RQ1 runs on a small, clearly labelled SYNTHETIC set
# and every RQ1 row says so. Both datasets above were reachable on 2026-11-09, so this is not expected.

RQ1_LABELS = ("negative", "neutral", "positive")
RQ1_TEST_FRACTION = 0.20              # phrasebank only
RQ1_BOOTSTRAP_RESAMPLES = 2000        # paired bootstrap over test sentences
# Models:
#   M0  majority class of the training split (trivial baseline)
#   L   lexicon (app/services/stocks/lexicon_sentiment.py), label by the APP rule
#       (score = positive - negative; > +0.15 positive, < -0.15 negative, else neutral)
#   F   FinBERT ProsusAI/finbert at the pinned app revision, label by the same APP rule (what users see);
#       FinBERT argmax reported as a secondary row
#   T   TF-IDF (word 1-2 grams, min_df=2, sublinear_tf) + LogisticRegression(C=1.0, max_iter=1000),
#       trained on the dataset's training split only, label = argmax. No tuning on the test set.
# Duplicates: test sentences whose normalised text (lower case, single spaces) also appears in the
# training split are DROPPED from the test set (Financial PhraseBank is known to contain duplicates).
# Metrics: accuracy, macro-F1, per-class recall, confusion matrix, 95% bootstrap CI of macro-F1.
#
# H1 (primary): on twitter_fin, F macro-F1 > L macro-F1. SUPPORTED if the 95% paired-bootstrap CI of
#     (F - L) lies entirely above 0. phrasebank is reported with the contamination label only.
# H1-T (exploratory): T (trained in-domain) vs F (zero-shot) on twitter_fin, same CI rule.
# Expectation written before running: H1 supported; T close to or above F on twitter_fin.

# ---------------------------------------------------------------------------------------------
# RQ2: does aggregated news sentiment lead returns?
# ---------------------------------------------------------------------------------------------
RQ2_HALF_LIFE_DAYS = 3.0              # primary (the app default); ablation 1 and 7
RQ2_HALF_LIFE_ABLATION = (1.0, 7.0)
RQ2_LOOKBACK_DAYS = 7                 # headlines published in the 7 calendar days up to the cutoff
RQ2_HORIZONS = (1, 5)                 # next 1-day and 5-day returns from the close of the sentiment day
RQ2_MIN_DAYS = 30                     # fewer sentiment days for a symbol -> "not evaluable"
RQ2_PERMUTATIONS = 10000
# Assignment rule (leakage control): a headline published after 16:00 IST on trading day d (or on a
# holiday) belongs to the NEXT trading day, so sentiment for day d only uses news known at d's close.
# Test: Spearman rank correlation per symbol and horizon, two-sided permutation p-value.
# Family: len(UNIVERSE) x len(RQ2_HORIZONS) = 20 tests -> Bonferroni alpha 0.0025 (primary half-life,
# primary scorer only; ablations are descriptive). A pooled test (all symbol-days) is reported too.
# Scorer: lexicon (primary, the app default); FinBERT as an ablation when the ml image runs.
#
# H2: |rho| > 0 for at least one symbol-horizon after Bonferroni.
# Expectation written before running: NOT EVALUABLE on real data. The free RSS feed gave 25-30
# headlines per symbol covering 1-25 distinct days (counted 2026-11-09), far below RQ2_MIN_DAYS.
# Power analysis (synthetic, decided with Vishesh on 2026-11-09 instead of scraping older news):
RQ2_POWER_RHO = (0.05, 0.10, 0.20, 0.30)
RQ2_POWER_N = (25, 50, 100, 250, 500, 1000, 2500)
RQ2_POWER_REPLICATIONS = 1000
# Bivariate normal (sentiment, next return) with correlation rho; power = share of replications with
# p < 0.0025, p from the normal approximation z = rho_hat * sqrt(n - 1) on Spearman's rho. Reported:
# the smallest n with power >= 0.80 for each rho, compared with the days of headlines we actually have.

# ---------------------------------------------------------------------------------------------
# RQ3: does any model predict next-day direction better than simple baselines, out of sample?
# ---------------------------------------------------------------------------------------------
# Features for day t (computed ONLY from closes up to and including t):
RQ3_FEATURES = ("ret_lag0", "ret_lag1", "ret_lag2", "ret_lag3", "ret_lag4",   # daily simple returns t, t-1, ..
                "rsi14", "vol20", "mom5", "dist_sma50", "dist_sma200")
# vol20 = sample std of the last 20 daily returns (not annualised); mom5 = close_t / close_{t-5} - 1;
# dist_smaN = close_t / SMA_N(t) - 1. The first usable row is the first with SMA200 (warm-up rows dropped).
# Label: 1 if close_{t+1} > close_t, 0 if lower; days with an exactly zero change are dropped from
# training and from accuracy (walk_forward.directional_accuracy skips them).
RQ3_INITIAL_TRAIN = 504               # about 2 years of usable rows
RQ3_TEST_BLOCK = 21                   # refit every 21 rows (about monthly), expanding window
# Splits: app/services/stocks/walk_forward.walk_forward_splits(n, 504, test_size=21, step=21).
# Baselines:
#   B_up    always up (the buy-and-hold signal)
#   B_prev  previous day's direction (sign of ret_lag0)
#   B_mom5  sign of mom5
#   B_sma   up when SMA50 > SMA200 (the app's trend averages)
# Models (fixed settings, never tuned on test data):
RQ3_LR_PARAMS = {"C": 1.0, "max_iter": 1000}          # StandardScaler + LogisticRegression (lbfgs)
RQ3_LGBM_PARAMS = {"n_estimators": 200, "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 50,
                   "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "random_state": SEED,
                   "n_jobs": 1, "deterministic": True, "force_row_wise": True, "verbose": -1}
# Sentiment features (spec ablation "sentiment on/off"): run ONLY if a symbol has sentiment on at least
# RQ3_INITIAL_TRAIN + RQ3_TEST_BLOCK trading days; otherwise reported as "not evaluable" (expected).
RQ3_SENTIMENT_MIN_DAYS = RQ3_INITIAL_TRAIN + RQ3_TEST_BLOCK
#
# Leakage controls (spec section 9, RQ3):
#   L1 features only from data up to day t (a unit test changes every price after t and checks the
#      features at t do not move)
#   L2 walk-forward only: every test row comes after every training row; nothing is shuffled
#   L3 the scaler is fitted inside each fold on the training window only (sklearn Pipeline)
#   L4 no hyperparameter search; the settings above are fixed
#   L5 placebo control: the same pipeline on 3 seeded synthetic random walks (no signal). The pooled
#      accuracy of each model must lie within 50% +/- 2.58 standard errors; outside means a leak or a bug.
#   L6 power control: 3 seeded synthetic AR(1) return series (phi = 0.10). LR must beat 50% with
#      binomial p < 0.025, showing the harness CAN find a small real signal.
#
# Metrics: directional accuracy (pooled over the 10 symbols and per symbol), exact one-sided binomial
# p-value against 50%, exact one-sided McNemar test (sign test on the days where model and B_up disagree)
# against B_up. Long/flat strategy (hold when the signal is up): total return, CAGR, max drawdown, trades,
# exposure, after a cost of RQ3_COST_BPS charged each time the position changes (buy-and-hold pays it once).
RQ3_COST_BPS = 10
RQ3_COST_ABLATION_BPS = (0, 25)
RQ3_MODEL_FAMILY = 2                  # LR and LightGBM -> pooled alpha 0.025
# Per-symbol tests are descriptive, Bonferroni over 10 symbols x 2 models = 20 (alpha 0.0025).
#
# H3: a model beats the baselines. SUPPORTED only if, pooled: binomial p < 0.025 AND its accuracy is
#     above EVERY baseline's pooled accuracy AND McNemar vs B_up p < 0.025.
# H3-S: the long/flat strategy of a model beats buy-and-hold after costs in a majority (>= 6 of 10) symbols.
# Expectation written before running: H3 NOT supported (accuracy within ~1-2 points of 50%, B_up hard
# to beat because prices drift up); H3-S not supported.

# ---------------------------------------------------------------------------------------------
# RQ4: does a historical block bootstrap change a SIP projection compared with the normal assumption?
# ---------------------------------------------------------------------------------------------
# Data: month-end closes of BENCHMARK (last trading day of each month) up to EVAL_END; monthly simple returns.
RQ4_MONTHLY_SIP = 10000.0             # rupees
RQ4_YEARS = (5, 10)
RQ4_BLOCKS = (3, 6, 12)               # 6 is primary
RQ4_PRIMARY_BLOCK = 6
RQ4_PATHS = 10000
RQ4_GOAL_ANNUAL_RETURN = 0.10         # goal = future value of the SIP at 10% a year (illustrative)
# Normal method: app/services/investing/monte_carlo.simulate_sip with MATCHED moments:
#   mean_annual_return = (1 + mean monthly return) ** 12 - 1, annual_volatility = sd monthly * sqrt(12),
#   so both methods share the sample mean and standard deviation; only the SHAPE differs.
# Bootstrap: app/services/stocks/bootstrap_returns.block_bootstrap_sip, same seed and paths.
# Metrics: p10, p50, p90, P(goal) per method, horizon, block; relative difference vs normal;
# skewness and excess kurtosis of the monthly returns. Descriptive extra: realised outcomes of every
# historical 5-year SIP window (overlapping, in-sample) and the share below each method's p10.
#
# H4: the normal assumption understates bad outcomes. SUPPORTED if bootstrap p10 < normal p10 for every
#     block length at both horizons AND the gap at block 6 / 10 years is larger than 1% of normal p10.
# Expectation written before running: weakly supported (Indian equity monthly returns are mildly
# fat-tailed); the result depends on one 10-year history, which also feeds the bootstrap.

# ---------------------------------------------------------------------------------------------
# RQ5: robustness of the system (data fallback and the explanation guards)
# ---------------------------------------------------------------------------------------------
# Simulation through the REAL cache_service on SQLite, a scripted provider and a simulated clock.
RQ5_SYMBOLS = 10
RQ5_DAYS = 60                         # simulated weekdays
RQ5_REQUESTS_PER_SYMBOL_DAY = 4       # at seeded random IST times between 09:00 and 23:00
RQ5_SCENARIOS = {
    "S0_healthy": "provider always works",
    "S1_rate_limited": "each call fails with rate_limited with probability 0.3",
    "S2_outage_5d": "provider unavailable for 5 consecutive days in the middle",
    "S3_bad_response": "each call fails with bad_response with probability 0.2",
    "S4_down_after_day1": "provider works on day 1 only (stale cache for the rest)",
    "S5_cold_outage": "provider down from the start, nothing cached (errors expected)",
}
RQ5_TTL_ABLATION_HOURS = (1, 6, 24)   # under S0 and S1; 24 is the app default
RQ5_COOLDOWN_ABLATION_MINUTES = (0, 15)   # under S1 and S2; 15 is the app default
# Metrics: served rate (data returned), share by cache status (fresh / provider / stale), error rate,
# staleness at serve time in trading days (latest final close vs data_as_of), provider calls and failed
# calls per 100 requests.
# H5a: served rate >= 99% in S1-S4. H5b: TTL 24 h uses at most a quarter of the provider calls of TTL 1 h
#      with mean staleness <= 1 trading day under S0.
#
# Guards:
#   G1 template: for every UNIVERSE symbol at the last 12 month-ends up to EVAL_END, the template built
#      from the real facts must pass every guard (any-number, shorthand, claims v3). Expected 100%.
#   G2 claims guard v3 on evaluation/stocks_guard_cases.csv (85 sentences written BEFORE measuring, not
#      used while developing the guard; same author): precision, recall, F1, false-positive rate on the
#      hard neutral sentences (trigger words used neutrally).
#   G3 --llm real only (public market aggregates, no personal data): the 10 symbols x 3 month-ends
#      through the configured LLM: raw prohibited-claim rate before the guards (claims v3), unsupported-
#      number rate, template fallback rate, LLM calls per analysis (expected 1.0). "Guard off" = the raw
#      rates are what would reach the user. Not part of the committed deterministic results.
GUARD_CASES_FILE = "stocks_guard_cases.csv"
RQ5_LLM_MONTH_ENDS = 3

# ---------------------------------------------------------------------------------------------
# Changes after seeing the results (filled in once, after the first full run; at most one round).
# Each entry: what changed, why, and the before/after numbers (also in docs/modules/stocks-evaluation.md).
# ---------------------------------------------------------------------------------------------
CHANGES_AFTER_RESULTS: tuple[str, ...] = ()


def bonferroni(alpha: float, family_size: int) -> float:
    """Per-test level that keeps the family-wise error rate at `alpha`."""
    if family_size < 1 or not 0 < alpha < 1:
        raise ValueError("family_size must be >= 1 and 0 < alpha < 1")
    return alpha / family_size


RQ2_ALPHA = bonferroni(ALPHA, len(UNIVERSE) * len(RQ2_HORIZONS))     # 0.0025
RQ3_POOLED_ALPHA = bonferroni(ALPHA, RQ3_MODEL_FAMILY)              # 0.025
RQ3_SYMBOL_ALPHA = bonferroni(ALPHA, len(UNIVERSE) * RQ3_MODEL_FAMILY)   # 0.0025
