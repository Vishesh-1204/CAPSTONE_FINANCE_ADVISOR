# app/market_data/__init__.py
#
# Purpose: the market-data layer (Phase 7). Like llm/, it is the only place that talks to an
# outside data vendor: base.py (contract), the adapters, factory.py (pick one from .env) and
# cache_service.py (database cache + fallback chain, the only entry point the app uses).
