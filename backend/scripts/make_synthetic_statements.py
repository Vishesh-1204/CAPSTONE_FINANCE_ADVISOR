# backend/scripts/make_synthetic_statements.py
#
# Purpose: generates SYNTHETIC bank statements for tests and evaluation.
# Every name, UPI ID, account and reference number is made up.
#
#   hdfc_like.pdf      ruled table, 2 pages, repeated header, footers, wrapped narrations
#   sbi_like.pdf       unruled columns, opening balance line, narrations continued on the
#                      next line, ONE planted balance error (printed amount != balance move)
#   protected.pdf      hdfc-like, password "test1234"
#   scanned.pdf        drawings only, no text layer (what a scanned image looks like)
#   statement.xlsx     preamble rows, real date and number cells
#   messy.csv          preamble, Dr/Cr column, "Rs." amounts, one bad row, footers
#   ground_truth.json  expected rows / categories / txn types per file (used by tests and S6)
#
# Needs the dev requirements (reportlab). Run inside Docker:
#   docker compose exec backend python -m scripts.make_synthetic_statements

import csv
import io
import json
import re
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from reportlab import rl_config

rl_config.invariant = 1          # byte-stable PDFs (no timestamps / random ids): clean git diffs

from reportlab.lib import colors                                   # noqa: E402
from reportlab.lib.pagesizes import A4                             # noqa: E402
from reportlab.lib.pdfencrypt import StandardEncryption            # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet               # noqa: E402
from reportlab.lib.units import mm                                 # noqa: E402
from reportlab.pdfgen import canvas                                # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "statements"
PDF_PASSWORD = "test1234"
OPENING_BALANCE = Decimal("25000.00")


@dataclass(frozen=True)
class Txn:
    """One synthetic transaction plus its ground-truth labels."""
    day: int
    narration: str
    debit: Decimal | None
    credit: Decimal | None
    category: str
    txn_type: str
    is_person: bool = False


def d(value: str) -> Decimal:
    return Decimal(value)


# September 2026. Labels are what a careful human would choose.
TRANSACTIONS: list[Txn] = [
    Txn(1, "UPI/412345678901/SWIGGY/swiggy@icici", d("450.00"), None, "Food & Dining", "expense"),
    Txn(2, "NEFT-ACME TECH PAYROLL SEP 2026", None, d("85000.00"), "Salary", "income"),
    Txn(3, "NACH/HDFC MF SIP/FOLIO 1234567", d("5000.00"), None, "Investments & Savings", "investment_out"),
    Txn(4, "UPI/412345678902/RAHUL KUMAR/9876543210@ybl", d("1200.00"), None, "Transfers", "expense", True),
    Txn(5, "POS 4829 BIGBASKET BANGALORE", d("2340.50"), None, "Groceries", "expense"),
    Txn(6, "UPI/412345678903/ZERODHA BROKING/zerodha.broking@hdfcbank ADD FUNDS FOR EQUITY TRADING ACCOUNT",
        d("10000.00"), None, "Investments & Savings", "investment_out"),
    Txn(7, "SB INT PD 01-06-2026 TO 31-08-2026", None, d("312.40"), "Interest", "income"),
    Txn(8, "ATW-512345XXXXXX1234-MG ROAD BANGALORE", d("3000.00"), None, "Other", "expense"),
    Txn(10, "UPI/412345678904/NETFLIX/netflix@icici", d("649.00"), None, "Subscriptions", "expense"),
    Txn(12, "ACH C- ACME INDUSTRIES DIVIDEND 2026", None, d("1500.00"), "Dividend", "income"),
    Txn(14, "UPI/412345678905/GROWW/groww.payout@axisbank REDEMPTION", None, d("4200.00"),
        "Investment Redemption", "investment_in"),
    Txn(15, "IMPS/P2A/412345678906/OWN ACCOUNT TRANSFER SELF", d("20000.00"), None, "Transfers", "transfer"),
    Txn(17, "UPI/412345678907/PRIYA SHARMA/priya.s@oksbi", d("750.00"), None, "Transfers", "expense", True),
    Txn(18, "ELECTRICITY BILL CITY POWER SEP", d("1890.00"), None, "Utilities", "expense"),
    Txn(20, "UPI/412345678908/SHARMA SWEETS/sharmasweets@ybl", d("380.00"), None, "Food & Dining", "expense"),
    Txn(21, "UPI/412345678909/UBER INDIA/uber@axisbank", d("312.00"), None, "Transportation", "expense"),
    Txn(22, "UPI/412345678910/ZOMATO/zomato@hdfcbank REFUND", None, d("220.00"), "Refund & Cashback", "income"),
    Txn(24, "APOLLO PHARMACY KORAMANGALA", d("780.00"), None, "Healthcare", "expense"),
    Txn(25, "MONTHLY HOUSE RENT SEP", d("18000.00"), None, "Housing & Rent", "expense"),
    Txn(26, "AIRTEL BROADBAND BILL", d("1179.00"), None, "Utilities", "expense"),
    Txn(27, "UPI/412345678911/ANIL MEHTA/anil.mehta@okaxis", d("2500.00"), None, "Transfers", "expense", True),
    Txn(28, "BOOKMYSHOW MOVIE TICKETS", d("600.00"), None, "Entertainment", "expense"),
    Txn(29, "LIC PREMIUM POLICY 99887766", d("4500.00"), None, "Insurance", "expense"),
    Txn(30, "UDEMY COURSE PYTHON", d("499.00"), None, "Education", "expense"),
]

# In sbi_like.pdf this row's PRINTED amount is wrong by 90 (the balance moves by
# the true amount), simulating a misread / misprinted value: exactly one mismatch.
PLANTED_ERROR_DAY = 18
PLANTED_ERROR_DELTA = Decimal("90.00")


def balances(rows: list[Txn]) -> list[Decimal]:
    running, out = OPENING_BALANCE, []
    for row in rows:
        running = running - (row.debit or 0) + (row.credit or 0)
        out.append(running)
    return out


def money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:,.2f}"


# ------------------------------------------------------------------ HDFC-like (ruled)


def _hdfc_story(rows: list[Txn]) -> list:
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=7, leading=8)
    data: list[list] = [["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]]
    for index, (row, balance) in enumerate(zip(rows, balances(rows)), start=1):
        when = date(2026, 9, row.day).strftime("%d/%m/%y")
        data.append([
            when, Paragraph(row.narration, small), f"{index:016d}", when,
            money(row.debit), money(row.credit), money(balance),
        ])
    table = Table(data, colWidths=[16 * mm, 62 * mm, 28 * mm, 16 * mm, 22 * mm, 22 * mm, 24 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),          # tall rows, like real statements:
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),       # the table must spill onto page 2
    ]))
    header = Paragraph(
        "SYNTHETIC BANK LTD - Statement of account<br/>Account holder: TEST USER<br/>"
        f"Account No: XXXXXXXX1234 &nbsp; Period: 01/09/2026 to 30/09/2026 &nbsp; Opening balance: {money(OPENING_BALANCE)}",
        styles["BodyText"],
    )
    # A tall account-details block pushes the table across the page break, so
    # page 2 starts with a REPEATED header row (repeatRows=1) - what the parser must skip.
    return [header, Spacer(1, 90 * mm), table]


def _page_footer(pdf_canvas, doc) -> None:
    pdf_canvas.setFont("Helvetica", 7)
    pdf_canvas.drawString(15 * mm, 10 * mm, f"Page {doc.page}  |  This is a computer generated statement")


def make_hdfc_pdf(path: Path, encrypt: StandardEncryption | None = None) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=20 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm, encrypt=encrypt,
                            title="Synthetic statement", author="test")
    doc.build(_hdfc_story(TRANSACTIONS), onFirstPage=_page_footer, onLaterPages=_page_footer)


# ------------------------------------------------------------------ SBI-like (unruled)

SBI_COLUMNS = [  # (header, x position in points, alignment)
    ("Txn Date", 30, "left"), ("Value Date", 95, "left"), ("Description", 160, "left"),
    ("Ref No./Cheque No.", 345, "left"), ("Debit", 470, "right"), ("Credit", 525, "right"), ("Balance", 585, "right"),
]
SBI_WRAP = 30          # characters of description per line
SBI_ROWS_PER_PAGE = 14


def _sbi_header(pdf_canvas, y: float) -> None:
    pdf_canvas.setFont("Helvetica-Bold", 8)
    for title, x, align in SBI_COLUMNS:
        (pdf_canvas.drawRightString if align == "right" else pdf_canvas.drawString)(x, y, title)


def make_sbi_pdf(path: Path) -> None:
    pdf_canvas = canvas.Canvas(str(path), pagesize=A4)
    pdf_canvas.setTitle("Synthetic statement")
    width, height = A4
    pages = [TRANSACTIONS[i:i + SBI_ROWS_PER_PAGE] for i in range(0, len(TRANSACTIONS), SBI_ROWS_PER_PAGE)]
    running = balances(TRANSACTIONS)
    index = 0
    for page_number, page_rows in enumerate(pages, start=1):
        y = height - 40
        pdf_canvas.setFont("Helvetica", 9)
        if page_number == 1:
            for line in ("SYNTHETIC STATE BANK", "Account Name: TEST USER", "Account Number: XXXXXXX5678"):
                pdf_canvas.drawString(30, y, line)
                y -= 14
            y -= 10
        _sbi_header(pdf_canvas, y)
        y -= 16
        pdf_canvas.setFont("Helvetica", 8)
        if page_number == 1:
            pdf_canvas.drawString(160, y, "Opening Balance")
            pdf_canvas.drawRightString(585, y, money(OPENING_BALANCE))
            y -= 14
        for row in page_rows:
            when = date(2026, 9, row.day).strftime("%d %b %Y")
            debit = row.debit
            if row.day == PLANTED_ERROR_DAY and debit is not None:
                debit = debit + PLANTED_ERROR_DELTA            # printed wrongly; balance stays correct
            # Wrap like a bank: at spaces where possible, mid-token for long UPI strings
            chunks = textwrap.wrap(row.narration, SBI_WRAP, break_long_words=True, break_on_hyphens=False)
            pdf_canvas.drawString(30, y, when)
            pdf_canvas.drawString(95, y, when)
            pdf_canvas.drawString(160, y, chunks[0])
            pdf_canvas.drawString(345, y, f"REF{index + 1:08d}")
            pdf_canvas.drawRightString(470, y, money(debit))
            pdf_canvas.drawRightString(525, y, money(row.credit))
            pdf_canvas.drawRightString(585, y, money(running[index]))
            y -= 12
            for chunk in chunks[1:]:                          # wrapped narration: no date, no amounts
                pdf_canvas.drawString(160, y, chunk)
                y -= 12
            y -= 4
            index += 1
        pdf_canvas.setFont("Helvetica", 7)
        pdf_canvas.drawString(30, 30, f"Page {page_number} of {len(pages)}")
        pdf_canvas.showPage()
    pdf_canvas.save()


# ------------------------------------------------------------------ scanned, XLSX, CSV


def make_scanned_pdf(path: Path) -> None:
    """Only shapes, no text layer: exactly what pdfplumber sees in a scanned statement."""
    pdf_canvas = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    pdf_canvas.setFillColor(colors.lightgrey)
    for line in range(30):
        y = height - 60 - line * 22
        pdf_canvas.rect(30, y, 140 + (line * 37) % 300, 9, stroke=0, fill=1)   # "text-like" blocks
    pdf_canvas.showPage()
    pdf_canvas.save()


def make_xlsx(path: Path) -> None:
    workbook = Workbook()
    workbook.properties.creator = "test"
    workbook.properties.created = datetime(2026, 10, 1)
    workbook.properties.modified = datetime(2026, 10, 1)
    sheet = workbook.active
    sheet.title = "Statement"
    sheet.append(["SYNTHETIC BANK LTD"])
    sheet.append(["Account holder", "TEST USER"])
    sheet.append([])
    sheet.append(["Transaction Date", "Description", "Debit", "Credit", "Balance"])
    for row, balance in zip(TRANSACTIONS, balances(TRANSACTIONS)):
        sheet.append([
            datetime(2026, 9, row.day), row.narration,
            float(row.debit) if row.debit else None, float(row.credit) if row.credit else None, float(balance),
        ])
    buffer = io.BytesIO()
    workbook.save(buffer)
    _write_stable_zip(buffer.getvalue(), path)


def _write_stable_zip(xlsx_bytes: bytes, path: Path) -> None:
    """An .xlsx is a zip that stores the current time per entry; pin it so reruns are byte-identical."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            pinned = zipfile.ZipInfo(item.filename, date_time=(2026, 10, 1, 0, 0, 0))
            pinned.compress_type = zipfile.ZIP_DEFLATED
            content = source.read(item.filename)
            if item.filename == "docProps/core.xml":   # openpyxl always stamps "modified = now" on save
                content = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-10-01T00:00:00Z", content)
            target.writestr(pinned, content)
    path.write_bytes(out.getvalue())


MESSY_CSV_BAD_ROWS = 1     # one row with an unreadable date


def make_messy_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["SYNTHETIC BANK LTD - account statement"])
        writer.writerow(["Account holder: TEST USER"])
        writer.writerow([])
        writer.writerow(["Date", "Particulars", "Amount", "Dr/Cr", "Balance"])
        for row, balance in zip(TRANSACTIONS, balances(TRANSACTIONS)):
            amount = row.debit or row.credit
            writer.writerow([
                date(2026, 9, row.day).strftime("%d/%m/%Y"), row.narration, f"Rs. {money(amount)}",
                "DR" if row.debit else "CR", money(balance),
            ])
        writer.writerow(["31/31/2026", "BROKEN ROW WITH BAD DATE", "Rs. 10.00", "DR", ""])
        writer.writerow([])
        writer.writerow(["Page 1 of 1"])
        writer.writerow(["Closing Balance", "", "", "", money(balances(TRANSACTIONS)[-1])])


# ------------------------------------------------------------------ ground truth


def ground_truth() -> dict:
    rows = [
        {
            "date": f"2026-09-{t.day:02d}", "narration": t.narration,
            "direction": "debit" if t.debit else "credit", "amount": str(t.debit or t.credit),
            "category": t.category, "txn_type": t.txn_type, "is_person": t.is_person,
        }
        for t in TRANSACTIONS
    ]
    count = len(TRANSACTIONS)
    return {
        "transactions": rows,
        "files": {
            "hdfc_like.pdf": {"layout": "hdfc_like", "rows": count, "invalid": 0, "balance_mismatches": 0},
            "sbi_like.pdf": {"layout": "sbi_like", "rows": count, "invalid": 0, "balance_mismatches": 1,
                             "mismatch_day": PLANTED_ERROR_DAY},
            "protected.pdf": {"layout": "hdfc_like", "rows": count, "invalid": 0, "balance_mismatches": 0,
                              "password": PDF_PASSWORD},
            "scanned.pdf": {"expect_error": 422},
            "statement.xlsx": {"layout": "generic", "rows": count, "invalid": 0, "balance_mismatches": 0},
            "messy.csv": {"layout": "generic", "rows": count, "invalid": MESSY_CSV_BAD_ROWS, "balance_mismatches": 0},
        },
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_hdfc_pdf(OUT_DIR / "hdfc_like.pdf")
    make_sbi_pdf(OUT_DIR / "sbi_like.pdf")
    make_hdfc_pdf(OUT_DIR / "protected.pdf", encrypt=StandardEncryption(PDF_PASSWORD, canPrint=1, strength=128))
    make_scanned_pdf(OUT_DIR / "scanned.pdf")
    make_xlsx(OUT_DIR / "statement.xlsx")
    make_messy_csv(OUT_DIR / "messy.csv")
    (OUT_DIR / "ground_truth.json").write_text(json.dumps(ground_truth(), indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic statements written to {OUT_DIR}")


if __name__ == "__main__":
    main()
