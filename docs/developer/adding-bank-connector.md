# Guide Développeur : Ajouter un Nouveau Connecteur Bancaire

Ce guide explique comment ajouter un nouveau connecteur bancaire au système de synchronisation automatique.

## Architecture des Connecteurs

### Classe Abstraite `BaseBankConnector`

Tous les connecteurs bancaires héritent de la classe abstraite `BaseBankConnector` définie dans `finance/connectors/base.py`. Cette classe définit l'interface commune que tous les connecteurs doivent implémenter.

#### Méthodes à Implémenter

1. **`provider_name`** (property) : Retourne le nom du provider
2. **`authenticate(credentials)`** : Authentifie le connecteur
3. **`sync_transactions(account, since)`** : Récupère les transactions
4. **`get_balance(account)`** : Récupère le solde actuel
5. **`disconnect()`** : Ferme la connexion et nettoie les ressources

#### Méthode Optionnelle

- **`sync_portfolio_valuations(account)`** : Synchronise les valorisations de portefeuille (pour les courtiers)

### Exceptions Standardisées

Le module `base.py` définit plusieurs exceptions pour la gestion des erreurs :

- `BankConnectionError` : Exception de base
- `AuthenticationError` : Erreur d'authentification
- `InvalidCredentialsError` : Credentials invalides
- `RateLimitError` : Rate limiting de l'API
- `ConnectionTimeoutError` : Timeout de connexion

## Étapes pour Créer un Nouveau Connecteur

### Étape 1 : Créer la Classe du Connecteur

Créez un nouveau fichier dans `finance/connectors/` (ex: `finance/connectors/mabanque.py`) :

```python
"""
Connecteur MaBanque pour synchronisation automatique.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    RateLimitError,
    BaseBankConnector,
)

logger = logging.getLogger(__name__)


class MaBanqueConnector(BaseBankConnector):
    """
    Connecteur MaBanque pour synchronisation automatique.
    """

    def __init__(self):
        """Initialise le connecteur MaBanque."""
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.token: Optional[str] = None
        self._authenticated = False

    @property
    def provider_name(self) -> str:
        """Retourne le nom du provider bancaire."""
        return "MaBanque"

    def authenticate(self, credentials: Dict) -> Dict:
        """
        Authentifie le connecteur avec les credentials fournis.

        Args:
            credentials: Dictionnaire contenant :
                        - "username": str
                        - "password": str

        Returns:
            dict: Informations de session :
                  {"token": str, "expires_at": datetime}

        Raises:
            InvalidCredentialsError: Si les credentials sont invalides
            AuthenticationError: Si l'authentification échoue
        """
        username = credentials.get("username")
        password = credentials.get("password")

        if not username or not password:
            raise InvalidCredentialsError("username et password sont requis")

        self.username = username
        self.password = password

        # TODO: Implémenter l'authentification réelle
        # Exemple avec requests :
        # import requests
        # response = requests.post("https://api.mabanque.com/auth", {
        #     "username": username,
        #     "password": password
        # })
        # if response.status_code != 200:
        #     raise InvalidCredentialsError("Credentials invalides")
        # self.token = response.json()["token"]

        self.token = "mock_token"  # À remplacer par l'implémentation réelle
        self._authenticated = True

        return {
            "token": self.token,
            "expires_at": datetime.now() + timedelta(hours=24),
        }

    def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
        """
        Récupère les transactions depuis la dernière synchronisation.

        Args:
            account: Objet Account Django
            since: Date optionnelle de la dernière synchronisation

        Returns:
            list: Liste de transactions au format :
                  [
                      {
                          "posted_at": datetime,
                          "amount": Decimal,
                          "description": str,
                          "raw": dict,
                      },
                      ...
                  ]
        """
        if not self._authenticated:
            raise AuthenticationError("Authentifiez-vous d'abord")

        # TODO: Implémenter la récupération des transactions
        # Exemple :
        # transactions = []
        # response = requests.get(
        #     "https://api.mabanque.com/transactions",
        #     headers={"Authorization": f"Bearer {self.token}"},
        #     params={"since": since.isoformat() if since else None}
        # )
        # for tx in response.json():
        #     transactions.append({
        #         "posted_at": datetime.fromisoformat(tx["date"]),
        #         "amount": Decimal(tx["amount"]),
        #         "description": tx["description"],
        #         "raw": {"transaction_id": tx["id"], "source": "mabanque"},
        #     })
        # return transactions

        return []  # À remplacer par l'implémentation réelle

    def get_balance(self, account) -> Decimal:
        """
        Récupère le solde actuel du compte.

        Args:
            account: Objet Account Django

        Returns:
            Decimal: Solde actuel
        """
        if not self._authenticated:
            raise AuthenticationError("Authentifiez-vous d'abord")

        # TODO: Implémenter la récupération du solde
        # Exemple :
        # response = requests.get(
        #     "https://api.mabanque.com/balance",
        #     headers={"Authorization": f"Bearer {self.token}"}
        # )
        # return Decimal(response.json()["balance"])

        return Decimal("0")  # À remplacer par l'implémentation réelle

    def disconnect(self) -> None:
        """Ferme la connexion et nettoie les ressources."""
        self.token = None
        self._authenticated = False
        # TODO: Fermer les sessions HTTP, WebSocket, etc.
```

### Étape 2 : Implémenter `authenticate()`

L'authentification varie selon le provider :

#### Exemple avec API REST simple

```python
def authenticate(self, credentials: Dict) -> Dict:
    import requests
    
    username = credentials.get("username")
    password = credentials.get("password")
    
    response = requests.post(
        "https://api.mabanque.com/auth",
        json={"username": username, "password": password},
        timeout=30
    )
    
    if response.status_code == 401:
        raise InvalidCredentialsError("Credentials invalides")
    if response.status_code != 200:
        raise AuthenticationError(f"Erreur d'authentification: {response.status_code}")
    
    data = response.json()
    self.token = data["access_token"]
    self._authenticated = True
    
    return {
        "token": self.token,
        "expires_at": datetime.fromisoformat(data["expires_at"]),
    }
```

#### Exemple avec Authentification 2FA

```python
def authenticate(self, credentials: Dict) -> Dict:
    import requests
    
    username = credentials.get("username")
    password = credentials.get("password")
    two_fa_code = credentials.get("2fa_code")
    
    # Étape 1 : Login initial
    response = requests.post(
        "https://api.mabanque.com/auth/login",
        json={"username": username, "password": password},
    )
    
    if response.status_code == 401:
        raise InvalidCredentialsError("Credentials invalides")
    
    data = response.json()
    
    # Étape 2 : Vérification 2FA si requis
    if data.get("requires_2fa"):
        if not two_fa_code:
            return {"requires_2fa": True}  # Le service gérera cela
        
        response = requests.post(
            "https://api.mabanque.com/auth/verify-2fa",
            json={"session_id": data["session_id"], "code": two_fa_code},
        )
        
        if response.status_code != 200:
            raise AuthenticationError("Code 2FA invalide")
        
        data = response.json()
    
    self.token = data["access_token"]
    self._authenticated = True
    
    return {
        "token": self.token,
        "expires_at": datetime.fromisoformat(data["expires_at"]),
    }
```

#### Exemple avec Playwright (Scraping Web)

```python
def authenticate(self, credentials: Dict) -> Dict:
    from playwright.sync_api import sync_playwright
    
    username = credentials.get("username")
    password = credentials.get("password")
    
    self.playwright = sync_playwright().start()
    self.browser = self.playwright.chromium.launch(headless=True)
    self.page = self.browser.new_page()
    
    try:
        # Naviguer vers la page de connexion
        self.page.goto("https://www.mabanque.com/login")
        
        # Remplir le formulaire
        self.page.fill('input[name="username"]', username)
        self.page.fill('input[name="password"]', password)
        self.page.click('button[type="submit"]')
        
        # Attendre la redirection ou vérifier l'authentification
        self.page.wait_for_url("https://www.mabanque.com/dashboard", timeout=30000)
        
        # Extraire les cookies de session
        cookies = self.page.context.cookies()
        session_cookie = next((c for c in cookies if c["name"] == "session_id"), None)
        
        if not session_cookie:
            raise AuthenticationError("Authentification échouée")
        
        self._authenticated = True
        
        return {
            "session_id": session_cookie["value"],
            "cookies": cookies,
            "expires_at": datetime.fromtimestamp(session_cookie["expires"]),
        }
    except Exception as e:
        self.disconnect()
        raise AuthenticationError(f"Erreur d'authentification: {str(e)}")
```

### Étape 3 : Implémenter `sync_transactions()`

La méthode `sync_transactions()` doit retourner une liste de transactions au format standardisé :

```python
def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
    """
    Récupère les transactions depuis la dernière synchronisation.
    """
    if not self._authenticated:
        raise AuthenticationError("Authentifiez-vous d'abord")
    
    from django.utils import timezone
    from django.conf import settings
    
    # Construire les paramètres de requête
    params = {}
    if since:
        # Convertir en format attendu par l'API
        params["since"] = since.isoformat()
    
    # Faire la requête à l'API
    response = requests.get(
        "https://api.mabanque.com/transactions",
        headers={"Authorization": f"Bearer {self.token}"},
        params=params,
        timeout=60
    )
    
    if response.status_code == 401:
        raise AuthenticationError("Session expirée")
    if response.status_code == 429:
        raise RateLimitError("Rate limit atteint")
    if response.status_code != 200:
        raise BankConnectionError(f"Erreur API: {response.status_code}")
    
    # Parser les transactions
    transactions = []
    for tx_data in response.json().get("transactions", []):
        # Parser la date
        posted_at = datetime.fromisoformat(tx_data["date"])
        if settings.USE_TZ and timezone.is_naive(posted_at):
            posted_at = timezone.make_aware(posted_at, timezone.get_current_timezone())
        
        # Parser le montant
        amount = Decimal(str(tx_data["amount"]))
        
        transactions.append({
            "posted_at": posted_at,
            "amount": amount,
            "description": tx_data.get("description", ""),
            "raw": {
                "transaction_id": tx_data.get("id"),
                "source": "mabanque",
                "category": tx_data.get("category"),
            },
        })
    
    return transactions
```

**Points importants** :
- Les dates doivent être timezone-aware si `USE_TZ=True`
- Les montants doivent être des `Decimal`
- Inclure un `transaction_id` dans `raw` pour la déduplication
- Gérer les erreurs appropriées (401 → AuthenticationError, 429 → RateLimitError)

### Étape 4 : Implémenter `get_balance()` (Optionnel)

```python
def get_balance(self, account) -> Decimal:
    """
    Récupère le solde actuel du compte.
    """
    if not self._authenticated:
        raise AuthenticationError("Authentifiez-vous d'abord")
    
    response = requests.get(
        "https://api.mabanque.com/balance",
        headers={"Authorization": f"Bearer {self.token}"},
        timeout=30
    )
    
    if response.status_code != 200:
        raise BankConnectionError(f"Erreur lors de la récupération du solde: {response.status_code}")
    
    return Decimal(str(response.json()["balance"]))
```

### Étape 5 : Ajouter le Provider dans le Modèle

Ajoutez le nouveau provider dans `finance/models.py` :

```python
class BankConnection(models.Model):
    class Provider(models.TextChoices):
        TRADE_REPUBLIC = "trade_republic", "Trade Republic"
        BOURSORAMA = "boursorama", "BoursoBank"
        HELLOBANK = "hellobank", "Hello Bank"
        MABANQUE = "mabanque", "MaBanque"  # Nouveau provider
```

### Étape 6 : Enregistrer le Connecteur dans `SyncService`

Ajoutez le mapping dans `finance/services/sync_service.py` :

```python
@staticmethod
def _get_connector_for_provider(provider: str) -> BaseBankConnector:
    """
    Retourne le connecteur approprié pour le provider donné.
    """
    from finance.connectors.mabanque import MaBanqueConnector
    
    connector_map = {
        BankConnection.Provider.TRADE_REPUBLIC: TradeRepublicConnector,
        BankConnection.Provider.BOURSORAMA: BoursoBankConnector,
        BankConnection.Provider.HELLOBANK: HelloBankConnector,
        BankConnection.Provider.MABANQUE: MaBanqueConnector,  # Nouveau connecteur
    }
    
    connector_class = connector_map.get(provider)
    if not connector_class:
        raise ValueError(f"Provider non supporté: {provider}")
    
    return connector_class()
```

### Étape 7 : Créer les Tests Unitaires

Créez un fichier de tests dans `finance/connectors/tests/test_mabanque.py` :

```python
"""
Tests unitaires pour le connecteur MaBanque.
"""

from unittest.mock import Mock, patch
from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from finance.connectors.mabanque import MaBanqueConnector
from finance.connectors.base import (
    AuthenticationError,
    InvalidCredentialsError,
)


class TestMaBanqueConnector(TestCase):
    """Tests pour le connecteur MaBanque."""

    def setUp(self):
        """Configure l'environnement de test."""
        self.connector = MaBanqueConnector()

    def test_provider_name(self):
        """Test du nom du provider."""
        self.assertEqual(self.connector.provider_name, "MaBanque")

    @patch("finance.connectors.mabanque.requests")
    def test_authenticate_success(self, mock_requests):
        """Test d'authentification réussie."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "test_token",
            "expires_at": "2025-12-31T23:59:59Z",
        }
        mock_requests.post.return_value = mock_response

        result = self.connector.authenticate({
            "username": "test_user",
            "password": "test_pass",
        })

        self.assertTrue(self.connector._authenticated)
        self.assertEqual(result["token"], "test_token")

    @patch("finance.connectors.mabanque.requests")
    def test_authenticate_invalid_credentials(self, mock_requests):
        """Test avec credentials invalides."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_requests.post.return_value = mock_response

        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({
                "username": "wrong",
                "password": "wrong",
            })

    # Ajouter d'autres tests...
```

### Étape 8 : Documenter l'API/Scraping Utilisé

Créez un fichier de documentation dans `docs/connectors/mabanque.md` :

```markdown
# Connecteur MaBanque

## API Utilisée

- **Endpoint d'authentification** : `POST https://api.mabanque.com/auth`
- **Endpoint des transactions** : `GET https://api.mabanque.com/transactions`
- **Endpoint du solde** : `GET https://api.mabanque.com/balance`

## Authentification

L'authentification utilise un token Bearer obtenu via l'endpoint `/auth`.

## Format des Données

### Transactions

```json
{
  "id": "tx_123",
  "date": "2025-01-15T10:30:00Z",
  "amount": "-50.00",
  "description": "Achat",
  "category": "Shopping"
}
```

## Rate Limiting

L'API limite à 100 requêtes par minute. Le connecteur gère automatiquement les erreurs 429.
```

## Bonnes Pratiques

### Gestion des Erreurs

Toujours lever les exceptions appropriées :

```python
if response.status_code == 401:
    raise AuthenticationError("Session expirée")
if response.status_code == 429:
    raise RateLimitError("Rate limit atteint")
if response.status_code >= 500:
    raise ConnectionTimeoutError("Erreur serveur")
```

### Retry et Backoff

Le service `SyncService` gère automatiquement les retry avec backoff exponentiel pour :
- `ConnectionTimeoutError`
- `RateLimitError`

Pour les autres erreurs, le retry doit être géré dans le connecteur si nécessaire.

### Logging

Utilisez le logger du module pour tracer les opérations :

```python
import logging

logger = logging.getLogger(__name__)

def sync_transactions(self, account, since=None):
    logger.info(f"Début de synchronisation pour le compte {account.id}")
    try:
        # ...
        logger.info(f"Synchronisation réussie: {len(transactions)} transactions")
    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation: {str(e)}", exc_info=True)
        raise
```

### Tests

Créez des tests complets couvrant :
- Authentification réussie/échouée
- Récupération des transactions
- Gestion des erreurs
- Cas limites (pas de transactions, transactions multiples, etc.)

Utilisez des mocks pour éviter les appels réels aux APIs.

### Gestion des Timezones

Toujours convertir les dates en timezone-aware :

```python
from django.conf import settings
from django.utils import timezone

posted_at = datetime.fromisoformat(tx_data["date"])
if settings.USE_TZ and timezone.is_naive(posted_at):
    # Assumer UTC si pas de timezone
    posted_at = posted_at.replace(tzinfo=timezone.utc)
    # Convertir vers la timezone Django
    posted_at = posted_at.astimezone(timezone.get_current_timezone())
```

## Référence API

### BaseBankConnector

#### `authenticate(credentials: dict) -> dict`

Authentifie le connecteur avec les credentials fournis.

**Paramètres** :
- `credentials` : Dictionnaire avec les credentials nécessaires (varie selon le provider)

**Retourne** :
- `dict` : Informations de session (token, session_id, expires_at, etc.)

**Lève** :
- `InvalidCredentialsError` : Si les credentials sont invalides
- `AuthenticationError` : Si l'authentification échoue
- `ConnectionTimeoutError` : Si la connexion timeout

#### `sync_transactions(account, since: Optional[datetime] = None) -> list`

Récupère les transactions depuis la dernière synchronisation.

**Paramètres** :
- `account` : Objet `Account` Django
- `since` : Date optionnelle de la dernière synchronisation

**Retourne** :
- `list[dict]` : Liste de transactions au format :
  ```python
  {
      "posted_at": datetime,  # timezone-aware
      "amount": Decimal,
      "description": str,
      "raw": dict,  # Optionnel, avec transaction_id pour déduplication
  }
  ```

**Lève** :
- `AuthenticationError` : Si la session a expiré
- `RateLimitError` : Si le rate limit est atteint
- `ConnectionTimeoutError` : Si la connexion timeout
- `BankConnectionError` : Pour toute autre erreur

#### `get_balance(account) -> Decimal`

Récupère le solde actuel du compte.

**Paramètres** :
- `account` : Objet `Account` Django

**Retourne** :
- `Decimal` : Solde actuel

**Lève** :
- `AuthenticationError` : Si la session a expiré
- `ConnectionTimeoutError` : Si la connexion timeout
- `BankConnectionError` : Pour toute autre erreur

#### `disconnect() -> None`

Ferme la connexion et nettoie les ressources.

### Types d'Erreurs

#### `BankConnectionError`
Exception de base pour toutes les erreurs de connecteur.

#### `AuthenticationError(BankConnectionError)`
Erreur lors de l'authentification.

#### `InvalidCredentialsError(AuthenticationError)`
Credentials invalides fournis.

#### `RateLimitError(BankConnectionError)`
Rate limiting de l'API bancaire.

#### `ConnectionTimeoutError(BankConnectionError)`
Timeout lors de la connexion à l'API bancaire.

### Format des Données Retournées

#### Transactions

Chaque transaction doit être un dictionnaire avec :

- **`posted_at`** (datetime, requis) : Date/heure de la transaction (timezone-aware)
- **`amount`** (Decimal, requis) : Montant (positif pour revenus, négatif pour dépenses)
- **`description`** (str, requis) : Description de la transaction
- **`raw`** (dict, optionnel) : Métadonnées supplémentaires
  - **`transaction_id`** (str, recommandé) : ID unique pour déduplication
  - **`source`** (str, recommandé) : Source de la transaction (nom du provider)

#### Informations de Session

Le retour de `authenticate()` doit contenir :

- **`token`** ou **`session_id`** (str) : Identifiant de session
- **`expires_at`** (datetime, optionnel) : Date d'expiration de la session
- **`cookies`** (list, optionnel) : Cookies de session (pour Playwright)
- **`requires_2fa`** (bool, optionnel) : Si True, le service demandera le code 2FA

## Exemples Complets

### Exemple 1 : Connecteur Simple avec API REST

Voir `finance/connectors/traderepublic.py` pour un exemple complet avec API REST et WebSocket.

### Exemple 2 : Connecteur avec Authentification 2FA

Voir `finance/connectors/traderepublic.py` pour un exemple avec authentification 2FA.

### Exemple 3 : Connecteur avec Playwright (Scraping)

Voir `finance/connectors/boursorama.py` et `finance/connectors/hellobank.py` pour des exemples avec Playwright.

## Checklist de Développement

- [ ] Classe créée héritant de `BaseBankConnector`
- [ ] Méthode `provider_name` implémentée
- [ ] Méthode `authenticate` implémentée avec gestion d'erreurs
- [ ] Méthode `sync_transactions` implémentée avec format standardisé
- [ ] Méthode `get_balance` implémentée (si applicable)
- [ ] Méthode `disconnect` implémentée pour nettoyage
- [ ] Provider ajouté dans `BankConnection.Provider`
- [ ] Connecteur enregistré dans `SyncService._get_connector_for_provider`
- [ ] Tests unitaires créés avec mocks
- [ ] Documentation de l'API/scraping créée
- [ ] Gestion des timezones correcte
- [ ] Logging approprié
- [ ] Gestion des erreurs complète

## Ressources

- `finance/connectors/base.py` : Classe abstraite et exceptions
- `finance/connectors/traderepublic.py` : Exemple avec API REST + WebSocket + 2FA
- `finance/connectors/boursorama.py` : Exemple avec Playwright
- `finance/connectors/hellobank.py` : Exemple avec Playwright
- `finance/services/sync_service.py` : Service de synchronisation qui utilise les connecteurs
- `finance/tests/test_sync_service.py` : Tests d'intégration
