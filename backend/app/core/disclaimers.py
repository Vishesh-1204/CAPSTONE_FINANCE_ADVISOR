# app/core/disclaimers.py
#
# Purpose: user-facing disclaimers, in one place. The Investment Advisor's
# disclaimer is added by CODE (to every API response and after every
# explanation), never written by the LLM. Lives in core/ so schemas, services
# and agents can all use it without importing each other.

INVESTING_DISCLAIMER = (
    "Educational planning tool, not financial advice. "
    "Projections are scenarios under stated assumptions, not guarantees."
)
