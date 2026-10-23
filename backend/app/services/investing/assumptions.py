# app/services/investing/assumptions.py
#
# Purpose: the ONE place the Monte Carlo and SIP maths get their market assumptions
# from. Returns, volatilities, the SIP safety factor and the simulation settings are
# DATA (environment variables, see core/config.py), never constants inside the maths,
# so they can be shown to the user (GET /api/investing/assumptions), stored with every
# plan, and swapped for the low / base / high sensitivity runs of the evaluation.
# The defaults are illustrative placeholders, not cited market figures.

from dataclasses import dataclass

from app.core.config import Settings, settings
from app.services.investing.allocation import ASSET_CLASSES
from app.services.investing.portfolio_stats import AssetAssumption

ASSUMPTIONS_NOTE = (
    "Illustrative placeholder assumptions for an educational tool, not cited market data. "
    "Real returns can be lower, higher or negative."
)


@dataclass(frozen=True)
class InvestingAssumptions:
    assets: dict[str, AssetAssumption]    # asset class -> annual expected return and volatility
    sip_safety_factor: float
    mc_paths: int
    mc_seed: int
    note: str = ASSUMPTIONS_NOTE

    def to_dict(self) -> dict:
        """JSON-ready form: stored in investment_plans.assumptions_json and returned by the API."""
        return {
            "assets": [
                {"asset_class": name, "expected_return": self.assets[name].expected_return,
                 "volatility": self.assets[name].volatility}
                for name in ASSET_CLASSES
            ],
            "sip_safety_factor": self.sip_safety_factor,
            "mc_paths": self.mc_paths,
            "mc_seed": self.mc_seed,
            "note": self.note,
        }


def load_assumptions(config: Settings = settings) -> InvestingAssumptions:
    """Reads the INVEST_* settings. `config` can be replaced in tests and evaluation runs."""
    return InvestingAssumptions(
        assets={
            "equity": AssetAssumption(config.INVEST_EQUITY_RETURN, config.INVEST_EQUITY_VOL),
            "debt": AssetAssumption(config.INVEST_DEBT_RETURN, config.INVEST_DEBT_VOL),
            "gold": AssetAssumption(config.INVEST_GOLD_RETURN, config.INVEST_GOLD_VOL),
            "cash": AssetAssumption(config.INVEST_CASH_RETURN, config.INVEST_CASH_VOL),
        },
        sip_safety_factor=config.INVEST_SIP_SAFETY_FACTOR,
        mc_paths=config.INVEST_MC_PATHS,
        mc_seed=config.INVEST_MC_SEED,
    )
