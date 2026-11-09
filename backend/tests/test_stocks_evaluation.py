# tests/test_stocks_evaluation.py
#
# Purpose: tests for the Phase 7B evaluation HARNESS (evaluation/stocks_*.py), above all the leakage
# controls pre-registered in evaluation/stocks_rules.py: features only from the past (L1), walk-forward
# order (L2), a fresh model and scaler per fold (L3). Also the metrics, the dataset parsers (tiny
# synthetic fixtures built here, never the real datasets), the lead-lag statistics, the SIP helpers and
# the fallback simulation. No network, no database except SQLite in memory.

import csv
import io
import math
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from evaluation import stocks_direction as direction
from evaluation import stocks_leadlag as leadlag
from evaluation import stocks_robustness as robustness
from evaluation import stocks_sentiment_eval as rq1
from evaluation import stocks_sip as sip
from evaluation import stocks_rules as rules
from evaluation.stocks_data import ar1_walk, random_walk, series_bars


# ------------------------------------------------------------------ pre-registration

def test_rules_constants_are_consistent():
    assert len(rules.UNIVERSE) == 10 and len(set(rules.UNIVERSE)) == 10
    assert rules.RQ2_ALPHA == pytest.approx(0.0025)
    assert rules.RQ3_POOLED_ALPHA == pytest.approx(0.025)
    assert rules.RQ3_SYMBOL_ALPHA == pytest.approx(0.0025)
    assert rules.RQ3_SENTIMENT_MIN_DAYS == 525
    assert {d.role for d in rules.RQ1_DATASETS} == {"primary", "contaminated"}
    assert set(rules.RQ5_SCENARIOS) == set(robustness.SCRIPTS)


def test_bonferroni_rejects_bad_input():
    with pytest.raises(ValueError):
        rules.bonferroni(0.05, 0)


def test_guard_cases_file_is_well_formed():
    cases = robustness.load_guard_cases()
    assert len(cases) == 85 and len({c.case_id for c in cases}) == 85
    assert sum(c.prohibited for c in cases) == 40
    assert all(c.prohibited == (c.kind in ("advice", "prediction")) for c in cases)


# ------------------------------------------------------------------ RQ3 leakage controls

def _closes(n: int = 400, seed: int = 7) -> list[float]:
    return random_walk("T", n, seed).closes


def test_features_at_t_ignore_every_later_price():          # L1
    closes = _closes()
    original = {r.index: r.features for r in direction.build_rows(closes)}
    t = 300
    changed = closes[:t + 1] + [c * 3.0 for c in closes[t + 1:]]       # rewrite the whole future
    altered = {r.index: r.features for r in direction.build_rows(changed)}
    for index in range(direction.WARMUP - 1, t + 1):
        if index in original and index in altered:
            assert altered[index] == original[index]


def test_label_and_next_return_use_the_next_close_only():
    closes = _closes()
    for row in direction.build_rows(closes)[:50]:
        assert row.next_return == pytest.approx(closes[row.index + 1] / closes[row.index] - 1)
        assert row.label == (1 if closes[row.index + 1] > closes[row.index] else 0)


def test_build_rows_drops_flat_days_and_short_series():
    closes = _closes(260)
    closes[250] = closes[249]                     # an exactly flat next day for t = 249
    assert 249 not in {r.index for r in direction.build_rows(closes)}
    assert direction.build_rows(_closes(150)) == []


class RecordingModel:
    """Remembers what it was trained on; predicts 'up' for everything."""
    fits: list[list[list[float]]] = []

    def fit(self, x, y):
        RecordingModel.fits.append(x)
        self.mean = sum(row[0] for row in x) / len(x)
        return self

    def predict(self, x):
        return [1] * len(x)


def test_walk_forward_trains_only_on_the_past_with_a_fresh_model_per_fold():   # L2 + L3
    RecordingModel.fits = []
    rows = direction.build_rows(_closes(900))
    instances = []

    def factory():
        model = RecordingModel()
        instances.append(model)
        return model

    result = direction.walk_forward("T", rows, {"REC": factory}, initial_train=300, block=50)
    assert result.folds == len(instances) == len(RecordingModel.fits) > 1
    assert len({id(m) for m in instances}) == len(instances)          # a new model every fold
    for fold, x in enumerate(RecordingModel.fits):
        assert len(x) == 300 + 50 * fold                               # expanding window
    seen = [r.index for r in result.test_rows]
    assert seen == sorted(seen) and len(seen) == len(set(seen))        # never shuffled, no repeats
    assert seen[0] == rows[300].index


def test_lr_pipeline_scales_inside_the_fold():                         # L3 with the real sklearn pipeline
    pytest.importorskip("sklearn")
    rows = direction.build_rows(_closes(900))
    train = rows[:300]
    model = direction.make_lr()
    model.fit([list(r.features) for r in train], [r.label for r in train])
    scaler = model.named_steps["scale"]
    expected = sum(r.features[0] for r in train) / len(train)
    assert scaler.mean_[0] == pytest.approx(expected)                  # fitted on the 300 training rows only


def test_baselines():
    rows = direction.build_rows(_closes())
    row = rows[0]
    assert direction.baseline_signal("B_up", row) == 1
    assert direction.baseline_signal("B_prev", row) == (1 if row.features[0] > 0 else 0)
    assert direction.baseline_signal("B_mom5", row) == (1 if row.features[7] > 0 else 0)
    with pytest.raises(ValueError):
        direction.baseline_signal("B_magic", row)


def test_strategy_costs_and_buy_and_hold():
    returns = [0.01, -0.02, 0.03, 0.0]
    hold = direction.strategy(returns, [1, 1, 1, 1], cost_bps=10)
    assert hold.trades == 1
    assert hold.total_return == pytest.approx(0.999 * 1.01 * 0.98 * 1.03 - 1)
    flat_then_in = direction.strategy(returns, [0, 0, 1, 0], cost_bps=0)
    assert flat_then_in.total_return == pytest.approx(0.03) and flat_then_in.trades == 2
    assert flat_then_in.exposure == pytest.approx(0.25)
    assert hold.max_drawdown == pytest.approx(0.98 - 1)
    with pytest.raises(ValueError):
        direction.strategy([0.01], [1, 0], 10)


def test_mcnemar_counts_only_disagreements():
    rows = direction.build_rows(_closes())[:6]
    truth = [r.label for r in rows]
    model = truth[:]                                   # always right
    baseline = [1 - t for t in truth]                  # always wrong
    p, b, c = direction.mcnemar_p_greater(rows, model, baseline)
    assert (b, c) == (6, 0) and p == pytest.approx(0.5 ** 6)
    assert direction.mcnemar_p_greater(rows, model, model)[0] == 1.0


def test_placebo_band_and_synthetic_series_are_seeded():
    lo, hi = direction.binomial_band(10000)
    assert lo == pytest.approx(0.5 - 2.58 * 0.005) and hi == pytest.approx(0.5 + 2.58 * 0.005)
    assert random_walk("A", 50, 1).closes == random_walk("A", 50, 1).closes
    assert ar1_walk("A", 50, 1).closes != random_walk("A", 50, 1).closes
    walk = ar1_walk("A", 3000, 3, phi=0.5)
    r = [walk.closes[i] / walk.closes[i - 1] - 1 for i in range(1, len(walk.closes))]
    assert leadlag._pearson(r[:-1], r[1:]) > 0.4                        # the planted signal is there


# ------------------------------------------------------------------ RQ1

def _twitter_bytes(rows: list[tuple[str, str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["text", "label"])
    writer.writerows(rows)
    return out.getvalue().encode()


def test_parse_twitter_maps_labels():
    examples = rq1.parse_twitter_csv(_twitter_bytes([("Stock up", "1"), ("Stock down", "0"), ("Meh", "2"), ("x", "9")]))
    assert [e.label for e in examples] == ["positive", "negative", "neutral"]


def test_parse_phrasebank_from_a_tiny_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(rq1.PHRASEBANK_MEMBER, "Sales rose .@positive\nNo change@neutral\nbad line\n".encode("latin-1"))
    examples = rq1.parse_phrasebank(buffer.getvalue())
    assert [(e.text, e.label) for e in examples] == [("Sales rose .", "positive"), ("No change", "neutral")]


def test_stratified_split_is_seeded_and_stratified():
    examples = [rq1.Example(f"s{i}", label) for i, label in enumerate(["positive"] * 50 + ["negative"] * 30 + ["neutral"] * 20)]
    train, test = rq1.stratified_split(examples, 0.2, seed=1)
    assert len(test) == 20 and sum(e.label == "positive" for e in test) == 10
    assert rq1.stratified_split(examples, 0.2, seed=1) == (train, test)
    assert not {e.text for e in train} & {e.text for e in test}


def test_duplicates_between_train_and_test_are_dropped():
    train = [rq1.Example("Profit  ROSE", "positive")]
    test = [rq1.Example("profit rose", "positive"), rq1.Example("loss widened", "negative")]
    kept, dropped = rq1.drop_duplicates(train, test)
    assert dropped == 1 and [e.text for e in kept] == ["loss widened"]


def test_metrics_and_macro_f1():
    gold = ["positive", "negative", "neutral", "neutral"]
    assert rq1.classification_metrics(gold, gold)["macro_f1"] == 1.0
    predicted = ["positive", "neutral", "neutral", "neutral"]
    m = rq1.classification_metrics(gold, predicted)
    assert m["accuracy"] == 0.75 and m["recall_negative"] == 0.0
    assert m["macro_f1"] == pytest.approx((1 + 0 + 0.8) / 3)
    assert sum(rq1.confusion(gold, predicted).values()) == 4


def test_bootstrap_ci_brackets_the_point_and_paired_difference_of_equal_models_is_zero():
    gold = ["positive", "negative", "neutral"] * 30
    predicted = gold[:60] + ["neutral"] * 30
    lo, hi = rq1.bootstrap_macro_f1(gold, predicted, resamples=200)
    assert lo <= rq1.macro_f1(gold, predicted) <= hi
    assert rq1.bootstrap_macro_f1(gold, predicted, predicted, resamples=50) == (0.0, 0.0)


def test_label_rules_and_majority_and_tfidf():
    from app.sentiment.base import SentimentProbabilities
    probs = [SentimentProbabilities(0.5, 0.4, 0.1), SentimentProbabilities(0.1, 0.2, 0.7)]
    assert rq1.app_labels(probs) == ["neutral", "neutral"]               # score 0.1 is inside +/- 0.15
    assert rq1.argmax_labels(probs) == ["positive", "neutral"]
    train = [rq1.Example(t, l) for t, l in [("profit rises strongly", "positive"), ("profit rises again", "positive"),
                                             ("loss widens badly", "negative"), ("loss widens again", "negative"),
                                             ("board meets today", "neutral"), ("board meets again", "neutral")]]
    split = rq1.SentimentSplit("tiny", "synthetic", train, [rq1.Example("profit rises", "positive")])
    assert rq1.majority_predict(split) == ["positive"]
    pytest.importorskip("sklearn")
    assert rq1.tfidf_lr_predict(split) == ["positive"]


def test_load_splits_falls_back_to_the_labelled_synthetic_set(tmp_path):
    splits, notes = rq1.load_splits(tmp_path)
    assert [s.source for s in splits] == ["synthetic"]
    assert any("SYNTHETIC" in note for note in notes)


def test_load_splits_reads_present_files(tmp_path):
    folder = tmp_path / "twitter_fin"
    folder.mkdir()
    (folder / "sent_train.csv").write_bytes(_twitter_bytes([("up a lot", "1"), ("down a lot", "0")]))
    (folder / "sent_valid.csv").write_bytes(_twitter_bytes([("up a lot", "1"), ("flat day", "2")]))
    splits, notes = rq1.load_splits(tmp_path)
    assert [s.key for s in splits] == ["twitter_fin"] and splits[0].dropped_duplicates == 1
    assert len(splits[0].sha256["sent_valid.csv"]) == 64
    assert any("phrasebank" in note for note in notes)


# ------------------------------------------------------------------ RQ2

IST_CLOSE_UTC = 10, 30            # 16:00 IST = 10:30 UTC


def test_headline_after_the_close_counts_for_the_next_trading_day():
    days = [date(2026, 10, 29), date(2026, 10, 30), date(2026, 11, 2)]      # Thu, Fri, Mon
    before = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)             # 15:30 IST Thursday
    after = datetime(2026, 10, 29, 11, 0, tzinfo=timezone.utc)              # 16:30 IST Thursday
    weekend = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)             # Saturday
    late = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)                 # after the last day
    assert leadlag.assign_days([before, after, weekend, late], days) == [days[0], days[1], days[2], None]


def test_daily_sentiment_uses_only_news_known_by_each_day():
    days = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)]       # Mon-Fri
    published = [datetime(2026, 10, 26, 5, tzinfo=timezone.utc), datetime(2026, 10, 28, 5, tzinfo=timezone.utc)]
    probs = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    daily = leadlag.daily_sentiment(published, probs, days, half_life_days=3.0)
    assert set(daily) == {days[0], days[2]}                  # sentiment days = days with new headlines
    assert daily[days[0]] == pytest.approx(1.0)              # only the first headline was known on Monday
    assert -1 < daily[days[2]] < 0                           # Wednesday mixes both, the newer one weighs more


def test_spearman_ranks_and_permutation():
    assert leadlag.ranks([10, 20, 20, 5]) == [2.0, 3.5, 3.5, 1.0]
    assert leadlag.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert leadlag.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    x = list(range(40))
    assert leadlag.permutation_p(x, x, 200) == pytest.approx(1 / 201)
    assert leadlag.permutation_p(x, x, 200) == leadlag.permutation_p(x, x, 200)       # seeded


def test_forward_returns_and_power():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(4)]
    assert leadlag.forward_returns(days, [100, 110, 121, 100], 2) == {days[0]: pytest.approx(0.21),
                                                                      days[1]: pytest.approx(100 / 110 - 1)}
    assert leadlag.normal_two_sided_p(0.0, 100) == pytest.approx(1.0)
    assert leadlag.power(0.0, 100, 0.0025, 200) < 0.05
    assert leadlag.power(0.6, 100, 0.0025, 100) > 0.95


# ------------------------------------------------------------------ RQ4

def test_month_end_returns_and_moments():
    dates = [date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 27), date(2026, 3, 31)]
    months = sip.month_end_closes(dates, [100, 101, 110, 99])
    assert months == [("2026-01", 100), ("2026-02", 110), ("2026-03", 99)]
    assert sip.monthly_returns(months) == pytest.approx([0.10, -0.10])
    mean_annual, vol = sip.matched_normal_inputs([0.01, 0.03])
    assert mean_annual == pytest.approx(1.02 ** 12 - 1)
    assert vol == pytest.approx(math.sqrt(0.0002) * math.sqrt(12))
    skew, _ = sip.shape([0.0, 0.0, 0.0, 0.3])
    assert skew > 0


def test_goal_value_matches_a_constant_return_path():
    i = 1.10 ** (1 / 12) - 1
    assert sip.goal_value(1000, 2, 0.10) == pytest.approx(sip.realised_sip([i] * 24, 1000))
    assert len(sip.rolling_realised([0.01] * 70, 1000, 5)) == 11


def test_compare_runs_both_methods_with_the_same_goal():
    returns = [0.02, -0.01, 0.015, -0.03, 0.04, 0.01] * 10
    out = sip.compare(returns, 1000, [1], [3], paths=200, seed=1, goal_annual_return=0.1)
    assert [c.method for c in out] == ["normal", "bootstrap_b3"]
    assert out[0].goal == out[1].goal and out[0].result.n_paths == 200


# ------------------------------------------------------------------ RQ5

def test_weekdays_between_and_latest_final_date():
    assert robustness.weekdays_between(date(2026, 10, 30), date(2026, 11, 2)) == 1       # Fri -> Mon
    assert robustness.weekdays_between(date(2026, 11, 2), date(2026, 11, 2)) == 0
    morning = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)                           # Mon 09:30 IST
    assert robustness.latest_final_date(morning) == date(2026, 10, 30)


def test_simulation_healthy_serves_everything_and_cold_outage_serves_nothing():
    healthy = robustness.simulate("S0_healthy", n_symbols=2, n_days=3, per_day=2, ttl_hours=24, cooldown_minutes=15)
    assert healthy.requests == 12 and healthy.served == 12 and healthy.errors == 0
    assert healthy.provider_calls <= healthy.requests
    assert max(healthy.staleness) == 0
    cold = robustness.simulate("S5_cold_outage", n_symbols=2, n_days=3, per_day=2, ttl_hours=24, cooldown_minutes=15)
    assert cold.served == 0 and cold.errors == 12


def test_simulation_outage_after_day_one_is_served_stale():
    stats = robustness.simulate("S4_down_after_day1", n_symbols=1, n_days=4, per_day=2, ttl_hours=24,
                                cooldown_minutes=15)
    assert stats.served == stats.requests
    assert stats.by_status.get("stale", 0) > 0 and max(stats.staleness) >= 1


def test_guard_metrics_counts():
    cases = [robustness.GuardCase("a", "advice", True, "buy"), robustness.GuardCase("b", "neutral", False, "fell"),
             robustness.GuardCase("c", "hard_neutral", False, "buyback")]
    metrics, wrong = robustness.guard_metrics(cases, lambda text: text.startswith("buy"))
    assert metrics["precision"] == 0.5 and metrics["recall"] == 1.0
    assert metrics["false_positive_rate_hard_neutral"] == 1.0 and wrong == ["c"]


def test_analysis_result_matches_the_service_shape():
    bars = series_bars(random_walk("SYNTH.NS", 400, 3))
    bench = series_bars(random_walk("^NSEI", 400, 4))
    result = robustness.analysis_result("SYNTH.NS", bars, "^NSEI", bench, bars[-1].bar_date)
    assert result["indicators"]["bars_used"] > 252 and result["sentiment"] is None
    assert result["indicators"]["beta"] is not None
