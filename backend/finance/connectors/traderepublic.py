"""
Compat shim for legacy Trade Republic connector.

The old scraper/importer stack has been removed in favor of the Bun bridge flow.
This connector remains only to provide a clear error message where legacy paths
are still invoked.
"""

from __future__ import annotations

from finance.connectors.base import BankConnectionError, BaseBankConnector


class TradeRepublicConnector(BaseBankConnector):
    @property
    def provider_name(self) -> str:
        return "Trade Republic (legacy disabled)"

    def authenticate(self, credentials):
        raise BankConnectionError(
            "Le connecteur Trade Republic legacy est désactivé. "
            "Utilisez la synchronisation via le bridge (panel Connexions Bancaires)."
        )

    def fetch_transactions(self, session_data, from_date=None):
        return []

    def fetch_balance(self, session_data):
        return None

    def fetch_portfolio(self, session_data):
        return None
