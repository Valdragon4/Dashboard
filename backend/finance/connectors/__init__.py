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
from finance.connectors.powens import PowensConnector

# Import conditionnel de BoursoBankConnector et HelloBankConnector
try:
    from finance.connectors.boursorama import BoursoBankConnector
except ImportError:
    BoursoBankConnector = None

try:
    from finance.connectors.hellobank import HelloBankConnector
except ImportError:
    HelloBankConnector = None

__all__ = [
    "BaseBankConnector",
    "BankConnectionError",
    "AuthenticationError",
    "RateLimitError",
    "ConnectionTimeoutError",
    "InvalidCredentialsError",
    "TradeRepublicConnector",
    "PowensConnector",
]

if BoursoBankConnector is not None:
    __all__.append("BoursoBankConnector")
if HelloBankConnector is not None:
    __all__.append("HelloBankConnector")
