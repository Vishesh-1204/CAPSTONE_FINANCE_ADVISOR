# app/sentiment/finbert_scorer.py
#
# Purpose: OPTIONAL headline scorer using FinBERT (ProsusAI/finbert, a BERT model fine-tuned on
# financial news sentences) through HuggingFace transformers, on the CPU.
#
# Heavy on purpose, so it is kept out of the way:
#   * torch and transformers are NOT in requirements.txt; they come from requirements-ml.txt
#     (the "ml" Docker Compose profile).
#   * Nothing is imported at module level: the packages are imported, and the model is
#     downloaded/loaded, on the FIRST score() call only (lazy). The app starts without them.
#   * If they are missing or the model cannot load, score() raises ScorerUnavailable and the
#     caller (sentiment/factory.py) falls back to the lexicon with "finbert_unavailable".
#
# The model's labels are mapped BY NAME from its own config (id2label), never by position:
# FinBERT checkpoints do not all use the same label order, and a silent mix-up would swap
# positive and negative. The softmax is plain Python so it is testable without torch.

import importlib.util
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, NoReturn, Sequence

from app.core.config import settings
from app.sentiment.base import FINBERT, ScorerUnavailable, SentimentProbabilities

logger = logging.getLogger(__name__)

MAX_TOKENS = 128                  # headlines are short; longer text is truncated
RETRY_LOAD_AFTER_SECONDS = 600    # after a failed load (e.g. offline), try again at most every 10 minutes
REQUIRED_LABELS = {"positive", "negative", "neutral"}


@dataclass(frozen=True)
class LoadedModel:
    """What the loader returns: a batch -> raw logits function, and the label name of each logit."""
    predict_logits: Callable[[list[str]], list[list[float]]]
    labels: list[str]             # lower-case, index i = logit i


Loader = Callable[[str, str], LoadedModel]


def finbert_installed() -> bool:
    """True when torch and transformers can be imported. Checks WITHOUT importing them (fast)."""
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers"))


def load_transformers_model(model_name: str, revision: str) -> LoadedModel:
    """The real loader: downloads (first time) and loads the model on the CPU. Imports happen HERE."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
    # safetensors only: pytorch_model.bin is a Python pickle, which can run code when loaded.
    # (ProsusAI/finbert ships model.safetensors, so only that ~438 MB file is downloaded.)
    model = AutoModelForSequenceClassification.from_pretrained(model_name, revision=revision, use_safetensors=True)
    model.to("cpu")
    model.eval()                                   # inference mode: no dropout
    labels = [str(model.config.id2label[i]).lower() for i in range(model.config.num_labels)]

    def predict_logits(batch: list[str]) -> list[list[float]]:
        encoded = tokenizer(batch, padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors="pt")
        with torch.inference_mode():
            return model(**encoded).logits.tolist()

    return LoadedModel(predict_logits, labels)


def softmax(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax (subtract the max before exp)."""
    top = max(logits)
    exps = [math.exp(x - top) for x in logits]
    total = sum(exps)
    return [e / total for e in exps]


class FinbertScorer:
    """SentimentScorer backed by FinBERT. One instance per process keeps the model in memory."""

    name = FINBERT

    def __init__(self, model_name: str | None = None, revision: str | None = None,
                 batch_size: int | None = None, loader: Loader | None = None) -> None:
        self.model_name = model_name or settings.FINBERT_MODEL_NAME
        self.revision = revision or settings.FINBERT_MODEL_REVISION
        self.batch_size = batch_size or settings.FINBERT_BATCH_SIZE
        if self.batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._loader = loader
        self._model: LoadedModel | None = None
        self._lock = threading.Lock()              # two first requests must not load the model twice
        self._load_error: str | None = None
        self._failed_at: float | None = None
        self.load_seconds: float | None = None     # how long the first load took (reported in the docs)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_loaded(self) -> LoadedModel:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_LOAD_AFTER_SECONDS:
                raise ScorerUnavailable(self._load_error or "FinBERT failed to load")
            loader = self._loader
            if loader is None:
                if not finbert_installed():
                    self._fail("torch/transformers are not installed (use the 'ml' Docker Compose profile)")
                loader = load_transformers_model
            start = time.perf_counter()
            try:
                model = loader(self.model_name, self.revision)
            except Exception as exc:  # noqa: BLE001 - download, disk or format errors all mean "unavailable"
                self._fail(f"could not load {self.model_name}: {type(exc).__name__}: {exc}")
            if not REQUIRED_LABELS <= set(model.labels):
                self._fail(f"{self.model_name} labels {model.labels} are not positive/negative/neutral")
            self.load_seconds = time.perf_counter() - start
            self._model, self._load_error, self._failed_at = model, None, None
            logger.info("FinBERT %s loaded in %.1fs", self.model_name, self.load_seconds)
            return model

    def _fail(self, message: str) -> NoReturn:
        """Remember the failure (so we do not retry on every request) and raise ScorerUnavailable."""
        self._load_error, self._failed_at = message, time.monotonic()
        logger.warning("FinBERT unavailable: %s", message)
        raise ScorerUnavailable(message)

    def score(self, texts: Sequence[str]) -> list[SentimentProbabilities]:
        texts = list(texts)
        if not texts:
            return []
        model = self._ensure_loaded()
        index = {label: i for i, label in enumerate(model.labels)}
        results: list[SentimentProbabilities] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                rows = model.predict_logits(batch)
            except Exception as exc:  # noqa: BLE001
                raise ScorerUnavailable(f"FinBERT inference failed: {type(exc).__name__}: {exc}") from exc
            if len(rows) != len(batch):
                raise ScorerUnavailable("FinBERT returned a different number of results than inputs")
            for row in rows:
                probs = softmax(row)
                results.append(SentimentProbabilities(
                    probs[index["positive"]], probs[index["negative"]], probs[index["neutral"]]))
        return results
