"""Shared base for future OAuth-based accounting API providers.

No API calls are implemented yet. These classes exist so the rest of the
application (Settings page, provider selection, engine) is already shaped for
the integrations, and so that the configuration status is reported honestly.
"""

from __future__ import annotations

import os

from src.accounting.base import (
    AccountingDataKind,
    AccountingLoadResult,
    AccountingProvider,
    AccountingQuery,
    IntegrationNotAvailableError,
)
from src.parsers.columns import ColumnMapping


class OAuthAccountingProvider(AccountingProvider):
    """Reads OAuth client settings from environment variables only."""

    env_prefix: str = ""
    display_name: str = ""

    @property
    def _env_names(self) -> tuple[str, str, str]:
        p = self.env_prefix
        return f"{p}_CLIENT_ID", f"{p}_CLIENT_SECRET", f"{p}_REDIRECT_URI"

    def missing_settings(self) -> list[str]:
        return [name for name in self._env_names if not os.getenv(name, "").strip()]

    def is_configured(self) -> bool:
        return not self.missing_settings()

    def status_message(self) -> str:
        if not self.is_configured():
            return f"{self.display_name} integration: Not configured"
        return (
            f"{self.display_name} integration: credentials found, but the API connector is "
            "not implemented in this version. Use a CSV/XLSX export instead."
        )

    def load(
        self,
        query: AccountingQuery,
        kind: AccountingDataKind = AccountingDataKind.BILLS,
        mapping: ColumnMapping | None = None,
    ) -> AccountingLoadResult:
        raise IntegrationNotAvailableError(self.status_message())
