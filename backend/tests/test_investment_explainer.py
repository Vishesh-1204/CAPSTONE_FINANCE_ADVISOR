# tests/test_investment_explainer.py
#
# Unit tests for the Investment Advisor's explanation layer (Phase 6A-4).
# The LLM is a fake chain (an object with .invoke), so no API key or network is
# needed. RESULT has the shape investment_service stores in result_json.

import copy
import json

import pytest

from app.agents.investment_explainer import (
    REJECTED_AMOUNT, REJECTED_CLAIMS, REJECTED_PERCENTAGE, REJECTED_SHORTHAND, InvestmentExplainer, build_facts,
    build_facts_payload, indian_grouping, inr, pct, signed_inr, template_text,
)
from app.core.config import settings
from app.core.disclaimers import INVESTING_DISCLAIMER
from app.services.investing.claims_guard import find_prohibited_claims
from app.services.investing.instrument_catalog import instruments_for

TARGET = {"equity": 0.55, "debt": 0.30, "gold": 0.10, "cash": 0.05}
RESULT = {
    "risk_profile": {"tolerance_score": 100.0, "capacity_score": 40.0, "tolerance_category": "aggressive",
                     "capacity_category": "moderate", "category": "moderate", "limiting_factor": "capacity"},
    "target_allocation": TARGET,
    "current_allocation": {"equity": 0.9, "debt": 0.1, "gold": 0.0, "cash": 0.0},
    "rebalance": [
        {"asset_class": "equity", "current_weight": 0.9, "target_weight": 0.55, "amount_change": -35000.0, "action": "reduce"},
        {"asset_class": "debt", "current_weight": 0.1, "target_weight": 0.30, "amount_change": 20000.0, "action": "add"},
        {"asset_class": "gold", "current_weight": 0.0, "target_weight": 0.10, "amount_change": 10000.0, "action": "add"},
        {"asset_class": "cash", "current_weight": 0.0, "target_weight": 0.05, "amount_change": 5000.0, "action": "hold"},
    ],
    "sip": {"capacity_total": 6234.66, "recommended_total": 6234.66, "change_vs_existing": 4234.66,
            "emergency_topup": 10000.0, "limited_by": "capacity"},
    "projection_current": {"monthly_sip": 2000.0, "years": 8.0, "p10": 250000.0, "p50": 290000.5, "p90": 335000.0,
                           "probability_of_goal": 0.05, "months_to_goal": 200, "n_paths": 200},
    "projection_recommended": {"monthly_sip": 6234.66, "years": 8.0, "p10": 780000.0, "p50": 1234567.4,
                               "p90": 1500000.0, "probability_of_goal": 0.62, "months_to_goal": 92, "n_paths": 200},
    "portfolio": {"expected_return": 0.0812, "volatility": 0.1015},
    "emergency_fund": {"essential_monthly_spend": 20000.0, "essentials_source": "budget", "months_covered": 1.0,
                       "target_months": 6.0, "gap": 100000.0},
    "surplus": {"monthly_low": 20293.33, "source": "forecast", "forecast_as_of": "2026-10-22"},
    "goal": {"amount": 1000000.0, "years": 8.0, "required_sip": 7000.12},
    "instruments": instruments_for(TARGET),
    "warnings": [],
}
ASSUMPTIONS = {"assets": [], "sip_safety_factor": 0.8, "mc_paths": 200, "mc_seed": 42, "note": "placeholder"}

HONEST = ("Your category is moderate: your finances, not your comfort with risk, are the limit. The target is "
          "55% equity, and the suggested SIP is ₹6,235. After 8 years the middle outcome is ₹12,34,567, with a "
          "62% chance of reaching your goal. These are scenarios under stated assumptions.")


class FakeChain:
    """Returns a canned answer (or raises) and records what it was sent."""

    def __init__(self, answer: str | Exception):
        self.answer = answer
        self.calls: list[dict] = []

    def invoke(self, inputs: dict) -> str:
        self.calls.append(inputs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def answer(summary: str) -> str:
    return json.dumps({"summary": summary}, ensure_ascii=False)


def explainer(*chains: FakeChain) -> InvestmentExplainer:
    return InvestmentExplainer(find_prohibited_claims, chains=[(f"fake{i}", c) for i, c in enumerate(chains)])


def variant(**changes) -> dict:
    """RESULT with some top-level keys replaced (deep copy, so RESULT never changes)."""
    result = copy.deepcopy(RESULT)
    result.update(changes)
    return result


# --- Formatting ---

def test_rupees_use_indian_digit_grouping_and_whole_rupees():
    assert indian_grouping(1234567) == "12,34,567" and indian_grouping(999) == "999"
    assert inr(1234567.4) == "₹12,34,567" and inr(100000) == "₹1,00,000"
    assert inr(-1234567.5) == "-₹12,34,568"
    assert signed_inr(4234.66) == "+₹4,235" and signed_inr(-2000) == "-₹2,000" and signed_inr(0) == "₹0"


def test_percentages_have_at_most_one_decimal():
    assert pct(0.55) == "55%" and pct(0.123) == "12.3%" and pct(0.0) == "0%"


# --- Facts and template ---

def test_payload_is_preformatted_aggregates_only():
    payload = build_facts_payload(build_facts(RESULT, ASSUMPTIONS))
    facts = json.loads(payload)
    assert facts["range_at_suggested_sip"]["middle"] == "₹12,34,567"
    assert facts["target_allocation"]["equity"] == "55%" and facts["share_of_surplus_used"] == "80%"
    assert "1234567.4" not in payload                  # no raw floats the LLM could re-round
    assert [row["asset_class"] for row in facts["rebalancing"]] == ["equity", "debt", "gold"]   # 'hold' omitted


VARIANTS = {
    "capacity-limited": RESULT,
    "tolerance-limited": variant(risk_profile={**RESULT["risk_profile"], "limiting_factor": "tolerance",
                                               "tolerance_category": "conservative", "category": "conservative"}),
    "both": variant(risk_profile={**RESULT["risk_profile"], "limiting_factor": "both"}),
    "no goal, no holdings, no top-up": variant(
        goal={"amount": None, "years": 8.0, "required_sip": None},
        current_allocation={"equity": 0.0, "debt": 0.0, "gold": 0.0, "cash": 0.0},
        rebalance=[{**row, "action": "hold"} for row in RESULT["rebalance"]],
        sip={**RESULT["sip"], "emergency_topup": 0.0}, warnings=["no_holdings"],
    ),
    "nothing to size a SIP from": variant(
        surplus={"monthly_low": 0.0, "source": "none", "forecast_as_of": None},
        sip={"capacity_total": 0.0, "recommended_total": 0.0, "change_vs_existing": -2000.0,
             "emergency_topup": 0.0, "limited_by": "capacity"},
        projection_recommended={**RESULT["projection_recommended"], "monthly_sip": 0.0, "p10": 0.0, "p50": 0.0,
                                "p90": 0.0, "probability_of_goal": 0.0, "months_to_goal": None},
        warnings=["forecast_missing", "surplus_unknown", "essentials_unknown", "forecast_stale"],
    ),
}


@pytest.mark.parametrize("name", VARIANTS)
def test_template_passes_every_guard_in_every_variant(name):
    facts = build_facts(VARIANTS[name], ASSUMPTIONS)
    problems, claims = explainer().check(template_text(facts), build_facts_payload(facts))
    assert (problems, claims) == ([], [])


def test_template_names_the_limit_and_says_projections_are_scenarios():
    text = template_text(build_facts(RESULT, ASSUMPTIONS))
    assert "Your finances, not your comfort with risk, are the limit" in text
    assert "scenarios under assumed returns" in text and "not predictions" in text
    assert "₹12,34,567" in text and "55%" in text


def test_no_llm_means_template_with_the_disclaimer_added_once_and_no_calls():
    result = InvestmentExplainer(find_prohibited_claims, chains=[]).explain(RESULT, ASSUMPTIONS)
    assert result.text.endswith(INVESTING_DISCLAIMER) and result.text.count(INVESTING_DISCLAIMER) == 1
    assert (result.llm_used, result.explanation_faithful, result.claims_compliant, result.llm_calls) == \
        (False, None, None, 0)


# --- LLM answers ---

def test_faithful_text_is_accepted_with_exactly_one_call():
    chain = FakeChain(answer(HONEST))
    result = explainer(chain).explain(RESULT, ASSUMPTIONS)
    assert len(chain.calls) == 1 and result.llm_calls == 1
    assert result.text == f"{HONEST}\n\n{INVESTING_DISCLAIMER}"
    assert (result.llm_used, result.explanation_faithful, result.claims_compliant) == (True, True, True)
    assert result.raw_prohibited_claims == 0 and result.rejected_for == ()


@pytest.mark.parametrize("text, reason", [
    ("You could reach ₹99,999 a month.", REJECTED_AMOUNT),
    ("The middle outcome is about ₹12,35,000.", REJECTED_AMOUNT),       # the LLM rounded: not in the facts
    ("Put 70% in equity.", REJECTED_PERCENTAGE),
    ("The middle outcome is about 12.3 lakh.", REJECTED_SHORTHAND),
    ("Invest ₹6k a month.", REJECTED_SHORTHAND),
])
def test_unfaithful_numbers_fall_back_to_the_template(text, reason):
    result = explainer(FakeChain(answer(text))).explain(RESULT, ASSUMPTIONS)
    assert reason in result.rejected_for
    assert (result.llm_used, result.explanation_faithful, result.claims_compliant) == (True, False, True)
    assert result.text == explainer().explain(RESULT, ASSUMPTIONS).text     # the template


def test_percentage_within_one_point_is_accepted():
    result = explainer(FakeChain(answer("About 54.5% goes to equity."))).explain(RESULT, ASSUMPTIONS)
    assert result.explanation_faithful is True


def test_guaranteed_returns_are_rejected_and_counted():
    result = explainer(FakeChain(answer("Index funds give guaranteed returns."))).explain(RESULT, ASSUMPTIONS)
    assert (result.explanation_faithful, result.claims_compliant, result.raw_prohibited_claims) == (True, False, 1)
    assert result.rejected_for == (REJECTED_CLAIMS,)
    assert "guaranteed" not in result.text.lower()


def test_negated_guarantee_is_also_rejected_on_purpose():
    result = explainer(FakeChain(answer("This plan is not guaranteed."))).explain(RESULT, ASSUMPTIONS)
    assert result.claims_compliant is False


def test_llm_copying_the_disclaimer_is_rejected_which_is_why_code_adds_it():
    result = explainer(FakeChain(answer(f"Equity is 55%. {INVESTING_DISCLAIMER}"))).explain(RESULT, ASSUMPTIONS)
    assert result.claims_compliant is False


def test_both_guards_are_measured_even_when_both_fail():
    text = "Guaranteed ₹99,999 with zero risk."
    result = explainer(FakeChain(answer(text))).explain(RESULT, ASSUMPTIONS)
    assert (result.explanation_faithful, result.claims_compliant, result.raw_prohibited_claims) == (False, False, 2)
    assert result.rejected_for == (REJECTED_AMOUNT, REJECTED_CLAIMS)


def test_provider_error_gives_the_template():
    result = explainer(FakeChain(RuntimeError("503 unavailable"))).explain(RESULT, ASSUMPTIONS)
    assert (result.llm_used, result.explanation_faithful, result.claims_compliant, result.llm_calls) == \
        (False, None, None, 1)
    assert result.text.endswith(INVESTING_DISCLAIMER)


def test_malformed_answer_gives_the_template():
    result = explainer(FakeChain("Sure! Here is your plan.")).explain(RESULT, ASSUMPTIONS)
    assert result.llm_used is False and result.llm_calls == 1


def test_fallback_provider_is_tried_when_the_primary_fails():
    primary, fallback = FakeChain(RuntimeError("down")), FakeChain(answer(HONEST))
    result = explainer(primary, fallback).explain(RESULT, ASSUMPTIONS)
    assert result.provider == "fake1" and result.llm_calls == 2 and result.explanation_faithful is True


def test_switching_llm_explanations_off_builds_a_template_only_explainer(monkeypatch):
    monkeypatch.setattr(settings, "INVEST_USE_LLM_EXPLANATIONS", False)
    result = InvestmentExplainer.from_settings(find_prohibited_claims).explain(RESULT, ASSUMPTIONS)
    assert result.llm_calls == 0 and result.llm_used is False
