# tests/test_budget_planner.py
#
# Unit tests for the Budget Planning Agent's explanation layer. The LLM is a
# fake chain (an object with .invoke), so no API key or network is needed.

import json
from decimal import Decimal

import pytest

from app.agents import budget_planner as bp
from app.agents.budget_planner import (
    BudgetFact, BudgetPlannerAgent, MalformedExplanationError, PlanContext,
    allowed_numbers, build_facts_payload, format_inr, parse_explanation_response, template_explanation,
)
from app.agents.explanation_guard import is_faithful
from app.services.budget_calculator import METHOD_BASELINE

FACTS = [
    BudgetFact("Housing & Rent", Decimal("15000.00"), Decimal("15000.00"), essential=True),
    BudgetFact("Food & Dining", Decimal("4170.00"), Decimal("4633.33"), essential=False),
]


def make_ctx(**overrides) -> PlanContext:
    values = dict(
        month="2026-10", method="history", income=Decimal("40000.00"), savings_target_pct=Decimal("20.00"),
        savings_amount=Decimal("8000.00"), spendable=Decimal("32000.00"), feasible=True,
        shortfall=Decimal("0.00"), months_used=3, trim=Decimal("0.10"), discretionary_scale=Decimal("1"),
    )
    values.update(overrides)
    return PlanContext(**values)


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


def llm_answer(summary: str, reasons: dict[str, str]) -> str:
    return json.dumps({"summary": summary, "reasons": reasons}, ensure_ascii=False)


GOOD = llm_answer(
    "Your ₹32,000 spendable income covers rent and food after saving ₹8,000.",
    {"Housing & Rent": "Rent stays at ₹15,000.", "Food & Dining": "Dining is trimmed to ₹4,170."},
)


# --- templates ---

@pytest.mark.parametrize("ctx", [
    make_ctx(),
    make_ctx(discretionary_scale=Decimal("0.5")),
    make_ctx(feasible=False, shortfall=Decimal("1234.56"), discretionary_scale=Decimal("0")),
    make_ctx(method=METHOD_BASELINE, months_used=0),
    make_ctx(months_used=1),
])
def test_templates_always_pass_the_guard(ctx):
    explanation = template_explanation(ctx, FACTS)
    allowed = allowed_numbers(ctx, FACTS)
    assert is_faithful(explanation.summary, allowed)
    assert all(is_faithful(text, allowed) for text in explanation.reasons.values())
    assert set(explanation.reasons) == {f.category for f in FACTS}
    assert not explanation.llm_used and explanation.faithful is None


def test_template_mentions_shortfall_when_infeasible():
    ctx = make_ctx(feasible=False, shortfall=Decimal("1234.56"), discretionary_scale=Decimal("0"))
    assert "₹1,234.56" in template_explanation(ctx, FACTS).summary


def test_format_inr():
    assert format_inr(Decimal("4170.00")) == "₹4,170"
    assert format_inr(Decimal("3333.33")) == "₹3,333.33"
    assert format_inr(Decimal("0")) == "₹0"


def test_agent_and_calculator_agree_on_the_baseline_method_name():
    assert bp.METHOD_BASELINE == METHOD_BASELINE


# --- parsing ---

def test_parse_accepts_fenced_json_and_matches_category_case():
    raw = "```json\n" + llm_answer("Fine.", {"food & dining": "Trimmed."}) + "\n```"
    summary, reasons = parse_explanation_response(raw, ["Food & Dining"])
    assert summary == "Fine." and reasons == {"Food & Dining": "Trimmed."}


def test_parse_ignores_unknown_categories_and_non_string_reasons():
    raw = llm_answer("Fine.", {"Crypto": "Buy more.", "Food & Dining": 42})
    assert parse_explanation_response(raw, ["Food & Dining"]) == ("Fine.", {})


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", ""])
def test_parse_rejects_non_objects(raw):
    with pytest.raises(MalformedExplanationError):
        parse_explanation_response(raw, ["Food & Dining"])


# --- the agent ---

def test_good_llm_answer_is_used():
    chain = FakeChain(GOOD)
    result = BudgetPlannerAgent(chain=chain).explain(make_ctx(), FACTS)
    assert result.llm_used and result.faithful is True and result.replaced_texts == 0
    assert result.reasons["Food & Dining"] == "Dining is trimmed to ₹4,170."
    assert len(chain.calls) == 1                              # ONE call per plan


def test_invented_amount_is_replaced_by_the_template():
    bad = llm_answer(
        "Save ₹8,000 and cut dining to ₹2,500.",              # ₹2,500 is not in the plan
        {"Housing & Rent": "Rent stays at ₹15,000.", "Food & Dining": "Spend ₹999 at most."},
    )
    ctx = make_ctx()
    result = BudgetPlannerAgent(chain=FakeChain(bad)).explain(ctx, FACTS)
    template = template_explanation(ctx, FACTS)
    assert result.llm_used and result.faithful is False and result.replaced_texts == 2
    assert result.summary == template.summary
    assert result.reasons["Food & Dining"] == template.reasons["Food & Dining"]
    assert result.reasons["Housing & Rent"] == "Rent stays at ₹15,000."      # faithful text is kept


def test_rounded_amounts_within_tolerance_are_accepted():
    answer = llm_answer("Dining averages ₹4,633.", {})      # real average is 4633.33
    result = BudgetPlannerAgent(chain=FakeChain(answer), tolerance=1.0).explain(make_ctx(), FACTS)
    assert result.faithful is True and result.summary == "Dining averages ₹4,633."


def test_missing_reasons_are_filled_but_not_counted_as_unfaithful():
    answer = llm_answer("Looks good.", {"Food & Dining": "Trimmed a little."})
    ctx = make_ctx()
    result = BudgetPlannerAgent(chain=FakeChain(answer)).explain(ctx, FACTS)
    assert result.faithful is True
    assert result.reasons["Housing & Rent"] == template_explanation(ctx, FACTS).reasons["Housing & Rent"]


def test_malformed_json_falls_back_to_template():
    ctx = make_ctx()
    result = BudgetPlannerAgent(chain=FakeChain("Sure! Here is your budget...")).explain(ctx, FACTS)
    assert result == template_explanation(ctx, FACTS)
    assert not result.llm_used and result.faithful is None


def test_fallback_provider_is_tried_once_when_the_primary_fails():
    primary, fallback = FakeChain(RuntimeError("429 quota exceeded")), FakeChain(GOOD)
    agent = BudgetPlannerAgent(chains=[("gemini", primary), ("openai", fallback)])
    result = agent.explain(make_ctx(), FACTS)
    assert result.llm_used and result.provider == "openai"
    assert len(primary.calls) == 1 and len(fallback.calls) == 1    # no retries
    assert agent.llm_calls == 2


def test_every_provider_down_gives_the_template():
    agent = BudgetPlannerAgent(chains=[("a", FakeChain(RuntimeError("down"))), ("b", FakeChain(RuntimeError("down")))])
    result = agent.explain(make_ctx(), FACTS)
    assert not result.llm_used and result.faithful is None


def test_no_chains_means_templates_and_no_calls():
    agent = BudgetPlannerAgent(chains=[])
    assert not agent.explain(make_ctx(), FACTS).llm_used
    assert agent.llm_calls == 0


def test_from_settings_without_any_api_key_is_template_only(monkeypatch):
    def no_providers():
        raise ValueError("GEMINI_API_KEY is missing")
    monkeypatch.setattr(bp, "get_llm_provider_chain", no_providers)
    agent = BudgetPlannerAgent.from_settings()
    assert not agent.explain(make_ctx(), FACTS).llm_used


def test_payload_contains_only_plan_facts():
    """Privacy: category names and aggregate numbers only."""
    chain = FakeChain(GOOD)
    BudgetPlannerAgent(chain=chain).explain(make_ctx(), FACTS)
    (inputs,) = chain.calls
    assert set(inputs) == {"facts_json"}
    payload = json.loads(inputs["facts_json"])
    assert set(payload) == {
        "month", "method", "monthly_income", "savings_target_pct", "savings_amount", "spendable_income",
        "feasible", "shortfall", "months_of_history", "discretionary_trim_pct", "discretionary_scaled_down",
        "categories",
    }
    assert all(set(c) == {"category", "limit", "avg_monthly_spend", "essential"} for c in payload["categories"])
    assert inputs["facts_json"] == build_facts_payload(make_ctx(), FACTS)
