# app/services/investing/instrument_catalog.py
#
# Purpose: static, plain-language description of what each asset class can be held in.
# Deliberately instrument TYPES only: no named funds, no stocks, no return figures.
# Naming real products with returns would mean fabricated or stale data and a
# regulatory risk; specific securities belong to Phase 7 (Stock Intelligence).
# Pure data: no database, no LLM.

from dataclasses import dataclass

from app.services.investing.allocation import ASSET_CLASSES


@dataclass(frozen=True)
class InstrumentTypes:
    asset_class: str
    instrument_types: tuple[str, ...]
    note: str


CATALOG: dict[str, InstrumentTypes] = {
    "equity": InstrumentTypes(
        "equity", ("Broad-market index fund or ETF", "Flexi-cap fund"),
        "Higher long-term growth, can fall sharply in the short term.",
    ),
    "debt": InstrumentTypes(
        "debt", ("Short-duration debt fund", "Government securities fund", "Fixed deposit"),
        "Lower swings, returns can still vary.",
    ),
    "gold": InstrumentTypes(
        "gold", ("Gold ETF", "Sovereign-gold-type instrument, if available"),
        "Diversifier, the price can be flat for years.",
    ),
    "cash": InstrumentTypes(
        "cash", ("Savings account", "Liquid fund"),
        "Safety and access, loses to inflation over time.",
    ),
}


def instruments_for(weights: dict[str, float]) -> list[dict]:
    """Catalog rows for the asset classes that have a share in `weights`, in the standard order."""
    return [
        {"asset_class": name, "instrument_types": list(CATALOG[name].instrument_types), "note": CATALOG[name].note}
        for name in ASSET_CLASSES
        if weights.get(name, 0.0) > 0
    ]
