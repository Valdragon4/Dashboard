# Architecture des Connecteurs Bancaires

## Vue d'ensemble

Ce document décrit l'architecture modulaire des connecteurs bancaires utilisée pour intégrer différents providers bancaires (BoursoBank, Hello Bank, Trade Republic, etc.) de manière uniforme dans le système de synchronisation automatique.

## Pattern de Classe Abstraite

L'architecture utilise le pattern de classe abstraite pour définir une interface commune que tous les connecteurs bancaires doivent implémenter. Cela permet :

- **Uniformité** : Tous les connecteurs suivent la même interface
- **Extensibilité** : Ajouter un nouveau provider nécessite uniquement d'implémenter la classe abstraite
- **Testabilité** : Les connecteurs peuvent être testés indépendamment avec des mocks
- **Maintenabilité** : Les changements d'interface sont centralisés dans la classe abstraite

## Classe Abstraite : BaseBankConnector

Tous les connecteurs bancaires doivent hériter de `BaseBankConnector` et implémenter les méthodes abstraites requises.

### Localisation

- **Fichier** : `backend/finance/connectors/base.py`
- **Classe** : `BaseBankConnector`

### Méthodes Requises

#### `provider_name` (propriété abstraite)

```python
@property
@abstractmethod
def provider_name(self) -> str:
    """Retourne le nom du provider bancaire."""
    pass
```

**Description** : Propriété en lecture seule qui retourne le nom unique du provider (ex: "Trade Republic", "BoursoBank", "Hello Bank").

#### `authenticate(credentials: dict) -> dict`

```python
@abstractmethod
def authenticate(self, credentials: dict) -> dict:
    """Authentifie le connecteur avec les credentials fournis."""
    pass
```

**Description** : Authentifie le connecteur avec les credentials fournis. Gère l'authentification complète, incluant si nécessaire l'authentification 2FA.

**Paramètres** :
- `credentials` : Dictionnaire contenant les credentials nécessaires (ex: `{"username": "...", "password": "...", "2fa_code": "..."}`)

**Retourne** : Dictionnaire contenant les informations de session nécessaires pour les appels suivants (ex: `{"token": "...", "session_id": "...", "expires_at": "..."}`)

**Exceptions** :
- `AuthenticationError` : Si l'authentification échoue
- `InvalidCredentialsError` : Si les credentials sont invalides
- `ConnectionTimeoutError` : Si la connexion timeout

#### `sync_transactions(account, since: Optional[datetime] = None) -> list`

```python
@abstractmethod
def sync_transactions(self, account, since: Optional[datetime] = None) -> list:
    """Récupère les transactions depuis la dernière synchronisation."""
    pass
```

**Description** : Récupère toutes les transactions depuis la date spécifiée (ou toutes les transactions si `since` est None).

**Paramètres** :
- `account` : Objet `Account` Django représentant le compte à synchroniser
- `since` : Date optionnelle de la dernière synchronisation. Si None, récupère toutes les transactions disponibles.

**Retourne** : Liste de dictionnaires représentant les transactions. Chaque dictionnaire doit contenir au minimum :
- `posted_at` : datetime de la transaction
- `amount` : Decimal du montant (positif pour revenus, négatif pour dépenses)
- `description` : str description de la transaction
- `raw` : dict métadonnées supplémentaires (optionnel)

**Exceptions** :
- `AuthenticationError` : Si la session a expiré
- `RateLimitError` : Si le rate limit de l'API est atteint
- `ConnectionTimeoutError` : Si la connexion timeout
- `BankConnectionError` : Pour toute autre erreur de connexion

#### `get_balance(account) -> Decimal`

```python
@abstractmethod
def get_balance(self, account) -> Decimal:
    """Récupère le solde actuel du compte."""
    pass
```

**Description** : Récupère le solde actuel du compte.

**Paramètres** :
- `account` : Objet `Account` Django représentant le compte

**Retourne** : Decimal représentant le solde actuel du compte

**Exceptions** :
- `AuthenticationError` : Si la session a expiré
- `ConnectionTimeoutError` : Si la connexion timeout
- `BankConnectionError` : Pour toute autre erreur de connexion

#### `disconnect() -> None`

```python
@abstractmethod
def disconnect(self) -> None:
    """Ferme la connexion et nettoie les ressources."""
    pass
```

**Description** : Ferme proprement toutes les connexions ouvertes (sessions HTTP, WebSocket, etc.) et libère les ressources utilisées.

### Méthode Optionnelle

#### `sync_portfolio_valuations(account) -> dict`

```python
def sync_portfolio_valuations(self, account) -> dict:
    """Synchronise les valorisations de portefeuille (optionnel)."""
    return {}
```

**Description** : Méthode optionnelle pour synchroniser les valorisations de portefeuille. Retourne un dictionnaire vide par défaut. Doit être surchargée pour les providers supportant les investissements (Trade Republic, etc.).

**Paramètres** :
- `account` : Objet `Account` Django représentant le compte

**Retourne** : Dictionnaire de valorisations par type de portefeuille :
```python
{
    "PEA": Decimal(...),
    "CTO": Decimal(...),
    "CRYPTO": Decimal(...),
    ...
}
```

## Exceptions Standardisées

Toutes les exceptions héritent de `BankConnectionError` pour permettre une gestion d'erreur uniforme.

### Hiérarchie des Exceptions

```
Exception
└── BankConnectionError (exception de base)
    ├── AuthenticationError
    │   └── InvalidCredentialsError
    ├── RateLimitError
    └── ConnectionTimeoutError
```

### Localisation

- **Fichier** : `backend/finance/connectors/base.py`

### Utilisation

```python
from finance.connectors.base import (
    BankConnectionError,
    AuthenticationError,
    InvalidCredentialsError,
    RateLimitError,
    ConnectionTimeoutError,
)

try:
    connector.authenticate(credentials)
except InvalidCredentialsError:
    # Gérer les credentials invalides
    pass
except AuthenticationError:
    # Gérer les autres erreurs d'authentification
    pass
except BankConnectionError:
    # Gérer toutes les autres erreurs de connexion
    pass
```

## Exemple de Connecteur Minimal

Voici un exemple minimal d'implémentation d'un connecteur :

```python
from datetime import datetime
from decimal import Decimal
from typing import Optional

from finance.connectors.base import BaseBankConnector, AuthenticationError


class ExampleBankConnector(BaseBankConnector):
    """Exemple minimal de connecteur bancaire."""

    @property
    def provider_name(self) -> str:
        return "ExampleBank"

    def authenticate(self, credentials: dict) -> dict:
        """Authentifie avec le provider."""
        username = credentials.get("username")
        password = credentials.get("password")

        if not username or not password:
            raise AuthenticationError("Username and password required")

        # Ici, faire l'appel API réel pour l'authentification
        # token = api_client.login(username, password)
        # return {"token": token}

        return {"token": "example_token"}

    def sync_transactions(self, account, since: Optional[datetime] = None) -> list:
        """Récupère les transactions."""
        # Ici, faire l'appel API réel pour récupérer les transactions
        # transactions = api_client.get_transactions(account.external_id, since)
        # return format_transactions(transactions)

        return [
            {
                "posted_at": datetime.now(),
                "amount": Decimal("100.00"),
                "description": "Example transaction",
                "raw": {},
            }
        ]

    def get_balance(self, account) -> Decimal:
        """Récupère le solde."""
        # Ici, faire l'appel API réel pour récupérer le solde
        # balance = api_client.get_balance(account.external_id)
        # return Decimal(str(balance))

        return Decimal("1000.00")

    def disconnect(self) -> None:
        """Ferme la connexion."""
        # Ici, fermer les connexions ouvertes
        # api_client.close()
        pass
```

## Comment Ajouter un Nouveau Connecteur

Pour ajouter un nouveau connecteur bancaire :

1. **Créer le fichier du connecteur** dans `backend/finance/connectors/`
   - Exemple : `backend/finance/connectors/nouvellebanque.py`

2. **Créer la classe héritant de `BaseBankConnector`**
   ```python
   from finance.connectors.base import BaseBankConnector
   
   class NouvelleBanqueConnector(BaseBankConnector):
       # Implémenter toutes les méthodes abstraites
       pass
   ```

3. **Implémenter toutes les méthodes abstraites** :
   - `provider_name` (propriété)
   - `authenticate()`
   - `sync_transactions()`
   - `get_balance()`
   - `disconnect()`
   - `sync_portfolio_valuations()` (optionnel, si le provider supporte les investissements)

4. **Ajouter le connecteur dans `__init__.py`** pour l'exporter :
   ```python
   from finance.connectors.nouvellebanque import NouvelleBanqueConnector
   
   __all__ = [
       # ... autres exports
       "NouvelleBanqueConnector",
   ]
   ```

5. **Créer les tests unitaires** dans `backend/finance/connectors/tests/test_nouvellebanque.py`

6. **Documenter le connecteur** :
   - Ajouter une section dans ce document expliquant les spécificités du connecteur
   - Documenter les credentials requis
   - Documenter l'API utilisée (si scraping, API officielle, etc.)

## Intégration avec Django

Les connecteurs sont conçus pour être **indépendants de Django** (classes Python pures) pour faciliter :
- Les tests unitaires sans dépendance Django
- La réutilisabilité dans d'autres contextes
- La séparation des responsabilités

L'intégration avec Django (modèles, services, tâches Celery) se fait via les services dans `backend/finance/services/` (voir Story 1.6).

## Bonnes Pratiques

1. **Gestion d'erreurs** : Toujours lever les exceptions appropriées (`AuthenticationError`, `RateLimitError`, etc.)

2. **Type hints** : Utiliser les type hints Python pour toutes les méthodes

3. **Docstrings** : Documenter toutes les méthodes publiques avec des docstrings détaillées

4. **Logging** : Utiliser le logging Python pour tracer les opérations importantes (authentification, synchronisation, erreurs)

5. **Tests** : Créer des tests unitaires avec mocks pour éviter les appels API réels pendant les tests

6. **Sécurité** : Ne jamais logger ou exposer les credentials dans les logs ou les messages d'erreur

## Connecteurs Existants

### Trade Republic

**Fichier** : `backend/finance/connectors/traderepublic.py`

**Description** : Connecteur Trade Republic pour synchronisation automatique des transactions, soldes et portefeuilles (PEA/CTO/CRYPTO).

**Authentification** :
- Processus en deux étapes avec 2FA par SMS
- Credentials requis : `phone_number`, `pin`, `2fa_code` (optionnel)
- Si `2fa_code` fourni, authentification complète automatique
- Sinon, retourne `process_id` et `countdown` pour authentification en deux étapes

**Méthodes implémentées** :
- `authenticate()` : Authentification avec support 2FA
- `sync_transactions()` : Récupération des transactions avec filtrage par date
- `get_balance()` : Récupération du solde disponible
- `sync_portfolio_valuations()` : Synchronisation des valorisations PEA/CTO/CRYPTO
- `disconnect()` : Fermeture propre des connexions

**Spécificités** :
- Utilise l'API REST pour l'authentification
- Utilise WebSocket pour récupérer les données (transactions, soldes, portefeuilles)
- Retry automatique avec backoff exponentiel (max 3 tentatives)
- Transformation automatique des transactions au format standard

**Format des transactions** :
- `posted_at` : datetime depuis `timestamp` (format ISO 8601)
- `amount` : Decimal depuis `amount.value` (signe conservé)
- `description` : str combinant `type`, `title`, `description`
- `raw` : dict contenant toutes les métadonnées originales + `transaction_id` pour déduplication

**Gestion des erreurs** :
- `InvalidCredentialsError` : Credentials invalides
- `AuthenticationError` : Erreur d'authentification
- `ConnectionTimeoutError` : Timeout de connexion
- `RateLimitError` : Rate limit atteint
- `BankConnectionError` : Autres erreurs de connexion

**Tests** : `backend/finance/connectors/tests/test_traderepublic.py`

### BoursoBank

**Fichier** : `backend/finance/connectors/boursorama.py`

**Description** : Connecteur BoursoBank pour synchronisation automatique des transactions et soldes via scraping navigateur avec Playwright.

**Authentification** :
- Processus via interface web avec Playwright
- Credentials requis : `username`, `password`, `2fa_code` (optionnel)
- Si `2fa_code` fourni, authentification complète automatique
- Sinon, retourne `{"requires_2fa": True}` pour authentification en deux étapes

**Méthodes implémentées** :
- `authenticate()` : Authentification via navigateur avec support 2FA
- `sync_transactions()` : Récupération des transactions avec filtrage par date
- `get_balance()` : Récupération du solde disponible
- `disconnect()` : Fermeture propre du navigateur

**Spécificités** :
- Utilise Playwright pour automatiser un navigateur headless
- Scraping de l'interface web BoursoBank
- Retry automatique avec backoff exponentiel (max 3 tentatives)
- Transformation automatique des transactions au format standard
- Compatible avec le format CSV existant

**Format des transactions** :
- `posted_at` : datetime depuis `dateop` (format DD/MM/YYYY)
- `amount` : Decimal depuis le montant (signe conservé)
- `description` : str combinant `label` + `comment`
- `raw` : dict contenant toutes les métadonnées originales (category, counterparty, account_balance, etc.)

**Gestion des erreurs** :
- `InvalidCredentialsError` : Credentials invalides
- `AuthenticationError` : Erreur d'authentification
- `ConnectionTimeoutError` : Timeout de connexion
- `RateLimitError` : Rate limit atteint
- `BankConnectionError` : Autres erreurs de connexion

**Dépendances** :
- `playwright>=1.40.0` (nécessite installation : `playwright install chromium`)

**Tests** : `backend/finance/connectors/tests/test_boursorama.py`

**Documentation** : `docs/connectors/boursorama.md`

### Hello Bank

**Fichier** : `backend/finance/connectors/hellobank.py`

**Description** : Connecteur Hello Bank pour synchronisation automatique des transactions et soldes via scraping navigateur avec Playwright.

**Authentification** :
- Processus via interface web avec Playwright
- Credentials requis : `username`, `password`, `2fa_code` (optionnel)
- Si `2fa_code` fourni, authentification complète automatique
- Sinon, retourne `{"requires_2fa": True}` pour authentification en deux étapes

**Méthodes implémentées** :
- `authenticate()` : Authentification via navigateur avec support 2FA
- `sync_transactions()` : Récupération des transactions avec filtrage par date
- `get_balance()` : Récupération du solde disponible
- `disconnect()` : Fermeture propre du navigateur

**Spécificités** :
- Utilise Playwright pour automatiser un navigateur headless (réutilisation de la dépendance déjà disponible)
- Scraping de l'interface web Hello Bank
- Retry automatique avec backoff exponentiel (max 3 tentatives)
- Transformation automatique des transactions au format standard
- Compatible avec le format CSV existant (format Hello Bank : Date;Type;Libellé court;Libellé détaillé;Montant)

**Format des transactions** :
- `posted_at` : datetime depuis `Date` (format DD/MM/YYYY)
- `amount` : Decimal depuis `Montant` (signe conservé)
- `description` : str combinant `Libellé court` + `Libellé détaillé`
- `raw` : dict contenant toutes les métadonnées originales (operation_type, label_short, label_detailed, etc.)

**Gestion des erreurs** :
- `InvalidCredentialsError` : Credentials invalides
- `AuthenticationError` : Erreur d'authentification
- `ConnectionTimeoutError` : Timeout de connexion
- `RateLimitError` : Rate limit atteint
- `BankConnectionError` : Autres erreurs de connexion

**Dépendances** :
- `playwright>=1.40.0` (déjà disponible dans le projet, ajouté pour BoursoBank)

**Tests** : `backend/finance/connectors/tests/test_hellobank.py`

**Documentation** : `docs/connectors/hellobank.md`
