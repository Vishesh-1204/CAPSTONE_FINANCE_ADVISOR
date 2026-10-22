# app/agents/forecast_explainer.py
#
# Purpose: the LLM layer of the Financial Forecast Agent (Phase 5). It EXPLAINS
# a finished forecast; it never computes one. Every number comes from the
# forecasting service (Python), and this file only turns them into words.
#
# Flow for one forecast (same pattern as agents/budget_planner.py):
#   1. Build a deterministic TEMPLATE summary first (always available).
#   2. If an LLM is configured, make ONE call with the forecast's facts (the
#      fallback provider is tried only if the primary fails).
#   3. Parse the JSON defensively and run the faithfulness guard
#      (explanation_guard.is_faithful): if the text mentions a rupee amount that
#      is not one of the forecast's numbers, the template is used instead and
#      the run is logged as explanation_faithful=False.
#
# PRIVACY: the LLM receives only aggregate numbers, dates, model names and
# category names. Never descriptions, payees, UPI IDs or account details
# (ForecastFacts has no field that could carry them).
#
# This file knows nothing about the database or HTTP.

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.budget_planner import MalformedExplanationError, format_inr
from app.agents.explanation_guard import is_faithful
from app.core.config import settings
from app.llm.factory import get_llm_provider_chain

logger = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 2000
_MAX_WARNINGS_IN_TEXT = 3
_ZERO = Decimal("0")

# Words shown to the user for each model name (forecast_models.default_model_registry)
MODEL_LABELS: dict[str, str] = {
    "naive_last": "last month's spending",
    "moving_average_3": "the average of your last 3 months",
    "exp_smoothing": "a weighted average that favours recent months",
    "seasonal_naive_12": "the same month last year",
}

# Same strings as models/forecast.py (agents must not import models)
_SOURCE_STATEMENT = "statement"
_INCOME_RECURRING = "recurring"
_INCOME_PROFILE = "profile"


@dataclass(frozen=True)
class RecurringFact:
    """One active recurring item, without its payee (privacy)."""
    kind: str                  # 'income' | 'fixed_expense' | 'investment'
    category: str | None
    amount: Decimal
    day_of_month: int


@dataclass(frozen=True)
class BudgetWarningFact:
    """A category projected to end its month above its limit."""
    month: str
    category: str
    projected: Decimal
    limit: Decimal
    over_by: Decimal


@dataclass(frozen=True)
class ForecastFacts:
    """Every finished number of one forecast: all an explanation may talk about."""
    as_of: date
    horizon_days: int
    end_date: date
    model_name: str
    history_months: int
    low_confidence: bool
    model_selected_by_backtest: bool      # False = fallback model (too little history to compare)
    opening_balance: Decimal
    opening_balance_source: str           # 'request' | 'statement'
    opening_balance_date: date | None
    income_source: str                    # 'recurring' | 'profile' | 'none'
    expected_income: Decimal
    fixed_expenses: Decimal
    variable_expenses: Decimal
    variable_lower: Decimal
    variable_upper: Decimal
    investments: Decimal
    net_cashflow: Decimal
    end_balance: Decimal
    end_balance_lower: Decimal
    end_balance_upper: Decimal
    cash_crunch_date: date | None
    recurring: list[RecurringFact] = field(default_factory=list)
    budget_warnings: list[BudgetWarningFact] = field(default_factory=list)


@dataclass(frozen=True)
class ForecastExplanation:
    """The summary shown to the user, and how it was produced (saved on forecast_runs)."""
    summary: str
    llm_used: bool                 # an LLM answered with usable JSON
    faithful: bool | None          # None when there was no LLM text to check
    provider: str | None = None


# --- Formatting ---

def inr(amount: Decimal) -> str:
    """Whole rupees, sign in front: Decimal('-1234.56') -> '-₹1,235'. The guard reads the digits."""
    rounded = amount.quantize(Decimal("1"))
    text = format_inr(abs(rounded))
    return f"-{text}" if rounded < 0 else text


def _day(value: date) -> str:
    """date(2026, 11, 5) -> '5 Nov 2026' (no currency marker, so the guard ignores it)."""
    return f"{value.day} {value.strftime('%b %Y')}"


def allowed_numbers(facts: ForecastFacts) -> list[Decimal]:
    """Every rupee amount an explanation may mention (absolute values: the guard reads digits only)."""
    numbers = [
        facts.opening_balance, facts.expected_income, facts.fixed_expenses, facts.variable_expenses,
        facts.variable_lower, facts.variable_upper, facts.investments, facts.net_cashflow,
        facts.end_balance, facts.end_balance_lower, facts.end_balance_upper, _ZERO,
    ]
    for item in facts.recurring:
        numbers.append(item.amount)
    for warning in facts.budget_warnings:
        numbers.extend((warning.projected, warning.limit, warning.over_by))
    return [abs(n) for n in numbers]


# --- Deterministic template (used when the LLM is off, down, or unfaithful) ---

def template_summary(facts: ForecastFacts) -> str:
    """A short paragraph built only from the forecast's numbers."""
    if facts.income_source == _INCOME_RECURRING:
        income = f"{inr(facts.expected_income)} of income from your regular credits"
    elif facts.income_source == _INCOME_PROFILE:
        income = (f"{inr(facts.expected_income)} of income from your financial profile, spread evenly "
                  f"because no regular salary was detected")
    else:
        income = "no income, because none was detected and no financial profile is set"
    if facts.opening_balance_source == _SOURCE_STATEMENT and facts.opening_balance_date is not None:
        opening = f"your statement balance of {inr(facts.opening_balance)} on {_day(facts.opening_balance_date)}"
    else:
        opening = f"the {inr(facts.opening_balance)} balance you entered"

    parts = [
        f"Over the next {facts.horizon_days} days (until {_day(facts.end_date)}) you can expect {income}, "
        f"{inr(facts.fixed_expenses)} of fixed bills, about {inr(facts.variable_expenses)} of everyday "
        f"spending (likely between {inr(facts.variable_lower)} and {inr(facts.variable_upper)}) and "
        f"{inr(facts.investments)} going into investments, a net cash flow of {inr(facts.net_cashflow)}.",
        f"Starting from {opening}, your balance is projected to end near {inr(facts.end_balance)} "
        f"(between {inr(facts.end_balance_lower)} and {inr(facts.end_balance_upper)}).",
    ]
    if facts.cash_crunch_date is not None:
        parts.append(
            f"Warning: if spending is on the high side, your balance could fall below {inr(_ZERO)} "
            f"around {_day(facts.cash_crunch_date)}."
        )
    else:
        parts.append(f"Even if spending is on the high side, your balance stays above {inr(_ZERO)}.")
    if facts.budget_warnings:
        listed = "; ".join(
            f"{w.category} in {w.month} ({inr(w.projected)} against a {inr(w.limit)} limit)"
            for w in facts.budget_warnings[:_MAX_WARNINGS_IN_TEXT]
        )
        parts.append(f"Projected to go over budget: {listed}.")
    label = MODEL_LABELS.get(facts.model_name, facts.model_name)
    if facts.model_selected_by_backtest:
        parts.append(f"Everyday spending is based on {label}, which had the smallest error on your past months.")
    else:
        parts.append(f"Everyday spending is based on {label}, because there is not yet enough history to compare models.")
    if facts.low_confidence:
        months = f"{facts.history_months} month{'s' if facts.history_months != 1 else ''}"
        parts.append(f"This uses only {months} of history, so treat the range as rough.")
    return " ".join(parts)


# --- LLM prompt and parsing ---

EXPLAIN_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You explain a cash-flow forecast for a personal finance app used in India. Amounts are in INR.\n"
        "A forecasting engine already calculated every number below. The numbers are final.\n\n"
        "Rules:\n"
        "- Never change, recompute, add up or invent an amount. Mention only amounts that appear in the "
        "facts, written in whole rupees like ₹4,170.\n"
        "- Say that ranges are estimates. If cash_crunch_date is set, warn about it clearly.\n"
        "- If low_confidence is true, say the forecast is rough because the history is short.\n"
        "- Do not give investment, tax or legal advice.\n"
        "- summary: 3-5 short, plain sentences.\n"
        "- Respond with ONLY a JSON object. No markdown fences, no preamble.\n\n"
        'Format: {{"summary": "..."}}'
    ),
    ("human", "Forecast facts:\n{facts_json}"),
])


def _money(value: Decimal) -> float:
    return round(float(value), 2)


def build_facts_payload(facts: ForecastFacts) -> str:
    """The ONLY data sent to the LLM: aggregate numbers, dates, the model and category names."""
    return json.dumps(
        {
            "forecast_date": facts.as_of.isoformat(),
            "horizon_days": facts.horizon_days,
            "end_date": facts.end_date.isoformat(),
            "history_months": facts.history_months,
            "low_confidence": facts.low_confidence,
            "spending_model": MODEL_LABELS.get(facts.model_name, facts.model_name),
            "opening_balance": _money(facts.opening_balance),
            "opening_balance_source": facts.opening_balance_source,
            "income_source": facts.income_source,
            "expected_income": _money(facts.expected_income),
            "fixed_expenses": _money(facts.fixed_expenses),
            "everyday_spending": {
                "expected": _money(facts.variable_expenses),
                "low": _money(facts.variable_lower),
                "high": _money(facts.variable_upper),
            },
            "investments": _money(facts.investments),
            "net_cashflow": _money(facts.net_cashflow),
            "end_balance": {
                "expected": _money(facts.end_balance),
                "low": _money(facts.end_balance_lower),
                "high": _money(facts.end_balance_upper),
            },
            "cash_crunch_date": facts.cash_crunch_date.isoformat() if facts.cash_crunch_date else None,
            "recurring": [
                {"kind": r.kind, "category": r.category, "amount": _money(r.amount), "day_of_month": r.day_of_month}
                for r in facts.recurring
            ],
            "budget_warnings": [
                {"month": w.month, "category": w.category, "projected": _money(w.projected),
                 "limit": _money(w.limit), "over_by": _money(w.over_by)}
                for w in facts.budget_warnings
            ],
        },
        ensure_ascii=False,
    )


def parse_summary_response(raw: str) -> str:
    """Returns the summary text. Raises MalformedExplanationError for anything else."""
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MalformedExplanationError(f"Not valid JSON: {exc}") from exc
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        raise MalformedExplanationError("Expected a JSON object with a non-empty 'summary'.")
    return summary.strip()[:_MAX_SUMMARY_CHARS]


# --- The agent ---

class ForecastExplainer:
    """
    Explains a finished forecast with at most one LLM call per provider.

    `chain` (one fake) or `chains` (a list of (name, fake) pairs) can be injected
    for testing, so unit tests never call a real API. `chains=[]` means "no LLM".
    """

    def __init__(self, chain=None, chains: list[tuple[str, object]] | None = None, tolerance: float | None = None) -> None:
        if chains is not None:
            self._chains = list(chains)
        elif chain is not None:
            self._chains = [("injected", chain)]
        else:
            # Raises ValueError if NO provider can be built (e.g. no API keys at all)
            self._chains = [
                (provider.provider_name, EXPLAIN_PROMPT | provider.get_chat_model() | StrOutputParser())
                for provider in get_llm_provider_chain()
            ]
        self.tolerance = settings.BUDGET_GUARD_TOLERANCE if tolerance is None else tolerance
        self.llm_calls = 0       # research metric: LLM calls per forecast

    @classmethod
    def from_settings(cls) -> "ForecastExplainer":
        """The real explainer, or template-only when switched off or no provider can be built."""
        if not settings.FORECAST_USE_LLM_EXPLANATIONS:
            return cls(chains=[])
        try:
            return cls()
        except Exception as exc:  # most often: API key missing for every configured provider
            logger.warning(f"Forecast explainer LLM unavailable ({exc}); using the template.")
            return cls(chains=[])

    def explain(self, facts: ForecastFacts) -> ForecastExplanation:
        """Returns the explanation for one forecast. Never raises: the template is the safety net."""
        template = template_summary(facts)
        if not self._chains:
            return ForecastExplanation(template, llm_used=False, faithful=None)

        payload = build_facts_payload(facts)
        for name, chain in self._chains:
            self.llm_calls += 1
            try:
                summary = parse_summary_response(chain.invoke({"facts_json": payload}))
            except Exception as exc:
                logger.error(f"Forecast explanation failed via {name} ({type(exc).__name__}): {exc}")
                continue                      # try the next provider in the chain
            if not is_faithful(summary, allowed_numbers(facts), self.tolerance):
                logger.warning(f"Guard rejected the LLM forecast summary: {summary!r}")
                return ForecastExplanation(template, llm_used=True, faithful=False, provider=name)
            logger.info(f"Forecast explained via {name}; guard passed")
            return ForecastExplanation(summary, llm_used=True, faithful=True, provider=name)

        logger.warning("Every LLM provider failed; using the template forecast explanation.")
        return ForecastExplanation(template, llm_used=False, faithful=None)
