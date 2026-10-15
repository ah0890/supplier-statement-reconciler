"""Shared upload / validation panel for statement and accounting files."""

from __future__ import annotations

import streamlit as st

from src.database.models import ReconciliationRow
from src.models.transaction import CanonicalTransaction
from src.services.import_service import ROLE_EXTENSIONS, FileRole, ImportService, ImportServiceError
from ui.components import dataframe, kpi_row
from ui.state import flash, import_service, reconciliation_service

TYPE_LABEL = {"INVOICE": "Invoice", "CREDIT_NOTE": "Credit note", "PAYMENT": "Payment"}


def transactions_table(transactions: list[CanonicalTransaction], *, height: int = 360) -> None:
    dataframe(
        [
            {
                "Row": t.source_row,
                "Date": t.invoice_date,
                "Type": TYPE_LABEL[t.transaction_type.value],
                "Invoice / Ref": t.display_reference,
                "Allocated To": t.related_invoice_number or "",
                "Description": t.description or "",
                "Amount": t.amount,
                "Balance": t.balance,
                "Currency": t.currency or "",
            }
            for t in transactions
        ],
        height=height,
    )


def validation_summary(recon: ReconciliationRow, role: FileRole) -> dict | None:
    service = import_service()
    summary = service.validation_status(recon.id, role)
    if summary is None:
        return None
    errors, warnings = summary.get("errors", 0), summary.get("warnings", 0)
    kpi_row(
        [
            ("Records read", summary.get("records_read", 0), summary.get("file_name", ""), "blue"),
            ("Valid transactions", summary.get("valid", 0), "imported", "green"),
            ("Warnings", warnings, "review recommended", "amber" if warnings else "green"),
            ("Errors", errors, "rows excluded", "red" if errors else "green"),
        ]
    )
    issues = summary.get("issues") or []
    if issues:
        with st.expander(f"Validation details ({len(issues)})", expanded=bool(errors)):
            dataframe(
                [
                    {"Severity": i["severity"].title(), "Row": i.get("row"),
                     "Message": i["message"]}
                    for i in issues
                ],
                height=min(400, 38 + 35 * len(issues)),
            )  # fmt: skip
    if ImportService.blocking_errors(summary):
        st.error(
            f"{errors} row(s) could not be imported. Correct the column mapping, or confirm "
            "that these rows should be excluded from the reconciliation."
        )
        if st.button("Exclude rows with errors and continue", key=f"ack_{role.value}"):
            service.acknowledge_errors(recon.id, role)
            flash(
                "Rows with errors will be excluded. This decision is recorded in the audit trail."
            )
            st.rerun()
    elif errors:
        st.caption(f"{errors} row(s) with errors are excluded (confirmed).")
    return summary


def upload_section(recon: ReconciliationRow, role: FileRole, help_text: str) -> None:
    extensions = sorted(e.lstrip(".") for e in ROLE_EXTENSIONS[role])
    uploaded = st.file_uploader(
        f"Upload {role.label.lower()}",
        type=extensions,
        key=f"upload_{role.value}_{recon.id}",
        help=help_text,
    )
    sheet = None
    if uploaded is not None and uploaded.name.lower().endswith(".xlsx"):
        sheet = (
            st.text_input(
                "Worksheet name (optional)",
                key=f"sheet_{role.value}",
                help="Leave empty to pick the worksheet that looks most like a transaction table.",
            ).strip()
            or None
        )
    if uploaded is not None and st.button(
        "Import file", type="primary", key=f"import_{role.value}"
    ):
        with st.spinner("Reading and validating the file…"):
            try:
                outcome = import_service().upload(
                    recon.id, role, uploaded.name, uploaded.getvalue(), sheet_name=sheet
                )
            except ImportServiceError as exc:
                st.error(str(exc))
                return
        if not outcome.mapping_complete:
            flash(
                "File uploaded, but some required columns could not be identified: "
                + ", ".join(outcome.missing_required)
                + ". Please complete the column mapping.",
                "warning",
            )
        else:
            count = len(outcome.result.transactions)
            flash(f"{role.label} imported: {count} transaction(s) found.")
            uncertain = outcome.mapping.uncertain_fields()
            if uncertain:
                flash(
                    "Some columns were detected with low confidence ("
                    + ", ".join(uncertain)
                    + "). Please check them on the Column Mapping page.",
                    "warning",
                )
        st.rerun()


def remove_button(recon: ReconciliationRow, role: FileRole) -> None:
    if st.button(f"Remove {role.label.lower()}", key=f"remove_{role.value}"):
        import_service().remove_file(recon.id, role)
        flash(f"{role.label} removed.", "info")
        st.rerun()


def stored_transactions(recon: ReconciliationRow, role: FileRole) -> list[CanonicalTransaction]:
    data = reconciliation_service().load_data(recon.id)
    return {
        FileRole.STATEMENT: data.statement,
        FileRole.ACCOUNTING: data.accounting,
        FileRole.PAYMENTS: data.payments,
    }[role]
