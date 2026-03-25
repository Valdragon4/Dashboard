# Intro Project Analysis and Context

## Analysis Source

Document-project output available at: `docs/brownfield-architecture.md`

## Current Project State

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

## Available Documentation Analysis

Document-project analysis available - using existing technical documentation.

**Documentation disponible** :
- ✓ Tech Stack Documentation (`docs/brownfield-architecture.md`)
- ✓ Source Tree/Architecture (`docs/brownfield-architecture.md`)
- ✓ API Documentation (endpoints JSON documentés)
- ✓ External API Documentation (Trade Republic, OpenAI)
- ✓ Technical Debt Documentation (dette technique identifiée)
- ⚠ Coding Standards (partiel - patterns Django standards)
- ⚠ UX/UI Guidelines (non documenté - templates Django intégrés)

## Enhancement Scope Definition

### Enhancement Type

- ✓ New Feature Addition
- ✓ Integration with New Systems

### Enhancement Description

Intégration automatique quotidienne avec plusieurs banques (BoursoBank, Hello Bank, Trade Republic) pour récupérer automatiquement les transactions et données de comptes sans intervention manuelle. L'objectif est de remplacer les imports CSV/PDF manuels par un système automatisé fiable et maintenable.

### Impact Assessment

- ✓ Significant Impact (substantial existing code changes)
  - Nouveau système de connecteurs bancaires
  - Refactoring de l'import existant
  - Nouvelle infrastructure de tâches Celery
  - Gestion sécurisée des credentials bancaires
  - Système de monitoring et alertes

## Goals and Background Context

### Goals

- Automatiser la récupération quotidienne des données bancaires sans intervention manuelle
- Centraliser la gestion des credentials bancaires de manière sécurisée
- Réduire le temps passé sur l'import manuel de données
- Améliorer la fiabilité et la maintenabilité du système d'import
- Permettre l'ajout facile de nouveaux providers bancaires
- Assurer la continuité du service même en cas d'échec temporaire d'un provider

### Background Context

Actuellement, l'utilisateur doit importer manuellement les données depuis chaque banque via CSV ou PDF. Le système dispose déjà d'un scraper Trade Republic fonctionnel avec authentification 2FA, mais il nécessite une intervention manuelle à chaque import. L'exploration de POWENS n'a pas été concluante, nécessitant une solution plus solide et maîtrisable.

Cette amélioration permettra de transformer le dashboard en un véritable système de suivi financier automatique, similaire aux applications de gestion financière modernes, tout en conservant le contrôle sur les données et l'infrastructure.

## Change Log

| Change | Date | Version | Description | Author |
| ------ | ---- | ------- | ----------- | ------ |
| Initial PRD | 2025-01-XX | 1.0 | Création du PRD pour intégration bancaire automatique | PM |
