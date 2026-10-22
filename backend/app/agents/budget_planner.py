# app/agents/budget_planner.py
#
# Purpose: the LLM layer of the Budget Planning Agent. It EXPLAINS a finished
# plan; it never computes one. The numbers come from
# services/budget_calculator.py, and this file only turns them into words.
#
# Flow for one plan:
#   1. Build a deterministic TEMPLATE explanation first (always available).
#   2. If an LLM is configured, make ONE call with the plan's facts (no retries;
#      the fallback provider is tried only if the primary fails).
#   3. Parse the JSON defensively, then run the faithfulness guard on the
#      summary and on every per-category reason. Any text that mentions a
#      rupee amount not in the plan is replaced by the template text.
#
# PRIVACY: the LLM receives only category names and aggregate numbers
# (limits, averages, income, savings). Never descriptions, payees or UPI IDs.
#
# This file knows nothing about the database or HTTP.

import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.explanation_guard import is_faithful
from app.core.config import settings
from app.llm.factory import get_llm_provider_chain

logger = logging.getLogger(__name__)

METHOD_BASELINE = "baseline"    # same value as services.budget_calculator.METHOD_BASELINE
_MAX_REASON_CHARS = 500          # budgets.reasoning is String(1000); keep well inside it
_MAX_SUMMARY_CHARS = 1500
_ZERO = Decimal("0")


@dataclass(frozen=True)
class BudgetFact:
    """One category of a finished plan: everything the explanation may talk about."""
    category: str
    limit: Decimal
    avg_monthly_spend: Decimal     # 0 for a baseline plan (no history)
    essential: bool


@dataclass(frozen=True)
class PlanContext:
    """Plan-level numbers (all already computed by the calculator)."""
    month: str
    method: str                    # 'history' | 'baseline'
    income: Decimal
    savings_target_pct: Decimal
    savings_amount: Decimal
    spendable: Decimal
    feasible: bool
    shortfall: Decimal
    months_used: int
    trim: Decimal                  # 0.10 = discretionary trimmed by 10%
    discretionary_scale: Decimal   # <1 = discretionary also scaled down to fit


@dataclass(frozen=True)
class PlanExplanation:
    """The words shown to the user, and how they were produced (for budget_runs)."""
    summary: str
    reasons: dict[str, str]        # category -> one sentence
    llm_used: bool                 # an LLM answered with usable JSON
    faithful: bool | None          # None when there was no LLM text to check
    replaced_texts: int = 0        # LLM texts swapped for the template by the guard
    provider: str | None = None


class MalformedExplanationError(Exception):
    """The model answered, but not with the JSON object we asked for."""


# --- Formatting helpers (shared by templates and the LLM facts) ---

def format_inr(amount: Decimal) -> str:
    """Decimal('4170.00') -> '₹4,170'; Decimal('3333.33') -> '₹3,333.33'."""
    text = f"{amount:,.2f}"
    return "₹" + (text[:-3] if text.endswith(".00") else text)


def _format_pct(value: Decimal) -> str:
    """Decimal('20.00') -> '20', Decimal('0.10') * 100 -> '10'."""
    return f"{value.normalize():f}"


def allowed_numbers(ctx: PlanContext, facts: list[BudgetFact]) -> list[Decimal]:
    """Every rupee amount an explanation is allowed to mention."""
    numbers = [ctx.income, ctx.savings_amount, ctx.spendable, ctx.shortfall, _ZERO]
    for fact in facts:
        numbers.extend((fact.limit, fact.avg_monthly_spend))
    return numbers


# --- Deterministic templates (used when the LLM is off, down, or unfaithful) ---

def template_reason(fact: BudgetFact, ctx: PlanContext) -> str:
    """One sentence explaining one category's limit, built only from plan numbers."""
    if ctx.method == METHOD_BASELINE:
        return (
            f"No spending history yet, so this is a starter share of your {format_inr(ctx.spendable)} "
            f"spendable income: {format_inr(fact.limit)}."
        )
    if fact.essential:
        return f"Essential spending, so it stays at your monthly average of {format_inr(fact.limit)}."
    if not ctx.feasible:
        return (
            f"Set to {format_inr(fact.limit)} because your essentials already exceed your spendable "
            f"income by {format_inr(ctx.shortfall)}."
        )
    trim = _format_pct(ctx.trim * 100)
    if ctx.discretionary_scale < 1:
        return (
            f"Your average is {format_inr(fact.avg_monthly_spend)}; trimmed {trim}% and scaled down "
            f"to fit your spendable income: {format_inr(fact.limit)}."
        )
    return f"Your average is {format_inr(fact.avg_monthly_spend)}; trimmed {trim}% to {format_inr(fact.limit)}."


def template_summary(ctx: PlanContext) -> str:
    """A short paragraph explaining the whole plan, built only from plan numbers."""
    saving = (
        f"after saving {format_inr(ctx.savings_amount)} ({_format_pct(ctx.savings_target_pct)}% of "
        f"your {format_inr(ctx.income)} income)"
    )
    if ctx.method == METHOD_BASELINE:
        return (
            f"There is no categorized spending before {ctx.month}, so your {format_inr(ctx.spendable)} "
            f"spendable income {saving} is split with a starter plan. Import and categorize statements, "
            f"then regenerate for a personal plan."
        )
    months = f"{ctx.months_used} month{'s' if ctx.months_used != 1 else ''}"
    if not ctx.feasible:
        return (
            f"Based on {months} of spending, your essentials cost {format_inr(ctx.shortfall)} more than "
            f"your {format_inr(ctx.spendable)} spendable income {saving}, so discretionary limits are "
            f"{format_inr(_ZERO)}. Consider a lower savings target or cutting an essential cost."
        )
    text = (
        f"Based on {months} of spending, essentials keep their average and discretionary categories are "
        f"trimmed {_format_pct(ctx.trim * 100)}% to fit your {format_inr(ctx.spendable)} spendable income {saving}."
    )
    if ctx.discretionary_scale < 1:
        text += " Discretionary limits were scaled down further so the total fits."
    return text


def template_explanation(ctx: PlanContext, facts: list[BudgetFact]) -> PlanExplanation:
    """The full deterministic explanation (no LLM)."""
    return PlanExplanation(
        summary=template_summary(ctx),
        reasons={fact.category: template_reason(fact, ctx) for fact in facts},
        llm_used=False,
        faithful=None,
    )


# --- LLM prompt and parsing ---

EXPLAIN_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You explain a monthly budget for a personal finance app used in India. Amounts are in INR.\n"
        "A rules engine already calculated every number below. The numbers are final.\n\n"
        "Rules:\n"
        "- Never change, recompute, add up or invent an amount. Mention only amounts that appear in the "
        "facts, written like ₹4,170.\n"
        "- Do not give investment, tax or legal advice.\n"
        "- summary: 2-3 short sentences on how the plan was built and whether it fits the income.\n"
        "- reasons: one sentence (at most 25 words) per category, keyed by the exact category name.\n"
        "- Respond with ONLY a JSON object. No markdown fences, no preamble.\n\n"
        'Format: {{"summary": "...", "reasons": {{"<category>": "..."}}}}'
    ),
    ("human", "Budget facts:\n{facts_json}"),
])


def _money(value: Decimal) -> float:
    return round(float(value), 2)


def build_facts_payload(ctx: PlanContext, facts: list[BudgetFact]) -> str:
    """The ONLY data sent to the LLM: category names and aggregate numbers."""
    return json.dumps(
        {
            "month": ctx.month,
            "method": ctx.method,
            "monthly_income": _money(ctx.income),
            "savings_target_pct": float(ctx.savings_target_pct),
            "savings_amount": _money(ctx.savings_amount),
            "spendable_income": _money(ctx.spendable),
            "feasible": ctx.feasible,
            "shortfall": _money(ctx.shortfall),
            "months_of_history": ctx.months_used,
            "discretionary_trim_pct": float(ctx.trim * 100),
            "discretionary_scaled_down": ctx.discretionary_scale < 1,
            "categories": [
                {
                    "category": f.category,
                    "limit": _money(f.limit),
                    "avg_monthly_spend": _money(f.avg_monthly_spend),
                    "essential": f.essential,
                }
                for f in facts
            ],
        },
        ensure_ascii=False,
    )


def _strip_code_fences(text: str) -> str:
    """Models often wrap JSON in ```json fences even when told not to."""
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def parse_explanation_response(raw: str, categories: list[str]) -> tuple[str | None, dict[str, str]]:
    """
    Turns the model's text into (summary, reasons), defensively.
    Raises MalformedExplanationError if it is not a JSON object.
    Reasons for categories that are not in the plan are ignored; category
    names are matched case-insensitively. A missing summary is returned as None.
    """
    try:
        data = json.loads(_strip_code_fences(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise MalformedExplanationError(f"Not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedExplanationError("Expected a JSON object.")

    summary = data.get("summary")
    summary = summary.strip()[:_MAX_SUMMARY_CHARS] if isinstance(summary, str) and summary.strip() else None

    canonical = {name.lower(): name for name in categories}
    reasons: dict[str, str] = {}
    raw_reasons = data.get("reasons")
    if isinstance(raw_reasons, dict):
        for key, text in raw_reasons.items():
            category = canonical.get(str(key).strip().lower())
            if category and isinstance(text, str) and text.strip() and category not in reasons:
                reasons[category] = text.strip()[:_MAX_REASON_CHARS]
    return summary, reasons


# --- The agent ---

class BudgetPlannerAgent:
    """
    Explains a finished budget with at most one LLM call per provider.

    `chain` (one fake) or `chains` (a list of (name, fake) pairs) can be injected
    for testing, so the unit tests never call a real API. `chains=[]` means
    "no LLM": every explanation comes from the templates.
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
        self.llm_calls = 0       # requests made by this agent (research metric: calls per plan)

    @classmethod
    def from_settings(cls) -> "BudgetPlannerAgent":
        """The real agent, or a template-only agent when no LLM provider can be built."""
        try:
            return cls()
        except Exception as exc:  # most often: API key missing for every configured provider
            logger.warning(f"Budget planner LLM unavailable ({exc}); using template explanations.")
            return cls(chains=[])

    def explain(self, ctx: PlanContext, facts: list[BudgetFact]) -> PlanExplanation:
        """Returns the explanation for one plan. Never raises: the template is the safety net."""
        template = template_explanation(ctx, facts)
        if not self._chains:
            return template

        payload = build_facts_payload(ctx, facts)
        for name, chain in self._chains:
            self.llm_calls += 1
            try:
                raw = chain.invoke({"facts_json": payload})
                summary, reasons = parse_explanation_response(raw, [f.category for f in facts])
            except Exception as exc:
                logger.error(f"Budget explanation failed via {name} ({type(exc).__name__}): {exc}")
                continue                      # try the next provider in the chain
            return self._apply_guard(summary, reasons, template, allowed_numbers(ctx, facts), name)

        logger.warning("Every LLM provider failed; using template explanations.")
        return template

    def _apply_guard(
        self,
        summary: str | None,
        reasons: dict[str, str],
        template: PlanExplanation,
        allowed: list[Decimal],
        provider: str,
    ) -> PlanExplanation:
        """Keeps each LLM text only if it passes the guard; otherwise uses the template text."""
        replaced = 0

        if summary is not None and not is_faithful(summary, allowed, self.tolerance):
            logger.warning(f"Guard rejected the LLM summary: {summary!r}")
            summary, replaced = None, replaced + 1
        final_summary = summary or template.summary

        final_reasons: dict[str, str] = {}
        for category, fallback in template.reasons.items():
            text = reasons.get(category)
            if text is not None and not is_faithful(text, allowed, self.tolerance):
                logger.warning(f"Guard rejected the LLM reason for {category}: {text!r}")
                text, replaced = None, replaced + 1
            # A reason the model simply forgot is filled in too, but is not "unfaithful"
            final_reasons[category] = text or fallback

        logger.info(f"Budget explained via {provider}; guard replaced {replaced} text(s)")
        return PlanExplanation(
            summary=final_summary,
            reasons=final_reasons,
            llm_used=True,
            faithful=replaced == 0,
            replaced_texts=replaced,
            provider=provider,
        )
