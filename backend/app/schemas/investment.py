# app/schemas/investment.py
#
# Purpose: request/response shapes for the Phase 6 endpoints (/api/investing/...).
#
# Requests take Decimal so money arrives exact (at most 2 decimal places).
# Responses use float, like the budget and forecast schemas, because JSON has no
# Decimal type; the database keeps the exact Numeric(14,2) values.
# Every response carries `disclaimer`: this is an educational planning tool.
# The Literal lists must match the pure maths modules
# (tests/test_investing_data_model.py checks that they do).

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DISCLAIMER = (
    "Educational planning tool, not financial advice. "
    "Projections are scenarios under stated assumptions, not guarantees."
)

AssetClass = Literal["equity", "debt", "gold", "cash"]                 # allocation.ASSET_CLASSES
RiskCategory = Literal["conservative", "moderate", "aggressive"]       # allocation policy base keys
LimitingFactor = Literal["tolerance", "capacity", "both"]
RebalanceAction = Literal["add", "reduce", "hold"]
SipLimitedBy = Literal["capacity", "target", "none"]
ExplanationSource = Literal["llm", "template"]

# Bounds (design choices for a sane, educational tool)
MAX_HOLDINGS = 50
MAX_DEPENDENTS = 20
MAX_HORIZON_YEARS = 50
GOAL_MIN_YEARS, GOAL_MAX_YEARS = 1, 40
SIMULATE_MIN_YEARS, SIMULATE_MAX_YEARS = 1, 40
MAX_MONTHLY_SIP = Decimal("10000000")      # ₹1,00,00,000 a month
MAX_ANNUAL_STEP_UP = 0.5                   # 50% a year


class _WithDisclaimer(BaseModel):
    """Base for every response: the disclaimer is added by code, never by the LLM."""
    disclaimer: str = DISCLAIMER


# --- Risk profile ---

class ToleranceAnswersIn(BaseModel):
    """The three questionnaire answers, each 1 (most cautious) to 5 (most comfortable with risk)."""
    reaction_to_20pct_drop: int = Field(ge=1, le=5, examples=[3])   # 1 = sell everything ... 5 = buy more
    investing_experience: int = Field(ge=1, le=5, examples=[2])     # 1 = none ... 5 = many years
    return_vs_safety: int = Field(ge=1, le=5, examples=[3])         # 1 = safety first ... 5 = maximum growth


class InvestmentProfileUpdate(BaseModel):
    """PUT /api/investing/profile. Emergency-fund months are computed from the user's own spending."""
    answers: ToleranceAnswersIn
    liquid_savings: Decimal = Field(ge=0, max_digits=14, decimal_places=2, examples=[150000])
    existing_monthly_sip: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2, examples=[5000])
    income_stability: int = Field(ge=1, le=5, examples=[4])          # 1 = irregular ... 5 = very stable
    dependents: int = Field(ge=0, le=MAX_DEPENDENTS, examples=[1])
    horizon_years: float = Field(ge=0, le=MAX_HORIZON_YEARS, examples=[10])


class RiskProfileOut(BaseModel):
    """Both scores (0-100), both categories, and which one limited the final category."""
    tolerance_score: float
    capacity_score: float
    tolerance_category: RiskCategory
    capacity_category: RiskCategory
    category: RiskCategory
    limiting_factor: LimitingFactor


class InvestmentProfileResponse(_WithDisclaimer):
    answers: ToleranceAnswersIn
    liquid_savings: float
    existing_monthly_sip: float
    income_stability: int
    dependents: int
    horizon_years: float
    emergency_fund_months: float          # liquid savings / average monthly essential spending
    risk_profile: RiskProfileOut
    updated_at: datetime | None


# --- Holdings ---

class HoldingIn(BaseModel):
    asset_class: AssetClass
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2, examples=[50000])
    label: str = Field(default="", max_length=100, examples=["index fund"])   # '' = no label
    as_of: date | None = None                                                 # None = today

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        """'  ' and '' are the same (no label), so duplicates cannot hide behind whitespace."""
        return value.strip()


class HoldingsUpdate(BaseModel):
    """PUT /api/investing/holdings REPLACES the whole list (an empty list clears it)."""
    holdings: list[HoldingIn] = Field(max_length=MAX_HOLDINGS)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "HoldingsUpdate":
        seen: set[tuple[str, str]] = set()
        for holding in self.holdings:
            key = (holding.asset_class, holding.label.casefold())
            if key in seen:
                raise ValueError(f"duplicate holding: {holding.asset_class} {holding.label!r}")
            seen.add(key)
        return self


class HoldingOut(BaseModel):
    asset_class: AssetClass
    amount: float
    label: str
    as_of: date
    weight: float                         # share of the total portfolio, 0-1


class HoldingsResponse(_WithDisclaimer):
    holdings: list[HoldingOut]
    total: float
    weights_by_class: dict[AssetClass, float]


# --- Plan ---

class PlanRequest(BaseModel):
    """POST /api/investing/plan. A goal is optional; goal_years defaults to the profile horizon."""
    goal_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2, examples=[1000000])
    goal_years: float | None = Field(default=None, ge=GOAL_MIN_YEARS, le=GOAL_MAX_YEARS, examples=[8])

    @model_validator(mode="after")
    def _years_need_a_goal(self) -> "PlanRequest":
        if self.goal_years is not None and self.goal_amount is None:
            raise ValueError("goal_years needs a goal_amount")
        return self


class RebalanceRow(BaseModel):
    asset_class: AssetClass
    current_weight: float
    target_weight: float
    amount_change: float                  # rupees to add (+) or reduce (-) at the same total
    action: RebalanceAction


class SipOut(BaseModel):
    capacity_total: float                 # most the user can safely invest per month
    recommended_total: float
    change_vs_existing: float
    emergency_topup: float                # monthly amount reserved to fill the emergency fund
    limited_by: SipLimitedBy


class ProjectionOut(BaseModel):
    """Monte Carlo range at one monthly SIP. Scenarios under assumptions, not predictions."""
    monthly_sip: float
    years: float
    p10: float
    p50: float
    p90: float
    probability_of_goal: float | None     # None when no goal was given
    months_to_goal: int | None            # None: no goal, zero SIP, or over 100 years
    n_paths: int


class InstrumentTypesOut(BaseModel):
    asset_class: AssetClass
    instrument_types: list[str]           # instrument TYPES only, never named funds
    note: str


class AssetAssumptionOut(BaseModel):
    asset_class: AssetClass
    expected_return: float                # annual, 0.10 = 10%
    volatility: float                     # annual standard deviation


class AssumptionsResponse(_WithDisclaimer):
    """GET /api/investing/assumptions: the numbers every projection depends on."""
    assets: list[AssetAssumptionOut]
    sip_safety_factor: float
    mc_paths: int
    mc_seed: int
    note: str                             # "illustrative placeholders, not cited market data"


class InvestmentPlanResponse(_WithDisclaimer):
    id: int
    created_at: datetime | None
    risk_profile: RiskProfileOut
    target_allocation: dict[AssetClass, float]
    current_allocation: dict[AssetClass, float]
    rebalance: list[RebalanceRow]
    sip: SipOut
    projection_current: ProjectionOut     # at the existing SIP
    projection_recommended: ProjectionOut
    instruments: list[InstrumentTypesOut]
    assumptions: AssumptionsResponse
    warnings: list[str]                   # codes such as "forecast_missing", "forecast_stale"
    forecast_run_id: int | None
    explanation: str | None
    explanation_source: ExplanationSource
    llm_used: bool
    explanation_faithful: bool | None
    claims_compliant: bool | None
    engine_version: str


class PlanHistoryItem(BaseModel):
    id: int
    created_at: datetime | None
    category: RiskCategory
    recommended_sip: float
    explanation_source: ExplanationSource


class PlanHistoryResponse(_WithDisclaimer):
    plans: list[PlanHistoryItem]


# --- What-if simulation (never stored) ---

class SimulateRequest(BaseModel):
    """POST /api/investing/simulate. The number of paths and the seed come from settings."""
    monthly_sip: Decimal = Field(ge=0, le=MAX_MONTHLY_SIP, max_digits=14, decimal_places=2, examples=[10000])
    years: float = Field(ge=SIMULATE_MIN_YEARS, le=SIMULATE_MAX_YEARS, examples=[10])
    category: RiskCategory
    goal_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    annual_step_up: float = Field(default=0.0, ge=0, le=MAX_ANNUAL_STEP_UP)


class SimulateResponse(_WithDisclaimer):
    category: RiskCategory
    target_allocation: dict[AssetClass, float]
    expected_return: float                # portfolio-level, from the assumptions
    volatility: float
    projection: ProjectionOut
