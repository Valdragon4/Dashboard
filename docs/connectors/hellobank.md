# Connecteur Hello Bank

## Vue d'ensemble

Le connecteur Hello Bank permet la synchronisation automatique des transactions et soldes depuis l'interface web Hello Bank en utilisant Playwright pour automatiser un navigateur.

## Approche Technique

**Méthode choisie** : **Playwright (Scraping Navigateur)**

**Justification** :
- Simule un utilisateur réel, réduisant les risques de détection et de blocage
- Gère automatiquement les cookies, sessions, et authentification 2FA
- Plus stable face aux changements mineurs d'interface
- Pas de certification requise (adapté pour usage personnel)
- Approche éprouvée pour le scraping bancaire
- Réutilisation de Playwright déjà disponible dans le projet (ajouté pour BoursoBank)

**Documentation de recherche** : Voir `docs/connectors/hellobank-research.md` pour l'évaluation complète des options.

## Installation

Le connecteur nécessite Playwright (déjà disponible dans le projet) :

```bash
pip install playwright>=1.40.0
playwright install chromium
```

## Credentials Requis

Format des credentials (déchiffrés depuis `BankConnection.encrypted_credentials`) :

```python
{
    "username": "identifiant_hellobank",  # Identifiant Hello Bank
    "password": "mot_de_passe",            # Mot de passe
    "2fa_code": "123456"                   # Code 2FA (optionnel, pour authentification complète)
}
```

**Note** : Si `2fa_code` n'est pas fourni, le connecteur retournera `{"requires_2fa": True}` pour permettre l'authentification en deux étapes depuis l'interface utilisateur.

## Authentification

Le processus d'authentification utilise Playwright pour :
1. Ouvrir un navigateur headless
2. Naviguer vers la page de connexion Hello Bank
3. Remplir le formulaire de connexion (username/password)
4. Gérer l'authentification 2FA si nécessaire
5. Stocker les cookies de session pour les requêtes suivantes

**Gestion 2FA** :
- Si un code 2FA est requis et fourni dans les credentials, il est automatiquement saisi
- Sinon, le connecteur retourne `{"requires_2fa": True}` pour permettre la saisie manuelle depuis l'interface

## Format des Données

### Transactions

Format de retour de `sync_transactions()` :

```python
{
    "posted_at": datetime(...),      # Date de l'opération (depuis Date)
    "amount": Decimal(...),          # Montant (négatif pour dépenses, positif pour revenus)
    "description": str(...),        # Description (combinaison de libellé court + libellé détaillé)
    "raw": {
        "source": "hellobank",
        "operation_type": str(...),  # Type d'opération (VIREMENT, DEBIT, etc.)
        "label_short": str(...),     # Libellé court
        "label_detailed": str(...),  # Libellé détaillé
        "raw_data": [...],          # Données originales de la ligne
    }
}
```

**Compatibilité** : Le format est compatible avec le format CSV existant pour maintenir la compatibilité avec les imports manuels (IV2).

**Format CSV Hello Bank** :
- Format : `Date;Type;Libellé court;Libellé détaillé;Montant`
- Délimiteur : point-virgule (`;`)
- Description : Combinaison de libellé court + libellé détaillé

### Solde

Format de retour de `get_balance()` :

```python
Decimal("1500.50")  # Solde actuel du compte
```

## Méthodes Implémentées

### `authenticate(credentials: dict) -> dict`

Authentifie le connecteur avec les credentials fournis.

**Paramètres** :
- `credentials` : Dictionnaire contenant `username`, `password`, et optionnellement `2fa_code`

**Retourne** :
- Si authentification complète : `{"session_id": str, "cookies": list, "expires_at": datetime}`
- Si 2FA requise : `{"requires_2fa": True, "message": str}`

**Exceptions** :
- `InvalidCredentialsError` : Credentials invalides
- `AuthenticationError` : Erreur d'authentification
- `ConnectionTimeoutError` : Timeout de connexion

### `sync_transactions(account, since: Optional[datetime] = None) -> list`

Récupère les transactions depuis la dernière synchronisation.

**Paramètres** :
- `account` : Objet Account Django
- `since` : Date optionnelle pour filtrer les transactions

**Retourne** : Liste de transactions au format standard

**Exceptions** :
- `AuthenticationError` : Session expirée
- `RateLimitError` : Rate limit atteint
- `ConnectionTimeoutError` : Timeout
- `BankConnectionError` : Autres erreurs

### `get_balance(account) -> Decimal`

Récupère le solde actuel du compte.

**Paramètres** :
- `account` : Objet Account Django

**Retourne** : Solde du compte en Decimal

**Exceptions** :
- `AuthenticationError` : Session expirée
- `ConnectionTimeoutError` : Timeout
- `BankConnectionError` : Autres erreurs

### `disconnect() -> None`

Ferme la connexion et nettoie les ressources (navigateur, sessions, etc.).

## Gestion des Erreurs

### Retry Automatique

Toutes les opérations utilisent un retry automatique avec backoff exponentiel :
- Max 3 tentatives
- Délai initial : 1 seconde
- Délai entre tentatives : `base_delay * (2 ** attempt_number)`

### Fallback

Si l'approche Playwright échoue après plusieurs tentatives, le connecteur peut essayer une approche alternative (scraping HTTP direct) si implémentée. Cette fonctionnalité peut être ajoutée dans une version future.

### Exceptions

Le connecteur utilise les exceptions standardisées de `BaseBankConnector` :
- `InvalidCredentialsError` : Credentials invalides
- `AuthenticationError` : Erreur d'authentification
- `ConnectionTimeoutError` : Timeout de connexion
- `RateLimitError` : Rate limit atteint
- `BankConnectionError` : Autres erreurs de connexion

## Limitations et Notes

### Sélecteurs CSS

Les sélecteurs CSS utilisés pour scraper les données peuvent changer si Hello Bank modifie son interface. Le connecteur utilise plusieurs sélecteurs alternatifs pour améliorer la robustesse, mais peut nécessiter des ajustements si l'interface change significativement.

### Performance

- Le scraping navigateur est plus lent que les appels HTTP directs
- Consommation de ressources plus importante (navigateur headless)
- Temps de synchronisation : ~10-30 secondes selon le nombre de transactions

### Détection de Doublons

La détection de doublons complète est gérée dans le service de synchronisation (Story 1.6). Le connecteur filtre uniquement par date `since` si fournie.

### Authentification 2FA

- Support de l'authentification 2FA par SMS ou TOTP
- Si 2FA requis et code non fourni, retourne `{"requires_2fa": True}` pour permettre la saisie manuelle
- Le code 2FA peut être fourni directement dans les credentials pour authentification complète automatique

### Format Hello Bank Spécifique

Le connecteur gère le format spécifique Hello Bank :
- Format CSV : `Date;Type;Libellé court;Libellé détaillé;Montant`
- Description : Combinaison de libellé court + libellé détaillé
- Métadonnées : Type d'opération, libellé court, libellé détaillé conservés dans `raw`

## Exemple d'Utilisation

```python
from finance.connectors import HelloBankConnector
from finance.services.encryption_service import EncryptionService
from finance.models import BankConnection, Account

# Récupérer les credentials déchiffrés
bank_connection = BankConnection.objects.get(provider="hellobank")
credentials = EncryptionService.decrypt_credentials(bank_connection.encrypted_credentials)

# Créer et authentifier le connecteur
connector = HelloBankConnector()
session = connector.authenticate(credentials)

# Si 2FA requise
if session.get("requires_2fa"):
    # Demander le code 2FA à l'utilisateur
    two_fa_code = input("Code 2FA: ")
    credentials["2fa_code"] = two_fa_code
    session = connector.authenticate(credentials)

# Récupérer les transactions
account = Account.objects.get(bank_connection=bank_connection)
transactions = connector.sync_transactions(account, since=account.last_sync_at)

# Récupérer le solde
balance = connector.get_balance(account)

# Fermer la connexion
connector.disconnect()
```

## Tests

Les tests unitaires sont disponibles dans `backend/finance/connectors/tests/test_hellobank.py`.

**Couverture** :
- Authentification (succès, 2FA, erreurs, timeout)
- Synchronisation des transactions (filtrage, formatage au format Hello Bank)
- Récupération du solde
- Fermeture propre de la connexion
- Parsing de dates et montants
- Parsing spécifique du format Hello Bank (Date;Type;Libellé court;Libellé détaillé;Montant)

## Maintenance

### Ajustement des Sélecteurs CSS

Si l'interface Hello Bank change, les sélecteurs CSS doivent être ajustés dans :
- `_authenticate_with_playwright()` : Sélecteurs pour le formulaire de connexion
- `_scrape_transactions_from_page()` : Sélecteurs pour le tableau des transactions
- `_scrape_balance_from_page()` : Sélecteurs pour le solde

### Logging

Le connecteur utilise le logging Python pour tracer les opérations :
- `INFO` : Opérations réussies
- `WARNING` : Retries, erreurs récupérables
- `ERROR` : Erreurs définitives

Les credentials et tokens ne sont jamais loggés en clair.

## Références

- Document de recherche : `docs/connectors/hellobank-research.md`
- Architecture des connecteurs : `docs/architecture/connectors.md`
- Documentation Playwright : https://playwright.dev/python/
