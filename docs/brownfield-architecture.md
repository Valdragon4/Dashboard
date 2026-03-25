# Dashboard Financier - Brownfield Architecture Document

## Introduction

Ce document capture l'**ÉTAT ACTUEL** du codebase du Dashboard Financier, incluant la dette technique, les workarounds et les patterns réels. Il sert de référence pour les agents IA travaillant sur des améliorations.

### Document Scope

Documentation complète du système existant pour permettre aux agents IA de comprendre le contexte avant toute modification ou ajout de fonctionnalités.

### Change Log

| Date   | Version | Description                 | Author    |
| ------ | ------- | --------------------------- | --------- |
| 2025-01-XX | 1.0     | Initial brownfield analysis | Architect |

## Quick Reference - Key Files and Entry Points

### Critical Files for Understanding the System

- **Main Entry**: `backend/manage.py` - Point d'entrée Django
- **Configuration**: `backend/config/settings.py` - Configuration Django
- **Core Business Logic**: `backend/finance/views.py` - Vues principales (dashboard, transactions, comptes)
- **Data Models**: `backend/finance/models.py` - Modèles Django (Account, Transaction, Category, InvestmentHolding)
- **Import Logic**: `backend/finance/importers/loader.py` - Import CSV/PDF
- **Trade Republic Scraper**: `backend/finance/importers/traderepublic_scraper.py` - Scraping Trade Republic
- **URL Routing**: `backend/config/urls.py`, `backend/finance/urls.py` - Routage des URLs
- **Templates**: `backend/finance/templates/finance/` - Templates HTML Django
- **Docker Setup**: `docker-compose.yml` - Configuration Docker Compose

## High Level Architecture

### Technical Summary

Application web Django monolithique pour la gestion financière personnelle avec :
- Dashboard de visualisation des finances
- Gestion de comptes bancaires multiples
- Suivi des transactions
- Gestion des investissements (Trade Republic)
- Import de relevés bancaires (CSV/PDF)
- Scraping automatisé Trade Republic

### Actual Tech Stack

| Category  | Technology | Version | Notes                      |
| --------- | ---------- | ------- | -------------------------- |
| Runtime   | Python     | 3.x     | Django 4.x requis          |
| Framework | Django     | 4.0+    | Framework web principal    |
| Database  | PostgreSQL | 16      | Via Docker Compose         |
| Cache/Queue | Redis   | 7       | Pour Celery                |
| Task Queue | Celery     | 5.0+    | Tâches asynchrones        |
| Web Server | Gunicorn   | 21.0+   | Serveur WSGI production   |
| PDF Parsing | PyPDF2   | 3.0+    | Extraction texte PDF       |
| AI/ML     | OpenAI API | 1.0+    | Analyse PDF Trade Republic |
| HTTP Client | Requests | 2.31+   | Requêtes HTTP              |
| Data Analysis | Pandas | 2.0+    | Traitement données CSV     |
| Date Utils | python-dateutil | - | Manipulation dates         |

### Repository Structure Reality Check

- **Type**: Monorepo (backend Django + scraper Trade Republic séparé)
- **Package Manager**: pip (requirements.txt)
- **Notable**: 
  - Backend Django dans `backend/`
  - Scraper Trade Republic dans `trade_republic_scraper/` (monté dans Docker)
  - Pas de frontend séparé (templates Django intégrés)
  - Configuration Docker Compose à la racine

## Source Tree and Module Organization

### Project Structure (Actual)

```text
dashboard/
├── backend/                    # Application Django principale
│   ├── config/                  # Configuration Django
│   │   ├── settings.py         # Settings Django (env vars)
│   │   ├── urls.py             # URLs racine
│   │   └── wsgi.py             # WSGI config
│   ├── finance/                # App Django principale
│   │   ├── models.py           # Modèles de données
│   │   ├── views.py            # Vues (dashboard, transactions, comptes)
│   │   ├── urls.py             # URLs de l'app finance
│   │   ├── forms.py            # Formulaires Django
│   │   ├── admin.py            # Admin Django
│   │   ├── services.py         # Services métier (si existant)
│   │   ├── tasks.py            # Tâches Celery
│   │   ├── importers/          # Modules d'import
│   │   │   ├── loader.py       # Point d'entrée import CSV
│   │   │   ├── statement_csv.py    # Parser CSV bancaires génériques
│   │   │   ├── traderepublic_csv.py # Parser CSV Trade Republic
│   │   │   └── traderepublic_scraper.py # Scraper Trade Republic
│   │   ├── templates/          # Templates HTML
│   │   │   └── finance/
│   │   │       ├── dashboard.html
│   │   │       ├── transactions.html
│   │   │       ├── accounts.html
│   │   │       └── ...
│   │   ├── migrations/         # Migrations Django
│   │   └── tests/              # Tests unitaires
│   ├── connectors/             # Connecteurs externes (si existant)
│   ├── manage.py               # CLI Django
│   ├── requirements.txt        # Dépendances Python
│   └── Dockerfile              # Image Docker backend
├── trade_republic_scraper/     # Scraper Trade Republic (séparé)
├── docker-compose.yml           # Orchestration Docker
└── .bmad-core/                 # Configuration BMAD Method
```

### Key Modules and Their Purpose

- **Dashboard View** (`finance/views.py::dashboard`): Vue principale affichant revenus, dépenses, soldes, investissements avec graphiques
- **Transaction Management** (`finance/views.py::transactions`): Liste paginée des transactions avec filtres par compte
- **Account Management** (`finance/views.py::accounts`, `account_create`, `account_detail`): CRUD comptes + détails investissements
- **Import System** (`finance/importers/loader.py`): Import CSV bancaires génériques et Trade Republic
- **Trade Republic Scraper** (`finance/importers/traderepublic_scraper.py`): Scraping automatisé Trade Republic avec 2FA
- **PDF Import** (`finance/views.py::import_traderepublic_pdf`): Import PDF Trade Republic avec analyse OpenAI
- **Investment Tracking** (`finance/models.py::InvestmentHolding`): Suivi des positions d'investissement par portefeuille (PEA/CTO/CRYPTO)

## Data Models and APIs

### Data Models

Voir `backend/finance/models.py` pour les définitions complètes :

- **Account**: Comptes bancaires (CHECKING, SAVINGS, BROKER, CASH, CREDIT)
  - Champs clés: `owner`, `name`, `provider`, `type`, `initial_balance`, `include_in_dashboard`
  - Support multi-portefeuilles pour comptes broker (PEA, CTO, PEA-PME, CRYPTO)
  
- **Transaction**: Transactions financières
  - Champs clés: `account`, `posted_at`, `amount`, `description`, `category`, `account_balance`, `raw` (JSON)
  - **IMPORTANT**: `amount=0` indique un snapshot de valorisation (pas une vraie transaction)
  - `account_balance` contient le solde brut depuis le CSV
  - `raw` JSON stocke métadonnées (portfolio_type, source, etc.)

- **Category**: Catégories de transactions (hiérarchique avec `parent`)
  
- **InvestmentHolding**: Positions d'investissement
  - Champs: `account`, `symbol`, `name`, `quantity`, `avg_cost`, `tax_wrapper` (PEA/CTO)
  
- **InvestmentPrice**: Prix historiques des instruments (non utilisé actuellement)

- **CashflowRule**: Règles d'auto-catégorisation (non utilisé actuellement)

- **BudgetGoal**: Objectifs budgétaires (non utilisé actuellement)

### API Specifications

**Pas d'API REST formelle** - Application Django traditionnelle avec :
- Vues basées sur fonctions (`@login_required`)
- Endpoints JSON pour certaines actions AJAX :
  - `/api/traderepublic/initiate` - Initier connexion Trade Republic
  - `/api/traderepublic/verify` - Vérifier 2FA et scraper
  - `/api/investments/update-valuation` - Mettre à jour valorisation manuelle
  - `/api/accounts/<id>/toggle-dashboard` - Activer/désactiver compte dans dashboard
  - `/api/transactions/<id>/update-category` - Mettre à jour catégorie transaction
  - `/api/traderepublic/import-pdf` - Import PDF Trade Republic

**Format de réponse JSON standard**:
```json
{
  "success": true/false,
  "message": "Message descriptif",
  "error": "Message d'erreur si échec"
}
```

## Technical Debt and Known Issues

### Critical Technical Debt

1. **Calcul de solde complexe** (`finance/views.py::transactions`):
   - Logique de calcul de solde différente selon le provider (hellobank/traderepublic vs autres)
   - Calculs complexes avec `account_balance` pour déterminer l'ordre chronologique
   - Code répétitif pour initialiser les soldes par compte
   - **Impact**: Difficile à maintenir, risque d'erreurs

2. **Gestion des snapshots de valorisation**:
   - Utilisation de `amount=0` pour identifier les snapshots (non intuitif)
   - Logique de valorisation par portefeuille dans `raw.portfolio_type`
   - Calculs de valorisation complexes dans `dashboard()` avec fallback multiples
   - **Impact**: Risque de confusion entre transactions réelles et snapshots

3. **Import PDF Trade Republic avec OpenAI**:
   - Dépendance à OpenAI API pour parser les PDFs
   - Prompts complexes et fragiles dans `import_traderepublic_pdf()`
   - Pas de fallback si OpenAI échoue
   - **Impact**: Coût API, fragilité du parsing

4. **Scraper Trade Republic**:
   - Scraping web fragile (peut casser si Trade Republic change leur UI)
   - Gestion de session Django pour stocker credentials temporairement
   - **Impact**: Maintenance continue requise

5. **Pas de tests automatisés visibles**:
   - Dossier `tests/` existe mais pas de tests visibles dans le code analysé
   - **Impact**: Risque de régression élevé

6. **Gestion des dates complexes**:
   - Période par défaut: du 24 du mois précédent au 24 du mois actuel (logique métier spécifique)
   - Conversions timezone multiples dans `dashboard()`
   - **Impact**: Code difficile à comprendre

### Workarounds and Gotchas

- **Période par défaut**: Du 24 du mois précédent au 24 du mois actuel (logique métier spécifique, pas standard)
- **Exclusions dans dashboard**: Trade Republic, comptes SAVINGS, comptes BROKER exclus des calculs revenus/dépenses
- **Snapshots de valorisation**: `amount=0` + `account_balance` = snapshot (pas une transaction réelle)
- **Calcul solde Hello Bank/Trade Republic**: Calcul depuis `initial_balance` + somme transactions (pas depuis `account_balance`)
- **Multi-portefeuilles**: Valorisation par type (PEA/CTO/CRYPTO) stockée dans `raw.portfolio_type` des transactions snapshot
- **Session Django pour Trade Republic**: Credentials stockés temporairement dans `request.session` pendant le scraping

## Integration Points and External Dependencies

### External Services

| Service  | Purpose  | Integration Type | Key Files                      |
| -------- | -------- | ---------------- | ------------------------------ |
| Trade Republic | Scraping transactions/portefeuille | Web Scraping + API | `finance/importers/traderepublic_scraper.py` |
| OpenAI API | Analyse PDF Trade Republic | REST API | `finance/views.py::import_traderepublic_pdf()` |
| PostgreSQL | Base de données | Django ORM | `config/settings.py` (DATABASES) |
| Redis | Cache + Queue Celery | Django Cache + Celery | `config/settings.py` |

### Internal Integration Points

- **Frontend**: Templates Django intégrés (pas de SPA séparée)
- **Background Jobs**: Celery configuré mais utilisation limitée visible
- **Static Files**: WhiteNoise pour servir les fichiers statiques en production
- **CORS**: django-cors-headers configuré (probablement pour API future)

## Development and Deployment

### Local Development Setup

1. **Prérequis**:
   - Docker et Docker Compose
   - Python 3.x (si développement local sans Docker)
   - Variables d'environnement dans `.env`:
     - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
     - `DJANGO_SECRET_KEY`
     - `DJANGO_DEBUG=True`
     - `OPENAI_API_KEY` (pour import PDF)

2. **Démarrer avec Docker**:
   ```bash
   docker-compose up
   ```
   - Base de données PostgreSQL sur port 5432
   - Application Django sur port 8000
   - Redis sur port 6380
   - Celery worker et beat automatiquement démarrés

3. **Migrations**:
   ```bash
   docker-compose exec web python manage.py migrate
   ```

4. **Créer superutilisateur**:
   ```bash
   docker-compose exec web python manage.py createsuperuser
   ```

### Build and Deployment Process

- **Build**: Docker build via `docker-compose build`
- **Deployment**: `docker-compose up -d` (production)
- **Web Server**: Gunicorn avec 2 workers, timeout 300s
- **Static Files**: WhiteNoise middleware (pas de collectstatic nécessaire)
- **Environments**: Géré via variables d'environnement dans `.env`

## Testing Reality

### Current Test Coverage

- **Unit Tests**: Dossier `backend/finance/tests/` existe mais contenu non analysé
- **Integration Tests**: Non visible dans le code analysé
- **E2E Tests**: Aucun
- **Manual Testing**: Méthode principale de validation

### Running Tests

```bash
# Si tests existent
docker-compose exec web python manage.py test
```

## Key Business Logic Patterns

### Dashboard Calculations

**Période par défaut**: Du 24 du mois précédent au 24 du mois actuel
- Logique métier spécifique (pas un mois calendaire standard)
- Permet de sélectionner un mois ou une période personnalisée

**Exclusions importantes**:
- Transactions Trade Republic exclues des revenus/dépenses (investissements)
- Comptes SAVINGS exclus (transferts internes)
- Comptes BROKER exclus des revenus/dépenses
- Snapshots (`amount=0`) exclus des calculs

**Calculs dashboard**:
- Revenus: Somme transactions positives (montants > 0)
- Dépenses: Somme transactions négatives (absolue)
- Solde courant: Comptes CHECKING uniquement
- Épargne: Comptes SAVINGS
- Investissements: Comptes Trade Republic + BROKER
  - Valorisation actuelle depuis snapshots (`account_balance` des transactions `amount=0`)
  - Montant investi: Somme transactions non-snapshot jusqu'à la date de valorisation
  - Plus-value: Valorisation - Montant investi

### Transaction Balance Calculation

**Deux stratégies selon provider**:

1. **Hello Bank / Trade Republic**:
   - Calcul depuis `initial_balance` + somme de toutes les transactions
   - Ignore `account_balance` pour le calcul (utilisé uniquement pour ordre chronologique)

2. **Autres providers**:
   - Utilise `account_balance` directement si disponible
   - Sinon calcule depuis `initial_balance` + transactions

**Ordre chronologique complexe**:
- Tri par `posted_at`, puis `account_balance`, puis `id`
- Permet de gérer les transactions avec même timestamp

### Investment Valuation Logic

**Multi-portefeuilles**:
- Chaque compte broker peut avoir plusieurs portefeuilles (PEA, CTO, CRYPTO, PEA-PME)
- Valorisation stockée dans transactions snapshot avec `raw.portfolio_type`
- Recherche de la dernière valorisation par type avant une date donnée

**Synchronisation montant investi / valorisation**:
- Montant investi calculé jusqu'à la date de la dernière valorisation
- Évite les incohérences (comparer valorisation du 05/11 avec investi du 10/11)

### Import CSV Logic

**Profiles supportés**:
- `generic`: Format CSV générique
- `hellobank`: Format Hello Bank spécifique
- `traderepublic`: Format Trade Republic CSV

**Détection du solde initial**:
- Pour Hello Bank/Trade Republic: Prendre la première transaction avec `account_balance`
- Sinon: Utiliser `initial_balance` du compte

**Déduplication**:
- Vérification par `posted_at` + `amount` + `description` pour éviter doublons

## Code Patterns and Conventions

### Django Patterns

- **Vues**: Fonctions avec décorateurs `@login_required`
- **Modèles**: Django ORM standard avec `models.TextChoices` pour les enums
- **Forms**: Django Forms dans `forms.py`
- **Templates**: Django Templates avec context processors standards
- **URLs**: `path()` avec noms explicites

### Naming Conventions

- **Modèles**: PascalCase (`Account`, `Transaction`)
- **Vues**: snake_case (`dashboard`, `transaction_create`)
- **Fichiers**: snake_case (`views.py`, `models.py`)
- **Champs modèles**: snake_case (`posted_at`, `account_balance`)

### Error Handling

- **Messages Django**: `messages.success()` / `messages.error()` pour feedback utilisateur
- **Exceptions**: Try/except avec messages d'erreur descriptifs
- **JSON Responses**: Format standardisé `{"success": bool, "message": str, "error": str}`

## Security Considerations

- **Authentication**: Django auth standard (`@login_required`)
- **CSRF**: Protection CSRF Django activée
- **SQL Injection**: Protection via Django ORM (pas de requêtes SQL brutes visibles)
- **XSS**: Templates Django échappent automatiquement
- **Credentials**: Trade Republic credentials stockés temporairement en session Django (non persistés)

## Performance Considerations

- **Database Indexes**: 
  - `Transaction.posted_at`
  - `Transaction(account, posted_at)` composite
- **Queries**: Utilisation de `select_related()` pour éviter N+1 queries
- **Pagination**: Transactions paginées (100 par page)
- **Aggregations**: Utilisation de `Sum()`, `aggregate()` pour calculs DB-side

## Future Enhancement Areas

### Identified Opportunities

1. **API REST formelle**: Actuellement endpoints JSON ad-hoc, pourrait bénéficier de Django REST Framework
2. **Tests automatisés**: Ajouter tests unitaires et d'intégration
3. **Refactoring calcul solde**: Simplifier la logique de calcul de solde
4. **Frontend moderne**: SPA React/Vue pour meilleure UX
5. **Export données**: Export CSV/PDF des transactions
6. **Budgeting**: Implémenter les règles `CashflowRule` et `BudgetGoal`
7. **Multi-devises**: Support complet multi-devises (actuellement EUR par défaut)
8. **Notifications**: Alertes budget, rappels import, etc.

## Appendix - Useful Commands and Scripts

### Frequently Used Commands

```bash
# Démarrer l'environnement
docker-compose up

# Migrations
docker-compose exec web python manage.py migrate

# Créer superutilisateur
docker-compose exec web python manage.py createsuperuser

# Shell Django
docker-compose exec web python manage.py shell

# Collecter fichiers statiques (si nécessaire)
docker-compose exec web python manage.py collectstatic

# Voir les logs
docker-compose logs -f web
```

### Debugging and Troubleshooting

- **Logs**: `docker-compose logs web` pour logs application
- **Database**: `docker-compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB`
- **Debug Mode**: `DJANGO_DEBUG=True` dans `.env`
- **Common Issues**:
  - Port 8000 déjà utilisé: Changer dans `docker-compose.yml`
  - Database connection errors: Vérifier variables `.env`
  - OpenAI API errors: Vérifier `OPENAI_API_KEY` dans `.env`

## Notes

- Ce document reflète l'**état réel** du système, incluant la dette technique
- Les patterns documentés sont ceux **actuellement utilisés**, pas des idéaux
- Les contraintes et workarounds sont documentés pour éviter les erreurs
- Ce document doit être mis à jour lors de changements architecturaux majeurs
