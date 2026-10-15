# app/services/statement_parsers/pdf_parser.py
#
# Purpose: DIGITAL bank-statement PDFs -> ParsedRows, 100% locally (pdfplumber).
#
# Steps:
#   1. open (with the password if the PDF is protected). The password lives only
#      in this function call: never stored, logged or put in an error message.
#   2. no extractable text on any page -> it is a scanned image -> HTTP 422
#   3. build a grid of cells, page by page:
#        a. ruled tables (grid lines, HDFC-like): pdfplumber's table extractor
#        b. unruled tables (SBI-like): find the header line, use the header
#           words' x-positions as column bands, drop every word into its band
#   4. tabular.assemble_rows: header mapping, repeated headers, footers,
#      multi-line narrations, validation (shared with CSV and XLSX)

import io
import logging
from dataclasses import dataclass

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from app.services.statement_parsers.base import ParseResult, StatementParser, StatementParseError
from app.services.statement_parsers.tabular import assemble_rows, is_header, map_headers

logger = logging.getLogger(__name__)

MAX_PAGES = 200
SCANNED_PDF_MESSAGE = "This looks like a scanned PDF. Scanned statements are not supported yet."
LINE_TOLERANCE = 3.0       # words whose tops differ by less than this (points) are on one line
CELL_GAP = 7.0             # a horizontal gap wider than this starts a new cell


@dataclass
class _Cell:
    """A run of words on one line that belong together, with its horizontal extent."""
    text: str
    x0: float
    x1: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


def _is_password_error(exc: BaseException) -> bool:
    """pdfplumber wraps pdfminer errors; walk the chain looking for 'wrong/missing password'."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, PDFPasswordIncorrect) or any(isinstance(a, PDFPasswordIncorrect) for a in seen.args):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _lines_of_cells(page) -> list[list[_Cell]]:
    """Groups a page's words into lines (by vertical position), then into cells (by horizontal gaps)."""
    words = sorted(page.extract_words(x_tolerance=1.5, y_tolerance=2), key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[list[dict]] = []
    for word in words:
        if lines and abs(lines[-1][0]["top"] - word["top"]) <= LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])

    result: list[list[_Cell]] = []
    for line in lines:
        cells: list[_Cell] = []
        for word in sorted(line, key=lambda w: w["x0"]):
            if cells and word["x0"] - cells[-1].x1 <= CELL_GAP:
                cells[-1].text += " " + word["text"]
                cells[-1].x1 = word["x1"]
            else:
                cells.append(_Cell(word["text"], word["x0"], word["x1"]))
        result.append(cells)
    return result


def _band_edges(header: list[_Cell]) -> list[float]:
    """Column boundaries: halfway between one header cell's end and the next one's start."""
    return [(left.x1 + right.x0) / 2 for left, right in zip(header, header[1:])]


def _to_row(cells: list[_Cell], edges: list[float], width: int) -> list[object]:
    """Places each cell into the column band that contains its centre."""
    row: list[str] = [""] * width
    for cell in cells:
        column = sum(1 for edge in edges if cell.center > edge)
        row[column] = f"{row[column]} {cell.text}".strip()
    return row


def _grid_from_words(pages) -> list[list[object]]:
    """Unruled layout: column bands come from the header line, which may repeat on every page."""
    grid: list[list[object]] = []
    edges: list[float] | None = None
    width = 0
    for page in pages:
        for cells in _lines_of_cells(page):
            texts = [c.text for c in cells]
            if is_header(map_headers(texts)):
                edges, width = _band_edges(cells), len(cells)
                grid.append(texts)
            elif edges is not None:
                grid.append(_to_row(cells, edges, width))
    return grid


def _grid_from_tables(pages) -> list[list[object]]:
    """Ruled layout: every table row on every page, in order."""
    grid: list[list[object]] = []
    for page in pages:
        for table in page.extract_tables():
            grid.extend(table)
    return grid


def _has_header(grid: list[list[object]]) -> bool:
    return any(is_header(map_headers(row)) for row in grid[:50])


class PdfParser(StatementParser):
    """Digital (text) PDF statements; ruled or unruled tables; optional password."""

    source_type = "pdf"

    def parse(self, file_bytes: bytes, password: str | None = None) -> ParseResult:
        try:
            pdf = pdfplumber.open(io.BytesIO(file_bytes), password=password or "")
            pages = pdf.pages               # forces the document (and the password check) to load
        except Exception as exc:  # pdfplumber/pdfminer raise many different types
            if _is_password_error(exc):
                if password:
                    raise StatementParseError("The PDF password is incorrect.") from None
                raise StatementParseError(
                    "This PDF is password-protected. Enter its password in the password field."
                ) from None
            logger.warning(f"Unreadable PDF upload ({type(exc).__name__})")
            raise StatementParseError("This is not a readable PDF file.") from None

        try:
            if len(pages) > MAX_PAGES:
                raise StatementParseError(f"Too many pages (limit {MAX_PAGES}). Split the statement.")
            if not any((page.extract_text() or "").strip() for page in pages):
                raise StatementParseError(SCANNED_PDF_MESSAGE, status_code=422)

            grid = _grid_from_tables(pages)
            strategy = "tables"
            if not _has_header(grid):
                grid = _grid_from_words(pages)
                strategy = "words"
            logger.info(f"PDF parsed: {len(pages)} page(s), strategy={strategy}, grid rows={len(grid)}")
            return assemble_rows(grid, first_row_number=1)
        finally:
            pdf.close()
