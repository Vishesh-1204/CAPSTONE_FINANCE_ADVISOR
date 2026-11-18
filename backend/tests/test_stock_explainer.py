# tests/test_stock_explainer.py
#
# Purpose: Phase 7A-5 explainer tests with a stub LLM (no API key, no network).
#   * Facts are PRE-FORMATTED strings (₹2,950.10, -4.2%, 0.92), and contain no headline text.
#   * The template only uses facts strings, so it passes every guard (checked on several
#     scenarios, including short history where values are missing).
#   * LLM text is accepted only if every number is in the facts and there is no advice,
#     prediction or lakh/crore wording; otherwise the template is used and the reason recorded.
#   * Exactly one call with a single stub; provider failure -> template; disclaimer added by code.

import json
from decimal import Decimal

import pytest

from app.agents.explanation_guard import extract_numbers, find_unsupported_numbers
from app.agents.stock_explainer import (
    REJECTED_CLAIMS, REJECTED_NUMBER, REJECTED_SHORTHAND, StockExplainer, build_facts, build_facts_payload,
    money_text, pct_text, signed_decimal_text, template_text,
)
from app.core.disclaimers import STOCKS_DISCLAIMER
from app.services.investing.claims_guard import find_prohibited_stock_claims

pytestmark = pytest.mark.usefixtures("block_network")

FULL = {
    "symbol": "SYNTH.NS", "is_index": False, "benchmark": "^NSEI", "data_as_of": "2026-11-06", "window": 252,
    "indicators": {
        "bars_used": 275, "last_close": 2950.1, "sma_50": 2900.0, "sma_200": 2701.456, "trend": "above_both_averages",
        "rsi_14": 68.44, "rsi_label": "normal", "annualized_volatility": 0.2412, "max_drawdown": -0.1823,
        "var_95_one_day": 0.0231, "change_1m": -0.0421, "change_1y": 0.1234, "beta": 0.9234, "correlation": 0.6512,
    },
    "sentiment": {"score": 0.3214, "label": "positive", "n_items": 12, "reliable": True, "effective_weight": 4.1,
                  "scorer": "lexicon"},
    "warnings": [],
}
SHORT = {
    **FULL, "warnings": ["insufficient_history", "benchmark_unavailable", "news_unavailable"], "sentiment": None,
    "indicators": {**{k: None for k in FULL["indicators"]}, "bars_used": 30, "last_close": 101.5,
                   "trend": "not_enough_history", "rsi_14": 55.0, "rsi_label": "normal",
                   "annualized_volatility": 0.3, "max_drawdown": -0.05, "var_95_one_day": 0.02,
                   "change_1m": 0.01},
}
INDEX = {**FULL, "symbol": "^NSEI", "is_index": True, "benchmark": None,
         "indicators": {**FULL["indicators"], "last_close": 24350.5, "beta": None, "correlation": None}}
ONE_BAR = {**SHORT, "indicators": {**SHORT["indicators"], "rsi_14": None, "rsi_label": None,
                                   "annualized_volatility": None, "max_drawdown": None, "var_95_one_day": None,
                                   "change_1m": None, "bars_used": 1}}


class StubChain:
    def __init__(self, reply: str | Exception):
        self.reply, self.calls = reply, 0

    def invoke(self, inputs):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def explainer(*chains) -> StockExplainer:
    return StockExplainer(find_prohibited_stock_claims, chains=[(f"stub{i}", c) for i, c in enumerate(chains)])


def reply(text: str) -> str:
    return json.dumps({"summary": text})


# --- Formatting ---

def test_number_formats():
    assert money_text(2950.1, is_index=False) == "₹2,950.10"
    assert money_text(1234567.891, is_index=False) == "₹12,34,567.89"         # Indian grouping
    assert money_text(24350.5, is_index=True) == "24,350.50 points"
    assert pct_text(0.2412) == "24.1%" and pct_text(-0.0421, signed=True) == "-4.2%"
    assert pct_text(0.1234, signed=True) == "+12.3%" and pct_text(-0.0001, signed=True) == "0.0%"
    assert signed_decimal_text(0.3214) == "+0.32" and signed_decimal_text(-0.5) == "-0.50"


def test_facts_are_preformatted_strings_and_hold_no_headlines():
    facts = build_facts(FULL)
    assert facts["last_close"] == "₹2,950.10" and facts["average_200_day"] == "₹2,701.46"
    assert facts["change_1_month"] == "-4.2%" and facts["largest_fall_from_a_peak"] == "18.2%"
    assert facts["beta"] == "0.92" and facts["rsi_14_day"] == "68.4"
    assert facts["sentiment"] == {"label": "positive", "score_from_minus_1_to_plus_1": "+0.32", "headlines": "12",
                                  "reliable": "yes", "scorer": "lexicon"}
    payload = build_facts_payload(facts)
    assert "headline\":" not in payload and "http" not in payload       # no headline text or links
    for value in facts.values():
        assert value is None or isinstance(value, (str, list, dict)), value      # never a raw float


def test_missing_history_gives_null_facts():
    facts = build_facts(SHORT)
    assert facts["average_200_day"] is None and facts["trend"] is None and facts["beta"] is None
    assert facts["sentiment"] is None and facts["change_1_year"] is None


# --- The template always passes its own guards ---

@pytest.mark.parametrize("result", [FULL, SHORT, INDEX, ONE_BAR], ids=["full", "short", "index", "one_bar"])
def test_template_uses_only_facts_and_passes_every_guard(result):
    facts = build_facts(result)
    text = template_text(facts)
    problems, claims = explainer().check(text, build_facts_payload(facts))
    assert problems == [] and claims == [], (problems, claims, text)
    assert "None" not in text


def test_template_mentions_warnings_in_plain_words():
    text = template_text(build_facts(SHORT))
    assert "less than a year of price history" in text and "No headlines" in text


# --- The any-number guard ---

def test_number_guard_reads_every_kind_of_number():
    assert extract_numbers("₹12,34,567.89 fell 4.2% on 2026-11-06, RSI 68.4, 12 headlines") == [
        Decimal("1234567.89"), Decimal("4.2"), Decimal("2026"), Decimal("11"), Decimal("6"), Decimal("68.4"),
        Decimal("12")]
    facts = '{"a": "-4.2%", "b": "₹2,950.10"}'
    assert find_unsupported_numbers("It fell 4.2% to ₹2,950.1.", facts) == []          # same value, sign dropped
    assert find_unsupported_numbers("It fell 4.3%.", facts) == [Decimal("4.3")]


# --- LLM text: accepted or rejected ---

GOOD = ("SYNTH.NS closed at ₹2,950.10 on 2026-11-06, above both its 50-day and 200-day averages. "
        "It changed by -4.2% over the last month. Volatility was 24.1% a year and beta was 0.92. "
        "Headline sentiment from 12 headlines was positive.")


def test_faithful_neutral_text_is_accepted_with_the_disclaimer_added():
    chain = StubChain(reply(GOOD))
    result = explainer(chain).explain(FULL)
    assert result.llm_used and result.explanation_faithful and result.claims_compliant
    assert result.text == f"{GOOD}\n\n{STOCKS_DISCLAIMER}"
    assert chain.calls == 1 and result.llm_calls == 1 and result.rejected_for == ()


@pytest.mark.parametrize("text, reason", [
    (GOOD.replace("0.92", "0.95"), REJECTED_NUMBER),                       # invented beta
    (GOOD.replace("-4.2%", "-5%"), REJECTED_NUMBER),                       # invented percentage
    (GOOD + " That is a 3-year high.", REJECTED_NUMBER),                   # any number, not just ₹ and %
    (GOOD + " The market value is about 2 lakh crore.", REJECTED_SHORTHAND),
])
def test_unfaithful_text_falls_back_to_the_template(text, reason):
    result = explainer(StubChain(reply(text))).explain(FULL)
    assert result.llm_used and result.explanation_faithful is False and result.claims_compliant is True
    assert reason in result.rejected_for
    assert result.text == f"{template_text(build_facts(FULL))}\n\n{STOCKS_DISCLAIMER}"


@pytest.mark.parametrize("extra", [" You should buy before the results.", " The price will rise next week.",
                                   " The stock looks undervalued.", " Returns are guaranteed."])
def test_advice_or_prediction_falls_back_and_is_counted(extra):
    result = explainer(StubChain(reply(GOOD + extra))).explain(FULL)
    assert result.claims_compliant is False and result.explanation_faithful is True
    assert result.rejected_for == (REJECTED_CLAIMS,) and result.raw_prohibited_claims >= 1
    assert result.text.startswith(template_text(build_facts(FULL)))


def test_provider_failure_and_bad_json_use_the_template():
    failing = StubChain(RuntimeError("quota"))
    result = explainer(failing).explain(FULL)
    assert not result.llm_used and result.explanation_faithful is None and result.llm_calls == 1
    assert explainer(StubChain("not json at all")).explain(FULL).llm_used is False


def test_the_fallback_provider_is_tried_only_after_a_failure():
    first, second = StubChain(RuntimeError("down")), StubChain(reply(GOOD))
    result = explainer(first, second).explain(FULL)
    assert result.llm_used and result.llm_calls == 2 and result.provider == "stub1"
    assert (first.calls, second.calls) == (1, 1)


def test_no_llm_means_template_and_zero_calls():
    result = explainer().explain(FULL)
    assert (result.llm_used, result.llm_calls, result.explanation_faithful) == (False, 0, None)
    assert result.text.endswith(STOCKS_DISCLAIMER)
