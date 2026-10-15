# tests/test_statements_api.py
#
# HTTP-level tests for statement upload, import batches and the old
# /upload-csv alias. Login is bypassed (user 1). Synthetic data only.

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes import statements as statement_routes
from app.api.routes import transactions as tx_routes
from app.db.session import get_db
from app.models.user import User

CSV = b"Date,Narration,Withdrawal Amt.,Deposit Amt.\n01/09/2026,UPI-SWIGGY,450.00,\n02/09/2026,SALARY SEP,,90000.00\n"


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(tx_routes.router)
    app.include_router(statement_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    return TestClient(app)


def upload(client, content: bytes, filename: str = "sept.csv", **form):
    return client.post("/api/statements/upload", files={"file": (filename, content, "text/csv")}, data=form)


def test_upload_returns_the_import_summary(client):
    r = upload(client, CSV)
    body = r.json()
    assert r.status_code == 201
    assert (body["rows_imported"], body["rows_duplicate"], body["rows_invalid"]) == (2, 0, 0)
    assert body["by_txn_type"] == {"expense": 1, "income": 1}
    assert body["next_step"] == "POST /api/transactions/categorize"


def test_reupload_is_all_duplicates_and_batches_are_listed(client):
    first = upload(client, CSV).json()
    second = upload(client, CSV).json()
    assert second["rows_imported"] == 0 and second["rows_duplicate"] == 2

    batches = client.get("/api/import-batches").json()
    assert [b["id"] for b in batches] == [second["import_batch_id"], first["import_batch_id"]]
    one = client.get(f"/api/import-batches/{first['import_batch_id']}").json()
    assert one["rows_imported"] == 2 and one["source_type"] == "csv"
    assert client.get("/api/import-batches/999").status_code == 404


def test_unsupported_type_and_oversized_files_are_rejected(client, monkeypatch):
    assert upload(client, b"hello", filename="notes.txt").status_code == 400
    monkeypatch.setattr(statement_routes, "MAX_STATEMENT_BYTES", 10)
    assert upload(client, CSV).status_code == 413


def test_old_upload_csv_alias_still_works_and_now_imports_credits(client):
    r = client.post("/api/transactions/upload-csv", files={"file": ("s.csv", CSV, "text/csv")})
    body = r.json()
    assert r.status_code == 201 and body["imported"] == 2 and body["skipped_credits"] == 0
    assert body["import_batch_id"] is not None
    credit = client.get("/api/transactions/", params={"txn_type": "income"}).json()
    assert credit["total"] == 1


# ---------------------------------------------------------------- S5: PDF / XLSX over HTTP

from pathlib import Path  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "statements"


def test_pdf_upload_with_password_form_field(client):
    pdf = (FIXTURES / "protected.pdf").read_bytes()
    assert upload(client, pdf, filename="protected.pdf").status_code == 400
    wrong = upload(client, pdf, filename="protected.pdf", password="nope")
    assert wrong.status_code == 400 and "incorrect" in wrong.json()["detail"]
    ok = upload(client, pdf, filename="protected.pdf", password="test1234")
    assert ok.status_code == 201 and ok.json()["rows_imported"] == 24


def test_scanned_pdf_and_xlsx_over_http(client):
    scanned = upload(client, (FIXTURES / "scanned.pdf").read_bytes(), filename="scanned.pdf")
    assert scanned.status_code == 422 and "scanned" in scanned.json()["detail"]
    xlsx = upload(client, (FIXTURES / "statement.xlsx").read_bytes(), filename="statement.xlsx")
    assert xlsx.status_code == 201 and xlsx.json()["source_type"] == "xlsx"
