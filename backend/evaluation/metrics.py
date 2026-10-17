# evaluation/metrics.py
#
# Purpose: pure metric functions for the paper (no database, no LLM), so they
# are easy to unit-test and to reuse. Written by hand instead of importing
# scikit-learn: the formulas are short, and it avoids a new dependency.
#
#   accuracy        share of ALL rows predicted correctly (no answer = wrong)
#   coverage        share of rows that got any answer
#   macro-F1        F1 averaged over categories, so rare categories count as much
#                   as common ones (accuracy alone hides a category that always fails)
#   override rate   share of agent suggestions the user changed (the feedback metric)


def per_category_scores(truths: list[str], preds: list[str | None]) -> dict[str, dict[str, float]]:
    """Precision, recall, F1 and support for every category present in the ground truth."""
    scores: dict[str, dict[str, float]] = {}
    for category in sorted(set(truths)):
        tp = sum(1 for t, p in zip(truths, preds) if t == category and p == category)
        fp = sum(1 for t, p in zip(truths, preds) if t != category and p == category)
        fn = sum(1 for t, p in zip(truths, preds) if t == category and p != category)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores[category] = {"precision": precision, "recall": recall, "f1": f1, "support": float(truths.count(category))}
    return scores


def classification_metrics(truths: list[str], preds: list[str | None]) -> dict:
    """Headline numbers for one configuration. `None` in preds means 'no answer'."""
    if not truths:
        return {"accuracy": 0.0, "accuracy_when_answered": 0.0, "coverage": 0.0, "macro_f1": 0.0, "per_category": {}}
    answered = [(t, p) for t, p in zip(truths, preds) if p is not None]
    correct = sum(1 for t, p in answered if t == p)
    per_category = per_category_scores(truths, preds)
    return {
        "accuracy": correct / len(truths),
        "accuracy_when_answered": correct / len(answered) if answered else 0.0,
        "coverage": len(answered) / len(truths),
        "macro_f1": sum(c["f1"] for c in per_category.values()) / len(per_category),
        "per_category": per_category,
    }


def binary_precision_recall(truth_flags: list[bool], pred_flags: list[bool]) -> tuple[float, float]:
    """Precision/recall of a yes/no detector (e.g. 'is this payee a person?')."""
    tp = sum(1 for t, p in zip(truth_flags, pred_flags) if t and p)
    fp = sum(1 for t, p in zip(truth_flags, pred_flags) if not t and p)
    fn = sum(1 for t, p in zip(truth_flags, pred_flags) if t and not p)
    precision = tp / (tp + fp) if tp + fp else 1.0      # never flagged anything wrongly
    recall = tp / (tp + fn) if tp + fn else 1.0         # nothing to find
    return precision, recall


def override_rate(pairs: list[tuple[str | None, str | None]]) -> tuple[float, dict[str, float]]:
    """
    pairs = (agent_category, final_category) per row. Rows the agent never
    categorized (agent_category None) are excluded. Returns the overall rate and
    the rate per FINAL category ("how often did users have to fix X").
    """
    judged = [(agent, final) for agent, final in pairs if agent is not None and final is not None]
    if not judged:
        return 0.0, {}
    overall = sum(1 for agent, final in judged if agent != final) / len(judged)
    per_category: dict[str, float] = {}
    for category in sorted({final for _, final in judged}):
        rows = [(a, f) for a, f in judged if f == category]
        per_category[category] = sum(1 for a, f in rows if a != f) / len(rows)
    return overall, per_category
