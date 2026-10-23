# backend/evaluation/evaluate_investing.py
#
# Purpose: MEASURE the Investment Advisor Agent for the paper (Phase 6B).
# The suitability rules (evaluation/investing_rules.py) were committed BEFORE the
# methods (evaluation/investing_methods.py) were written: pre-registration.
#
# Experiments:
#   1. personas    500 seeded synthetic personas (investing_personas.py) x every method
#                  (B0, B1, B2, P and two mixed variants) and every P ablation:
#                  rule violations (allocation and SIP rules reported separately),
#                  shortfall risk (p10 corpus below the money invested), mean goal
#                  probability, SIP size. Seeded Monte Carlo with COMMON random numbers:
#                  every method sees the same return paths for a given persona.
#   2. sensitivity plans built under the BASE assumptions, outcomes simulated under
#                  low / base / high returns ("what if the assumptions are wrong?")
#   3. affordability the Phase 5 synthetic panel (30 users, 15 origins) is REPLAYED
#                  through the real generate_forecast: a SIP sized from each surplus
#                  rule is compared with the ACTUAL surplus before investing in the
#                  next 30 days. Links Phase 5 uncertainty to Phase 6 outcomes.
#   4. claims guard  precision / recall on investing_guard_cases.csv (hand-labelled;
#                  written by the method's author: no independent labelling)
#   5. pipeline    30 personas through the REAL generate_plan (SQLite): P here must
#                  equal the service, and the template must pass every guard.
#                  --llm real: the same 30 through the configured LLM (synthetic data).
#   6. variability B0, B2 and P on personas whose spending variability is drawn from a low and
#                  a high range (the primary 5-30% range is unchanged), with the Phase 5 panel's
#                  measured variability as a reference (the panel is synthetic too).
#   7. goal gap    how much more than P's SIP the goal needs at the expected return (about 50%).
#
#   --stratified   a SEPARATE run on ~100 personas that oversample rare corners (R7's strong
#                  households, fragile households, cautious people, short horizons). Never mixed
#                  into the main tables.
#
# Run (inside Docker):
#   docker compose exec backend python -m evaluation.evaluate_investing                 (final: 2,000 paths)
#   docker compose exec backend python -m evaluation.evaluate_investing --paths 500     (development)
#   docker compose exec backend python -m evaluation.evaluate_investing --llm real      (synthetic data only)
#   docker compose exec backend python -m evaluation.evaluate_investing --stratified    (separate rare-corner run)
#
# Output (long format, see FIELDS):
#   --paths 2000 --llm none  evaluation/investing_metrics.csv + .md (committed; deterministic)
#   ... --stratified         evaluation/investing_metrics_stratified.csv + .md (committed)
#   anything else            evaluation/results/investing_metrics_<label>.csv + .md (gitignored)
#   always                   evaluation/results/investing_persona_rows_<label>.csv (one row per
#                            persona x method x config x assumption, for significance tests)

import argparse
import csv
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean, median, pstdev

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table)
from app.agents.forecast_explainer import ForecastExplainer
from app.agents.investment_explainer import InvestmentExplainer, build_facts, build_facts_payload, template_text
from app.core.money import to_money
from app.core.transaction_types import TXN_EXPENSE
from app.db.base_class import Base
from app.models.budget import Budget
from app.models.forecast import ForecastRun
from app.models.investment import InvestmentPlan
from app.models.user import User
from app.schemas.investment import InvestmentProfileUpdate
from app.services import investment_service
from app.services.forecast_service import generate_forecast, latest_run_row
from app.services.investing.assumptions import InvestingAssumptions, load_assumptions
from app.services.investing.claims_guard import find_prohibited_claims, is_compliant
from app.services.investing.monte_carlo import simulate_sip
from app.services.investing.portfolio_stats import AssetAssumption, portfolio_return_and_volatility
from app.services.investing.sip_planner import required_sip
from evaluation.evaluate_categorizer import overridden_settings
from evaluation.evaluate_forecast import Ledger, default_origins, load_db, set_profile_income, window_actuals
from evaluation.evaluate_forecast import explainer as forecast_explainer
from evaluation.investing_methods import ABLATIONS, METHODS, MethodPlan, ablation_plan, method_plan
from evaluation.investing_personas import (
    DEFAULT_COUNT, DEFAULT_SEED, SEGMENTS, VARIABILITY_RANGES, Persona, generate_personas, generate_stratified_personas,
)
from evaluation.investing_rules import (
    ALLOCATION, EMERGENCY_FILL_MONTHS, RULES, RUPEE_SLACK, SIP, PersonaFacts, check, gap_to_floor,
)
from scripts.make_synthetic_panel import generate_panel, panel_sha256

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
GUARD_CASES = EVAL_DIR / "investing_guard_cases.csv"
FINAL_PATHS = 2000                    # the committed results use this many Monte Carlo paths
SERVICE_SAMPLE = 30                   # personas run through the real generate_plan (and the LLM)
AS_OF = date(2026, 10, 15)            # "today" for the service sample (only its month matters)
DAYS_PER_MONTH = 30.44
FORECAST_DAYS = 30
BASE = "base"
DEFAULT_CONFIG = "default"

ALLOCATION_RULES = tuple(r.rule_id for r in RULES if r.kind == ALLOCATION)
PRIMARY_SIP_RULES = ("R3a", "R4")      # R3b is the strict secondary reading, reported on its own

# Sensitivity: returns shifted from the base assumptions (volatilities unchanged). Illustrative.
RETURN_SHIFTS: dict[str, dict[str, float]] = {
    "low": {"equity": -0.03, "debt": -0.01, "gold": -0.02, "cash": -0.005},
    "high": {"equity": 0.03, "debt": 0.01, "gold": 0.02, "cash": 0.005},
}
SENSITIVITY_METHODS = ("P", "B0")
VARIABILITY_METHODS = ("B0", "B2", "P")
MIN_RULE_N = 10                       # a rule that applies to fewer personas is reported as not evaluated
GUARD_VERSION = "v2"                  # claims_guard phrase list (v2 added words after seeing v1's misses)

# Panel replay: SIP = max(0, surplus band x factor), compared with the actual next-30-day surplus
PANEL_RULES: dict[str, tuple[str, float]] = {
    "P: low band x 0.8": ("low", 0.8),
    "naive: mean x 1.0": ("mean", 1.0),
    "mean x 0.8": ("mean", 0.8),
    "low band x 1.0": ("low", 1.0),
    "low band x 0.6": ("low", 0.6),
}

FIELDS = ("llm_mode", "paths", "experiment", "method", "config", "assumption", "group", "metric", "value", "n")


# ------------------------------------------------------------------ records


def _record(experiment: str, method: str, config: str, assumption: str, group: str, metric: str,
            value: float, n: int) -> dict:
    return {"experiment": experiment, "method": method, "config": config, "assumption": assumption,
            "group": group, "metric": metric, "value": value, "n": n}


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else float("nan")


def _mean(values: list[float]) -> float:
    return mean(values) if values else float("nan")


# ------------------------------------------------------------------ 1-2. personas


class Simulator:
    """simulate_sip with a cache: ablations often give the same plan as P, so it runs once."""

    def __init__(self, paths: int) -> None:
        self.paths = paths
        self._cache: dict[tuple, tuple[float, float]] = {}

    def run(self, sip: float, years: float, mean_return: float, volatility: float, goal: float,
            seed: int) -> tuple[float, float]:
        """(p10 corpus, probability of reaching the goal). No SIP: nothing grows, the goal is missed."""
        if sip <= 0:
            return 0.0, 0.0
        key = (round(sip, 2), years, round(mean_return, 12), round(volatility, 12), goal, seed)
        if key not in self._cache:
            result = simulate_sip(sip, years, mean_return, volatility, n_paths=self.paths, seed=seed, goal=goal)
            self._cache[key] = (result.p10, result.probability_of_goal)
        return self._cache[key]


@dataclass(frozen=True)
class PersonaRow:
    persona_id: int
    method: str
    config: str
    assumption: str
    segments: tuple[str, ...]
    rules: dict[str, bool | None]
    equity: float
    monthly_sip: float
    emergency_topup: float
    invested: float                   # SIP x months: what the person puts in
    p10: float
    goal_probability: float
    goal_sip_needed: float = float("nan")      # P only: the goal's SIP at the expected return (about 50%)
    goal_prob_at_needed: float = float("nan")  # P only: the probability that SIP really achieves


def shifted_assumptions(base: InvestingAssumptions, label: str) -> dict[str, AssetAssumption]:
    if label == BASE:
        return base.assets
    shifts = RETURN_SHIFTS[label]
    return {name: AssetAssumption(a.expected_return + shifts[name], a.volatility) for name, a in base.assets.items()}


def evaluate_personas(
    personas: list[Persona], assumptions: InvestingAssumptions, paths: int, methods: tuple[str, ...] = METHODS,
    ablations: bool = True, sensitivity: bool = True, goal_gap: bool = True,
) -> list[PersonaRow]:
    simulator = Simulator(paths)
    rows: list[PersonaRow] = []
    for persona in personas:
        facts = persona.facts()
        months = round(persona.horizon_years * 12)
        variants: list[tuple[str, str, MethodPlan]] = [
            (name, DEFAULT_CONFIG, method_plan(name, persona, assumptions)) for name in methods
        ] + ([("P", label, ablation_plan(label, persona, assumptions)) for label in ABLATIONS] if ablations else [])
        defaults = {name: plan for name, config, plan in variants if config == DEFAULT_CONFIG}

        def add(method: str, config: str, assumption: str, plan: MethodPlan, mean_return: float,
                volatility: float, **extra: float) -> None:
            p10, probability = simulator.run(plan.monthly_sip, persona.horizon_years, mean_return, volatility,
                                             persona.goal_amount, persona.seed)
            rows.append(PersonaRow(
                persona.persona_id, method, config, assumption, tuple(persona.segments),
                check(facts, plan.recommendation()), plan.weights.get("equity", 0.0), plan.monthly_sip,
                plan.emergency_topup, plan.monthly_sip * months, p10, probability, **extra,
            ))

        for method, config, plan in variants:
            extra: dict[str, float] = {}
            if goal_gap and method == "P" and config == DEFAULT_CONFIG and months >= 1:
                # The SIP the goal needs at the expected return, and what it really achieves
                needed = round(required_sip(persona.goal_amount, plan.expected_return, persona.horizon_years), 2)
                _, at_needed = simulator.run(needed, persona.horizon_years, plan.expected_return, plan.volatility,
                                             persona.goal_amount, persona.seed)
                extra = {"goal_sip_needed": needed, "goal_prob_at_needed": at_needed}
            add(method, config, BASE, plan, plan.expected_return, plan.volatility, **extra)
        for method in (SENSITIVITY_METHODS if sensitivity else ()):
            plan = defaults[method]
            for label in RETURN_SHIFTS:
                mean_return, volatility = portfolio_return_and_volatility(
                    plan.weights, shifted_assumptions(assumptions, label))
                add(method, DEFAULT_CONFIG, label, plan, mean_return, volatility)
    return rows


def aggregate_personas(rows: list[PersonaRow], experiment: str = "personas") -> list[dict]:
    records: list[dict] = []
    groups: dict[tuple[str, str, str], list[PersonaRow]] = {}
    for row in rows:
        groups.setdefault((row.method, row.config, row.assumption), []).append(row)
    for (method, config, assumption), group_rows in groups.items():
        for segment in SEGMENTS:
            subset = [r for r in group_rows if segment in r.segments]
            if not subset:
                continue
            n = len(subset)

            def add(metric: str, value: float, count: int = n) -> None:
                records.append(_record(experiment, method, config, assumption, segment, metric, value, count))

            investing = [r for r in subset if r.monthly_sip > 0]
            add("shortfall_rate", _rate([r.p10 < r.invested for r in investing]), len(investing))
            add("goal_probability_mean", _mean([r.goal_probability for r in subset]))
            if assumption != BASE:
                continue                          # rules and SIP sizes do not change with the assumptions
            for rule in RULES:
                applicable = [r.rules[rule.rule_id] for r in subset if r.rules[rule.rule_id] is not None]
                add(f"violation_rate:{rule.rule_id}", _rate([not ok for ok in applicable]), len(applicable))
            add("violation_any:allocation", _rate([any(r.rules[i] is False for i in ALLOCATION_RULES) for r in subset]))
            add("violation_any:sip", _rate([any(r.rules[i] is False for i in PRIMARY_SIP_RULES) for r in subset]))
            add("share_investing", _rate([r.monthly_sip > 0 for r in subset]))
            add("mean_sip", _mean([r.monthly_sip for r in subset]))
            add("mean_topup", _mean([r.emergency_topup for r in subset]))
            add("mean_equity", _mean([r.equity for r in subset]))
            gaps = [r for r in subset if r.goal_sip_needed == r.goal_sip_needed]   # not NaN: P default only
            if gaps:
                extra = [max(0.0, r.goal_sip_needed - r.monthly_sip) for r in gaps]
                add("goal_gap:mean_needed_sip", _mean([r.goal_sip_needed for r in gaps]), len(gaps))
                add("goal_gap:mean_extra_sip", _mean(extra), len(gaps))
                add("goal_gap:median_extra_sip", median(extra), len(gaps))
                add("goal_gap:share_already_there", _rate([e <= 0.01 for e in extra]), len(gaps))
                add("goal_gap:goal_probability_at_needed", _mean([r.goal_prob_at_needed for r in gaps]), len(gaps))
    return records


def r3a_feasible(facts: PersonaFacts) -> bool:
    """
    Could ANY plan meet R3a without breaking R4? Only if the whole low-band surplus, set aside for
    EMERGENCY_FILL_MONTHS, covers the emergency gap. Reporting only: no method uses this.
    """
    return max(0.0, facts.low_surplus) * EMERGENCY_FILL_MONTHS >= gap_to_floor(facts) - RUPEE_SLACK


def r3a_split(rows: list[PersonaRow], people: list[Persona]) -> dict[str, dict[str, int]]:
    """
    R3a violations per method (shipped config, base returns) split into "avoidable" (the surplus could
    have filled the fund in time) and "infeasible" (it could not, so meeting R3a would break R4).
    Added after seeing the engine 6.1 results, when R3a was relabelled enforced -> trade_off.
    """
    feasible = {person.persona_id: r3a_feasible(person.facts()) for person in people}
    split: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.config != DEFAULT_CONFIG or row.assumption != BASE or row.rules["R3a"] is not False:
            continue
        counts = split.setdefault(row.method, {"avoidable": 0, "infeasible": 0})
        counts["avoidable" if feasible[row.persona_id] else "infeasible"] += 1
    return split


# ------------------------------------------------------------------ 3. panel affordability


@dataclass
class PanelInfo:
    users: int = 0
    rows: int = 0
    sha256: str = ""
    origins: list[date] = field(default_factory=list)
    forecasts: int = 0


def panel_affordability(origins: list[date] | None = None) -> tuple[list[dict], PanelInfo]:
    """Replays the Phase 5 panel through the real 30-day forecast (template explanations only)."""
    panel = generate_panel()
    origins = origins or default_origins()
    db, users = load_db(panel)
    by_user: dict[str, list] = {}
    for row in panel:
        by_user.setdefault(row.user, []).append(row)
    ledgers = {user: Ledger(rows) for user, rows in by_user.items()}
    scale = DAYS_PER_MONTH / FORECAST_DAYS
    outcomes: list[tuple[str, str, float, float]] = []      # (persona, rule, sip, actual surplus)
    try:
        with forecast_explainer(lambda: ForecastExplainer(chains=[])):
            for as_of in origins:
                for user, user_id in users.items():
                    set_profile_income(db, user_id, as_of)
                    generate_forecast(db, user_id, FORECAST_DAYS, as_of)
                    run = latest_run_row(db, user_id, FORECAST_DAYS)
                    surpluses = {
                        "low": float(investment_service.low_surplus_from_run(run)),
                        "mean": float(run.expected_income - run.expected_fixed_expenses
                                      - run.expected_variable_expenses) * scale,
                    }
                    actual = window_actuals(ledgers[user], as_of + timedelta(days=1),
                                            as_of + timedelta(days=FORECAST_DAYS), [])
                    actual_surplus = (actual.income - actual.total_expenses) * scale   # before investing
                    for rule, (band, factor) in PANEL_RULES.items():
                        sip = round(max(0.0, surpluses[band] * factor), 2)
                        outcomes.append((ledgers[user].persona, rule, sip, actual_surplus))
    finally:
        db.close()

    records: list[dict] = []
    personas = ["all"] + sorted({persona for persona, *_ in outcomes})
    for rule in PANEL_RULES:
        for persona in personas:
            subset = [(sip, act) for p, r, sip, act in outcomes if r == rule and persona in ("all", p)]
            failures = [sip - act for sip, act in subset if sip > act + 0.005]
            for metric, value in (
                ("failure_rate", len(failures) / len(subset)),
                ("mean_sip", _mean([sip for sip, _ in subset])),
                ("mean_excess_when_failing", _mean(failures)),
            ):
                records.append(_record("affordability", rule, DEFAULT_CONFIG, BASE, persona, metric, value, len(subset)))
    records += panel_variability(by_user)
    info = PanelInfo(len(users), len(panel), panel_sha256(panel), origins, len(outcomes) // len(PANEL_RULES))
    return records, info


def panel_variability(by_user: dict[str, list]) -> list[dict]:
    """
    Reference for the persona variability ranges: each panel user's month-to-month spending
    variability (sd / mean of monthly expense totals over the 24 months). The panel is synthetic
    too, so this is a reference point, not ground truth. It includes festival seasonality and
    drift, which the persona cv (one month's uncertainty) does not separate out.
    """
    cvs: dict[str, list[float]] = {}
    for rows in by_user.values():
        monthly: dict[str, float] = {}
        for row in rows:
            if row.txn_type == TXN_EXPENSE:
                key = f"{row.day:%Y-%m}"
                monthly[key] = monthly.get(key, 0.0) + float(row.amount)
        values = list(monthly.values())
        cvs.setdefault(rows[0].persona, []).append(pstdev(values) / mean(values))
    records: list[dict] = []
    for group in ["all", *sorted(cvs)]:
        values = [v for persona, vs in cvs.items() for v in vs if group in ("all", persona)]
        for metric, value in (("cv_median", median(values)), ("cv_min", min(values)), ("cv_max", max(values))):
            records.append(_record("panel_variability", "panel", DEFAULT_CONFIG, BASE, group, metric, value, len(values)))
    return records


# ------------------------------------------------------------------ 4. claims guard


@dataclass(frozen=True)
class GuardCase:
    case_id: str
    label: str
    kind: str
    author: str
    text: str

    @property
    def flagged(self) -> bool:
        return not is_compliant(self.text)


def load_guard_cases(path: Path = GUARD_CASES) -> list[GuardCase]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [GuardCase(**row) for row in csv.DictReader(handle)]


def guard_metrics(cases: list[GuardCase]) -> list[dict]:
    records: list[dict] = []
    for subset_name, subset in (("all", cases),):
        unsafe = [c for c in subset if c.label == "unsafe"]
        safe = [c for c in subset if c.label == "safe"]
        flagged = [c for c in subset if c.flagged]
        true_positive = sum(1 for c in unsafe if c.flagged)

        def add(metric: str, value: float, n: int) -> None:
            records.append(_record("claims_guard", "claims_guard", DEFAULT_CONFIG, BASE, subset_name, metric, value, n))

        add("precision", true_positive / len(flagged) if flagged else float("nan"), len(flagged))
        add("recall", true_positive / len(unsafe) if unsafe else float("nan"), len(unsafe))
        add("false_positive_rate", _rate([c.flagged for c in safe]), len(safe))
        for kind in sorted({c.kind for c in subset}):
            of_kind = [c for c in subset if c.kind == kind]
            add(f"flag_rate:{of_kind[0].label}:{kind}", _rate([c.flagged for c in of_kind]), len(of_kind))
    return records


# ------------------------------------------------------------------ 5. real pipeline sample


@contextmanager
def investment_explainer(factory: Callable[[], InvestmentExplainer]) -> Iterator[None]:
    original = investment_service._build_explainer
    investment_service._build_explainer = factory
    try:
        yield
    finally:
        investment_service._build_explainer = original


def _seed_persona(db: Session, persona: Persona) -> int:
    """One user whose budget, forecast and profile reproduce the persona's numbers exactly."""
    user = User(email=f"persona-{persona.persona_id}@synthetic.example", full_name="Synthetic", hashed_password="x")
    db.add(user)
    db.flush()
    db.add(Budget(user_id=user.id, month=f"{AS_OF:%Y-%m}", category="Housing & Rent",
                  limit_amount=to_money(persona.essential_monthly), agent_limit_amount=to_money(persona.essential_monthly)))
    window = FORECAST_DAYS / DAYS_PER_MONTH      # monthly figures -> one 30-day window
    income = to_money(persona.monthly_income * window)
    variable = to_money((persona.monthly_income - persona.mean_surplus) * window)
    variable_upper = to_money((persona.monthly_income - persona.low_surplus) * window)
    zero = Decimal("0.00")
    db.add(ForecastRun(
        user_id=user.id, as_of=AS_OF, horizon_days=FORECAST_DAYS, model_name="persona", history_months=6,
        low_confidence=False, opening_balance=zero, opening_balance_source="request", income_source="profile",
        expected_income=income, expected_fixed_expenses=zero, expected_variable_expenses=variable,
        expected_variable_expenses_lower=variable, expected_variable_expenses_upper=variable_upper,
        expected_investments=zero, net_cashflow=income - variable, projected_end_balance=income - variable,
        projected_end_balance_lower=income - variable_upper, projected_end_balance_upper=income - variable,
        summary="persona stub", llm_used=False, trajectory=[], recurring_items=[], budget_warnings=[], model_scores=[],
    ))
    db.commit()
    investment_service.upsert_profile(db, user.id, InvestmentProfileUpdate(
        answers=dict(zip(("reaction_to_20pct_drop", "investing_experience", "return_vs_safety"), persona.answers)),
        liquid_savings=to_money(persona.liquid_savings), existing_monthly_sip=to_money(persona.existing_sip),
        income_stability=persona.income_stability, dependents=persona.dependents,
        horizon_years=persona.horizon_years,
    ), AS_OF)
    return user.id


def pipeline_sample(personas: list[Persona], assumptions: InvestingAssumptions, paths: int,
                    llm_factory: Callable[[], InvestmentExplainer] | None, llm_delay: float = 0.0) -> list[dict]:
    """The real generate_plan on SQLite: P must match, the template must pass, and (optionally) the LLM."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    template_only = lambda: InvestmentExplainer(find_prohibited_claims, chains=[])   # noqa: E731
    checker = template_only()
    rows: list[dict] = []
    try:
        with overridden_settings({"INVEST_MC_PATHS": paths}), investment_explainer(llm_factory or template_only):
            for number, persona in enumerate(personas):
                if llm_factory is not None and llm_delay > 0 and number > 0:
                    time.sleep(llm_delay)          # stay under the free tier's requests-per-minute limit
                user_id = _seed_persona(db, persona)
                plan = investment_service.generate_plan(
                    db, user_id, AS_OF, goal_amount=to_money(persona.goal_amount), goal_years=persona.horizon_years)
                stored = db.get(InvestmentPlan, plan["id"])
                expected = method_plan("P", persona, assumptions)
                facts = build_facts(plan, plan["assumptions"])
                problems, claims = checker.check(template_text(facts), build_facts_payload(facts))
                rows.append({
                    "matches_p": (all(abs(plan["target_allocation"][k] - expected.weights.get(k, 0.0)) < 1e-9
                                      for k in plan["target_allocation"])
                                  and abs(plan["sip"]["recommended_total"] - expected.monthly_sip) <= 0.05
                                  and abs(plan["sip"]["emergency_topup"] - expected.emergency_topup) <= 0.05),
                    "template_passes": not problems and not claims,
                    "llm_used": stored.llm_used, "faithful": stored.explanation_faithful,
                    "compliant": stored.claims_compliant, "llm_calls": stored.llm_calls,
                    "raw_claims": stored.raw_prohibited_claims, "source": stored.explanation_source,
                })
    finally:
        db.close()
        engine.dispose()

    n = len(rows)
    answered = [r for r in rows if r["llm_used"]]

    def add(metric: str, value: float, count: int) -> dict:
        return _record("pipeline", "P", DEFAULT_CONFIG, BASE, "sample", metric, value, count)

    records = [
        add("matches_evaluation_p", _rate([r["matches_p"] for r in rows]), n),
        add("template_passes_guards", _rate([r["template_passes"] for r in rows]), n),
        add("llm_calls_per_plan", _mean([r["llm_calls"] for r in rows]), n),
        add("llm_answered_rate", _rate([r["llm_used"] for r in rows]), n),
    ]
    if answered:
        records += [
            add("raw_faithful_rate", _rate([bool(r["faithful"]) for r in answered]), len(answered)),
            add("raw_compliant_rate", _rate([bool(r["compliant"]) for r in answered]), len(answered)),
            add("raw_prohibited_claims_per_plan", _mean([r["raw_claims"] for r in answered]), len(answered)),
            add("llm_text_used_rate", _rate([r["source"] == "llm" for r in answered]), len(answered)),
        ]
    return records


# ------------------------------------------------------------------ run and write


@dataclass
class EvaluationResult:
    records: list[dict]
    persona_rows: list[PersonaRow]
    panel: PanelInfo
    guard_cases: list[GuardCase]
    info: dict


def run_evaluation(
    personas: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED, paths: int = FINAL_PATHS, llm_mode: str = "none",
    llm_factory: Callable[[], InvestmentExplainer] | None = None, panel: bool = True,
    panel_origins: list[date] | None = None, sample: int = SERVICE_SAMPLE, stratified: bool = False,
    variability: bool = True, llm_delay: float = 0.0,
) -> EvaluationResult:
    """
    Every experiment. `panel=False` skips the slow replay; tests pass small numbers everywhere.
    `stratified=True` runs the separate rare-corner set instead of the main personas (no panel,
    no variability sensitivity: those belong to the main run).
    """
    assumptions = load_assumptions()
    if stratified:
        people = generate_stratified_personas(seed=seed)
        panel = variability = False
        pipeline_people = people[::max(1, len(people) // sample)][:sample]     # spread over the strata
    else:
        people = generate_personas(personas, seed)
        pipeline_people = people[:sample]
    persona_rows = evaluate_personas(people, assumptions, paths)
    records = aggregate_personas(persona_rows)
    if variability:
        for label in ("low", "high"):
            varied = generate_personas(personas, seed, VARIABILITY_RANGES[label])
            rows = evaluate_personas(varied, assumptions, paths, methods=VARIABILITY_METHODS, ablations=False,
                                     sensitivity=False, goal_gap=False)
            records += aggregate_personas([replace(r, config=f"variability={label}") for r in rows], "variability")
    panel_info = PanelInfo()
    if panel:
        panel_records, panel_info = panel_affordability(panel_origins)
        records += panel_records
    cases = load_guard_cases()
    records += guard_metrics(cases)
    if llm_mode == "real" and llm_factory is None:
        llm_factory = lambda: InvestmentExplainer.from_settings(find_prohibited_claims)   # noqa: E731
    records += pipeline_sample(pipeline_people, assumptions, paths, llm_factory, llm_delay)
    for record in records:
        record["llm_mode"], record["paths"] = llm_mode, paths
    info = {"personas": len(people), "seed": seed, "paths": paths, "llm_mode": llm_mode,
            "sample": len(pipeline_people), "assumptions": assumptions.to_dict(), "stratified": stratified,
            "engine_version": investment_service.ENGINE_VERSION, "r3a_split": r3a_split(persona_rows, people)}
    return EvaluationResult(records, persona_rows, panel_info, cases, info)


def _fmt(value: float) -> str:
    return "" if value != value else f"{value:.4f}"


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({**{k: record[k] for k in FIELDS}, "value": _fmt(record["value"])})


def write_persona_rows(path: Path, rows: list[PersonaRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rule_ids = [r.rule_id for r in RULES]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["persona_id", "method", "config", "assumption", "segments", "equity", "monthly_sip",
                         "emergency_topup", "invested", "p10", "goal_probability", *rule_ids])
        for r in rows:
            writer.writerow([r.persona_id, r.method, r.config, r.assumption, "|".join(r.segments), f"{r.equity:.4f}",
                             f"{r.monthly_sip:.2f}", f"{r.emergency_topup:.2f}", f"{r.invested:.2f}", f"{r.p10:.2f}",
                             f"{r.goal_probability:.4f}",
                             *["" if r.rules[i] is None else int(not r.rules[i]) for i in rule_ids]])


# ------------------------------------------------------------------ markdown report


class _Index:
    def __init__(self, records: list[dict]) -> None:
        self._by_key = {
            (r["experiment"], r["method"], r["config"], r["assumption"], r["group"], r["metric"]): r for r in records
        }

    def get(self, experiment, method, metric, group="all", config=DEFAULT_CONFIG, assumption=BASE) -> dict | None:
        return self._by_key.get((experiment, method, config, assumption, group, metric))

    def value(self, *args, **kwargs) -> float:
        record = self.get(*args, **kwargs)
        return float("nan") if record is None else record["value"]


def _pct(value: float) -> str:
    return "n/a" if value != value else f"{value * 100:.1f}%"


def _rupees(value: float) -> str:
    return "n/a" if value != value else f"₹{value:,.0f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows]


def _summary_row(index: _Index, label: str, method: str, config: str = DEFAULT_CONFIG, group: str = "all") -> list[str]:
    v = lambda metric: index.value("personas", method, metric, group=group, config=config)   # noqa: E731
    return [label, _pct(v("violation_any:allocation")), _pct(v("violation_any:sip")), _pct(v("violation_rate:R3b")),
            _pct(v("shortfall_rate")), _pct(v("goal_probability_mean")), _pct(v("share_investing")),
            _rupees(v("mean_sip")), _pct(v("mean_equity"))]


SUMMARY_HEADER = ["Method", "Allocation rules broken (R1, R2, R5-R7)", "SIP rules broken (R3a, R4)", "R3b strict",
                  "Shortfall risk (p10 < invested)", "Mean goal probability", "Investing", "Mean SIP", "Mean equity"]
METHOD_LABELS = {
    "B0": "B0 fixed 60/40", "B1": "B1 100 minus age", "B2": "B2 questionnaire only", "P": "**P proposed**",
    "B2_alloc+P_sip": "B2 allocation + P's SIP", "P_alloc+naive_sip": "P allocation + naive SIP",
}


def markdown_report(result: EvaluationResult) -> str:
    index = _Index(result.records)
    info = result.info
    if info["stratified"]:
        title = "# Investment Advisor Agent: STRATIFIED evaluation (rare corners, separate from the main results)"
        population = (f"Stratified personas: {info['personas']} (seed {info['seed']}), 25 per rare corner "
                      f"(R7's strong households, fragile households, cautious people, short horizons). They "
                      f"OVERSAMPLE rare cases, so the pooled rates below are not population rates; read them per rule "
                      f"and per segment.")
    else:
        title = "# Investment Advisor Agent: evaluation results"
        population = f"Synthetic personas: {info['personas']} (seed {info['seed']}, each attribute drawn independently)."
    lines = [
        title,
        "",
        f"{population} Engine {info['engine_version']}. "
        f"Monte Carlo: **{info['paths']:,} paths** per projection, seeded per persona, common random numbers "
        f"across methods. LLM mode: {info['llm_mode']}. Suitability rules R1-R7 were committed before the "
        f"methods were written (see evaluation/investing_rules.py and its git history).",
        "",
        "Return assumptions are the ILLUSTRATIVE PLACEHOLDERS from settings: "
        + ", ".join(f"{a['asset_class']} {a['expected_return'] * 100:.1f}% (vol {a['volatility'] * 100:.1f}%)"
                    for a in info["assumptions"]["assets"])
        + ". They must be replaced with cited figures before publication.",
        "",
        "Naive SIP (B0, B1, B2) = the whole mean monthly surplus, no emergency reservation, no goal cap. "
        "P's SIP = emergency top-up first, then 80% of the LOWER-band surplus, capped at what the goal needs.",
        "",
        "## 1. Main comparison (all personas, base assumptions)",
        "",
    ]
    lines += _table(SUMMARY_HEADER, [_summary_row(index, METHOD_LABELS[m], m) for m in METHODS])
    lines += [
        "",
        "Shortfall risk is over personas with a SIP > 0. Goal probability counts a persona with no SIP as 0%.",
        "",
    ]
    gap = lambda metric: index.value("personas", "P", f"goal_gap:{metric}")   # noqa: E731
    if gap("mean_needed_sip") == gap("mean_needed_sip"):
        lines += [
            f"**Goal gap (P).** At the expected return a goal needs on average {_rupees(gap('mean_needed_sip'))} a "
            f"month, which reaches it in {_pct(gap('goal_probability_at_needed'))} of paths. P's SIP is on average "
            f"{_rupees(gap('mean_extra_sip'))} short of that (median {_rupees(gap('median_extra_sip'))}); "
            f"{_pct(gap('share_already_there'))} of personas already invest enough.",
            "",
        ]
    lines += [
        "## 2. Violation rate per rule",
        "",
        "`enforced` rules are ones P is BUILT to satisfy, so P scoring 0% there only checks the implementation. "
        "`trade_off` (R3a) is built in too, but not at the cost of R4: when even the whole low-band surplus cannot "
        "fill the emergency fund in 12 months, P keeps R4 (it never asks for more than the month leaves). R3a was "
        "relabelled from `enforced` AFTER seeing the engine 6.1 results; its cells split the violations into "
        "avoidable and infeasible given the surplus. "
        "`independent` rules test judgement P was not designed to satisfy (they are still written by the method's "
        "author) and carry more weight. `secondary` (R3b) is a "
        "stricter reading P fails by design: P invests what is left after the emergency top-up. "
        "`fixed_after_evaluation` (R5) found a weakness that engine 6.1 fixed, so its score is no longer evidence. "
        f"A rule that applies to fewer than {MIN_RULE_N} personas is not evaluated.",
        "",
    ]
    rule_rows = []
    for rule in RULES:
        n_applies = index.get("personas", "P", f"violation_rate:{rule.rule_id}")
        n = n_applies["n"] if n_applies else 0
        cells = ([f"not evaluated (n={n})"] * len(METHODS) if n < MIN_RULE_N
                 else [_pct(index.value("personas", m, f"violation_rate:{rule.rule_id}")) for m in METHODS])
        split = info.get("r3a_split")
        if rule.rule_id == "R3a" and split is not None and n >= MIN_RULE_N:
            cells = [f"{cell} ({split.get(m, {}).get('avoidable', 0)} avoidable, "
                     f"{split.get(m, {}).get('infeasible', 0)} infeasible)" for cell, m in zip(cells, METHODS)]
        rule_rows.append([rule.rule_id, rule.kind, rule.design, str(n)] + cells)
    lines += _table(["Rule", "Kind", "Design", "Applies (n)"] + [METHOD_LABELS[m] for m in METHODS], rule_rows)
    lines += ["", *[f"- **{r.rule_id}** {r.description}" for r in RULES], ""]
    if info.get("r3a_split") is not None:
        lines += [
            "R3a counts are personas. **Infeasible** = even the whole low-band surplus x 12 cannot fill the "
            "emergency fund to 3 months, so meeting R3a would break R4; **avoidable** = the surplus was enough. "
            "Computed from the same per-persona results (no re-run).",
            "",
        ]
    lines += ["## 3. By segment", ""]
    segment_rows = []
    for segment in SEGMENTS[1:]:
        for method in ("B0", "B2", "P"):
            record = index.get("personas", method, "share_investing", group=segment)
            if record is None:
                continue
            segment_rows.append([f"{segment} (n={record['n']})"] + _summary_row(index, METHOD_LABELS[method], method,
                                                                               group=segment))
    lines += _table(["Segment"] + SUMMARY_HEADER, segment_rows)
    lines += ["", "## 4. Ablations of P (all personas)", ""]
    ablation_rows = [_summary_row(index, "P (shipped defaults)", "P")]
    ablation_rows += [_summary_row(index, label, "P", config=label) for label in ABLATIONS]
    lines += _table(["P variant"] + SUMMARY_HEADER[1:], ablation_rows)
    lines += [
        "",
        "`goal_p75` sizes the goal's SIP at a heuristic 25th-percentile return instead of the mean; the goal "
        "probability column shows what it really achieves.",
        "",
        "## 5. Sensitivity: plans built under base assumptions, outcomes under other returns",
        "",
    ]
    sensitivity_rows = []
    for method in SENSITIVITY_METHODS:
        for assumption in ("low", BASE, "high"):
            sensitivity_rows.append([
                METHOD_LABELS[method], assumption,
                _pct(index.value("personas", method, "goal_probability_mean", assumption=assumption)),
                _pct(index.value("personas", method, "shortfall_rate", assumption=assumption)),
            ])
    lines += _table(["Method", "Returns", "Mean goal probability", "Shortfall risk"], sensitivity_rows)
    shifts = "; ".join(f"{label}: " + ", ".join(f"{k} {v * 100:+.1f} pp" for k, v in s.items())
                       for label, s in RETURN_SHIFTS.items())
    lines += ["", f"Shifts from the base returns ({shifts}); volatilities unchanged.", ""]

    if any(r["experiment"] == "variability" for r in result.records):
        lines += [
            "## 5b. Sensitivity to the persona spending variability",
            "",
            "The primary run draws spending variability (sd / spending) from the base range; it was NOT changed "
            "after seeing results. The same personas redrawn with a low and a high range:",
            "",
        ]
        variability_rows = []
        for method in VARIABILITY_METHODS:
            for label, (low, high) in VARIABILITY_RANGES.items():
                experiment, config = ("personas", DEFAULT_CONFIG) if label == BASE else ("variability", f"variability={label}")
                v = lambda metric: index.value(experiment, method, metric, config=config)   # noqa: E731
                variability_rows.append([
                    METHOD_LABELS[method], f"{label} ({low * 100:.0f}-{high * 100:.0f}%)",
                    _pct(v("violation_any:sip")), _pct(v("violation_rate:R4")), _pct(v("share_investing")),
                    _rupees(v("mean_sip")), _pct(v("goal_probability_mean")), _pct(v("shortfall_rate")),
                ])
        lines += _table(["Method", "Variability", "SIP rules broken", "R4", "Investing", "Mean SIP",
                         "Mean goal probability", "Shortfall risk"], variability_rows)
        reference = index.get("panel_variability", "panel", "cv_median")
        if reference is not None:
            by_persona = sorted({r["group"] for r in result.records if r["experiment"] == "panel_variability"} - {"all"})
            lines += [
                "",
                f"Reference: the Phase 5 panel's month-to-month spending variability (sd / mean of 24 monthly expense "
                f"totals) has a median of {_pct(reference['value'])} over {reference['n']} users (range "
                f"{_pct(index.value('panel_variability', 'panel', 'cv_min'))}-"
                f"{_pct(index.value('panel_variability', 'panel', 'cv_max'))}; "
                + ", ".join(f"{g} {_pct(index.value('panel_variability', 'panel', 'cv_median', group=g))}"
                            for g in by_persona)
                + "). The panel is synthetic too, so it is a reference point, not ground truth, and it mixes "
                  "seasonality and drift into the variability.",
            ]
        lines += [""]

    lines += ["## 6. SIP affordability on the Phase 5 panel (replay through the real forecast)", ""]
    if result.panel.forecasts:
        p = result.panel
        lines += [
            f"{p.users} synthetic users, {p.rows:,} rows, SHA-256 `{p.sha256[:16]}…`; {len(p.origins)} origins "
            f"({p.origins[0]} … {p.origins[-1]}), {p.forecasts} real 30-day forecasts. A SIP = max(0, surplus band "
            f"x factor) FAILS when it is larger than the ACTUAL surplus before investing in the next 30 days. "
            f"No emergency fund or goal here (the panel has no savings data). Independent of the Monte Carlo paths.",
            "",
        ]
        panel_personas = sorted({r["group"] for r in result.records if r["experiment"] == "affordability"} - {"all"})
        panel_rows = []
        for rule in PANEL_RULES:
            panel_rows.append([rule] + [_pct(index.value("affordability", rule, "failure_rate", group=g))
                                        for g in ["all"] + panel_personas]
                              + [_rupees(index.value("affordability", rule, "mean_sip")),
                                 _rupees(index.value("affordability", rule, "mean_excess_when_failing"))])
        lines += _table(["SIP rule", "Failure rate (all)"] + panel_personas + ["Mean SIP", "Mean excess when failing"],
                        panel_rows)
    else:
        lines += ["Not part of this run (the panel replay belongs to the main run; see investing_metrics.md)."
                  if info["stratified"] else "Not run (--skip-panel)."]

    lines += [
        "",
        "## 7. Claims guard on hand-labelled sentences",
        "",
        f"Claims guard **{GUARD_VERSION}**. {len(result.guard_cases)} synthetic sentences in "
        f"evaluation/investing_guard_cases.csv, all written by the same author as the method and the regex (no "
        f"independent labelling). The v2 phrases were added AFTER seeing v1's misses on these same sentences, so "
        f"the numbers below are NOT evidence of recall (the v1 numbers are logged in "
        f"docs/modules/investing-evaluation.md). The real-LLM run shows how often real model text hits the guard.",
        "",
    ]
    guard_rows = [[metric.replace("_", " "), _pct(index.value("claims_guard", "claims_guard", metric)),
                   str((index.get("claims_guard", "claims_guard", metric) or {"n": 0})["n"])]
                  for metric in ("precision", "recall", "false_positive_rate")]
    kinds = sorted(r["metric"] for r in result.records
                   if r["experiment"] == "claims_guard" and r["group"] == "all" and r["metric"].startswith("flag_rate:"))
    guard_rows += [[f"flagged: {k.split(':', 1)[1]}", _pct(index.value("claims_guard", "claims_guard", k)),
                    str(index.get("claims_guard", "claims_guard", k)["n"])] for k in kinds]
    lines += _table(["Metric", "Value", "n"], guard_rows)
    missed = [c for c in result.guard_cases if c.label == "unsafe" and not c.flagged]
    false_alarms = [c for c in result.guard_cases if c.label == "safe" and c.flagged]
    lines += ["", f"Unsafe sentences the guard MISSED ({len(missed)}):", ""]
    lines += [f"- {c.case_id}: {c.text}" for c in missed] or ["- none"]
    lines += ["", f"Safe sentences the guard FLAGGED ({len(false_alarms)}):", ""]
    lines += [f"- {c.case_id} ({c.kind}): {c.text}" for c in false_alarms] or ["- none"]

    lines += ["", "## 8. Real pipeline sample", ""]
    sample = lambda metric: index.get("pipeline", "P", metric, group="sample")   # noqa: E731
    pipeline_rows = [[metric.replace("_", " "), _pct(rec["value"]) if "rate" in metric or "matches" in metric
                      or "passes" in metric else f"{rec['value']:.2f}", str(rec["n"])]
                     for metric in ("matches_evaluation_p", "template_passes_guards", "llm_answered_rate",
                                    "llm_calls_per_plan", "raw_faithful_rate", "raw_compliant_rate",
                                    "raw_prohibited_claims_per_plan", "llm_text_used_rate")
                     if (rec := sample(metric)) is not None]
    lines += [
        f"{info['sample']} personas through the REAL generate_plan (SQLite, budget and forecast rows "
        f"stubbed from the persona). `matches evaluation p` = same allocation and SIP as P in this script.",
        "",
    ]
    lines += _table(["Metric", "Value", "n"], pipeline_rows)
    lines += [
        "",
        "## 9. Limitations",
        "",
        "- Synthetic personas; every attribute is drawn independently, so unrealistic combinations occur "
        "(for example age 60 with a 25-year horizon).",
        "- There is no ground truth for a suitable allocation: the rules are the team's pre-registered judgement. "
        "R1, R2 and R4 are enforced by P's design (R4 only since engine 6.1: a regression test now). R3a is a "
        "trade-off with R4, relabelled after seeing the engine 6.1 results: capping the top-up at the surplus "
        "means P breaks R3a whenever the surplus cannot fill the emergency fund in time (the per-rule table "
        "splits these violations into avoidable and infeasible). R5 was fixed after the evaluation found it; the "
        "remaining independent rules (R6, R7) and the panel replay carry the weight.",
        "- All rules and guard sentences were written by the same author as the method (no independent labelling).",
        "- One round of fixes after seeing results (engine 6.1), logged with before/after numbers in "
        "docs/modules/investing-evaluation.md. No further tuning against these rules.",
        "- Normal monthly returns understate fat tails; the return assumptions are placeholders; no tax, fees or "
        "inflation.",
        "- The low surplus of a persona is modelled (mean - 1.28 sd); the panel replay uses the real forecast "
        "instead, whose interval coverage is 61-65% against 80% nominal.",
        "- The claims guard flags negations on purpose (fail safe), so its false-positive rate is high by design.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Investment Advisor Agent on synthetic data.")
    parser.add_argument("--personas", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--paths", type=int, default=FINAL_PATHS, help=f"Monte Carlo paths (final: {FINAL_PATHS})")
    parser.add_argument("--llm-delay", type=float, default=4.0,
                        help="seconds between real LLM calls (free-tier rate limit); only with --llm real")
    parser.add_argument("--llm", choices=("none", "real"), default="none",
                        help="real = the configured LLM on the pipeline sample (synthetic data only)")
    parser.add_argument("--skip-panel", action="store_true", help="skip the Phase 5 panel replay")
    parser.add_argument("--skip-variability", action="store_true", help="skip the variability sensitivity")
    parser.add_argument("--stratified", action="store_true", help="the separate rare-corner set (~100 personas)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    result = run_evaluation(args.personas, args.seed, args.paths, args.llm, panel=not args.skip_panel,
                            stratified=args.stratified, variability=not args.skip_variability,
                            llm_delay=args.llm_delay if args.llm == "real" else 0.0)
    suffix = "_stratified" if args.stratified else ""
    final = args.paths == FINAL_PATHS and args.llm == "none" and args.seed == DEFAULT_SEED and (
        args.stratified or (args.personas == DEFAULT_COUNT and not args.skip_panel and not args.skip_variability))
    if final:
        out_csv, out_md = EVAL_DIR / f"investing_metrics{suffix}.csv", EVAL_DIR / f"investing_metrics{suffix}.md"
        label = f"paths{args.paths}{suffix}"
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") if args.llm == "real" else ""
        label = f"paths{args.paths}{suffix}" + (f"_llm_{stamp}" if stamp else "")
        out_csv, out_md = RESULTS_DIR / f"investing_metrics_{label}.csv", RESULTS_DIR / f"investing_metrics_{label}.md"
    write_csv(out_csv, result.records)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(markdown_report(result), encoding="utf-8", newline="\n")
    write_persona_rows(RESULTS_DIR / f"investing_persona_rows_{label}.csv", result.persona_rows)
    print(f"Wrote {out_csv} and {out_md} ({len(result.records)} records)")


if __name__ == "__main__":
    main()
