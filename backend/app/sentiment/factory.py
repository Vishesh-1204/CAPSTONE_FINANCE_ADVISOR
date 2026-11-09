# app/sentiment/factory.py
#
# Purpose: picks the headline scorer from SENTIMENT_SCORER and owns the fallback rule:
# if FinBERT is chosen but cannot run (packages missing, model not downloadable), the lexicon
# scores the headlines instead and the result carries the warning "finbert_unavailable".
# Callers (stock service, POST /api/stocks/sentiment/score, evaluation) use score_headlines()
# and get back WHICH scorer was really used, so the UI and the paper never mislabel results.
#
# One FinbertScorer per process (lru_cache), so the model is loaded once, on first use.

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Sequence

from app.core.config import settings
from app.sentiment.base import FINBERT, FINBERT_UNAVAILABLE, LEXICON, ScorerUnavailable, SentimentProbabilities, SentimentScorer
from app.sentiment.finbert_scorer import FinbertScorer, finbert_installed
from app.sentiment.lexicon_scorer import LexiconScorer


@dataclass(frozen=True)
class ScoringResult:
    probabilities: list[SentimentProbabilities]   # one per text, same order
    scorer: str                                   # the scorer actually used: "lexicon" | "finbert"
    warnings: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _finbert() -> FinbertScorer:
    return FinbertScorer()


def get_scorer(name: str | None = None) -> SentimentScorer:
    """The scorer object for a name (no loading happens here; FinBERT loads on its first score())."""
    name = name or settings.SENTIMENT_SCORER
    if name == LEXICON:
        return LexiconScorer()
    if name == FINBERT:
        return _finbert()
    raise ValueError(f"Unknown SENTIMENT_SCORER '{name}'. Choose one of: {LEXICON}, {FINBERT}")


def finbert_available() -> bool:
    """For GET /api/stocks/status: are the packages installed? (Does not import or load the model.)"""
    return finbert_installed()


def score_headlines(texts: Sequence[str], scorer_name: str | None = None,
                    *, finbert: SentimentScorer | None = None) -> ScoringResult:
    """
    Score headlines with the configured scorer, falling back to the lexicon if FinBERT cannot run.
    `finbert` lets tests pass a stub instead of the real model.
    """
    texts = list(texts)
    name = scorer_name or settings.SENTIMENT_SCORER
    lexicon = LexiconScorer()
    if name == LEXICON:
        return ScoringResult(lexicon.score(texts), LEXICON)
    if name != FINBERT:
        raise ValueError(f"Unknown SENTIMENT_SCORER '{name}'")
    try:
        return ScoringResult((finbert or _finbert()).score(texts), FINBERT)
    except ScorerUnavailable:
        return ScoringResult(lexicon.score(texts), LEXICON, [FINBERT_UNAVAILABLE])
