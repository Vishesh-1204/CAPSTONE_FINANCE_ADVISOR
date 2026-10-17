# app/services/statement_parsers/base.py
#
# Purpose: the common contract every statement parser follows (strategy pattern).
#
#   CsvParser / XlsxParser / PdfParser  --parse()-->  ParseResult(list[ParsedRow])
#
# After parsing, ONE shared pipeline (statement_import_service) validates,
# de-duplicates, balance-checks, saves and enriches the rows, so no file
# format duplicates that logic. Parsers are pure: no database, no HTTP, no LLM.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.core.transaction_types import CREDIT, DEBIT


class StatementParseError(Exception):
    """
    The file cannot be read as a statement. `status_code` tells the HTTP layer
    which error to return (400 bad file / wrong password, 422 scanned PDF ...).
    The message is shown to the user, so it must never contain the password.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ParsedRow:
    """
    One statement line, already validated: exactly one of debit/credit is > 0.
    `row_number` is the line/row in the source file (for error reports).
    """
    row_number: int
    date: datetime
    narration: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None = None
    ref: str | None = None
    balance_ok: bool | None = None    # set by the import pipeline's balance check

    @property
    def direction(self) -> str:
        return DEBIT if self.debit else CREDIT

    @property
    def amount(self) -> Decimal:
        return self.debit or self.credit  # type: ignore[return-value]


@dataclass
class ParseResult:
    """Everything a parser found in one file."""
    rows: list[ParsedRow]
    invalid_rows: int                 # transaction-like lines that could not be read
    layout: str                       # 'hdfc_like' | 'sbi_like' | 'generic'
    has_balance: bool                 # the file has a running-balance column
    opening_balance: Decimal | None = None   # from an "Opening Balance" line, if the file has one


class StatementParser(ABC):
    """Interface: turn the raw bytes of one statement file into ParsedRows."""

    source_type: str                  # 'csv' | 'xlsx' | 'pdf' (stored on the import batch)

    @abstractmethod
    def parse(self, file_bytes: bytes, password: str | None = None) -> ParseResult:
        """Raises StatementParseError when the file is unusable."""
        raise NotImplementedError
