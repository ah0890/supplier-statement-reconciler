"""Review and correct automatically detected column mappings."""

from __future__ import annotations

import streamlit as st

from src.parsers.columns import (
    FIELD_LABELS,
    ColumnField,
    ColumnMapping,
    missing_required_fields,
)
from src.services.import_service import FileRole, ImportServiceError
from ui.components import badge, dataframe, esc, page_header, require_reconciliation, workflow_steps
from ui.import_panel import validation_summary
from ui.state import flash, import_service

NOT_MAPPED = "— not mapped —"
COMMON_FIELDS = {
    FileRole.STATEMENT: (
        ColumnField.DATE, ColumnField.INVOICE_NUMBER, ColumnField.REFERENCE,
        ColumnField.DESCRIPTION, ColumnField.TRANSACTION_TYPE, ColumnField.DEBIT,
        ColumnField.CREDIT, ColumnField.AMOUNT, ColumnField.PAYMENT_AMOUNT, ColumnField.BALANCE,
        ColumnField.DUE_DATE, ColumnField.TAX, ColumnField.CURRENCY,
        ColumnField.RELATED_INVOICE,
    ),
    FileRole.ACCOUNTING: (
        ColumnField.DATE, ColumnField.INVOICE_NUMBER, ColumnField.REFERENCE,
        ColumnField.SUPPLIER_NAME, ColumnField.SUPPLIER_ACCOUNT, ColumnField.AMOUNT,
        ColumnField.DEBIT, ColumnField.CREDIT, ColumnField.TRANSACTION_TYPE,
        ColumnField.DUE_DATE, ColumnField.SUBTOTAL, ColumnField.TAX,
        ColumnField.OUTSTANDING, ColumnField.PAYMENT_AMOUNT, ColumnField.STATUS,
        ColumnField.CURRENCY, ColumnField.DESCRIPTION, ColumnField.RELATED_INVOICE,
    ),
    FileRole.PAYMENTS: (
        ColumnField.DATE, ColumnField.REFERENCE, ColumnField.AMOUNT,
        ColumnField.PAYMENT_AMOUNT, ColumnField.INVOICE_NUMBER, ColumnField.RELATED_INVOICE,
        ColumnField.SUPPLIER_NAME, ColumnField.SUPPLIER_ACCOUNT, ColumnField.CURRENCY,
        ColumnField.DESCRIPTION,
    ),
}  # fmt: skip
DAYFIRST_OPTIONS = {"Detect automatically": None, "Day first (DD/MM/YYYY)": True,
                    "Month first (MM/DD/YYYY)": False}  # fmt: skip
DECIMAL_OPTIONS = {"Detect automatically": None, "1,234.56": False, "1.234,56": True}

page_header("Column Mapping", "Check how the columns in your files were interpreted.")
recon = require_reconciliation()
workflow_steps("Mapping", done=("Details", "Statement", "Accounting"))

roles = [r for r in FileRole if import_service().validation_status(recon.id, r) is not None]
if not roles:
    st.info("Upload a supplier statement or an accounting export first.")
    st.stop()

tabs = st.tabs([role.label for role in roles])
for tab, role in zip(tabs, roles, strict=True):
    with tab:
        try:
            preview = import_service().apply_mapping(recon.id, role, persist=False)
        except ImportServiceError as exc:
            st.error(str(exc))
            continue
        table, mapping = preview.table, preview.mapping
        st.caption(
            f"{preview.file_name} · {len(table.rows)} row(s) · extraction: {table.method}"
            + ("" if table.header_detected else " · no header row recognised")
        )
        with st.expander("Extracted table (first 50 rows)", expanded=not preview.mapping_complete):
            dataframe(table.preview(50), height=300)

        options = [NOT_MAPPED, *table.headers]
        new_fields: dict[str, str] = {}
        with st.form(f"mapping_{role.value}"):
            st.markdown("**Map each field to a column in your file** (* required)")
            columns = st.columns(2)
            for index, field in enumerate(COMMON_FIELDS[role]):
                current = mapping.column_for(field)
                confidence = mapping.confidence.get(field.value)
                label = FIELD_LABELS[field]
                if field == ColumnField.DATE:
                    label += " *"
                elif field == ColumnField.INVOICE_NUMBER and role != FileRole.PAYMENTS:
                    label += " * (or Reference)"
                elif field == ColumnField.AMOUNT:
                    label += " * (or Debit / Credit)"
                with columns[index % 2]:
                    choice = st.selectbox(
                        label,
                        options,
                        index=options.index(current) if current in options else 0,
                        key=f"{role.value}_{field.value}",
                        help=mapping.notes.get(field.value),
                    )
                    if current and confidence is not None:
                        tone = (
                            "green" if confidence >= 80 else "amber" if confidence >= 60 else "red"
                        )
                        note = mapping.notes.get(field.value, "")
                        st.markdown(
                            badge(f"detected · {confidence}% confidence", tone)
                            + (f" <small>{esc(note)}</small>" if note else ""),
                            unsafe_allow_html=True,
                        )
                if choice != NOT_MAPPED:
                    new_fields[field.value] = choice
            st.markdown("**Formats**")
            left, right = st.columns(2)
            dayfirst_label = next(k for k, v in DAYFIRST_OPTIONS.items() if v == mapping.dayfirst)
            decimal_label = next(
                k for k, v in DECIMAL_OPTIONS.items() if v == mapping.decimal_comma
            )
            dayfirst_choice = left.selectbox("Date order", list(DAYFIRST_OPTIONS),
                                             index=list(DAYFIRST_OPTIONS).index(dayfirst_label),
                                             key=f"{role.value}_dayfirst")  # fmt: skip
            decimal_choice = right.selectbox("Number format", list(DECIMAL_OPTIONS),
                                             index=list(DECIMAL_OPTIONS).index(decimal_label),
                                             key=f"{role.value}_decimal")  # fmt: skip
            submitted = st.form_submit_button("Validate & apply mapping", type="primary")

        if submitted:
            duplicates = {c for c in new_fields.values() if list(new_fields.values()).count(c) > 1}
            new_mapping = ColumnMapping(
                fields=new_fields,
                confidence={f: 100 for f in new_fields},
                dayfirst=DAYFIRST_OPTIONS[dayfirst_choice],
                decimal_comma=DECIMAL_OPTIONS[decimal_choice],
            )
            missing = missing_required_fields(
                new_mapping, require_reference=role != FileRole.PAYMENTS
            )
            if duplicates:
                st.error("Each column can only be mapped once: " + ", ".join(sorted(duplicates)))
            elif missing:
                st.error("Please map: " + ", ".join(missing) + ".")
            else:
                outcome = import_service().apply_mapping(recon.id, role, new_mapping)
                flash(
                    f"{role.label}: mapping applied - {len(outcome.result.transactions)} "
                    "transaction(s) imported."
                )
                st.rerun()

        st.markdown("##### Validation summary")
        validation_summary(recon, role)

st.divider()
st.page_link("pages/reconciliation.py", label="Next: run the reconciliation",
             icon=":material/arrow_forward:")  # fmt: skip
