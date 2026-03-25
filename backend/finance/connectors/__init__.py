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

# Import conditionnel de BoursoBankConnector et HelloBankConnector (nécessitent Playwright)
try:
    from finance.connectors.boursorama import BoursoBankConnector
    from finance.connectors.hellobank import HelloBankConnector
    __all__ = [
        "BaseBankConnector",
        "BankConnectionError",
        "AuthenticationError",
        "RateLimitError",
        "ConnectionTimeoutError",
        "InvalidCredentialsError",
        "TradeRepublicConnector",
        "PowensConnector",
        "BoursoBankConnector",
        "HelloBankConnector",
    ]
except ImportError:
    # Si Playwright n'est pas disponible, essayer d'importer seulement BoursoBankConnector
    try:
        from finance.connectors.boursorama import BoursoBankConnector
        __all__ = [
            "BaseBankConnector",
            "BankConnectionError",
            "AuthenticationError",
            "RateLimitError",
            "ConnectionTimeoutError",
            "InvalidCredentialsError",
            "TradeRepublicConnector",
            "BoursoBankConnector",
        ]
    except ImportError:
        __all__ = [
            "BaseBankConnector",
            "BankConnectionError",
            "AuthenticationError",
            "RateLimitError",
            "ConnectionTimeoutError",
            "InvalidCredentialsError",
            "TradeRepublicConnector",
        ]
