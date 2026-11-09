# tests/test_stock_service.py
#
# Purpose: Phase 7A-5 service tests (in-memory SQLite, FakeProvider, template or stub LLM).
#   * compute_indicators never crashes on short history: each indicator appears only when there
#     is enough data for it, otherwise None.
#   * analyze() end to end: stored research fields (llm_used, explanation_faithful,
#     claims_compliant, llm_calls, raw_prohibited_claims, data_as_of, sources, warnings) and
#     every warning path (short history, stale data, no benchmark, no news, FinBERT missing).
#   * CSV fallback: parsing, rejected rows, and "adds missing days only".

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.agents.stock_explainer import StockExplainer
from app.core.config import settings
from app.core.disclaimers import STOCKS_DISCLAIMER
from app.market_data import cache_service
from app.market_data.base import ProviderError
from app.models.stock import NewsItemRow, PriceBar as PriceBarRow, StockAnalysis, WatchlistItem
from app.sentiment import factory as sentiment_factory
from app.sentiment.base import ScorerUnavailable
from app.services import stock_service as svc
from app.services.investing.claims_guard import find_prohibited_stock_claims
from tests.market_fakes import BENCHMARK, NOW, SYMBOL, TODAY, FakeProvider, headlines, synthetic_bars

pytestmark = pytest.mark.usefixtures("block_network")


class StubChain:
    def __init__(self, text: str):
        self.text, self.calls = text, 0

    def invoke(self, inputs):
        self.calls += 1
        return json.dumps({"summary": self.text})


@pytest.fixture()
def env(monkeypatch):
    """Fake providers, a fixed clock and a template-only explainer; tests may swap any of them."""
    state = {
        "prices": FakeProvider(bars=synthetic_bars() + synthetic_bars(BENCHMARK, seed=7)),
        "news": FakeProvider(news=headlines(5)),
        "explainer": StockExplainer(find_prohibited_stock_claims, chains=[]),
    }
    monkeypatch.setattr(svc, "_now", lambda: NOW)
    monkeypatch.setattr(svc, "_price_provider", lambda: state["prices"])
    monkeypatch.setattr(svc, "_news_provider", lambda: state["news"])
    monkeypatch.setattr(svc, "_build_explainer", lambda: state["explainer"])
    monkeypatch.setattr(settings, "SENTIMENT_SCORER", "lexicon")
    return state


def watch(db, symbol: str = SYMBOL, user_id: int = 1) -> None:
    db.add(WatchlistItem(user_id=user_id, symbol=symbol))
    db.commit()


# --- Indicators on short history ---

@pytest.mark.parametrize("n", [1, 2, 15, 21, 22, 49, 50, 199, 200, 251, 252, 253, 300])
def test_indicators_never_crash_and_appear_only_with_enough_history(n):
    bars = synthetic_bars(days=600)[-n:]
    ind = svc.compute_indicators(bars, synthetic_bars(BENCHMARK, days=600, seed=7))
    assert ind["bars_used"] == n and ind["last_close"] == float(bars[-1].close)
    assert (ind["sma_50"] is not None) == (n >= 50)
    assert (ind["sma_200"] is not None) == (n >= 200)
    assert (ind["trend"] == "not_enough_history") == (n < 200)
    assert (ind["rsi_14"] is not None) == (n > 14)
    assert (ind["annualized_volatility"] is not None) == (n >= 21)          # 20 returns
    assert (ind["var_95_one_day"] is not None) == (n >= 21)
    assert (ind["beta"] is not None) == (n >= 21)
    assert (ind["change_1m"] is not None) == (n >= 22)
    assert (ind["change_1y"] is not None) == (n >= 253)
    assert (ind["max_drawdown"] is not None) == (n >= 2)


def test_rsi_label_and_trend_follow_the_conventions():
    rising = synthetic_bars(drift=0.01, volatility=0.001)
    ind = svc.compute_indicators(rising, None)
    assert ind["trend"] == "above_both_averages" and ind["rsi_label"] == "high" and ind["beta"] is None
    falling = svc.compute_indicators(synthetic_bars(drift=-0.01, volatility=0.001), None)
    assert falling["trend"] == "below_both_averages" and falling["rsi_label"] == "low"


def test_a_flat_benchmark_gives_no_beta_instead_of_crashing():
    flat = synthetic_bars(BENCHMARK, drift=0.0, volatility=0.0)
    assert svc.compute_indicators(synthetic_bars(), flat)["beta"] is None


# --- analyze(): the full flow ---

def test_analysis_is_stored_with_every_research_field(db, env):
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    row = db.get(StockAnalysis, response["id"])
    assert (row.llm_used, row.llm_calls, row.raw_prohibited_claims) == (False, 0, 0)
    assert row.explanation_faithful is None and row.claims_compliant is None
    assert row.explanation.endswith(STOCKS_DISCLAIMER)               # added by code, after the guards
    assert row.data_as_of == TODAY and response["data_as_of"] == TODAY
    assert row.sources_json == {"prices": "fake", "prices_cache": "refreshed", "benchmark": BENCHMARK,
                                "news": "fake", "news_cache": "refreshed", "sentiment_scorer": "lexicon"}
    assert row.result_json["warnings"] == [] and row.engine_version == svc.ENGINE_VERSION
    assert row.inputs_json["risk_window"] == 252 and row.inputs_json["sma_windows"] == [50, 200]
    ind = response["indicators"]
    assert ind["sma_200"] is not None and ind["beta"] is not None and ind["benchmark"] == BENCHMARK
    sentiment = response["sentiment"]
    assert sentiment["n_items"] == 5 and sentiment["reliable"] and len(sentiment["headlines"]) == 5
    # The scores are written back to the shared news_items rows
    assert {r.scorer_name for r in db.query(NewsItemRow)} == {"lexicon"}


def test_fewer_than_252_bars_gives_fewer_indicators_and_a_warning(db, env):
    env["prices"].bars = synthetic_bars(days=60) + synthetic_bars(BENCHMARK, days=60, seed=7)
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert "insufficient_history" in response["warnings"]
    assert response["indicators"]["sma_200"] is None and response["indicators"]["trend"] == "not_enough_history"
    assert response["indicators"]["sma_50"] is None or response["indicators"]["bars_used"] >= 50
    assert response["explanation"]                                   # the template still explains it


def test_a_single_bar_still_gives_an_analysis(db, env):
    env["prices"].bars = synthetic_bars(days=0)
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert response["indicators"]["bars_used"] == 1 and response["indicators"]["rsi_14"] is None
    assert "insufficient_history" in response["warnings"]


def test_stale_prices_are_used_with_the_warning(db, env):
    watch(db)
    cache_service.upsert_bars(db, synthetic_bars(end=TODAY - timedelta(days=3)), "fake", NOW - timedelta(days=3))
    cache_service.upsert_bars(db, synthetic_bars(BENCHMARK, seed=7), "fake", NOW - timedelta(hours=1))
    db.commit()
    env["prices"].error = ProviderError("rate_limited", "429")
    response = svc.analyze(db, 1, SYMBOL)
    assert "data_stale" in response["warnings"] and response["sources"]["prices_cache"] == "stale"
    assert response["data_as_of"] == date(2026, 11, 6)


def test_missing_benchmark_gives_a_warning_and_no_beta(db, env):
    env["prices"].bars = synthetic_bars()                            # no ^NSEI bars at all
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert "benchmark_unavailable" in response["warnings"] and response["indicators"]["beta"] is None


def test_the_benchmark_itself_has_no_beta_and_no_warning(db, env):
    watch(db, BENCHMARK)
    response = svc.analyze(db, 1, BENCHMARK)
    assert response["indicators"]["benchmark"] is None and response["indicators"]["beta"] is None
    assert "benchmark_unavailable" not in response["warnings"]


def test_no_news_gives_no_sentiment_and_a_warning(db, env):
    env["news"].error = ProviderError("unavailable", "offline")
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert response["sentiment"] is None and "news_unavailable" in response["warnings"]
    assert response["sources"]["news"] == "none"


def test_few_headlines_are_flagged_unreliable(db, env):
    env["news"].news = headlines(2)
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert response["sentiment"]["reliable"] is False and "few_headlines" in response["warnings"]


def test_finbert_unavailable_falls_back_to_the_lexicon_with_a_warning(db, env, monkeypatch):
    class Unavailable:
        name = "finbert"

        def score(self, texts):
            raise ScorerUnavailable("not installed")

    monkeypatch.setattr(settings, "SENTIMENT_SCORER", "finbert")
    monkeypatch.setattr(sentiment_factory, "_finbert", lambda: Unavailable())
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert "finbert_unavailable" in response["warnings"] and response["sources"]["sentiment_scorer"] == "lexicon"
    assert response["sentiment"]["scorer"] == "lexicon"


def test_llm_text_with_advice_is_rejected_and_recorded(db, env):
    chain = StubChain(f"{SYMBOL} closed higher. You should buy now.")
    env["explainer"] = StockExplainer(find_prohibited_stock_claims, chains=[("stub", chain)])
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    row = db.get(StockAnalysis, response["id"])
    assert chain.calls == 1 and row.llm_calls == 1 and row.llm_used
    assert row.claims_compliant is False and row.raw_prohibited_claims >= 1
    assert response["explanation_source"] == "template" and "buy now" not in response["explanation"]
    assert row.result_json["explanation_rejected_for"] == ["claims"]


def test_faithful_llm_text_is_used(db, env):
    chain = StubChain(f"{SYMBOL} is described here without any numbers.")
    env["explainer"] = StockExplainer(find_prohibited_stock_claims, chains=[("stub", chain)])
    watch(db)
    response = svc.analyze(db, 1, SYMBOL)
    assert response["explanation_source"] == "llm" and response["explanation_faithful"] is True


def test_analysis_needs_the_symbol_on_the_watchlist(db, env):
    with pytest.raises(HTTPException) as info:
        svc.analyze(db, 1, SYMBOL)
    assert info.value.status_code == 404 and "watchlist" in info.value.detail


def test_nothing_cached_and_provider_down_is_a_clear_503(db, env):
    env["prices"].error = ProviderError("unavailable", "offline")
    watch(db)
    with pytest.raises(HTTPException) as info:
        svc.analyze(db, 1, SYMBOL)
    assert info.value.status_code == 503 and "upload a CSV" in info.value.detail


# --- CSV fallback ---

CSV = (b"Date,Open,High,Low,Close,Adj Close,Volume\n"
       b"2026-11-02,100,102,99,101,101,1500\n"
       b"03-11-2026,101,103,100,102,102,1,600\n"           # extra comma in volume -> the row still parses
       b"2026-11-04,0,103,100,102,102,1600\n"              # zero price -> rejected
       b"2026-11-05,101,99,100,102,102,1600\n"             # low above high -> rejected
       b"2026-12-01,101,103,100,102,102,1600\n"            # future date -> rejected
       b"not a date,101,103,100,102,102,1600\n"            # bad date -> rejected
       b"2026-11-06,\"1,01.5\",103,100,102.12345,102,\n")  # grouping commas, 5 decimals, no volume


def test_csv_parser_keeps_valid_rows_and_counts_rejected_ones():
    bars, rejected = svc.parse_price_csv(SYMBOL, CSV, TODAY)
    assert [b.bar_date for b in bars] == [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 6)]
    assert rejected == 4
    assert bars[-1].close == Decimal("102.1235") and bars[-1].volume is None and bars[0].volume == 1500


@pytest.mark.parametrize("content", [b"day,price\n2026-11-02,100\n", b"\xff\xfe\x00bad"])
def test_csv_without_the_columns_or_not_utf8_is_a_422(content):
    with pytest.raises(HTTPException) as info:
        svc.parse_price_csv(SYMBOL, content, TODAY)
    assert info.value.status_code == 422


def test_upload_adds_missing_days_only(db, env):
    watch(db)
    cache_service.upsert_bars(db, synthetic_bars(end=date(2026, 11, 3), days=2), "fake", NOW)
    db.commit()
    response = svc.upload_prices(db, 1, SYMBOL, CSV)
    assert response["bars_saved"] == 1 and response["rows_rejected"] == 4          # only 2026-11-06 was new
    assert set(response["warnings"]) == {"rows_rejected", "existing_days_kept"}
    assert db.query(PriceBarRow).filter_by(bar_date=date(2026, 11, 2)).one().source == "fake"
