# backend/scripts/make_synthetic_history.py
#
# Purpose: generates a SYNTHETIC 6-month transaction history (April-September
# 2026) for three personas, used to evaluate the Budget Planning Agent
# (evaluation/evaluate_budget.py). Every merchant, payee and amount is made up.
#
#   frugal       steady salary, spends well under its income, low month-to-month noise
#   overspender  steady salary, discretionary spending far above what fits -> limits get scaled down
#   irregular    freelance income that swings month to month, noisy spending and one-off spikes
#                (a trip, a medical bill, festival shopping)
#
# Every persona ALSO has money movements that are NOT spending (SIPs, broker
# top-ups, self-transfers, redemptions, refunds, interest). The planner must
# ignore all of them, so the evaluation checks the "expenses only" rule end to end.
#
# Categories are ground truth ("oracle labels"): the evaluation measures the
# planner, not the categorizer. Reproducible: a fixed seed and a local
# random.Random, so the same seed always gives byte-identical output.
#
# Run (inside Docker):
#   docker compose exec backend python -m scripts.make_synthetic_history
#   docker compose exec backend python -m scripts.make_synthetic_history --seed 7 --out /tmp/history.csv

import argparse
import csv
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.core.categories import (
    INTEREST_CATEGORY, INVESTMENT_REDEMPTION_CATEGORY, INVESTMENTS_CATEGORY, OTHER_INCOME_CATEGORY,
    REFUND_CATEGORY, SALARY_CATEGORY, TRANSFERS_CATEGORY,
)
from app.core.transaction_types import (
    CREDIT, DEBIT, TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_IN, TXN_INVESTMENT_OUT, TXN_TRANSFER,
)

DEFAULT_SEED = 42
MONTHS: tuple[str, ...] = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "evaluation" / "synthetic_budget_history.csv"
FIELDS = ("persona", "date", "description", "amount", "direction", "txn_type", "category")

# Made-up merchant narrations per expense category (never real people)
MERCHANTS: dict[str, tuple[str, ...]] = {
    "Housing & Rent": ("NEFT-SYNTH HOUSING SOCIETY RENT",),
    "Groceries": ("POS SYNTH KIRANA STORE", "UPI/SYNTHMART GROCERY/synthmart@okaxis", "POS FRESH VEG SYNTH"),
    "Utilities": ("SYNTH POWER ELECTRICITY BILL", "SYNTH FIBER BROADBAND", "SYNTH MOBILE RECHARGE"),
    "Transportation": ("UPI/SYNTH CABS/synthcabs@okicici", "SYNTH METRO CARD TOPUP", "POS SYNTH FUEL STATION"),
    "Healthcare": ("POS SYNTH PHARMACY", "SYNTH CLINIC CONSULTATION"),
    "Insurance": ("NACH/SYNTH HEALTH INSURANCE PREMIUM",),
    "Education": ("SYNTH ONLINE COURSE FEE",),
    "Food & Dining": ("UPI/SYNTH FOOD DELIVERY/synthfood@okhdfc", "POS SYNTH CAFE", "POS SYNTH RESTAURANT"),
    "Entertainment": ("SYNTH CINEMAS TICKETS", "SYNTH GAMING STORE"),
    "Shopping": ("SYNTH ONLINE MARKETPLACE ORDER", "POS SYNTH FASHION STORE", "SYNTH ELECTRONICS ORDER"),
    "Travel": ("SYNTH RAIL BOOKING", "SYNTH AIRLINES TICKET", "SYNTH HOTEL BOOKING"),
    "Personal Care": ("POS SYNTH SALON", "SYNTH GYM MEMBERSHIP"),
    "Subscriptions": ("SYNTH STREAMING MONTHLY",),
    TRANSFERS_CATEGORY: ("UPI/P2P/PAYEE-0001", "UPI/P2P/PAYEE-0002", "UPI/P2P/PAYEE-0003"),
}


@dataclass(frozen=True)
class HistoryRow:
    """One synthetic transaction with its true labels."""
    persona: str
    day: date
    description: str
    amount: Decimal                # always positive; `direction` says in or out
    direction: str
    txn_type: str
    category: str


@dataclass(frozen=True)
class Spend:
    """A recurring expense category: monthly base amount, transactions per month, noise (+/- fraction)."""
    base: int
    count: int
    noise: float


@dataclass(frozen=True)
class Persona:
    name: str
    savings_target_pct: Decimal                        # what the evaluation puts in the profile
    salary: tuple[int, ...] | None                     # monthly salary (None = freelancer)
    freelance: tuple[int, ...] | None                  # monthly freelance income
    spending: dict[str, Spend]
    one_offs: dict[int, list[tuple[str, int]]] = field(default_factory=dict)   # month index -> (category, amount)
    sip: int = 0                                       # monthly SIP (investment_out)
    broker_topups: dict[int, int] = field(default_factory=dict)                # month index -> amount
    self_transfers: dict[int, int] = field(default_factory=dict)               # month index -> amount


PERSONAS: tuple[Persona, ...] = (
    Persona(
        name="frugal", savings_target_pct=Decimal("25"), salary=(55_000,) * 6, freelance=None,
        spending={
            "Housing & Rent": Spend(12_000, 1, 0.0), "Groceries": Spend(5_500, 6, 0.05),
            "Utilities": Spend(2_200, 3, 0.05), "Transportation": Spend(1_800, 6, 0.05),
            "Food & Dining": Spend(2_500, 5, 0.05), "Subscriptions": Spend(199, 1, 0.0),
            "Personal Care": Spend(600, 1, 0.05), "Healthcare": Spend(400, 1, 0.10),
            TRANSFERS_CATEGORY: Spend(800, 1, 0.10),
        },
        sip=8_000, self_transfers={3: 10_000},
    ),
    Persona(
        name="overspender", savings_target_pct=Decimal("20"), salary=(70_000,) * 6, freelance=None,
        spending={
            "Housing & Rent": Spend(22_000, 1, 0.0), "Groceries": Spend(7_000, 6, 0.10),
            "Utilities": Spend(3_000, 3, 0.10), "Transportation": Spend(4_500, 8, 0.15),
            "Insurance": Spend(1_500, 1, 0.0), "Food & Dining": Spend(9_000, 12, 0.15),
            "Shopping": Spend(9_000, 5, 0.20), "Entertainment": Spend(4_000, 4, 0.20),
            "Subscriptions": Spend(1_200, 3, 0.0), "Personal Care": Spend(2_500, 2, 0.15),
            "Travel": Spend(3_000, 1, 0.25), TRANSFERS_CATEGORY: Spend(2_000, 3, 0.20),
        },
        sip=3_000, self_transfers={1: 15_000, 4: 15_000},
    ),
    Persona(
        name="irregular", savings_target_pct=Decimal("15"), salary=None,
        freelance=(38_000, 92_000, 45_000, 70_000, 36_000, 88_000),
        spending={
            "Housing & Rent": Spend(15_000, 1, 0.0), "Groceries": Spend(6_000, 5, 0.15),
            "Utilities": Spend(2_500, 3, 0.10), "Transportation": Spend(3_000, 6, 0.25),
            "Education": Spend(1_000, 1, 0.0), "Food & Dining": Spend(5_000, 8, 0.35),
            "Shopping": Spend(3_000, 3, 0.50), "Entertainment": Spend(2_000, 2, 0.50),
            TRANSFERS_CATEGORY: Spend(1_500, 2, 0.40),
        },
        # A trip in May, a medical bill in June, festival shopping in August, another trip in September
        one_offs={1: [("Travel", 18_000)], 2: [("Healthcare", 9_500)], 4: [("Shopping", 14_000)],
                  5: [("Travel", 12_000)]},
        sip=2_000, broker_topups={1: 5_000, 4: 15_000},
    ),
)


def _split(total: int, parts: int, rng: random.Random) -> list[int]:
    """Splits a whole-rupee total into `parts` positive amounts that add up EXACTLY to it."""
    if parts <= 1:
        return [total]
    weights = [rng.uniform(0.5, 1.5) for _ in range(parts)]
    amounts = [max(1, int(total * w / sum(weights))) for w in weights[:-1]]
    return amounts + [total - sum(amounts)]


def _row(persona: str, month: str, day: int, description: str, amount: int,
         direction: str, txn_type: str, category: str) -> HistoryRow:
    year, mon = (int(part) for part in month.split("-"))
    return HistoryRow(persona, date(year, mon, day), description, Decimal(amount).quantize(Decimal("0.01")),
                      direction, txn_type, category)


def _persona_rows(persona: Persona, rng: random.Random) -> list[HistoryRow]:
    rows: list[HistoryRow] = []
    p = persona.name
    for index, month in enumerate(MONTHS):
        # --- income ---
        if persona.salary:
            rows.append(_row(p, month, 1, "NEFT-SYNTH EMPLOYER PAYROLL", persona.salary[index],
                             CREDIT, TXN_INCOME, SALARY_CATEGORY))
        if persona.freelance:
            for part in _split(persona.freelance[index], 2, rng):
                rows.append(_row(p, month, rng.randint(1, 28), "NEFT-SYNTH CLIENT INVOICE PAYMENT", part,
                                 CREDIT, TXN_INCOME, OTHER_INCOME_CATEGORY))

        # --- expenses (the only rows that count as spending) ---
        for category, spend in persona.spending.items():
            total = round(spend.base * (1 + rng.uniform(-spend.noise, spend.noise)))
            for part in _split(total, spend.count, rng):
                day = rng.randint(1, 5) if category == "Housing & Rent" else rng.randint(1, 28)
                rows.append(_row(p, month, day, rng.choice(MERCHANTS[category]), part,
                                 DEBIT, TXN_EXPENSE, category))
        for category, amount in persona.one_offs.get(index, []):
            rows.append(_row(p, month, rng.randint(1, 28), rng.choice(MERCHANTS[category]), amount,
                             DEBIT, TXN_EXPENSE, category))

        # --- NOT spending: the planner must ignore every row below ---
        if persona.sip:
            rows.append(_row(p, month, 5, "NACH/SYNTH MF SIP/FOLIO 0000001", persona.sip,
                             DEBIT, TXN_INVESTMENT_OUT, INVESTMENTS_CATEGORY))
        if index in persona.broker_topups:
            rows.append(_row(p, month, 15, "UPI/SYNTH BROKING/synthbroker@okaxis ADD FUNDS",
                             persona.broker_topups[index], DEBIT, TXN_INVESTMENT_OUT, INVESTMENTS_CATEGORY))
        if index in persona.self_transfers:
            rows.append(_row(p, month, 20, "IMPS/OWN ACCOUNT TRANSFER SELF", persona.self_transfers[index],
                             DEBIT, TXN_TRANSFER, TRANSFERS_CATEGORY))
        if index == 2:      # June: a refund (money back, not income) and quarterly interest
            rows.append(_row(p, month, 18, "REFUND SYNTH ONLINE MARKETPLACE", 1_200,
                             CREDIT, TXN_INCOME, REFUND_CATEGORY))
        if index in (2, 5):
            rows.append(_row(p, month, 30, "SB INT PD SYNTH BANK", 350 + 50 * index,
                             CREDIT, TXN_INCOME, INTEREST_CATEGORY))
        if index == 3:      # July: a mutual-fund redemption (money back, not income)
            rows.append(_row(p, month, 22, "SYNTH MF REDEMPTION PAYOUT", 6_000,
                             CREDIT, TXN_INVESTMENT_IN, INVESTMENT_REDEMPTION_CATEGORY))
    return rows


def generate_history(seed: int = DEFAULT_SEED) -> list[HistoryRow]:
    """All personas' rows, sorted by persona then date. Same seed -> same rows."""
    rng = random.Random(seed)      # local generator: never touches the global random state
    rows = [row for persona in PERSONAS for row in _persona_rows(persona, rng)]
    return sorted(rows, key=lambda r: (r.persona, r.day, r.direction, r.description, r.amount))


def write_csv(rows: list[HistoryRow], path: Path) -> None:
    """Writes the rows with '\n' line endings so the file is byte-identical on every OS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(FIELDS)
        for r in rows:
            writer.writerow([r.persona, r.day.isoformat(), r.description, f"{r.amount:.2f}",
                             r.direction, r.txn_type, r.category])


def read_csv(path: Path) -> list[HistoryRow]:
    """Reads a file written by write_csv back into HistoryRow objects."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            HistoryRow(r["persona"], date.fromisoformat(r["date"]), r["description"], Decimal(r["amount"]),
                       r["direction"], r["txn_type"], r["category"])
            for r in csv.DictReader(handle)
        ]


def persona_by_name(name: str) -> Persona:
    return next(p for p in PERSONAS if p.name == name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic 6-month budget history.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rows = generate_history(args.seed)
    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} rows for {len(PERSONAS)} personas x {len(MONTHS)} months to {args.out}")


if __name__ == "__main__":
    main()
