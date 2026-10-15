# app/schemas/statement.py
#
# Purpose: response shapes for statement uploads and import batches.

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StatementImportSummary(BaseModel):
    """What one upload did. Also feeds the parsing-quality metrics of the paper."""
    import_batch_id: int
    filename: str
    source_type: str                     # 'csv' | 'xlsx' | 'pdf'
    layout: str                          # 'hdfc_like' | 'sbi_like' | 'generic'
    rows_total: int                      # transaction-like rows found (valid + invalid)
    rows_imported: int
    rows_duplicate: int                  # already in your account
    rows_invalid: int                    # unreadable (bad date/amount, empty narration)
    by_txn_type: dict[str, int]          # e.g. {"expense": 40, "income": 2, "investment_out": 3}
    balance_checked: bool                # the file had a running balance we could verify
    balance_mismatches: int
    balance_mismatch_rows: list[int]     # first 10 source row numbers that did not add up
    next_step: str


class ImportBatchResponse(BaseModel):
    id: int
    filename: str
    source_type: str
    layout: str | None
    rows_total: int
    rows_imported: int
    rows_duplicate: int
    rows_invalid: int
    balance_checked: bool
    balance_mismatches: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
