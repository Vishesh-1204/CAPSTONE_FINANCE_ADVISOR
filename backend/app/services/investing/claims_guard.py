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


def _matches(text: str, patterns: tuple[re.Pattern, ...]) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0).lower()) for pattern in patterns for m in pattern.finditer(text)]


def find_prohibited_stock_claims_v3(text: str) -> list[str]:
    """Guard v3 exactly as evaluated in engine 7.0 (kept for the before/after comparison)."""
    return [phrase for _, phrase in sorted(_matches(text, (_PROHIBITED, _STOCK_ADVICE, _STOCK_PREDICTION)))]


# --- v3.1 (engine 7.1, after the Phase 7B evaluation) ---
# v3 had recall 40% on the 85 held-out sentences (evaluation/stocks_guard_cases.csv) and flagged 3 of 20
# "hard neutral" ones. Every pattern below was written from a v3 miss in THAT set (case id in the
# comment), never from the second set (stocks_guard_cases_b.csv), which was committed before this code
# so the fix could be measured on sentences it was not written from. Recall on set A is therefore no
# longer evidence; set B is the honest (same-author) check. See docs/modules/stocks-evaluation.md.
STOCK_GUARD_VERSION = "v3.1"

_ACTIONS_V31 = (r"(?:buy(?:ing)?|sell(?:ing)?|hold(?:ing)?|invest(?:ing)?|accumulat(?:e|ing)|exit(?:ing)?|"
                r"short(?:ing)?|add(?:ing)?|trim(?:ming)?|reduc(?:e|ing)|avoid(?:ing)?|switch(?:ing)?|"
                r"get(?:ting)? (?:in|out)|enter(?:ing)?)")
_MOVE_STEMS = (r"(?:ris|ros|fall|fell|drop|climb|doubl|tripl|halv|crash|rall|surg|soar|plung|tank|recover|"
               r"rebound|bounc|outperform|underperform|gain|los|reach|hit|touch)\w*")

_STOCK_ADVICE_V31 = re.compile(
    rf"\b(?:you|investors|one) (?:should|must|need to|ought to) (?:(?:consider|think about) )?{_ACTIONS_V31}\b"  # A19
    rf"|\b(?:we|i) (?:would|will|'d) (?:{_ACTIONS_V31}|stay away)\b"                                    # A05
    rf"|\b(?:looks|seems|is|appears) (?:sensible|wise|prudent|smart|advisable|a good idea)\b"           # A04
    rf"|\b(?:attractive|good|great|ideal|perfect) (?:entry|exit|buying|selling) (?:point|level|price)\b"  # A06
    rf"|\bconsider {_ACTIONS_V31}\b"                                                                    # A07
    rf"|\b(?:right|best|good|ideal|perfect) time to {_ACTIONS_V31}\b"                                   # A16
    rf"|\b(?:good|great|solid|safe|top) (?:stock|share|pick|bet)s? to (?:buy|hold|own|invest in)\b"     # A18
    rf"|\bstay invested\b|\b(?:do not|don't) (?:sell|buy|exit)\b",                                       # A14
    re.IGNORECASE,
)
# Sentence-initial imperatives: "Add more on weakness." (A10), "Exit the position ..." (A11),
# "Switch from this stock ..." (A15), "Expect the stock to ..." / "Expect gains ..." (P02, P18)
_IMPERATIVE_V31 = re.compile(
    r"^\W*(?:buy|sell|hold|add|exit|switch|accumulate|trim|reduce|avoid|expect)\b", re.IGNORECASE)
_STOCK_PREDICTION_V31 = re.compile(
    rf"\b(?:will|should|is (?:likely|expected|poised|set) to) (?:touch|hit|cross)\b"                    # P04
    rf"|\b(?:breakout|rally|correction|crash|fall|rebound|recovery|bounce|decline|downturn) "
    rf"(?:is|are) (?:coming|imminent|near|on the way|ahead|due)\b"                                      # P05, P14, P17
    rf"|\b(?:rally|fall|decline|trend|uptrend|downtrend|momentum|rise) (?:will|is set to|is likely to) continue\b"  # P06
    rf"|\b(?:upside|downside) (?:from here )?(?:is |looks |appears )?(?:limited|capped|ahead)\b"         # P07, P19
    rf"|\b(?:poised|set|ready|due|primed) for (?:a |an )?(?:rebound|rally|fall|correction|breakout|"
    rf"recovery|bounce|rise|decline|surge)\b"                                                           # P08
    rf"|\bcan(?:not|'t| not) go (?:much )?(?:lower|higher|down|up)\b"                                   # P12
    rf"|\b(?:high|good|strong|real) (?:chance|probability|likelihood) (?:that )?(?:the )?(?:price|stock|share)"  # P13
    rf"|\b{_MOVE_STEMS}\b[^.!?;]*\b(?:tomorrow|next (?:week|month|quarter|year)|soon\b(?! after))",     # P13, P20
    re.IGNORECASE,
)
# A match inside a DISCLAIMING frame earlier in the same sentence is dropped: "Nobody can say whether the
# price will rise or fall" (H08), "it does not mean the price will fall" (H19).
# Known gap: a negated prediction ("the price will not fall") is NOT caught by v3 or v3.1 (no set-A miss
# covered it, so no pattern was added; tests/test_stock_claims_guard.py records it).
_DISCLAIMING_FRAME = re.compile(
    r"\b(?:(?:does|do|did) not|doesn't|don't|cannot|can't|can not) (?:mean|imply|say|tell|know|predict|promise)\b"
    r"|\b(?:no one|nobody|no-one)\b|\bwhether\b"
    r"|\bnot (?:a|any) (?:forecast|prediction|promise|guarantee|recommendation)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?;]+[.!?;]*")


def find_prohibited_stock_claims(text: str) -> list[str]:
    """
    Phase 7 stock-text guard (v3.1): the Phase 6 promises ("guaranteed", "risk-free") PLUS advice and
    prediction wording, minus matches inside a disclaiming frame. Every phrase found, lower-cased,
    in order of appearance.
    """
    found: list[tuple[int, str]] = []
    for sentence in _SENTENCE.finditer(text):
        part, offset = sentence.group(0), sentence.start()
        hits = _matches(part, (_PROHIBITED, _STOCK_ADVICE, _STOCK_PREDICTION, _STOCK_ADVICE_V31,
                               _STOCK_PREDICTION_V31))
        imperative = _IMPERATIVE_V31.match(part)
        if imperative:
            hits.append((imperative.start(), imperative.group(0).strip(" \"'(").lower()))
        for start, phrase in hits:
            frame = _DISCLAIMING_FRAME.search(part[:start])
            if frame is None:
                found.append((offset + start, phrase))
    seen, out = set(), []
    for start, phrase in sorted(found):              # two patterns can match the same words
        if (start, phrase) not in seen:
            seen.add((start, phrase))
            out.append(phrase)
    return out


def is_stock_text_compliant(text: str) -> bool:
    return not find_prohibited_stock_claims(text)
