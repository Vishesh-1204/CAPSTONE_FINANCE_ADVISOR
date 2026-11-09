# backend/evaluation/stocks_data.py
#
# Purpose: the ONLY place the Phase 7 evaluation reads the database. Loads cached daily bars (up to the
# pre-registered EVAL_END) and cached headlines for the evaluation universe, fingerprints what was used
# (SHA-256 of symbol/date/close rows, so a reader can tell whether two runs used the same data without
# any price data being committed), and makes the SYNTHETIC series used by the placebo and power controls.
#
# Real prices come from the local cache filled by scripts/seed_market_cache.py (yfinance). Nothing
# here calls a provider.

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_data.base import PriceBar
from app.models.stock import NewsItemRow
from app.models.stock import PriceBar as PriceBarRow
from evaluation.stocks_rules import EVAL_END


@dataclass(frozen=True)
class Series:
    """One symbol's daily closes, oldest first (dates and closes have the same length)."""
    symbol: str
    dates: list[date]
    closes: list[float]
    synthetic: bool = False


@dataclass(frozen=True)
class Headline:
    symbol: str
    text: str
    published_at: datetime            # UTC


class MissingPriceData(Exception):
    """A pre-registered symbol has no cached bars: run scripts.seed_market_cache first."""


def load_bars(db: Session, symbol: str, end: date = EVAL_END) -> list[PriceBar]:
    rows = db.scalars(select(PriceBarRow).where(PriceBarRow.symbol == symbol, PriceBarRow.bar_date <= end)
                      .order_by(PriceBarRow.bar_date)).all()
    return [PriceBar(r.symbol, r.bar_date, r.open, r.high, r.low, r.close, r.volume) for r in rows]


def to_series(symbol: str, bars: list[PriceBar]) -> Series:
    return Series(symbol, [b.bar_date for b in bars], [float(b.close) for b in bars])


def load_universe(db: Session, symbols: tuple[str, ...], end: date = EVAL_END) -> dict[str, list[PriceBar]]:
    out = {}
    for symbol in symbols:
        bars = load_bars(db, symbol, end)
        if not bars:
            raise MissingPriceData(f"no cached bars for {symbol}; run: python -m scripts.seed_market_cache "
                                   f"--symbols {symbol}")
        out[symbol] = bars
    return out


def fingerprint(bars_by_symbol: dict[str, list[PriceBar]]) -> str:
    """SHA-256 of 'symbol,date,close' lines (sorted): identical data -> identical hash."""
    digest = hashlib.sha256()
    for symbol in sorted(bars_by_symbol):
        for bar in bars_by_symbol[symbol]:
            digest.update(f"{symbol},{bar.bar_date.isoformat()},{bar.close}\n".encode())
    return digest.hexdigest()


def load_headlines(db: Session, symbols: tuple[str, ...]) -> dict[str, list[Headline]]:
    out: dict[str, list[Headline]] = {s: [] for s in symbols}
    rows = db.scalars(select(NewsItemRow).where(NewsItemRow.symbol.in_(symbols))
                      .order_by(NewsItemRow.published_at)).all()
    for row in rows:
        moment = row.published_at
        moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
        out[row.symbol].append(Headline(row.symbol, row.headline, moment))
    return out


# --- Synthetic series (controls; never mixed with real results) ---

def _weekdays(n: int, end: date = EVAL_END) -> list[date]:
    days: list[date] = []
    day = end
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


def random_walk(symbol: str, n: int, seed: int, drift: float = 0.0004, volatility: float = 0.015) -> Series:
    """Placebo: independent daily returns, so NO model can beat 50% except by luck."""
    rng = random.Random(seed)
    price, closes = 100.0, []
    for _ in range(n):
        price *= 1 + rng.gauss(drift, volatility)
        closes.append(round(price, 4))
    return Series(symbol, _weekdays(n), closes, synthetic=True)


def ar1_walk(symbol: str, n: int, seed: int, phi: float = 0.10, volatility: float = 0.015) -> Series:
    """Power control: returns r_t = phi * r_{t-1} + noise, a small REAL signal the harness should find."""
    rng = random.Random(seed)
    price, previous, closes = 100.0, 0.0, []
    for _ in range(n):
        r = phi * previous + rng.gauss(0.0, volatility * math.sqrt(1 - phi * phi))
        price *= 1 + r
        previous = r
        closes.append(round(price, 4))
    return Series(symbol, _weekdays(n), closes, synthetic=True)


def series_bars(series: Series) -> list[PriceBar]:
    """Synthetic Series -> PriceBars (high = low = close), for code that needs bars."""
    out = []
    for day, close in zip(series.dates, series.closes):
        price = Decimal(str(close))
        out.append(PriceBar(series.symbol, day, price, price, price, price, 0))
    return out
