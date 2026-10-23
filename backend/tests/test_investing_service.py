# tests/test_investing_service.py
#
# Purpose: investment_service (Phase 6A-3) on a seeded SQLite user.
# The forecast is "stubbed" by saving a forecast_runs row with chosen numbers:
# the service only ever READS saved runs, so no forecast maths runs here.
# Essential spending comes from Phase 4 budget rows. No LLM is involved yet.

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.agents.investment_explainer import InvestmentExplainer
from app.core.config import settings
from app.core.disclaimers import INVESTING_DISCLAIMER
from app.models.budget import Budget
from app.models.forecast import ForecastRun
from app.models.investment import InvestmentPlan
from app.schemas.investment import HoldingIn, InvestmentProfileUpdate, SimulateRequest
from app.services import budget_service
from app.services import investment_service as svc
from app.services.forecast_service import latest_run_row
from app.services.investing.claims_guard import find_prohibited_claims
from app.services.investing.sip_planner import required_sip

AS_OF = date(2026, 10, 23)
MONTH = "2026-10"
BRAVE, CAUTIOUS = (5, 5, 5), (1, 1, 1)


@pytest.fixture(autouse=True)
def fast_simulation_and_no_llm(monkeypatch):
    """Fewer Monte Carlo paths keep the suite fast (same maths); no test may reach a real LLM."""
    monkeypatch.setattr(settings, "INVEST_MC_PATHS", 200)
    monkeypatch.setattr(svc, "_build_explainer", lambda: InvestmentExplainer(find_prohibited_claims, chains=[]))


class FakeChain:
    """Fake LLM: returns a canned answer (or raises) and records every request."""

    def __init__(self, answer: str | Exception):
        self.answer = answer
        self.calls: list[dict] = []

    def invoke(self, inputs: dict) -> str:
        self.calls.append(inputs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def use_fake_llm(monkeypatch, summary: str) -> FakeChain:
    chain = FakeChain(json.dumps({"summary": summary}, ensure_ascii=False))
    monkeypatch.setattr(svc, "_build_explainer", lambda: InvestmentExplainer(find_prohibited_claims, chain=chain))
    return chain


# --- Helpers ---

def save_profile(db, answers=BRAVE, liquid="200000", sip="0", stability=5, dependents=0, horizon=15, user_id=1):
    data = InvestmentProfileUpdate(
        answers=dict(zip(("reaction_to_20pct_drop", "investing_experience", "return_vs_safety"), answers)),
        liquid_savings=Decimal(liquid), existing_monthly_sip=Decimal(sip), income_stability=stability,
        dependents=dependents, horizon_years=horizon,
    )
    return svc.upsert_profile(db, user_id, data, AS_OF)


def add_budget(db, category, amount, user_id=1, month=MONTH):
    value = Decimal(str(amount))
    db.add(Budget(user_id=user_id, month=month, category=category, limit_amount=value, agent_limit_amount=value))
    db.commit()


def essentials_20000(db, user_id=1):
    """Rent 15,000 + groceries 5,000 = 20,000 a month of essential spending."""
    add_budget(db, "Housing & Rent", 15000, user_id)
    add_budget(db, "Groceries", 5000, user_id)


def add_forecast(db, user_id=1, as_of=date(2026, 10, 22), horizon=30, income=60000, fixed=20000,
                 variable=15000, variable_upper=20000, investments=3000) -> ForecastRun:
    """A saved forecast with chosen totals (only the fields the advisor reads matter)."""
    def money(value):
        return Decimal(str(value))
    net = income - fixed - variable - investments
    run = ForecastRun(
        user_id=user_id, as_of=as_of, horizon_days=horizon, model_name="moving_average_3", history_months=4,
        low_confidence=False, opening_balance=money(50000), opening_balance_source="statement",
        income_source="recurring", expected_income=money(income), expected_fixed_expenses=money(fixed),
        expected_variable_expenses=money(variable), expected_variable_expenses_lower=money(variable * 0.8),
        expected_variable_expenses_upper=money(variable_upper), expected_investments=money(investments),
        net_cashflow=money(net), projected_end_balance=money(50000 + net),
        projected_end_balance_lower=money(50000 + net - (variable_upper - variable)),
        projected_end_balance_upper=money(50000 + net + variable * 0.2), summary="stub", llm_used=False,
        trajectory=[], recurring_items=[], budget_warnings=[], model_scores=[],
    )
    db.add(run)
    db.commit()
    return run


# The standard forecast's lower-band monthly surplus: (60,000 - 20,000 - 20,000) x 30.44 / 30
STANDARD_LOW_SURPLUS = 20293.33


# --- Surplus (spec 14.1) ---

def test_low_surplus_uses_the_upper_variable_band_and_is_scaled_to_one_month(db):
    assert svc.low_surplus_from_run(add_forecast(db)) == Decimal("20293.33")
    # The same monthly figures over 90 days give the same monthly surplus
    ninety = add_forecast(db, horizon=90, income=180000, fixed=60000, variable=45000, variable_upper=60000)
    assert svc.low_surplus_from_run(ninety) == Decimal("20293.33")


def test_plan_uses_the_latest_30_day_run_even_when_a_90_day_run_is_newer(db):
    save_profile(db)
    run30 = add_forecast(db)
    add_forecast(db, horizon=90, income=180000, fixed=60000, variable=45000, variable_upper=90000)
    plan = svc.generate_plan(db, 1, AS_OF)
    assert plan["forecast_run_id"] == run30.id
    assert plan["surplus"] == {"monthly_low": STANDARD_LOW_SURPLUS, "source": "forecast", "forecast_as_of": "2026-10-22"}


def test_latest_run_row_returns_none_instead_of_404(db):
    assert latest_run_row(db, 1, 30) is None


def test_missing_forecast_falls_back_to_income_minus_budgets_with_a_warning(db):
    save_profile(db)
    budget_service.upsert_profile(db, 1, Decimal("50000"), Decimal("20"))
    essentials_20000(db)
    add_budget(db, "Shopping", 10000)
    plan = svc.generate_plan(db, 1, AS_OF)
    assert "forecast_missing" in plan["warnings"] and "surplus_unknown" not in plan["warnings"]
    assert plan["surplus"]["source"] == "budget" and plan["surplus"]["monthly_low"] == 20000
    assert plan["forecast_run_id"] is None


def test_no_forecast_and_no_budget_means_no_sip_and_an_explicit_warning(db):
    save_profile(db)
    plan = svc.generate_plan(db, 1, AS_OF)
    assert {"forecast_missing", "surplus_unknown"} <= set(plan["warnings"])
    assert plan["sip"]["recommended_total"] == 0


def test_old_forecast_is_flagged_stale(db):
    save_profile(db)
    add_forecast(db, as_of=date(2026, 9, 1))           # 52 days before AS_OF
    assert "forecast_stale" in svc.generate_plan(db, 1, AS_OF)["warnings"]


def test_fresh_forecast_has_no_forecast_warning(db):
    save_profile(db)
    essentials_20000(db)
    add_forecast(db)
    warnings = svc.generate_plan(db, 1, AS_OF)["warnings"]
    assert not {"forecast_missing", "forecast_stale", "surplus_unknown"} & set(warnings)


# --- Risk profile: tolerance vs capacity ---

def test_brave_user_without_an_emergency_fund_is_limited_by_capacity(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db, answers=BRAVE, liquid="20000")    # 1 month of essentials, everything else strong
    risk = svc.generate_plan(db, 1, AS_OF)["risk_profile"]
    assert risk["tolerance_category"] == "aggressive"
    assert risk["capacity_score"] == 40.0              # capped: under 3 months of emergency fund
    assert risk["category"] == "moderate" and risk["limiting_factor"] == "capacity"


def test_cautious_user_with_strong_finances_is_limited_by_tolerance(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db, answers=CAUTIOUS, liquid="200000", horizon=12)   # 10 months of essentials
    risk = svc.generate_plan(db, 1, AS_OF)["risk_profile"]
    assert risk["capacity_category"] == "aggressive"
    assert risk["category"] == "conservative" and risk["limiting_factor"] == "tolerance"


def test_unknown_essentials_count_as_no_emergency_fund(db):
    save_profile(db, liquid="500000")                  # lots of savings, but nothing to measure them against
    plan = svc.generate_plan(db, 1, AS_OF)
    assert "essentials_unknown" in plan["warnings"]
    assert plan["emergency_fund"]["months_covered"] == 0
    assert plan["risk_profile"]["capacity_score"] <= 40


# --- SIP ---

def test_no_emergency_fund_reserves_a_monthly_top_up_before_investing(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db, liquid="0", sip="2000")
    plan = svc.generate_plan(db, 1, AS_OF)
    assert plan["emergency_fund"]["gap"] == 120000      # 6 months x 20,000
    assert plan["sip"]["emergency_topup"] == 10000      # filled over 12 months
    # 20,293.33 x 0.8 - 10,000
    assert plan["sip"]["capacity_total"] == pytest.approx(6234.66, abs=0.01)
    assert plan["sip"]["change_vs_existing"] == pytest.approx(4234.66, abs=0.01)


def test_goal_below_capacity_limits_the_sip_to_what_the_goal_needs(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db)
    plan = svc.generate_plan(db, 1, AS_OF, goal_amount=Decimal("100000"), goal_years=5)
    needed = required_sip(100000, plan["portfolio"]["expected_return"], 5)
    assert plan["sip"]["limited_by"] == "target"
    assert plan["sip"]["recommended_total"] == pytest.approx(needed, abs=0.01)
    assert plan["projection_recommended"]["probability_of_goal"] is not None
    assert plan["projection_recommended"]["months_to_goal"] is not None


# --- Allocation ---

def test_short_profile_horizon_caps_equity(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db, horizon=2)
    assert svc.generate_plan(db, 1, AS_OF)["target_allocation"]["equity"] == 0.20


def test_goal_years_replace_the_profile_horizon(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db, horizon=15)
    plan = svc.generate_plan(db, 1, AS_OF, goal_amount=Decimal("300000"), goal_years=2)
    assert plan["goal"]["years"] == 2 and plan["target_allocation"]["equity"] == 0.20
    assert plan["projection_recommended"]["years"] == 2


def test_holdings_drive_the_current_allocation_and_rebalancing(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db, answers=CAUTIOUS)                 # conservative: 25% equity target
    svc.replace_holdings(db, 1, [HoldingIn(asset_class="equity", amount=Decimal("90000")),
                                 HoldingIn(asset_class="debt", amount=Decimal("10000"))], AS_OF)
    plan = svc.generate_plan(db, 1, AS_OF)
    assert plan["current_allocation"]["equity"] == 0.9
    equity = next(row for row in plan["rebalance"] if row["asset_class"] == "equity")
    assert equity["action"] == "reduce" and equity["amount_change"] == -65000


def test_missing_holdings_are_treated_as_empty(db):
    save_profile(db)
    plan = svc.generate_plan(db, 1, AS_OF)
    assert "no_holdings" in plan["warnings"]
    assert all(row["action"] == "hold" for row in plan["rebalance"])


# --- Profile and holdings ---

def test_profile_is_404_until_saved_then_one_row_is_updated(db):
    with pytest.raises(HTTPException) as error:
        svc.get_profile(db, 1)
    assert error.value.status_code == 404
    save_profile(db, answers=CAUTIOUS)
    saved = save_profile(db, answers=BRAVE)
    assert svc.get_profile(db, 1)["risk_profile"] == saved["risk_profile"]
    assert saved["risk_profile"]["tolerance_score"] == 100.0


def test_replace_holdings_replaces_the_list_and_an_empty_list_clears_it(db):
    svc.replace_holdings(db, 1, [HoldingIn(asset_class="equity", amount=Decimal("60000")),
                                 HoldingIn(asset_class="debt", amount=Decimal("40000"))], AS_OF)
    holdings = svc.get_holdings(db, 1)
    assert holdings["total"] == 100000
    assert holdings["weights_by_class"] == {"equity": 0.6, "debt": 0.4, "gold": 0.0, "cash": 0.0}
    assert all(row["as_of"] == AS_OF for row in holdings["holdings"])
    svc.replace_holdings(db, 1, [HoldingIn(asset_class="gold", amount=Decimal("5000"))], AS_OF)
    assert [row["asset_class"] for row in svc.get_holdings(db, 1)["holdings"]] == ["gold"]
    assert svc.replace_holdings(db, 1, [], AS_OF)["holdings"] == []


# --- Storage, history, reproducibility ---

def test_plan_without_a_profile_is_409(db):
    with pytest.raises(HTTPException) as error:
        svc.generate_plan(db, 1, AS_OF)
    assert error.value.status_code == 409


def test_plan_is_stored_with_the_template_and_research_log_when_no_llm_is_configured(db):
    save_profile(db)
    run = add_forecast(db)
    plan = svc.generate_plan(db, 1, AS_OF)
    row = db.get(InvestmentPlan, plan["id"])
    assert row.explanation.endswith(INVESTING_DISCLAIMER) and "limit" in row.explanation
    assert (row.llm_used, row.explanation_faithful, row.claims_compliant) == (False, None, None)
    assert (row.llm_calls, row.raw_prohibited_claims, row.explanation_source) == (0, 0, "template")
    assert row.engine_version == svc.ENGINE_VERSION
    assert row.inputs_json["forecast_run_id"] == run.id and row.inputs_json["detected_monthly_sip"] == 3044.0
    assert row.assumptions_json["mc_seed"] == settings.INVEST_MC_SEED
    assert plan["assumptions"]["mc_paths"] == 200


def test_same_inputs_give_the_same_plan(db):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db)
    first = svc.generate_plan(db, 1, AS_OF, goal_amount=Decimal("1000000"), goal_years=8)
    second = svc.generate_plan(db, 1, AS_OF, goal_amount=Decimal("1000000"), goal_years=8)
    ignore = {"id", "created_at"}
    assert {k: v for k, v in first.items() if k not in ignore} == {k: v for k, v in second.items() if k not in ignore}


def test_latest_and_history_are_newest_first(db):
    save_profile(db)
    first = svc.generate_plan(db, 1, AS_OF)
    second = svc.generate_plan(db, 1, AS_OF)
    assert svc.latest_plan(db, 1)["id"] == second["id"]
    assert [p["id"] for p in svc.list_plans(db, 1, limit=10)["plans"]] == [second["id"], first["id"]]


def test_user_isolation(db):
    essentials_20000(db, user_id=1)
    add_forecast(db, user_id=1)
    save_profile(db, user_id=1)
    svc.replace_holdings(db, 1, [HoldingIn(asset_class="equity", amount=Decimal("50000"))], AS_OF)
    plan_a = svc.generate_plan(db, 1, AS_OF)

    with pytest.raises(HTTPException) as error:
        svc.latest_plan(db, 2)                          # user B cannot see user A's plan
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        svc.generate_plan(db, 2, AS_OF)                 # nor use A's profile
    assert svc.get_holdings(db, 2)["holdings"] == []

    save_profile(db, user_id=2)
    plan_b = svc.generate_plan(db, 2, AS_OF)            # A's forecast, budgets and holdings are not used
    assert plan_b["forecast_run_id"] is None
    assert {"forecast_missing", "essentials_unknown", "no_holdings"} <= set(plan_b["warnings"])
    assert [p["id"] for p in svc.list_plans(db, 1, limit=10)["plans"]] == [plan_a["id"]]
    assert [p["id"] for p in svc.list_plans(db, 2, limit=10)["plans"]] == [plan_b["id"]]


# --- Explanation (the agent itself is tested in test_investment_explainer.py) ---

def test_faithful_llm_text_is_stored_with_one_call(db, monkeypatch):
    essentials_20000(db)
    add_forecast(db)
    save_profile(db)
    chain = use_fake_llm(monkeypatch, "Your category is aggressive and both factors agree. Projections are "
                                      "scenarios under stated assumptions.")
    plan = svc.generate_plan(db, 1, AS_OF)
    row = db.get(InvestmentPlan, plan["id"])
    assert len(chain.calls) == 1 and row.llm_calls == 1
    assert (row.llm_used, row.explanation_faithful, row.claims_compliant) == (True, True, True)
    assert plan["explanation_source"] == "llm" and plan["explanation"].endswith(INVESTING_DISCLAIMER)


def test_prohibited_claim_is_logged_and_the_template_is_stored(db, monkeypatch):
    save_profile(db)
    svc.replace_holdings(db, 1, [HoldingIn(asset_class="equity", amount=Decimal("1000"), label="my secret label")],
                         AS_OF)
    chain = use_fake_llm(monkeypatch, "Index funds give guaranteed returns.")
    plan = svc.generate_plan(db, 1, AS_OF)
    row = db.get(InvestmentPlan, plan["id"])
    # Privacy: holding labels never reach the LLM
    assert "my secret label" not in chain.calls[0]["facts_json"]
    assert (row.llm_used, row.explanation_faithful, row.claims_compliant) == (True, True, False)
    assert row.raw_prohibited_claims == 1 and plan["explanation_source"] == "template"
    assert "guaranteed" not in plan["explanation"].lower()


# --- What-if and assumptions ---

def test_simulate_is_deterministic_and_not_stored(db):
    request = SimulateRequest(monthly_sip=Decimal("10000"), years=10, category="moderate",
                              goal_amount=Decimal("2000000"))
    first, second = svc.simulate(request), svc.simulate(request)
    assert first == second
    projection = first["projection"]
    assert projection["p10"] <= projection["p50"] <= projection["p90"]
    assert projection["months_to_goal"] is not None
    assert db.query(InvestmentPlan).count() == 0


def test_simulate_with_step_up_reports_no_months_to_goal(db):
    request = SimulateRequest(monthly_sip=Decimal("10000"), years=10, category="moderate",
                              goal_amount=Decimal("2000000"), annual_step_up=0.1)
    assert svc.simulate(request)["projection"]["months_to_goal"] is None


def test_assumptions_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "INVEST_EQUITY_RETURN", 0.09)
    assumptions = svc.get_assumptions()
    equity = next(a for a in assumptions["assets"] if a["asset_class"] == "equity")
    assert equity["expected_return"] == 0.09 and "placeholder" in assumptions["note"].lower()
