# app/models/import_batch.py
#
# Purpose: the `import_batches` table. One row per uploaded statement file, so
# the user (and the research metrics) can see what each upload did: how many
# rows were read, imported, duplicated or invalid, and whether the running
# balances checked out. Filled by the statement upload pipeline (S4/S5).

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ImportBatch(Base):
    """Summary of one statement upload by one user."""
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False)        # 'csv' | 'xlsx' | 'pdf'
    layout: Mapped[str | None] = mapped_column(String(30), nullable=True)       # 'hdfc_like' | 'sbi_like' | 'generic'

    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rows_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rows_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Whether the file had a balance column we could verify, and how many rows disagreed
    balance_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    balance_mismatches: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
