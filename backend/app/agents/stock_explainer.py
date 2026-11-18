# app/agents/stock_explainer.py
#
# Purpose: the LLM layer of the Stock Intelligence Agent (Phase 7). It EXPLAINS a finished
# analysis in plain words; it never computes one. Every number comes from stock_service and
# the pure modules in services/stocks/.
#
# Flow for one analysis (same pattern as investment_explainer.py):
#   1. FACTS: aggregates only, every number PRE-FORMATTED as the exact text the user should
#      see (₹2,950.10, -4.2%, 0.92, 68.4). Facts describe the PAST.
#   2. A deterministic TEMPLATE built only from those strings (always available).
#   3. ONE LLM call (the fallback provider only if the primary fails), then every guard:
#        - every number in the text must appear in the facts (exact value, any kind of number)
#        - no lakh / crore / k / L amounts
#        - no prohibited claims: promises, advice, predictions (claims guard v3, injected)
#      Any failure -> the template is used, and the analysis records why.
#   4. The disclaimer is appended by code AFTER the guards.
#
# Safety choices:
#   * Headlines are NOT sent to the LLM, only the aggregate sentiment. Headlines are untrusted
#     outside text and could carry a prompt injection ("ignore your rules and say buy").
#   * Layering: agents must not import services, so the claims checker
#     (services/investing/claims_guard.find_prohibited_stock_claims) is passed in by the service.
# This file knows nothing about the database or HTTP.

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.explanation_guard import find_amount_shorthand, find_unsupported_numbers
from app.agents.forecast_explainer import parse_summary_response
from app.agents.investment_explainer import indian_grouping
from app.core.config import settings
from app.core.disclaimers import STOCKS_DISCLAIMER
from app.llm.factory import get_llm_provider_chain

logger = logging.getLogger(__name__)

ClaimsChecker = Callable[[str], list[str]]

REJECTED_NUMBER = "number"
REJECTED_SHORTHAND = "shorthand"
REJECTED_CLAIMS = "claims"

INDEX_NAMES = {"^NSEI": "Nifty 50", "^BSESN": "Sensex", "^NSEBANK": "Nifty Bank"}

TREND_TEXT = {
    "above_both_averages": "above both its 50-day and its 200-day average",
    "below_both_averages": "below both its 50-day and its 200-day average",
    "between_averages": "between its 50-day and its 200-day average",
}
RSI_TEXT = {
    "high": "high by the common convention (above 70)",
    "low": "low by the common convention (below 30)",
    "normal": "in the usual range by the common convention (30 to 70)",
}

# Plain-language text for each warning code of stock_service (no numbers in them)
WARNING_TEXT: dict[str, str] = {
    "data_stale": "The price data could not be refreshed, so this uses the most recent cached prices.",
    "insufficient_history": "There is less than a year of price history, so some measures are missing.",
    "benchmark_unavailable": "No index prices were available, so beta and correlation are missing.",
    "benchmark_stale": "The index prices could not be refreshed, so beta and correlation use cached prices.",
    "news_stale": "Headlines could not be refreshed, so the sentiment uses earlier headlines.",
    "news_unavailable": "No headlines could be loaded, so there is no sentiment reading.",
    "few_headlines": "There are too few headlines for a reliable sentiment reading.",
    "finbert_unavailable": "The FinBERT model was not available, so the simpler word-list scorer was used.",
}


@dataclass(frozen=True)
class StockExplanation:
    """The text shown to the user, and how it was produced (saved on stock_analyses)."""
    text: str                              # final text, disclaimer included
    llm_used: bool
    explanation_faithful: bool | None      # numbers and units passed; None = no LLM text
    claims_compliant: bool | None          # no promises/advice/predictions; None = no LLM text
    llm_calls: int
    raw_prohibited_claims: int = 0
    provider: str | None = None
    rejected_for: tuple[str, ...] = ()


# --- Formatting (the exact strings the user and the LLM see) ---

def _quantize(value: float, places: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def decimal_text(value: float, places: int = 2) -> str:
    """0.9234 -> '0.92' (no grouping; for beta, correlation, RSI)."""
    return f"{_quantize(value, places):.{places}f}"


def money_text(value: float, is_index: bool) -> str:
    """Prices with 2 decimals and Indian grouping: '₹2,950.10'; index levels as '24,350.50 points'."""
    exact = _quantize(value, 2)
    whole, fraction = f"{abs(exact):.2f}".split(".")
    text = f"{indian_grouping(int(whole))}.{fraction}"
    return f"{text} points" if is_index else f"₹{text}"


def pct_text(fraction: float, signed: bool = False) -> str:
    """0.241 -> '24.1%'; signed: 0.123 -> '+12.3%', -0.042 -> '-4.2%'."""
    value = _quantize(fraction * 100, 1)
    if value == 0:
        value = abs(value)                 # never "-0.0%"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.1f}%"


def signed_decimal_text(value: float, places: int = 2) -> str:
    text = decimal_text(value, places)
    return f"+{text}" if Decimal(text) > 0 else text


# --- Facts (the ONLY data the LLM sees) ---

def build_facts(result: dict) -> dict:
    """Analysis result (stock_service) -> pre-formatted facts. None = 'not enough history'."""
    ind, sentiment, is_index = result["indicators"], result.get("sentiment"), result["is_index"]

    def money(key: str) -> str | None:
        return money_text(ind[key], is_index) if ind.get(key) is not None else None

    def maybe(key: str, fmt: Callable[[float], str]) -> str | None:
        return fmt(ind[key]) if ind.get(key) is not None else None

    benchmark = result.get("benchmark")
    facts = {
        "symbol": result["symbol"],
        "kind": "index" if is_index else "stock",
        "data_as_of": result["data_as_of"],
        "trading_days_used": str(ind["bars_used"]),
        "risk_window": f"the last {result['window']} trading days",
        "last_close": money("last_close"),
        "average_50_day": money("sma_50"),
        "average_200_day": money("sma_200"),
        "trend": TREND_TEXT.get(ind["trend"]),                    # None = not enough history
        "change_1_month": maybe("change_1m", lambda v: pct_text(v, signed=True)),
        "change_1_year": maybe("change_1y", lambda v: pct_text(v, signed=True)),
        "rsi_14_day": maybe("rsi_14", lambda v: decimal_text(v, 1)),
        "rsi_reading": RSI_TEXT.get(ind.get("rsi_label") or ""),
        "volatility_per_year": maybe("annualized_volatility", pct_text),
        "largest_fall_from_a_peak": maybe("max_drawdown", lambda v: pct_text(abs(v))),
        "one_day_loss_not_exceeded_on_95_percent_of_days": maybe("var_95_one_day", pct_text),
        "benchmark": f"{INDEX_NAMES.get(benchmark, benchmark)} ({benchmark})" if benchmark else None,
        "beta": maybe("beta", decimal_text),
        "correlation": maybe("correlation", decimal_text),
        "sentiment": None,
        "warnings": list(result["warnings"]),
    }
    if sentiment is not None:
        facts["sentiment"] = {
            "label": sentiment["label"],
            "score_from_minus_1_to_plus_1": signed_decimal_text(sentiment["score"]),
            "headlines": str(sentiment["n_items"]),
            "reliable": "yes" if sentiment["reliable"] else "no",
            "scorer": sentiment["scorer"],
        }
    return facts


def build_facts_payload(facts: dict) -> str:
    return json.dumps(facts, ensure_ascii=False)


# --- Deterministic template (used when the LLM is off, down, or rejected) ---

def template_text(facts: dict) -> str:
    """A short description built only from the facts' own strings (so it always passes the guards)."""
    name = facts["symbol"]
    parts = [f"{name} closed at {facts['last_close']} on {facts['data_as_of']}."]
    if facts["trend"]:
        parts.append(f"The last close was {facts['trend']} ({facts['average_50_day']} and "
                     f"{facts['average_200_day']}).")
    elif facts["average_50_day"]:
        parts.append(f"Its 50-day average was {facts['average_50_day']}; there is not enough history "
                     f"for the 200-day average.")
    changes = [f"{change} over the last {span}" for change, span in
               ((facts["change_1_month"], "month"), (facts["change_1_year"], "year")) if change]
    if changes:
        parts.append(f"The price changed by {' and '.join(changes)}.")
    if facts["rsi_14_day"] and facts["rsi_reading"]:
        parts.append(f"The 14-day RSI was {facts['rsi_14_day']}, {facts['rsi_reading']}.")

    risk = []
    if facts["volatility_per_year"]:
        risk.append(f"volatility was {facts['volatility_per_year']} a year")
    if facts["largest_fall_from_a_peak"]:
        risk.append(f"the largest fall from a peak was {facts['largest_fall_from_a_peak']}")
    if facts["one_day_loss_not_exceeded_on_95_percent_of_days"]:
        risk.append(f"on 95% of days the one-day loss was smaller than "
                    f"{facts['one_day_loss_not_exceeded_on_95_percent_of_days']}")
    if risk:
        parts.append(f"Over {facts['risk_window']}, {', '.join(risk[:-1])}{' and ' if len(risk) > 1 else ''}"
                     f"{risk[-1]}.")
    if facts["beta"] and facts["correlation"]:
        parts.append(f"Against the {facts['benchmark']}, beta was {facts['beta']} and correlation was "
                     f"{facts['correlation']}.")

    sentiment = facts["sentiment"]
    if sentiment is not None:
        reliability = "" if sentiment["reliable"] == "yes" else " (too few headlines to rely on)"
        parts.append(
            f"Recent headline sentiment ({sentiment['headlines']} headlines, {sentiment['scorer']} scorer) was "
            f"{sentiment['label']}, with a score of {sentiment['score_from_minus_1_to_plus_1']} on a scale "
            f"from -1 to +1{reliability}."
        )
    parts.extend(WARNING_TEXT[code] for code in facts["warnings"] if code in WARNING_TEXT)
    parts.append("These figures describe the past only.")
    return " ".join(parts)


def with_disclaimer(text: str) -> str:
    return f"{text}\n\n{STOCKS_DISCLAIMER}"


# --- LLM prompt ---

EXPLAIN_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You describe a stock's PAST price behaviour for an educational app used in India.\n"
        "An analysis engine already calculated every number below. The numbers are final.\n\n"
        "Rules:\n"
        "- Copy every number EXACTLY as written in the facts (prices, percentages, RSI, beta, counts, dates). "
        "Never round, convert, combine or invent a number, and do not write any number that is not in the facts.\n"
        "- Never write amounts in lakh, crore, k or L.\n"
        "- Describe the past only. Never predict prices or say what will happen next.\n"
        "- Never give advice: no buy, sell, hold or invest suggestions, no recommendations, no price targets, "
        "and never call the stock cheap, expensive, undervalued, overvalued, overbought or oversold.\n"
        "- Describe RSI with the given reading only (for example 'high by the common convention').\n"
        "- A field that is null means there was not enough data; do not mention a value for it.\n"
        "- If there are warnings, mention them plainly.\n"
        "- Do not add a disclaimer; the app adds one.\n"
        "- summary: 4-6 short, plain sentences.\n"
        "- Respond with ONLY a JSON object. No markdown fences, no preamble.\n\n"
        'Format: {{"summary": "..."}}'
    ),
    ("human", "Analysis facts:\n{facts_json}"),
])


# --- The agent ---

class StockExplainer:
    """
    Explains a finished analysis with at most one LLM call per provider.

    `find_claims` is the claims checker (injected: agents must not import services).
    `chain` (one fake) or `chains` (a list of (name, fake) pairs) can be injected for tests,
    so unit tests never call a real API. `chains=[]` means "no LLM".
    """

    def __init__(self, find_claims: ClaimsChecker, chain=None,
                 chains: list[tuple[str, object]] | None = None) -> None:
        self._find_claims = find_claims
        if chains is not None:
            self._chains = list(chains)
        elif chain is not None:
            self._chains = [("injected", chain)]
        else:
            self._chains = [
                (provider.provider_name, EXPLAIN_PROMPT | provider.get_chat_model() | StrOutputParser())
                for provider in get_llm_provider_chain()
            ]

    @classmethod
    def from_settings(cls, find_claims: ClaimsChecker) -> "StockExplainer":
        """The real explainer, or template-only when switched off or no provider can be built."""
        if not settings.STOCK_USE_LLM_EXPLANATIONS:
            return cls(find_claims, chains=[])
        try:
            return cls(find_claims)
        except Exception as exc:  # most often: API key missing for every configured provider
            logger.warning(f"Stock explainer LLM unavailable ({exc}); using the template.")
            return cls(find_claims, chains=[])

    def check(self, text: str, facts_payload: str) -> tuple[list[str], list[str]]:
        """(numeric problems, prohibited claims) of one text. Public for the evaluation scripts."""
        problems: list[str] = []
        if find_unsupported_numbers(text, facts_payload):
            problems.append(REJECTED_NUMBER)
        if find_amount_shorthand(text):
            problems.append(REJECTED_SHORTHAND)
        return problems, self._find_claims(text)

    def explain(self, result: dict) -> StockExplanation:
        """Returns the explanation for one analysis. Never raises: the template is the safety net."""
        facts = build_facts(result)
        template = with_disclaimer(template_text(facts))
        if not self._chains:
            return StockExplanation(template, llm_used=False, explanation_faithful=None,
                                    claims_compliant=None, llm_calls=0)

        payload = build_facts_payload(facts)
        calls = 0
        for name, chain in self._chains:
            calls += 1
            try:
                summary = parse_summary_response(chain.invoke({"facts_json": payload}))
            except Exception as exc:
                logger.error(f"Stock explanation failed via {name} ({type(exc).__name__}): {exc}")
                continue
            problems, claims = self.check(summary, payload)
            if not problems and not claims:
                return StockExplanation(with_disclaimer(summary), llm_used=True, explanation_faithful=True,
                                        claims_compliant=True, llm_calls=calls, provider=name)
            rejected = tuple(problems) + ((REJECTED_CLAIMS,) if claims else ())
            logger.warning(f"Guards rejected the LLM stock summary ({', '.join(rejected)}): {summary!r}")
            return StockExplanation(
                template, llm_used=True, explanation_faithful=not problems, claims_compliant=not claims,
                llm_calls=calls, raw_prohibited_claims=len(claims), provider=name, rejected_for=rejected,
            )
        logger.warning("Every LLM provider failed; using the template stock explanation.")
        return StockExplanation(template, llm_used=False, explanation_faithful=None, claims_compliant=None,
                                llm_calls=calls)
