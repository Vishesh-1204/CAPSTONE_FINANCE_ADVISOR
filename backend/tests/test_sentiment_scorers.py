# tests/test_sentiment_scorers.py
#
# Purpose: Phase 7A-4 sentiment scorer tests.
#   * Lexicon scorer = the reference function, in the shared probability shape.
#   * FinbertScorer logic with a STUB model (no torch needed): label mapping by name, softmax,
#     batching, lazy one-time loading (also under two threads), failure handling.
#   * The fallback rule: finbert chosen but unavailable -> lexicon + "finbert_unavailable".
#   * The weight rule: importing the app never imports torch/transformers, and they are not
#     in requirements.txt.
#   * ONE real FinBERT test, skipped unless torch + transformers are installed AND the model is
#     already in the local HuggingFace cache (the "ml" profile). It never downloads: the network
#     is blocked and the Hub is put in offline mode.

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import get_args

import pytest

from app.core.config import Settings
from app.schemas.stock import SentimentLabel
from app.sentiment import factory
from app.sentiment import finbert_scorer as fb
from app.sentiment.base import FINBERT_UNAVAILABLE, ScorerUnavailable, SentimentProbabilities, SentimentScorer
from app.sentiment.finbert_scorer import FinbertScorer, LoadedModel, softmax
from app.sentiment.lexicon_scorer import LexiconScorer
from app.services.stocks.lexicon_sentiment import score_headline

pytestmark = pytest.mark.usefixtures("block_network")

BACKEND_DIR = Path(__file__).resolve().parent.parent
HEADLINES = ["Company reports record profit", "Shares plunge after probe", "Board meets on Thursday"]


class StubLoader:
    """Stands in for the transformers loader: a fake model whose logits come from a keyword table."""

    LOGITS = {"profit": [3.0, -1.0, 0.0], "plunge": [-1.0, 3.0, 0.0]}     # in (positive, negative, neutral)

    def __init__(self, labels=("positive", "negative", "neutral"), error: Exception | None = None,
                 delay: float = 0.0):
        self.labels = list(labels)
        self.error = error
        self.delay = delay
        self.loads = 0
        self.batches: list[list[str]] = []

    def __call__(self, model_name: str, revision: str) -> LoadedModel:
        self.loads += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        order = {"positive": 0, "negative": 1, "neutral": 2}

        def predict(batch: list[str]) -> list[list[float]]:
            self.batches.append(batch)
            rows = []
            for text in batch:
                base = next((v for k, v in self.LOGITS.items() if k in text.lower()), [0.0, 0.0, 3.0])
                rows.append([base[order[label]] for label in self.labels])     # logits in the model's order
            return rows

        return LoadedModel(predict, self.labels)


class UnavailableScorer:
    name = "finbert"

    def score(self, texts):
        raise ScorerUnavailable("not installed")


# --- Shared shape ---

def test_probabilities_validate_and_label_like_the_aggregate():
    assert SentimentProbabilities(0.7, 0.1, 0.2).label == "positive"
    assert SentimentProbabilities(0.1, 0.7, 0.2).label == "negative"
    assert SentimentProbabilities(0.2, 0.2, 0.6).label == "neutral"
    assert {SentimentProbabilities(*p).label for p in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]} == set(get_args(SentimentLabel))
    for bad in [(0.5, 0.5, 0.5), (-0.1, 0.6, 0.5), (1.2, 0.0, -0.2)]:
        with pytest.raises(ValueError):
            SentimentProbabilities(*bad)


def test_lexicon_scorer_is_the_reference_function_in_the_shared_shape():
    scorer = LexiconScorer()
    assert isinstance(scorer, SentimentScorer) and scorer.name == "lexicon"
    assert [p.as_tuple() for p in scorer.score(HEADLINES)] == [score_headline(h) for h in HEADLINES]
    assert scorer.score([]) == []


def test_softmax_is_stable_for_large_logits():
    probs = softmax([1000.0, 999.0, -1000.0])
    assert abs(sum(probs) - 1) < 1e-12 and probs[0] > probs[1] > probs[2]


# --- FinbertScorer with a stub model ---

def test_finbert_maps_labels_by_name_not_by_position():
    """A checkpoint whose labels are in another order must give the SAME probabilities."""
    standard = FinbertScorer(loader=StubLoader()).score(HEADLINES)
    shuffled = FinbertScorer(loader=StubLoader(labels=("neutral", "positive", "negative"))).score(HEADLINES)
    flat = lambda results: [x for p in results for x in p.as_tuple()]   # approx cannot compare nested tuples
    assert flat(shuffled) == pytest.approx(flat(standard))
    assert [p.label for p in standard] == ["positive", "negative", "neutral"]


def test_finbert_batches_by_the_batch_size_and_loads_once():
    loader = StubLoader()
    scorer = FinbertScorer(loader=loader, batch_size=2)
    assert not scorer.loaded
    results = scorer.score([f"headline {i}" for i in range(5)])
    assert len(results) == 5 and [len(b) for b in loader.batches] == [2, 2, 1]
    scorer.score(["another one"])
    assert loader.loads == 1 and scorer.loaded and scorer.load_seconds is not None


def test_finbert_does_not_load_for_an_empty_list():
    loader = StubLoader()
    assert FinbertScorer(loader=loader).score([]) == [] and loader.loads == 0


def test_two_first_requests_load_the_model_only_once():
    loader = StubLoader(delay=0.2)
    scorer = FinbertScorer(loader=loader)
    threads = [threading.Thread(target=scorer.score, args=(HEADLINES,)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert loader.loads == 1


def test_a_failed_load_is_remembered_then_retried_after_the_cooldown(monkeypatch):
    loader = StubLoader(error=OSError("offline"))
    scorer = FinbertScorer(loader=loader)
    for _ in range(3):
        with pytest.raises(ScorerUnavailable, match="offline"):
            scorer.score(HEADLINES)
    assert loader.loads == 1                       # not retried on every request
    monkeypatch.setattr(fb, "RETRY_LOAD_AFTER_SECONDS", 0)
    loader.error = None
    assert len(scorer.score(HEADLINES)) == 3 and loader.loads == 2


def test_a_model_without_the_three_labels_is_unavailable():
    with pytest.raises(ScorerUnavailable, match="labels"):
        FinbertScorer(loader=StubLoader(labels=("label_0", "label_1", "label_2"))).score(HEADLINES)


def test_missing_packages_make_finbert_unavailable_without_importing_anything(monkeypatch):
    monkeypatch.setattr(fb, "finbert_installed", lambda: False)
    with pytest.raises(ScorerUnavailable, match="not installed"):
        FinbertScorer().score(HEADLINES)


def test_inference_errors_and_wrong_result_counts_are_unavailable():
    broken = StubLoader()
    scorer = FinbertScorer(loader=broken)
    scorer.score(["warm up"])
    scorer._model = LoadedModel(lambda batch: 1 / 0, broken.labels)
    with pytest.raises(ScorerUnavailable, match="inference failed"):
        scorer.score(HEADLINES)
    scorer._model = LoadedModel(lambda batch: [[0.0, 0.0, 1.0]], broken.labels)
    with pytest.raises(ScorerUnavailable, match="number of results"):
        scorer.score(HEADLINES)


# --- Factory: choice and fallback ---

def test_default_scorer_is_the_lexicon(monkeypatch):
    # The CODE default (the ml container overrides it with SENTIMENT_SCORER=finbert)
    assert Settings.model_fields["SENTIMENT_SCORER"].default == "lexicon"
    monkeypatch.setattr(factory.settings, "SENTIMENT_SCORER", "lexicon")
    result = factory.score_headlines(HEADLINES)
    assert result.scorer == "lexicon" and result.warnings == []
    assert isinstance(factory.get_scorer(), LexiconScorer)


def test_finbert_is_used_when_it_works():
    result = factory.score_headlines(HEADLINES, "finbert", finbert=FinbertScorer(loader=StubLoader()))
    assert result.scorer == "finbert" and result.warnings == []
    assert [p.label for p in result.probabilities] == ["positive", "negative", "neutral"]


def test_unavailable_finbert_falls_back_to_the_lexicon_with_a_warning():
    result = factory.score_headlines(HEADLINES, "finbert", finbert=UnavailableScorer())
    assert result.scorer == "lexicon" and result.warnings == [FINBERT_UNAVAILABLE]
    assert [p.as_tuple() for p in result.probabilities] == [score_headline(h) for h in HEADLINES]


def test_unknown_scorer_name_is_rejected():
    with pytest.raises(ValueError):
        factory.get_scorer("vader")
    with pytest.raises(ValueError):
        factory.score_headlines(HEADLINES, "vader")


# --- The weight rule ---

def test_app_start_does_not_import_torch_or_transformers():
    """A fresh interpreter imports the whole app (and scores with the lexicon); neither heavy package loads."""
    code = (
        "import sys, app.main\n"
        "from app.sentiment.factory import score_headlines, finbert_available\n"
        "score_headlines(['Profit rises'], 'lexicon'); finbert_available()\n"
        "print(sorted(m for m in ('torch', 'transformers') if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, capture_output=True, text=True,
                         timeout=120, env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_ml_packages_live_only_in_requirements_ml():
    default = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
    ml = (BACKEND_DIR / "requirements-ml.txt").read_text(encoding="utf-8").lower()
    for package in ("torch", "transformers"):
        assert not any(line.strip().startswith(package) for line in default.splitlines()), package
        assert any(line.strip().startswith(package) for line in ml.splitlines()), package
    assert "download.pytorch.org/whl/cpu" in ml and "+cpu" in ml        # the CPU-only wheel


# --- The real model (only where it is installed AND cached) ---

def test_real_finbert_scores_clear_headlines(monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")          # never download in a test
    import huggingface_hub.constants
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_OFFLINE", True)
    scorer = FinbertScorer()
    try:
        results = scorer.score(["Company reports record quarterly profit", "Shares plunge after regulator opens probe"])
    except ScorerUnavailable as exc:
        pytest.skip(f"FinBERT not in the local HuggingFace cache: {exc}")
    assert [p.label for p in results] == ["positive", "negative"]
    assert all(abs(sum(p.as_tuple()) - 1) < 1e-6 for p in results)
