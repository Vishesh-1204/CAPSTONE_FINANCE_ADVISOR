# backend/evaluation/stocks_sentiment_eval.py
#
# Purpose: RQ1 helpers. Does FinBERT beat the word-list baseline on labelled financial sentences?
# Loads the two pre-registered datasets (evaluation/stocks_rules.py RQ1_DATASETS; downloaded by
# scripts/fetch_sentiment_datasets.py into evaluation/data/, gitignored), makes the pre-registered
# splits, runs the models (majority class, lexicon, FinBERT, TF-IDF + logistic regression) and computes
# accuracy, macro-F1, per-class recall, confusion matrices and paired bootstrap confidence intervals.
#
# Labels: lexicon and FinBERT use the APP rule (SentimentProbabilities.label: score > +0.15 positive,
# < -0.15 negative), because that is what a user of the app sees; FinBERT argmax is a secondary row.
# TF-IDF + LR is a classifier and uses its argmax.
#
# No network here: missing files are reported, never downloaded.

import csv
import hashlib
import io
import random
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.sentiment.base import SentimentProbabilities
from evaluation.stocks_rules import RQ1_LABELS, RQ1_TEST_FRACTION, SEED

DATA_DIR = Path(__file__).parent / "data"
TWITTER_LABELS = {"0": "negative", "1": "positive", "2": "neutral"}     # bearish / bullish / neutral
PHRASEBANK_MEMBER = "FinancialPhraseBank-v1.0/Sentences_AllAgree.txt"

# Used ONLY if no dataset file can be obtained (spec fallback). Written by the method's author,
# clearly labelled "synthetic" in every output row. Not expected to be used (both datasets reachable).
SYNTHETIC_SET: tuple[tuple[str, str], ...] = (
    ("Quarterly profit rose sharply on strong demand", "positive"),
    ("The company reported record revenue for the year", "positive"),
    ("Net sales increased compared with the previous quarter", "positive"),
    ("Operating margin improved to a five-year high", "positive"),
    ("The bank posted higher earnings and lower bad loans", "positive"),
    ("Shares fell after the firm cut its outlook", "negative"),
    ("The company swung to a loss in the third quarter", "negative"),
    ("Revenue declined as orders weakened", "negative"),
    ("The regulator fined the lender over compliance lapses", "negative"),
    ("Profit dropped because raw material costs rose", "negative"),
    ("The board will meet on Friday to consider the accounts", "neutral"),
    ("The company is headquartered in Mumbai", "neutral"),
    ("The results will be announced next week", "neutral"),
    ("The firm operates plants in three states", "neutral"),
    ("Trading in the shares continued as normal", "neutral"),
)


@dataclass(frozen=True)
class Example:
    text: str
    label: str                        # one of RQ1_LABELS


@dataclass
class SentimentSplit:
    """One dataset's train and test sets, ready for every model."""
    key: str
    source: str                       # "real" | "synthetic"
    train: list[Example]
    test: list[Example]
    dropped_duplicates: int = 0
    sha256: dict[str, str] = field(default_factory=dict)


def normalise(text: str) -> str:
    """Lower case, single spaces: how duplicates between train and test are found."""
    return " ".join(text.lower().split())


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --- Loading ---

def parse_twitter_csv(content: bytes) -> list[Example]:
    rows = csv.DictReader(io.StringIO(content.decode("utf-8")))
    examples = []
    for row in rows:
        label = TWITTER_LABELS.get((row.get("label") or "").strip())
        text = " ".join((row.get("text") or "").split())
        if label and text:
            examples.append(Example(text, label))
    return examples


def parse_phrasebank(zip_bytes: bytes, member: str = PHRASEBANK_MEMBER) -> list[Example]:
    """Lines look like 'sentence@label' (latin-1 text inside the original zip)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        text = archive.read(member).decode("latin-1")
    examples = []
    for line in text.splitlines():
        if "@" not in line:
            continue
        sentence, label = line.rsplit("@", 1)
        label, sentence = label.strip(), " ".join(sentence.split())
        if label in RQ1_LABELS and sentence:
            examples.append(Example(sentence, label))
    return examples


def stratified_split(examples: Sequence[Example], test_fraction: float = RQ1_TEST_FRACTION,
                     seed: int = SEED) -> tuple[list[Example], list[Example]]:
    """Seeded split that keeps each label's share in both parts. Sentences are independent, so shuffling is fine."""
    rng = random.Random(seed)
    train, test = [], []
    for label in RQ1_LABELS:
        group = [e for e in examples if e.label == label]
        rng.shuffle(group)
        cut = round(len(group) * test_fraction)
        test.extend(group[:cut])
        train.extend(group[cut:])
    return train, test


def drop_duplicates(train: Sequence[Example], test: Sequence[Example]) -> tuple[list[Example], int]:
    """Pre-registered leakage control: test sentences that also appear in train are removed."""
    seen = {normalise(e.text) for e in train}
    kept = [e for e in test if normalise(e.text) not in seen]
    return kept, len(test) - len(kept)


def load_splits(data_dir: Path = DATA_DIR) -> tuple[list[SentimentSplit], list[str]]:
    """Every pre-registered dataset that is present, plus notes for the ones that are missing."""
    splits, notes = [], []
    train_path, test_path = data_dir / "twitter_fin" / "sent_train.csv", data_dir / "twitter_fin" / "sent_valid.csv"
    if train_path.exists() and test_path.exists():
        train_bytes, test_bytes = train_path.read_bytes(), test_path.read_bytes()
        train, test = parse_twitter_csv(train_bytes), parse_twitter_csv(test_bytes)
        test, dropped = drop_duplicates(train, test)
        splits.append(SentimentSplit("twitter_fin", "real", train, test, dropped,
                                     {"sent_train.csv": _sha(train_bytes), "sent_valid.csv": _sha(test_bytes)}))
    else:
        notes.append("twitter_fin: files missing (run scripts.fetch_sentiment_datasets)")
    zip_path = data_dir / "phrasebank" / "FinancialPhraseBank-v1.0.zip"
    if zip_path.exists():
        content = zip_path.read_bytes()
        train, test = stratified_split(parse_phrasebank(content))
        test, dropped = drop_duplicates(train, test)
        splits.append(SentimentSplit("phrasebank", "real", train, test, dropped,
                                     {"FinancialPhraseBank-v1.0.zip": _sha(content)}))
    else:
        notes.append("phrasebank: file missing (run scripts.fetch_sentiment_datasets)")
    if not splits:
        examples = [Example(t, label) for t, label in SYNTHETIC_SET]
        train, test = stratified_split(examples, 0.4)
        splits.append(SentimentSplit("synthetic", "synthetic", train, test))
        notes.append("NO real dataset available: RQ1 ran on the small SYNTHETIC set (not evidence)")
    return splits, notes


# --- Models ---

Predictor = Callable[[SentimentSplit], list[str]]


def app_labels(probabilities: Sequence[SentimentProbabilities]) -> list[str]:
    return [p.label for p in probabilities]


def argmax_labels(probabilities: Sequence[SentimentProbabilities]) -> list[str]:
    def best(p: SentimentProbabilities) -> str:
        return max((("positive", p.positive), ("negative", p.negative), ("neutral", p.neutral)), key=lambda kv: kv[1])[0]
    return [best(p) for p in probabilities]


def majority_predict(split: SentimentSplit) -> list[str]:
    label = Counter(e.label for e in split.train).most_common(1)[0][0]
    return [label] * len(split.test)


def tfidf_lr_predict(split: SentimentSplit) -> list[str]:
    """Pre-registered settings, trained on the training split only (no tuning on the test set)."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    model = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("lr", LogisticRegression(C=1.0, max_iter=1000)),
    ])
    model.fit([e.text for e in split.train], [e.label for e in split.train])
    return [str(label) for label in model.predict([e.text for e in split.test])]


# --- Metrics ---

def confusion(gold: Sequence[str], predicted: Sequence[str]) -> dict[tuple[str, str], int]:
    """(gold, predicted) -> count, for every pair of labels."""
    counts = Counter(zip(gold, predicted))
    return {(g, p): counts.get((g, p), 0) for g in RQ1_LABELS for p in RQ1_LABELS}


def macro_f1(gold: Sequence[str], predicted: Sequence[str]) -> float:
    """Mean of the per-class F1 scores (a class with no predictions and no gold items scores 0)."""
    scores = []
    for label in RQ1_LABELS:
        tp = sum(1 for g, p in zip(gold, predicted) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, predicted) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, predicted) if g == label and p != label)
        scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores)


def classification_metrics(gold: Sequence[str], predicted: Sequence[str]) -> dict[str, float]:
    if len(gold) != len(predicted) or not gold:
        raise ValueError("gold and predicted must be non-empty and the same length")
    metrics = {"accuracy": sum(g == p for g, p in zip(gold, predicted)) / len(gold),
               "macro_f1": macro_f1(gold, predicted)}
    for label in RQ1_LABELS:
        total = sum(1 for g in gold if g == label)
        hits = sum(1 for g, p in zip(gold, predicted) if g == label and p == label)
        metrics[f"recall_{label}"] = hits / total if total else 0.0
    return metrics


def bootstrap_macro_f1(gold: Sequence[str], predicted: Sequence[str], baseline: Sequence[str] | None = None,
                       resamples: int = 2000, seed: int = SEED) -> tuple[float, float]:
    """
    95% percentile CI of macro-F1 (or, with `baseline`, of macro-F1(predicted) - macro-F1(baseline)
    on the SAME resampled sentences: a paired bootstrap).
    """
    rng = random.Random(seed)
    n = len(gold)
    values = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        g = [gold[i] for i in idx]
        value = macro_f1(g, [predicted[i] for i in idx])
        if baseline is not None:
            value -= macro_f1(g, [baseline[i] for i in idx])
        values.append(value)
    values.sort()
    return values[int(0.025 * (resamples - 1))], values[int(round(0.975 * (resamples - 1)))]
