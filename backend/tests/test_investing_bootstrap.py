# tests/test_investing_bootstrap.py
#
# Phase 7D: POST /api/investing/simulate with method="bootstrap".
#   * pure helpers: daily closes -> complete-month returns, and the moment matching
#   * service: bootstrap with enough cached benchmark history, the "history_too_short" fallback
#     (identical to the normal method), the 60-month boundary, no provider call, no user data
#   * API: the new request field and response fields
# The benchmark bars are the SYNTHETIC seeded random walk from tests/market_fakes.py: no real
# market data, no network.

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import investing as investing_routes
from app.core.config import settings
from app.db.session import get_db
from app.market_data import cache_service
from app.models.investment import InvestmentPlan
from app.models.stock import ProviderCall
from app.models.user import User
from app.schemas.investment import SimulateRequest
from app.services import investment_service as svc
from app.services.stocks.bootstrap_returns import (
    annualised, match_moments, mean_and_sd, monthly_history,
)
from tests.market_fakes import BENCHMARK, NOW, SYMBOL, TODAY, synthetic_bars

YEARS_OF_BARS = 7          # about 83 complete months before TODAY (2026-11-09): enough for 60


@pytest.fixture(autouse=True)
def few_paths(monkeypatch):
    monkeypatch.setattr(settings, "INVEST_MC_PATHS", 200)
    monkeypatch.setattr(settings, "STOCK_BENCHMARK_SYMBOL", BENCHMARK)


def seed(db, symbol: str = BENCHMARK, years: float = YEARS_OF_BARS, end: date = TODAY) -> None:
    cache_service.upsert_bars(db, synthetic_bars(symbol, end=end, days=int(years * 365)), "yfinance", NOW)
    db.commit()


def request(method: str = "bootstrap", category: str = "moderate", **extra) -> SimulateRequest:
    return SimulateRequest(monthly_sip=Decimal("10000"), years=10, category=category,
                           goal_amount=Decimal("2000000"), method=method, **extra)


# --- Pure helpers ---

def test_monthly_history_uses_the_last_close_of_each_month_and_drops_the_current_month():
    dates = [date(2026, 8, 3), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30),
             date(2026, 10, 30), date(2026, 11, 6)]
    closes = [90.0, 100.0, 999.0, 110.0, 99.0, 500.0]
    history = monthly_history(dates, closes, today=date(2026, 11, 9))
    assert history.close_dates == [date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 30)]
    assert history.returns == pytest.approx([0.10, -0.10])        # November (unfinished) is not used
    assert (history.start, history.end) == (date(2026, 8, 31), date(2026, 10, 30))


def test_monthly_history_keeps_only_the_newest_unbroken_run_of_months():
    # a missing March: Jan -> Feb, then (gap), Apr -> May -> Jun
    dates = [date(2026, 1, 30), date(2026, 2, 27), date(2026, 4, 30), date(2026, 5, 29), date(2026, 6, 30)]
    history = monthly_history(dates, [100.0, 50.0, 100.0, 110.0, 121.0], today=date(2026, 7, 10))
    assert history.start == date(2026, 4, 30)
    assert history.returns == pytest.approx([0.10, 0.10])          # no fake Feb -> Apr "monthly" return


def test_monthly_history_with_nothing_complete_is_empty():
    history = monthly_history([date(2026, 11, 2), date(2026, 11, 3)], [1.0, 2.0], today=date(2026, 11, 9))
    assert history.returns == [] and history.start is None and history.end is None
    one_month = monthly_history([date(2026, 10, 30)], [1.0], today=date(2026, 11, 9))
    assert one_month.returns == [] and one_month.start is None      # one close is not a return


def test_match_moments_hits_the_targets_and_keeps_the_order():
    raw = [0.05, -0.12, 0.02, 0.03, -0.01, 0.08, 0.00, -0.04]
    scaled = match_moments(raw, annual_return=0.09, annual_volatility=0.12)
    mean, sd = mean_and_sd(scaled)
    assert mean == pytest.approx((1.09) ** (1 / 12) - 1)            # simulate_sip's monthly mean
    assert sd == pytest.approx(0.12 / math.sqrt(12))
    assert sorted(range(8), key=raw.__getitem__) == sorted(range(8), key=scaled.__getitem__)
    assert annualised(mean, sd) == pytest.approx((0.09, 0.12))


def test_match_moments_of_a_flat_history_is_the_target_mean():
    assert match_moments([0.01] * 5, 0.06, 0.1) == pytest.approx([(1.06) ** (1 / 12) - 1] * 5)


# --- Service ---

def test_bootstrap_uses_the_cached_benchmark_history(db):
    seed(db)
    body = svc.simulate(db, request(), now=NOW)
    history = body["history"]
    assert (body["requested_method"], body["method"], body["warnings"]) == ("bootstrap", "bootstrap", [])
    assert history["symbol"] == BENCHMARK and history["monthly_returns"] >= settings.INVEST_BOOTSTRAP_MIN_MONTHS
    assert history["end"] == date(2026, 10, 30)                       # last weekday of October; November dropped
    assert history["start"] < history["end"] - timedelta(days=60 * 30)
    assert history["block_months"] == settings.INVEST_BOOTSTRAP_BLOCK_MONTHS
    assert history["historical_annual_volatility"] > 0
    projection = body["projection"]
    assert projection["p10"] <= projection["p50"] <= projection["p90"]
    assert projection["n_paths"] == settings.INVEST_MC_PATHS
    assert body == svc.simulate(db, request(), now=NOW)                # seeded: same answer twice


def test_bootstrap_differs_from_normal_but_keeps_the_assumptions(db):
    seed(db)
    boot, normal = svc.simulate(db, request(), now=NOW), svc.simulate(db, request("normal"), now=NOW)
    assert boot["projection"] != normal["projection"]
    # rescaled to the same assumed mean/volatility, so the typical outcome stays in the same region
    assert boot["projection"]["p50"] == pytest.approx(normal["projection"]["p50"], rel=0.1)
    for key in ("expected_return", "volatility", "target_allocation"):
        assert boot[key] == normal[key]
    assert boot["projection"]["months_to_goal"] == normal["projection"]["months_to_goal"]   # deterministic


def test_bootstrap_range_follows_the_risk_category(db):
    """The equity index is rescaled per portfolio: a conservative mix must not get an all-equity range."""
    seed(db)
    spread = {}
    for category in ("conservative", "aggressive"):
        p = svc.simulate(db, request(category=category), now=NOW)["projection"]
        spread[category] = p["p90"] - p["p10"]
    assert spread["conservative"] < spread["aggressive"]


def test_too_short_history_falls_back_to_exactly_the_normal_method(db):
    seed(db, years=3)                                                  # about 35 complete months
    body = svc.simulate(db, request(), now=NOW)
    assert (body["requested_method"], body["method"]) == ("bootstrap", "normal")
    assert body["warnings"] == [svc.WARNING_HISTORY_TOO_SHORT]
    assert 0 < body["history"]["monthly_returns"] < 60 and body["history"]["start"] is not None
    assert body["projection"] == svc.simulate(db, request("normal"), now=NOW)["projection"]


def test_nothing_cached_falls_back_with_an_empty_history(db):
    seed(db, symbol=SYMBOL)                    # a long history, but of a watchlist stock, not the benchmark
    body = svc.simulate(db, request(), now=NOW)
    assert body["method"] == "normal" and body["warnings"] == [svc.WARNING_HISTORY_TOO_SHORT]
    history = body["history"]
    assert (history["monthly_returns"], history["start"], history["end"]) == (0, None, None)
    assert history["historical_annual_return"] is None


def test_the_minimum_is_inclusive(db, monkeypatch):
    seed(db, years=3)
    found = svc.simulate(db, request(), now=NOW)["history"]["monthly_returns"]
    monkeypatch.setattr(settings, "INVEST_BOOTSTRAP_MIN_MONTHS", found)
    assert svc.simulate(db, request(), now=NOW)["method"] == "bootstrap"
    monkeypatch.setattr(settings, "INVEST_BOOTSTRAP_MIN_MONTHS", found + 1)
    assert svc.simulate(db, request(), now=NOW)["method"] == "normal"


def test_block_length_comes_from_settings(db, monkeypatch):
    seed(db)
    six = svc.simulate(db, request(), now=NOW)
    monkeypatch.setattr(settings, "INVEST_BOOTSTRAP_BLOCK_MONTHS", 12)
    twelve = svc.simulate(db, request(), now=NOW)
    assert twelve["history"]["block_months"] == 12 and twelve["projection"] != six["projection"]


def test_normal_is_the_default_and_reads_no_history(db):
    seed(db)
    body = svc.simulate(db, SimulateRequest(monthly_sip=Decimal("5000"), years=5, category="moderate"), now=NOW)
    assert (body["requested_method"], body["method"], body["history"], body["warnings"]) == (
        "normal", "normal", None, [])


def test_bootstrap_never_calls_a_provider_and_stores_nothing(db):
    seed(db)
    svc.simulate(db, request(), now=NOW)
    assert db.query(ProviderCall).count() == 0                         # cache only
    assert db.query(InvestmentPlan).count() == 0


def test_bootstrap_with_step_up_runs_and_reports_no_months_to_goal(db):
    seed(db)
    flat = svc.simulate(db, request(), now=NOW)["projection"]
    stepped = svc.simulate(db, request(annual_step_up=0.1), now=NOW)["projection"]
    assert stepped["months_to_goal"] is None and stepped["p50"] > flat["p50"]


# --- API ---

@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(investing_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    return TestClient(app)


SIMULATE = {"monthly_sip": 10000, "years": 10, "category": "moderate"}


def test_api_bootstrap_shows_the_history_used(client, db):
    seed(db, end=date.today())             # the route uses the real clock
    response = client.post("/api/investing/simulate", json={**SIMULATE, "method": "bootstrap"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["method"] == "bootstrap" and body["history"]["symbol"] == BENCHMARK
    assert body["history"]["monthly_returns"] >= 60 and body["history"]["start"] < body["history"]["end"]
    assert body["disclaimer"]


def test_api_fallback_and_default(client, db):
    fallback = client.post("/api/investing/simulate", json={**SIMULATE, "method": "bootstrap"}).json()
    assert fallback["method"] == "normal" and fallback["warnings"] == ["history_too_short"]
    default = client.post("/api/investing/simulate", json=SIMULATE).json()
    assert default["method"] == "normal" and default["history"] is None
    assert default["projection"] == fallback["projection"]


def test_api_unknown_method_is_422(client):
    assert client.post("/api/investing/simulate", json={**SIMULATE, "method": "magic"}).status_code == 422
