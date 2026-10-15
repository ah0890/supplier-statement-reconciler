"""Synthetic DEMO data: a supplier statement, an accounting export and payments.

All names, references and amounts are fictional. The data deliberately
contains the situations a bookkeeper meets in practice, and a manifest records
which references belong to which scenario so tests can verify the outcome.

Scenarios (beyond exact matches): missing invoices, accounting-only bills
(one dated after the statement - a timing difference), a double-entered bill,
amount mismatches (net entered instead of gross), date mismatches, reference
formatting differences, a transposed-digit typo, a difference within
tolerance, credit notes (one missing), payments (a payment run split across
allocations, a payment matched by amount/date, a payment not yet on the
statement) and an ambiguous blank-reference line.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

DEMO_SUPPLIER = "Brightline Industrial Supplies Ltd"
DEMO_SUPPLIER_ACCOUNT = "BIS-00417"
DEMO_CLIENT = "Demo Client Ltd"
OTHER_SUPPLIER = "Northgate Packaging Co"
CURRENCY = "GBP"
PERIOD_START = date(2026, 9, 1)
PERIOD_END = date(2026, 9, 30)
OPENING_BALANCE = Decimal("3250.00")
VAT = Decimal("1.20")
CENT = Decimal("0.01")

DESCRIPTIONS = (
    "Safety gloves (box of 100)", "Hydraulic hose assemblies", "Steel fixings - assorted",
    "Bearings 6204-2RS", "Cable ties and clips", "Pallet wrap 500mm", "Welding rods E6013",
    "Hi-vis vests", "Industrial lubricants", "Drill bits HSS set", "Conveyor belt repair kit",
    "LED work lights", "Abrasive discs 115mm", "Torque wrench calibration",
    "Spray paint - RAL 7016",
)  # fmt: skip


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal | None) -> str:
    return "" if value is None else f"{value:,.2f}"


@dataclass
class StatementLine:
    day: date
    kind: str  # Invoice / Credit Note / Payment
    reference: str
    description: str
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal = Decimal("0")


@dataclass
class Bill:
    supplier: str
    number: str
    day: date
    due: date
    total: Decimal
    kind: str = "ACCPAY"  # ACCPAYCREDIT for credit notes
    status: str = "AUTHORISED"
    reference: str = ""

    @property
    def subtotal(self) -> Decimal:
        return _money(self.total / VAT)

    @property
    def tax(self) -> Decimal:
        return self.total - self.subtotal


@dataclass
class Payment:
    day: date
    reference: str
    amount: Decimal
    allocated_to: str = ""


@dataclass
class DemoDataset:
    statement: list[StatementLine]
    bills: list[Bill]
    payments: list[Payment]
    opening_balance: Decimal
    closing_balance: Decimal
    manifest: dict[str, list[str]] = field(default_factory=dict)


def generate(invoice_count: int = 40, seed: int = 2026) -> DemoDataset:
    """Build the demo dataset. ``invoice_count`` is the number of September invoices."""
    if invoice_count < 25:
        raise ValueError("Use at least 25 invoices so every scenario can be included.")
    rng = random.Random(seed)
    manifest: dict[str, list[str]] = {k: [] for k in (
        "exact", "missing_in_accounting", "accounting_only", "accounting_only_after_period",
        "duplicate_in_accounting", "amount_mismatch", "date_mismatch", "reference_format",
        "fuzzy_typo", "within_tolerance", "credit_note_matched", "credit_note_missing",
        "payment_matched", "payment_run_grouped", "payment_amount_date", "payment_not_on_statement",
        "unresolved_blank_reference",
    )}  # fmt: skip

    # ---- September invoices on the statement --------------------------------------
    invoices: list[tuple[str, date, Decimal, str]] = []
    for i in range(invoice_count):
        number = f"INV-{24101 + i}"
        day = PERIOD_START + timedelta(days=min(28, int(i * 28 / invoice_count)))
        amount = _money(Decimal(rng.randint(4500, 950000)) / 100)
        invoices.append((number, day, amount, rng.choice(DESCRIPTIONS)))

    order = list(range(invoice_count))
    rng.shuffle(order)
    picks = iter(order)

    def take(n: int) -> list[int]:
        return [next(picks) for _ in range(n)]

    scenario_of: dict[int, str] = {}
    for name, n in (
        ("missing_in_accounting", 3), ("duplicate_in_accounting", 1), ("amount_mismatch", 2),
        ("date_mismatch", 2), ("reference_format", 4), ("fuzzy_typo", 1),
        ("within_tolerance", 1),
    ):  # fmt: skip
        for index in take(n):
            scenario_of[index] = name

    bills: list[Bill] = []
    for index, (number, day, amount, _) in enumerate(invoices):
        scenario = scenario_of.get(index, "exact")
        manifest[scenario].append(number)
        due = day + timedelta(days=30)
        digits = number.split("-")[1]
        if scenario == "missing_in_accounting":
            continue
        bill = Bill(DEMO_SUPPLIER, number, day, due, amount)
        if scenario == "amount_mismatch":
            bill.total = _money(amount / VAT)  # net keyed in instead of gross
        elif scenario == "date_mismatch":
            bill.day = day + timedelta(days=rng.randint(6, 12))
        elif scenario == "reference_format":
            bill.number = rng.choice([f"INV{digits}", f"inv {digits}", f"INV/{digits}", digits])
        elif scenario == "fuzzy_typo":
            bill.number = f"INV-{digits[:-2]}{digits[-1]}{digits[-2]}"  # transposed digits
        elif scenario == "within_tolerance":
            bill.total = amount - CENT
        bills.append(bill)
        if scenario == "duplicate_in_accounting":
            bills.append(Bill(DEMO_SUPPLIER, number, day, due, amount))

    # ---- Statement lines --------------------------------------------------------
    lines = [StatementLine(d, "Invoice", n, desc, debit=a) for n, d, a, desc in invoices]
    credit_matched = ("CN-5012", PERIOD_START + timedelta(days=9), Decimal("186.00"),
                      invoices[2][0])  # fmt: skip
    credit_missing = ("CN-5013", PERIOD_START + timedelta(days=21), Decimal("94.80"),
                      invoices[7][0])  # fmt: skip
    for ref, day, amount, related in (credit_matched, credit_missing):
        lines.append(StatementLine(day, "Credit Note", ref, f"Credit - returned goods ({related})",
                                   credit=amount))  # fmt: skip
    manifest["credit_note_matched"].append(credit_matched[0])
    manifest["credit_note_missing"].append(credit_missing[0])
    bills.append(Bill(DEMO_SUPPLIER, credit_matched[0], credit_matched[1], credit_matched[1],
                      credit_matched[2], kind="ACCPAYCREDIT", status="PAID"))  # fmt: skip

    ambiguous_amount = Decimal("432.10")
    ambiguous_day = PERIOD_START + timedelta(days=17)
    lines.append(
        StatementLine(ambiguous_day, "Invoice", "", "Delivery charge", debit=ambiguous_amount)
    )
    manifest["unresolved_blank_reference"].append("Delivery charge")
    for suffix, offset in (("24990", 0), ("24991", 1)):
        bills.append(Bill(DEMO_SUPPLIER, f"DC-{suffix}", ambiguous_day + timedelta(days=offset),
                          ambiguous_day + timedelta(days=30), ambiguous_amount))  # fmt: skip

    # Payments on the statement
    run_invoices = [invoices[0], invoices[1]]
    run_total = sum((a for _, _, a, _ in run_invoices), Decimal("0"))
    payments = [
        Payment(PERIOD_START + timedelta(days=11), "PMT-24901", OPENING_BALANCE, "INV-23988"),
        *(
            Payment(PERIOD_START + timedelta(days=25), "PMT-24902", amount, number)
            for number, _, amount, _ in run_invoices
        ),
        Payment(PERIOD_START + timedelta(days=17), "FP 88213", Decimal("1250.00")),
        # Paid just before the statement date - not yet on the supplier's statement.
        Payment(PERIOD_END - timedelta(days=1), "PMT-24904", invoices[3][2], invoices[3][0]),
    ]  # fmt: skip
    lines += [
        StatementLine(payments[0].day, "Payment", "PMT-24901", "Payment received - thank you",
                      credit=OPENING_BALANCE),
        StatementLine(payments[1].day, "Payment", "PMT-24902", "Payment received - thank you",
                      credit=run_total),
        StatementLine(payments[3].day + timedelta(days=1), "Payment", "BACS 1250",
                      "Bank transfer received", credit=Decimal("1250.00")),
    ]  # fmt: skip
    manifest["payment_matched"].append("PMT-24901")
    manifest["payment_run_grouped"].append("PMT-24902")
    manifest["payment_amount_date"].append("BACS 1250")
    manifest["payment_not_on_statement"].append("PMT-24904")

    # Accounting-only bills (one is after the statement date - timing difference)
    only_inside = Bill(DEMO_SUPPLIER, "INV-24095", PERIOD_START + timedelta(days=4),
                       PERIOD_START + timedelta(days=34), Decimal("615.40"))  # fmt: skip
    only_after = Bill(DEMO_SUPPLIER, f"INV-{24101 + invoice_count + 5}",
                      PERIOD_END + timedelta(days=2), PERIOD_END + timedelta(days=32),
                      Decimal("2875.00"))  # fmt: skip
    bills += [only_inside, only_after]
    manifest["accounting_only"].append(only_inside.number)
    manifest["accounting_only_after_period"].append(only_after.number)

    # Another supplier's bills in the same export (must be filtered out)
    for i in range(4):
        bills.append(Bill(OTHER_SUPPLIER, f"NP-{7700 + i}", PERIOD_START + timedelta(days=3 * i),
                          PERIOD_START + timedelta(days=3 * i + 30),
                          _money(Decimal(rng.randint(5000, 90000)) / 100)))  # fmt: skip

    # Running balance
    lines.sort(key=lambda line: (line.day, line.kind != "Invoice", line.reference))
    balance = OPENING_BALANCE
    for line in lines:
        balance += (line.debit or Decimal("0")) - (line.credit or Decimal("0"))
        line.balance = balance

    rng.shuffle(bills)
    return DemoDataset(lines, bills, payments, OPENING_BALANCE, balance, manifest)


# --------------------------------------------------------------------------- writers
STATEMENT_HEADER = ["Date", "Type", "Reference", "Description", "Debit", "Credit", "Balance"]
BILL_HEADER = [
    "ContactName", "InvoiceNumber", "Reference", "InvoiceDate", "DueDate", "SubTotal",
    "TaxTotal", "Total", "AmountDue", "Currency", "Type", "Status",
]  # fmt: skip
PAYMENT_HEADER = ["Payment Date", "Payment Reference", "Supplier", "Invoice Number", "Amount Paid"]


def _statement_rows(data: DemoDataset) -> list[list[str]]:
    rows = [[PERIOD_START.strftime("%d-%b-%Y"), "", "", "Balance brought forward", "", "",
             _fmt(data.opening_balance)]]  # fmt: skip
    for line in data.statement:
        rows.append([line.day.strftime("%d-%b-%Y"), line.kind, line.reference, line.description,
                     _fmt(line.debit), _fmt(line.credit), _fmt(line.balance)])  # fmt: skip
    return rows


def _bill_rows(data: DemoDataset) -> list[list[str]]:
    rows = []
    for bill in data.bills:
        amount_due = Decimal("0.00") if bill.status == "PAID" else bill.total
        rows.append([
            bill.supplier, bill.number, bill.reference, bill.day.strftime("%d/%m/%Y"),
            bill.due.strftime("%d/%m/%Y"), f"{bill.subtotal:.2f}", f"{bill.tax:.2f}",
            f"{bill.total:.2f}", f"{amount_due:.2f}", CURRENCY, bill.kind, bill.status,
        ])  # fmt: skip
    return rows


def _payment_rows(data: DemoDataset) -> list[list[str]]:
    return [
        [p.day.strftime("%d/%m/%Y"), p.reference, DEMO_SUPPLIER, p.allocated_to, f"{p.amount:.2f}"]
        for p in data.payments
    ]


def _letterhead() -> list[list[str]]:
    return [
        [f"{DEMO_SUPPLIER} - DEMO DATA (synthetic, for demonstration only)"],
        ["STATEMENT OF ACCOUNT"],
        [f"Customer: {DEMO_CLIENT}", "", f"Account: {DEMO_SUPPLIER_ACCOUNT}"],
        [f"Statement date: {PERIOD_END.strftime('%d-%b-%Y')}", "", f"Currency: {CURRENCY}"],
        [],
    ]


def _write_csv(path: Path, rows: Sequence[Sequence[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def _write_xlsx(path: Path, rows: Sequence[Sequence[object]], title: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    fixed = datetime(2026, 10, 1)  # reproducible output
    workbook.properties.created = workbook.properties.modified = fixed
    workbook.save(path)


def write_statement_pdf(path: Path, data: DemoDataset, rows_per_page: int = 28) -> None:
    """Multi-page statement laid out in columns (no grid lines), like many real ones."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)  # reproducible output
    width, height = A4
    x = [36, 100, 160, 226, 410, 480, 552]  # Date, Type, Ref, Description | Debit, Credit, Balance
    right = {4, 5, 6}
    rows = _statement_rows(data)
    pages = [rows[i : i + rows_per_page] for i in range(0, len(rows), rows_per_page)]

    for number, page_rows in enumerate(pages, start=1):
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(36, height - 50, DEMO_SUPPLIER)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(36, height - 63, "Unit 4, Example Trading Estate · DEMO DATA - not a real "
                       "company")  # fmt: skip
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(36, height - 88, "STATEMENT OF ACCOUNT")
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(36, height - 102, f"Customer: {DEMO_CLIENT}")
        pdf.drawString(300, height - 102, f"Account no: {DEMO_SUPPLIER_ACCOUNT}")
        pdf.drawString(36, height - 114, f"Statement date: {PERIOD_END.strftime('%d-%b-%Y')}")
        pdf.drawString(300, height - 114, f"Currency: {CURRENCY}")

        y = height - 142
        pdf.setFont("Helvetica-Bold", 8.5)
        for index, title in enumerate(STATEMENT_HEADER):
            if index in right:
                pdf.drawRightString(x[index], y, title)
            else:
                pdf.drawString(x[index], y, title)
        pdf.line(36, y - 4, width - 36, y - 4)
        pdf.setFont("Helvetica", 8.5)
        for row in page_rows:
            y -= 15
            for index, value in enumerate(row):
                if not value:
                    continue
                if index in right:
                    pdf.drawRightString(x[index], y, value)
                else:
                    pdf.drawString(x[index], y, value[:34])
        if number == len(pages):
            y -= 24
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(226, y, "Closing balance")
            pdf.drawRightString(x[6], y, _fmt(data.closing_balance))
            y -= 30
            pdf.setFont("Helvetica", 8)
            pdf.drawString(36, y, "Current        30 days        60 days        90+ days")
            pdf.drawString(36, y - 12, f"{_fmt(data.closing_balance)}        0.00        0.00"
                           "        0.00")  # fmt: skip
        else:
            pdf.setFont("Helvetica-Oblique", 8)
            pdf.drawString(226, y - 18, "Continued on next page")
        pdf.setFont("Helvetica", 7.5)
        pdf.drawCentredString(width / 2, 28, f"Page {number} of {len(pages)} · DEMO DATA")
        pdf.showPage()
    pdf.save()


def write_files(data: DemoDataset, output_dir: Path) -> dict[str, Path]:
    """Write CSV, XLSX and PDF versions of the demo files plus the manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "statement_csv": output_dir / "supplier_statement.csv",
        "statement_xlsx": output_dir / "supplier_statement.xlsx",
        "statement_pdf": output_dir / "supplier_statement.pdf",
        "accounting_csv": output_dir / "accounting_bills.csv",
        "accounting_xlsx": output_dir / "accounting_bills.xlsx",
        "payments_csv": output_dir / "payments.csv",
        "manifest": output_dir / "demo_manifest.json",
    }
    statement = _statement_rows(data)
    closing = [["", "", "", "Closing balance", "", "", _fmt(data.closing_balance)]]
    _write_csv(paths["statement_csv"], [*_letterhead(), STATEMENT_HEADER, *statement, *closing])
    _write_xlsx(paths["statement_xlsx"], [*_letterhead(), STATEMENT_HEADER, *statement, *closing],
                "Statement")  # fmt: skip
    write_statement_pdf(paths["statement_pdf"], data)
    _write_csv(paths["accounting_csv"], [BILL_HEADER, *_bill_rows(data)])
    _write_xlsx(paths["accounting_xlsx"], [BILL_HEADER, *_bill_rows(data)], "Bills")
    _write_csv(paths["payments_csv"], [PAYMENT_HEADER, *_payment_rows(data)])
    paths["manifest"].write_text(
        json.dumps(
            {
                "note": "DEMO DATA - synthetic and fictional. Expected scenario membership.",
                "supplier": DEMO_SUPPLIER,
                "period_start": PERIOD_START.isoformat(),
                "period_end": PERIOD_END.isoformat(),
                "opening_balance": str(data.opening_balance),
                "closing_balance": str(data.closing_balance),
                "scenarios": data.manifest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return paths


def create_demo_reconciliation(reconciliations, importer, work_dir: Path,
                               invoice_count: int = 40) -> int:  # fmt: skip
    """Create a fully imported (not yet run) DEMO reconciliation in the application."""
    from src.services.import_service import FileRole
    from src.services.reconciliation_service import NewReconciliation

    data = generate(invoice_count)
    paths = write_files(data, work_dir)
    recon_id = reconciliations.create(
        NewReconciliation(
            supplier_name=DEMO_SUPPLIER,
            supplier_account=DEMO_SUPPLIER_ACCOUNT,
            client_name=DEMO_CLIENT,
            currency=CURRENCY,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            notes="DEMO DATA - synthetic records for demonstration only.",
            is_demo=True,
        )
    )
    for role, key in (
        (FileRole.STATEMENT, "statement_pdf"),
        (FileRole.ACCOUNTING, "accounting_csv"),
        (FileRole.PAYMENTS, "payments_csv"),
    ):
        path = paths[key]
        importer.upload(recon_id, role, path.name, path.read_bytes())
    return recon_id
