"""
Classe abstraite de base pour tous les connecteurs bancaires.

Ce module définit l'interface commune que tous les connecteurs bancaires doivent implémenter,
ainsi que les exceptions standardisées pour la gestion des erreurs.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Optional


class BankConnectionError(Exception):
    """Exception de base pour toutes les erreurs de connecteur bancaire."""

    pass


class AuthenticationError(BankConnectionError):
    """Erreur lors de l'authentification."""

    pass


class InvalidCredentialsError(AuthenticationError):
    """Credentials invalides fournis."""

    pass


class RateLimitError(BankConnectionError):
    """Erreur de rate limiting de l'API bancaire."""

    pass


class ConnectionTimeoutError(BankConnectionError):
    """Timeout lors de la connexion à l'API bancaire."""

    pass


class BaseBankConnector(ABC):
    """
    Classe abstraite de base pour tous les connecteurs bancaires.

    Cette classe définit l'interface commune que tous les connecteurs doivent implémenter
    pour permettre une intégration uniforme dans le système de synchronisation automatique.

    Les connecteurs doivent être indépendants de Django (classes Python pures) pour faciliter
    les tests et la réutilisabilité. L'intégration avec Django se fait via les services.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Retourne le nom du provider bancaire.

        Returns:
            str: Nom du provider (ex: "Trade Republic", "BoursoBank", "Hello Bank")
        """
        pass

    @abstractmethod
    def authenticate(self, credentials: dict) -> dict:
        """
        Authentifie le connecteur avec les credentials fournis.

        Cette méthode doit gérer l'authentification complète avec le provider bancaire,
        incluant si nécessaire l'authentification 2FA.

        Args:
            credentials: Dictionnaire contenant les credentials nécessaires
                        (ex: {"username": "...", "password": "...", "2fa_code": "..."})

        Returns:
            dict: Informations de session nécessaires pour les appels suivants
                  (ex: {"token": "...", "session_id": "...", "expires_at": "..."})

        Raises:
            AuthenticationError: Si l'authentification échoue
            InvalidCredentialsError: Si les credentials sont invalides
            ConnectionTimeoutError: Si la connexion timeout
        """
        pass

    @abstractmethod
    def sync_transactions(self, account, since: Optional[datetime] = None) -> list:
        """
        Récupère les transactions depuis la dernière synchronisation.

        Cette méthode doit récupérer toutes les transactions depuis la date spécifiée
        (ou toutes les transactions si `since` est None) et les retourner sous forme
        de liste de dictionnaires.

        Args:
            account: Objet Account Django représentant le compte à synchroniser
            since: Date optionnelle de la dernière synchronisation. Si None, récupère
                   toutes les transactions disponibles.

        Returns:
            list: Liste de dictionnaires représentant les transactions. Chaque dictionnaire
                  doit contenir au minimum les clés suivantes:
                  - "posted_at": datetime de la transaction
                  - "amount": Decimal du montant (positif pour revenus, négatif pour dépenses)
                  - "description": str description de la transaction
                  - "raw": dict métadonnées supplémentaires (optionnel)

        Raises:
            AuthenticationError: Si la session a expiré et nécessite une ré-authentification
            RateLimitError: Si le rate limit de l'API est atteint
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        pass

    @abstractmethod
    def get_balance(self, account) -> Decimal:
        """
        Récupère le solde actuel du compte.

        Args:
            account: Objet Account Django représentant le compte

        Returns:
            Decimal: Solde actuel du compte

        Raises:
            AuthenticationError: Si la session a expiré et nécessite une ré-authentification
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """
        Ferme la connexion et nettoie les ressources.

        Cette méthode doit fermer proprement toutes les connexions ouvertes
        (sessions HTTP, WebSocket, etc.) et libérer les ressources utilisées.
        """
        pass

    def sync_portfolio_valuations(self, account) -> dict:
        """
        Synchronise les valorisations de portefeuille (optionnel).

        Cette méthode est optionnelle et retourne un dictionnaire vide par défaut.
        Elle doit être surchargée pour les providers supportant les investissements
        (Trade Republic, etc.).

        Args:
            account: Objet Account Django représentant le compte

        Returns:
            dict: Valorisations par type de portefeuille. Format:
                  {
                      "PEA": Decimal(...),
                      "CTO": Decimal(...),
                      "CRYPTO": Decimal(...),
                      ...
                  }
                  Retourne un dict vide par défaut si non implémenté.

        Raises:
            AuthenticationError: Si la session a expiré et nécessite une ré-authentification
            ConnectionTimeoutError: Si la connexion timeout
            BankConnectionError: Pour toute autre erreur de connexion
        """
        return {}
