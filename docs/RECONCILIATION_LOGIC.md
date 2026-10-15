# Reconciliation Logic

This page explains, with examples, exactly how the application decides each result.
Defaults can be changed on the **Settings** page; every run records the settings it used.

## 1. Reading the files

Each file becomes a list of **canonical transactions**: date, type (invoice / credit note /
payment), reference, amount (always positive), supplier, currency and the original row.

- **Columns** are detected from header names ("Inv #", "InvoiceNumber", "Document" → invoice
  number) and checked against their content. A "Date" column that contains no dates gets a
  low confidence and is flagged for the user to confirm.
- **Rows that are not transactions** are skipped and counted: blank lines, repeated headers on
  later pages, page totals, ageing summaries, letterhead text, wrapped description lines
  (merged into the line above), and opening/closing balance lines (captured as balances).
- **Type** comes from a type column (`Invoice`, `Credit Note`, `ACCPAYCREDIT`, `Vendor Credit`,
  Sage `PI`/`PC`/`PP`), then from reference patterns (`CN-…` = credit note, `PMT-…` =
  payment), then from keywords ("Payment received"), and finally from the debit/credit side.
- **Amounts** accept `1,234.56`, `1.234,56`, `£1,234.56`, `(150.00)`, `150.00-`, `150.00 CR`.
  Anything that cannot be read safely is an **error**, never a guess.
- **Dates** accept ISO, `DD/MM/YYYY`, `01-Oct-2026`, `1st Oct 2026` and Excel dates. When every
  date in a file is ambiguous (all days ≤ 12), the user is warned and asked to confirm.

## 2. Normalisation

| Original | Normalised | Why |
|---|---|---|
| `INV-00123`, `INV00123`, `inv 00123`, `INV/00123`, `Invoice No: INV-00123` | `INV00123` | Case, spaces, `- / _ . # :` and label prefixes never distinguish documents |
| `INV-0123` vs `INV-123` | `INV0123` vs `INV123` | **Kept different.** Leading zeros might be meaningful |
| `INV-123A` vs `INV-123` | different | Letters are always kept |
| `ABC Ltd`, `ABC LIMITED`, `ABC LTD.`, `The ABC Limited` | `ABC` | Only trailing legal-form words and a leading "The" are removed |

A looser **core** key (significant digits only: `INV-00123` → `123`) is used **only** to find
candidates, and only together with agreeing amounts or dates.

## 3. Duplicate detection (each side separately)

| Evidence | Confidence | Effect |
|---|---|---|
| Same supplier, reference, amount and date | 98 | Extra copy reported as **DUPLICATE**, removed from matching |
| Same reference and amount, dates within tolerance | 92 | Same |
| Same reference and amount, dates further apart | 85 | Same |
| Same reference, different amount | 65 | Possible re-issue: both copies stay in matching; the unmatched one is reported |
| Same amount and date, very similar reference (≥ 85% similar) | 75 | Reported only if otherwise unmatched |
| Same amount and date, one reference blank | 60 | Reported only if otherwise unmatched |

*Example:* the accounting export contains `INV-24106` twice (6,347.26, 04-Sep). The statement
shows it once. One bill matches; the other is **DUPLICATE (98)**: "very likely entered twice".

## 4. Matching levels

Each level runs across **all** remaining records before the next starts, so a weak method
can never take a record that a stronger method would have matched.
Invoices only match invoices, and credit notes only match credit notes. Different
currencies never match.

| Level | Rule | Confidence | Example |
|---|---|---|---|
| 1 | Same supplier + same normalised reference | 100 (−4 if amounts differ, −3 if dates differ, further penalties for very large gaps) | `INV-10025` ↔ `inv 10025` |
| 2 | Same digit core + amount within tolerance | 90 | `INV-0045123` ↔ `45123`, both 250.00 |
| 3 | Same digit core + date within tolerance | 82 | `INV-45123` ↔ `45123`, same day, amounts differ |
| 4 | Fuzzy reference ≥ threshold (default 80%) **and** amount agrees (or ≥ 95% similar with agreeing date) | 50–89 | `INV-10025` ↔ `INV-10052` (transposed), same amount |
| 5 | Amount within tolerance + date within tolerance, **only if exactly one candidate** | 60–79 | Statement line with no reference ↔ the single bill of 321.45 a day later |

Rules that prevent false matches:

- Sequential invoice numbers (`INV-1003` vs `INV-1009`) are always similar, so similarity
  alone is never enough. Level 4 needs an agreeing amount.
- Level 5 refuses ambiguous cases. Two bills of 432.10 on similar dates → **UNRESOLVED**, with
  both candidates listed for the user.
- Proposals are assigned greedily, best first. A record is used at most once.

## 5. Confidence bands and review

| Score | Band | Default behaviour |
|---|---|---|
| 95–100 | Very strong | Accepted automatically |
| 80–94 | Strong | Needs review |
| 60–79 | Possible | Needs review |
| < 60 | Unresolved | Not matched |

Fuzzy (level 4) and proximity (level 5) matches are capped below 90 and **always** need
review. A weak match is never shown as an exact match. All thresholds are configurable.

## 6. Mismatch rules for matched pairs

**Amount** (default tolerance 0.01):

| Statement | Accounting | Difference | Result |
|---|---|---|---|
| 100.00 | 100.00 | 0.00 | EXACT_MATCH → **MATCHED** |
| 100.01 | 100.00 | 0.01 | MATCH_WITHIN_TOLERANCE → **MATCHED_WITH_TOLERANCE** |
| 10,500.00 | 10,250.00 | 250.00 | **AMOUNT_MISMATCH** |

**Date** (default tolerance 3 days): 0 days → matched; 1–3 days → within tolerance;
4+ days → **DATE_MISMATCH**. A missing date is reported but is not a mismatch.

**Priority** when several differ: AMOUNT_MISMATCH → REFERENCE_MISMATCH → DATE_MISMATCH →
MATCHED_WITH_TOLERANCE → MATCHED. Every detected issue is still listed in the reason.

*Example (from the specification):*

```
SUPPLIER STATEMENT              ACCOUNTING
Invoice  INV-10025              Invoice  INV-10025
Date     01-Oct-2026            Date     05-Oct-2026
Amount   10,500.00              Amount   10,250.00
Result: AMOUNT_MISMATCH · difference 250.00 · 4 days apart (also beyond tolerance)
Method: exact reference · confidence 93
```

## 7. Missing and accounting-only items

- On the statement, not matched → **MISSING_IN_ACCOUNTING**, with date, amount, due date and description.
- In the accounting records, not matched → **NOT_ON_SUPPLIER_STATEMENT**. If it is dated after the
  statement date, the reason says *"likely a timing difference"*.
- In multi-supplier exports, other suppliers' rows are excluded first (by name or account number).

## 8. Credit notes

Credit notes are matched like invoices (never against invoices). A clean match shows as
**CREDIT_NOTE** and mentions the related invoice when known. Credit notes reduce balances
and are allocated to their related invoice where one is given.

## 9. Payments

1. A statement payment is matched to recorded payments by **reference**. A *payment run*
   (one statement payment ↔ several recorded rows with the same reference, one per invoice)
   matches when the rows add up to the statement amount.
2. Otherwise by **amount + date** within the payment window (default 7 days), only when there
   is exactly one candidate.
3. Differences, unmatched statement payments and recorded payments missing from the statement
   → **PAYMENT_MISMATCH** (with a timing note after the statement date).
4. **No payments export uploaded** → statement payments are marked "could not be verified"
   (UNRESOLVED). They are never treated as matched.

**Allocation** uses explicit links only:

```
Outstanding = Invoice amount − allocated payments − allocated credits
INV-1 1,000.00 − PMT-1 600.00 − CN-1 100.00 = 300.00 (PART_PAID)
```

Payments without an allocation are listed as unallocated; the application **never assumes**
which invoice they paid. Over-allocation → PAYMENT_MISMATCH ("overpaid by …").

## 10. Balance reconciliation

**Statement arithmetic**

```
Opening balance            3,250.00
+ Invoices               205,929.51
− Credit notes               280.80
− Payments                11,108.31
= Expected closing       197,790.40
Supplier closing         197,790.40   → BALANCED
```

If the opening or closing balance is missing, the result is **BALANCE_CHECK_NOT_POSSIBLE**
with the reason. Balances can be typed in on the Supplier Statement page. A statement with
no opening balance whose items add up to the closing balance is treated as an *open-item*
statement. Running balances are also checked line by line, which catches lines that were
not extracted.

**Statement vs accounting.** The period is replayed through the accounting records from the
same opening balance. The difference is then explained by category. Each transaction appears in
exactly one result, so the categories add up to the whole difference:

```
Supplier closing − accounting balance      8,755.86
  Items missing in accounting             15,972.50
  Accounting items not on statement       −3,922.50
  Amount differences                       1,931.90
  Duplicates                              −6,779.36
  Payment differences                      1,121.22
  Unresolved items                           432.10
Unexplained                                    0.00
```

An unexplained remainder means something was not imported (e.g. rows excluded because of
errors) and is shown prominently.

## 11. Suggested outcome

| Condition | Suggested outcome |
|---|---|
| Statement arithmetic does not balance, or reviews are pending, or part of the difference is unexplained | NOT_RECONCILED |
| No exceptions | RECONCILED |
| Exceptions exist and fully explain the difference | RECONCILED_WITH_EXCEPTIONS |

The user always makes the final decision and can add a note. The decision is recorded in
the audit trail.
