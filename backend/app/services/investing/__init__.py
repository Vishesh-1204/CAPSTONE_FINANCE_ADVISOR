# app/services/investing/__init__.py
#
# Purpose: the Investment Advisor Agent's (Phase 6) building blocks.
#
# Pure modules (no database, no LLM, no HTTP; plain Python, float maths):
#   risk_scoring.py     risk tolerance (questionnaire) vs capacity (finances); the safer category wins
#   allocation.py       target split per category with a horizon equity cap, drift / rebalance rows
#   portfolio_stats.py  portfolio expected return and volatility from per-class assumptions
#   sip_planner.py      affordable SIP from the lower-band surplus, future value, months to a goal
#   monte_carlo.py      seeded p10 / p50 / p90 corpus range and probability of reaching a goal
#   claims_guard.py     rejects LLM text that promises returns ("guaranteed", "risk-free")
#
# Money is float inside these modules, and is converted to exact Decimal
# (core/money.py) by the service before it is saved or returned.
