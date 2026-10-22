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
#   budget metrics  MAE / MAPE / adherence / override stats (Phase 4, at the bottom)


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


# --- Budget Planning Agent (Phase 4) ---
#
#   MAE        mean absolute error, in rupees, between each category's limit and
#              what was actually spent (|limit - actual|, averaged over categories)
#   MAPE       the same error as a share of the actual spending. Undefined when
#              actual = 0, so those categories are SKIPPED and counted separately
#   adherence  share of categories where actual spending stayed at or under the limit
#   overspend  rupees spent ABOVE the limits; unused = rupees of limit left unspent.
#              Reported together with adherence, because adherence alone rewards
#              loose budgets (a huge limit is always "adhered to")
#   overrides  share of limits the user replaced, and how big the change was
#              relative to the agent's number


def _paired(limits: dict[str, float], actuals: dict[str, float]) -> list[tuple[float, float]]:
    """(limit, actual) per category in either dict. A missing limit is 0 (unbudgeted spending)."""
    return [(limits.get(c, 0.0), actuals.get(c, 0.0)) for c in sorted(set(limits) | set(actuals))]


def mae(limits: dict[str, float], actuals: dict[str, float]) -> float:
    pairs = _paired(limits, actuals)
    return sum(abs(limit - actual) for limit, actual in pairs) / len(pairs) if pairs else 0.0


def mape(limits: dict[str, float], actuals: dict[str, float]) -> tuple[float | None, int]:
    """Returns (MAPE as a fraction, number of categories skipped because actual was 0)."""
    pairs = _paired(limits, actuals)
    scored = [abs(limit - actual) / actual for limit, actual in pairs if actual > 0]
    skipped = len(pairs) - len(scored)
    return (sum(scored) / len(scored) if scored else None), skipped


def adherence_rate(limits: dict[str, float], actuals: dict[str, float]) -> float:
    """Share of categories with actual <= limit. Spending with no limit at all is NOT adherent."""
    pairs = _paired(limits, actuals)
    return sum(1 for limit, actual in pairs if actual <= limit) / len(pairs) if pairs else 1.0


def overspend(limits: dict[str, float], actuals: dict[str, float]) -> float:
    """Total rupees spent above the limits (unbudgeted spending counts in full)."""
    return sum(max(0.0, actual - limit) for limit, actual in _paired(limits, actuals))


def unused_limit(limits: dict[str, float], actuals: dict[str, float]) -> float:
    """Total rupees of limit that were not spent (the budget's slack)."""
    return sum(max(0.0, limit - actual) for limit, actual in _paired(limits, actuals))


def override_stats(rows: list[tuple[float, float, str]]) -> tuple[float, float | None]:
    """
    rows = (agent_limit_amount, limit_amount, source) per budget row.
    Returns (override rate, mean relative override size). The size is
    |final - agent| / agent over the overridden rows with an agent limit > 0
    (None when there are none).
    """
    if not rows:
        return 0.0, None
    overridden = [(agent, final) for agent, final, source in rows if source == "user"]
    sized = [abs(final - agent) / agent for agent, final in overridden if agent > 0]
    return len(overridden) / len(rows), (sum(sized) / len(sized) if sized else None)


# --- Financial Forecast Agent (Phase 5) ---
#
# Point errors (MAE, RMSE, MAPE, sMAPE) come from app/services/forecasting/metrics.py,
# the SAME functions the agent's backtest uses. Only evaluation-specific ones live here:
#
#   coverage   share of forecasts whose interval [lower, upper] contained the actual
#              value. An 80% interval should cover about 80%: far less = overconfident,
#              far more = too wide to be useful (so always report it with the width)
#   width      mean (upper - lower), in rupees
#   regret     how much worse the backtest-selected model did than the best FIXED model
#              chosen in hindsight (negative = selection beat every fixed model)
#   confusion  TP/FP/FN/TN, precision, recall and F1 of a yes/no warning (cash crunch)


def interval_coverage(actuals: list[float], lowers: list[float], uppers: list[float]) -> float:
    """Share of actuals inside their [lower, upper] interval (bounds included)."""
    if not actuals or not len(actuals) == len(lowers) == len(uppers):
        raise ValueError("actuals, lowers and uppers must be non-empty and the same length")
    return sum(1 for a, lo, hi in zip(actuals, lowers, uppers) if lo <= a <= hi) / len(actuals)


def mean_interval_width(lowers: list[float], uppers: list[float]) -> float:
    """Mean of upper - lower."""
    if not lowers or len(lowers) != len(uppers):
        raise ValueError("lowers and uppers must be non-empty and the same length")
    return sum(hi - lo for lo, hi in zip(lowers, uppers)) / len(lowers)


def selection_regret(selected_error: float, fixed_errors: dict[str, float]) -> tuple[float, str]:
    """(selected error - best fixed error, name of that best fixed model)."""
    if not fixed_errors:
        raise ValueError("at least one fixed model error is needed")
    best = min(fixed_errors, key=lambda name: (fixed_errors[name], name))
    return selected_error - fixed_errors[best], best


def confusion(truth_flags: list[bool], pred_flags: list[bool]) -> dict[str, float]:
    """Counts plus precision/recall/F1 (precision/recall follow binary_precision_recall's empty-case rule)."""
    precision, recall = binary_precision_recall(truth_flags, pred_flags)
    pairs = list(zip(truth_flags, pred_flags))
    counts = {
        "tp": sum(1 for t, p in pairs if t and p), "fp": sum(1 for t, p in pairs if not t and p),
        "fn": sum(1 for t, p in pairs if t and not p), "tn": sum(1 for t, p in pairs if not t and not p),
    }
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {**{k: float(v) for k, v in counts.items()}, "precision": precision, "recall": recall, "f1": f1}
