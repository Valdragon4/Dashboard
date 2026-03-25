"""
Connecteur Powens (ex-Budget Insight) pour synchronisation automatique.

Ce connecteur utilise l'API Powens pour récupérer les transactions et soldes
depuis les comptes bancaires connectés via Powens.

Documentation API : https://docs.powens.com/
"""

import logging
import requests
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from django.utils import timezone

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    RateLimitError,
    BaseBankConnector,
)

logger = logging.getLogger(__name__)


class PowensConnector(BaseBankConnector):
    """
    Connecteur Powens pour synchronisation automatique.
    
    Utilise l'API Powens pour se connecter aux banques et récupérer les données.
    Powens gère l'authentification avec les banques via PSD2.
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.powens.com"):
        """
        Initialise le connecteur Powens.
        
        Args:
            api_key: Clé API Powens
            api_secret: Secret API Powens
            base_url: URL de base de l'API Powens (par défaut: production)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip('/')
        self.session_token = None
        self.user_id = None
        
    @property
    def provider_name(self) -> str:
        """Retourne le nom du provider."""
        return "Powens"

    def _get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        """
        Retourne les headers HTTP pour les requêtes API.
        
        Args:
            include_auth: Si True, inclut le token d'authentification
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        if include_auth and self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        
        return headers

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        timeout: int = 30,
    ) -> Dict:
        """
        Effectue une requête HTTP vers l'API Powens.
        
        Args:
            method: Méthode HTTP (GET, POST, etc.)
            endpoint: Endpoint API (ex: "/users")
            data: Données à envoyer (pour POST/PUT)
            params: Paramètres de requête (pour GET)
            timeout: Timeout en secondes
            
        Returns:
            dict: Réponse JSON de l'API
            
        Raises:
            ConnectionTimeoutError: Si la requête timeout
            RateLimitError: Si le rate limit est atteint
            AuthenticationError: Si l'authentification échoue
            BankConnectionError: Pour toute autre erreur
        """
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                json=data,
                params=params,
                headers=self._get_headers(),
                auth=(self.api_key, self.api_secret) if not self.session_token else None,
                timeout=timeout,
            )
            
            # Gérer les erreurs HTTP
            if response.status_code == 401:
                raise AuthenticationError("Invalid Powens API credentials")
            elif response.status_code == 429:
                raise RateLimitError("Powens API rate limit exceeded")
            elif response.status_code >= 500:
                raise BankConnectionError(f"Powens API server error: {response.status_code}")
            elif not response.ok:
                error_msg = response.json().get("error", {}).get("message", response.text)
                raise BankConnectionError(f"Powens API error: {error_msg}")
            
            return response.json()
            
        except requests.exceptions.Timeout:
            raise ConnectionTimeoutError(f"Timeout connecting to Powens API: {endpoint}")
        except requests.exceptions.RequestException as e:
            raise BankConnectionError(f"Error connecting to Powens API: {str(e)}")

    def authenticate(self, credentials: Dict) -> Dict:
        """
        Authentifie le connecteur avec Powens.
        
        Pour Powens, l'authentification se fait en deux étapes :
        1. Créer ou récupérer un utilisateur Powens
        2. Connecter une banque (via le flux PSD2 géré par Powens)
        
        Args:
            credentials: Dictionnaire contenant :
                        - "user_id" (optionnel) : ID utilisateur Powens existant
                        - "connection_id" (optionnel) : ID de connexion bancaire existante
                        - Pour créer une nouvelle connexion, Powens gère le flux OAuth
        
        Returns:
            dict: Informations de session contenant :
                  - "user_id": ID utilisateur Powens
                  - "connection_id": ID de connexion bancaire (si disponible)
                  - "requires_bank_connection": True si une nouvelle connexion est nécessaire
        """
        try:
            # Si un user_id est fourni, l'utiliser, sinon créer un nouvel utilisateur
            user_id = credentials.get("user_id")
            
            if not user_id:
                # Créer un nouvel utilisateur Powens
                logger.info("Creating new Powens user")
                user_data = self._make_request("POST", "/users", data={})
                user_id = user_data.get("id")
                
                if not user_id:
                    raise AuthenticationError("Failed to create Powens user")
                
                logger.info(f"Created Powens user: {user_id}")
            
            self.user_id = user_id
            
            # Vérifier si une connexion bancaire existe déjà
            connection_id = credentials.get("connection_id")
            
            if connection_id:
                # Vérifier que la connexion existe et est valide
                try:
                    connection = self._make_request("GET", f"/users/{user_id}/connections/{connection_id}")
                    if connection.get("status") == "valid":
                        logger.info(f"Using existing Powens connection: {connection_id}")
                        return {
                            "user_id": user_id,
                            "connection_id": connection_id,
                            "requires_bank_connection": False,
                        }
                except BankConnectionError:
                    logger.warning(f"Connection {connection_id} not found or invalid, will create new one")
            
            # Si pas de connexion valide, retourner les infos pour créer une nouvelle connexion
            return {
                "user_id": user_id,
                "connection_id": None,
                "requires_bank_connection": True,
            }
            
        except (AuthenticationError, RateLimitError, ConnectionTimeoutError):
            raise
        except Exception as e:
            logger.error(f"Error during Powens authentication: {str(e)}")
            raise AuthenticationError(f"Failed to authenticate with Powens: {str(e)}")

    def create_bank_connection(self, bank_id: str, redirect_url: str) -> Dict:
        """
        Crée une nouvelle connexion bancaire via Powens.
        
        Cette méthode initie le flux OAuth pour connecter une banque.
        L'utilisateur doit être redirigé vers l'URL retournée pour autoriser l'accès.
        
        Args:
            bank_id: ID de la banque dans Powens (ex: "boursorama", "hellobank")
            redirect_url: URL de redirection après autorisation
            
        Returns:
            dict: Contenant :
                  - "auth_url": URL vers laquelle rediriger l'utilisateur
                  - "connection_id": ID de la connexion (sera complété après autorisation)
        """
        if not self.user_id:
            raise AuthenticationError("User must be authenticated before creating bank connection")
        
        try:
            data = {
                "bank_id": bank_id,
                "redirect_url": redirect_url,
            }
            
            response = self._make_request(
                "POST",
                f"/users/{self.user_id}/connections",
                data=data,
            )
            
            auth_url = response.get("redirect_url")
            connection_id = response.get("id")
            
            if not auth_url:
                raise BankConnectionError("Failed to get authorization URL from Powens")
            
            return {
                "auth_url": auth_url,
                "connection_id": connection_id,
            }
            
        except Exception as e:
            logger.error(f"Error creating Powens bank connection: {str(e)}")
            raise BankConnectionError(f"Failed to create bank connection: {str(e)}")

    def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
        """
        Récupère les transactions depuis Powens.
        
        Args:
            account: Objet Account Django avec :
                    - account.bank_connection.encrypted_credentials contenant user_id et connection_id
                    - account.external_id peut contenir le connection_id Powens
            since: Date optionnelle de la dernière synchronisation
        
        Returns:
            list: Liste de dictionnaires de transactions au format standard
        """
        if not self.user_id:
            raise AuthenticationError("User must be authenticated before syncing transactions")
        
        # Récupérer le connection_id depuis les credentials ou external_id
        connection_id = account.external_id
        if not connection_id:
            # Essayer de récupérer depuis les credentials
            from finance.services.encryption_service import EncryptionService
            credentials = EncryptionService.decrypt_credentials(
                account.bank_connection.encrypted_credentials
            )
            connection_id = credentials.get("connection_id")
        
        if not connection_id:
            raise BankConnectionError("No Powens connection ID found for this account")
        
        try:
            # Construire les paramètres de requête
            params = {}
            if since:
                # Convertir en timestamp Unix
                params["from"] = int(since.timestamp())
            
            # Récupérer les transactions depuis Powens
            response = self._make_request(
                "GET",
                f"/users/{self.user_id}/connections/{connection_id}/transactions",
                params=params,
            )
            
            transactions = response.get("transactions", [])
            
            # Convertir au format standard
            formatted_transactions = []
            for tx in transactions:
                try:
                    # Parse la date
                    posted_at_str = tx.get("date") or tx.get("value_date")
                    if posted_at_str:
                        # Powens retourne généralement des dates ISO 8601
                        posted_at = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
                        # S'assurer que c'est timezone-aware
                        if posted_at.tzinfo is None:
                            posted_at = timezone.make_aware(posted_at)
                    else:
                        posted_at = timezone.now()
                    
                    # Parse le montant (Powens retourne généralement en centimes)
                    amount_raw = tx.get("amount") or tx.get("value")
                    if amount_raw is None:
                        continue
                    
                    # Convertir en Decimal (si c'est en centimes, diviser par 100)
                    amount = Decimal(str(amount_raw))
                    if abs(amount) > 1000000:  # Probablement en centimes
                        amount = amount / 100
                    
                    # Description
                    description = tx.get("label") or tx.get("description") or tx.get("wording") or ""
                    
                    formatted_transactions.append({
                        "posted_at": posted_at,
                        "amount": amount,
                        "description": description,
                        "raw": tx,  # Conserver les données brutes
                    })
                    
                except Exception as e:
                    logger.warning(f"Error formatting transaction: {str(e)}, skipping")
                    continue
            
            logger.info(f"Retrieved {len(formatted_transactions)} transactions from Powens")
            return formatted_transactions
            
        except Exception as e:
            logger.error(f"Error syncing transactions from Powens: {str(e)}")
            raise BankConnectionError(f"Failed to sync transactions: {str(e)}")

    def get_balance(self, account) -> Decimal:
        """
        Récupère le solde actuel du compte depuis Powens.
        
        Args:
            account: Objet Account Django
            
        Returns:
            Decimal: Solde actuel du compte
        """
        if not self.user_id:
            raise AuthenticationError("User must be authenticated before getting balance")
        
        connection_id = account.external_id
        if not connection_id:
            from finance.services.encryption_service import EncryptionService
            credentials = EncryptionService.decrypt_credentials(
                account.bank_connection.encrypted_credentials
            )
            connection_id = credentials.get("connection_id")
        
        if not connection_id:
            raise BankConnectionError("No Powens connection ID found for this account")
        
        try:
            # Récupérer les comptes depuis la connexion
            response = self._make_request(
                "GET",
                f"/users/{self.user_id}/connections/{connection_id}/accounts",
            )
            
            accounts = response.get("accounts", [])
            
            # Trouver le compte correspondant (par IBAN ou nom)
            account_number = account.account_number
            for acc in accounts:
                if acc.get("iban") == account_number or acc.get("number") == account_number:
                    balance_raw = acc.get("balance") or acc.get("amount")
                    if balance_raw is not None:
                        balance = Decimal(str(balance_raw))
                        # Si c'est en centimes, diviser par 100
                        if abs(balance) > 1000000:
                            balance = balance / 100
                        return balance
            
            # Si pas trouvé, retourner 0 ou lever une erreur
            logger.warning(f"Account {account_number} not found in Powens connection")
            return Decimal("0.00")
            
        except Exception as e:
            logger.error(f"Error getting balance from Powens: {str(e)}")
            raise BankConnectionError(f"Failed to get balance: {str(e)}")

    def disconnect(self) -> None:
        """Ferme la connexion et nettoie les ressources."""
        self.session_token = None
        self.user_id = None
        logger.info("Disconnected from Powens")
