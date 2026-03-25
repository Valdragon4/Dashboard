# Epic 1: Intégration Bancaire Automatique

**Epic Goal**: Permettre la synchronisation automatique quotidienne des données bancaires depuis BoursoBank, Hello Bank et Trade Republic sans intervention manuelle, tout en maintenant la compatibilité avec les imports manuels existants.

**Integration Requirements**: 
- Architecture modulaire de connecteurs bancaires réutilisable
- Infrastructure Celery pour synchronisation asynchrone
- Stockage sécurisé des credentials bancaires
- Interface utilisateur pour gestion des connexions
- Système de monitoring et logging des synchronisations
- Compatibilité avec l'import CSV/PDF manuel existant

## Story 1.1: Architecture de Base des Connecteurs Bancaires

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

## Story 1.2: Modèle de Données pour Connexions Bancaires

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

## Story 1.3: Connecteur Trade Republic (Refactorisation)

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

## Story 1.4: Connecteur BoursoBank

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

## Story 1.5: Connecteur Hello Bank

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

## Story 1.6: Service de Synchronisation Centralisé

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

## Story 1.7: Tâches Celery pour Synchronisation Automatique

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

## Story 1.8: Interface de Gestion des Connexions Bancaires

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

## Story 1.9: Extension de la Page de Gestion des Comptes

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

## Story 1.10: Monitoring et Logging des Synchronisations

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

## Story 1.11: Tests de Non-Régression et Documentation

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
