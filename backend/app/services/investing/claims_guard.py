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


# --- v3: stock text (Phase 7, spec section 5.9) ---
# A SEPARATE function so the Phase 6 guard above (and its evaluation numbers) stays unchanged.
# The Stock Intelligence Agent describes the past; it must never give advice (buy / sell /
# hold calls, recommendations, targets, valuation verdicts) or predict a price. Deliberately
# strict: when in doubt the template is used. It must NOT flag neutral descriptions such as
# "the price fell 4% last month", "it can fall sharply" or "compared with a buy-and-hold approach".
_ACTIONS = r"(?:buy(?:ing)?|sell(?:ing)?|hold(?:ing)?|invest(?:ing)?|accumulat(?:e|ing)|exit(?:ing)?|short(?:ing)?)"
_MOVES = (r"(?:rise|fall|drop|climb|go (?:up|down)|double|triple|halve|crash|rally|surge|soar|plunge|"
          r"tank|recover|rebound|bounce back|outperform|underperform|beat the (?:market|index)|gain|lose|"
          r"break ?out|hit (?:a )?new highs?|reach)")

_STOCK_ADVICE = re.compile(
    rf"\b(?:you|investors|one) (?:should|must|need to|ought to) (?:(?:consider|think about) )?{_ACTIONS}\b"
    rf"|\b(?:we|i) (?:would )?(?:recommend|suggest|advise)\w*\b"
    rf"|\brecommend\w*\s+(?:\w+\s+){{0,2}}(?:to\s+)?{_ACTIONS}\b"
    rf"|\b(?:strong|a clear|a) (?:buy|sell)\b(?![- ](?:and[- ]hold|offs?)\b)"     # not "buy-and-hold", "sell-off"
    rf"|\b(?:buy|sell|exit|accumulate) (?:now|today|more|on (?:the )?dips?|the dip|this (?:stock|share)s?)\b"
    rf"|\b(?:good|great|right|best|ideal) (?:time|moment|opportunity|entry point) to {_ACTIONS}\b"
    rf"|\b(?:worth|consider) (?:buying|selling|adding)\b"
    rf"|\b(?:book|take) (?:your )?profits?\b"
    rf"|\badd (?:it |this |the stock )?to your (?:portfolio|holdings|position)\b"
    rf"|\b(?:under|over)valued\b|\b(?:over|under)priced\b|\bover(?:bought|sold)\b|\bundersold\b"
    rf"|\bgo (?:long|short)\b",
    re.IGNORECASE,
)

_STOCK_PREDICTION = re.compile(
    rf"\b(?:will|shall|is going to|are going to|is set to|are set to|is (?:likely|expected|poised|bound|sure) to|"
    rf"are (?:likely|expected|poised|bound|sure) to|should) (?:(?:soon|likely|probably|definitely|continue to) )?{_MOVES}\w*\b"
    rf"|\babout to {_MOVES}\w*\b"
    rf"|\b(?:price|share|stock) target\b|\btarget (?:price|of ₹)\b"
    rf"|\b(?:upside|downside) (?:potential|of|target)\b"
    rf"|\b(?:will|is going to) (?:make|give|earn|return)\b",
    re.IGNORECASE,
)


def find_prohibited_stock_claims(text: str) -> list[str]:
    """
    Phase 7 stock-text guard: the Phase 6 promises ("guaranteed", "risk-free") PLUS advice and
    prediction wording. Returns every phrase found, lower-cased, in order of appearance.
    """
    found = [(m.start(), m.group(0).lower()) for m in _PROHIBITED.finditer(text)]
    found += [(m.start(), m.group(0).lower()) for m in _STOCK_ADVICE.finditer(text)]
    found += [(m.start(), m.group(0).lower()) for m in _STOCK_PREDICTION.finditer(text)]
    return [phrase for _, phrase in sorted(found)]


def is_stock_text_compliant(text: str) -> bool:
    return not find_prohibited_stock_claims(text)
