# app/agents/investment_explainer.py
#
# Purpose: the LLM layer of the Investment Advisor Agent (Phase 6). It EXPLAINS a
# finished plan; it never computes one. Every number comes from investment_service
# and the pure modules in services/investing/; this file only turns them into words.
#
# Flow for one plan (same pattern as forecast_explainer.py):
#   1. Turn the plan into FACTS: aggregates only, every number PRE-FORMATTED as the
#      text the user should see (whole rupees with Indian digit grouping such as
#      ₹12,34,567, and percentages such as 55%).
#   2. Build a deterministic TEMPLATE from those facts (always available).
#   3. If an LLM is configured, make ONE call (the fallback provider is tried only
#      if the primary fails) and run every guard on the answer:
#        - rupee amounts must exist in the facts (explanation_guard, ₹1 tolerance)
#        - percentages must exist in the facts (1 percentage point tolerance)
#        - no lakh / crore / k / L amounts (the rupee guard would mis-read them)
#        - no prohibited claims ("guaranteed", "risk-free"), via the injected checker
#      Any failure -> the template is used, and the plan records why.
#   4. The disclaimer is appended by code AFTER the guards (it says "not guarantees",
#      which the claims guard would flag).
#
# Layering: agents must not import services, so the claims checker
# (services/investing/claims_guard.find_prohibited_claims) is passed in by the
# service. PRIVACY: the LLM receives only aggregate numbers, categories, asset
# classes and instrument TYPES; never holding labels, names or transactions.
# This file knows nothing about the database or HTTP.

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.explanation_guard import (
    extract_percentages, extract_rupee_amounts, find_amount_shorthand, find_unsupported_amounts,
    find_unsupported_percentages,
)
from app.agents.forecast_explainer import parse_summary_response
from app.core.config import settings
from app.core.disclaimers import INVESTING_DISCLAIMER
from app.llm.factory import get_llm_provider_chain

logger = logging.getLogger(__name__)

PERCENT_TOLERANCE = 1.0                      # percentage points (spec 14.3)
ClaimsChecker = Callable[[str], list[str]]   # text -> prohibited phrases found

# Same order as services/investing/allocation.ASSET_CLASSES (agents must not import services)
_ASSET_ORDER = ("equity", "debt", "gold", "cash")

# Rejection reasons, logged and returned for the research metrics
REJECTED_AMOUNT = "amount"
REJECTED_PERCENTAGE = "percentage"
REJECTED_SHORTHAND = "shorthand"
REJECTED_CLAIMS = "claims"

# Plain-language text for each warning code of investment_service (no numbers in them)
WARNING_TEXT: dict[str, str] = {
    "forecast_missing": "No 30-day forecast was found, so the surplus is your income minus your budgets; "
                        "generate a forecast for a more careful estimate.",
    "forecast_stale": "Your latest forecast is old, so generate a new one before acting on this plan.",
    "surplus_unknown": "There is no forecast or budget to size a SIP from, so no SIP is suggested yet.",
    "essentials_unknown": "Your essential spending is unknown, so your emergency fund is treated as empty.",
    "no_holdings": "No holdings were entered, so there is nothing to rebalance yet.",
    "emergency_fill_slow": "Your surplus is smaller than the emergency top-up needed, so all of it goes to the "
                           "emergency fund for now, and filling it will take longer than planned.",
}


@dataclass(frozen=True)
class InvestmentExplanation:
    """The text shown to the user, and how it was produced (saved on investment_plans)."""
    text: str                              # final text, disclaimer included
    llm_used: bool                         # an LLM answered with usable JSON
    explanation_faithful: bool | None      # amounts, percentages and units passed; None = no LLM text
    claims_compliant: bool | None          # no prohibited claims; None = no LLM text
    llm_calls: int                         # requests made for this plan
    raw_prohibited_claims: int = 0         # prohibited phrases in the RAW LLM text
    provider: str | None = None
    rejected_for: tuple[str, ...] = ()


# --- Formatting (the exact strings the user and the LLM see) ---

def _whole(value: float | Decimal) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def indian_grouping(number: int) -> str:
    """1234567 -> '12,34,567' (last three digits, then pairs)."""
    digits = str(abs(number))
    if len(digits) <= 3:
        return digits
    head, groups = digits[:-3], [digits[-3:]]
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    groups.insert(0, head)
    return ",".join(groups)


def inr(value: float | Decimal) -> str:
    """Whole rupees, Indian grouping, sign first: -1234567.5 -> '-₹12,34,568'."""
    whole = _whole(value)
    return f"{'-' if whole < 0 else ''}₹{indian_grouping(whole)}"


def signed_inr(value: float | Decimal) -> str:
    """A change: '+₹4,235', '-₹2,000' or '₹0'."""
    return f"+{inr(value)}" if _whole(value) > 0 else inr(value)


def pct(fraction: float) -> str:
    """0.55 -> '55%', 0.0925 -> '9.3%' (at most one decimal)."""
    text = f"{fraction * 100:.1f}"
    return f"{text.removesuffix('.0')}%"


def _count(value: float, unit: str) -> str:
    """(15, 'year') -> '15 years', (1, 'year') -> '1 year', (2.5, 'month') -> '2.5 months'."""
    text = f"{value:.1f}".removesuffix(".0")
    return f"{text} {unit}{'' if text == '1' else 's'}"


# --- Facts (the ONLY data the LLM sees) ---

def _range(projection: dict) -> dict:
    chance = projection["probability_of_goal"]
    months = projection["months_to_goal"]
    return {
        "monthly_sip": inr(projection["monthly_sip"]),
        "low_10th_percentile": inr(projection["p10"]),
        "middle": inr(projection["p50"]),
        "high_90th_percentile": inr(projection["p90"]),
        "chance_of_reaching_goal": pct(chance) if chance is not None else None,
        "months_to_goal": _count(months, "month") if months is not None else None,
    }


def build_facts(result: dict, assumptions: dict) -> dict:
    """Plan result (investment_service) -> pre-formatted aggregate facts."""
    risk, sip, goal = result["risk_profile"], result["sip"], result["goal"]
    emergency, surplus = result["emergency_fund"], result["surplus"]
    current = result["current_allocation"]
    return {
        "risk_category": risk["category"],
        "limiting_factor": risk["limiting_factor"],
        "attitude_category": risk["tolerance_category"],
        "finances_category": risk["capacity_category"],
        "horizon": _count(goal["years"], "year"),
        "target_allocation": {name: pct(result["target_allocation"][name]) for name in _ASSET_ORDER},
        "current_allocation": (
            {name: pct(current[name]) for name in _ASSET_ORDER} if any(current.values()) else None
        ),
        "rebalancing": [
            {"asset_class": row["asset_class"], "action": row["action"], "amount": inr(abs(row["amount_change"]))}
            for row in result["rebalance"] if row["action"] != "hold"
        ],
        "emergency_fund": {
            "essential_monthly_spend": inr(emergency["essential_monthly_spend"]),
            "months_covered": _count(emergency["months_covered"], "month"),
            "target": _count(emergency["target_months"], "month"),
            "monthly_top_up": inr(sip["emergency_topup"]),
        },
        "surplus_source": surplus["source"],
        "monthly_surplus_low": inr(surplus["monthly_low"]),
        "share_of_surplus_used": pct(assumptions["sip_safety_factor"]),
        "sip": {
            "existing": inr(result["projection_current"]["monthly_sip"]),
            "most_affordable": inr(sip["capacity_total"]),
            "suggested": inr(sip["recommended_total"]),
            "change": signed_inr(sip["change_vs_existing"]),
            "limited_by": sip["limited_by"],
        },
        "goal": (
            {"amount": inr(goal["amount"]),
             "required_monthly_sip": inr(goal["required_sip"]) if goal["required_sip"] is not None else None}
            if goal["amount"] is not None else None
        ),
        "range_at_suggested_sip": _range(result["projection_recommended"]),
        "range_at_existing_sip": _range(result["projection_current"]),
        "assumed_annual_return": pct(result["portfolio"]["expected_return"]),
        "assumed_volatility": pct(result["portfolio"]["volatility"]),
        "instrument_types": {row["asset_class"]: row["instrument_types"] for row in result["instruments"]},
        "warnings": list(result["warnings"]),
    }


def build_facts_payload(facts: dict) -> str:
    return json.dumps(facts, ensure_ascii=False)


# --- Deterministic template (used when the LLM is off, down, or rejected) ---

def _limiting_sentence(facts: dict) -> str:
    if facts["limiting_factor"] == "capacity":
        return (f"Your finances, not your comfort with risk, are the limit right now: "
                f"your attitude alone would suggest {facts['attitude_category']}.")
    if facts["limiting_factor"] == "tolerance":
        return (f"Your comfort with risk sets the limit: "
                f"your finances alone could support {facts['finances_category']}.")
    return "Your attitude to risk and your finances point to the same category."


def template_text(facts: dict) -> str:
    """A short explanation built only from the facts' own strings (so it always passes the guards)."""
    split = ", ".join(f"{name} {share}" for name, share in facts["target_allocation"].items())
    parts = [
        f"Your suggested risk category is {facts['risk_category']}.",
        _limiting_sentence(facts),
        f"With a horizon of {facts['horizon']}, the target split is {split}.",
    ]
    if facts["rebalancing"]:
        moves = "; ".join(
            f"add {row['amount']} to {row['asset_class']}" if row["action"] == "add"
            else f"reduce {row['asset_class']} by {row['amount']}"
            for row in facts["rebalancing"]
        )
        parts.append(f"To move towards it at the same total: {moves}.")
    elif facts["current_allocation"] is not None:
        parts.append("Your current holdings are close enough to the target that no rebalancing is needed.")

    emergency = facts["emergency_fund"]
    if emergency["monthly_top_up"] != inr(0):
        parts.append(
            f"Your savings cover {emergency['months_covered']} of essential spending "
            f"({emergency['essential_monthly_spend']} a month), so {emergency['monthly_top_up']} a month "
            f"goes to the emergency fund first, until it covers {emergency['target']}."
        )

    sip = facts["sip"]
    if facts["surplus_source"] != "none":
        basis = ("the low end of your forecast monthly surplus" if facts["surplus_source"] == "forecast"
                 else "your income minus your budgets")
        parts.append(
            f"Using {facts['share_of_surplus_used']} of {basis} ({facts['monthly_surplus_low']}), you could invest "
            f"up to {sip['most_affordable']} a month; the suggested SIP is {sip['suggested']}, a change of "
            f"{sip['change']} from your current {sip['existing']}."
        )
    goal = facts["goal"]
    if goal is not None and goal["required_monthly_sip"] is not None:
        parts.append(
            f"Reaching {goal['amount']} in {facts['horizon']} needs about {goal['required_monthly_sip']} "
            f"a month at the assumed return."
        )

    suggested = facts["range_at_suggested_sip"]
    if sip["suggested"] != inr(0):
        chance = suggested["chance_of_reaching_goal"]
        parts.append(
            f"At the suggested SIP, simulated outcomes after {facts['horizon']} range from "
            f"{suggested['low_10th_percentile']} (10th percentile) to {suggested['high_90th_percentile']} "
            f"(90th percentile), with a middle of {suggested['middle']}"
            + (f", and a {chance} chance of reaching your goal." if chance is not None else ".")
        )
    parts.append(
        f"These are scenarios under assumed returns (about {facts['assumed_annual_return']} a year with "
        f"{facts['assumed_volatility']} volatility for this mix), not predictions; real returns can be lower."
    )
    parts.extend(WARNING_TEXT[code] for code in facts["warnings"] if code in WARNING_TEXT)
    return " ".join(parts)


def with_disclaimer(text: str) -> str:
    return f"{text}\n\n{INVESTING_DISCLAIMER}"


# --- LLM prompt ---

EXPLAIN_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You explain an investment plan for an educational personal finance app used in India.\n"
        "A planning engine already calculated every number below. The numbers are final.\n\n"
        "Rules:\n"
        "- Copy amounts and percentages EXACTLY as written in the facts (for example ₹12,34,567 or 55%). "
        "Never round, convert, add up or invent one.\n"
        "- Never write amounts in lakh, crore, k or L.\n"
        "- Say what limits the risk category: limiting_factor 'capacity' means their finances, 'tolerance' "
        "means their comfort with risk, 'both' means the two agree.\n"
        "- Say that projections are scenarios under the stated assumptions, not predictions.\n"
        "- Never promise anything. Do not use the words guaranteed, risk-free, assured, certain, surely, "
        "for sure, locked in or completely safe, not even to deny them.\n"
        "- Mention instrument types only as given. Never name a specific fund, company or stock.\n"
        "- If there are warnings, mention them plainly.\n"
        "- Do not add a disclaimer; the app adds one.\n"
        "- summary: 4-6 short, plain sentences.\n"
        "- Respond with ONLY a JSON object. No markdown fences, no preamble.\n\n"
        'Format: {{"summary": "..."}}'
    ),
    ("human", "Plan facts:\n{facts_json}"),
])


# --- The agent ---

class InvestmentExplainer:
    """
    Explains a finished plan with at most one LLM call per provider.

    `find_claims` is the claims checker (injected: agents must not import services).
    `chain` (one fake) or `chains` (a list of (name, fake) pairs) can be injected for
    testing, so unit tests never call a real API. `chains=[]` means "no LLM".
    """

    def __init__(
        self, find_claims: ClaimsChecker, chain=None, chains: list[tuple[str, object]] | None = None,
        tolerance: float | None = None,
    ) -> None:
        self._find_claims = find_claims
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

    @classmethod
    def from_settings(cls, find_claims: ClaimsChecker) -> "InvestmentExplainer":
        """The real explainer, or template-only when switched off or no provider can be built."""
        if not settings.INVEST_USE_LLM_EXPLANATIONS:
            return cls(find_claims, chains=[])
        try:
            return cls(find_claims)
        except Exception as exc:  # most often: API key missing for every configured provider
            logger.warning(f"Investment explainer LLM unavailable ({exc}); using the template.")
            return cls(find_claims, chains=[])

    def check(self, text: str, facts_payload: str) -> tuple[list[str], list[str]]:
        """(numeric problems, prohibited claims) of one text. Public for the evaluation scripts."""
        problems: list[str] = []
        if find_unsupported_amounts(text, extract_rupee_amounts(facts_payload), self.tolerance):
            problems.append(REJECTED_AMOUNT)
        if find_unsupported_percentages(text, extract_percentages(facts_payload), PERCENT_TOLERANCE):
            problems.append(REJECTED_PERCENTAGE)
        if find_amount_shorthand(text):
            problems.append(REJECTED_SHORTHAND)
        return problems, self._find_claims(text)

    def explain(self, result: dict, assumptions: dict) -> InvestmentExplanation:
        """Returns the explanation for one plan. Never raises: the template is the safety net."""
        facts = build_facts(result, assumptions)
        template = with_disclaimer(template_text(facts))
        if not self._chains:
            return InvestmentExplanation(template, llm_used=False, explanation_faithful=None,
                                         claims_compliant=None, llm_calls=0)

        payload = build_facts_payload(facts)
        calls = 0
        for name, chain in self._chains:
            calls += 1
            try:
                summary = parse_summary_response(chain.invoke({"facts_json": payload}))
            except Exception as exc:
                logger.error(f"Investment explanation failed via {name} ({type(exc).__name__}): {exc}")
                continue                      # try the next provider in the chain
            problems, claims = self.check(summary, payload)
            if not problems and not claims:
                logger.info(f"Investment plan explained via {name}; all guards passed")
                return InvestmentExplanation(with_disclaimer(summary), llm_used=True, explanation_faithful=True,
                                             claims_compliant=True, llm_calls=calls, provider=name)
            rejected = tuple(problems) + ((REJECTED_CLAIMS,) if claims else ())
            logger.warning(f"Guards rejected the LLM investment summary ({', '.join(rejected)}): {summary!r}")
            return InvestmentExplanation(
                template, llm_used=True, explanation_faithful=not problems, claims_compliant=not claims,
                llm_calls=calls, raw_prohibited_claims=len(claims), provider=name, rejected_for=rejected,
            )

        logger.warning("Every LLM provider failed; using the template investment explanation.")
        return InvestmentExplanation(template, llm_used=False, explanation_faithful=None, claims_compliant=None,
                                     llm_calls=calls)
