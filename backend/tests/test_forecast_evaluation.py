# tests/test_forecast_evaluation.py
#
# Keeps the forecast evaluation harness honest and from silently breaking. It runs
# on a TINY panel (one user per persona, two origins, one of them with >= 12 months
# so the seasonal model and the history-length ablation run too) and checks:
#   * every method x horizon is reported, CSV and markdown are written
#   * no leakage: cutting the panel at as_of does not change a single forecast
#   * the harness's S numbers are the service's numbers (and the pure function's)
#   * the opening balance comes from the statement and equals the true balance
#   * template mode makes 0 LLM calls; a fake LLM that invents an amount is caught
# plus unit tests of the new metric functions.

import csv
import json
import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.agents.forecast_explainer import ForecastExplainer
from app.services import forecast_service
from app.services.forecasting.data_preparation import prepare_history
from app.services.forecasting.horizon import horizon_slices
from app.services.forecasting.variable_forecast import forecast_variable
from evaluation.evaluate_forecast import (
    ALL, FIXED_LABELS, HISTORY_LENGTHS, HORIZONS, OLD_DETECTOR, Ledger, default_origins, explainer, load_db,
    run_evaluation,
)
from evaluation.metrics import confusion, interval_coverage, mean_interval_width, selection_regret
from scripts.make_synthetic_panel import generate_panel

USERS = ["u01", "u09", "u16", "u24"]                        # one per persona
SHORT, LONG = date(2025, 4, 15), date(2026, 1, 15)          # 6 and 15 months of history


# --- metric formulas ---

def test_interval_coverage_and_width():
    assert interval_coverage([5, 10, 20], [0, 11, 15], [5, 12, 25]) == pytest.approx(2 / 3)   # bounds count
    assert mean_interval_width([0, 10], [4, 30]) == 12
    with pytest.raises(ValueError):
        interval_coverage([], [], [])


def test_selection_regret_against_the_best_fixed_model():
    assert selection_regret(90.0, {"B0": 120.0, "B1": 100.0, "B3": 95.0}) == (-5.0, "B3")
    assert selection_regret(100.0, {"B1": 100.0, "B0": 100.0}) == (0.0, "B0")    # ties: stable by name


def test_confusion_counts_and_f1():
    scores = confusion([True, True, False, False], [True, False, True, False])
    assert (scores["tp"], scores["fp"], scores["fn"], scores["tn"]) == (1, 1, 1, 1)
    assert scores["precision"] == scores["recall"] == scores["f1"] == 0.5


# --- origins ---

def test_default_origins_have_six_months_of_history_and_fit_the_90_day_window():
    origins = default_origins()
    assert origins[0] == date(2025, 4, 15) and origins[-1] == date(2026, 6, 15) and len(origins) == 15
    assert all(o.day == 15 for o in origins)
    assert origins[-1] + timedelta(days=max(HORIZONS)) <= date(2026, 9, 30)


# --- the harness on a tiny panel ---

class InventingChain:
    """Fake LLM that invents an amount, so the guard must replace its text."""

    def invoke(self, inputs: dict) -> str:
        return json.dumps({"summary": "You will have ₹123,456 left at the end."})


@pytest.fixture(scope="module")
def rows():
    return generate_panel(users=USERS)


@pytest.fixture(scope="module")
def result(rows, tmp_path_factory):
    out = tmp_path_factory.mktemp("forecast_eval")
    res = run_evaluation(
        rows, origins=[SHORT, LONG], llm_mode="fake",
        llm_factory=lambda: ForecastExplainer(chain=InventingChain()),
        out_csv=out / "metrics.csv", out_md=out / "metrics.md", out_predictions=out / "predictions.csv",
    )
    return res, out


def value(records, experiment, method, config, horizon, persona, metric):
    found = [r["value"] for r in records if (r["experiment"], r["method"], r["config"], r["horizon_days"],
                                              r["persona"], r["metric"]) == (experiment, method, config, horizon, persona, metric)]
    assert len(found) == 1, (experiment, method, config, horizon, persona, metric)
    return found[0]


def test_every_method_and_horizon_is_reported(result):
    res, _ = result
    records = res.records
    for horizon in HORIZONS:
        for label in FIXED_LABELS.values():
            assert value(records, "variable_spend", label, "fixed", horizon, ALL, "n") > 0
        for config in ("default", "select_window=6", "select_window=12", "interval=empirical", "interval=conformal",
                       "interval=fixed_width"):
            assert value(records, "variable_spend", "S", config, horizon, ALL, "n") == 2 * len(USERS)
        for experiment, method, config in (("total_expenses", "S", "decomposed"), ("total_expenses", "A", "no_decomposition"),
                                           ("net_cashflow", "A", "no_decomposition"), ("end_balance", "S", "decomposed")):
            assert value(records, experiment, method, config, horizon, ALL, "n") == 2 * len(USERS)
        for length in HISTORY_LENGTHS:                    # only the LONG origin has >= 12 months
            assert value(records, "history_length", "S", f"months={length}", horizon, ALL, "n") == len(USERS)
        assert value(records, "cash_crunch", "S", "lower_band", horizon, ALL, "n") == 2 * len(USERS)
        # the pre-F6.1 detector ablation runs the whole service again
        assert value(records, "total_expenses", "S", OLD_DETECTOR, horizon, ALL, "n") == 2 * len(USERS)
        assert value(records, "cash_crunch", "S", OLD_DETECTOR, horizon, ALL, "n") == 2 * len(USERS)
    # seasonal naive only exists where 12 months of history do
    assert value(records, "variable_spend", "B2", "fixed", 30, ALL, "n") == len(USERS)
    assert res.info["forecasts"] == 2 * len(USERS) * len(HORIZONS)


def test_csv_markdown_and_predictions_are_written(result):
    res, out = result
    with (out / "metrics.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(res.records)
    with (out / "predictions.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(res.predictions)
    text = (out / "metrics.md").read_text(encoding="utf-8")
    assert "Variable spending" in text and "Cash-crunch" in text and res.info["sha256"][:16] in text


def test_rates_are_between_zero_and_one(result):
    res, _ = result
    rate_metrics = {"coverage", "precision", "recall", "f1", "actual_rate", "low_confidence_rate",
                    "llm_used", "explanation_faithful"}
    for r in res.records:
        if (r["metric"] in rate_metrics or r["metric"].startswith("chosen_share_")) and not math.isnan(r["value"]):
            assert 0.0 <= r["value"] <= 1.0, r


def test_chosen_model_shares_add_up_to_one(result):
    res, _ = result
    for horizon in HORIZONS:
        total = sum(value(res.records, "selection", "S", "default", horizon, ALL, f"chosen_share_{label}")
                    for label in FIXED_LABELS.values())
        assert total == pytest.approx(1.0)


def test_no_leakage_changing_the_future_changes_no_forecast(rows, result):
    # The truth needs the rows after as_of, so they stay in the database. To prove the
    # FORECASTS never read them, every amount after as_of is doubled: the actuals must
    # change, and every single forecast must stay exactly the same.
    res, _ = result
    changed = [r if r.day <= LONG else replace(r, amount=r.amount * 2, balance=r.balance * 2) for r in rows]
    other = run_evaluation(changed, origins=[LONG])

    def forecasts(predictions):
        return sorted((p.experiment, p.method, p.config, p.horizon_days, p.user, round(p.point, 2), round(p.lower, 2),
                       round(p.upper, 2)) for p in predictions if p.as_of == LONG)

    def actuals(predictions):
        return sorted((p.experiment, p.method, p.config, p.horizon_days, p.user, round(p.actual, 2))
                      for p in predictions if p.as_of == LONG)

    assert forecasts(res.predictions) == forecasts(other.predictions)
    assert actuals(res.predictions) != actuals(other.predictions)


def test_harness_numbers_are_the_service_numbers(rows):
    db, users = load_db(rows)
    try:
        for user, user_id in users.items():
            ledger = Ledger([r for r in rows if r.user == user])
            with explainer(lambda: ForecastExplainer(chains=[])) as built:
                run = forecast_service.generate_forecast(db, user_id, 30, LONG)
            # opening balance: read from the statement, equal to the true end-of-day balance
            assert run["opening_balance_source"] == "statement"
            assert run["opening_balance"] == pytest.approx(ledger.balance_on(LONG))
            assert sum(e.llm_calls for e in built) == 0
            # the service's variable forecast is the pure function on the prepared series
            history = prepare_history(db, user_id, LONG)
            slices = horizon_slices(history.first_forecast_month, LONG + timedelta(days=1), LONG + timedelta(days=30))
            pure = forecast_variable(history.variable_series, [s.weight for s in slices])
            assert run["expected_variable_expenses"]["point"] == pytest.approx(pure.point, abs=0.01)
            assert all(m < f"{LONG:%Y-%m}" for m in history.months)          # history ends before as_of's month
    finally:
        db.close()


def test_template_runs_make_no_llm_call_and_the_guard_catches_the_fake(result):
    res, _ = result
    for horizon in HORIZONS:
        assert value(res.records, "llm", "S", "template", horizon, ALL, "llm_calls_per_forecast") == 0
    assert value(res.records, "llm", "S", "explainer", 30, ALL, "llm_calls_per_forecast") == 1
    assert value(res.records, "llm", "S", "explainer", 30, ALL, "llm_used") == 1
    assert value(res.records, "llm", "S", "explainer", 30, ALL, "explanation_faithful") == 0


def test_recurrence_detection_finds_every_scheduled_item(result):
    res, _ = result
    assert value(res.records, "recurrence", "detector", "default", "", ALL, "recall") == 1.0


def test_frequency_check_raises_detection_precision(result):
    res, _ = result
    new = value(res.records, "recurrence", "detector", "default", "", ALL, "precision")
    old = value(res.records, "recurrence", "detector", OLD_DETECTOR, "", ALL, "precision")
    assert new > old


def test_student_t_intervals_cover_more_than_plain_percentiles(result):
    res, _ = result
    for horizon in HORIZONS:
        default = value(res.records, "variable_spend", "S", "default", horizon, ALL, "width")
        assert default > value(res.records, "variable_spend", "S", "interval=empirical", horizon, ALL, "width")
