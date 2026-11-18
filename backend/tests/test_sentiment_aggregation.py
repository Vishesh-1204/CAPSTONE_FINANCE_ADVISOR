# tests/test_sentiment_aggregation.py

import pytest

from app.services.stocks.lexicon_sentiment import score_headline
from app.services.stocks.sentiment_aggregation import HeadlineSentiment, aggregate_sentiment

GOOD = HeadlineSentiment(0.9, 0.05, 0.05, age_days=0)
BAD = HeadlineSentiment(0.05, 0.9, 0.05, age_days=0)


def test_score_is_positive_minus_negative():
    assert GOOD.score == pytest.approx(0.85)


def test_invalid_probabilities_are_rejected():
    with pytest.raises(ValueError):
        HeadlineSentiment(0.5, 0.5, 0.5, 0)
    with pytest.raises(ValueError):
        HeadlineSentiment(0.5, 0.3, 0.2, -1)


def test_no_headlines_is_neutral_and_unreliable():
    result = aggregate_sentiment([])
    assert result.label == "neutral" and not result.reliable and result.n_items == 0


def test_mostly_good_news_is_positive_and_reliable_with_enough_items():
    result = aggregate_sentiment([GOOD, GOOD, GOOD, BAD])
    assert result.label == "positive" and result.reliable


def test_two_headlines_are_not_reliable():
    assert not aggregate_sentiment([GOOD, GOOD]).reliable


def test_recent_news_outweighs_old_news():
    old_bad = HeadlineSentiment(0.05, 0.9, 0.05, age_days=30)
    new_good = HeadlineSentiment(0.9, 0.05, 0.05, age_days=0)
    assert aggregate_sentiment([old_bad, new_good]).score > 0.5


def test_half_life_halves_the_weight():
    items = [HeadlineSentiment(1, 0, 0, 3.0)]
    assert aggregate_sentiment(items, half_life_days=3.0).effective_weight == pytest.approx(0.5)


def test_lexicon_classifies_clear_headlines():
    pos, neg, neu = score_headline("Company profits surge as strong demand beats estimates")
    assert pos > neg and pos + neg + neu == pytest.approx(1.0)
    pos, neg, _ = score_headline("Shares plunge after fraud probe and downgrade")
    assert neg > pos


def test_lexicon_negation_flips_the_word():
    pos, neg, _ = score_headline("Company did not profit this quarter")
    assert neg > pos


def test_lexicon_without_sentiment_words_is_fully_neutral():
    assert score_headline("Board meeting scheduled for Tuesday") == (0.0, 0.0, 1.0)
