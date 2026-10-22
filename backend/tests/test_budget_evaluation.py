# tests/test_budget_evaluation.py
#
# Keeps the budget evaluation harness from silently breaking: runs it offline
# on the synthetic history and checks the CSV, values known by construction,
# and the B2 path with a FAKE LLM (no network). Also unit-tests the formulas.

import csv
import json
import math

import pytest

from app.agents.budget_planner import BudgetPlannerAgent
from evaluation.evaluate_budget import CONFIGS, MEAN, PLAN, TEST_MONTHS, run_evaluation
from evaluation.metrics import adherence_rate, mae, mape, override_stats, overspend, unused_limit
from scripts.make_synthetic_history import PERSONAS, generate_history


# --- metric formulas ---

def test_mae_counts_unbudgeted_spending_and_unused_limits():
    assert mae({"Food": 100, "Rent": 1000}, {"Food": 150, "Shopping": 50}) == pytest.approx((50 + 1000 + 50) / 3)


def test_mape_skips_categories_with_no_actual_spending():
    value, skipped = mape({"Food": 90, "Rent": 500}, {"Food": 100})
    assert value == pytest.approx(0.10) and skipped == 1
    assert mape({"Food": 90}, {}) == (None, 1)


def test_adherence_overspend_and_unused_limit():
    limits, actuals = {"Food": 100, "Rent": 1000}, {"Food": 150, "Rent": 900, "Shopping": 50}
    assert adherence_rate(limits, actuals) == pytest.approx(1 / 3)      # only Rent is within its limit
    assert overspend(limits, actuals) == 100                             # 50 over on Food + 50 unbudgeted
    assert unused_limit(limits, actuals) == 100                          # Rent left 100 unspent


def test_override_stats():
    rows = [(100.0, 150.0, "user"), (200.0, 200.0, "agent"), (0.0, 50.0, "user"), (80.0, 80.0, "agent")]
    rate, size = override_stats(rows)
    assert rate == 0.5 and size == pytest.approx(0.5)                    # an agent limit of 0 is not sized
    assert override_stats([]) == (0.0, None)


# --- the harness ---

class FakeChain:
    """Fake LLM that invents one amount in its summary, so the guard must replace it."""

    def invoke(self, inputs: dict) -> str:
        categories = [c["category"] for c in json.loads(inputs["facts_json"])["categories"]]
        return json.dumps({
            "summary": "Save ₹123,456 more every month.",                 # not a plan number
            "reasons": {c: "Based on your history." for c in categories},
        })


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("budget_eval")
    records = run_evaluation(
        generate_history(), llm_mode="fake",
        llm_factory=lambda: BudgetPlannerAgent(chain=FakeChain()),
        out_csv=out / "metrics.csv", out_md=out / "metrics.md",
    )
    return records, out


def value(records, method, config, persona, month, metric, category=""):
    found = [
        r["value"] for r in records
        if (r["method"], r["config"], r["persona"], r["eval_month"], r["metric"], r["category"])
        == (method, config, persona, month, metric, category)
    ]
    assert len(found) == 1, (method, config, persona, month, metric)
    return found[0]


def test_every_config_and_persona_is_reported(run):
    records, _ = run
    reported = {(r["method"], r["config"], r["persona"]) for r in records}
    for config in CONFIGS:
        for persona in PERSONAS:
            assert (config.method, config.name, persona.name) in reported
    assert ("B2", "llm", "frugal") in reported


def test_csv_and_markdown_are_written(run):
    records, out = run
    with (out / "metrics.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(records)
    assert "Main comparison" in (out / "metrics.md").read_text(encoding="utf-8")


def test_rates_are_between_zero_and_one(run):
    records, _ = run
    rates = {"adherence_rate", "override_rate", "feasible", "unfaithful_text_rate"}
    for r in records:
        if r["metric"] in rates and not math.isnan(r["value"]):
            assert 0.0 <= r["value"] <= 1.0, r


def test_plans_never_exceed_spendable(run):
    records, _ = run
    for config in CONFIGS:
        for persona in PERSONAS:
            total = value(records, config.method, config.name, persona.name, PLAN, "total_limit")
            assert total <= value(records, config.method, config.name, persona.name, PLAN, "spendable") + 0.005


def test_history_plan_beats_the_baseline_on_mae(run):
    records, _ = run
    for p in PERSONAS:
        assert value(records, "B1", "default", p.name, MEAN, "mae") < value(records, "B0", "baseline", p.name, MEAN, "mae")


def test_counting_investments_distorts_the_history(run):
    records, _ = run
    for p in PERSONAS:
        assert value(records, "B1", "investments_as_spending", p.name, PLAN, "budget_distortion_pct") > 0
        assert value(records, "B1", "investments_as_spending", p.name, PLAN, "limit_on_non_spending") > 0


def test_income_comes_from_the_suggestion_and_excludes_the_refund(run):
    records, _ = run
    # frugal: salary 55,000 x 3 + June interest 450 (refund excluded) = 165,450 / 3 = 55,150; 25% saved
    assert value(records, "B0", "baseline", "frugal", PLAN, "spendable") == pytest.approx(41362.50)


def test_b2_guard_replaces_the_invented_amount(run):
    records, _ = run
    for p in PERSONAS:
        assert value(records, "B2", "llm", p.name, PLAN, "llm_calls_per_plan") == 1
        assert value(records, "B2", "llm", p.name, PLAN, "explanation_faithful") == 0
        assert value(records, "B2", "llm", p.name, PLAN, "texts_replaced_by_guard") == 1
        for month in TEST_MONTHS:      # same numbers as B1: the LLM only explains
            assert value(records, "B2", "llm", p.name, month, "mae") == value(records, "B1", "default", p.name, month, "mae")


def test_b1_makes_no_llm_call(run):
    records, _ = run
    assert all(value(records, "B1", "default", p.name, PLAN, "llm_calls_per_plan") == 0 for p in PERSONAS)
