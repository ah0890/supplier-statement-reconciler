"""Reconciliation lifecycle: create, list, run, finalise and audit."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from src.config import get_settings
from src.database.database import Database
from src.database.models import ReconciliationRow, utcnow
from src.database.repository import Repository
from src.models.reconciliation import (
    BalanceCheckResult,
    BalanceStatus,
    MatchResult,
    PaymentAllocation,
    ReconciliationConfig,
    ReconciliationOutcome,
    ReconciliationRun,
    ReconciliationSummary,
)
from src.models.supplier import Supplier
from src.models.transaction import CanonicalTransaction, StatementBalances, TransactionSource
from src.reconciliation.engine import ReconciliationEngine
from src.utils.normalization import normalize_supplier_name

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_KEY = "default_reconciliation_config"


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


@dataclass
class Readiness:
    statement_transactions: int = 0
    accounting_transactions: int = 0
    payment_records: int = 0
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_run(self) -> bool:
        return not self.blocking


@dataclass
class ReconciliationData:
    """Inputs of a reconciliation, loaded from the database."""

    statement: list[CanonicalTransaction]
    accounting: list[CanonicalTransaction]
    payments: list[CanonicalTransaction]
    balances: StatementBalances

    def lookup(self) -> dict[str, CanonicalTransaction]:
        return {t.transaction_id: t for t in (*self.statement, *self.accounting, *self.payments)}


@dataclass
class StoredRun:
    """A persisted reconciliation run, reloaded for display/export."""

    results: list[MatchResult]
    balance: BalanceCheckResult
    summary: ReconciliationSummary
    config: ReconciliationConfig
    allocations: list[PaymentAllocation]
    unallocated_payments: list[str]


def workflow_label(recon: ReconciliationRow) -> str:
    if recon.outcome:
        return "Finalised"
    if recon.workflow_status == "DATA_CHANGED":
        return "Data changed - re-run needed"
    if recon.last_run_at:
        return "Reconciled - review"
    return "In progress"


class ReconciliationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    # ------------------------------------------------------------------ CRUD
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

    def suppliers(self) -> list[Supplier]:
        """Suppliers used in earlier reconciliations (for quick re-use)."""
        with self.database.session() as session:
            return [
                Supplier(
                    id=row.id,
                    name=row.name,
                    normalized_name=row.normalized_name,
                    account_number=row.account_number,
                    contact_reference=row.contact_reference,
                    currency=row.currency,
                )
                for row in Repository(session).list_suppliers()
            ]

    def get(self, reconciliation_id: int) -> ReconciliationRow | None:
        with self.database.session() as session:
            recon = Repository(session).get_reconciliation(reconciliation_id)
            if recon is not None:
                _ = recon.supplier  # load relationship before the session closes
            return recon

    def list_all(self) -> list[ReconciliationRow]:
        with self.database.session() as session:
            rows = list(Repository(session).list_reconciliations())
            for row in rows:
                _ = row.supplier
            return rows

    def delete(self, reconciliation_id: int) -> None:
        """Delete a reconciliation, its stored uploads and all derived data."""
        with self.database.session() as session:
            repo = Repository(session)
            for file_row in repo.list_files(reconciliation_id, active_only=False):
                Path(file_row.stored_path).unlink(missing_ok=True)
            repo.delete_reconciliation(reconciliation_id)
        logger.info("Deleted reconciliation %s", reconciliation_id)

    # ---------------------------------------------------------------- inputs
    def load_data(self, reconciliation_id: int) -> ReconciliationData:
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None:
                raise ReconciliationServiceError("Reconciliation not found.")
            return ReconciliationData(
                statement=repo.get_transactions(reconciliation_id, TransactionSource.STATEMENT),
                accounting=repo.get_transactions(reconciliation_id, TransactionSource.ACCOUNTING),
                payments=repo.get_transactions(reconciliation_id, TransactionSource.PAYMENTS),
                balances=StatementBalances(
                    opening_balance=recon.opening_balance,
                    closing_balance=recon.closing_balance,
                    opening_source=recon.opening_balance_source,
                    closing_source=recon.closing_balance_source,
                ),
            )

    def readiness(self, reconciliation_id: int) -> Readiness:
        """What still has to happen before reconciliation can run."""
        from src.services.import_service import FileRole, ImportService

        state = Readiness()
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None:
                state.blocking.append("Reconciliation not found.")
                return state
            state.statement_transactions = repo.count_transactions(
                reconciliation_id, TransactionSource.STATEMENT
            )
            state.accounting_transactions = repo.count_transactions(
                reconciliation_id, TransactionSource.ACCOUNTING
            )
            state.payment_records = repo.count_transactions(
                reconciliation_id, TransactionSource.PAYMENTS
            )
            summaries = {}
            for role in FileRole:
                file_row = repo.get_active_file(reconciliation_id, role.value)
                if file_row is not None:
                    summary = dict(file_row.validation_json or {})
                    summary["errors_acknowledged"] = bool(
                        (file_row.import_options or {}).get("errors_acknowledged")
                    )
                    summaries[role] = summary
            opening, closing = recon.opening_balance, recon.closing_balance

        if not state.statement_transactions:
            state.blocking.append("Upload the supplier statement and map its columns.")
        if not state.accounting_transactions:
            state.blocking.append("Upload the accounting export and map its columns.")
        for role, summary in summaries.items():
            if ImportService.blocking_errors(summary):
                state.blocking.append(
                    f"{role.label}: {summary.get('errors')} row(s) have errors. Fix the mapping "
                    "or confirm that these rows should be excluded."
                )
        if closing is None or opening is None:
            state.warnings.append(
                "Opening and/or closing balance is missing - the balance check may not be "
                "possible. You can enter balances on the Supplier Statement page."
            )
        if not state.payment_records:
            state.warnings.append(
                "No payments export uploaded - payments on the statement cannot be verified."
            )
        return state

    # ---------------------------------------------------------------- config
    def default_config(self) -> ReconciliationConfig:
        with self.database.session() as session:
            raw = Repository(session).get_setting(DEFAULT_CONFIG_KEY)
        if raw:
            try:
                return ReconciliationConfig.model_validate_json(raw)
            except ValidationError:
                logger.warning("Stored default configuration is invalid; using defaults")
        settings = get_settings()
        try:
            return ReconciliationConfig(
                amount_tolerance=settings.default_amount_tolerance,
                date_tolerance_days=settings.default_date_tolerance_days,
                fuzzy_match_threshold=settings.fuzzy_match_threshold,
            )
        except ValidationError:
            return ReconciliationConfig()

    def save_default_config(self, config: ReconciliationConfig) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            repo.set_setting(DEFAULT_CONFIG_KEY, config.model_dump_json())
            repo.add_audit_event(
                None, "SETTINGS_CHANGED", "Default reconciliation settings updated.",
                config.model_dump(mode="json"),
            )  # fmt: skip

    def config_for(self, reconciliation_id: int) -> ReconciliationConfig:
        recon = self.get(reconciliation_id)
        if recon is not None and recon.config_json:
            try:
                return ReconciliationConfig.model_validate(recon.config_json)
            except ValidationError:
                pass
        return self.default_config()

    # ------------------------------------------------------------------- run
    def run(
        self, reconciliation_id: int, config: ReconciliationConfig | None = None
    ) -> ReconciliationRun:
        readiness = self.readiness(reconciliation_id)
        if not readiness.can_run:
            raise ReconciliationServiceError(" ".join(readiness.blocking))
        config = config or self.config_for(reconciliation_id)
        data = self.load_data(reconciliation_id)
        recon = self.get(reconciliation_id)
        assert recon is not None
        run = ReconciliationEngine(config).run(
            data.statement,
            data.accounting,
            data.payments,
            balances=data.balances,
            period_end=recon.period_end,
        )
        self._persist_run(reconciliation_id, run)
        return run

    def _persist_run(self, reconciliation_id: int, run: ReconciliationRun) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None:
                raise ReconciliationServiceError("Reconciliation not found.")
            repo.replace_results(reconciliation_id, run.results)
            recon.config_json = run.config.model_dump(mode="json")
            recon.balance_json = run.balance.model_dump(mode="json")
            recon.summary_json = run.summary.model_dump(mode="json")
            recon.allocations_json = {
                "allocations": [a.model_dump(mode="json") for a in run.allocations],
                "unallocated": run.unallocated_payments,
            }
            recon.last_run_at = utcnow()
            recon.workflow_status = "RECONCILED_RUN"
            recon.outcome = None
            recon.finalised_at = None
            files = [
                {"role": f.role, "file": f.original_name, "sha256": f.sha256,
                 "mapping": (f.column_mapping or {}).get("fields")}
                for f in repo.list_files(reconciliation_id)
            ]  # fmt: skip
            repo.add_audit_event(
                reconciliation_id,
                "RECONCILIATION_RUN",
                f"Reconciliation run: {len(run.results)} results, "
                f"{run.summary.exceptions} exception(s), {run.summary.pending_reviews} pending "
                f"review(s), balance {run.balance.status.value}.",
                {
                    "config": run.config.model_dump(mode="json"),
                    "status_counts": run.summary.status_counts,
                    "files": files,
                    "duration_ms": run.duration_ms,
                    "suggested_outcome": run.summary.suggested_outcome.value,
                },
            )

    def stored_run(self, reconciliation_id: int) -> StoredRun | None:
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None or recon.last_run_at is None or recon.balance_json is None:
                return None
            results = repo.get_results(reconciliation_id)
            allocations = recon.allocations_json or {}
            return StoredRun(
                results=results,
                balance=BalanceCheckResult.model_validate(recon.balance_json),
                summary=ReconciliationSummary.model_validate(recon.summary_json or {}),
                config=ReconciliationConfig.model_validate(recon.config_json or {}),
                allocations=[
                    PaymentAllocation.model_validate(a) for a in allocations.get("allocations", [])
                ],
                unallocated_payments=list(allocations.get("unallocated", [])),
            )

    # ------------------------------------------------------------- finalise
    def finalise(
        self, reconciliation_id: int, outcome: ReconciliationOutcome, note: str | None = None
    ) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None or recon.last_run_at is None:
                raise ReconciliationServiceError("Run the reconciliation before finalising it.")
            if recon.workflow_status == "DATA_CHANGED":
                raise ReconciliationServiceError(
                    "Data changed after the last run. Re-run the reconciliation first."
                )
            recon.outcome = outcome.value
            recon.outcome_note = (note or "").strip() or None
            recon.finalised_at = utcnow()
            repo.add_audit_event(
                reconciliation_id,
                "RECONCILIATION_FINALISED",
                f"Reconciliation marked {outcome.value}.",
                {"note": recon.outcome_note},
            )

    def reopen(self, reconciliation_id: int) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None:
                return
            recon.outcome = None
            recon.finalised_at = None
            repo.add_audit_event(reconciliation_id, "RECONCILIATION_REOPENED", "Reopened.")

    def audit_trail(self, reconciliation_id: int) -> list[dict]:
        with self.database.session() as session:
            return [
                {
                    "When": event.created_at,
                    "Event": event.event_type.replace("_", " ").title(),
                    "Details": event.message,
                    "Data": json.dumps(event.details, default=str) if event.details else "",
                }
                for event in Repository(session).get_audit_events(reconciliation_id)
            ]


def balance_is_known(balance: BalanceCheckResult) -> bool:
    return balance.status != BalanceStatus.BALANCE_CHECK_NOT_POSSIBLE


def as_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))
