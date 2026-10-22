# backend/evaluation/evaluate_budget.py
#
# Purpose: MEASURE the Budget Planning Agent for the paper. Like
# evaluate_categorizer.py, it runs the REAL services (suggest_income,
# generate_budget, budget_status, override_budget) on a throwaway in-memory
# SQLite database loaded with the synthetic history from
# scripts/make_synthetic_history.py (3 personas, April-September 2026).
#
# Protocol (time-based split, never random):
#   train   April-June 2026 (months 1-3): the plan's history
#   plan    July 2026: the target month of the plan
#   test    the July plan is scored against the ACTUAL spending of July,
#           August and September (months 4-6), per month and as a mean
#   income  each persona's profile income = suggest_income() over the training
#           window with the default 3-month lookback (P5, used end to end), and
#           it is kept fixed across ablations so only the planner changes
#
# Methods:
#   B0  fixed-weights baseline (build_baseline_plan)
#   B1  history-trim rules engine, template explanations (generate_budget)
#   B2  B1 + ONE LLM call to explain (only with --llm real). Same numbers as B1,
#       so it isolates the explanation: faithfulness, guard replacements, calls/plan
#
# Ablations on B1: trim 0/5/10/20%; lookback 1/2/3 months (6 would read the test
# months, so it is left out); investments + self-transfers counted as spending
# (budget distortion); simulated user overrides (an oracle user replaces a limit
# that July overshot by >20% with July's actual).
#
# Caveat for the paper: synthetic actuals do not react to the budget, so
# adherence measures how REALISTIC the limits are, not whether a person complies.
#
# Run (inside Docker):
#   docker compose exec backend python -m evaluation.evaluate_budget
#   docker compose exec backend python -m evaluation.evaluate_budget --llm real   (synthetic data only)
#
# Output (long format: llm_mode, method, config, persona, eval_month, category, metric, value):
#   --llm none  evaluation/budget_metrics.csv + budget_metrics.md (committed; deterministic)
#   --llm real  evaluation/results/budget_metrics_<timestamp>.csv + .md (gitignored)

import argparse
import csv
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table)
from app.agents.budget_planner import BudgetPlannerAgent, PlanExplanation
from app.core.categories import CATEGORY_NAMES, INVESTMENTS_CATEGORY
from app.core.config import settings
from app.core.money import to_money
from app.core.month_utils import lookback_window, month_key
from app.core.transaction_types import DEBIT
from app.db.base_class import Base
from app.models.budget import Budget
from app.models.transaction import Transaction
from app.models.user import User
from app.services import budget_service as svc
from app.services.budget_calculator import build_baseline_plan, build_history_plan
from evaluation.evaluate_categorizer import overridden_settings
from evaluation.metrics import adherence_rate, mae, mape, override_stats, overspend, unused_limit
from scripts.make_synthetic_history import (
    DEFAULT_OUT as DEFAULT_DATA, HistoryRow, generate_history, persona_by_name, read_csv,
)

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
TARGET = "2026-07"                                   # month 4: the plan's month
TEST_MONTHS: tuple[str, ...] = ("2026-07", "2026-08", "2026-09")
MEAN = "mean"                                         # eval_month label for the 3-month average
PLAN = "plan"                                         # eval_month label for plan-level facts
OVERRIDE_TOLERANCE = 1.20                             # oracle overrides when actual > 120% of the limit

# What we score: real expense categories. Savings are not a spending limit.
SCORED_CATEGORIES = frozenset(CATEGORY_NAMES) - {INVESTMENTS_CATEGORY}
# In the naive ablation, investments become an ordinary (discretionary) budget line
NAIVE_INVESTMENTS_LABEL = "Investments (counted as spending)"
FIELDS = ("llm_mode", "method", "config", "persona", "eval_month", "category", "metric", "value")


@dataclass(frozen=True)
class Config:
    """One row of the experiment grid."""
    method: str                                       # B0 | B1 | B2
    name: str
    settings: dict[str, object] = field(default_factory=dict)
    kind: str = "service"                             # service | baseline | naive


CONFIGS: tuple[Config, ...] = (
    Config("B0", "baseline", kind="baseline"),
    Config("B1", "default"),
    *(Config("B1", f"trim={t:.2f}", {"BUDGET_DISCRETIONARY_TRIM": t}) for t in (0.0, 0.05, 0.10, 0.20)),
    *(Config("B1", f"lookback={n}", {"BUDGET_LOOKBACK_MONTHS": n}) for n in (1, 2, 3)),
    Config("B1", "investments_as_spending", kind="naive"),
)
LLM_CONFIG = Config("B2", "llm")
DETAILED = {("B0", "baseline"), ("B1", "default"), ("B2", "llm")}   # configs that also get per-category rows


@dataclass
class PlanResult:
    """What one persona's July plan looks like, whatever method built it."""
    limits: dict[str, float]
    spendable: float
    feasible: bool
    extra: dict[str, float] = field(default_factory=dict)   # plan-level metrics (llm, distortion, ...)


class RecordingPlanner:
    """Wraps the planner agent so the evaluation can read the explanation the service discarded."""

    def __init__(self, inner: BudgetPlannerAgent) -> None:
        self.inner = inner
        self.last: PlanExplanation | None = None

    def explain(self, ctx, facts) -> PlanExplanation:
        self.last = self.inner.explain(ctx, facts)
        return self.last


@contextmanager
def planner(factory: Callable[[], object]) -> Iterator[None]:
    """Temporarily makes generate_budget use `factory` to build its planner."""
    original = svc._build_planner
    svc._build_planner = factory
    try:
        yield
    finally:
        svc._build_planner = original


# ------------------------------------------------------------------ database


def _new_db(rows: list[HistoryRow]) -> tuple[Session, dict[str, int]]:
    """A fresh in-memory DB with one user per persona and their history. Returns (session, persona -> user id)."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    users: dict[str, int] = {}
    for persona in sorted({r.persona for r in rows}):
        user = User(email=f"{persona}@synthetic.example", full_name=f"Synthetic {persona}", hashed_password="x")
        db.add(user)
        db.flush()
        users[persona] = user.id
    db.add_all(
        Transaction(
            user_id=users[r.persona], description=r.description, amount=r.amount, direction=r.direction,
            txn_type=r.txn_type, category=r.category,
            transaction_date=datetime(r.day.year, r.day.month, r.day.day, 12, tzinfo=timezone.utc),
        )
        for r in rows
    )
    db.commit()
    return db, users


def _set_profile(db: Session, user_id: int, persona: str) -> Decimal:
    """Income from the P5 suggestion (default lookback), savings target from the persona."""
    suggestion = svc.suggest_income(db, user_id, TARGET)
    if suggestion["suggested_monthly_income"] is None:
        raise ValueError(f"persona {persona!r} has no income in the training window")
    income = to_money(suggestion["suggested_monthly_income"])
    svc.upsert_profile(db, user_id, income, persona_by_name(persona).savings_target_pct)
    return income


def actual_spending(db: Session, user_id: int, month: str) -> dict[str, float]:
    """Actual expenses per category in `month`, read through the real status endpoint logic."""
    items = svc.budget_status(db, user_id, month)["items"]
    return {item["category"]: item["spent"] for item in items if item["category"] in SCORED_CATEGORIES}


# ------------------------------------------------------------------ plans


def _service_plan(db: Session, user_id: int, factory: Callable[[], BudgetPlannerAgent]) -> PlanResult:
    """B1/B2: the real generate_budget, with the explanation recorded."""
    recorder = RecordingPlanner(factory())
    with planner(lambda: recorder):
        result = svc.generate_budget(db, user_id, TARGET)
    explanation = recorder.last
    texts = 1 + len(explanation.reasons)          # summary + one reason per category
    extra = {
        "months_used": result["data_quality"]["months_used"],
        "llm_used": float(explanation.llm_used),
        "llm_calls_per_plan": float(recorder.inner.llm_calls),
        "texts_total": float(texts),
        "texts_replaced_by_guard": float(explanation.replaced_texts),
        # Guard OFF: these texts (with an invented amount) would have reached the user
        "unfaithful_text_rate": explanation.replaced_texts / texts,
    }
    if explanation.faithful is not None:
        extra["explanation_faithful"] = float(explanation.faithful)
    limits = {b["category"]: b["limit_amount"] for b in result["budgets"]}
    return PlanResult(limits, result["spendable_income"], result["feasible"], extra)


def _naive_averages(db: Session, user_id: int) -> dict[str, Decimal]:
    """
    What a naive planner would average: EVERY categorized debit (SIPs, broker
    top-ups and self-transfers included), divided like _history_averages.
    """
    start, end = lookback_window(TARGET, settings.BUDGET_LOOKBACK_MONTHS)
    rows = db.query(Transaction.category, Transaction.amount, Transaction.transaction_date).filter(
        Transaction.user_id == user_id, Transaction.direction == DEBIT,
        Transaction.transaction_date >= start, Transaction.transaction_date < end,
        Transaction.category.in_(CATEGORY_NAMES),
    )
    totals: dict[str, Decimal] = {}
    months: set[str] = set()
    for category, amount, moment in rows:
        label = NAIVE_INVESTMENTS_LABEL if category == INVESTMENTS_CATEGORY else category
        totals[label] = totals.get(label, Decimal("0")) + to_money(amount)
        months.add(month_key(moment))
    return {c: t / len(months) for c, t in totals.items()} if months else {}


def _naive_plan(db: Session, user_id: int, income: Decimal, persona: str) -> PlanResult:
    """Ablation: investments and self-transfers counted as spending."""
    pct = persona_by_name(persona).savings_target_pct
    # Evaluation-only use of the service's private helper: the CORRECT history, for comparison
    correct, _ = svc._history_averages(db, user_id, TARGET, settings.BUDGET_LOOKBACK_MONTHS)
    naive = _naive_averages(db, user_id)
    plan = build_history_plan(income, pct, naive, Decimal(str(settings.BUDGET_DISCRETIONARY_TRIM)))
    true_total, naive_total = sum(correct.values()), sum(naive.values())
    extra = {
        "history_spend_correct": float(to_money(true_total)),
        "history_spend_naive": float(to_money(naive_total)),
        "budget_distortion_pct": float((naive_total - true_total) / true_total * 100) if true_total else 0.0,
        "limit_on_non_spending": float(plan.limits.get(NAIVE_INVESTMENTS_LABEL, 0)),
    }
    return PlanResult({c: float(v) for c, v in plan.limits.items()}, float(plan.spendable), plan.feasible, extra)


def _baseline_plan(income: Decimal, persona: str) -> PlanResult:
    plan = build_baseline_plan(income, persona_by_name(persona).savings_target_pct)
    return PlanResult({c: float(v) for c, v in plan.limits.items()}, float(plan.spendable), plan.feasible)


# ------------------------------------------------------------------ scoring


def _score(plan: PlanResult, actuals: dict[str, dict[str, float]], detailed: bool) -> list[tuple[str, str, str, float]]:
    """(eval_month, category, metric, value) rows for one persona's plan."""
    limits = {c: v for c, v in plan.limits.items() if c in SCORED_CATEGORIES}
    out: list[tuple[str, str, str, float]] = [
        (PLAN, "", "spendable", plan.spendable),
        (PLAN, "", "feasible", float(plan.feasible)),
        (PLAN, "", "total_limit", sum(limits.values())),
        (PLAN, "", "categories_budgeted", float(len(limits))),
        *((PLAN, "", metric, value) for metric, value in plan.extra.items()),
    ]
    per_month: dict[str, list[float]] = {"mae": [], "mape": [], "adherence_rate": [], "overspend": [], "unused_limit": []}
    for month in TEST_MONTHS:
        actual = actuals[month]
        month_mae, (month_mape, skipped), adherence = mae(limits, actual), mape(limits, actual), adherence_rate(limits, actual)
        per_month["mae"].append(month_mae)
        per_month["adherence_rate"].append(adherence)
        per_month["overspend"].append(overspend(limits, actual))
        per_month["unused_limit"].append(unused_limit(limits, actual))
        if month_mape is not None:
            per_month["mape"].append(month_mape)
        out += [
            (month, "", "mae", month_mae),
            (month, "", "mape", month_mape if month_mape is not None else float("nan")),
            (month, "", "mape_skipped", float(skipped)),
            (month, "", "adherence_rate", adherence),
            (month, "", "overspend", per_month["overspend"][-1]),
            (month, "", "unused_limit", per_month["unused_limit"][-1]),
            (month, "", "total_actual", sum(actual.values())),
            (month, "", "within_spendable", float(sum(actual.values()) <= plan.spendable)),
        ]
    out += [(MEAN, "", metric, mean(values)) for metric, values in per_month.items() if values]
    if detailed:   # per-category MAE over the 3 test months, plus the limit itself
        for category in sorted(set(limits) | {c for a in actuals.values() for c in a}):
            limit = limits.get(category, 0.0)
            errors = [abs(limit - actuals[m].get(category, 0.0)) for m in TEST_MONTHS]
            out += [(MEAN, category, "mae", mean(errors)), (PLAN, category, "limit", limit)]
    return out


def _simulate_overrides(db: Session, user_id: int, actual_july: dict[str, float]) -> tuple[float, float | None]:
    """An oracle user overrides each July limit that was overshot by >20%, setting it to July's actual."""
    for budget in svc.list_budgets(db, user_id, TARGET):
        spent = actual_july.get(budget.category, 0.0)
        if spent > float(budget.limit_amount) * OVERRIDE_TOLERANCE:
            svc.override_budget(db, user_id, budget.id, to_money(spent))
    rows = db.query(Budget).filter(Budget.user_id == user_id, Budget.month == TARGET).all()
    return override_stats([(float(b.agent_limit_amount), float(b.limit_amount), b.source) for b in rows])


# ------------------------------------------------------------------ run


def run_config(config: Config, rows: list[HistoryRow], llm_factory: Callable[[], BudgetPlannerAgent]) -> list[dict]:
    """Every persona's metrics for one configuration (fresh database each time)."""
    db, users = _new_db(rows)
    records: list[dict] = []
    try:
        for persona, user_id in users.items():
            income = _set_profile(db, user_id, persona)          # default settings: same income in every config
            actuals = {m: actual_spending(db, user_id, m) for m in TEST_MONTHS}
            with overridden_settings(config.settings):
                if config.kind == "baseline":
                    plan = _baseline_plan(income, persona)
                elif config.kind == "naive":
                    plan = _naive_plan(db, user_id, income, persona)
                else:
                    plan = _service_plan(db, user_id, llm_factory)
            for eval_month, category, metric, value in _score(plan, actuals, (config.method, config.name) in DETAILED):
                records.append({"method": config.method, "config": config.name, "persona": persona,
                                 "eval_month": eval_month, "category": category, "metric": metric, "value": value})
            if (config.method, config.name) == ("B1", "default"):
                rate, size = _simulate_overrides(db, user_id, actuals[TARGET])
                for metric, value in (("override_rate", rate), ("mean_relative_override_size", size)):
                    records.append({"method": "B1", "config": "simulated_overrides", "persona": persona,
                                    "eval_month": TARGET, "category": "", "metric": metric,
                                    "value": float("nan") if value is None else value})
    finally:
        db.close()
    return records


def run_evaluation(
    rows: list[HistoryRow],
    llm_mode: str = "none",
    llm_factory: Callable[[], BudgetPlannerAgent] | None = None,
    out_csv: Path | None = None,
    out_md: Path | None = None,
) -> list[dict]:
    """
    Runs every configuration and writes the CSV (+ markdown). B2 runs when
    llm_mode='real' (the configured provider) or when `llm_factory` is given
    (tests inject a fake LLM). Returns the records.
    """
    template_only = lambda: BudgetPlannerAgent(chains=[])        # noqa: E731  (B1: no LLM call)
    records: list[dict] = []
    for config in CONFIGS:
        records += run_config(config, rows, template_only)
    if llm_mode == "real" or llm_factory is not None:
        records += run_config(LLM_CONFIG, rows, llm_factory or BudgetPlannerAgent.from_settings)
    for record in records:
        record["llm_mode"] = llm_mode

    if out_csv is not None:
        write_csv(out_csv, records)
    if out_md is not None:
        out_md.write_text(markdown_report(records), encoding="utf-8", newline="\n")
    return records


def _fmt(value: float) -> str:
    """Stable text for the CSV: 4 decimals, blank for 'not defined'."""
    return "" if value != value else f"{value:.4f}"      # NaN != NaN


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({**record, "value": _fmt(record["value"])})


# ------------------------------------------------------------------ markdown for the paper


def _get(records: list[dict], method: str, config: str, persona: str, month: str, metric: str) -> float:
    for r in records:
        if (r["method"], r["config"], r["persona"], r["eval_month"], r["metric"], r["category"]) == (
            method, config, persona, month, metric, ""
        ):
            return r["value"]
    return float("nan")


def _rupees(value: float) -> str:
    return "n/a" if value != value else f"₹{value:,.0f}"


def _pct(value: float) -> str:
    return "n/a" if value != value else f"{value * 100:.1f}%"


def markdown_report(records: list[dict]) -> str:
    """Short tables for the paper, built only from `records` (so they always match the CSV)."""
    personas = sorted({r["persona"] for r in records})
    configs = list(dict.fromkeys((r["method"], r["config"]) for r in records if r["config"] != "simulated_overrides"))
    lines = [
        "# Budget Planning Agent: evaluation results",
        "",
        f"Synthetic data, {len(personas)} personas. Plan for {TARGET} built from the 3 months before it; "
        f"scored against actual spending in {', '.join(TEST_MONTHS)}. MAE, MAPE and adherence are means over "
        "those 3 months. LLM mode: " + records[0]["llm_mode"] + ".",
        "",
        "## Main comparison (per persona)",
        "",
        "| Persona | Method | Feasible | Spendable | MAE | MAPE | Adherence | Overspend | Unused limit |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for persona in personas:
        for method, config in (("B0", "baseline"), ("B1", "default"), ("B2", "llm")):
            if (method, config) not in configs:
                continue
            feasible = _get(records, method, config, persona, PLAN, "feasible")
            lines.append(
                f"| {persona} | {method} {config} | {'yes' if feasible == 1 else 'no'} | "
                f"{_rupees(_get(records, method, config, persona, PLAN, 'spendable'))} | "
                f"{_rupees(_get(records, method, config, persona, MEAN, 'mae'))} | "
                f"{_pct(_get(records, method, config, persona, MEAN, 'mape'))} | "
                f"{_pct(_get(records, method, config, persona, MEAN, 'adherence_rate'))} | "
                f"{_rupees(_get(records, method, config, persona, MEAN, 'overspend'))} | "
                f"{_rupees(_get(records, method, config, persona, MEAN, 'unused_limit'))} |"
            )

    lines += ["", "## Ablations (mean over personas)", "",
              "| Configuration | MAE | MAPE | Adherence | Overspend | Unused limit | Feasibility rate |",
              "|---|---|---|---|---|---|---|"]
    for method, config in configs:
        def avg(month: str, metric: str) -> float:
            values = [_get(records, method, config, p, month, metric) for p in personas]
            values = [v for v in values if v == v]
            return mean(values) if values else float("nan")
        lines.append(f"| {method} {config} | {_rupees(avg(MEAN, 'mae'))} | {_pct(avg(MEAN, 'mape'))} | "
                     f"{_pct(avg(MEAN, 'adherence_rate'))} | {_rupees(avg(MEAN, 'overspend'))} | "
                     f"{_rupees(avg(MEAN, 'unused_limit'))} | {_pct(avg(PLAN, 'feasible'))} |")

    lines += ["", "## Investments counted as spending (budget distortion)", "",
              "| Persona | History spend (correct) | History spend (naive) | Distortion | Limit given to non-spending "
              "| Adherence correct | Adherence naive |", "|---|---|---|---|---|---|---|"]
    for persona in personas:
        get = lambda month, metric, config="investments_as_spending": _get(records, "B1", config, persona, month, metric)  # noqa: E731
        lines.append(
            f"| {persona} | {_rupees(get(PLAN, 'history_spend_correct'))} | {_rupees(get(PLAN, 'history_spend_naive'))} "
            f"| {get(PLAN, 'budget_distortion_pct'):.1f}% | {_rupees(get(PLAN, 'limit_on_non_spending'))} "
            f"| {_pct(get(MEAN, 'adherence_rate', 'default'))} | {_pct(get(MEAN, 'adherence_rate'))} |"
        )

    lines += ["", "## Simulated overrides (oracle user, July)", "",
              "| Persona | Override rate | Mean relative override size |", "|---|---|---|"]
    for persona in personas:
        lines.append(f"| {persona} | {_pct(_get(records, 'B1', 'simulated_overrides', persona, TARGET, 'override_rate'))} "
                     f"| {_pct(_get(records, 'B1', 'simulated_overrides', persona, TARGET, 'mean_relative_override_size'))} |")

    if ("B2", "llm") in configs:
        lines += ["", "## LLM explanations (B2)", "",
                  "| Persona | LLM used | Faithful | Texts replaced by guard | Unfaithful text rate (guard off) "
                  "| LLM calls per plan |", "|---|---|---|---|---|---|"]
        for persona in personas:
            get = lambda metric: _get(records, "B2", "llm", persona, PLAN, metric)  # noqa: E731
            faithful = get("explanation_faithful")
            lines.append(
                f"| {persona} | {'yes' if get('llm_used') == 1 else 'no'} | "
                f"{'n/a' if faithful != faithful else ('yes' if faithful == 1 else 'no')} | "
                f"{get('texts_replaced_by_guard'):.0f} / {get('texts_total'):.0f} | "
                f"{_pct(get('unfaithful_text_rate'))} | {get('llm_calls_per_plan'):.0f} |"
            )

    lines += ["", "Caveat: synthetic actuals do not react to the budget, so adherence measures how realistic "
              "the limits are, not whether a person would follow them. Adherence alone rewards loose budgets "
              "(B0 gives the frugal persona far more than it spends), so read it next to overspend and unused "
              "limit. MAE also includes the INTENDED discretionary trim, so compare methods, not absolute values.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Budget Planning Agent on synthetic history.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA,
                        help="history CSV from scripts.make_synthetic_history (generated if missing)")
    parser.add_argument("--llm", choices=("none", "real"), default="none",
                        help="'real' adds B2 with the configured provider (SYNTHETIC data only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    rows = read_csv(args.data) if args.data.exists() else generate_history()
    if args.llm == "none":
        out_csv, out_md = EVAL_DIR / "budget_metrics.csv", EVAL_DIR / "budget_metrics.md"
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv, out_md = RESULTS_DIR / f"budget_metrics_{stamp}.csv", RESULTS_DIR / f"budget_metrics_{stamp}.md"

    records = run_evaluation(rows, llm_mode=args.llm, out_csv=out_csv, out_md=out_md)
    print(markdown_report(records))
    print(f"Wrote {len(records)} metric rows to {out_csv} and the tables to {out_md}")


if __name__ == "__main__":
    main()
