# tests/test_stocks_api.py
#
# HTTP-level tests for /api/stocks/... (Phase 7A-5). SQLite in memory, login bypassed for most
# tests (the current user can be switched), FakeProviders (no network: block_network), a fixed
# clock and a template-only explainer. The auth tests use the REAL get_current_user.
# Covers: auth, user isolation, symbol validation (body and path), the watchlist limit, the
# disclaimer on every response, the status endpoint, and each route's main behaviour.

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.stock_explainer import StockExplainer
from app.api.deps import get_current_user
from app.api.routes import stocks as stock_routes
from app.core.config import settings
from app.core.disclaimers import STOCKS_DISCLAIMER
from app.db.session import get_db
from app.market_data.base import ProviderError
from app.models.stock import ProviderCall
from app.models.user import User
from app.services import stock_service as svc
from app.services.investing.claims_guard import find_prohibited_stock_claims
from tests.market_fakes import BENCHMARK, NOW, SYMBOL, FakeProvider, headlines, synthetic_bars

pytestmark = pytest.mark.usefixtures("block_network")

S = "/api/stocks"
CSV = b"date,open,high,low,close,volume\n2026-11-02,100,102,99,101,1500\n2026-11-03,101,103,100,102,1600\n"

# Every endpoint: (method, path, JSON body, upload file)
ENDPOINTS = [
    ("GET", f"{S}/status", None, None),
    ("POST", f"{S}/sentiment/score", {"headline": "Profit rises"}, None),
    ("GET", f"{S}/watchlist", None, None),
    ("POST", f"{S}/watchlist", {"symbol": SYMBOL}, None),
    ("POST", f"{S}/{SYMBOL}/refresh", None, None),
    ("GET", f"{S}/{SYMBOL}/prices", None, None),
    ("POST", f"{S}/{SYMBOL}/prices/upload", None, CSV),
    ("POST", f"{S}/{SYMBOL}/analysis", None, None),
    ("GET", f"{S}/{SYMBOL}/analysis/latest", None, None),
    ("GET", f"{S}/{SYMBOL}/analyses", None, None),
    ("GET", f"{S}/{SYMBOL}/analyses/1", None, None),
    ("DELETE", f"{S}/watchlist/{SYMBOL}", None, None),
]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    state = {"prices": FakeProvider(bars=synthetic_bars() + synthetic_bars(BENCHMARK, seed=7), name="yfinance"),
             "news": FakeProvider(news=headlines(5), name="google_rss")}
    monkeypatch.setattr(svc, "_now", lambda: NOW)
    monkeypatch.setattr(svc, "_price_provider", lambda: state["prices"])
    monkeypatch.setattr(svc, "_news_provider", lambda: state["news"])
    monkeypatch.setattr(svc, "_build_explainer", lambda: StockExplainer(find_prohibited_stock_claims, chains=[]))
    monkeypatch.setattr(settings, "SENTIMENT_SCORER", "lexicon")
    return state


@pytest.fixture()
def current(db):
    """Which user the API thinks is logged in (tests can switch to user 2)."""
    return {"user_id": 1}


def _app(db) -> FastAPI:
    app = FastAPI()
    app.include_router(stock_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture()
def client(db, current):
    app = _app(db)
    app.dependency_overrides[get_current_user] = lambda: db.get(User, current["user_id"])
    return TestClient(app)


@pytest.fixture()
def anonymous(db):
    return TestClient(_app(db))


def call(client, method, path, body=None, status=200, file=None, **params):
    files = {"file": ("prices.csv", file, "text/csv")} if file is not None else None
    response = client.request(method, path, json=body, files=files, params=params or None)
    assert response.status_code == status, response.text
    return response.json()


def add(client, symbol=SYMBOL, status=201):
    return call(client, "POST", f"{S}/watchlist", {"symbol": symbol}, status=status)


# --- Auth ---

@pytest.mark.parametrize("method, path, body, file", ENDPOINTS)
def test_every_route_needs_a_login(anonymous, method, path, body, file):
    call(anonymous, method, path, body, status=401, file=file)


def test_a_bad_token_is_401(anonymous):
    assert anonymous.get(f"{S}/watchlist", headers={"Authorization": "Bearer nope"}).status_code == 401


# --- Disclaimer ---

def test_every_successful_response_carries_the_disclaimer(client):
    statuses = {f"{S}/watchlist": 201}
    for method, path, body, file in ENDPOINTS:
        if path.endswith("/analysis/latest"):
            call(client, "POST", f"{S}/{SYMBOL}/analysis")             # something to read back
        status = statuses.get(path, 200) if method == "POST" else 200
        assert call(client, method, path, body, status=status, file=file)["disclaimer"] == STOCKS_DISCLAIMER, path


# --- Symbol validation ---

@pytest.mark.parametrize("bad", ["", "REL;DROP", "A" * 21, "../etc", "RELIANCE NS", "relıance"])
def test_bad_symbols_in_the_body_are_422(client, bad):
    call(client, "POST", f"{S}/watchlist", {"symbol": bad}, status=422)


@pytest.mark.parametrize("bad", ["REL;DROP", "A" * 21, "R%26apikey%3Dx", "-ABC"])
def test_bad_symbols_in_the_path_are_422(client, bad):
    call(client, "POST", f"{S}/{bad}/analysis", status=422)


def test_symbols_are_normalised_and_index_carets_work_url_encoded(client):
    assert add(client, " synth.ns ")["items"][0]["symbol"] == SYMBOL
    add(client, "^nsei")
    assert call(client, "POST", f"{S}/%5ENSEI/refresh")["symbol"] == BENCHMARK


# --- Watchlist ---

def test_duplicate_symbol_is_409(client):
    add(client)
    add(client, "synth.ns", status=409)


def test_watchlist_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_WATCHLIST_LIMIT", 2)
    add(client, "AAA.NS")
    body = add(client, "BBB.NS")
    assert body["limit"] == 2 and [i["symbol"] for i in body["items"]] == ["AAA.NS", "BBB.NS"]
    assert "full" in call(client, "POST", f"{S}/watchlist", {"symbol": "CCC.NS"}, status=409)["detail"]
    call(client, "DELETE", f"{S}/watchlist/AAA.NS")
    add(client, "CCC.NS")                                              # room again after a removal


def test_removing_an_unknown_symbol_is_404(client):
    call(client, "DELETE", f"{S}/watchlist/{SYMBOL}", status=404)


def test_watchlist_shows_the_last_data_date(client):
    add(client)
    assert call(client, "GET", f"{S}/watchlist")["items"][0]["last_bar_date"] is None
    call(client, "POST", f"{S}/{SYMBOL}/refresh")
    assert call(client, "GET", f"{S}/watchlist")["items"][0]["last_bar_date"] == "2026-11-09"


# --- User isolation ---

def test_users_have_separate_watchlists_and_analyses(client, current):
    add(client)
    analysis = call(client, "POST", f"{S}/{SYMBOL}/analysis")

    current["user_id"] = 2
    assert call(client, "GET", f"{S}/watchlist")["items"] == []
    call(client, "POST", f"{S}/{SYMBOL}/analysis", status=404)          # not on user 2's watchlist
    call(client, "GET", f"{S}/{SYMBOL}/analysis/latest", status=404)
    call(client, "GET", f"{S}/{SYMBOL}/analyses/{analysis['id']}", status=404)
    assert call(client, "GET", f"{S}/{SYMBOL}/analyses")["analyses"] == []
    call(client, "DELETE", f"{S}/watchlist/{SYMBOL}", status=404)        # cannot remove user 1's entry

    current["user_id"] = 1
    assert call(client, "GET", f"{S}/{SYMBOL}/analyses/{analysis['id']}")["id"] == analysis["id"]


def test_symbol_routes_need_the_symbol_on_your_watchlist(client):
    for method, path in (("POST", "refresh"), ("GET", "prices"), ("POST", "analysis")):
        assert "watchlist" in call(client, method, f"{S}/{SYMBOL}/{path}", status=404)["detail"]


# --- Refresh, prices, upload ---

def test_refresh_reports_freshness_and_then_hits_the_cache(client, env):
    add(client)
    first = call(client, "POST", f"{S}/{SYMBOL}/refresh")
    assert first["cache_status"] == "refreshed" and first["price_source"] == "yfinance" and first["bars_saved"] > 0
    assert first["headlines_saved"] == 5 and first["data_as_of"] == "2026-11-09"
    second = call(client, "POST", f"{S}/{SYMBOL}/refresh")
    assert second["cache_status"] == "fresh" and second["bars_saved"] == 0
    assert len(env["prices"].price_calls) == 1                         # repeated clicks: one provider call


def test_refresh_with_nothing_cached_and_provider_down_is_503(client, env):
    env["prices"].error = ProviderError("unavailable", "offline")
    add(client)
    assert "upload a CSV" in call(client, "POST", f"{S}/{SYMBOL}/refresh", status=503)["detail"]


def test_prices_come_from_the_cache_only_with_both_averages(client, env):
    add(client)
    call(client, "GET", f"{S}/{SYMBOL}/prices", status=404)              # nothing cached yet
    call(client, "POST", f"{S}/{SYMBOL}/refresh")
    calls = len(env["prices"].price_calls)
    body = call(client, "GET", f"{S}/{SYMBOL}/prices", days=30)
    assert len(env["prices"].price_calls) == calls                      # GET never calls the provider
    assert body["bars"] and body["bars"][0]["bar_date"] >= "2026-10-10"
    assert body["bars"][-1]["sma_200"] is not None and body["sources"] == ["yfinance"]
    call(client, "GET", f"{S}/{SYMBOL}/prices", days=0, status=422)


def test_csv_upload_fills_an_empty_cache(client, env):
    env["prices"].error = ProviderError("rate_limited", "429")
    add(client)
    body = call(client, "POST", f"{S}/{SYMBOL}/prices/upload", file=CSV)
    assert body["bars_saved"] == 2 and body["rows_rejected"] == 0 and body["data_as_of"] == "2026-11-03"
    prices = call(client, "GET", f"{S}/{SYMBOL}/prices", days=30)
    assert prices["sources"] == ["csv_upload"] and len(prices["bars"]) == 2


# --- Analysis ---

def test_analysis_flow_latest_history_and_by_id(client):
    add(client)
    first = call(client, "POST", f"{S}/{SYMBOL}/analysis")
    assert first["explanation_source"] == "template" and first["sources"]["prices"] == "yfinance"
    assert first["indicators"]["trend"] in ("above_both_averages", "below_both_averages", "between_averages")
    second = call(client, "POST", f"{S}/{SYMBOL}/analysis")
    assert call(client, "GET", f"{S}/{SYMBOL}/analysis/latest")["id"] == second["id"]
    history = call(client, "GET", f"{S}/{SYMBOL}/analyses")["analyses"]
    assert [item["id"] for item in history] == [second["id"], first["id"]]
    call(client, "GET", f"{S}/OTHER.NS/analyses/{first['id']}", status=404)      # wrong symbol


# --- Headline scorer and status ---

def test_headline_scorer(client):
    body = call(client, "POST", f"{S}/sentiment/score", {"headline": "  Company reports record profit  "})
    assert body["scorer"] == "lexicon" and body["label"] == "positive" and body["warnings"] == []
    assert abs(body["positive"] + body["negative"] + body["neutral"] - 1) < 1e-9
    call(client, "POST", f"{S}/sentiment/score", {"headline": "   "}, status=422)


def test_status_shows_providers_calls_today_and_cache_ages(client, db):
    add(client)
    call(client, "POST", f"{S}/{SYMBOL}/refresh")
    db.add(ProviderCall(provider="google_rss", endpoint="news", status="rate_limited", at=NOW))
    db.commit()
    body = call(client, "GET", f"{S}/status")
    assert body["market_data_provider"] == settings.MARKET_DATA_PROVIDER
    assert body["sentiment_scorer"] == "lexicon" and isinstance(body["finbert_available"], bool)
    rss = next(p for p in body["providers"] if p["provider"] == "google_rss")
    assert rss["calls_today"] == 2 and rss["last_status"] == "rate_limited"       # the refresh + this one
    cache = {row["symbol"]: row for row in body["cache"]}
    assert set(cache) == {SYMBOL, BENCHMARK}                           # your watchlist + the benchmark
    assert cache[SYMBOL]["latest_bar_date"] == "2026-11-09" and cache[BENCHMARK]["latest_bar_date"] is None


def test_headline_scorer_can_be_chosen_per_call(client, monkeypatch):
    """Phase 7C: the page asks for each scorer; FinBERT that cannot load falls back with a warning."""
    from app.sentiment import factory
    from app.sentiment.base import ScorerUnavailable

    class Broken:
        name = "finbert"

        def score(self, texts):
            raise ScorerUnavailable("not installed")

    monkeypatch.setattr(factory, "_finbert", lambda: Broken())
    lexicon = call(client, "POST", f"{S}/sentiment/score", {"headline": "Profit rises", "scorer": "lexicon"})
    assert lexicon["scorer"] == "lexicon" and lexicon["warnings"] == []
    finbert = call(client, "POST", f"{S}/sentiment/score", {"headline": "Profit rises", "scorer": "finbert"})
    assert finbert["scorer"] == "lexicon" and finbert["warnings"] == ["finbert_unavailable"]
    call(client, "POST", f"{S}/sentiment/score", {"headline": "Profit rises", "scorer": "bert"}, status=422)
