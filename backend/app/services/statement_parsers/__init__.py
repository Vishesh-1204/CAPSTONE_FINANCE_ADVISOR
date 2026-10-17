# app/services/statement_parsers/__init__.py
#
# Purpose: picks the right parser for an uploaded file (by extension).
# Adding a format = write one StatementParser subclass and register it here.

from pathlib import PurePath

from app.services.statement_parsers.base import ParsedRow, ParseResult, StatementParser, StatementParseError
from app.services.statement_parsers.csv_parser import CsvParser
from app.services.statement_parsers.pdf_parser import PdfParser
from app.services.statement_parsers.xlsx_parser import XlsxParser

PARSERS: dict[str, type[StatementParser]] = {
    ".csv": CsvParser,
    ".xlsx": XlsxParser,
    ".pdf": PdfParser,
}


def get_parser(filename: str) -> StatementParser:
    """Parser for this file name. Raises StatementParseError for unsupported types."""
    extension = PurePath(filename or "").suffix.lower()
    parser_class = PARSERS.get(extension)
    if parser_class is None:
        supported = ", ".join(sorted(PARSERS))
        raise StatementParseError(f"Unsupported file type. Upload one of: {supported}")
    return parser_class()


__all__ = ["ParsedRow", "ParseResult", "StatementParser", "StatementParseError", "get_parser", "PARSERS"]
