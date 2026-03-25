# Requirements

## Functional Requirements

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

## Non-Functional Requirements

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

## Compatibility Requirements

**CR1**: L'API existante d'import CSV/PDF manuel doit rester fonctionnelle sans modification pour permettre une transition progressive.

**CR2**: Le modèle de données `Account` et `Transaction` doit rester compatible avec les données existantes (pas de migration destructive).

**CR3**: Les calculs du dashboard existants doivent continuer à fonctionner avec les nouvelles données synchronisées automatiquement.

**CR4**: Le système de scraping Trade Republic existant doit être intégré dans la nouvelle architecture de connecteurs sans casser la fonctionnalité actuelle.

**CR5**: L'interface utilisateur existante doit être étendue plutôt que remplacée pour maintenir la familiarité.
