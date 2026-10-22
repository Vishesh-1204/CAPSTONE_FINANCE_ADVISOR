# tests/test_forecast_explainer.py
#
# Unit tests for the Forecast Agent's explanation layer. The LLM is a fake
# chain (an object with .invoke), so no API key or network is needed.

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.agents.explanation_guard import is_faithful
from app.agents.forecast_explainer import (
    BudgetWarningFact, ForecastExplainer, ForecastFacts, RecurringFact, allowed_numbers, build_facts_payload,
    inr, template_summary,
)

FACTS = ForecastFacts(
    as_of=date(2026, 10, 22), horizon_days=30, end_date=date(2026, 11, 21),
    model_name="exp_smoothing", history_months=8, low_confidence=False, model_selected_by_backtest=True,
    opening_balance=Decimal("25000.00"), opening_balance_source="statement", opening_balance_date=date(2026, 10, 20),
    income_source="recurring", expected_income=Decimal("60000.00"),
    fixed_expenses=Decimal("18000.00"), variable_expenses=Decimal("14873.55"),
    variable_lower=Decimal("11200.40"), variable_upper=Decimal("19050.00"),
    investments=Decimal("3000.00"), net_cashflow=Decimal("24126.45"),
    end_balance=Decimal("49126.45"), end_balance_lower=Decimal("44950.00"), end_balance_upper=Decimal("52799.60"),
    cash_crunch_date=None,
    recurring=[RecurringFact("income", "Salary", Decimal("60000.00"), 1),
               RecurringFact("fixed_expense", "Housing & Rent", Decimal("18000.00"), 5)],
    budget_warnings=[BudgetWarningFact("2026-11", "Food & Dining", Decimal("6200.00"), Decimal("5000.00"),
                                       Decimal("1200.00"))],
)


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


HONEST = answer("You should end the month near ₹49,126, after rent of ₹18,000 and salary of ₹60,000.")
INVENTED = answer("You will save ₹99,999 this month.")


# --- formatting and template ---

def test_inr_rounds_to_whole_rupees_and_puts_the_sign_first():
    assert inr(Decimal("14873.55")) == "₹14,874"
    assert inr(Decimal("-1234.56")) == "-₹1,235"
    assert inr(Decimal("0.00")) == "₹0"


def test_template_passes_its_own_guard_in_every_variant():
    variants = [
        FACTS,
        replace(FACTS, cash_crunch_date=date(2026, 11, 3), end_balance_lower=Decimal("-4200.00")),
        replace(FACTS, income_source="profile", opening_balance_source="request", opening_balance_date=None),
        replace(FACTS, income_source="none", expected_income=Decimal("0"), net_cashflow=Decimal("-35873.55")),
        replace(FACTS, low_confidence=True, history_months=1, model_selected_by_backtest=False,
                model_name="moving_average_3", budget_warnings=[]),
    ]
    for facts in variants:
        text = template_summary(facts)
        assert is_faithful(text, allowed_numbers(facts)), text


def test_template_states_the_key_numbers_and_sources():
    text = template_summary(FACTS)
    for snippet in ("₹60,000", "₹18,000", "₹14,874", "₹11,200", "₹19,050", "₹49,126", "₹3,000",
                    "regular credits", "statement balance", "20 Oct 2026", "Food & Dining", "smallest error"):
        assert snippet in text, snippet
    assert "below ₹0" not in text


def test_template_warns_about_a_cash_crunch():
    text = template_summary(replace(FACTS, cash_crunch_date=date(2026, 11, 3)))
    assert "Warning" in text and "3 Nov 2026" in text


def test_template_is_honest_about_little_history_and_missing_income():
    text = template_summary(replace(FACTS, low_confidence=True, history_months=1, model_selected_by_backtest=False,
                                    income_source="none", expected_income=Decimal("0")))
    assert "only 1 month of history" in text
    assert "not yet enough history" in text
    assert "no income" in text


# --- privacy ---

def test_payload_has_aggregates_and_categories_only():
    payload = json.loads(build_facts_payload(FACTS))
    text = json.dumps(payload)
    assert payload["end_balance"]["expected"] == 49126.45
    assert payload["recurring"][1]["category"] == "Housing & Rent"
    assert "@" not in text and "payee" not in text and "description" not in text


# --- the agent ---

def test_no_llm_means_template_and_no_calls():
    explainer = ForecastExplainer(chains=[])
    result = explainer.explain(FACTS)
    assert result.summary == template_summary(FACTS)
    assert (result.llm_used, result.faithful, explainer.llm_calls) == (False, None, 0)


def test_honest_llm_text_is_used_with_one_call():
    chain = FakeChain(HONEST)
    explainer = ForecastExplainer(chain=chain)
    result = explainer.explain(FACTS)
    assert result.summary.startswith("You should end the month near ₹49,126")
    assert (result.llm_used, result.faithful, explainer.llm_calls) == (True, True, 1)
    assert "facts_json" in chain.calls[0]


def test_invented_amount_falls_back_to_the_template_and_is_logged_unfaithful():
    result = ForecastExplainer(chain=FakeChain(INVENTED)).explain(FACTS)
    assert result.summary == template_summary(FACTS)
    assert (result.llm_used, result.faithful) == (True, False)


def test_llm_down_falls_back_to_the_template():
    result = ForecastExplainer(chain=FakeChain(RuntimeError("quota exceeded"))).explain(FACTS)
    assert result.summary == template_summary(FACTS)
    assert (result.llm_used, result.faithful) == (False, None)


def test_malformed_answers_fall_back_to_the_template():
    for raw in ("not json", json.dumps(["a list"]), json.dumps({"summary": "   "}), json.dumps({"text": "x"})):
        result = ForecastExplainer(chain=FakeChain(raw)).explain(FACTS)
        assert result.summary == template_summary(FACTS) and not result.llm_used


def test_code_fences_are_stripped():
    result = ForecastExplainer(chain=FakeChain(f"```json\n{HONEST}\n```")).explain(FACTS)
    assert result.llm_used and result.faithful


def test_fallback_provider_is_tried_when_the_primary_fails():
    explainer = ForecastExplainer(chains=[("gemini", FakeChain(RuntimeError("down"))), ("openai", FakeChain(HONEST))])
    result = explainer.explain(FACTS)
    assert (result.provider, result.faithful, explainer.llm_calls) == ("openai", True, 2)


def test_rounding_within_a_rupee_is_allowed_but_a_bigger_change_is_not():
    near = answer("Expected everyday spending is about ₹14,874.")     # 14,873.55 rounded
    off = answer("Expected everyday spending is about ₹15,000.")      # rounded too far: not a forecast number
    assert ForecastExplainer(chain=FakeChain(near)).explain(FACTS).faithful is True
    assert ForecastExplainer(chain=FakeChain(off)).explain(FACTS).faithful is False


def test_switching_llm_explanations_off_builds_a_template_only_explainer(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "FORECAST_USE_LLM_EXPLANATIONS", False)
    explainer = ForecastExplainer.from_settings()
    assert explainer.explain(FACTS).llm_used is False and explainer.llm_calls == 0
