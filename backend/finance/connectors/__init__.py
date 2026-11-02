"""
Module de connecteurs bancaires.

Ce module fournit une architecture modulaire pour intégrer différents providers bancaires
(BoursoBank, Hello Bank, Trade Republic, etc.) de manière uniforme.
"""

from finance.connectors.base import (
    BaseBankConnector,
    BankConnectionError,
    AuthenticationError,
    RateLimitError,
    ConnectionTimeoutError,
    InvalidCredentialsError,
)
from finance.connectors.traderepublic import TradeRepublicConnector

from finance.connectors.enablebanking import EnableBankingConnector

__all__ = [
    "BaseBankConnector",
    "BankConnectionError",
    "AuthenticationError",
    "RateLimitError",
    "ConnectionTimeoutError",
    "InvalidCredentialsError",
    "TradeRepublicConnector",
    "EnableBankingConnector",
]

# BoursoBankConnector nécessite boursobank-scraper (Playwright, Python 3.13+).
try:
    from finance.connectors.boursorama import BoursoBankConnector  # noqa: F401

    __all__.append("BoursoBankConnector")
except ImportError:
    pass
