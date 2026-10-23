# tests/test_investing_evaluation.py
#
# Purpose: the Phase 6B evaluation code itself (rules, personas, methods, runner).
# Small numbers everywhere (few personas, few Monte Carlo paths), so the suite
# stays fast; the real run is `python -m evaluation.evaluate_investing`.

import json
from dataclasses import replace

import pytest

from app.agents.investment_explainer import InvestmentExplainer
from app.services.investing.assumptions import load_assumptions
from app.services.investing.claims_guard import find_prohibited_claims
from evaluation import evaluate_investing as ev
from evaluation.evaluate_forecast import default_origins
from evaluation.investing_methods import ABLATIONS, METHODS, ablation_plan, method_plan
from evaluation.investing_personas import generate_personas, make_persona
from evaluation.investing_rules import (
    ENFORCED, FIXED_AFTER_EVALUATION, INDEPENDENT, RULES, SECONDARY, PersonaFacts, Recommendation, check,
)

ASSUMPTIONS = load_assumptions()
PERSONAS = generate_personas(40)


class FakeChain:
    def __init__(self, answer: str):
        self.answer = answer

    def invoke(self, inputs: dict) -> str:
        return json.dumps({"summary": self.answer})


def fake_llm(text: str):
    return lambda: InvestmentExplainer(find_prohibited_claims, chain=FakeChain(text))


def persona_like(**changes):
    """A persona with chosen facts (the rest from persona 0)."""
    return replace(PERSONAS[0], **changes)


# --- Rules ---

def test_rules_carry_their_honesty_labels():
    design = {rule.rule_id: rule.design for rule in RULES}
    assert [i for i, d in design.items() if d == ENFORCED] == ["R1", "R2", "R3a", "R4"]
    assert [i for i, d in design.items() if d == INDEPENDENT] == ["R6", "R7"]
    assert [i for i, d in design.items() if d == FIXED_AFTER_EVALUATION] == ["R5"]
    assert [i for i, d in design.items() if d == SECONDARY] == ["R3b"]


def test_rules_that_do_not_apply_return_none():
    facts = PersonaFacts(age=30, horizon_years=20, emergency_months=8, income_stability=5, dependents=0,
                         answers=(3, 3, 3), essential_monthly=20000, liquid_savings=160000, low_surplus=15000)
    result = check(facts, Recommendation(equity=0.55, monthly_sip=10000, emergency_topup=0))
    assert result["R1"] is None and result["R3a"] is None and result["R7"] is None
    assert result["R4"] is True


def test_p_fails_the_strict_r3b_by_design_but_meets_r3a():
    weak = persona_like(essential_monthly=10000.0, liquid_savings=0.0, emergency_months=0.0, low_surplus=40000.0,
                        mean_surplus=50000.0)
    rules = check(weak.facts(), method_plan("P", weak, ASSUMPTIONS).recommendation())
    assert rules["R3a"] is True and rules["R3b"] is False


# --- Personas ---

def test_personas_are_reproducible_and_stable_when_more_are_added():
    assert generate_personas(10) == generate_personas(10)
    assert generate_personas(5) == generate_personas(10)[:5]
    assert make_persona(3, seed=7) != make_persona(3, seed=42)


def test_persona_facts_are_in_range():
    for p in PERSONAS:
        assert 22 <= p.age <= 60 and 0 <= p.dependents <= 4 and 1 <= p.income_stability <= 5
        assert p.low_surplus < p.mean_surplus and p.goal_amount >= 10_000
        assert p.horizon_years == 0.5 or 1 <= p.horizon_years <= 25
        assert p.emergency_months == round(p.liquid_savings / p.essential_monthly, 2)


# --- Methods ---

def test_baseline_allocations():
    young_brave_short = persona_like(age=30, answers=(5, 5, 5), horizon_years=2.0)
    assert method_plan("B0", young_brave_short, ASSUMPTIONS).weights["equity"] == 0.60
    assert method_plan("B1", young_brave_short, ASSUMPTIONS).weights["equity"] == 0.70
    assert method_plan("B2", young_brave_short, ASSUMPTIONS).weights["equity"] == 0.75   # no horizon cap
    assert method_plan("P", young_brave_short, ASSUMPTIONS).weights["equity"] <= 0.20    # P caps short horizons


def test_naive_sip_is_the_whole_mean_surplus_with_nothing_reserved():
    plan = method_plan("B0", PERSONAS[1], ASSUMPTIONS)
    assert plan.monthly_sip == round(max(0.0, PERSONAS[1].mean_surplus), 2) and plan.emergency_topup == 0


def test_mixed_variants_combine_the_two_halves():
    for persona in PERSONAS[:10]:
        b2, p = method_plan("B2", persona, ASSUMPTIONS), method_plan("P", persona, ASSUMPTIONS)
        mixed = method_plan("B2_alloc+P_sip", persona, ASSUMPTIONS)
        assert mixed.weights == b2.weights and mixed.emergency_topup == p.emergency_topup
        other = method_plan("P_alloc+naive_sip", persona, ASSUMPTIONS)
        assert other.weights == p.weights and other.monthly_sip == b2.monthly_sip


def test_ablations_change_only_their_part():
    weak = persona_like(essential_monthly=10000.0, liquid_savings=0.0, emergency_months=0.0, low_surplus=40000.0,
                        mean_surplus=50000.0,
                        goal_amount=1e9)
    default = method_plan("P", weak, ASSUMPTIONS)
    assert ablation_plan("no_emergency_first", weak, ASSUMPTIONS).emergency_topup == 0
    assert ablation_plan("safety=1.0", weak, ASSUMPTIONS).monthly_sip > default.monthly_sip
    assert ablation_plan("mean_surplus", weak, ASSUMPTIONS).monthly_sip > default.monthly_sip
    assert set(ABLATIONS) >= {"no_capacity", "no_horizon_glide", "goal_p75"}


def test_goal_p75_never_asks_for_less_than_the_expected_sizing():
    for persona in PERSONAS[:20]:
        assert (ablation_plan("goal_p75", persona, ASSUMPTIONS).monthly_sip
                >= method_plan("P", persona, ASSUMPTIONS).monthly_sip - 0.01)


# --- The real pipeline must equal the evaluation's P ---

def test_evaluation_p_matches_the_real_service_and_the_template_passes():
    records = ev.pipeline_sample(PERSONAS[:8], ASSUMPTIONS, paths=50, llm_factory=None)
    values = {r["metric"]: r["value"] for r in records}
    assert values["matches_evaluation_p"] == 1.0 and values["template_passes_guards"] == 1.0
    assert values["llm_calls_per_plan"] == 0


def test_llm_sample_measures_raw_output():
    honest = ev.pipeline_sample(PERSONAS[:3], ASSUMPTIONS, 50, fake_llm("Projections are scenarios, not predictions."))
    promise = ev.pipeline_sample(PERSONAS[:3], ASSUMPTIONS, 50, fake_llm("Returns are guaranteed."))
    honest_values = {r["metric"]: r["value"] for r in honest}
    promise_values = {r["metric"]: r["value"] for r in promise}
    assert honest_values["raw_compliant_rate"] == 1.0 and honest_values["llm_text_used_rate"] == 1.0
    assert promise_values["raw_compliant_rate"] == 0.0 and promise_values["raw_prohibited_claims_per_plan"] == 1.0
    assert promise_values["llm_calls_per_plan"] == 1.0


# --- Panel replay and guard cases ---

def test_panel_replay_on_one_origin_is_monotonic_in_the_sip_size():
    records, info = ev.panel_affordability([default_origins()[0]])
    assert info.forecasts == 30
    failure = {r["method"]: r["value"] for r in records if r["metric"] == "failure_rate" and r["group"] == "all"}
    assert all(0 <= rate <= 1 for rate in failure.values())
    # A smaller SIP from the same surplus can never fail more often
    assert failure["low band x 0.6"] <= failure["P: low band x 0.8"] <= failure["low band x 1.0"]
    assert failure["mean x 0.8"] <= failure["naive: mean x 1.0"]


def test_guard_cases_are_labelled_and_negations_are_flagged_on_purpose():
    cases = ev.load_guard_cases()
    assert len(cases) >= 60 and {c.label for c in cases} == {"safe", "unsafe"}
    values = {r["metric"]: r["value"] for r in ev.guard_metrics(cases) if r["group"] == "all"}
    assert values["flag_rate:safe:negation"] == 1.0        # deliberate over-strictness
    assert values["flag_rate:safe:balanced"] == 0.0


# --- The runner ---

def test_small_run_is_deterministic_and_reports_every_experiment():
    first = ev.run_evaluation(personas=15, paths=50, panel=False, sample=3)
    second = ev.run_evaluation(personas=15, paths=50, panel=False, sample=3)
    # Compared as written to the CSV (NaN != NaN in Python; the CSV writes it as a blank)
    as_written = lambda result: [{**r, "value": ev._fmt(r["value"])} for r in result.records]   # noqa: E731
    assert as_written(first) == as_written(second)
    assert ev.markdown_report(first) == ev.markdown_report(second)
    experiments = {r["experiment"] for r in first.records}
    assert experiments == {"personas", "claims_guard", "pipeline"}
    methods = {r["method"] for r in first.records if r["experiment"] == "personas"}
    assert methods == set(METHODS)
    configs = {r["config"] for r in first.records if r["method"] == "P" and r["experiment"] == "personas"}
    assert configs == {"default", *ABLATIONS}
    report = ev.markdown_report(first)
    for heading in ("## 1. Main comparison", "## 2. Violation rate per rule", "## 4. Ablations", "## 5. Sensitivity",
                    "## 7. Claims guard", "## 8. Real pipeline sample", "## 9. Limitations"):
        assert heading in report
    assert "50 paths" in report
