# tests/test_allocation.py

import pytest

from app.services.investing.allocation import equity_cap, rebalance_actions, target_allocation


@pytest.mark.parametrize("category", ["conservative", "moderate", "aggressive"])
@pytest.mark.parametrize("horizon", [0.5, 2, 4, 6, 10, 20])
def test_allocation_always_sums_to_one(category, horizon):
    assert sum(target_allocation(category, horizon).values()) == pytest.approx(1.0)


def test_higher_risk_means_more_equity_over_a_long_horizon():
    equity = [target_allocation(c, 15)["equity"] for c in ("conservative", "moderate", "aggressive")]
    assert equity == sorted(equity) and len(set(equity)) == 3


def test_short_horizon_caps_equity():
    assert target_allocation("aggressive", 2)["equity"] == equity_cap(2) == 0.20


def test_under_one_year_has_no_equity_and_excess_goes_to_cash():
    allocation = target_allocation("aggressive", 0.5)
    assert allocation["equity"] == 0.0 and allocation["cash"] > 0.5


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        target_allocation("reckless", 10)


def test_no_flags_when_holdings_match_target():
    target = target_allocation("moderate", 10)
    holdings = {name: 100000 * weight for name, weight in target.items()}
    assert all(row.action == "hold" for row in rebalance_actions(holdings, target))


def test_overweight_equity_is_flagged_to_reduce_and_amounts_balance():
    target = target_allocation("conservative", 10)
    holdings = {"equity": 70000, "debt": 20000, "gold": 5000, "cash": 5000}
    rows = {row.asset_class: row for row in rebalance_actions(holdings, target)}
    assert rows["equity"].action == "reduce" and rows["equity"].amount_change < 0
    assert sum(row.amount_change for row in rows.values()) == pytest.approx(0, abs=0.05)


def test_empty_portfolio_flags_nothing():
    assert all(row.action == "hold" for row in rebalance_actions({}, target_allocation("moderate", 10)))
