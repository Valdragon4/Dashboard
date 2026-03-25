# Dashboard Financier

Application web Django pour gérer vos finances personnelles avec synchronisation automatique bancaire.

## Fonctionnalités

### Synchronisation Bancaire Automatique

Le système permet de synchroniser automatiquement vos transactions et soldes depuis vos comptes bancaires sans avoir à importer manuellement des fichiers CSV ou PDF.

#### Providers Supportés

- **Trade Republic** : Courtier en ligne avec support complet (authentification 2FA)
- **BoursoBank** : Banque en ligne française
- **Hello Bank** : Banque en ligne française

#### Avantages

- ✅ **Automatique** : Synchronisation quotidienne sans intervention
- ✅ **Temps gagné** : Plus besoin d'importer manuellement des fichiers
- ✅ **Données à jour** : Vos transactions sont toujours synchronisées
- ✅ **Sécurisé** : Credentials chiffrés avec AES-256
- ✅ **Traçable** : Historique complet des synchronisations avec logs détaillés

#### Fonctionnalités Clés

- Synchronisation automatique quotidienne (configurable)
- Synchronisation manuelle à la demande
- Détection automatique des doublons entre import manuel et synchronisation automatique
- Logs de synchronisation avec statistiques et alertes
- Export CSV des logs de synchronisation
- Support de l'authentification 2FA pour Trade Republic

### Import Manuel

Le système continue de supporter l'import manuel de fichiers CSV/PDF :

- Import CSV générique (format standard)
- Import CSV Trade Republic
- Import PDF Trade Republic avec analyse OpenAI
- Support de multiples profils (BoursoBank, Hello Bank, etc.)

Les deux méthodes (import manuel et synchronisation automatique) sont **100% compatibles** et le système détecte automatiquement les doublons.

## Installation

### Prérequis

- Python 3.10+
- PostgreSQL (ou SQLite pour le développement)
- Redis (pour Celery)
- Docker et Docker Compose (recommandé)

### Installation avec Docker

1. **Cloner le dépôt** :
```bash
git clone <repository-url>
cd dashboard
```

2. **Configurer les variables d'environnement** :
Créez un fichier `.env` à la racine du projet avec les variables suivantes :

```env
# Base de données
DATABASE_URL=postgresql://user:password@db:5432/dashboard

# Secret Django
SECRET_KEY=your-secret-key-here

# Chiffrement des credentials bancaires (OBLIGATOIRE)
ENCRYPTION_KEY=your-encryption-key-here

# Configuration Celery
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Configuration de synchronisation (optionnel)
SYNC_LOG_RETENTION_DAYS=30
SYNC_FAILURE_ALERT_THRESHOLD=3
BANK_SYNC_SCHEDULE_HOUR=2
BANK_SYNC_SCHEDULE_MINUTE=0

# OpenAI (pour import PDF Trade Republic, optionnel)
OPENAI_API_KEY=your-openai-api-key-here

# Trade Republic (optionnel, debug)
# 1 = tente d'abord l'init via Playwright (Chromium) + contexte WAF, puis fallback HTTP
# 0 = désactive l'étape Playwright pour diagnostiquer (peut augmenter la probabilité de 403 sur certains réseaux)
TRADEPUBLIC_USE_PLAYWRIGHT_INITIATE=1
```

**Important** : Générez une clé de chiffrement sécurisée avec :
```python
from finance.services.encryption_service import EncryptionService
print(EncryptionService.generate_key())
```

3. **Lancer avec Docker Compose** :
```bash
docker-compose up -d
```

4. **Créer un superutilisateur** :
```bash
docker-compose exec web python manage.py createsuperuser
```

5. **Accéder à l'application** :
Ouvrez votre navigateur à `http://localhost:8000`

### Installation Locale

1. **Installer les dépendances** :
```bash
cd backend
pip install -r requirements.txt
```

2. **Configurer la base de données** :
```bash
python manage.py migrate
```

3. **Créer un superutilisateur** :
```bash
python manage.py createsuperuser
```

4. **Lancer le serveur de développement** :
```bash
python manage.py runserver
```

5. **Lancer Celery** (dans un terminal séparé) :
```bash
celery -A config worker -l info
```

6. **Lancer Celery Beat** (dans un terminal séparé) :
```bash
celery -A config beat -l info
```

## Configuration

### Variables d'Environnement

#### Obligatoires

- **`ENCRYPTION_KEY`** : Clé de chiffrement AES-256 pour les credentials bancaires
  - Générer avec : `python -c "from finance.services.encryption_service import EncryptionService; print(EncryptionService.generate_key())"`

#### Optionnelles

- **`SYNC_LOG_RETENTION_DAYS`** : Nombre de jours de rétention des logs de synchronisation (défaut: 30)
- **`SYNC_FAILURE_ALERT_THRESHOLD`** : Nombre d'échecs consécutifs avant alerte (défaut: 3)
- **`BANK_SYNC_SCHEDULE_HOUR`** : Heure de synchronisation automatique quotidienne (défaut: 2)
- **`BANK_SYNC_SCHEDULE_MINUTE`** : Minute de synchronisation automatique quotidienne (défaut: 0)
- **`OPENAI_API_KEY`** : Clé API OpenAI pour l'import PDF Trade Republic (optionnel)

### Configuration Celery Beat

La synchronisation automatique est configurée dans `backend/config/celery.py` :

```python
app.conf.beat_schedule = {
    'sync-all-bank-accounts': {
        'task': 'finance.tasks.sync_all_bank_accounts',
        'schedule': crontab(hour=2, minute=0),  # 2h du matin
    },
    'cleanup-old-sync-logs': {
        'task': 'finance.tasks.cleanup_old_sync_logs',
        'schedule': crontab(hour=3, minute=0),  # 3h du matin
    },
}
```

Vous pouvez modifier l'heure via les variables d'environnement `BANK_SYNC_SCHEDULE_HOUR` et `BANK_SYNC_SCHEDULE_MINUTE`.

## Utilisation

### Créer une Connexion Bancaire

1. Accédez à "Connexions Bancaires" dans le menu
2. Cliquez sur "Nouvelle connexion"
3. Remplissez le formulaire avec vos credentials
4. Activez la synchronisation automatique si souhaité
5. Sauvegardez

Pour plus de détails, consultez le [Guide Utilisateur : Connexions Bancaires](docs/user/bank-connections.md).

### Synchronisation Manuelle

Vous pouvez forcer une synchronisation manuelle à tout moment :

- Depuis la liste des connexions : Cliquez sur "Sync"
- Depuis la page de gestion des comptes : Cliquez sur "Sync" pour un compte spécifique

### Consulter les Logs

Pour consulter l'historique des synchronisations :

- Accédez à "Logs de Synchronisation" dans le menu
- Ou cliquez sur "Voir les logs" depuis une connexion spécifique

Les logs affichent :
- Date et heure de synchronisation
- Durée de synchronisation
- Nombre de transactions synchronisées
- Statut (Succès/Erreur/En cours)
- Message d'erreur si échec

### Migration depuis l'Import Manuel

Si vous utilisez actuellement l'import manuel, vous pouvez migrer vers la synchronisation automatique. Consultez le [Guide de Migration](docs/user/migration-manual-to-auto.md) pour les instructions détaillées.

## Documentation

### Documentation Utilisateur

- [Guide Utilisateur : Connexions Bancaires](docs/user/bank-connections.md)
  - Configuration des connexions bancaires
  - Instructions pour chaque provider
  - Gestion de la synchronisation
  - FAQ et dépannage

- [Guide de Migration : Import Manuel vers Synchronisation Automatique](docs/user/migration-manual-to-auto.md)
  - Étapes de migration
  - Gestion des doublons
  - Recommandations

### Documentation Développeur

- [Guide Développeur : Ajouter un Nouveau Connecteur Bancaire](docs/developer/adding-bank-connector.md)
  - Architecture des connecteurs
  - Étapes pour créer un nouveau connecteur
  - Exemples de code
  - Bonnes pratiques

### Documentation Technique

- [Architecture Brownfield](docs/brownfield-architecture.md)
- [PRD - Product Requirements Document](docs/prd.md)
- [Stories](docs/stories/)

## Tests

### Exécuter les Tests

```bash
# Tous les tests
python manage.py test

# Tests spécifiques
python manage.py test finance.tests.test_sync_service
python manage.py test finance.tests.test_import_regression
python manage.py test finance.tests.test_sync_integration
python manage.py test finance.tests.test_performance
```

### Mesurer la Couverture de Tests

```bash
# Installer coverage
pip install coverage

# Exécuter les tests avec coverage
coverage run --source='.' manage.py test

# Générer le rapport
coverage report

# Générer le rapport HTML
coverage html
```

### Types de Tests

- **Tests de non-régression** (`test_import_regression.py`) : Vérifient que les imports manuels continuent de fonctionner
- **Tests d'intégration** (`test_sync_integration.py`) : Testent le flux complet de synchronisation
- **Tests de performance** (`test_performance.py`) : Vérifient que les vues restent performantes
- **Tests unitaires** : Tests des composants individuels

## Structure du Projet

```
dashboard/
├── backend/                 # Application Django
│   ├── finance/            # Application principale
│   │   ├── connectors/     # Connecteurs bancaires
│   │   │   ├── base.py     # Classe abstraite BaseBankConnector
│   │   │   ├── traderepublic.py
│   │   │   ├── boursorama.py
│   │   │   └── hellobank.py
│   │   ├── services/       # Services métier
│   │   │   ├── sync_service.py      # Service de synchronisation
│   │   │   └── encryption_service.py # Service de chiffrement
│   │   ├── models.py       # Modèles Django
│   │   ├── views.py        # Vues Django
│   │   ├── tasks.py        # Tâches Celery
│   │   └── tests/          # Tests
│   ├── config/             # Configuration Django
│   │   └── celery.py       # Configuration Celery
│   └── manage.py
├── docs/                   # Documentation
│   ├── user/              # Documentation utilisateur
│   ├── developer/         # Documentation développeur
│   ├── stories/           # Stories de développement
│   └── qa/                # Rapports QA
├── docker-compose.yml     # Configuration Docker
└── README.md             # Ce fichier
```

## Sécurité

### Chiffrement des Credentials

Les credentials bancaires sont chiffrés avec AES-256 avant stockage dans la base de données. La clé de chiffrement est stockée dans les variables d'environnement et n'est jamais exposée dans le code.

### Bonnes Pratiques

- Ne jamais commiter la clé de chiffrement (`ENCRYPTION_KEY`)
- Utiliser des credentials différents pour chaque environnement (dev, staging, prod)
- Limiter l'accès aux logs de synchronisation (contiennent potentiellement des informations sensibles)
- Mettre à jour régulièrement les dépendances pour corriger les vulnérabilités

## Contribution

Pour contribuer au projet :

1. Créer une branche depuis `main`
2. Implémenter les changements
3. Ajouter des tests pour les nouvelles fonctionnalités
4. Mettre à jour la documentation si nécessaire
5. Créer une pull request

### Ajouter un Nouveau Connecteur

Consultez le [Guide Développeur : Ajouter un Nouveau Connecteur Bancaire](docs/developer/adding-bank-connector.md) pour les instructions détaillées.

## Support

Pour toute question ou problème :

1. Consultez la documentation utilisateur et développeur
2. Vérifiez les logs de synchronisation pour les erreurs
3. Ouvrez une issue sur le dépôt GitHub

## Licence

[À définir]

## Changelog

### Version 1.0 (2025-01-27)

- Synchronisation bancaire automatique
- Support Trade Republic, BoursoBank, Hello Bank
- Logs de synchronisation avec statistiques
- Détection automatique des doublons
- Export CSV des logs
- Tests de non-régression et d'intégration
- Documentation complète utilisateur et développeur
