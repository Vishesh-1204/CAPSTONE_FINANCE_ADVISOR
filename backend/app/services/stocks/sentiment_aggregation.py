# app/services/stocks/sentiment_aggregation.py
#
# Purpose: turn many per-headline sentiment results (from FinBERT or the fallback lexicon scorer)
# into ONE explainable score for a stock. Newer headlines count more (exponential half-life),
# and the result says whether it is RELIABLE (enough headlines) so a single headline never
# drives the story. Pure Python: the model itself lives elsewhere.

from dataclasses import dataclass

POSITIVE_ABOVE = 0.15
NEGATIVE_BELOW = -0.15
PROBABILITY_TOLERANCE = 0.01


@dataclass(frozen=True)
class HeadlineSentiment:
    positive: float
    negative: float
    neutral: float
    age_days: float          # how old the headline is

    def __post_init__(self) -> None:
        parts = (self.positive, self.negative, self.neutral)
        if any(p < 0 or p > 1 for p in parts) or abs(sum(parts) - 1) > PROBABILITY_TOLERANCE:
            raise ValueError("positive, negative and neutral must be probabilities that sum to 1")
        if self.age_days < 0:
            raise ValueError("age_days cannot be negative")

    @property
    def score(self) -> float:
        """Positive minus negative probability, from -1 (all bad news) to +1 (all good news)."""
        return self.positive - self.negative


@dataclass(frozen=True)
class AggregateSentiment:
    score: float             # weighted average, -1 to +1
    label: str               # 'positive' | 'neutral' | 'negative'
    n_items: int
    reliable: bool           # False when there are too few headlines to say anything
    effective_weight: float  # sum of recency weights, a measure of how much evidence there is


def aggregate_sentiment(items: list[HeadlineSentiment], half_life_days: float = 3.0, min_items: int = 3) -> AggregateSentiment:
    """A headline half_life_days old counts half as much as one from today."""
    if half_life_days <= 0 or min_items < 1:
        raise ValueError("half_life_days must be > 0 and min_items >= 1")
    if not items:
        return AggregateSentiment(0.0, "neutral", 0, False, 0.0)
    weights = [0.5 ** (item.age_days / half_life_days) for item in items]
    total = sum(weights)
    score = sum(w * item.score for w, item in zip(weights, items)) / total
    label = "positive" if score > POSITIVE_ABOVE else "negative" if score < NEGATIVE_BELOW else "neutral"
    return AggregateSentiment(round(score, 4), label, len(items), len(items) >= min_items, round(total, 4))
