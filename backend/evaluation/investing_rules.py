# backend/evaluation/investing_rules.py
#
# Purpose: PRE-REGISTERED suitability rules for the Investment Advisor evaluation
# (Phase 6B). There is no ground truth for a "suitable" allocation, so instead of
# scoring methods against labels we score them against rules written down BEFORE
# any method was evaluated. This file was committed on its own, before
# evaluation/investing_methods.py existed; the git history is the proof.
#
# Rules look only at RAW persona facts (age, horizon, emergency months, income
# stability, dependents, questionnaire answers, surpluses) and at what a method
# RECOMMENDS (equity share, SIP, emergency top-up). They never look at a
# method's own scores or categories, so they cannot simply copy P's design.
#
# Honesty labels (column "design"):
#   enforced     P is BUILT to satisfy the rule (R1, R2, R3a, R4). P scoring 0% there
#                proves little; it only checks the implementation.
#   independent  judgement P was NOT tuned for (R5, R6, R7). These carry more weight.
#   secondary    a stricter reading P fails BY DESIGN (R3b), reported to show the trade-off.
# R4 note: P caps the SIP at the surplus, but NOT the emergency top-up, so P can still
# break R4 when the top-up alone exceeds a small surplus. That is a real finding, not a bug
# in the rule.
#
# Rule kinds: "allocation" (R1, R2, R5, R6, R7) and "sip" (R3a, R3b, R4) are reported
# separately, so a reader can see whether a method wins on allocation or on SIP sizing.
#
# Authors: R1-R7 written by Vishesh with Claude Code on 2026-10-23, after reading the
# Phase 6 spec. Rules written by a teammate who has NOT read P's code are added in a
# LATER, separate commit and marked author="teammate:<name>" (none yet).
#
# Pure Python: no database, no app imports.

from collections.abc import Callable
from dataclasses import dataclass

EPSILON = 1e-9           # float slack for share comparisons
RUPEE_SLACK = 1.0        # rupee slack for money comparisons (rounding to paise)
EMERGENCY_FLOOR_MONTHS = 3.0
EMERGENCY_FILL_MONTHS = 12

ALLOCATION, SIP = "allocation", "sip"
ENFORCED, INDEPENDENT, SECONDARY = "enforced", "independent", "secondary"


@dataclass(frozen=True)
class PersonaFacts:
    """The raw facts a rule may look at (never a method's own scores)."""
    age: int
    horizon_years: float
    emergency_months: float          # liquid savings / monthly essential spending
    income_stability: int            # 1 = irregular ... 5 = very stable
    dependents: int
    answers: tuple[int, int, int]    # questionnaire, each 1 (cautious) ... 5 (comfortable with risk)
    essential_monthly: float         # rupees a month of must-pay spending
    liquid_savings: float
    low_surplus: float               # lower-band monthly surplus BEFORE investing


@dataclass(frozen=True)
class Recommendation:
    """What a method recommends for one persona."""
    equity: float                    # equity share of the target allocation, 0-1
    monthly_sip: float               # total monthly SIP
    emergency_topup: float           # monthly amount set aside for the emergency fund


@dataclass(frozen=True)
class Rule:
    rule_id: str
    kind: str                        # ALLOCATION | SIP
    design: str                      # ENFORCED | INDEPENDENT | SECONDARY
    author: str
    description: str
    applies: Callable[[PersonaFacts], bool]
    satisfied: Callable[[PersonaFacts, Recommendation], bool]


def gap_to_floor(facts: PersonaFacts) -> float:
    """Rupees missing to reach EMERGENCY_FLOOR_MONTHS of essential spending."""
    return max(0.0, EMERGENCY_FLOOR_MONTHS * facts.essential_monthly - facts.liquid_savings)


_TEAM = "vishesh+claude-code (2026-10-23)"

RULES: tuple[Rule, ...] = (
    Rule("R1", ALLOCATION, ENFORCED, _TEAM,
         "Horizon under 1 year: no equity.",
         lambda f: f.horizon_years < 1,
         lambda f, r: r.equity <= EPSILON),
    Rule("R2", ALLOCATION, ENFORCED, _TEAM,
         "Horizon under 3 years: equity at most 20%.",
         lambda f: f.horizon_years < 3,
         lambda f, r: r.equity <= 0.20 + EPSILON),
    Rule("R3a", SIP, ENFORCED, _TEAM,
         "Under 3 months of emergency fund: the monthly top-up must reach 3 months within 12 months "
         "(reserved before any SIP).",
         lambda f: f.emergency_months < EMERGENCY_FLOOR_MONTHS,
         lambda f, r: r.emergency_topup * EMERGENCY_FILL_MONTHS >= gap_to_floor(f) - RUPEE_SLACK),
    Rule("R3b", SIP, SECONDARY, _TEAM,
         "Strict reading: under 3 months of emergency fund, no SIP at all.",
         lambda f: f.emergency_months < EMERGENCY_FLOOR_MONTHS,
         lambda f, r: r.monthly_sip <= RUPEE_SLACK),
    Rule("R4", SIP, ENFORCED, _TEAM,
         "SIP plus emergency top-up never exceeds the lower-band monthly surplus (no SIP when it is <= 0).",
         lambda f: True,
         lambda f, r: r.monthly_sip + r.emergency_topup <= max(0.0, f.low_surplus) + RUPEE_SLACK),
    Rule("R5", ALLOCATION, INDEPENDENT, _TEAM,
         "Irregular income (stability <= 2) and 2 or more dependents: equity at most 40%.",
         lambda f: f.income_stability <= 2 and f.dependents >= 2,
         lambda f, r: r.equity <= 0.40 + EPSILON),
    Rule("R6", ALLOCATION, INDEPENDENT, _TEAM,
         "Every questionnaire answer <= 2 (a cautious person): equity at most 30%.",
         lambda f: max(f.answers) <= 2,
         lambda f, r: r.equity <= 0.30 + EPSILON),
    Rule("R7", ALLOCATION, INDEPENDENT, _TEAM,
         "Not over-cautious: horizon >= 10 years, emergency fund >= 6 months, stability >= 4, "
         "dependents <= 1 and every answer >= 4: equity at least 50%.",
         lambda f: (f.horizon_years >= 10 and f.emergency_months >= 6 and f.income_stability >= 4
                    and f.dependents <= 1 and min(f.answers) >= 4),
         lambda f, r: r.equity >= 0.50 - EPSILON),
)


def check(facts: PersonaFacts, recommendation: Recommendation) -> dict[str, bool | None]:
    """rule_id -> True (satisfied), False (violated) or None (does not apply to this persona)."""
    return {
        rule.rule_id: (rule.satisfied(facts, recommendation) if rule.applies(facts) else None)
        for rule in RULES
    }
