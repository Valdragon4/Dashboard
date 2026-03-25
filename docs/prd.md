# Dashboard Financier - Brownfield Enhancement PRD

## Intro Project Analysis and Context

### Analysis Source

Document-project output available at: `docs/brownfield-architecture.md`

### Current Project State

Le Dashboard Financier est une application web Django monolithique pour la gestion financière personnelle avec :

- **Dashboard de visualisation** : Affichage des revenus, dépenses, soldes, investissements avec graphiques et statistiques
- **Gestion de comptes multiples** : Support de différents types de comptes (CHECKING, SAVINGS, BROKER, CASH, CREDIT)
- **Suivi des transactions** : Import CSV manuel et scraping Trade Republic avec authentification 2FA
- **Gestion des investissements** : Suivi multi-portefeuilles (PEA, CTO, CRYPTO, PEA-PME) avec valorisation
- **Import de données** : Support CSV générique, CSV Hello Bank, CSV Trade Republic, et PDF Trade Republic (avec OpenAI)

**Stack technique actuelle** :
- Backend : Django 4.0+, Python 3.x
- Database : PostgreSQL 16
- Task Queue : Celery + Redis 7
- Scraping : WebSocket + REST API pour Trade Republic
- PDF Analysis : OpenAI API

**Patterns existants** :
- Scraping Trade Republic via WebSocket avec authentification 2FA
- Import CSV via parsers spécialisés par provider
- Tâches Celery configurées mais utilisation limitée
- Stockage temporaire des credentials en session Django

### Available Documentation Analysis

Document-project analysis available - using existing technical documentation.

**Documentation disponible** :
- ✓ Tech Stack Documentation (`docs/brownfield-architecture.md`)
- ✓ Source Tree/Architecture (`docs/brownfield-architecture.md`)
- ✓ API Documentation (endpoints JSON documentés)
- ✓ External API Documentation (Trade Republic, OpenAI)
- ✓ Technical Debt Documentation (dette technique identifiée)
- ⚠ Coding Standards (partiel - patterns Django standards)
- ⚠ UX/UI Guidelines (non documenté - templates Django intégrés)

### Enhancement Scope Definition

#### Enhancement Type

- ✓ New Feature Addition
- ✓ Integration with New Systems

#### Enhancement Description

Intégration automatique quotidienne avec plusieurs banques (BoursoBank, Hello Bank, Trade Republic) pour récupérer automatiquement les transactions et données de comptes sans intervention manuelle. L'objectif est de remplacer les imports CSV/PDF manuels par un système automatisé fiable et maintenable.

#### Impact Assessment

- ✓ Significant Impact (substantial existing code changes)
  - Nouveau système de connecteurs bancaires
  - Refactoring de l'import existant
  - Nouvelle infrastructure de tâches Celery
  - Gestion sécurisée des credentials bancaires
  - Système de monitoring et alertes

### Goals and Background Context

#### Goals

- Automatiser la récupération quotidienne des données bancaires sans intervention manuelle
- Centraliser la gestion des credentials bancaires de manière sécurisée
- Réduire le temps passé sur l'import manuel de données
- Améliorer la fiabilité et la maintenabilité du système d'import
- Permettre l'ajout facile de nouveaux providers bancaires
- Assurer la continuité du service même en cas d'échec temporaire d'un provider

#### Background Context

Actuellement, l'utilisateur doit importer manuellement les données depuis chaque banque via CSV ou PDF. Le système dispose déjà d'un scraper Trade Republic fonctionnel avec authentification 2FA, mais il nécessite une intervention manuelle à chaque import. L'exploration de POWENS n'a pas été concluante, nécessitant une solution plus solide et maîtrisable.

Cette amélioration permettra de transformer le dashboard en un véritable système de suivi financier automatique, similaire aux applications de gestion financière modernes, tout en conservant le contrôle sur les données et l'infrastructure.

### Change Log

| Change | Date | Version | Description | Author |
| ------ | ---- | ------- | ----------- | ------ |
| Initial PRD | 2025-01-XX | 1.0 | Création du PRD pour intégration bancaire automatique | PM |

## Requirements

### Functional Requirements

**FR1**: Le système doit permettre la connexion sécurisée à BoursoBank, Hello Bank et Trade Republic avec stockage chiffré des credentials.

**FR2**: Le système doit récupérer automatiquement les transactions de chaque compte bancaire connecté une fois par jour via des tâches Celery planifiées.

**FR3**: Le système doit détecter et éviter les doublons lors de l'import automatique en comparant les transactions existantes par date, montant et description.

**FR4**: Le système doit gérer l'authentification 2FA pour les banques qui le requièrent (Trade Republic, potentiellement BoursoBank) avec stockage sécurisé des tokens de session.

**FR5**: Le système doit permettre à l'utilisateur de configurer quels comptes doivent être synchronisés automatiquement via une interface de gestion des connexions bancaires.

**FR6**: Le système doit permettre la synchronisation manuelle à la demande depuis l'interface utilisateur en plus de la synchronisation automatique.

**FR7**: Le système doit afficher l'état de la dernière synchronisation pour chaque compte (succès, échec, date/heure) dans l'interface de gestion des comptes.

**FR8**: Le système doit gérer les erreurs de connexion de manière gracieuse (retry automatique, notification à l'utilisateur) sans bloquer les autres synchronisations.

**FR9**: Le système doit supporter l'ajout de nouveaux providers bancaires sans modification majeure du code existant via une architecture de connecteurs modulaire.

**FR10**: Le système doit maintenir la compatibilité avec les imports CSV/PDF manuels existants pour permettre une transition progressive.

**FR11**: Le système doit synchroniser les soldes de comptes en plus des transactions pour maintenir la cohérence des données.

**FR12**: Pour Trade Republic, le système doit synchroniser les valorisations de portefeuilles (PEA/CTO/CRYPTO) en plus des transactions.

### Non-Functional Requirements

**NFR1**: Les credentials bancaires doivent être stockés de manière chiffrée dans la base de données avec un chiffrement au repos (AES-256).

**NFR2**: Les tâches de synchronisation doivent s'exécuter de manière asynchrone via Celery pour ne pas bloquer l'application web.

**NFR3**: Le système doit supporter au moins 3 tentatives de retry avec backoff exponentiel en cas d'échec temporaire de connexion.

**NFR4**: Les synchronisations doivent s'exécuter dans un délai raisonnable (< 5 minutes par compte) pour éviter les timeouts.

**NFR5**: Le système doit maintenir les performances existantes du dashboard malgré l'augmentation du volume de données.

**NFR6**: Les logs de synchronisation doivent être conservés pendant au moins 30 jours pour le debugging et l'audit.

**NFR7**: Le système doit être résilient aux changements d'API des banques (gestion des erreurs, fallback, alertes).

**NFR8**: La consommation mémoire et CPU des tâches Celery doit rester dans les limites raisonnables (< 500MB RAM par worker).

**NFR9**: Le système doit respecter les rate limits des APIs bancaires pour éviter les blocages de compte.

**NFR10**: Les données synchronisées doivent être cohérentes avec les données existantes (pas de corruption, pas de perte de données).

### Compatibility Requirements

**CR1**: L'API existante d'import CSV/PDF manuel doit rester fonctionnelle sans modification pour permettre une transition progressive.

**CR2**: Le modèle de données `Account` et `Transaction` doit rester compatible avec les données existantes (pas de migration destructive).

**CR3**: Les calculs du dashboard existants doivent continuer à fonctionner avec les nouvelles données synchronisées automatiquement.

**CR4**: Le système de scraping Trade Republic existant doit être intégré dans la nouvelle architecture de connecteurs sans casser la fonctionnalité actuelle.

**CR5**: L'interface utilisateur existante doit être étendue plutôt que remplacée pour maintenir la familiarité.

## Technical Constraints and Integration Requirements

### Banking Integration Options Research

**Résultats de recherche sur les options techniques disponibles** :

#### Option 1: Scraping Direct (Approche actuelle Trade Republic)
- **Avantages** : Contrôle total, pas de dépendance externe, gratuit
- **Inconvénients** : Fragile (peut casser si la banque change son interface), maintenance continue requise, risque de violation des CGU
- **Statut** : ✅ Déjà implémenté pour Trade Republic (WebSocket + REST API)
- **Recommandation** : Continuer cette approche pour Trade Republic, explorer pour BoursoBank/Hello Bank

#### Option 2: APIs Agrégatrices Tierces (POWENS, BudgetBakers, Bridge API, etc.)
- **Avantages** : Solution professionnelle, maintenance gérée par le fournisseur, conforme PSD2/DSP2
- **Inconvénients** : Coût (abonnement mensuel), dépendance externe, données transitent par un tiers, pas toujours disponible pour toutes les banques françaises
- **Statut** : ⚠️ Exploré (POWENS) mais non convaincant pour l'utilisateur
- **Recommandation** : Garder comme option de secours si le scraping direct échoue

#### Option 3: Open Banking PSD2/DSP2 (APIs Officielles)
- **Avantages** : Légal, sécurisé, standardisé
- **Inconvénients** : Pas toutes les banques françaises ont des APIs PSD2 complètes, processus d'accréditation complexe, peut nécessiter un agrément AISP
- **Statut** : ❓ À explorer pour BoursoBank et Hello Bank
- **Recommandation** : Vérifier la disponibilité des APIs PSD2 pour ces banques

#### Option 4: Scraping avec Selenium/Playwright (Automatisation navigateur)
- **Avantages** : Plus robuste que le scraping HTTP pur, peut gérer JavaScript complexe
- **Inconvénients** : Plus lent, plus de ressources, nécessite un navigateur headless
- **Statut** : ⚠️ Option à considérer si le scraping HTTP échoue
- **Recommandation** : Utiliser comme fallback si nécessaire

**Décision technique recommandée** :
1. **Trade Republic** : Continuer avec l'approche WebSocket/REST existante (déjà fonctionnelle)
2. **BoursoBank** : Explorer d'abord le scraping HTTP/API non-officielle, puis Selenium si nécessaire
3. **Hello Bank** : Explorer d'abord le scraping HTTP/API non-officielle, puis Selenium si nécessaire
4. **Fallback** : Garder l'option d'une API agrégatrice tierce (BudgetBakers, Bridge API) si le scraping direct s'avère trop fragile

### Existing Technology Stack

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

### Integration Approach

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

### Code Organization and Standards

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

### Deployment and Operations

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

### Risk Assessment and Mitigation

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

## Epic and Story Structure

### Epic Approach

**Epic Structure Decision**: Single comprehensive epic "Intégration Bancaire Automatique" car toutes les fonctionnalités sont liées et doivent être développées de manière coordonnée pour assurer la cohérence du système.

Cette amélioration nécessite une architecture modulaire de connecteurs, une infrastructure de synchronisation, et une interface utilisateur cohérente. Un seul epic permet de maintenir la vision globale tout en séquençant les stories pour minimiser les risques.

## Epic 1: Intégration Bancaire Automatique

**Epic Goal**: Permettre la synchronisation automatique quotidienne des données bancaires depuis BoursoBank, Hello Bank et Trade Republic sans intervention manuelle, tout en maintenant la compatibilité avec les imports manuels existants.

**Integration Requirements**: 
- Architecture modulaire de connecteurs bancaires réutilisable
- Infrastructure Celery pour synchronisation asynchrone
- Stockage sécurisé des credentials bancaires
- Interface utilisateur pour gestion des connexions
- Système de monitoring et logging des synchronisations
- Compatibilité avec l'import CSV/PDF manuel existant

### Story 1.1: Architecture de Base des Connecteurs Bancaires

**As a** développeur,
**I want** une architecture modulaire de connecteurs bancaires avec une classe abstraite commune,
**so that** je peux facilement ajouter de nouveaux providers sans dupliquer le code.

**Acceptance Criteria**:
1. Classe abstraite `BaseBankConnector` définie avec méthodes communes (`authenticate()`, `sync_transactions()`, `get_balance()`, `disconnect()`)
2. Interface standardisée pour les erreurs de connecteur (`BankConnectionError`, `AuthenticationError`, `RateLimitError`)
3. Structure de module `finance/connectors/` créée avec `__init__.py` et `base.py`
4. Documentation de l'architecture des connecteurs dans `docs/architecture/connectors.md`
5. Tests unitaires de base pour la classe abstraite

**Integration Verification**:
- IV1: Les imports existants CSV/PDF continuent de fonctionner sans modification
- IV2: Le code existant du scraper Trade Republic peut être intégré dans la nouvelle architecture
- IV3: Pas d'impact sur les performances du dashboard existant

### Story 1.2: Modèle de Données pour Connexions Bancaires

**As a** utilisateur,
**I want** pouvoir connecter mes comptes bancaires avec stockage sécurisé des credentials,
**so that** le système peut synchroniser automatiquement mes données.

**Acceptance Criteria**:
1. Modèle `BankConnection` créé avec champs : `user`, `provider`, `account_name`, `encrypted_credentials`, `auto_sync_enabled`, `last_sync_at`, `sync_status`
2. Modèle `SyncLog` créé pour tracer les synchronisations : `bank_connection`, `sync_type`, `status`, `started_at`, `completed_at`, `error_message`, `transactions_count`
3. Extension du modèle `Account` avec relation optionnelle vers `BankConnection` et champ `auto_sync_enabled`
4. Migration Django créée et testée sans perte de données existantes
5. Service de chiffrement/déchiffrement des credentials implémenté avec AES-256

**Integration Verification**:
- IV1: Les comptes existants sans `BankConnection` continuent de fonctionner normalement
- IV2: Les imports CSV/PDF manuels créent toujours des `Account` sans `BankConnection`
- IV3: Les requêtes du dashboard existant ne sont pas impactées par les nouveaux modèles

### Story 1.3: Connecteur Trade Republic (Refactorisation)

**As a** utilisateur,
**I want** que le scraper Trade Republic existant soit intégré dans la nouvelle architecture de connecteurs,
**so that** je peux synchroniser automatiquement mes comptes Trade Republic.

**Acceptance Criteria**:
1. Classe `TradeRepublicConnector` créée héritant de `BaseBankConnector`
2. Code existant du scraper refactorisé dans le nouveau connecteur
3. Support de l'authentification 2FA avec stockage sécurisé du token de session
4. Méthode `sync_transactions()` récupère toutes les transactions depuis la dernière synchronisation
5. Méthode `sync_portfolio_valuations()` pour synchroniser les valorisations PEA/CTO/CRYPTO
6. Gestion des erreurs et retry automatique en cas d'échec temporaire
7. Tests unitaires avec mocks de l'API Trade Republic

**Integration Verification**:
- IV1: L'import Trade Republic manuel existant continue de fonctionner via l'ancienne interface
- IV2: Les données synchronisées automatiquement sont compatibles avec les données importées manuellement
- IV3: Les calculs du dashboard fonctionnent correctement avec les nouvelles données Trade Republic

### Story 1.4: Connecteur BoursoBank

**As a** utilisateur,
**I want** pouvoir connecter mes comptes BoursoBank pour synchronisation automatique,
**so that** je n'ai plus besoin d'importer manuellement les CSV.

**Acceptance Criteria**:
1. Recherche et évaluation des options techniques disponibles :
   - Exploration de l'API/scraping HTTP direct (si disponible)
   - Vérification de l'API PSD2 officielle BoursoBank
   - Évaluation de Selenium/Playwright comme fallback
   - Documentation des options dans `docs/connectors/boursorama-research.md`
2. Classe `BoursoBankConnector` créée avec implémentation de l'authentification selon l'approche choisie
3. Support de la récupération des transactions et soldes
4. Gestion de l'authentification (login/password, potentiellement 2FA) adaptée à l'approche choisie
5. Détection et évitement des doublons lors de la synchronisation
6. Gestion robuste des erreurs avec retry et fallback si l'approche principale échoue
7. Tests unitaires avec mocks de l'API/scraping BoursoBank
8. Documentation du connecteur dans `docs/connectors/boursorama.md` avec détails de l'approche technique choisie

**Integration Verification**:
- IV1: Les imports CSV BoursoBank manuels existants continuent de fonctionner
- IV2: Les données synchronisées sont compatibles avec le format CSV existant
- IV3: Pas de conflit avec les autres connecteurs

### Story 1.5: Connecteur Hello Bank

**As a** utilisateur,
**I want** pouvoir connecter mes comptes Hello Bank pour synchronisation automatique,
**so that** je n'ai plus besoin d'importer manuellement les CSV.

**Acceptance Criteria**:
1. Recherche et évaluation des options techniques disponibles :
   - Exploration de l'API/scraping HTTP direct (si disponible)
   - Vérification de l'API PSD2 officielle Hello Bank
   - Évaluation de Selenium/Playwright comme fallback
   - Documentation des options dans `docs/connectors/hellobank-research.md`
2. Classe `HelloBankConnector` créée avec implémentation de l'authentification selon l'approche choisie
3. Support de la récupération des transactions et soldes
4. Gestion de l'authentification (login/password, potentiellement 2FA) adaptée à l'approche choisie
5. Détection et évitement des doublons lors de la synchronisation
6. Gestion robuste des erreurs avec retry et fallback si l'approche principale échoue
7. Tests unitaires avec mocks de l'API/scraping Hello Bank
8. Documentation du connecteur dans `docs/connectors/hellobank.md` avec détails de l'approche technique choisie

**Integration Verification**:
- IV1: Les imports CSV Hello Bank manuels existants continuent de fonctionner
- IV2: Les données synchronisées sont compatibles avec le format CSV existant
- IV3: Pas de conflit avec les autres connecteurs

### Story 1.6: Service de Synchronisation Centralisé

**As a** développeur,
**I want** un service centralisé de synchronisation qui orchestre les connecteurs,
**so that** la logique de synchronisation est réutilisable et testable.

**Acceptance Criteria**:
1. Service `SyncService` créé dans `finance/services/sync_service.py`
2. Méthode `sync_account(account)` qui utilise le connecteur approprié
3. Gestion de la détection de doublons centralisée
4. Gestion des erreurs et retry avec backoff exponentiel
5. Création automatique de `SyncLog` pour chaque synchronisation
6. Mise à jour des champs `last_sync_at` et `sync_status` sur `BankConnection`
7. Tests unitaires du service de synchronisation

**Integration Verification**:
- IV1: Le service peut être utilisé depuis les tâches Celery et les vues Django
- IV2: Les erreurs de synchronisation sont loggées sans casser l'application
- IV3: Les données synchronisées respectent les contraintes de la base de données existante

### Story 1.7: Tâches Celery pour Synchronisation Automatique

**As a** utilisateur,
**I want** que mes comptes soient synchronisés automatiquement une fois par jour,
**so that** mes données sont toujours à jour sans intervention manuelle.

**Acceptance Criteria**:
1. Tâche Celery `sync_all_bank_accounts()` créée dans `finance/tasks.py`
2. Tâche Celery `sync_bank_account(account_id)` pour synchronisation d'un compte spécifique
3. Configuration Celery Beat pour exécuter `sync_all_bank_accounts()` quotidiennement (configurable)
4. Gestion des erreurs dans les tâches avec retry automatique
5. Notification à l'utilisateur en cas d'échec répété (via Django messages ou email)
6. Logging structuré des synchronisations dans `SyncLog`
7. Tests des tâches Celery avec mocks des connecteurs

**Integration Verification**:
- IV1: Les tâches Celery existantes (si présentes) continuent de fonctionner
- IV2: Les synchronisations automatiques n'interfèrent pas avec les imports manuels
- IV3: Les performances du système ne sont pas dégradées par les tâches de synchronisation

### Story 1.8: Interface de Gestion des Connexions Bancaires

**As a** utilisateur,
**I want** une interface pour gérer mes connexions bancaires (ajouter, configurer, supprimer),
**so that** je peux contrôler quels comptes sont synchronisés automatiquement.

**Acceptance Criteria**:
1. Page `/bank-connections/` créée avec liste des connexions existantes
2. Formulaire d'ajout de nouvelle connexion bancaire avec sélection du provider
3. Formulaire de configuration des credentials avec chiffrement côté serveur
4. Affichage du statut de synchronisation (dernière sync, statut, nombre de transactions)
5. Bouton de synchronisation manuelle pour chaque connexion
6. Bouton de suppression de connexion avec confirmation
7. Gestion de l'authentification 2FA dans l'interface (pour Trade Republic)
8. Messages de succès/erreur pour les actions utilisateur

**Integration Verification**:
- IV1: L'interface utilise les modèles et services existants sans duplication
- IV2: Les comptes sans connexion bancaire continuent d'être affichés normalement
- IV3: L'interface est cohérente avec le design existant du dashboard

### Story 1.9: Extension de la Page de Gestion des Comptes

**As a** utilisateur,
**I want** voir le statut de synchronisation de mes comptes sur la page de gestion des comptes,
**so that** je peux rapidement identifier les comptes synchronisés automatiquement.

**Acceptance Criteria**:
1. Page `/accounts/` étendue pour afficher le statut de synchronisation
2. Badge/indicateur visuel pour les comptes avec synchronisation automatique activée
3. Affichage de la date/heure de la dernière synchronisation
4. Affichage du statut (succès, échec, en cours) avec code couleur
5. Bouton de synchronisation manuelle directement depuis la liste des comptes
6. Lien vers la page de gestion des connexions pour les comptes connectés

**Integration Verification**:
- IV1: Les comptes sans connexion bancaire s'affichent normalement sans erreur
- IV2: Les performances de la page ne sont pas dégradées par les nouvelles requêtes
- IV3: Le design reste cohérent avec l'interface existante

### Story 1.10: Monitoring et Logging des Synchronisations

**As a** administrateur,
**I want** pouvoir consulter l'historique des synchronisations et diagnostiquer les problèmes,
**so that** je peux assurer la fiabilité du système.

**Acceptance Criteria**:
1. Page `/bank-connections/logs/` affichant l'historique des synchronisations depuis `SyncLog`
2. Filtres par compte, statut, date pour faciliter la recherche
3. Affichage détaillé des erreurs avec stack trace pour debugging
4. Statistiques de synchronisation (taux de succès, temps moyen, nombre de transactions)
5. Alertes automatiques en cas d'échecs répétés (configurable)
6. Export des logs en CSV pour analyse externe
7. Rétention des logs configurable (30 jours par défaut)

**Integration Verification**:
- IV1: Les logs n'impactent pas les performances des synchronisations
- IV2: Les logs sont accessibles sans exposer les credentials bancaires
- IV3: L'interface de logs est cohérente avec le reste de l'application

### Story 1.11: Tests de Non-Régression et Documentation

**As a** développeur,
**I want** des tests complets et une documentation à jour,
**so that** le système est maintenable et les nouvelles fonctionnalités sont bien intégrées.

**Acceptance Criteria**:
1. Tests de non-régression pour tous les imports CSV/PDF manuels existants
2. Tests d'intégration pour le flux complet de synchronisation automatique
3. Tests de performance pour s'assurer que le dashboard reste rapide
4. Documentation utilisateur pour la configuration des connexions bancaires
5. Documentation développeur pour l'ajout de nouveaux connecteurs
6. Guide de migration depuis l'import manuel vers la synchronisation automatique
7. README mis à jour avec les nouvelles fonctionnalités

**Integration Verification**:
- IV1: Tous les tests existants passent toujours
- IV2: La couverture de tests est maintenue ou améliorée
- IV3: La documentation est accessible et à jour

---

**Note sur la séquence des stories** : Cette séquence est conçue pour minimiser les risques sur votre système existant. Les stories 1.1-1.2 établissent la fondation, les stories 1.3-1.5 implémentent les connecteurs (en commençant par Trade Republic qui existe déjà), puis les stories 1.6-1.10 ajoutent l'automatisation et l'interface utilisateur. La story 1.11 assure la qualité finale.

Cette séquence vous permet-elle de valider l'ordre logique compte tenu de l'architecture et des contraintes de votre projet ?
