# app/services/forecasting/cashflow_projector.py
#
# Purpose: turn the forecast pieces into a DAILY balance trajectory.
#
#   balance(day) = opening balance
#                + scheduled credits up to that day     (salary on its date)
#                - scheduled debits up to that day      (rent, EMI, SIP on their dates)
#                - variable spend per day x days so far (groceries, food, shopping...)
#
# Variable spend per day = that month's variable forecast / days in that month.
# Only variable spending is uncertain (scheduled items are treated as known), so
# the band comes from the variable interval and widens linearly with elapsed days:
# on day k of n, the band is k/n of the full horizon interval.
#   * LOWER balance uses the UPPER spending estimate (spend more -> less money left).
#   * Cash crunch = first day the LOWER balance is below zero.
# A square-root widening or day-of-week weights are possible later ablations.

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Sequence

from app.core.transaction_types import CREDIT
from app.services.forecasting.horizon import MonthSlice


@dataclass(frozen=True)
class ScheduledFlow:
    """A known future payment: a projected recurring item on one date."""
    on: date
    amount: float           # positive; direction gives the sign
    direction: str          # 'credit' (in) or 'debit' (out)


@dataclass(frozen=True)
class DailyBalance:
    on: date
    balance: float
    lower: float
    upper: float


@dataclass(frozen=True)
class Projection:
    """Everything the cash-flow forecast says about the window."""
    trajectory: list[DailyBalance]
    variable_point: float           # variable spending over the window
    variable_lower: float
    variable_upper: float
    scheduled_in: float             # scheduled credits inside the window
    scheduled_out: float            # scheduled debits inside the window
    cash_crunch_date: date | None   # first day the LOWER balance < 0

    @property
    def end(self) -> DailyBalance:
        return self.trajectory[-1]


def project_cashflow(
    start: date,
    end: date,
    opening_balance: float,
    slices: Sequence[MonthSlice],
    monthly_variable: Sequence[float],
    variable_lower: float,
    variable_upper: float,
    scheduled: Iterable[ScheduledFlow],
) -> Projection:
    """
    Builds the trajectory for every day in [start, end].
    `monthly_variable[i]` is the FULL-month variable forecast for `slices[i]`;
    `variable_lower/upper` is the interval of the window total (from the backtest).
    Scheduled flows outside the window are ignored.
    """
    if len(slices) != len(monthly_variable):
        raise ValueError("one monthly forecast is needed per month slice")
    if end < start:
        raise ValueError("forecast window is empty")

    daily_rate = {s.key: value / s.days_in_month for s, value in zip(slices, monthly_variable)}
    flows_by_day: dict[date, float] = {}
    scheduled_in = scheduled_out = 0.0
    for flow in scheduled:
        if start <= flow.on <= end:
            signed = flow.amount if flow.direction == CREDIT else -flow.amount
            flows_by_day[flow.on] = flows_by_day.get(flow.on, 0.0) + signed
            if signed > 0:
                scheduled_in += flow.amount
            else:
                scheduled_out += flow.amount

    total_days = (end - start).days + 1
    variable_point = sum(s.weight * v for s, v in zip(slices, monthly_variable))
    band_down = max(0.0, variable_point - variable_lower)   # spending may be this much LOWER
    band_up = max(0.0, variable_upper - variable_point)     # ... or this much HIGHER

    trajectory: list[DailyBalance] = []
    scheduled_net = spent = 0.0
    crunch: date | None = None
    for offset in range(total_days):
        day = start + timedelta(days=offset)
        scheduled_net += flows_by_day.get(day, 0.0)
        spent += daily_rate.get(f"{day.year:04d}-{day.month:02d}", 0.0)
        fraction = (offset + 1) / total_days
        balance = opening_balance + scheduled_net - spent
        point = DailyBalance(day, balance, balance - band_up * fraction, balance + band_down * fraction)
        trajectory.append(point)
        if crunch is None and point.lower < 0:
            crunch = day

    return Projection(
        trajectory=trajectory,
        variable_point=variable_point,
        variable_lower=variable_lower,
        variable_upper=variable_upper,
        scheduled_in=scheduled_in,
        scheduled_out=scheduled_out,
        cash_crunch_date=crunch,
    )
