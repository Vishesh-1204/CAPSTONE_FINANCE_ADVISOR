# tests/test_stocks_data_model.py
#
# Purpose: Phase 7 data model and schema checks (Prompt 7A-2).
#   * The schema Literals match the model constants and the pure sentiment module, so the API,
#     the database and the maths can never drift apart.
#   * The strict symbol validator: what it accepts, how it normalises, and what it rejects.
#   * Price columns are exact Numeric(14,4), never Float.
#   * Database rules (UNIQUE, CHECK) and the stock_analyses defaults.
#   * Every response carries the stocks disclaimer.
# No network, no LLM: the `db` fixture is in-memory SQLite.

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import BigInteger, Float, Numeric
from sqlalchemy.exc import IntegrityError

from app.core.disclaimers import STOCKS_DISCLAIMER
from app.core.stock_symbols import MAX_SYMBOL_LENGTH, normalize_symbol
from app.models.stock import (
    NEWS_SOURCES, PRICE_SOURCES, PROVIDER_CALL_STATUSES, SCORER_NAMES, NewsItemRow, PriceBar, ProviderCall,
    StockAnalysis, WatchlistItem,
)
from app.schemas import stock as schemas
from app.services.stocks.sentiment_aggregation import HeadlineSentiment, aggregate_sentiment

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)


# --- The schema, the model and the maths agree ---

def test_schema_literals_match_the_model_constants():
    assert get_args(schemas.PriceSource) == PRICE_SOURCES
    assert get_args(schemas.NewsSource) == NEWS_SOURCES
    assert get_args(schemas.ScorerName) == SCORER_NAMES
    assert get_args(schemas.ProviderCallStatus) == PROVIDER_CALL_STATUSES
    # The configurable market-data providers are a subset of the price sources (csv_upload is not a provider)
    assert set(get_args(schemas.MarketDataProviderName)) == set(PRICE_SOURCES) - {"csv_upload"}


def test_sentiment_labels_are_exactly_what_the_aggregator_produces():
    def label(p: float, n: float) -> str:
        return aggregate_sentiment([HeadlineSentiment(p, n, 1 - p - n, 0.0)] * 3).label

    produced = {label(0.9, 0.0), label(0.0, 0.9), label(0.1, 0.1), aggregate_sentiment([]).label}
    assert produced == set(get_args(schemas.SentimentLabel))


def test_every_response_carries_the_stocks_disclaimer():
    responses = [
        cls for name, cls in vars(schemas).items()
        if name.endswith("Response") and isinstance(cls, type) and issubclass(cls, BaseModel)
    ]
    # Watchlist, Refresh, Prices, PriceUpload, SentimentScore, StockAnalysis, AnalysisHistory, StockStatus
    assert len(responses) == 8
    for cls in responses:
        assert cls.model_fields["disclaimer"].default == STOCKS_DISCLAIMER, cls.__name__


def test_no_response_field_can_carry_advice():
    """The API shape itself cannot express a buy/sell call (spec 5.10): no such field names exist."""
    forbidden = ("signal", "rating", "recommend", "target", "action", "buy", "sell")
    for cls in vars(schemas).values():
        if isinstance(cls, type) and issubclass(cls, BaseModel):
            for field in cls.model_fields:
                assert not any(word in field for word in forbidden), f"{cls.__name__}.{field}"


# --- The strict symbol validator ---

@pytest.mark.parametrize("raw, expected", [
    ("RELIANCE.NS", "RELIANCE.NS"),
    ("reliance.ns", "RELIANCE.NS"),          # upper-cased, so the watchlist cannot hold both
    ("  ^nsei ", "^NSEI"),                   # trimmed; caret allowed at the start (index symbols)
    ("TCS.BSE", "TCS.BSE"),
    ("M-M.NS", "M-M.NS"),
    ("500325.BSE", "500325.BSE"),            # BSE numeric scrip code
    ("A" * MAX_SYMBOL_LENGTH, "A" * MAX_SYMBOL_LENGTH),
])
def test_valid_symbols_are_normalised(raw, expected):
    assert normalize_symbol(raw) == expected
    assert schemas.WatchlistAddRequest(symbol=raw).symbol == expected


@pytest.mark.parametrize("raw", [
    "", "   ",
    "A" * (MAX_SYMBOL_LENGTH + 1),           # too long
    "RELIANCE NS",                           # space inside
    "REL;DROP TABLE",                        # SQL-ish
    "RELIANCE.NS&apikey=x",                  # URL query injection
    "RELIANCE/NS", "../etc", ".NS", "-ABC",  # path-like / must start with a letter, digit or caret
    "^", "^^NSEI", "AB^C",                   # caret only once, only first, and not alone
    "REL\nIANCE",                            # control character inside
    "RELIANCE_NS", "TCS%2ENS",
    "relıance",                              # dotless i: would upper-case to a real ASCII symbol
    "ＲＥＬＩＡＮＣＥ",                     # full-width letters
])
def test_invalid_symbols_are_rejected(raw):
    with pytest.raises(ValueError):
        normalize_symbol(raw)
    with pytest.raises(ValidationError):
        schemas.WatchlistAddRequest(symbol=raw)


def test_non_text_symbol_is_rejected():
    with pytest.raises(ValueError):
        normalize_symbol(123)   # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        schemas.WatchlistAddRequest(symbol=123)


# --- Other request validation ---

def test_headline_to_score_is_trimmed_and_bounded():
    assert schemas.SentimentScoreRequest(headline="  Profit rises  ").headline == "Profit rises"
    for bad in ("", "    ", "x" * (schemas.MAX_HEADLINE_LENGTH + 1)):
        with pytest.raises(ValidationError):
            schemas.SentimentScoreRequest(headline=bad)


def test_unknown_fixed_values_are_rejected_in_responses():
    with pytest.raises(ValidationError):
        schemas.SentimentScoreResponse(positive=1, negative=0, neutral=0, label="bullish", scorer="lexicon", warnings=[])
    with pytest.raises(ValidationError):
        schemas.SentimentScoreResponse(positive=1, negative=0, neutral=0, label="positive", scorer="gpt", warnings=[])


# --- Column types: prices are exact ---

@pytest.mark.parametrize("column", ["open", "high", "low", "close"])
def test_price_columns_are_numeric_14_4_never_float(column):
    column_type = PriceBar.__table__.c[column].type
    # Float is a SUBCLASS of Numeric in SQLAlchemy, so check both ways
    assert isinstance(column_type, Numeric) and not isinstance(column_type, Float)
    assert (column_type.precision, column_type.scale) == (14, 4)


def test_volume_is_a_64_bit_integer():
    assert isinstance(PriceBar.__table__.c.volume.type, BigInteger)


def test_symbol_columns_fit_the_validator_limit():
    for model in (WatchlistItem, PriceBar, NewsItemRow, StockAnalysis):
        assert model.__table__.c.symbol.type.length == MAX_SYMBOL_LENGTH, model.__name__


# --- Database rules ---

def _bar(**overrides) -> PriceBar:
    values = {
        "symbol": "RELIANCE.NS", "bar_date": date(2026, 11, 6), "open": Decimal("100.1234"),
        "high": Decimal("101.5000"), "low": Decimal("99.7500"), "close": Decimal("100.5000"),
        "volume": 1_000_000, "source": "yfinance",
    }
    return PriceBar(**{**values, **overrides})


def test_price_bar_keeps_four_decimals(db):
    db.add(_bar())
    db.commit()
    assert db.query(PriceBar).one().open == Decimal("100.1234")


@pytest.mark.parametrize("overrides", [
    {"close": Decimal("0")}, {"open": Decimal("-1")}, {"low": Decimal("102"), "high": Decimal("101")},
    {"volume": -5},
])
def test_database_check_rules_reject_bad_bars(db, overrides):
    db.add(_bar(**overrides))
    with pytest.raises(IntegrityError):
        db.commit()


def test_missing_volume_is_allowed(db):
    db.add(_bar(volume=None))
    db.commit()
    assert db.query(PriceBar).one().volume is None


def test_one_bar_per_symbol_and_day(db):
    db.add(_bar())
    db.commit()
    db.add(_bar(bar_date=date(2026, 11, 5)))       # another day: fine
    db.add(_bar(symbol="TCS.NS"))                   # another symbol, same day: fine
    db.commit()
    db.add(_bar(close=Decimal("1.0000")))           # same symbol + day again: rejected
    with pytest.raises(IntegrityError):
        db.commit()


def test_watchlist_symbol_is_unique_per_user_not_globally(db):
    db.add_all([WatchlistItem(user_id=1, symbol="TCS.NS"), WatchlistItem(user_id=2, symbol="TCS.NS")])
    db.commit()
    assert db.query(WatchlistItem).count() == 2
    assert all(item.added_at is not None for item in db.query(WatchlistItem))
    db.add(WatchlistItem(user_id=1, symbol="TCS.NS"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_same_article_is_stored_once_per_symbol(db):
    def news(symbol: str) -> NewsItemRow:
        return NewsItemRow(symbol=symbol, headline="Synthetic headline", url="https://example.com/a",
                           published_at=NOW, source="google_rss")
    db.add_all([news("TCS.NS"), news("INFY.NS")])
    db.commit()
    row = db.query(NewsItemRow).filter_by(symbol="TCS.NS").one()
    assert row.sentiment_positive is None and row.scorer_name is None      # not scored yet
    db.add(news("TCS.NS"))
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("status", PROVIDER_CALL_STATUSES)
def test_provider_call_accepts_every_known_status(db, status):
    db.add(ProviderCall(provider="yfinance", endpoint="daily_prices", status=status))
    db.commit()
    assert db.query(ProviderCall).one().at is not None


def test_provider_call_rejects_an_unknown_status(db):
    db.add(ProviderCall(provider="yfinance", endpoint="daily_prices", status="maybe"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_analysis_defaults_and_derived_explanation_source(db):
    analysis = StockAnalysis(
        user_id=1, symbol="RELIANCE.NS", inputs_json={}, result_json={}, data_as_of=date(2026, 11, 6),
        sources_json={"prices": "yfinance"}, engine_version="7.0",
    )
    db.add(analysis)
    db.commit()
    assert (analysis.llm_used, analysis.llm_calls, analysis.raw_prohibited_claims) == (False, 0, 0)
    assert analysis.explanation is None and analysis.explanation_faithful is None
    assert analysis.claims_compliant is None and analysis.created_at is not None
    assert analysis.explanation_source == "template"

    analysis.llm_used, analysis.explanation_faithful, analysis.claims_compliant = True, True, True
    assert analysis.explanation_source == "llm"
    analysis.claims_compliant = False          # LLM text rejected by the claims guard -> template was shown
    assert analysis.explanation_source == "template"
