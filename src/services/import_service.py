"""Import workflow: validate -> store -> parse -> map -> normalise -> persist -> audit.

The service is UI-independent; Streamlit pages call it with raw bytes.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from src.accounting import get_file_provider
from src.accounting.base import AccountingDataKind, AccountingProviderError, AccountingQuery
from src.config import ACCOUNTING_EXTENSIONS, STATEMENT_EXTENSIONS, AppSettings
from src.database.database import Database
from src.database.models import ReconciliationRow, UploadedFileRow
from src.database.repository import Repository
from src.models.transaction import TransactionSource
from src.models.validation import ImportResult, IssueSeverity, ValidationReport
from src.parsers.base import StatementParserError, get_statement_parser
from src.parsers.columns import ColumnMapping
from src.parsers.excel_parser import ExcelStatementParser
from src.parsers.tabular import RawTable
from src.services.normalization_service import NormalizationContext, normalize_transactions
from src.utils.validation import (
    UploadValidationError,
    safe_join,
    sanitize_filename,
    validate_upload,
)

logger = logging.getLogger(__name__)


class FileRole(StrEnum):
    STATEMENT = "STATEMENT"
    ACCOUNTING = "ACCOUNTING"
    PAYMENTS = "PAYMENTS"

    @property
    def label(self) -> str:
        return {
            FileRole.STATEMENT: "Supplier statement",
            FileRole.ACCOUNTING: "Accounting bills export",
            FileRole.PAYMENTS: "Payments export",
        }[self]


ROLE_EXTENSIONS = {
    FileRole.STATEMENT: STATEMENT_EXTENSIONS,
    FileRole.ACCOUNTING: ACCOUNTING_EXTENSIONS,
    FileRole.PAYMENTS: ACCOUNTING_EXTENSIONS,
}
ROLE_SOURCE = {
    FileRole.STATEMENT: TransactionSource.STATEMENT,
    FileRole.ACCOUNTING: TransactionSource.ACCOUNTING,
    FileRole.PAYMENTS: TransactionSource.PAYMENTS,
}
MAX_STORED_ISSUES = 500


class ImportServiceError(Exception):
    """User-facing import failure."""


@dataclass
class ImportOutcome:
    role: FileRole
    file_id: int
    file_name: str
    parser_name: str
    table: RawTable
    mapping: ColumnMapping
    result: ImportResult
    missing_required: list[str] = field(default_factory=list)
    persisted: bool = False

    @property
    def mapping_complete(self) -> bool:
        return not self.missing_required


def report_summary(report: ValidationReport) -> dict[str, Any]:
    return {
        "records_read": report.records_read,
        "valid": report.valid,
        "skipped": report.skipped,
        "warnings": report.warnings,
        "errors": report.errors,
        "issues": [i.model_dump(mode="json") for i in report.issues[:MAX_STORED_ISSUES]],
    }


class ImportService:
    def __init__(self, database: Database, settings: AppSettings) -> None:
        self.database = database
        self.settings = settings

    # ------------------------------------------------------------------ upload
    def upload(
        self,
        reconciliation_id: int,
        role: FileRole,
        filename: str,
        data: bytes,
        *,
        sheet_name: str | None = None,
    ) -> ImportOutcome:
        """Validate and store an uploaded file, then import it with detected mapping."""
        try:
            extension = validate_upload(
                filename,
                data,
                allowed_extensions=ROLE_EXTENSIONS[role],
                max_bytes=self.settings.max_upload_bytes,
            )
        except UploadValidationError as exc:
            logger.warning("Upload rejected for reconciliation %s: %s", reconciliation_id, exc)
            raise ImportServiceError(str(exc)) from exc

        stored_path = self._store(reconciliation_id, role, filename, data)
        digest = hashlib.sha256(data).hexdigest()
        with self.database.session() as session:
            repo = Repository(session)
            if repo.get_reconciliation(reconciliation_id) is None:
                raise ImportServiceError("Reconciliation not found.")
            row = repo.add_uploaded_file(
                reconciliation_id,
                role=role.value,
                original_name=sanitize_filename(filename),
                stored_path=str(stored_path),
                sha256=digest,
                size_bytes=len(data),
                extension=extension,
                import_options={"sheet_name": sheet_name} if sheet_name else {},
            )
            repo.add_audit_event(
                reconciliation_id,
                "FILE_UPLOADED",
                f"{role.label} uploaded: {row.original_name}",
                {"role": role.value, "file": row.original_name, "sha256": digest,
                 "size_bytes": len(data)},
            )  # fmt: skip
        logger.info("Stored %s upload (%d bytes) for reconciliation %s",
                    role.value, len(data), reconciliation_id)  # fmt: skip
        return self.apply_mapping(reconciliation_id, role)

    def _store(self, reconciliation_id: int, role: FileRole, filename: str, data: bytes) -> Path:
        directory = safe_join(
            self.settings.upload_dir, f"reconciliation_{reconciliation_id}", role.value.lower()
        )
        directory.mkdir(parents=True, exist_ok=True)
        target = safe_join(directory, f"{uuid.uuid4().hex[:10]}_{sanitize_filename(filename)}")
        handle, temp_name = tempfile.mkstemp(dir=directory, prefix=".upload-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as temp_file:
                temp_file.write(data)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)
        with contextlib.suppress(OSError):  # not supported on some file systems
            os.chmod(target, 0o600)
        return target

    # ------------------------------------------------------------- mapping
    def apply_mapping(
        self,
        reconciliation_id: int,
        role: FileRole,
        mapping: ColumnMapping | None = None,
        *,
        persist: bool = True,
    ) -> ImportOutcome:
        """(Re)parse the active file for ``role`` with ``mapping`` (or the stored/detected
        mapping) and replace the imported transactions."""
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            file_row = repo.get_active_file(reconciliation_id, role.value)
            if recon is None or file_row is None:
                raise ImportServiceError(f"No {role.label.lower()} has been uploaded yet.")
            if mapping is None and file_row.column_mapping:
                mapping = ColumnMapping.model_validate(file_row.column_mapping)

            outcome = self._parse(recon, file_row, role, mapping)
            context = NormalizationContext(
                supplier_name=recon.supplier.name,
                supplier_account=recon.supplier_account,
                currency=recon.currency,
            )
            kept, excluded = normalize_transactions(
                outcome.result.transactions,
                context,
                outcome.result.report,
                filter_supplier=role != FileRole.STATEMENT,
            )
            for txn in kept:  # show the name the user uploaded, not the storage name
                txn.source_file = file_row.original_name
            outcome.result.transactions = kept
            outcome.result.excluded_other_suppliers = excluded
            outcome.result.report.valid = len(kept)

            if persist:
                self._persist(repo, recon, file_row, role, outcome)
        return outcome

    def _parse(
        self,
        recon: ReconciliationRow,
        file_row: UploadedFileRow,
        role: FileRole,
        mapping: ColumnMapping | None,
    ) -> ImportOutcome:
        path = Path(file_row.stored_path)
        if not path.is_file():
            raise ImportServiceError(
                "The uploaded file is no longer available on disk. Please upload it again."
            )
        sheet = (file_row.import_options or {}).get("sheet_name")
        table: RawTable | None
        mapping_used: ColumnMapping | None
        try:
            if role == FileRole.STATEMENT:
                parser = (
                    ExcelStatementParser(sheet) if path.suffix.lower() == ".xlsx"
                    else get_statement_parser(path)
                )  # fmt: skip
                parsed = parser.parse(
                    path,
                    mapping=mapping,
                    expected_currency=recon.currency,
                    supplier_name=recon.supplier.name,
                    supplier_account=recon.supplier_account,
                )
                table, mapping_used, result = parsed.table, parsed.mapping, parsed.result
                missing, parser_name = parsed.missing_required, f"{parser.name} statement parser"
            else:
                provider = get_file_provider(path, sheet)
                kind = (
                    AccountingDataKind.PAYMENTS if role == FileRole.PAYMENTS
                    else AccountingDataKind.BILLS
                )  # fmt: skip
                query = AccountingQuery(
                    supplier_name=recon.supplier.name,
                    supplier_account=recon.supplier_account,
                    period_start=recon.period_start,
                    period_end=recon.period_end,
                    currency=recon.currency,
                )
                loaded = provider.load(query, kind, mapping)
                table, mapping_used, result = loaded.table, loaded.mapping, loaded.result
                missing, parser_name = loaded.missing_required, provider.name
        except (StatementParserError, AccountingProviderError) as exc:
            raise ImportServiceError(str(exc)) from exc
        except Exception as exc:
            logger.exception("Unexpected error parsing %s file", role.value)
            raise ImportServiceError(
                "The file could not be processed. It may be damaged or in an unexpected format."
            ) from exc

        assert table is not None and mapping_used is not None
        return ImportOutcome(
            role=role,
            file_id=file_row.id,
            file_name=file_row.original_name,
            parser_name=parser_name,
            table=table,
            mapping=mapping_used,
            result=result,
            missing_required=missing,
        )

    def _persist(
        self,
        repo: Repository,
        recon: ReconciliationRow,
        file_row: UploadedFileRow,
        role: FileRole,
        outcome: ImportOutcome,
    ) -> None:
        file_row.column_mapping = outcome.mapping.model_dump(mode="json")
        file_row.parser = outcome.parser_name
        file_row.validation_json = report_summary(outcome.result.report)
        file_row.row_count = len(outcome.result.transactions)
        options = dict(file_row.import_options or {})
        options.pop("errors_acknowledged", None)
        file_row.import_options = options

        transactions = outcome.result.transactions if outcome.mapping_complete else []
        repo.replace_transactions(recon.id, ROLE_SOURCE[role], transactions, file_row.id)
        outcome.persisted = outcome.mapping_complete

        if role == FileRole.STATEMENT:
            balances = outcome.result.balances
            if recon.opening_balance_source != "manual":
                recon.opening_balance = balances.opening_balance
                recon.opening_balance_source = balances.opening_source
            if recon.closing_balance_source != "manual":
                recon.closing_balance = balances.closing_balance
                recon.closing_balance_source = balances.closing_source

        if recon.last_run_at is not None:
            recon.workflow_status = "DATA_CHANGED"
        repo.touch(recon)
        report = outcome.result.report
        repo.add_audit_event(
            recon.id,
            "MAPPING_APPLIED",
            f"{role.label} imported: {len(transactions)} transaction(s), "
            f"{report.warnings} warning(s), {report.errors} error(s).",
            {
                "role": role.value,
                "file": file_row.original_name,
                "parser": outcome.parser_name,
                "mapping": outcome.mapping.fields,
                "dayfirst": outcome.mapping.dayfirst,
                "decimal_comma": outcome.mapping.decimal_comma,
                "excluded_other_suppliers": outcome.result.excluded_other_suppliers,
            },
        )

    # ---------------------------------------------------------------- other
    def acknowledge_errors(self, reconciliation_id: int, role: FileRole) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            file_row = repo.get_active_file(reconciliation_id, role.value)
            if file_row is None:
                return
            options = dict(file_row.import_options or {})
            options["errors_acknowledged"] = True
            file_row.import_options = options
            repo.add_audit_event(
                reconciliation_id,
                "ERRORS_ACKNOWLEDGED",
                f"User acknowledged import errors in {role.label.lower()}; erroneous rows "
                "are excluded from reconciliation.",
            )

    def remove_file(self, reconciliation_id: int, role: FileRole) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            for file_row in repo.list_files(reconciliation_id, role=role.value):
                file_row.is_active = False
                Path(file_row.stored_path).unlink(missing_ok=True)
            repo.replace_transactions(reconciliation_id, ROLE_SOURCE[role], [])
            repo.add_audit_event(reconciliation_id, "FILE_REMOVED", f"{role.label} removed.")

    def set_balances(
        self,
        reconciliation_id: int,
        opening: Decimal | None,
        closing: Decimal | None,
    ) -> None:
        """Record statement balances entered or confirmed by the user."""
        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None:
                raise ImportServiceError("Reconciliation not found.")
            if opening != recon.opening_balance:
                recon.opening_balance, recon.opening_balance_source = opening, "manual"
            if closing != recon.closing_balance:
                recon.closing_balance, recon.closing_balance_source = closing, "manual"
            if recon.last_run_at is not None:
                recon.workflow_status = "DATA_CHANGED"
            repo.add_audit_event(
                reconciliation_id,
                "BALANCES_SET",
                "Statement balances confirmed/edited by user.",
                {"opening": opening, "closing": closing},
            )

    def validation_status(self, reconciliation_id: int, role: FileRole) -> dict[str, Any] | None:
        """Stored validation summary plus acknowledgement state for the active file."""
        with self.database.session() as session:
            file_row = Repository(session).get_active_file(reconciliation_id, role.value)
            if file_row is None:
                return None
            summary = dict(file_row.validation_json or {})
            summary["file_name"] = file_row.original_name
            summary["errors_acknowledged"] = bool(
                (file_row.import_options or {}).get("errors_acknowledged")
            )
            summary["mapping_complete"] = bool(file_row.row_count)
            summary["uploaded_at"] = file_row.uploaded_at
            summary["parser"] = file_row.parser
            return summary

    @staticmethod
    def blocking_errors(summary: dict[str, Any] | None) -> bool:
        """Errors the user must resolve or acknowledge before reconciling."""
        if not summary:
            return False
        return bool(summary.get("errors")) and not summary.get("errors_acknowledged")


def severity_counts(report: ValidationReport) -> dict[str, int]:
    return {s.value: sum(1 for i in report.issues if i.severity == s) for s in IssueSeverity}
