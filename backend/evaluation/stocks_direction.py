# backend/evaluation/stocks_direction.py
#
# Purpose: RQ3 helpers. Can any model predict NEXT-DAY direction better than simple baselines, out of
# sample? Builds the pre-registered features (evaluation/stocks_rules.py RQ3_FEATURES), runs the
# baselines and the two models (logistic regression, LightGBM) on expanding-window walk-forward splits,
# and computes accuracy, the exact binomial test against 50%, an exact McNemar test against "always up",
# and a long/flat strategy with transaction costs.
#
# Leakage controls (pre-registered, see stocks_rules.py L1-L6):
#   L1 build_rows computes the features of day t from closes[0..t] only. Every indicator used
#      (simple returns, SMA, Wilder RSI) depends only on the past; a unit test changes all prices
#      after t and checks the features at t do not move.
#   L2 walk_forward_splits: train = rows [0, train_end), test = [train_end, train_end + 21). Never shuffled.
#   L3 a NEW model (with its own StandardScaler inside a Pipeline) is built and fitted for every fold,
#      on that fold's training rows only.
#   L4 fixed settings from stocks_rules.py; no search.
#   The label of the last training row needs close[train_end], which is known at the close of the
#   first test day, the moment its prediction is made: no overlap for a 1-day horizon.

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.services.stocks.indicators import max_drawdown, rsi, sma
from app.services.stocks.walk_forward import binomial_p_value_greater, walk_forward_splits
from evaluation.stocks_data import Series
from evaluation.stocks_rules import (
    RQ3_FEATURES, RQ3_INITIAL_TRAIN, RQ3_LGBM_PARAMS, RQ3_LR_PARAMS, RQ3_TEST_BLOCK,
)

TRADING_DAYS = 252
BASELINES = ("B_up", "B_prev", "B_mom5", "B_sma")
MODELS = ("LR", "LGBM")
WARMUP = 200                    # first usable index: SMA200 needs 200 closes (indices 0..199)


@dataclass(frozen=True)
class Row:
    """One usable day t: features from closes up to t, the next-day return, and the label."""
    index: int                   # position of day t in the series
    features: tuple[float, ...]  # in RQ3_FEATURES order
    next_return: float           # close[t+1] / close[t] - 1
    label: int                   # 1 up, 0 down (zero-change days are dropped)
    sma50_above_200: bool


def build_rows(closes: Sequence[float]) -> list[Row]:
    """Feature rows for every day with a full warm-up and a known next close; flat days dropped."""
    closes = list(closes)
    n = len(closes)
    if n < WARMUP + 2:
        return []
    returns = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, n)]   # returns[i] = close i vs i-1
    sma50, sma200, rsi14 = sma(closes, 50), sma(closes, 200), rsi(closes, 14)
    rows = []
    for t in range(WARMUP - 1, n - 1):
        window = returns[t - 19:t + 1]                  # the 20 returns ending at t
        mean = math.fsum(window) / 20
        vol20 = math.sqrt(math.fsum((r - mean) ** 2 for r in window) / 19)
        features = (
            returns[t], returns[t - 1], returns[t - 2], returns[t - 3], returns[t - 4],
            rsi14[t], vol20, closes[t] / closes[t - 5] - 1,
            closes[t] / sma50[t] - 1, closes[t] / sma200[t] - 1,
        )
        change = closes[t + 1] - closes[t]
        if change == 0:
            continue
        rows.append(Row(t, features, closes[t + 1] / closes[t] - 1, 1 if change > 0 else 0, sma50[t] > sma200[t]))
    assert all(len(r.features) == len(RQ3_FEATURES) for r in rows)
    return rows


# --- Baselines (need no training) ---

def baseline_signal(name: str, row: Row) -> int:
    ret_lag0, mom5 = row.features[0], row.features[7]
    if name == "B_up":
        return 1
    if name == "B_prev":
        return 1 if ret_lag0 > 0 else 0
    if name == "B_mom5":
        return 1 if mom5 > 0 else 0
    if name == "B_sma":
        return 1 if row.sma50_above_200 else 0
    raise ValueError(f"unknown baseline {name}")


# --- Models (a fresh one per fold: L3) ---

def make_lr():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    return Pipeline([("scale", StandardScaler()), ("lr", LogisticRegression(**RQ3_LR_PARAMS))])


def make_lgbm():
    from lightgbm import LGBMClassifier
    return LGBMClassifier(**RQ3_LGBM_PARAMS)


MODEL_FACTORIES: dict[str, Callable[[], object]] = {"LR": make_lr, "LGBM": make_lgbm}


@dataclass
class WalkForwardResult:
    symbol: str
    test_rows: list[Row]
    signals: dict[str, list[int]]          # method -> 0/1 per test row
    folds: int


def walk_forward(symbol: str, rows: list[Row], factories: dict[str, Callable[[], object]] | None = None,
                 initial_train: int = RQ3_INITIAL_TRAIN, block: int = RQ3_TEST_BLOCK) -> WalkForwardResult:
    """Expanding-window walk-forward; every model is refitted on rows [0, train_end) for each block."""
    factories = MODEL_FACTORIES if factories is None else factories
    splits = walk_forward_splits(len(rows), initial_train, test_size=block, step=block)
    signals: dict[str, list[int]] = {name: [] for name in (*BASELINES, *factories)}
    test_rows: list[Row] = []
    for train_end, test_start, test_end in splits:
        train, test = rows[:train_end], rows[test_start:test_end]
        assert max(r.index for r in train) < min(r.index for r in test)      # L2, checked every fold
        test_rows.extend(test)
        for name in BASELINES:
            signals[name].extend(baseline_signal(name, r) for r in test)
        x_train, y_train = [list(r.features) for r in train], [r.label for r in train]
        x_test = [list(r.features) for r in test]
        for name, factory in factories.items():
            model = factory()                                                # L3: new model per fold
            model.fit(x_train, y_train)
            signals[name].extend(int(p) for p in model.predict(x_test))
    return WalkForwardResult(symbol, test_rows, signals, len(splits))


# --- Metrics ---

def hits(rows: Sequence[Row], signals: Sequence[int]) -> int:
    return sum(1 for r, s in zip(rows, signals) if r.label == s)


def mcnemar_p_greater(rows: Sequence[Row], model: Sequence[int], baseline: Sequence[int]) -> tuple[float, int, int]:
    """
    Exact one-sided McNemar: on the days the two disagree, is the model right more often than the
    baseline? Returns (p, model_only_right, baseline_only_right).
    """
    b = sum(1 for r, m, s in zip(rows, model, baseline) if m == r.label and s != r.label)
    c = sum(1 for r, m, s in zip(rows, model, baseline) if m != r.label and s == r.label)
    if b + c == 0:
        return 1.0, 0, 0
    return binomial_p_value_greater(b, b + c, 0.5), b, c


@dataclass(frozen=True)
class StrategyStats:
    total_return: float
    cagr: float
    max_drawdown: float          # negative fraction like the app's: -0.35 = a 35% fall from a peak
    trades: int                  # position changes (each one pays the cost)
    exposure: float              # share of days invested


def strategy(next_returns: Sequence[float], signals: Sequence[int], cost_bps: float) -> StrategyStats:
    """
    Long/flat: invested on day t+1 when the signal at t is 1. Starts flat; every change of position
    (in or out) costs cost_bps. Buy-and-hold is all ones (one entry cost).
    """
    if len(next_returns) != len(signals) or not signals:
        raise ValueError("returns and signals must be non-empty and the same length")
    cost = cost_bps / 10000
    growth, position, trades, equity = 1.0, 0, 0, [1.0]
    for r, s in zip(next_returns, signals):
        if s != position:
            growth *= 1 - cost
            trades += 1
            position = s
        growth *= 1 + (r if position else 0.0)
        equity.append(growth)
    years = len(signals) / TRADING_DAYS
    return StrategyStats(growth - 1, growth ** (1 / years) - 1, max_drawdown(equity).depth, trades,
                         sum(signals) / len(signals))


def binomial_band(n: int, z: float = 2.58) -> tuple[float, float]:
    """50% +/- z standard errors (L5 placebo check)."""
    half = z * math.sqrt(0.25 / n)
    return 0.5 - half, 0.5 + half
