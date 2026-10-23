# app/services/investing/claims_guard.py
#
# Purpose: compliance-style safety net for LLM-written investment text. The numeric guard from
# Phase 4 catches invented rupee amounts; this one catches promises and absolutes that an
# educational tool must never make ("guaranteed returns", "risk-free"). If either guard fails,
# the caller discards the LLM text and uses the deterministic template instead.
# Also usable as a research metric (how often would the raw LLM output have been unsafe?).

import re

_PROHIBITED = re.compile(
    r"\b(guarantee[sd]?|guaranteed|assured returns?|risk[- ]free|no risk|zero risk|without risk|"
    r"sure[- ]?shot|can[’']?t lose|cannot lose|will definitely|100% safe|certain to (?:grow|earn|profit)|"
    # v2 (after the Phase 6B evaluation): phrasings v1 missed on the labelled sentences. They were
    # chosen AFTER seeing those misses, so recall on that set is no longer evidence (see the write-up).
    r"surely|for sure|locked[- ]in|fully protected|completely safe|no downside|can(?:not|[’']?t) fail|"
    r"no chance of los(?:s|ing)|never (?:be )?at a loss)\b",
    re.IGNORECASE,
)


def find_prohibited_claims(text: str) -> list[str]:
    """Returns every prohibited phrase found, lower-cased, in order of appearance."""
    return [match.group(0).lower() for match in _PROHIBITED.finditer(text)]


def is_compliant(text: str) -> bool:
    return not find_prohibited_claims(text)
