# app/sentiment/base.py
#
# Purpose: the contract every headline sentiment scorer follows (Phase 7A-4). The lexicon
# scorer (default, baseline) and FinBERT (optional) both return the SAME shape: positive /
# negative / neutral probabilities that add up to 1 - exactly what the lexicon function in
# services/stocks/lexicon_sentiment.py already returns as a tuple. Code that uses a scorer
# (the stock service, the /sentiment/score route, the evaluation) never knows which one it has.

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from app.services.stocks.sentiment_aggregation import NEGATIVE_BELOW, POSITIVE_ABOVE, PROBABILITY_TOLERANCE

LEXICON = "lexicon"
FINBERT = "finbert"
FINBERT_UNAVAILABLE = "finbert_unavailable"     # warning code when FinBERT was asked for but not used


@dataclass(frozen=True)
class SentimentProbabilities:
    """One headline's probabilities (the lexicon's tuple, with names)."""
    positive: float
    negative: float
    neutral: float

    def __post_init__(self) -> None:
        parts = (self.positive, self.negative, self.neutral)
        if any(p < 0 or p > 1 for p in parts) or abs(sum(parts) - 1) > PROBABILITY_TOLERANCE:
            raise ValueError("positive, negative and neutral must be probabilities that sum to 1")

    @property
    def score(self) -> float:
        """Positive minus negative, from -1 to +1 (same as HeadlineSentiment.score)."""
        return self.positive - self.negative

    @property
    def label(self) -> str:
        """Same thresholds as the aggregate, so one headline and a stock are labelled alike."""
        if self.score > POSITIVE_ABOVE:
            return "positive"
        if self.score < NEGATIVE_BELOW:
            return "negative"
        return "neutral"

    def as_tuple(self) -> tuple[float, float, float]:
        return self.positive, self.negative, self.neutral


class ScorerUnavailable(Exception):
    """The scorer cannot run here (package missing, model download failed). Callers fall back."""


@runtime_checkable
class SentimentScorer(Protocol):
    name: str

    def score(self, texts: Sequence[str]) -> list[SentimentProbabilities]:
        """One result per text, in the same order. May raise ScorerUnavailable."""
        ...
