# app/services/stocks/lexicon_sentiment.py
#
# Purpose: a tiny word-list headline scorer used as (1) a FALLBACK when FinBERT is not loaded
# (it needs PyTorch, which is heavy) and (2) a BASELINE to compare FinBERT against in the paper.
# Deliberately simple and transparent. Returns (positive, negative, neutral) probabilities.

import re

POSITIVE_WORDS = frozenset({
    "gain", "gains", "rise", "rises", "rose", "surge", "surges", "jump", "jumps", "profit", "profits",
    "growth", "beat", "beats", "record", "upgrade", "upgrades", "strong", "rally", "rallies", "outperform",
    "bullish", "recovery", "expands", "expansion", "dividend", "wins", "order", "approval",
})
NEGATIVE_WORDS = frozenset({
    "fall", "falls", "fell", "drop", "drops", "plunge", "plunges", "loss", "losses", "decline", "declines",
    "miss", "misses", "downgrade", "downgrades", "weak", "slump", "bearish", "fraud", "probe", "penalty",
    "default", "lawsuit", "cut", "cuts", "layoffs", "recall", "warning", "debt",
})
_NEGATIONS = frozenset({"not", "no", "never", "without", "fails", "failed"})
_WORD = re.compile(r"[a-z']+")


def score_headline(text: str) -> tuple[float, float, float]:
    """
    Counts positive and negative words; a negation just before a word flips it.
    neutral = 1 / (matches + 1), so a headline with no sentiment words is fully neutral.
    """
    words = _WORD.findall(text.lower())
    positive = negative = 0
    for i, word in enumerate(words):
        flipped = i > 0 and words[i - 1] in _NEGATIONS
        if word in POSITIVE_WORDS:
            negative, positive = (negative + 1, positive) if flipped else (negative, positive + 1)
        elif word in NEGATIVE_WORDS:
            positive, negative = (positive + 1, negative) if flipped else (positive, negative + 1)
    total = positive + negative
    return positive / (total + 1), negative / (total + 1), 1 / (total + 1)
