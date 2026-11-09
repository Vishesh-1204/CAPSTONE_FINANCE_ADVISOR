# app/sentiment/lexicon_scorer.py
#
# Purpose: the DEFAULT headline scorer. A thin wrapper that gives the transparent word-list
# function in services/stocks/lexicon_sentiment.py the SentimentScorer interface. It needs no
# extra packages, never fails to load, and is the baseline FinBERT is compared against.

from typing import Sequence

from app.sentiment.base import LEXICON, SentimentProbabilities
from app.services.stocks.lexicon_sentiment import score_headline


class LexiconScorer:
    name = LEXICON

    def score(self, texts: Sequence[str]) -> list[SentimentProbabilities]:
        return [SentimentProbabilities(*score_headline(text)) for text in texts]
