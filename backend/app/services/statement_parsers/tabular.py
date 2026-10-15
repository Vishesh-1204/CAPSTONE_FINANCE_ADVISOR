# app/services/statement_parsers/tabular.py
#
# Purpose: the logic SHARED by every parser once a file has been turned into
# a grid of cells (list of rows, each a list of cell values):
#
#   1. find the header row (it is often not the first line: banks add
#      account details above the table) and map its columns
#   2. turn every following row into a validated ParsedRow, skipping repeated
#      page headers and footers, and joining multi-line narrations
#
# CSV, XLSX and PDF parsers differ only in how they produce the grid.

import re
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.money import to_money
from app.services.statement_parsers.base import ParsedRow, ParseResult, StatementParseError

MAX_ROWS = 5000
HEADER_SCAN_ROWS = 30         # look for the header within the first N rows

# canonical column -> accepted header spellings (compared after cleaning, see _clean_header)
HEADER_ALIASES: dict[str, set[str]] = {
    "description": {
        "description", "narration", "details", "particulars", "remarks", "transaction details",
        "transaction remarks", "transaction description",
    },
    "amount": {"amount", "amount inr", "transaction amount", "txn amount"},
    "debit": {"debit", "debit amount", "withdrawal", "withdrawals", "withdrawal amt", "withdrawal amount", "dr", "debit inr"},
    "credit": {"credit", "credit amount", "deposit", "deposits", "deposit amt", "deposit amount", "cr", "credit inr"},
    "date": {"date", "txn date", "transaction date", "tran date", "value date", "value dt", "posting date"},
    "balance": {
        "balance", "closing balance", "balance amt", "balance amount", "running balance",
        "available balance", "balance inr",
    },
    "ref": {
        "ref", "ref no", "reference", "reference no", "chq ref no", "chq no", "cheque no",
        "ref no cheque no", "utr", "utr no", "transaction id",
    },
    "drcr": {"dr cr", "cr dr", "debit credit", "txn type", "transaction type"},
}

DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y",
    "%d %b %y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
)

# Lines that are statement furniture, not transactions or narration continuations
_FOOTER = re.compile(
    r"(?i)^\s*(page\s*\d+(\s*of\s*\d+)?|statement\s+summary|opening\s+balance|closing\s+balance|"
    r"total|grand\s+total|end\s+of\s+statement|this\s+is\s+a\s+computer|generated\s+on)\b"
)
_OPENING_BALANCE = re.compile(r"(?i)^\s*(opening|brought\s+forward|b/f)\b")
_AMOUNT_NOISE = re.compile(r"(?i)(rs\.?|inr|₹|,|\s)")
_INDICATOR = re.compile(r"(?i)\s*\(?(dr|cr)\)?\.?\s*$")


def _clean_header(value: object) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower())
    return " ".join(text.split())


def map_headers(cells: list[object]) -> dict[str, int]:
    """canonical column -> position in the row. The first matching column wins."""
    mapping: dict[str, int] = {}
    for position, cell in enumerate(cells):
        cleaned = _clean_header(cell)
        for canonical, aliases in HEADER_ALIASES.items():
            if cleaned in aliases and canonical not in mapping:
                mapping[canonical] = position
    return mapping


def is_header(mapping: dict[str, int]) -> bool:
    """A usable header has a description, a date and at least one money column."""
    return (
        "description" in mapping and "date" in mapping
        and any(c in mapping for c in ("amount", "debit", "credit"))
    )


def find_header(grid: list[list[object]]) -> tuple[int, dict[str, int]]:
    """Index and column mapping of the header row. Raises StatementParseError if none is found."""
    for index, cells in enumerate(grid[:HEADER_SCAN_ROWS]):
        mapping = map_headers(cells)
        if is_header(mapping):
            return index, mapping
    raise StatementParseError(
        "Could not find the transaction table header. The file needs a description/narration column, "
        "a date column and an amount column (amount, or debit/withdrawal and credit/deposit)."
    )


def detect_layout(header_cells: list[object]) -> str:
    """Label used for parsing metrics. Mapping itself is always alias-based (works for any bank)."""
    names = {_clean_header(c) for c in header_cells}
    if {"narration", "closing balance"} <= names and any(n.startswith("withdrawal") for n in names):
        return "hdfc_like"
    if {"txn date", "description", "debit", "credit", "balance"} <= names:
        return "sbi_like"
    return "generic"


def _text(value: object) -> str:
    """Cell -> single-line text ('' for empty). Newlines inside a cell become spaces."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def parse_money(value: object) -> tuple[Decimal | None, str | None]:
    """
    '1,234.50' / 'Rs. 1,234.50' / '₹500' / '450.00 Dr' / 1234.5 -> (Decimal, 'dr'|'cr'|None).
    Empty or '-' -> (None, None). Raises ValueError for anything unreadable.
    """
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float, Decimal)):
        return to_money(value), None
    text = _text(value)
    indicator = None
    found = _INDICATOR.search(text)
    if found:
        indicator = found.group(1).lower()
        text = text[: found.start()]
    cleaned = _AMOUNT_NOISE.sub("", text)
    if cleaned in ("", "-", "--"):
        return None, indicator
    return to_money(cleaned), indicator


def parse_date(value: object) -> datetime:
    """Date cell -> timezone-aware UTC datetime. Slashed dates are DAY/MONTH/YEAR (Indian banks)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = _text(value)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{text}'")


def _cell(cells: list[object], columns: dict[str, int], name: str) -> object:
    position = columns.get(name)
    if position is None or position >= len(cells):
        return None
    return cells[position]


def _debit_credit(cells: list[object], columns: dict[str, int]) -> tuple[Decimal | None, Decimal | None]:
    """Reads the money columns of one row and decides which side it is on."""
    if "debit" in columns or "credit" in columns:
        debit, _ = parse_money(_cell(cells, columns, "debit"))
        credit, _ = parse_money(_cell(cells, columns, "credit"))
        return debit, credit

    amount, indicator = parse_money(_cell(cells, columns, "amount"))
    if amount is None:
        return None, None
    column_indicator = _text(_cell(cells, columns, "drcr")).lower()
    if column_indicator.startswith(("cr", "c")):
        indicator = "cr"
    elif column_indicator.startswith(("dr", "d")):
        indicator = "dr"
    if indicator == "cr" or (indicator is None and amount < 0):
        return None, abs(amount)
    return abs(amount), None


_GLUE_CHARS = "/@-._"


def join_narration(previous: str, continuation: str) -> str:
    """
    Joins a wrapped narration line onto the previous one. Bank text such as
    UPI/412345678901/SWIGGY/swiggy@icici is often cut mid-token, so when either
    side of the cut is a separator (/ @ - . _) the pieces are glued WITHOUT a
    space; otherwise it was a normal word wrap and a space is inserted.
    """
    if previous.endswith(tuple(_GLUE_CHARS)) or continuation.startswith(tuple(_GLUE_CHARS)):
        return f"{previous}{continuation}"[:500]
    return f"{previous} {continuation}"[:500]


def _row_is_empty(cells: list[object]) -> bool:
    return all(_text(c) == "" for c in cells)


def assemble_rows(grid: list[list[object]], first_row_number: int = 1) -> ParseResult:
    """
    The shared heart of every parser: grid of cells -> validated ParsedRows.
      * rows before the header and the header itself are skipped
      * a later row that is the header again (next PDF page) is skipped
      * footers ("Page 2 of 3", "Closing balance ...") are skipped
      * a row with no date and no amounts but some text CONTINUES the previous
        narration (multi-line narrations in PDFs)
      * anything else that cannot be read counts as invalid (never fatal)
    `first_row_number` is the human row number of grid[0] (1-based).
    """
    header_index, columns = find_header(grid)
    header = grid[header_index]
    rows: list[ParsedRow] = []
    invalid = 0
    opening_balance: Decimal | None = None

    for offset, cells in enumerate(grid[header_index + 1:], start=header_index + 1):
        row_number = first_row_number + offset
        if _row_is_empty(cells):
            continue
        if is_header(map_headers(cells)):
            continue                                          # repeated header on a new page
        narration = _text(_cell(cells, columns, "description"))
        date_text = _text(_cell(cells, columns, "date"))
        has_money = any(_text(_cell(cells, columns, c)) for c in ("amount", "debit", "credit"))
        first_text = next((_text(c) for c in cells if _text(c)), "")
        looks_like_footer = bool(_FOOTER.match(first_text) or _FOOTER.match(narration))

        if _OPENING_BALANCE.match(narration) and not has_money:
            # "Opening Balance ... 10,000.00": not a transaction, but it lets us check row 1's balance
            if opening_balance is None:
                try:
                    opening_balance, _ = parse_money(_cell(cells, columns, "balance"))
                except ValueError:
                    pass
            continue
        if looks_like_footer and not has_money:
            continue                                          # e.g. "30-09-2026 Closing Balance 12,000.00"
        if not date_text and (looks_like_footer or not has_money):
            # Footer lines are skipped. Other dateless, amountless text CONTINUES the
            # previous narration (a long PDF narration wrapped onto the next line).
            if not looks_like_footer and narration and rows:
                rows[-1].narration = join_narration(rows[-1].narration, narration)
            continue

        if len(rows) + invalid >= MAX_ROWS:
            raise StatementParseError(f"Too many rows (limit {MAX_ROWS}). Split the file.")
        try:
            if not narration:
                raise ValueError("empty description")
            when = parse_date(_cell(cells, columns, "date"))
            debit, credit = _debit_credit(cells, columns)
            debit = debit if debit and debit > 0 else None
            credit = credit if credit and credit > 0 else None
            if (debit is None) == (credit is None):
                raise ValueError("need exactly one of debit/credit greater than zero")
            balance, balance_side = parse_money(_cell(cells, columns, "balance"))
            if balance is not None and balance_side == "dr":
                balance = -balance                            # overdrawn balance printed as "500.00 Dr"
            rows.append(ParsedRow(
                row_number=row_number, date=when, narration=narration[:500], debit=debit, credit=credit,
                balance=balance, ref=_text(_cell(cells, columns, "ref")) or None,
            ))
        except ValueError:
            invalid += 1                                      # the reason is NOT logged: it may contain personal text

    return ParseResult(
        rows=rows, invalid_rows=invalid, layout=detect_layout(header),
        has_balance="balance" in columns, opening_balance=opening_balance,
    )
