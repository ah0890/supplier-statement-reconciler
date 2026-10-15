"""Xero provider - architecture placeholder for a future integration.

Planned implementation (not yet built):

* OAuth 2.0 authorisation-code flow using ``XERO_CLIENT_ID``,
  ``XERO_CLIENT_SECRET`` and ``XERO_REDIRECT_URI``; tokens stored encrypted,
  never logged.
* Bills: Accounting API ``GET /Invoices`` filtered to ``Type=="ACCPAY"`` and the
  supplier's ``ContactID``; supplier credit notes: ``GET /CreditNotes``
  (``ACCPAYCREDIT``); payments: ``GET /Payments``.
* Responses mapped to :class:`~src.models.transaction.CanonicalTransaction` so the
  reconciliation engine needs no changes.

Until then the provider reports "Not configured" (or "not implemented" when
credentials exist) and never returns fabricated data.
"""

from __future__ import annotations

from src.accounting.oauth_provider import OAuthAccountingProvider


class XeroProvider(OAuthAccountingProvider):
    name = "Xero"
    display_name = "Xero"
    env_prefix = "XERO"
