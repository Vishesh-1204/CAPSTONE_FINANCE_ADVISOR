# tests/test_portfolio_stats.py

import pytest

from app.services.investing.allocation import target_allocation
from app.services.investing.portfolio_stats import AssetAssumption, portfolio_return_and_volatility

ASSUMPTIONS = {
    "equity": AssetAssumption(0.11, 0.17), "debt": AssetAssumption(0.06, 0.04),
    "gold": AssetAssumption(0.07, 0.14), "cash": AssetAssumption(0.035, 0.005),
}


def test_all_equity_matches_the_equity_assumption():
    mean, vol = portfolio_return_and_volatility({"equity": 1.0}, ASSUMPTIONS)
    assert mean == pytest.approx(0.11) and vol == pytest.approx(0.17)


def test_diversification_lowers_volatility_below_the_weighted_average():
    weights = {"equity": 0.5, "debt": 0.5}
    _, vol = portfolio_return_and_volatility(weights, ASSUMPTIONS)
    assert vol < 0.5 * 0.17 + 0.5 * 0.04


def test_riskier_categories_have_higher_expected_return_and_volatility():
    stats = [portfolio_return_and_volatility(target_allocation(c, 15), ASSUMPTIONS)
             for c in ("conservative", "moderate", "aggressive")]
    assert [s[0] for s in stats] == sorted(s[0] for s in stats)
    assert [s[1] for s in stats] == sorted(s[1] for s in stats)


def test_missing_assumption_is_rejected():
    with pytest.raises(ValueError):
        portfolio_return_and_volatility({"equity": 1.0}, {})
