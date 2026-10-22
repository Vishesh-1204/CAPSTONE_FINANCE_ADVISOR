# backend/scripts/make_synthetic_panel.py
#
# Purpose: generates the SYNTHETIC 24-month panel (October 2024 - September 2026,
# 30 users, 4 personas) used to evaluate the Financial Forecast Agent
# (evaluation/evaluate_forecast.py). Every merchant, payee and amount is made up.
#
#   steady       salaried, spends a stable share of income, a medium SIP
#   freelancer   1-3 client invoices a month that swing a lot (sometimes none):
#                no salary to detect, so the agent must use the profile income
#   high_spender salaried with an EMI, spends about everything it earns: the
#                balance runs low and dips below zero in heavy months (cash crunch)
#   saver        salaried, low spending, a large SIP
#
# Built-in effects the forecaster has to cope with:
#   * festival season: discretionary spending jumps in October and November
#   * one salary hike in April 2026 for every salaried user
#   * one medical bill per user, in a random month (a one-off shock)
#   * slow lifestyle drift (spending grows or shrinks a little each month)
#   * refunds, quarterly interest and person-to-person transfers (UPI/P2P/PAYEE-xxxx)
#   * reactive own-account transfers on the 1st (txn_type 'transfer': neither income
#     nor spending): a month that ends below a floor gets a top-up from savings, and
#     a month that ends above a ceiling sweeps the surplus out to savings. This keeps
#     balances realistic (no one keeps 10 months of salary in a current account);
#     the dips below zero before a top-up are the real cash-crunch events the
#     evaluation scores.
#
# Ground truth columns:
#   balance    running statement balance after the row (rows are in statement order)
#   recurring  1 for the scheduled monthly items (salary, rent, subscription, SIP,
#              EMI): what recurrence detection SHOULD find
#
# Reproducible: user i draws from random.Random(seed * 1000 + i), so the same seed
# always gives byte-identical output and adding a user never changes the others.
# This is a NEW file (not an edit of make_synthetic_history.py) so the committed
# Phase 4 budget results stay reproducible; it reuses that file's merchants.
#
# Run (inside Docker):
#   docker compose exec backend python -m scripts.make_synthetic_panel
#   docker compose exec backend python -m scripts.make_synthetic_panel --seed 7 --out /tmp/panel.csv

import argparse
import calendar
import csv
import hashlib
import io
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.core.categories import (
    INTEREST_CATEGORY, INVESTMENTS_CATEGORY, OTHER_INCOME_CATEGORY, REFUND_CATEGORY, SALARY_CATEGORY,
    TRANSFERS_CATEGORY,
)
from app.core.transaction_types import (
    CREDIT, DEBIT, TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_OUT, TXN_TRANSFER,
)
from scripts.make_synthetic_history import MERCHANTS, _split

DEFAULT_SEED = 42
FIRST_MONTH = date(2024, 10, 1)
N_MONTHS = 24
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "evaluation" / "results" / "synthetic_forecast_panel.csv"
FIELDS = ("user", "persona", "date", "description", "amount", "direction", "txn_type", "category",
          "balance", "recurring")

FESTIVAL_MONTHS = frozenset({10, 11})            # October, November (Diwali season)
HIKE_MONTH = date(2026, 4, 1)                    # the appraisal cycle: one salary hike
DISCRETIONARY = frozenset({"Shopping", "Food & Dining", "Entertainment", "Travel"})

# Narrations of the scheduled (recurring) items, with the day of month they land on
SALARY = ("NEFT-SYNTH EMPLOYER PAYROLL", 1)
RENT = ("NEFT-SYNTH HOUSING SOCIETY RENT", 5)
SIP = ("NACH/SYNTH MF SIP/FOLIO 0000001", 7)
EMI = ("NACH/SYNTH AUTO LOAN EMI 0001", 10)          # a car loan: Transportation
SUBSCRIPTION = ("SYNTH STREAMING MONTHLY", 12)
INVOICE = "NEFT-SYNTH CLIENT INVOICE PAYMENT"
TOP_UP = "IMPS/OWN ACCOUNT TRANSFER SELF"
SWEEP = "IMPS/OWN ACCOUNT SWEEP TO SAVINGS"
REFUND = "REFUND SYNTH ONLINE MARKETPLACE"
INTEREST = "SB INT PD SYNTH BANK"
SUBSCRIPTION_PRICES = (199, 499, 649)


@dataclass(frozen=True)
class PanelRow:
    """One synthetic transaction with its true labels and the running balance after it."""
    user: str
    persona: str
    day: date
    description: str
    amount: Decimal                # always positive; `direction` says in or out
    direction: str
    txn_type: str
    category: str
    balance: Decimal
    recurring: bool


@dataclass(frozen=True)
class Variable:
    """A variable-spending category: share of monthly income, transactions per month, noise (+/- fraction)."""
    share: float
    count: int
    noise: float


@dataclass(frozen=True)
class Persona:
    """A template; every user of the persona draws its own numbers from these ranges."""
    name: str
    users: int
    income: tuple[int, int]                    # monthly salary (or mean freelance income) range
    salaried: bool
    rent_share: tuple[float, float]            # rent as a share of income
    sip_share: tuple[float, float]             # SIP as a share of income (0 = none)
    emi_share: tuple[float, float]             # EMI as a share of income (0 = none)
    variable: dict[str, Variable]
    opening_months: tuple[float, float]        # opening balance, in months of income
    top_up_below: float                        # top-up when a month ends below this x income ...
    top_up_to: float                           # ... back up to this x income
    sweep_above: float                         # sweep to savings when a month ends above this x income ...
    sweep_to: float                            # ... down to this x income
    drift: tuple[float, float]                 # monthly spending drift range (0.005 = +0.5%/month)


# Every variable category has >= 2 transactions a month or >= 20% noise, so it can
# never look like a fixed monthly bill (the recurring ground truth stays unambiguous).
_BASE_VARIABLE: dict[str, Variable] = {
    "Groceries": Variable(0.09, 6, 0.10),
    "Utilities": Variable(0.04, 3, 0.15),
    "Transportation": Variable(0.04, 6, 0.20),
    "Food & Dining": Variable(0.06, 8, 0.25),
    "Shopping": Variable(0.05, 3, 0.40),
    "Entertainment": Variable(0.02, 2, 0.40),
    "Personal Care": Variable(0.015, 2, 0.30),
    TRANSFERS_CATEGORY: Variable(0.02, 2, 0.40),
}


def _scaled(factor: float, **overrides: Variable) -> dict[str, Variable]:
    """The base basket with every share multiplied by `factor`, plus per-persona overrides."""
    basket = {c: Variable(v.share * factor, v.count, v.noise) for c, v in _BASE_VARIABLE.items()}
    return {**basket, **overrides}


PERSONAS: tuple[Persona, ...] = (
    Persona(
        name="steady", users=8, income=(45_000, 90_000), salaried=True,
        rent_share=(0.20, 0.28), sip_share=(0.08, 0.12), emi_share=(0.0, 0.0),
        variable=_scaled(1.0), opening_months=(0.8, 1.5), top_up_below=0.15, top_up_to=0.5,
        sweep_above=1.5, sweep_to=0.8, drift=(0.0, 0.004),
    ),
    Persona(
        name="freelancer", users=7, income=(50_000, 100_000), salaried=False,
        rent_share=(0.18, 0.25), sip_share=(0.03, 0.05), emi_share=(0.0, 0.0),
        variable=_scaled(1.0, **{"Food & Dining": Variable(0.08, 8, 0.35), "Shopping": Variable(0.06, 3, 0.50)}),
        opening_months=(0.4, 0.9), top_up_below=0.05, top_up_to=0.4,
        sweep_above=1.0, sweep_to=0.6, drift=(-0.002, 0.004),
    ),
    Persona(
        name="high_spender", users=8, income=(60_000, 120_000), salaried=True,
        rent_share=(0.22, 0.30), sip_share=(0.0, 0.0), emi_share=(0.08, 0.12),
        variable=_scaled(1.45, **{"Shopping": Variable(0.12, 5, 0.40), "Entertainment": Variable(0.05, 4, 0.40),
                                  "Food & Dining": Variable(0.12, 12, 0.25)}),
        opening_months=(0.15, 0.4), top_up_below=0.05, top_up_to=0.25,
        sweep_above=1.0, sweep_to=0.5, drift=(0.002, 0.008),
    ),
    Persona(
        name="saver", users=7, income=(50_000, 110_000), salaried=True,
        rent_share=(0.12, 0.20), sip_share=(0.25, 0.35), emi_share=(0.0, 0.0),
        variable=_scaled(0.7), opening_months=(1.5, 3.0), top_up_below=0.2, top_up_to=0.6,
        sweep_above=2.0, sweep_to=1.2, drift=(-0.002, 0.002),
    ),
)

# Festival multipliers on discretionary categories (other categories: x1)
_FESTIVAL_BOOST = {"Shopping": (1.8, 2.6), "Food & Dining": (1.2, 1.4), "Entertainment": (1.3, 1.6),
                   "Travel": (1.0, 1.0)}


def panel_months(first: date = FIRST_MONTH, count: int = N_MONTHS) -> list[date]:
    """The 1st of every panel month, oldest first."""
    months, current = [], first
    for _ in range(count):
        months.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return months


def _day(first: date, day: int) -> date:
    """`day` of the month, moved to month-end when the month is shorter."""
    return first.replace(day=min(day, calendar.monthrange(first.year, first.month)[1]))


@dataclass
class _Draft:
    """A row before its balance is known."""
    day: date
    description: str
    amount: int
    direction: str
    txn_type: str
    category: str
    recurring: bool = False


@dataclass(frozen=True)
class _UserPlan:
    """The numbers one user drew from its persona template (fixed for the whole panel)."""
    income: int
    rent: int
    sip: int
    emi: int
    subscription: int
    drift: float
    hike: float
    medical_month: int
    medical_amount: int
    opening: int


def _hundreds(value: float) -> int:
    """Rounds to whole hundreds of rupees, as an int (never a float inside a Decimal)."""
    return int(round(value, -2))


def _draw_plan(persona: Persona, rng: random.Random) -> _UserPlan:
    income = rng.randrange(persona.income[0], persona.income[1] + 1, 500)
    return _UserPlan(
        income=income,
        rent=_hundreds(income * rng.uniform(*persona.rent_share)),
        sip=_hundreds(income * rng.uniform(*persona.sip_share)),
        emi=_hundreds(income * rng.uniform(*persona.emi_share)),
        subscription=rng.choice(SUBSCRIPTION_PRICES),
        drift=rng.uniform(*persona.drift),
        hike=rng.uniform(0.08, 0.15),
        medical_month=rng.randint(3, N_MONTHS - 2),
        medical_amount=_hundreds(income * rng.uniform(0.15, 0.40)),
        opening=_hundreds(income * rng.uniform(*persona.opening_months)),
    )


def _month_drafts(persona: Persona, plan: _UserPlan, index: int, first: date, rng: random.Random) -> list[_Draft]:
    """Every row of one user-month except the reactive top-up."""
    rows: list[_Draft] = []

    # --- income ---
    if persona.salaried:
        salary = plan.income * (1 + plan.hike) if first >= HIKE_MONTH else plan.income
        rows.append(_Draft(_day(first, SALARY[1]), SALARY[0], _hundreds(salary), CREDIT, TXN_INCOME,
                           SALARY_CATEGORY, recurring=True))
    else:
        if rng.random() >= 0.08:                           # about 1 month in 12 has no paid invoice
            total = round(plan.income * rng.uniform(0.35, 1.75))
            for part in _split(total, rng.randint(1, 3), rng):
                rows.append(_Draft(_day(first, rng.randint(1, 28)), INVOICE, part, CREDIT, TXN_INCOME,
                                   OTHER_INCOME_CATEGORY))

    # --- scheduled commitments (the recurring ground truth) ---
    rows.append(_Draft(_day(first, RENT[1]), RENT[0], plan.rent, DEBIT, TXN_EXPENSE, "Housing & Rent", True))
    rows.append(_Draft(_day(first, SUBSCRIPTION[1]), SUBSCRIPTION[0], plan.subscription, DEBIT, TXN_EXPENSE,
                       "Subscriptions", True))
    if plan.emi:
        rows.append(_Draft(_day(first, EMI[1]), EMI[0], plan.emi, DEBIT, TXN_EXPENSE, "Transportation", True))
    if plan.sip:
        rows.append(_Draft(_day(first, SIP[1]), SIP[0], plan.sip, DEBIT, TXN_INVESTMENT_OUT,
                           INVESTMENTS_CATEGORY, True))

    # --- variable spending: base x drift x festival x noise, split into transactions ---
    level = plan.income * (1 + plan.drift) ** index
    for category, spec in persona.variable.items():
        boost = rng.uniform(*_FESTIVAL_BOOST[category]) if category in _FESTIVAL_BOOST and first.month in FESTIVAL_MONTHS else 1.0
        total = round(level * spec.share * boost * (1 + rng.uniform(-spec.noise, spec.noise)))
        if total <= 0:
            continue
        for part in _split(total, spec.count, rng):
            rows.append(_Draft(_day(first, rng.randint(1, 28)), rng.choice(MERCHANTS[category]), part, DEBIT,
                               TXN_EXPENSE, category))
    if rng.random() < (0.35 if first.month in FESTIVAL_MONTHS else 0.12):    # trips, more in the holidays
        rows.append(_Draft(_day(first, rng.randint(1, 28)), rng.choice(MERCHANTS["Travel"]),
                           round(level * rng.uniform(0.08, 0.25)), DEBIT, TXN_EXPENSE, "Travel"))
    if rng.random() < 0.3:                                                    # a small pharmacy/clinic bill
        rows.append(_Draft(_day(first, rng.randint(1, 28)), rng.choice(MERCHANTS["Healthcare"]),
                           rng.randint(200, 1_500), DEBIT, TXN_EXPENSE, "Healthcare"))
    if index == plan.medical_month:                                           # the one-off shock
        rows.append(_Draft(_day(first, rng.randint(1, 28)), "SYNTH HOSPITAL BILL", plan.medical_amount, DEBIT,
                           TXN_EXPENSE, "Healthcare"))

    # --- money in that is not earned income, and interest ---
    if rng.random() < 0.15:
        rows.append(_Draft(_day(first, rng.randint(1, 28)), REFUND, rng.randint(300, 2_000), CREDIT, TXN_INCOME,
                           REFUND_CATEGORY))
    if first.month in (3, 6, 9, 12):
        rows.append(_Draft(_day(first, 30), INTEREST, rng.randint(100, 900), CREDIT, TXN_INCOME, INTEREST_CATEGORY))
    return rows


def _statement_order(draft: _Draft) -> tuple:
    """Rows of one day: credits first (salary lands before the day's spending), then a stable order."""
    return draft.day, draft.direction != CREDIT, draft.description, draft.amount


def _user_rows(user: str, persona: Persona, rng: random.Random) -> list[PanelRow]:
    plan = _draw_plan(persona, rng)
    balance = Decimal(plan.opening)
    rows: list[PanelRow] = []
    transfer: _Draft | None = None              # next month's top-up or sweep
    for index, first in enumerate(panel_months()):
        drafts = _month_drafts(persona, plan, index, first, rng)
        if transfer is not None:
            drafts.append(transfer)
        for d in sorted(drafts, key=_statement_order):
            amount = Decimal(d.amount).quantize(Decimal("0.01"))
            balance += amount if d.direction == CREDIT else -amount
            rows.append(PanelRow(user, persona.name, d.day, d.description, amount, d.direction, d.txn_type,
                                 d.category, balance, d.recurring))
        # Reactive own-account transfer on the 1st of next month (a dip before it already happened)
        transfer = None
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        if balance < Decimal(plan.income * persona.top_up_below):
            needed = _hundreds(plan.income * persona.top_up_to - float(balance))
            transfer = _Draft(nxt, TOP_UP, needed, CREDIT, TXN_TRANSFER, TRANSFERS_CATEGORY)
        elif balance > Decimal(plan.income * persona.sweep_above):
            surplus = _hundreds(float(balance) - plan.income * persona.sweep_to)
            transfer = _Draft(nxt, SWEEP, surplus, DEBIT, TXN_TRANSFER, TRANSFERS_CATEGORY)
    return rows


def user_ids() -> list[tuple[str, Persona]]:
    """('u01', persona), ... in persona order; the number is the user's seed offset."""
    pairs, n = [], 0
    for persona in PERSONAS:
        for _ in range(persona.users):
            n += 1
            pairs.append((f"u{n:02d}", persona))
    return pairs


def generate_panel(seed: int = DEFAULT_SEED, users: list[str] | None = None) -> list[PanelRow]:
    """
    Every user's rows in statement order (user, then date). `users` keeps only
    those ids (the tests use a small panel). Same seed -> same rows.
    """
    rows: list[PanelRow] = []
    for user, persona in user_ids():
        if users is not None and user not in users:
            continue
        rng = random.Random(seed * 1000 + int(user[1:]))      # local generator per user
        rows += _user_rows(user, persona, rng)
    return rows


def _write(rows: list[PanelRow], handle) -> None:
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(FIELDS)
    for r in rows:
        writer.writerow([r.user, r.persona, r.day.isoformat(), r.description, f"{r.amount:.2f}", r.direction,
                         r.txn_type, r.category, f"{r.balance:.2f}", int(r.recurring)])


def panel_sha256(rows: list[PanelRow]) -> str:
    """Fingerprint of the CSV text: cite it with the results so others can check they rebuilt the same data."""
    buffer = io.StringIO()
    _write(rows, buffer)
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def write_csv(rows: list[PanelRow], path: Path) -> None:
    """Writes the rows with '\n' line endings so the file is byte-identical on every OS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        _write(rows, handle)


def read_csv(path: Path) -> list[PanelRow]:
    """Reads a file written by write_csv back into PanelRow objects."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            PanelRow(r["user"], r["persona"], date.fromisoformat(r["date"]), r["description"], Decimal(r["amount"]),
                     r["direction"], r["txn_type"], r["category"], Decimal(r["balance"]), r["recurring"] == "1")
            for r in csv.DictReader(handle)
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic 24-month forecast panel.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rows = generate_panel(args.seed)
    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} rows for {len(user_ids())} users x {N_MONTHS} months to {args.out}")
    print(f"SHA-256: {panel_sha256(rows)}")


if __name__ == "__main__":
    main()
