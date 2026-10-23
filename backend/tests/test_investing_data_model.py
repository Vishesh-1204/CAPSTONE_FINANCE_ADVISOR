# tests/test_investing_data_model.py
#
# Purpose: Phase 6 data model and schema checks.
#   * The schema Literals and the model's CHECK list match the pure maths modules,
#     so the API, the database and the maths can never drift apart.
#   * Request bounds, duplicate holdings, the disclaimer on every response.
#   * Database rules (CHECK, UNIQUE) and the investment_plans defaults.

from datetime import date
from decimal import Decimal
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.investment import HOLDING_ASSET_CLASSES, Holding, InvestmentPlan
from app.schemas import investment as schemas
from app.services.investing.allocation import ASSET_CLASSES, DEFAULT_POLICY
from app.services.investing.risk_scoring import AGGRESSIVE, CONSERVATIVE, MODERATE

ANSWERS = {"reaction_to_20pct_drop": 3, "investing_experience": 2, "return_vs_safety": 4}
PROFILE = {"answers": ANSWERS, "liquid_savings": "150000", "income_stability": 4, "dependents": 1, "horizon_years": 10}


# --- The schema, the model and the maths agree ---

def test_asset_classes_match_the_allocation_module_everywhere():
    assert get_args(schemas.AssetClass) == ASSET_CLASSES == HOLDING_ASSET_CLASSES


def test_risk_categories_match_the_allocation_policy_and_risk_scoring():
    assert set(get_args(schemas.RiskCategory)) == set(DEFAULT_POLICY.base)
    assert get_args(schemas.RiskCategory) == (CONSERVATIVE, MODERATE, AGGRESSIVE)


def test_every_response_carries_the_disclaimer():
    responses = [
        cls for name, cls in vars(schemas).items()
        if name.endswith("Response") and isinstance(cls, type) and issubclass(cls, BaseModel)
    ]
    assert len(responses) == 6
    for cls in responses:
        assert cls.model_fields["disclaimer"].default == schemas.DISCLAIMER, cls.__name__


# --- Request validation ---

@pytest.mark.parametrize("bad_answer", [0, 6])
def test_questionnaire_answers_must_be_one_to_five(bad_answer):
    with pytest.raises(ValidationError):
        schemas.InvestmentProfileUpdate(**{**PROFILE, "answers": {**ANSWERS, "return_vs_safety": bad_answer}})


def test_profile_rejects_negative_savings_and_too_many_dependents():
    with pytest.raises(ValidationError):
        schemas.InvestmentProfileUpdate(**{**PROFILE, "liquid_savings": "-1"})
    with pytest.raises(ValidationError):
        schemas.InvestmentProfileUpdate(**{**PROFILE, "dependents": schemas.MAX_DEPENDENTS + 1})


def test_valid_profile_keeps_money_exact():
    profile = schemas.InvestmentProfileUpdate(**PROFILE)
    assert profile.liquid_savings == Decimal("150000") and profile.existing_monthly_sip == 0


def test_unknown_asset_class_is_rejected():
    with pytest.raises(ValidationError):
        schemas.HoldingIn(asset_class="crypto", amount="1000")


def test_duplicate_holdings_are_rejected_even_with_spaces_or_case():
    with pytest.raises(ValidationError):
        schemas.HoldingsUpdate(holdings=[
            {"asset_class": "equity", "amount": "1000", "label": "Index Fund"},
            {"asset_class": "equity", "amount": "2000", "label": "  index fund "},
        ])
    # Same label in two different classes is fine
    update = schemas.HoldingsUpdate(holdings=[
        {"asset_class": "equity", "amount": "1000"}, {"asset_class": "debt", "amount": "2000"},
    ])
    assert [h.label for h in update.holdings] == ["", ""]


def test_goal_years_without_a_goal_amount_is_rejected():
    with pytest.raises(ValidationError):
        schemas.PlanRequest(goal_years=5)
    assert schemas.PlanRequest(goal_amount="500000").goal_years is None


@pytest.mark.parametrize("change", [
    {"years": 0.5}, {"years": 41}, {"monthly_sip": str(schemas.MAX_MONTHLY_SIP + 1)},
    {"category": "reckless"}, {"annual_step_up": 0.6},
])
def test_simulate_bounds(change):
    with pytest.raises(ValidationError):
        schemas.SimulateRequest(**{"monthly_sip": "10000", "years": 10, "category": "moderate", **change})


# --- Database rules ---

def _holding(**overrides) -> Holding:
    values = {"user_id": 1, "asset_class": "equity", "amount": Decimal("1000.00"), "as_of": date(2026, 10, 23)}
    return Holding(**{**values, **overrides})


@pytest.mark.parametrize("overrides", [{"asset_class": "crypto"}, {"amount": Decimal("-1.00")}])
def test_database_check_rules_reject_bad_holdings(db, overrides):
    db.add(_holding(**overrides))
    with pytest.raises(IntegrityError):
        db.commit()


def test_holding_without_label_cannot_be_duplicated(db):
    db.add(_holding())
    db.commit()
    assert db.query(Holding).one().label == ""
    db.add(_holding(amount=Decimal("5.00")))
    with pytest.raises(IntegrityError):
        db.commit()


def test_plan_defaults_and_derived_explanation_source(db):
    plan = InvestmentPlan(user_id=1, inputs_json={}, result_json={}, assumptions_json={}, engine_version="6.0")
    db.add(plan)
    db.commit()
    assert (plan.llm_used, plan.llm_calls, plan.raw_prohibited_claims) == (False, 0, 0)
    assert plan.explanation is None and plan.explanation_faithful is None and plan.claims_compliant is None
    assert plan.explanation_source == "template"

    plan.llm_used, plan.explanation_faithful, plan.claims_compliant = True, True, True
    assert plan.explanation_source == "llm"
    plan.claims_compliant = False          # LLM text rejected by the claims guard -> template was shown
    assert plan.explanation_source == "template"
