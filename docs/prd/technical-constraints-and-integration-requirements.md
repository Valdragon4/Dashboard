# Technical Constraints and Integration Requirements

## Banking Integration Options Research

**Résultats de recherche sur les options techniques disponibles** :

### Option 1: Scraping Direct (Approche actuelle Trade Republic)
- **Avantages** : Contrôle total, pas de dépendance externe, gratuit
- **Inconvénients** : Fragile (peut casser si la banque change son interface), maintenance continue requise, risque de violation des CGU
- **Statut** : ✅ Déjà implémenté pour Trade Republic (WebSocket + REST API)
- **Recommandation** : Continuer cette approche pour Trade Republic, explorer pour BoursoBank/Hello Bank

### Option 2: APIs Agrégatrices Tierces (POWENS, BudgetBakers, Bridge API, etc.)
- **Avantages** : Solution professionnelle, maintenance gérée par le fournisseur, conforme PSD2/DSP2
- **Inconvénients** : Coût (abonnement mensuel), dépendance externe, données transitent par un tiers, pas toujours disponible pour toutes les banques françaises
- **Statut** : ⚠️ Exploré (POWENS) mais non convaincant pour l'utilisateur
- **Recommandation** : Garder comme option de secours si le scraping direct échoue

### Option 3: Open Banking PSD2/DSP2 (APIs Officielles)
- **Avantages** : Légal, sécurisé, standardisé
- **Inconvénients** : Pas toutes les banques françaises ont des APIs PSD2 complètes, processus d'accréditation complexe, peut nécessiter un agrément AISP
- **Statut** : ❓ À explorer pour BoursoBank et Hello Bank
- **Recommandation** : Vérifier la disponibilité des APIs PSD2 pour ces banques

### Option 4: Scraping avec Selenium/Playwright (Automatisation navigateur)
- **Avantages** : Plus robuste que le scraping HTTP pur, peut gérer JavaScript complexe
- **Inconvénients** : Plus lent, plus de ressources, nécessite un navigateur headless
- **Statut** : ⚠️ Option à considérer si le scraping HTTP échoue
- **Recommandation** : Utiliser comme fallback si nécessaire

**Décision technique recommandée** :
1. **Trade Republic** : Continuer avec l'approche WebSocket/REST existante (déjà fonctionnelle)
2. **BoursoBank** : Explorer d'abord le scraping HTTP/API non-officielle, puis Selenium si nécessaire
3. **Hello Bank** : Explorer d'abord le scraping HTTP/API non-officielle, puis Selenium si nécessaire
4. **Fallback** : Garder l'option d'une API agrégatrice tierce (BudgetBakers, Bridge API) si le scraping direct s'avère trop fragile

## Existing Technology Stack

**Languages**: Python 3.x
**Frameworks**: Django 4.0+, Celery 5.0+
**Database**: PostgreSQL 16
**Infrastructure**: Docker Compose, Gunicorn, Redis 7
**External Dependencies**: 
- Trade Republic API (WebSocket + REST)
- OpenAI API (pour PDF Trade Republic)
- Requests, Pandas, PyPDF2, python-dateutil
- **Potentiellement** : Selenium/Playwright pour scraping navigateur (si nécessaire)
- **Potentiellement** : Bibliothèque de chiffrement (cryptography) pour stockage sécurisé des credentials

## Integration Approach

**Database Integration Strategy**: 
- Nouveau modèle `BankConnection` pour stocker les credentials chiffrés et métadonnées de connexion
- Nouveau modèle `SyncLog` pour tracer les synchronisations (succès/échecs, timestamps)
- Extension du modèle `Account` avec un champ `auto_sync_enabled` et relation vers `BankConnection`
- Pas de migration destructive des données existantes

**API Integration Strategy**:
- Architecture modulaire de connecteurs bancaires (`finance/connectors/`)
  - Classe abstraite `BaseBankConnector` avec méthodes communes
  - Implémentations spécifiques : `TradeRepublicConnector`, `BoursoBankConnector`, `HelloBankConnector`
- Réutilisation du scraper Trade Republic existant comme base pour `TradeRepublicConnector`
- Nouvelle API REST pour gérer les connexions bancaires (`/api/bank-connections/`)
- Tâches Celery pour synchronisation automatique (`finance/tasks.py`)

**Frontend Integration Strategy**:
- Nouvelle page de gestion des connexions bancaires (`finance/templates/finance/bank_connections.html`)
- Extension de la page de gestion des comptes pour afficher le statut de synchronisation
- Bouton de synchronisation manuelle sur chaque compte connecté
- Notifications Django messages pour les succès/échecs de synchronisation

**Testing Integration Strategy**:
- Tests unitaires pour chaque connecteur bancaire avec mocks des APIs
- Tests d'intégration pour le flux complet de synchronisation
- Tests de non-régression pour l'import CSV/PDF manuel existant

## Code Organization and Standards

**File Structure Approach**:
```
backend/finance/
├── connectors/              # Nouveau module de connecteurs
│   ├── __init__.py
│   ├── base.py             # Classe abstraite BaseBankConnector
│   ├── traderepublic.py     # Connecteur Trade Republic (refactorisé)
│   ├── boursorama.py       # Connecteur BoursoBank
│   └── hellobank.py         # Connecteur Hello Bank
├── models.py               # Extension avec BankConnection, SyncLog
├── tasks.py                # Tâches Celery de synchronisation
├── views.py                # Nouvelles vues pour gestion connexions
└── services/               # Services métier (si nécessaire)
    └── sync_service.py      # Logique de synchronisation centralisée
```

**Naming Conventions**:
- Connecteurs : `{Bank}Connector` (PascalCase)
- Méthodes de connecteur : `sync_transactions()`, `authenticate()`, `get_balance()` (snake_case)
- Tâches Celery : `sync_bank_account_{bank}` (snake_case)
- Modèles : `BankConnection`, `SyncLog` (PascalCase)

**Coding Standards**:
- Suivre les conventions Django existantes
- Utiliser les type hints Python pour la clarté
- Documentation docstring pour toutes les méthodes publiques
- Gestion d'erreurs explicite avec logging approprié

**Documentation Standards**:
- README pour chaque connecteur expliquant l'API utilisée
- Documentation des credentials requis et format de stockage
- Guide de développement pour ajouter un nouveau connecteur

## Deployment and Operations

**Build Process Integration**:
- Pas de changement au processus de build Docker existant
- Nouvelles dépendances Python ajoutées à `requirements.txt` si nécessaire (cryptographie pour chiffrement)

**Deployment Strategy**:
- Déploiement via Docker Compose existant
- Configuration Celery Beat pour planifier les synchronisations quotidiennes
- Variables d'environnement pour les clés de chiffrement des credentials

**Monitoring and Logging**:
- Logging structuré des synchronisations dans `SyncLog` model
- Logs Django pour debugging des erreurs de connecteurs
- Alertes en cas d'échecs répétés de synchronisation (à définir)

**Configuration Management**:
- Configuration des banques supportées dans `settings.py`
- Schedule Celery Beat configurable via variables d'environnement
- Paramètres de retry configurables par connecteur

## Risk Assessment and Mitigation

**Technical Risks**:
- **Changement d'API bancaire** : Les APIs peuvent changer sans préavis, cassant les connecteurs
  - *Mitigation* : Architecture modulaire permettant mise à jour rapide, monitoring des erreurs, fallback sur import manuel
- **Sécurité des credentials** : Stockage de credentials bancaires sensibles
  - *Mitigation* : Chiffrement AES-256, pas de logs de credentials, audit de sécurité
- **Rate limiting** : Les banques peuvent limiter les requêtes
  - *Mitigation* : Respect des rate limits, retry avec backoff, synchronisation échelonnée

**Integration Risks**:
- **Incompatibilité avec données existantes** : Les nouvelles données peuvent créer des incohérences
  - *Mitigation* : Tests de non-régression, validation des données avant import, détection de doublons robuste
- **Performance** : Augmentation du volume de données peut ralentir le dashboard
  - *Mitigation* : Indexation appropriée, pagination, optimisation des requêtes

**Deployment Risks**:
- **Downtime lors du déploiement** : Les synchronisations peuvent échouer pendant le déploiement
  - *Mitigation* : Déploiement progressif, rollback plan, synchronisation manuelle de secours
- **Migration de données** : Migration des credentials existants vers nouveau format
  - *Mitigation* : Script de migration testé, sauvegarde avant migration, rollback possible

**Mitigation Strategies**:
- Phase de développement progressive avec tests approfondis
- Documentation complète de chaque connecteur pour faciliter la maintenance
- Monitoring proactif des synchronisations pour détecter les problèmes rapidement
- Plan de rollback vers import manuel si nécessaire
