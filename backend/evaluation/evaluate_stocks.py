# backend/evaluation/evaluate_stocks.py
#
# Purpose: MEASURE the Stock Intelligence Agent for the paper (Phase 7B, RQ1-RQ5 of docs/phase7-spec.md
# section 9). Every hypothesis, metric, baseline, data window and model setting was PRE-REGISTERED in
# evaluation/stocks_rules.py (committed alone, before this file existed). This script only executes
# that plan and applies its decision rules; it never chooses a threshold itself.
#
#   RQ1 sentiment models   lexicon vs FinBERT vs TF-IDF+LR vs majority on two labelled datasets
#                          (stocks_sentiment_eval.py; data from scripts/fetch_sentiment_datasets.py)
#   RQ2 sentiment lead     Spearman of daily sentiment vs next 1/5-day returns + a synthetic power analysis
#                          (stocks_leadlag.py)
#   RQ3 direction models   walk-forward LR / LightGBM vs 4 baselines, binomial + McNemar tests, long/flat
#                          strategy with costs, placebo and power controls (stocks_direction.py)
#   RQ4 SIP projection     normal Monte Carlo vs block bootstrap on Nifty 50 monthly returns (stocks_sip.py)
#   RQ5 robustness         fallback-chain simulation through the real cache_service, guard metrics
#                          (stocks_robustness.py)
#
# Data: cached prices and headlines from the local database (filled by scripts/seed_market_cache.py),
# up to EVAL_END. No price data or dataset is committed: the results record SHA-256 fingerprints.
#
# Run:
#   docker compose --profile ml run --rm backend-ml python -m evaluation.evaluate_stocks    (final, with FinBERT)
#   docker compose exec backend python -m evaluation.evaluate_stocks                        (no FinBERT)
#   ... --quick           development run (fewer symbols, replications and days)
#   ... --llm real        adds G3: the configured LLM on 30 real analyses (public market aggregates only)
#
# Output (long format, see FIELDS):
#   full run, FinBERT available, --llm none -> evaluation/stocks_metrics.csv + .md (committed; deterministic)
#   anything else                           -> evaluation/results/stocks_metrics_<label>.csv + .md (gitignored)

import argparse
import csv
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from app.agents.stock_explainer import StockExplainer, build_facts, build_facts_payload, template_text
from app.db.session import SessionLocal
from app.sentiment.base import ScorerUnavailable, SentimentProbabilities
from app.sentiment.finbert_scorer import FinbertScorer, finbert_installed     # lazy: no torch import here
from app.sentiment.lexicon_scorer import LexiconScorer
from app.services.investing.claims_guard import find_prohibited_stock_claims
from app.services.stocks.walk_forward import binomial_p_value_greater
from evaluation.evaluate_categorizer import overridden_settings
from evaluation import stocks_direction as direction
from evaluation import stocks_leadlag as leadlag
from evaluation import stocks_robustness as robustness
from evaluation import stocks_sentiment_eval as rq1
from evaluation import stocks_sip as sip
from evaluation.stocks_data import (
    Series, ar1_walk, fingerprint, load_headlines, load_universe, random_walk, to_series,
)
from evaluation.stocks_rules import (
    BENCHMARK, CHANGES_AFTER_RESULTS, EVAL_END, RQ1_BOOTSTRAP_RESAMPLES, RQ1_DATASETS, RQ2_ALPHA,
    RQ2_HALF_LIFE_ABLATION, RQ2_HALF_LIFE_DAYS, RQ2_HORIZONS, RQ2_MIN_DAYS, RQ2_PERMUTATIONS, RQ2_POWER_N,
    RQ2_POWER_REPLICATIONS, RQ2_POWER_RHO, RQ3_COST_ABLATION_BPS, RQ3_COST_BPS, RQ3_POOLED_ALPHA,
    RQ3_SENTIMENT_MIN_DAYS, RQ3_SYMBOL_ALPHA, RQ4_BLOCKS, RQ4_GOAL_ANNUAL_RETURN, RQ4_MONTHLY_SIP, RQ4_PATHS,
    RQ4_PRIMARY_BLOCK, RQ4_YEARS, RQ5_COOLDOWN_ABLATION_MINUTES, RQ5_DAYS, RQ5_LLM_MONTH_ENDS,
    RQ5_REQUESTS_PER_SYMBOL_DAY, RQ5_SCENARIOS, RQ5_SYMBOLS, RQ5_TTL_ABLATION_HOURS, SEED, UNIVERSE,
)

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
FIELDS = ("experiment", "method", "config", "group", "metric", "value", "n")
PLACEBO_SEEDS = (1001, 1002, 1003)
POWER_SEEDS = (2001, 2002, 2003)
DEFAULT_TTL, DEFAULT_COOLDOWN = 24.0, 15.0
ALL = "all"

Scorer = Callable[[Sequence[str]], list[SentimentProbabilities]]


@dataclass
class Run:
    """Everything one evaluation run produces: long-format records plus tables for the report."""
    quick: bool
    llm: str
    records: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, experiment: str, method: str, config: str, group: str, metric: str, value: float, n: int) -> None:
        self.records.append({"experiment": experiment, "method": method, "config": config, "group": group,
                             "metric": metric, "value": value, "n": n})


# ------------------------------------------------------------------ scorers

def available_scorers(run: Run) -> dict[str, Scorer]:
    """The lexicon always; FinBERT when its packages and model are present (the ml image)."""
    scorers: dict[str, Scorer] = {"lexicon": LexiconScorer().score}
    if not finbert_installed():
        run.notes.append("FinBERT not installed in this image: its rows are missing (use the ml profile).")
        return scorers
    finbert = FinbertScorer()
    try:
        finbert.score(["warm-up"])                            # load once; fails fast if the model is missing
        scorers["finbert"] = finbert.score
    except ScorerUnavailable as exc:
        run.notes.append(f"FinBERT unavailable ({exc}): its rows are missing.")
    return scorers


# ------------------------------------------------------------------ RQ1

def run_rq1(run: Run, scorers: dict[str, Scorer]) -> None:
    splits, notes = rq1.load_splits()
    run.notes.extend(notes)
    resamples = 200 if run.quick else RQ1_BOOTSTRAP_RESAMPLES
    tables = {}
    for split in splits:
        texts, gold = [e.text for e in split.test], [e.label for e in split.test]
        predictions: dict[str, list[str]] = {"M0_majority": rq1.majority_predict(split),
                                             "L_lexicon": rq1.app_labels(scorers["lexicon"](texts))}
        if "finbert" in scorers:
            probabilities = scorers["finbert"](texts)
            predictions["F_finbert"] = rq1.app_labels(probabilities)
            predictions["F_finbert_argmax"] = rq1.argmax_labels(probabilities)
        predictions["T_tfidf_lr"] = rq1.tfidf_lr_predict(split)
        rows = {}
        for method, predicted in predictions.items():
            metrics = rq1.classification_metrics(gold, predicted)
            lo, hi = rq1.bootstrap_macro_f1(gold, predicted, resamples=resamples)
            metrics["macro_f1_ci_low"], metrics["macro_f1_ci_high"] = lo, hi
            for name, value in metrics.items():
                run.add("rq1_sentiment", method, split.source, split.key, name, value, len(gold))
            for (g, p), count in rq1.confusion(gold, predicted).items():
                run.add("rq1_sentiment", method, split.source, split.key, f"confusion_{g}_as_{p}", count, len(gold))
            rows[method] = metrics
        diffs = {}
        for a, b in (("F_finbert", "L_lexicon"), ("T_tfidf_lr", "F_finbert"), ("T_tfidf_lr", "L_lexicon")):
            if a in predictions and b in predictions:
                point = rows[a]["macro_f1"] - rows[b]["macro_f1"]
                lo, hi = rq1.bootstrap_macro_f1(gold, predictions[a], predictions[b], resamples=resamples)
                diffs[f"{a} - {b}"] = (point, lo, hi)
                for name, value in (("macro_f1_diff", point), ("diff_ci_low", lo), ("diff_ci_high", hi)):
                    run.add("rq1_sentiment", f"{a}-{b}", split.source, split.key, name, value, len(gold))
        tables[split.key] = {"source": split.source, "train": len(split.train), "test": len(split.test),
                             "dropped_duplicates": split.dropped_duplicates, "sha256": split.sha256,
                             "rows": rows, "diffs": diffs,
                             "labels": {label: gold.count(label) for label in rq1.RQ1_LABELS}}
    run.info["rq1"] = tables


# ------------------------------------------------------------------ RQ2

def run_rq2(run: Run, series: dict[str, Series], headlines: dict, scorers: dict[str, Scorer]) -> dict[str, int]:
    """Returns sentiment days per symbol (primary scorer and half-life), which RQ3 needs."""
    permutations = 999 if run.quick else RQ2_PERMUTATIONS
    sentiment_days: dict[str, int] = {}
    table, coverage = [], {}
    for scorer_name, scorer in scorers.items():
        for half_life in (RQ2_HALF_LIFE_DAYS, *RQ2_HALF_LIFE_ABLATION):
            config = f"{scorer_name}_hl{half_life:g}"
            primary = scorer_name == "lexicon" and half_life == RQ2_HALF_LIFE_DAYS
            pooled: dict[int, tuple[list[float], list[float]]] = {h: ([], []) for h in RQ2_HORIZONS}
            for symbol, s in series.items():
                items = headlines.get(symbol, [])
                probabilities = [p.as_tuple() for p in scorer([h.text for h in items])] if items else []
                daily = leadlag.daily_sentiment([h.published_at for h in items], probabilities, s.dates, half_life)
                if primary:
                    sentiment_days[symbol] = len(daily)
                    coverage[symbol] = {
                        "headlines": len(items),
                        "first": min((h.published_at.date() for h in items), default=None),
                        "last": max((h.published_at.date() for h in items), default=None),
                        "after_eval_end": sum(1 for h in items if h.published_at.date() > EVAL_END),
                        "sentiment_days": len(daily),
                    }
                for horizon in RQ2_HORIZONS:
                    forward = leadlag.forward_returns(s.dates, s.closes, horizon)
                    pairs = [(daily[d], forward[d]) for d in sorted(daily) if d in forward]
                    pooled[horizon][0].extend(p[0] for p in pairs)
                    pooled[horizon][1].extend(p[1] for p in pairs)
                    n = len(pairs)
                    run.add("rq2_leadlag", f"h{horizon}", config, symbol, "n_days", n, n)
                    if n >= RQ2_MIN_DAYS:
                        x, y = [p[0] for p in pairs], [p[1] for p in pairs]
                        rho, p = leadlag.spearman(x, y), leadlag.permutation_p(x, y, permutations)
                        run.add("rq2_leadlag", f"h{horizon}", config, symbol, "spearman", rho, n)
                        run.add("rq2_leadlag", f"h{horizon}", config, symbol, "p_permutation", p, n)
                        if primary:
                            table.append((symbol, horizon, n, rho, p))
                    elif primary:
                        table.append((symbol, horizon, n, None, None))
            for horizon, (x, y) in pooled.items():
                n = len(x)
                run.add("rq2_leadlag", f"h{horizon}", config, ALL, "n_days", n, n)
                if n >= RQ2_MIN_DAYS:
                    run.add("rq2_leadlag", f"h{horizon}", config, ALL, "spearman", leadlag.spearman(x, y), n)
                    run.add("rq2_leadlag", f"h{horizon}", config, ALL, "p_permutation",
                            leadlag.permutation_p(x, y, permutations), n)
    replications = 200 if run.quick else RQ2_POWER_REPLICATIONS
    power_table: dict[float, dict[int, float]] = {}
    for rho in RQ2_POWER_RHO:
        power_table[rho] = {}
        for n in RQ2_POWER_N:
            value = leadlag.power(rho, n, RQ2_ALPHA, replications, seed=SEED)
            power_table[rho][n] = value
            run.add("rq2_power", "spearman_normal_approx", f"rho{rho:g}", f"n{n}", "power", value, replications)
    run.info["rq2"] = {"table": table, "coverage": coverage, "power": power_table, "replications": replications,
                       "permutations": permutations}
    return sentiment_days


# ------------------------------------------------------------------ RQ3

def _evaluate_series(run: Run, s: Series, group: str) -> tuple[direction.WalkForwardResult, dict]:
    rows = direction.build_rows(s.closes)
    result = direction.walk_forward(s.symbol, rows)
    summary = {}
    for method, signals in result.signals.items():
        n, h = len(result.test_rows), direction.hits(result.test_rows, signals)
        p = binomial_p_value_greater(h, n)
        summary[method] = (h, n, p)
        run.add("rq3_direction", method, group, s.symbol, "accuracy", h / n, n)
        run.add("rq3_direction", method, group, s.symbol, "binomial_p", p, n)
    return result, summary


def run_rq3(run: Run, series: dict[str, Series], sentiment_days: dict[str, int]) -> None:
    methods = (*direction.BASELINES, *direction.MODELS)
    per_symbol, results = {}, {}
    for symbol, s in series.items():
        results[symbol], per_symbol[symbol] = _evaluate_series(run, s, "real")
    pooled = {}
    for method in methods:
        h = sum(per_symbol[s][method][0] for s in per_symbol)
        n = sum(per_symbol[s][method][1] for s in per_symbol)
        p = binomial_p_value_greater(h, n)
        b = c = 0
        for symbol, result in results.items():
            _, bb, cc = direction.mcnemar_p_greater(result.test_rows, result.signals[method], result.signals["B_up"])
            b, c = b + bb, c + cc
        mcnemar = binomial_p_value_greater(b, b + c) if b + c else 1.0
        pooled[method] = {"hits": h, "n": n, "accuracy": h / n, "binomial_p": p, "mcnemar_p": mcnemar,
                          "model_only_right": b, "up_only_right": c}
        for name in ("accuracy", "binomial_p", "mcnemar_p"):
            run.add("rq3_direction", method, "real", ALL, name, pooled[method][name], n)
    strategies: dict[int, dict[str, dict[str, direction.StrategyStats]]] = {}
    for cost in (RQ3_COST_BPS, *RQ3_COST_ABLATION_BPS):
        strategies[cost] = {}
        for symbol, result in results.items():
            returns = [r.next_return for r in result.test_rows]
            strategies[cost][symbol] = {m: direction.strategy(returns, result.signals[m], cost) for m in methods}
            for method, stats in strategies[cost][symbol].items():
                for name in ("total_return", "cagr", "max_drawdown", "trades", "exposure"):
                    run.add("rq3_strategy", method, f"cost{cost}bps", symbol, name, getattr(stats, name),
                            len(returns))
    controls = {}
    n_real = len(next(iter(series.values())).closes)
    for label, maker, seeds in (("placebo_random_walk", random_walk, PLACEBO_SEEDS),
                                ("power_ar1_phi0.10", ar1_walk, POWER_SEEDS)):
        seeds = seeds[:1] if run.quick else seeds
        totals = {m: [0, 0] for m in methods}
        for seed in seeds:
            synthetic = maker(f"{label.split('_')[0].upper()}{seed}", n_real, seed)
            _, summary = _evaluate_series(run, synthetic, label)
            for m in methods:
                totals[m][0] += summary[m][0]
                totals[m][1] += summary[m][1]
        controls[label] = {}
        for m, (h, n) in totals.items():
            p = binomial_p_value_greater(h, n)
            controls[label][m] = (h / n, n, p)
            run.add("rq3_direction", m, label, ALL, "accuracy", h / n, n)
            run.add("rq3_direction", m, label, ALL, "binomial_p", p, n)
    sentiment = {s: (days, days >= RQ3_SENTIMENT_MIN_DAYS) for s, days in sentiment_days.items()}
    for symbol, (days, ok) in sentiment.items():
        run.add("rq3_direction", "sentiment_features", "evaluable", symbol, "sentiment_days", days, int(ok))
    run.info["rq3"] = {"per_symbol": per_symbol, "pooled": pooled, "strategies": strategies,
                       "controls": controls, "sentiment": sentiment,
                       "folds": {s: r.folds for s, r in results.items()},
                       "test_days": {s: len(r.test_rows) for s, r in results.items()},
                       "first_test": {s: series[s].dates[r.test_rows[0].index] for s, r in results.items()},
                       "last_test": {s: series[s].dates[r.test_rows[-1].index] for s, r in results.items()}}


# ------------------------------------------------------------------ RQ4

def run_rq4(run: Run, benchmark: Series) -> None:
    months = sip.month_end_closes(benchmark.dates, benchmark.closes)
    returns = sip.monthly_returns(months)
    paths = 2000 if run.quick else RQ4_PATHS
    comparisons = sip.compare(returns, RQ4_MONTHLY_SIP, RQ4_YEARS, RQ4_BLOCKS, paths, SEED, RQ4_GOAL_ANNUAL_RETURN)
    normal = {c.years: c.result for c in comparisons if c.method == "normal"}
    for c in comparisons:
        r = c.result
        for name, value in (("p10", r.p10), ("p50", r.p50), ("p90", r.p90), ("prob_goal", r.probability_of_goal)):
            run.add("rq4_sip", c.method, f"{c.years}y", BENCHMARK, name, value, paths)
        if c.method != "normal":
            for name in ("p10", "p50", "p90"):
                base = getattr(normal[c.years], name)
                run.add("rq4_sip", c.method, f"{c.years}y", BENCHMARK, f"{name}_vs_normal",
                        getattr(r, name) / base - 1, paths)
    skew, kurt = sip.shape(returns)
    mean_annual, volatility = sip.matched_normal_inputs(returns)
    run.add("rq4_sip", "history", "monthly", BENCHMARK, "skewness", skew, len(returns))
    run.add("rq4_sip", "history", "monthly", BENCHMARK, "excess_kurtosis", kurt, len(returns))
    run.add("rq4_sip", "history", "monthly", BENCHMARK, "mean_annual_return", mean_annual, len(returns))
    run.add("rq4_sip", "history", "monthly", BENCHMARK, "annual_volatility", volatility, len(returns))
    realised = sip.rolling_realised(returns, RQ4_MONTHLY_SIP, 5)
    below = {}
    for c in comparisons:
        if c.years == 5 and realised:
            share = sum(1 for v in realised if v < c.result.p10) / len(realised)
            below[c.method] = share
            run.add("rq4_sip", c.method, "5y", BENCHMARK, "realised_share_below_p10", share, len(realised))
    run.info["rq4"] = {"months": len(returns), "first_month": months[0][0], "last_month": months[-1][0],
                       "comparisons": comparisons, "skew": skew, "kurtosis": kurt, "mean_annual": mean_annual,
                       "volatility": volatility, "realised_windows": len(realised), "below_p10": below,
                       "realised_min": min(realised) if realised else None, "paths": paths}


# ------------------------------------------------------------------ RQ5

def run_rq5(run: Run, bars: dict, benchmark_bars: list, llm_delay: float) -> None:
    days = 20 if run.quick else RQ5_DAYS
    configs = [(s, DEFAULT_TTL, DEFAULT_COOLDOWN) for s in RQ5_SCENARIOS]
    configs += [(s, ttl, DEFAULT_COOLDOWN) for s in ("S0_healthy", "S1_rate_limited")
                for ttl in RQ5_TTL_ABLATION_HOURS if ttl != DEFAULT_TTL]
    configs += [(s, DEFAULT_TTL, cd) for s in ("S1_rate_limited", "S2_outage_5d")
                for cd in RQ5_COOLDOWN_ABLATION_MINUTES if cd != DEFAULT_COOLDOWN]
    sims = []
    for scenario, ttl, cooldown in configs:
        stats = robustness.simulate(scenario, n_symbols=RQ5_SYMBOLS, n_days=days,
                                    per_day=RQ5_REQUESTS_PER_SYMBOL_DAY, ttl_hours=ttl, cooldown_minutes=cooldown)
        sims.append(stats)
        config = f"ttl{ttl:g}h_cooldown{cooldown:g}m"
        n = stats.requests
        values = {
            "served_rate": stats.served_rate, "error_rate": stats.errors / n,
            "share_fresh": stats.by_status.get("fresh", 0) / n,
            "share_refreshed": stats.by_status.get("refreshed", 0) / n,
            "share_stale": stats.by_status.get("stale", 0) / n,
            "staleness_mean_days": sum(stats.staleness) / len(stats.staleness) if stats.staleness else 0.0,
            "staleness_max_days": max(stats.staleness, default=0),
            "provider_calls_per_100": 100 * stats.provider_calls / n,
            "failed_calls_per_100": 100 * stats.failed_calls / n,
        }
        for name, value in values.items():
            run.add("rq5_fallback", scenario, config, ALL, name, value, n)
    # G1: the template on real facts at the last 12 month-ends
    explainer = StockExplainer(find_prohibited_stock_claims, chains=[])
    ends = robustness.month_ends([b.bar_date for b in benchmark_bars], 12)
    results, template_failures = [], []
    for symbol, symbol_bars in bars.items():
        for end in ends:
            result = robustness.analysis_result(symbol, symbol_bars, BENCHMARK, benchmark_bars, end)
            facts = build_facts(result)
            problems, claims = explainer.check(template_text(facts), build_facts_payload(facts))
            if problems or claims:
                template_failures.append((symbol, end.isoformat(), problems, claims))
            results.append((symbol, end, result))
    g1 = 1 - len(template_failures) / len(results)
    run.add("rq5_guards", "G1_template", "all_guards", ALL, "pass_rate", g1, len(results))
    # G2: claims guard v3 on the held-out sentences
    cases = robustness.load_guard_cases()
    g2, wrong = robustness.guard_metrics(cases, lambda text: bool(find_prohibited_stock_claims(text)))
    for name, value in g2.items():
        run.add("rq5_guards", "G2_claims_v3", "held_out_cases", ALL, name, value, len(cases))
    # G3: the configured LLM (only with --llm real)
    g3 = None
    if run.llm == "real":
        last_ends = set(ends[-RQ5_LLM_MONTH_ENDS:])
        sample = [(s, e, r) for s, e, r in results if e in last_ends]
        with overridden_settings({"STOCK_USE_LLM_EXPLANATIONS": True}):
            real = StockExplainer.from_settings(find_prohibited_stock_claims)
            outcomes = []
            for i, (symbol, end, result) in enumerate(sample):
                if i:
                    time.sleep(llm_delay)
                outcomes.append(real.explain(result))
        n = len(outcomes)
        answered = [o for o in outcomes if o.llm_used]
        g3 = {
            "analyses": n, "llm_answered": len(answered),
            "raw_prohibited_claims_rate": sum(1 for o in answered if o.claims_compliant is False) / max(1, len(answered)),
            "unsupported_number_rate": sum(1 for o in answered if o.explanation_faithful is False) / max(1, len(answered)),
            "template_fallback_rate": sum(1 for o in outcomes if not (o.llm_used and o.claims_compliant
                                                                    and o.explanation_faithful)) / n,
            "llm_calls_per_analysis": sum(o.llm_calls for o in outcomes) / n,
            "rejected_for": [list(o.rejected_for) for o in outcomes if o.rejected_for],
        }
        for name in ("raw_prohibited_claims_rate", "unsupported_number_rate", "template_fallback_rate",
                     "llm_calls_per_analysis"):
            run.add("rq5_guards", "G3_llm", "real", ALL, name, g3[name], n)
    run.info["rq5"] = {"sims": sims, "days": days, "g1": g1, "g1_n": len(results), "g1_failures": template_failures,
                       "g2": g2, "g2_wrong": wrong, "g2_cases": cases, "g3": g3}


# ------------------------------------------------------------------ verdicts (pre-registered rules)

def verdicts(run: Run) -> list[tuple[str, str, str]]:
    out = []
    twitter = run.info["rq1"].get("twitter_fin", {}).get("diffs", {})
    if "F_finbert - L_lexicon" in twitter:
        point, lo, hi = twitter["F_finbert - L_lexicon"]
        out.append(("H1", "supported" if lo > 0 else "not supported",
                    f"twitter_fin macro-F1 FinBERT - lexicon = {point:+.3f} (95% CI {lo:+.3f} to {hi:+.3f})"))
    else:
        out.append(("H1", "not evaluated", "FinBERT or the twitter_fin dataset missing in this run"))
    if "T_tfidf_lr - F_finbert" in twitter:
        point, lo, hi = twitter["T_tfidf_lr - F_finbert"]
        verdict = "T better" if lo > 0 else "F better" if hi < 0 else "no clear difference"
        out.append(("H1-T", verdict, f"TF-IDF+LR - FinBERT = {point:+.3f} (95% CI {lo:+.3f} to {hi:+.3f})"))
    rq2 = run.info["rq2"]["table"]
    evaluable = [row for row in rq2 if row[3] is not None]
    if not evaluable:
        out.append(("H2", "not evaluable",
                    f"no symbol had {RQ2_MIN_DAYS} sentiment days (max {max((r[2] for r in rq2), default=0)})"))
    else:
        significant = [r for r in evaluable if r[4] < RQ2_ALPHA]
        out.append(("H2", "supported" if significant else "not supported",
                    f"{len(significant)} of {len(evaluable)} evaluable tests below alpha {RQ2_ALPHA:g}"))
    pooled = run.info["rq3"]["pooled"]
    best_baseline = max(pooled[b]["accuracy"] for b in direction.BASELINES)
    for model in direction.MODELS:
        p = pooled[model]
        ok = (p["binomial_p"] < RQ3_POOLED_ALPHA and p["accuracy"] > best_baseline and p["mcnemar_p"] < RQ3_POOLED_ALPHA)
        out.append((f"H3 ({model})", "supported" if ok else "not supported",
                    f"pooled accuracy {p['accuracy']:.4f} vs best baseline {best_baseline:.4f}; binomial p "
                    f"{p['binomial_p']:.3g}, McNemar vs B_up p {p['mcnemar_p']:.3g} (alpha {RQ3_POOLED_ALPHA:g})"))
    strategies = run.info["rq3"]["strategies"][RQ3_COST_BPS]
    for model in direction.MODELS:
        wins = sum(1 for s in strategies.values() if s[model].total_return > s["B_up"].total_return)
        out.append((f"H3-S ({model})", "supported" if wins >= 6 else "not supported",
                    f"beats buy-and-hold after {RQ3_COST_BPS} bps in {wins} of {len(strategies)} symbols"))
    comparisons = run.info["rq4"]["comparisons"]
    normal = {c.years: c.result.p10 for c in comparisons if c.method == "normal"}
    boot = [(c.years, c.method, c.result.p10) for c in comparisons if c.method != "normal"]
    all_lower = all(p10 < normal[y] for y, _, p10 in boot)
    primary = next(p10 for y, m, p10 in boot if y == max(RQ4_YEARS) and m == f"bootstrap_b{RQ4_PRIMARY_BLOCK}")
    gap = primary / normal[max(RQ4_YEARS)] - 1
    out.append(("H4", "supported" if all_lower and gap < -0.01 else "not supported",
                f"bootstrap p10 below normal in {sum(p10 < normal[y] for y, _, p10 in boot)} of {len(boot)} cells; "
                f"block {RQ4_PRIMARY_BLOCK} / {max(RQ4_YEARS)} y gap {gap:+.1%}"))
    sims = {(s.scenario, s.ttl_hours, s.cooldown_minutes): s for s in run.info["rq5"]["sims"]}
    served = {k[0]: v.served_rate for k, v in sims.items() if k[1:] == (DEFAULT_TTL, DEFAULT_COOLDOWN)}
    worst = min(served[s] for s in ("S1_rate_limited", "S2_outage_5d", "S3_bad_response", "S4_down_after_day1"))
    out.append(("H5a", "supported" if worst >= 0.99 else "not supported", f"lowest served rate in S1-S4: {worst:.2%}"))
    s0_24, s0_1 = sims[("S0_healthy", 24.0, DEFAULT_COOLDOWN)], sims[("S0_healthy", 1, DEFAULT_COOLDOWN)]
    ratio = s0_24.provider_calls / s0_1.provider_calls if s0_1.provider_calls else 0.0
    stale = sum(s0_24.staleness) / len(s0_24.staleness)
    out.append(("H5b", "supported" if ratio <= 0.25 and stale <= 1 else "not supported",
                f"TTL 24 h uses {ratio:.1%} of the calls of TTL 1 h; mean staleness {stale:.2f} trading days"))
    return out


# ------------------------------------------------------------------ report

def _pct(value: float | None, places: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{places}f}%"


def _rupees(value: float) -> str:
    return f"Rs {value:,.0f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows]


def markdown_report(run: Run) -> str:
    info = run.info
    lines = ["# Stock Intelligence evaluation (Phase 7B)", "",
             "Generated by `python -m evaluation.evaluate_stocks`. Plan pre-registered in "
             "`evaluation/stocks_rules.py`; read `docs/modules/stocks-evaluation.md` for the discussion.", "",
             f"- Data: cached daily bars up to {EVAL_END.isoformat()} for {len(UNIVERSE)} hand-picked stocks + "
             f"{BENCHMARK}; SHA-256 of (symbol, date, close) rows: `{info['fingerprint']}`",
             f"- Libraries: {info['versions']}", f"- Quick run: {'yes' if run.quick else 'no'}; LLM: {run.llm}", ""]
    if CHANGES_AFTER_RESULTS:
        lines += ["Changes after seeing results: " + "; ".join(CHANGES_AFTER_RESULTS), ""]
    for note in run.notes:
        lines.append(f"> Note: {note}")
    lines += ["", "## Verdicts (pre-registered decision rules)", ""]
    lines += _table(["Hypothesis", "Verdict", "Evidence"], [[h, v, e] for h, v, e in verdicts(run)])

    lines += ["", "## RQ1 Sentiment models on labelled sentences", ""]
    names = {d.key: d for d in RQ1_DATASETS}
    for key, t in info["rq1"].items():
        meta = names.get(key)
        label = f"{key} ({meta.hf_id}, {meta.licence}, role: {meta.role})" if meta else key
        lines += [f"### {label}", "",
                  f"Train {t['train']}, test {t['test']} (gold labels {t['labels']}), duplicates dropped from test: "
                  f"{t['dropped_duplicates']}. Files: " + ", ".join(f"{k} `{v[:16]}`" for k, v in t["sha256"].items()), ""]
        if meta and meta.role == "contaminated":
            lines += ["**FinBERT was fine-tuned on Financial PhraseBank: its numbers here are in-sample.**", ""]
        rows = []
        for method, m in t["rows"].items():
            rows.append([method, f"{m['accuracy']:.3f}", f"{m['macro_f1']:.3f}",
                         f"{m['macro_f1_ci_low']:.3f} to {m['macro_f1_ci_high']:.3f}",
                         f"{m['recall_negative']:.3f}", f"{m['recall_neutral']:.3f}", f"{m['recall_positive']:.3f}"])
        lines += _table(["Model", "Accuracy", "Macro-F1", "95% CI", "Recall neg", "Recall neu", "Recall pos"], rows)
        if t["diffs"]:
            lines += [""] + _table(["Paired difference (macro-F1)", "Point", "95% CI"],
                                   [[k, f"{p:+.3f}", f"{lo:+.3f} to {hi:+.3f}"] for k, (p, lo, hi) in t["diffs"].items()])
        lines.append("")

    rq2 = info["rq2"]
    lines += ["## RQ2 Does news sentiment lead returns?", "",
              f"Primary: lexicon, half-life {RQ2_HALF_LIFE_DAYS:g} days, minimum {RQ2_MIN_DAYS} sentiment days, "
              f"Bonferroni alpha {RQ2_ALPHA:g}, {rq2['permutations']} permutations.", ""]
    lines += _table(["Symbol", "Headlines", "Published", "After EVAL_END", "Sentiment days"],
                    [[s, str(c["headlines"]), f"{c['first']} to {c['last']}", str(c["after_eval_end"]),
                      str(c["sentiment_days"])] for s, c in rq2["coverage"].items()])
    lines += [""] + _table(["Symbol", "Horizon", "Days", "Spearman", "p"],
                           [[s, f"{h} d", str(n), "not evaluable" if r is None else f"{r:+.3f}",
                             "" if p is None else f"{p:.4f}"] for s, h, n, r, p in rq2["table"]])
    lines += ["", f"Power at alpha {RQ2_ALPHA:g} (synthetic, {rq2['replications']} replications per cell):", ""]
    lines += _table(["rho"] + [f"n={n}" for n in RQ2_POWER_N] + ["n for 80%"],
                    [[f"{rho:g}"] + [f"{rq2['power'][rho][n]:.2f}" for n in RQ2_POWER_N]
                     + [next((str(n) for n in RQ2_POWER_N if rq2["power"][rho][n] >= 0.8), f"> {max(RQ2_POWER_N)}")]
                     for rho in RQ2_POWER_RHO])

    rq3 = info["rq3"]
    lines += ["", "## RQ3 Next-day direction, walk-forward", "",
              "Expanding window: first 504 usable days train, refit every 21. Test days per symbol: "
              + ", ".join(f"{s} {n} ({rq3['first_test'][s]} to {rq3['last_test'][s]})" for s, n in rq3["test_days"].items()), ""]
    lines += _table(["Method", "Pooled accuracy", "Days", "Binomial p vs 50%", "McNemar p vs B_up",
                     "Model-only right / B_up-only right"],
                    [[m, f"{p['accuracy']:.4f}", str(p["n"]), f"{p['binomial_p']:.3g}", f"{p['mcnemar_p']:.3g}",
                      f"{p['model_only_right']} / {p['up_only_right']}"] for m, p in rq3["pooled"].items()])
    lines += ["", f"Per symbol accuracy (descriptive; Bonferroni alpha {RQ3_SYMBOL_ALPHA:g}, * = below it):", ""]
    methods = list(rq3["pooled"])
    lines += _table(["Symbol"] + methods,
                    [[s] + [f"{h / n:.3f}{'*' if p < RQ3_SYMBOL_ALPHA else ''}" for h, n, p in
                            (rq3["per_symbol"][s][m] for m in methods)] for s in rq3["per_symbol"]])
    lines += ["", "Controls (pooled over the synthetic series):", ""]
    rows = []
    for label, values in rq3["controls"].items():
        lo, hi = direction.binomial_band(next(iter(values.values()))[1])
        for m, (acc, n, p) in values.items():
            rows.append([label, m, f"{acc:.4f}", str(n), f"{p:.3g}", f"{lo:.4f} to {hi:.4f}"])
    lines += _table(["Control", "Method", "Accuracy", "Days", "Binomial p", "Placebo band (2.58 SE)"], rows)
    for cost in (RQ3_COST_BPS, *RQ3_COST_ABLATION_BPS):
        lines += ["", f"Long/flat strategy, cost {cost} bps per position change (total return over the test period; "
                  "B_up = buy-and-hold):", ""]
        strat = rq3["strategies"][cost]
        lines += _table(["Symbol"] + methods,
                        [[s] + [_pct(strat[s][m].total_return) for m in methods] for s in strat])
        if cost == RQ3_COST_BPS:
            lines += ["", "Max drawdown and trades at this cost:", ""]
            lines += _table(["Symbol"] + methods,
                            [[s] + [f"{_pct(strat[s][m].max_drawdown, 0)} / {strat[s][m].trades}" for m in methods]
                             for s in strat])
    lines += ["", "Sentiment features: "
              + ", ".join(f"{s} {d} days ({'evaluable' if ok else 'not evaluable'})"
                          for s, (d, ok) in rq3["sentiment"].items())
              + f" (needs {RQ3_SENTIMENT_MIN_DAYS})."]

    rq4 = info["rq4"]
    lines += ["", "## RQ4 Normal Monte Carlo vs block bootstrap (Nifty 50 price index)", "",
              f"{rq4['months']} monthly returns ({rq4['first_month']} to {rq4['last_month']}); matched mean "
              f"{_pct(rq4['mean_annual'])} a year, volatility {_pct(rq4['volatility'])}; skewness {rq4['skew']:+.2f}, "
              f"excess kurtosis {rq4['kurtosis']:+.2f}. SIP {_rupees(RQ4_MONTHLY_SIP)}/month, {rq4['paths']} paths, "
              f"goal = value at {RQ4_GOAL_ANNUAL_RETURN:.0%} a year.", ""]
    normal = {c.years: c.result for c in rq4["comparisons"] if c.method == "normal"}
    lines += _table(["Years", "Method", "p10", "p50", "p90", "P(goal)", "p10 vs normal"],
                    [[str(c.years), c.method, _rupees(c.result.p10), _rupees(c.result.p50), _rupees(c.result.p90),
                      _pct(c.result.probability_of_goal), "" if c.method == "normal"
                      else f"{c.result.p10 / normal[c.years].p10 - 1:+.1%}"] for c in rq4["comparisons"]])
    if rq4["below_p10"]:
        lines += ["", f"Realised 5-year SIPs in the history ({rq4['realised_windows']} overlapping windows, in-sample; "
                  f"worst {_rupees(rq4['realised_min'])}): share below each method's 5-year p10: "
                  + ", ".join(f"{m} {_pct(v)}" for m, v in rq4["below_p10"].items())]

    rq5 = info["rq5"]
    lines += ["", "## RQ5 Robustness", "",
              f"Fallback chain: {RQ5_SYMBOLS} synthetic symbols, {rq5['days']} weekdays, "
              f"{RQ5_REQUESTS_PER_SYMBOL_DAY} requests per symbol per day, real cache_service on SQLite.", ""]
    lines += _table(["Scenario", "TTL", "Cooldown", "Served", "Fresh", "Refreshed", "Stale", "Errors",
                     "Staleness mean / max (days)", "Calls / 100", "Failed / 100"],
                    [[s.scenario, f"{s.ttl_hours:g} h", f"{s.cooldown_minutes:g} min", _pct(s.served_rate, 2),
                      _pct(s.by_status.get("fresh", 0) / s.requests), _pct(s.by_status.get("refreshed", 0) / s.requests),
                      _pct(s.by_status.get("stale", 0) / s.requests), str(s.errors),
                      f"{sum(s.staleness) / max(1, len(s.staleness)):.2f} / {max(s.staleness, default=0)}",
                      f"{100 * s.provider_calls / s.requests:.1f}", f"{100 * s.failed_calls / s.requests:.1f}"]
                     for s in rq5["sims"]])
    lines += ["", f"G1 template on real facts: pass rate {_pct(rq5['g1'])} of {rq5['g1_n']} "
              f"(failures: {rq5['g1_failures'] or 'none'}).", "",
              f"G2 claims guard v3 on {len(rq5['g2_cases'])} held-out sentences (same author as the guard):", ""]
    lines += _table(["Metric", "Value"], [[k, f"{v:.3f}"] for k, v in rq5["g2"].items()])
    by_id = {c.case_id: c for c in rq5["g2_cases"]}
    if rq5["g2_wrong"]:
        lines += ["", "Wrong decisions:", ""]
        lines += _table(["Case", "Kind", "Should flag", "Text"],
                        [[i, by_id[i].kind, "yes" if by_id[i].prohibited else "no", by_id[i].text]
                         for i in rq5["g2_wrong"]])
    if rq5["g3"]:
        g3 = rq5["g3"]
        lines += ["", f"G3 configured LLM on {g3['analyses']} analyses ({g3['llm_answered']} answered): raw prohibited "
                  f"claims {_pct(g3['raw_prohibited_claims_rate'])}, unsupported numbers "
                  f"{_pct(g3['unsupported_number_rate'])}, template fallback {_pct(g3['template_fallback_rate'])}, "
                  f"LLM calls per analysis {g3['llm_calls_per_analysis']:.2f}. Rejections: {g3['rejected_for']}"]
    else:
        lines += ["", "G3 (LLM outputs) not run: use --llm real."]
    return "\n".join(lines) + "\n"


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({k: _fmt(record[k]) if record[k] is not None else "" for k in FIELDS})


def versions() -> str:
    parts = []
    for package in ("scikit-learn", "lightgbm", "torch", "transformers"):
        try:
            parts.append(f"{package} {metadata.version(package)}")
        except metadata.PackageNotFoundError:
            parts.append(f"{package} not installed")
    return ", ".join(parts)


def run_evaluation(quick: bool = False, llm: str = "none", llm_delay: float = 4.0) -> Run:
    run = Run(quick, llm)
    symbols = UNIVERSE[:2] if quick else UNIVERSE
    with SessionLocal() as db:
        bars = load_universe(db, symbols)
        benchmark_bars = load_universe(db, (BENCHMARK,))[BENCHMARK]
        headlines = load_headlines(db, symbols)
    run.info["fingerprint"] = fingerprint({**bars, BENCHMARK: benchmark_bars})
    run.info["versions"] = versions()
    series = {s: to_series(s, b) for s, b in bars.items()}
    scorers = available_scorers(run)
    logger.info("RQ1 ...")
    run_rq1(run, scorers)
    logger.info("RQ2 ...")
    sentiment_days = run_rq2(run, series, headlines, scorers)
    logger.info("RQ3 ...")
    run_rq3(run, series, sentiment_days)
    logger.info("RQ4 ...")
    run_rq4(run, to_series(BENCHMARK, benchmark_bars))
    logger.info("RQ5 ...")
    run_rq5(run, bars, benchmark_bars, llm_delay)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Stock Intelligence Agent (RQ1-RQ5).")
    parser.add_argument("--quick", action="store_true", help="development run: 2 symbols, fewer replications")
    parser.add_argument("--llm", choices=("none", "real"), default="none",
                        help="real: G3, the configured LLM on 30 analyses (public market aggregates only)")
    parser.add_argument("--llm-delay", type=float, default=4.0, help="seconds between real LLM calls")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("app.market_data.cache_service", "httpx"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    started = time.perf_counter()
    run = run_evaluation(args.quick, args.llm, args.llm_delay)
    has_finbert = "F_finbert" in next(iter(run.info["rq1"].values()), {}).get("rows", {})
    if not args.quick and args.llm == "none" and has_finbert:
        out_csv, out_md = EVAL_DIR / "stocks_metrics.csv", EVAL_DIR / "stocks_metrics.md"
    else:
        label = "_".join(x for x in ("quick" if args.quick else "", f"llm_{args.llm}" if args.llm != "none" else "",
                                     "" if has_finbert else "no_finbert") if x) or "run"
        out_csv, out_md = RESULTS_DIR / f"stocks_metrics_{label}.csv", RESULTS_DIR / f"stocks_metrics_{label}.md"
    write_csv(out_csv, run.records)
    out_md.write_text(markdown_report(run), encoding="utf-8", newline="\n")
    print(f"Wrote {out_csv} and {out_md} ({len(run.records)} records) in {time.perf_counter() - started:.0f} s")
    for hypothesis, verdict, evidence in verdicts(run):
        print(f"{hypothesis:14s} {verdict:16s} {evidence}")


if __name__ == "__main__":
    main()
