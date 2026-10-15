"""QuickBooks Online provider - architecture placeholder for a future integration.

Planned implementation (not yet built):

* OAuth 2.0 using ``QUICKBOOKS_CLIENT_ID``, ``QUICKBOOKS_CLIENT_SECRET`` and
  ``QUICKBOOKS_REDIRECT_URI``; the company (realm) ID is captured during
  authorisation.
* Bills via the query endpoint (``SELECT * FROM Bill WHERE VendorRef = '<id>'``),
  vendor credits (``VendorCredit``) and bill payments (``BillPayment``).
* Responses mapped to :class:`~src.models.transaction.CanonicalTransaction`.

Until then the provider reports its status honestly and returns no data.
"""

from __future__ import annotations

from src.accounting.oauth_provider import OAuthAccountingProvider


class QuickBooksProvider(OAuthAccountingProvider):
    name = "QuickBooks"
    display_name = "QuickBooks Online"
    env_prefix = "QUICKBOOKS"
