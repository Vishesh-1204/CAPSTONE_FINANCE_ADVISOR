# backend/evaluation/stocks_robustness.py
#
# Purpose: RQ5 helpers. How robust is the system?
#   1. Data fallback: thousands of simulated price requests go through the REAL cache_service.get_prices
#      (SQLite in memory, a simulated clock) while a scripted provider fails in pre-registered ways
#      (stocks_rules.py RQ5_SCENARIOS). Measures how many requests are still served, by which step of the
#      fallback chain, how old the data is when served, and how many provider calls it costs.
#   2. Guards: G1 the template on real facts, G2 claims guard v3 on the held-out sentence file,
#      G3 (--llm real only) raw LLM output rates before the guards.
#
# The scripted provider returns SYNTHETIC bars (a seeded random walk), so this part needs no real data.

import csv
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.market_data import cache_service
from app.market_data.base import IST, PriceBar, ProviderError
from app.services.stock_service import ANALYSIS_DAYS, RISK_WINDOW, compute_indicators
from evaluation.stocks_data import random_walk, series_bars
from evaluation.stocks_rules import EVAL_END, GUARD_CASES_FILE, SEED

EVAL_DIR = Path(__file__).parent
REQUEST_DAYS = 90                  # calendar days asked for per request
HISTORY_DAYS = 150                 # the provider's full window (small, so the simulation is fast)
FIRST_HOUR, LAST_HOUR = 9, 23      # requests between 09:00 and 23:00 IST


# --- Failure scripts: (day index, rng) -> ProviderError kind or None ---

FailScript = Callable[[int, random.Random, int], str | None]     # (day, rng, total days) -> kind | None

SCRIPTS: dict[str, FailScript] = {
    "S0_healthy": lambda day, rng, days: None,
    "S1_rate_limited": lambda day, rng, days: "rate_limited" if rng.random() < 0.3 else None,
    "S2_outage_5d": lambda day, rng, days: "unavailable" if days // 2 - 2 <= day < days // 2 + 3 else None,
    "S3_bad_response": lambda day, rng, days: "bad_response" if rng.random() < 0.2 else None,
    "S4_down_after_day1": lambda day, rng, days: "unavailable" if day >= 1 else None,
    "S5_cold_outage": lambda day, rng, days: "unavailable",
}


def latest_final_date(now: datetime) -> date:
    return cache_service.latest_close_cutoff(now).astimezone(IST).date()


def weekdays_between(older: date, newer: date) -> int:
    """Trading days (weekdays) in (older, newer]: 0 when the data has the latest close."""
    count, day = 0, older
    while day < newer:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


class ScriptedProvider:
    """A provider that knows the simulated clock and fails according to a script."""

    name = "scripted"

    def __init__(self, bars: dict[str, list[PriceBar]], script: FailScript, days: list[date], seed: int) -> None:
        self._bars, self._script, self._days = bars, script, days
        self._rng = random.Random(seed)
        self.now: datetime | None = None
        self.calls = self.failed = 0

    def get_daily_prices(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        self.calls += 1
        today = self.now.astimezone(IST).date()
        day_index = self._days.index(today) if today in self._days else len(self._days)
        kind = self._script(day_index, self._rng, len(self._days))
        if kind:
            self.failed += 1
            raise ProviderError(kind, "scripted failure", self.name)
        last = min(end, latest_final_date(self.now))
        return [b for b in self._bars[symbol] if start <= b.bar_date <= last]

    def get_news(self, symbol: str, limit: int) -> list:
        return []


@dataclass
class SimulationStats:
    scenario: str
    ttl_hours: float
    cooldown_minutes: float
    cooldown_max_minutes: float
    requests: int = 0
    served: int = 0
    errors: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    staleness: list[int] = field(default_factory=list)       # trading days behind, per served request
    provider_calls: int = 0
    failed_calls: int = 0

    @property
    def served_rate(self) -> float:
        return self.served / self.requests if self.requests else 0.0


def simulation_days(n: int, end: date = EVAL_END) -> list[date]:
    days, day = [], end
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


def request_schedule(symbols: list[str], days: list[date], per_day: int, seed: int) -> list[tuple[datetime, str]]:
    rng = random.Random(seed)
    schedule = []
    for day in days:
        for symbol in symbols:
            for _ in range(per_day):
                minutes = rng.randrange((LAST_HOUR - FIRST_HOUR) * 60)
                local = datetime.combine(day, time(FIRST_HOUR), tzinfo=IST) + timedelta(minutes=minutes)
                schedule.append((local.astimezone(timezone.utc), symbol))
    return sorted(schedule)


def simulate(scenario: str, *, n_symbols: int, n_days: int, per_day: int, ttl_hours: float,
             cooldown_minutes: float, cooldown_max_minutes: float, seed: int = SEED) -> SimulationStats:
    """One scenario through the real cache_service, on a fresh in-memory database."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db: Session = sessionmaker(bind=engine)()
    days = simulation_days(n_days)
    symbols = [f"SIM{i:02d}.NS" for i in range(n_symbols)]
    history = n_days + HISTORY_DAYS                           # weekdays of synthetic bars, ending EVAL_END
    bars = {s: series_bars(random_walk(s, history, seed + i)) for i, s in enumerate(symbols)}
    provider = ScriptedProvider(bars, SCRIPTS[scenario], days, seed)
    stats = SimulationStats(scenario, ttl_hours, cooldown_minutes, cooldown_max_minutes)
    try:
        for now, symbol in request_schedule(symbols, days, per_day, seed):
            provider.now = now
            stats.requests += 1
            try:
                result = cache_service.get_prices(db, symbol, REQUEST_DAYS, provider, now=now, ttl_hours=ttl_hours,
                                                  history_days=HISTORY_DAYS, cooldown_minutes=cooldown_minutes,
                                                  cooldown_max_minutes=cooldown_max_minutes)
            except cache_service.NoMarketDataError:
                stats.errors += 1
                continue
            stats.served += 1
            stats.by_status[result.cache_status] = stats.by_status.get(result.cache_status, 0) + 1
            stats.staleness.append(weekdays_between(result.data_as_of, latest_final_date(now)))
    finally:
        db.close()
        engine.dispose()
    stats.provider_calls, stats.failed_calls = provider.calls, provider.failed
    return stats


# --- Guards ---

@dataclass(frozen=True)
class GuardCase:
    case_id: str
    kind: str                    # advice | prediction | neutral | hard_neutral
    prohibited: bool
    text: str


def load_guard_cases(path: Path = EVAL_DIR / GUARD_CASES_FILE) -> list[GuardCase]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [GuardCase(r["case_id"], r["kind"], r["prohibited"] == "1", r["text"]) for r in csv.DictReader(handle)]


def guard_metrics(cases: list[GuardCase], flagged: Callable[[str], bool]) -> tuple[dict[str, float], list[str]]:
    """Precision / recall / F1 / false-positive rates, and the ids of every wrong decision."""
    decisions = [(case, flagged(case.text)) for case in cases]
    tp = sum(1 for c, f in decisions if c.prohibited and f)
    fp = sum(1 for c, f in decisions if not c.prohibited and f)
    fn = sum(1 for c, f in decisions if c.prohibited and not f)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    metrics = {"precision": precision, "recall": recall,
               "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    for kind in ("advice", "prediction"):
        group = [f for c, f in decisions if c.kind == kind]
        metrics[f"recall_{kind}"] = sum(group) / len(group) if group else 0.0
    for kind in ("neutral", "hard_neutral"):
        group = [f for c, f in decisions if c.kind == kind]
        metrics[f"false_positive_rate_{kind}"] = sum(group) / len(group) if group else 0.0
    wrong = [c.case_id for c, f in decisions if f != c.prohibited]
    return metrics, wrong


def month_ends(dates: list[date], count: int) -> list[date]:
    """The last trading date of each of the last `count` months in `dates`."""
    last: dict[tuple[int, int], date] = {}
    for d in dates:
        last[(d.year, d.month)] = d
    return [last[k] for k in sorted(last)][-count:]


def analysis_result(symbol: str, bars: list[PriceBar], benchmark_symbol: str, benchmark: list[PriceBar],
                    as_of: date) -> dict:
    """The same result dict stock_service.analyze builds (no headlines: none exist for past dates)."""
    start = as_of - timedelta(days=ANALYSIS_DAYS)
    window = [b for b in bars if start <= b.bar_date <= as_of]
    bench = [b for b in benchmark if start <= b.bar_date <= as_of]
    return {
        "symbol": symbol, "is_index": symbol.startswith("^"), "benchmark": benchmark_symbol,
        "data_as_of": window[-1].bar_date.isoformat(), "window": RISK_WINDOW,
        "indicators": compute_indicators(window, bench), "sentiment": None, "warnings": [],
    }
