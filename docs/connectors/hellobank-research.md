# Recherche des Options Techniques pour le Connecteur Hello Bank

## Date de Recherche

2025-01-XX

## Objectif

Évaluer les différentes options techniques disponibles pour créer un connecteur Hello Bank permettant la synchronisation automatique des transactions et soldes, en remplacement de l'import CSV manuel.

## Options Évaluées

### Option 1: API PSD2/DSP2 Officielle

**Description** : Utilisation de l'API officielle PSD2/DSP2 de BNP Paribas Fortis (maison mère de Hello Bank) via le portail développeur.

**Ressources identifiées** :
- Portail développeur BNP Paribas Fortis : https://group.bnpparibas/en/psd2-apis
- Documentation technique disponible
- Environnement de test (Sandbox) disponible
- Conformité aux réglementations européennes (PSD2/DSP2)

**Avantages** :
- ✅ API officielle et stable
- ✅ Conforme aux réglementations européennes (PSD2/DSP2)
- ✅ Documentation complète disponible
- ✅ Environnement de test pour développement
- ✅ Support officiel disponible

**Inconvénients** :
- ❌ Nécessite une certification en tant que TPP (Third Party Provider)
- ❌ Nécessite un certificat eIDAS valide
- ❌ Processus d'accès complexe (création de compte, validation, etc.)
- ❌ Probablement destiné aux prestataires de services tiers professionnels
- ❌ Peut nécessiter des coûts ou des accords commerciaux
- ❌ Complexité administrative importante pour un usage personnel/privé

**Évaluation** : ⚠️ **Non recommandé pour usage personnel**

**Justification** : Bien que l'API officielle soit la solution la plus stable et conforme, elle nécessite une certification TPP et un certificat eIDAS qui sont complexes à obtenir pour un usage personnel. Cette option est plus adaptée pour des applications commerciales ou des services tiers professionnels.

### Option 2: Scraping HTTP Direct (API Non-Officielle)

**Description** : Analyse des requêtes réseau de l'interface web Hello Bank et utilisation directe des endpoints HTTP.

**Méthodologie d'analyse** :
- Analyser les requêtes réseau lors de la connexion à l'interface web Hello Bank
- Identifier les endpoints utilisés pour l'authentification et la récupération des données
- Tester l'authentification avec login/password
- Vérifier la présence de 2FA et son fonctionnement

**Avantages** :
- ✅ Pas de certification requise
- ✅ Accès direct aux données
- ✅ Performances meilleures que Selenium (pas de navigateur)
- ✅ Moins de consommation de ressources

**Inconvénients** :
- ❌ API non-officielle, peut changer sans préavis
- ❌ Nécessite une analyse approfondie des requêtes réseau
- ❌ Peut être fragile face aux changements d'interface
- ❌ Risque de blocage si détecté comme bot
- ❌ Gestion complexe des tokens de session et cookies
- ❌ 2FA peut être difficile à gérer

**Évaluation** : ⚠️ **Risqué mais possible comme approche secondaire**

**Justification** : Cette approche peut fonctionner mais est fragile et nécessite une maintenance constante. Elle peut être utilisée comme fallback si Selenium échoue, mais n'est pas recommandée comme approche principale.

### Option 3: Selenium/Playwright (Scraping Navigateur)

**Description** : Automatisation d'un navigateur pour simuler une connexion utilisateur et récupérer les données.

**Avantages** :
- ✅ Simule un utilisateur réel, moins de risque de détection
- ✅ Gère automatiquement les cookies et sessions
- ✅ Peut gérer l'authentification 2FA via interface utilisateur
- ✅ Plus stable face aux changements mineurs d'interface
- ✅ Pas de certification requise
- ✅ Approche éprouvée pour le scraping bancaire
- ✅ Playwright déjà ajouté pour BoursoBank (Story 1.4), réutilisation possible

**Inconvénients** :
- ❌ Plus lent que les appels HTTP directs
- ❌ Consommation de ressources plus importante (navigateur)
- ❌ Nécessite des dépendances supplémentaires (Selenium/Playwright) - déjà disponible
- ❌ Peut être fragile face aux changements majeurs d'interface
- ❌ Nécessite une gestion du navigateur (headless, etc.)

**Évaluation** : ✅ **Recommandé comme approche principale**

**Justification** : Cette approche offre le meilleur compromis entre stabilité, facilité d'implémentation et maintenabilité pour un usage personnel. Elle simule un utilisateur réel, ce qui réduit les risques de blocage, et peut gérer l'authentification 2FA de manière naturelle. De plus, Playwright est déjà disponible dans le projet pour BoursoBank, ce qui permet une réutilisation des dépendances.

## Recommandation Technique

### Approche Choisie : Playwright (Scraping Navigateur)

**Justification** :
1. **Stabilité** : Simule un utilisateur réel, moins de risque de détection et de blocage
2. **Facilité d'implémentation** : Gestion automatique des cookies, sessions, et authentification 2FA
3. **Maintenabilité** : Plus résistant aux changements mineurs d'interface
4. **Pas de certification requise** : Adapté pour un usage personnel
5. **Approche éprouvée** : Utilisée avec succès pour BoursoBank (Story 1.4)
6. **Réutilisation** : Playwright déjà disponible dans le projet, pas besoin d'ajouter de nouvelles dépendances

### Approche de Fallback : Scraping HTTP Direct

Si l'approche Playwright échoue après plusieurs tentatives, essayer le scraping HTTP direct comme fallback. Cette approche sera implémentée de manière basique et pourra être améliorée si nécessaire.

## Plan d'Implémentation

### Phase 1: Infrastructure de Base
1. Réutiliser Playwright déjà disponible dans le projet
2. Créer la classe `HelloBankConnector` héritant de `BaseBankConnector`
3. Implémenter la structure de base avec gestion du navigateur (similaire à BoursoBank)

### Phase 2: Authentification
1. Implémenter `authenticate()` avec Playwright
2. Gérer le login/password
3. Gérer l'authentification 2FA si nécessaire (SMS, TOTP)
4. Stocker les cookies/session pour les requêtes suivantes

### Phase 3: Récupération des Données
1. Implémenter `sync_transactions()` :
   - Naviguer vers la page des transactions
   - Scraper les transactions depuis l'interface
   - Transformer au format standard (Date;Type;Libellé court;Libellé détaillé;Montant)
   - Filtrer par date `since`
2. Implémenter `get_balance()` :
   - Naviguer vers la page du compte
   - Scraper le solde disponible

### Phase 4: Gestion des Erreurs et Retry
1. Implémenter retry avec backoff exponentiel
2. Gérer les timeouts
3. Implémenter fallback vers scraping HTTP si Playwright échoue

### Phase 5: Tests et Documentation
1. Créer les tests unitaires avec mocks
2. Documenter le connecteur
3. Documenter les credentials requis et le processus d'authentification

## Dépendances Nécessaires

- `playwright>=1.40.0` - **Déjà disponible** dans le projet (ajouté pour BoursoBank)
- Pas besoin d'ajouter de nouvelles dépendances

## Notes Techniques

### Réutilisation de Playwright

Playwright a été ajouté au projet pour BoursoBank (Story 1.4). Le connecteur Hello Bank peut réutiliser cette dépendance existante, ce qui réduit la complexité et les dépendances du projet.

### Gestion du Navigateur

- Utiliser le mode headless pour les environnements serveur
- Configurer les timeouts appropriés
- Gérer les popups et modales
- Gérer les cookies et sessions

### Authentification 2FA

- Si 2FA par SMS : Attendre la saisie manuelle du code (pour l'instant)
- Si 2FA par TOTP : Utiliser une bibliothèque comme `pyotp` si possible
- Stocker les informations de session pour éviter les reconnexions fréquentes

### Format des Données Hello Bank

Le format CSV Hello Bank est spécifique :
- Format : `Date;Type;Libellé court;Libellé détaillé;Montant`
- Délimiteur : point-virgule (`;`)
- Description : Combinaison de libellé court + libellé détaillé

Le connecteur doit transformer les données scrappées pour correspondre à ce format.

## Risques et Mitigation

**Risque 1** : Changements d'interface web Hello Bank
- **Mitigation** : Utiliser des sélecteurs CSS robustes, gérer les erreurs gracieusement, logger les changements détectés

**Risque 2** : Détection de bot
- **Mitigation** : Simuler un utilisateur réel (User-Agent, délais entre actions, etc.)

**Risque 3** : Performance et ressources
- **Mitigation** : Utiliser le mode headless, fermer proprement le navigateur après utilisation, limiter le nombre de connexions simultanées

**Risque 4** : Authentification 2FA complexe
- **Mitigation** : Implémenter un support pour 2FA manuel dans un premier temps, améliorer avec TOTP si possible

## Conclusion

L'approche **Playwright** est recommandée comme solution principale pour le connecteur Hello Bank. Elle offre le meilleur compromis entre stabilité, facilité d'implémentation et maintenabilité pour un usage personnel. Le scraping HTTP direct peut être utilisé comme fallback si nécessaire. La réutilisation de Playwright déjà disponible dans le projet est un avantage supplémentaire.

## Références

- Portail développeur BNP Paribas Fortis : https://group.bnpparibas/en/psd2-apis
- Documentation Playwright : https://playwright.dev/python/
- Documentation Selenium : https://www.selenium.dev/documentation/
