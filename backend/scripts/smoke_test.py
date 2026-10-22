# backend/scripts/smoke_test.py
#
# Purpose: end-to-end SMOKE TEST of the running API (Phase 2 auth -> Phase 3
# import/categorize -> Phase 4 budgets). Unlike the pytest suite (SQLite + fake
# LLM, in-process), this talks to the REAL server over HTTP, with the real
# Postgres database and migrations, so it catches wiring problems the unit
# tests cannot see (missing router, broken migration, CORS-free HTTP errors...).
#
# Flow: health -> register a unique user -> log in -> upload a small CSV ->
# categorize -> save the financial profile -> income suggestion -> generate the
# budget for NEXT month -> status -> override one limit -> regenerate ->
# confirm the override survived.
#
# Synthetic data only: every merchant and amount below is made up, and every
# description matches a keyword rule, so categorization needs no LLM call.
# Each run creates a NEW user (smoke-<random>@example.com); its rows stay in
# the dev database, which is harmless test data.
#
# Standard library only (urllib), so it also runs in an image without dev extras.
#
# Run (inside Docker, from the repo root):
#   docker compose exec backend python -m scripts.smoke_test
#   docker compose exec backend python -m scripts.smoke_test --base-url http://127.0.0.1:8000
#
# Exit code: 0 when every step passes, 1 on the first failing step.

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT_SECONDS = 120        # categorize/generate may wait on a rate-limited LLM
MONTHLY_INCOME = 60_000
SAVINGS_TARGET_PCT = 20
OVERRIDE_CATEGORY = "Food & Dining"  # a discretionary category the CSV is sure to produce


class StepFailed(Exception):
    """Raised by a step to stop the run with a readable reason."""


@dataclass
class Response:
    """The parts of an HTTP response the steps look at."""
    status: int
    body: Any          # parsed JSON, or the raw text when it is not JSON


# --- Tiny HTTP client (urllib) ---

class ApiClient:
    """Sends requests to the API and remembers the bearer token after login."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        form: dict[str, str] | None = None,
        file: tuple[str, bytes, str] | None = None,
    ) -> Response:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers: dict[str, str] = {"Accept": "application/json"}
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif file is not None:
            data, headers["Content-Type"] = _multipart("file", *file)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                return Response(resp.status, _parse(resp.read()))
        except urllib.error.HTTPError as err:   # 4xx/5xx still carry a useful JSON body
            return Response(err.code, _parse(err.read()))


def _parse(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


def _multipart(field: str, filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    """Encodes one file as multipart/form-data (what FastAPI's UploadFile expects)."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def expect(response: Response, status: int, what: str) -> Any:
    """Fails the step unless the status code matches; returns the JSON body."""
    if response.status != status:
        raise StepFailed(f"{what}: expected HTTP {status}, got {response.status}: {str(response.body)[:300]}")
    return response.body


# --- Synthetic data ---

def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def shift(year: int, month: int, delta: int) -> tuple[int, int]:
    """(year, month) moved by `delta` months; same integer trick as core/month_utils.shift_month."""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def build_csv(now: datetime) -> bytes:
    """
    Three months of synthetic history: the two full months before this one and
    day 1 of this month. Those all fall in the lookback window of NEXT month's
    plan. Amounts differ a little per month so the averages are realistic.
    The SIP row is an investment: the planner must NOT count it as spending.
    """
    # (description, column, amount, varies): fixed bills stay the same, day-to-day spending varies
    template = [
        ("SALARY CREDIT ACME PAYROLL", "credit", MONTHLY_INCOME, False),
        ("HOUSE RENT PAYMENT FLAT", "debit", 15_000, False),
        ("BIGBASKET ORDER", "debit", 4_000, True),
        ("SWIGGY ORDER", "debit", 1_200, True),
        ("ZOMATO ORDER", "debit", 800, True),
        ("UBER RIDE", "debit", 600, True),
        ("ELECTRICITY BILL", "debit", 1_500, True),
        ("NETFLIX SUBSCRIPTION", "debit", 649, False),
        ("HDFC MF SIP", "debit", 5_000, False),
    ]
    rows = ["Date,Description,Debit,Credit"]
    for offset, factor in ((-2, 1.00), (-1, 1.05), (0, 0.95)):
        year, month = shift(now.year, now.month, offset)
        for index, (description, column, amount, varies) in enumerate(template):
            # The current month only uses day 1, so no row is ever dated in the future
            day = 1 if offset == 0 else index + 2
            value = f"{amount * factor if varies else amount:.2f}"
            debit, credit = (value, "") if column == "debit" else ("", value)
            rows.append(f"{year:04d}-{month:02d}-{day:02d},{description},{debit},{credit}")
    return ("\n".join(rows) + "\n").encode()


# --- The smoke test itself ---

class SmokeTest:
    """Runs the steps in order; each later step uses what earlier ones stored on `self`."""

    def __init__(self, base_url: str) -> None:
        self.api = ApiClient(base_url)
        now = datetime.now(timezone.utc)
        self.now = now
        self.target_month = month_key(*shift(now.year, now.month, 1))
        self.email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
        self.password = f"Smoke-{uuid.uuid4().hex[:12]}"
        self.override_id: int | None = None
        self.override_value: float | None = None
        self.regenerated: dict = {}

    def steps(self) -> list[tuple[str, Callable[[], str]]]:
        return [
            ("health", self.health),
            ("register unique user", self.register),
            ("login", self.login),
            ("upload CSV", self.upload),
            ("categorize", self.categorize),
            ("save financial profile", self.save_profile),
            ("income suggestion", self.income_suggestion),
            (f"generate budget {self.target_month}", self.generate),
            ("budget status", self.status),
            ("override one limit", self.override),
            ("regenerate budget", self.regenerate),
            ("override survived regeneration", self.override_survived),
        ]

    # Each step returns a short detail string for the PASS line, or raises StepFailed.

    def health(self) -> str:
        body = expect(self.api.request("GET", "/health"), 200, "GET /health")
        return f"status={body.get('status')}"

    def register(self) -> str:
        payload = {"email": self.email, "full_name": "Smoke Test User", "password": self.password}
        body = expect(self.api.request("POST", "/api/auth/register", json_body=payload), 201, "register")
        return f"user id={body['id']} {self.email}"

    def login(self) -> str:
        form = {"username": self.email, "password": self.password}
        body = expect(self.api.request("POST", "/api/auth/login", form=form), 200, "login")
        self.api.token = body["access_token"]
        return "got bearer token"

    def upload(self) -> str:
        file = ("smoke_statement.csv", build_csv(self.now), "text/csv")
        body = expect(self.api.request("POST", "/api/transactions/upload-csv", file=file), 201, "upload-csv")
        if body["imported"] != 27 or body["skipped_invalid"] != 0:
            raise StepFailed(f"expected 27 rows imported and 0 invalid, got {body}")
        return f"imported={body['imported']} batch={body.get('import_batch_id')}"

    def categorize(self) -> str:
        body = expect(self.api.request("POST", "/api/transactions/categorize"), 200, "categorize")
        if body["failed"] != 0:
            raise StepFailed(f"{body['failed']} transactions left uncategorized: {body}")
        return (f"processed={body['total_processed']} rules={body['categorized_by_rules']} "
                f"llm={body['categorized_by_llm']} needs_review={body['needs_review']}")

    def save_profile(self) -> str:
        payload = {"monthly_income": MONTHLY_INCOME, "savings_target_pct": SAVINGS_TARGET_PCT}
        body = expect(self.api.request("PUT", "/api/profile/financial", json_body=payload), 200, "save profile")
        expected_spendable = MONTHLY_INCOME * (1 - SAVINGS_TARGET_PCT / 100)
        if abs(body["spendable_income"] - expected_spendable) > 0.01:
            raise StepFailed(f"spendable {body['spendable_income']} != {expected_spendable}")
        return f"spendable={body['spendable_income']:.2f}"

    def income_suggestion(self) -> str:
        response = self.api.request("GET", "/api/profile/financial/suggestion", params={"month": self.target_month})
        body = expect(response, 200, "income suggestion")
        if body["suggested_monthly_income"] is None or abs(body["suggested_monthly_income"] - MONTHLY_INCOME) > 0.01:
            raise StepFailed(f"expected suggestion {MONTHLY_INCOME}, got {body['suggested_monthly_income']}")
        return f"suggested={body['suggested_monthly_income']:.2f} months_used={body['months_used']}"

    def _generate(self) -> dict:
        response = self.api.request("POST", "/api/budgets/generate", params={"month": self.target_month})
        body = expect(response, 200, "generate")
        total = sum(item["limit_amount"] for item in body["budgets"])
        if total > body["spendable_income"] + 0.001:
            raise StepFailed(f"limits sum to {total:.2f}, above spendable {body['spendable_income']:.2f}")
        if any(item["category"] == "Investments & Savings" for item in body["budgets"]):
            raise StepFailed("the SIP (an investment) was budgeted as spending")
        return body

    def generate(self) -> str:
        body = self._generate()
        if body["method"] != "history":
            raise StepFailed(f"expected a history plan from the uploaded months, got method={body['method']}")
        quality = body["data_quality"]
        if quality["months_used"] < 2:
            raise StepFailed(f"expected at least 2 months of history, got {quality}")
        if not any(item["category"] == OVERRIDE_CATEGORY for item in body["budgets"]):
            raise StepFailed(f"no {OVERRIDE_CATEGORY} limit in the plan")
        return (f"method={body['method']} limits={len(body['budgets'])} feasible={body['feasible']} "
                f"months_used={quality['months_used']} llm_used={body['llm_used']}")

    def status(self) -> str:
        response = self.api.request("GET", "/api/budgets/status", params={"month": self.target_month})
        body = expect(response, 200, "status")
        budgeted = [item for item in body["items"] if item["limit"] is not None]
        if not budgeted:
            raise StepFailed("status has no budgeted categories")
        # Nothing is spent yet in next month, so every budgeted category must be 'ok'
        not_ok = [item["category"] for item in budgeted if item["status"] != "ok"]
        if not_ok:
            raise StepFailed(f"expected every category 'ok' in an unspent month, got non-ok: {not_ok}")
        return f"categories={len(budgeted)} total_limit={body['total_limit']:.2f} total_spent={body['total_spent']:.2f}"

    def override(self) -> str:
        response = self.api.request("GET", "/api/budgets/", params={"month": self.target_month})
        items = expect(response, 200, "list budgets")
        row = next(item for item in items if item["category"] == OVERRIDE_CATEGORY)
        # A whole-rupee value clearly different from the agent's limit
        self.override_id = row["id"]
        self.override_value = float(max(1, int(row["agent_limit_amount"] // 2)))
        response = self.api.request("PUT", f"/api/budgets/{self.override_id}",
                                    json_body={"limit_amount": self.override_value})
        body = expect(response, 200, "override")
        if body["source"] != "user" or body["limit_amount"] != self.override_value:
            raise StepFailed(f"override not applied: {body}")
        return f"{OVERRIDE_CATEGORY}: agent {row['agent_limit_amount']:.2f} -> user {self.override_value:.2f}"

    def regenerate(self) -> str:
        body = self._generate()
        self.regenerated = body
        return f"limits={len(body['budgets'])} llm_used={body['llm_used']}"

    def override_survived(self) -> str:
        row = next((item for item in self.regenerated["budgets"] if item["category"] == OVERRIDE_CATEGORY), None)
        if row is None:
            raise StepFailed(f"{OVERRIDE_CATEGORY} disappeared after regeneration")
        if row["id"] != self.override_id or row["source"] != "user" or row["limit_amount"] != self.override_value:
            raise StepFailed(f"override lost: expected user limit {self.override_value} on id {self.override_id}, got {row}")
        return f"{OVERRIDE_CATEGORY} still {row['limit_amount']:.2f} (source=user)"

    def run(self) -> int:
        """Prints one PASS/FAIL line per step; stops at the first failure (later steps depend on it)."""
        print(f"Smoke test against {self.api.base_url} (budget month {self.target_month})")
        steps = self.steps()
        for number, (name, step) in enumerate(steps, start=1):
            label = f"[{number:02d}/{len(steps)}] {name}"
            try:
                detail = step()
            except StepFailed as err:
                print(f"FAIL {label}: {err}")
                return 1
            except urllib.error.URLError as err:
                print(f"FAIL {label}: cannot reach the API ({err.reason}). Is `docker compose up` running?")
                return 1
            except Exception as err:  # a bug or an unexpected response shape: still report, never crash silently
                print(f"FAIL {label}: unexpected {type(err).__name__}: {err}")
                return 1
            print(f"PASS {label}: {detail}")
        print(f"ALL {len(steps)} STEPS PASSED")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end smoke test of the running API (synthetic data).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API root (default {DEFAULT_BASE_URL})")
    args = parser.parse_args()
    sys.exit(SmokeTest(args.base_url).run())


if __name__ == "__main__":
    main()
