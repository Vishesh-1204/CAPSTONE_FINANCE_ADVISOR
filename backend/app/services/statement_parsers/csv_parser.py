# app/services/statement_parsers/csv_parser.py
#
# Purpose: CSV bank exports -> ParsedRows. Only CSV-specific work happens here
# (decoding the bytes, guessing the delimiter); header detection, amounts,
# dates, multi-line narrations and footers are handled by tabular.assemble_rows.

import csv
import io

from app.services.statement_parsers.base import ParseResult, StatementParser, StatementParseError
from app.services.statement_parsers.tabular import assemble_rows


def _decode(file_bytes: bytes) -> str:
    try:
        return file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_bytes.decode("cp1252", errors="replace")    # common for Excel-exported CSVs


class CsvParser(StatementParser):
    """Comma, semicolon or tab separated statements, with or without preamble lines."""

    source_type = "csv"

    def parse(self, file_bytes: bytes, password: str | None = None) -> ParseResult:
        text = _decode(file_bytes)
        if not text.strip():
            raise StatementParseError("The file appears to be empty.")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        grid: list[list[object]] = [list(row) for row in csv.reader(io.StringIO(text), dialect=dialect)]
        return assemble_rows(grid, first_row_number=1)
