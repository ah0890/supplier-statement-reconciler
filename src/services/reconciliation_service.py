"""Reconciliation lifecycle: create, list, run, review and finalise."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.database.database import Database
from src.database.models import ReconciliationRow
from src.database.repository import Repository
from src.utils.normalization import normalize_supplier_name

logger = logging.getLogger(__name__)


class ReconciliationServiceError(Exception):
    """User-facing error."""


@dataclass
class NewReconciliation:
    supplier_name: str
    currency: str
    client_name: str | None = None
    supplier_account: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    notes: str | None = None
    is_demo: bool = False


class ReconciliationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, request: NewReconciliation) -> int:
        name = request.supplier_name.strip()
        if not name:
            raise ReconciliationServiceError("Supplier name is required.")
        if (
            request.period_start
            and request.period_end
            and request.period_start > request.period_end
        ):
            raise ReconciliationServiceError("Statement period start must be before its end.")
        normalized = normalize_supplier_name(name) or name.upper()
        with self.database.session() as session:
            repo = Repository(session)
            supplier = repo.get_or_create_supplier(
                name,
                normalized,
                account_number=(request.supplier_account or "").strip() or None,
                currency=request.currency,
            )
            recon = repo.create_reconciliation(
                supplier,
                client_name=(request.client_name or "").strip() or None,
                supplier_account=(request.supplier_account or "").strip() or None,
                period_start=request.period_start,
                period_end=request.period_end,
                currency=request.currency.upper(),
                notes=request.notes,
                is_demo=request.is_demo,
            )
            repo.add_audit_event(
                recon.id,
                "RECONCILIATION_CREATED",
                f"Reconciliation created for {name}.",
                {"currency": request.currency, "period_start": request.period_start,
                 "period_end": request.period_end, "demo": request.is_demo},
            )  # fmt: skip
            logger.info("Created reconciliation %s", recon.id)
            return recon.id

    def get(self, reconciliation_id: int) -> ReconciliationRow | None:
        with self.database.session() as session:
            recon = Repository(session).get_reconciliation(reconciliation_id)
            if recon is not None:
                _ = recon.supplier  # load relationship before the session closes
            return recon

    def list(self) -> list[ReconciliationRow]:
        with self.database.session() as session:
            rows = list(Repository(session).list_reconciliations())
            for row in rows:
                _ = row.supplier
            return rows

    def delete(self, reconciliation_id: int) -> None:
        """Delete a reconciliation, its stored uploads and all derived data."""
        from pathlib import Path

        with self.database.session() as session:
            repo = Repository(session)
            for file_row in repo.list_files(reconciliation_id, active_only=False):
                Path(file_row.stored_path).unlink(missing_ok=True)
            repo.delete_reconciliation(reconciliation_id)
        logger.info("Deleted reconciliation %s", reconciliation_id)
