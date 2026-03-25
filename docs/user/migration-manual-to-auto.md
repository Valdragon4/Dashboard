# Guide de Migration : Import Manuel vers Synchronisation Automatique

Ce guide vous explique comment migrer vos comptes existants de l'import manuel vers la synchronisation automatique.

## Introduction

Si vous utilisez actuellement l'import manuel de fichiers CSV/PDF pour vos comptes bancaires, vous pouvez migrer vers la synchronisation automatique pour gagner du temps et avoir vos données toujours à jour.

### Pourquoi Migrer ?

- ✅ **Gain de temps** : Plus besoin d'exporter et d'importer manuellement des fichiers
- ✅ **Données à jour** : Synchronisation quotidienne automatique
- ✅ **Moins d'erreurs** : Pas de risque d'oublier d'importer des transactions
- ✅ **Historique complet** : Traçabilité de toutes les synchronisations

### Compatibilité

Les deux méthodes (import manuel et synchronisation automatique) sont **100% compatibles** :
- Vous pouvez continuer à utiliser l'import manuel même après avoir créé une connexion bancaire
- Le système détecte automatiquement les doublons entre les deux méthodes
- Vos données existantes ne seront pas affectées

## Prérequis

Avant de commencer la migration, vérifiez que :

1. ✅ **Votre compte existe déjà** dans le système (créé via import manuel)
2. ✅ **Vos credentials bancaires sont disponibles** (identifiant, mot de passe, etc.)
3. ✅ **Votre banque est supportée** (Trade Republic, BoursoBank, Hello Bank)
4. ✅ **Vos données sont compatibles** (les transactions importées manuellement sont correctes)

## Étapes de Migration

### Étape 1 : Créer une BankConnection pour le Compte Existant

1. **Accédez à la page de gestion des connexions**
   - Cliquez sur "Connexions Bancaires" dans le menu
   - Ou accédez à `/bank-connections/`

2. **Cliquez sur "Nouvelle connexion"**

3. **Remplissez le formulaire** :
   - **Nom du compte** : Utilisez le même nom que votre compte existant (ex: "Compte Courant BoursoBank")
   - **Provider** : Sélectionnez votre banque
   - **Compte associé** : **IMPORTANT** - Sélectionnez votre compte existant dans la liste déroulante
     - Cela lie la connexion bancaire à votre compte existant
     - Toutes les transactions synchronisées seront ajoutées à ce compte

4. **Remplissez les credentials** :
   - Suivez les instructions selon votre provider (voir guide utilisateur)
   - Pour Trade Republic : numéro de téléphone, PIN, code 2FA
   - Pour BoursoBank/Hello Bank : identifiant, mot de passe

5. **Sauvegardez**
   - Cliquez sur "Créer la connexion"
   - Le système tentera une première connexion pour valider vos credentials

### Étape 2 : Configurer les Credentials

Selon votre provider, configurez les credentials appropriés :

#### Trade Republic

- **Numéro de téléphone** : Format international (ex: +33123456789)
- **PIN** : Votre code PIN Trade Republic (4 chiffres)
- **Code 2FA** : Code reçu par SMS lors de la première connexion

#### BoursoBank

- **Nom d'utilisateur** : Votre identifiant BoursoBank
- **Mot de passe** : Votre mot de passe BoursoBank
- **Code 2FA** (si requis) : Code reçu par SMS ou application

#### Hello Bank

- **Nom d'utilisateur** : Votre identifiant Hello Bank
- **Mot de passe** : Votre mot de passe Hello Bank

### Étape 3 : Tester la Synchronisation Manuelle

Avant d'activer la synchronisation automatique, testez manuellement :

1. **Depuis la liste des connexions** :
   - Cliquez sur le bouton "Sync" à côté de votre nouvelle connexion
   - Ou accédez à `/accounts/` et cliquez sur "Sync" pour le compte associé

2. **Vérifiez les résultats** :
   - Consultez les logs de synchronisation pour voir si la synchronisation a réussi
   - Vérifiez que les transactions synchronisées apparaissent dans votre compte
   - Vérifiez qu'il n'y a pas de doublons avec les transactions importées manuellement

3. **Si des erreurs apparaissent** :
   - Consultez le message d'erreur dans les logs
   - Vérifiez vos credentials
   - Réessayez la synchronisation

### Étape 4 : Activer la Synchronisation Automatique

Une fois que la synchronisation manuelle fonctionne :

1. **Activez la synchronisation automatique** :
   - Depuis la liste des connexions, cliquez sur "Modifier"
   - Cochez "Synchronisation automatique activée"
   - Sauvegardez

2. **Vérifiez la configuration** :
   - La synchronisation automatique s'exécute quotidiennement à l'heure configurée (par défaut 2h du matin)
   - Vous pouvez changer cette heure via la variable d'environnement `BANK_SYNC_SCHEDULE_HOUR`

### Étape 5 : Vérifier les Données Synchronisées

Après quelques jours de synchronisation automatique :

1. **Consultez les logs** :
   - Accédez à `/bank-connections/logs/`
   - Vérifiez que les synchronisations quotidiennes réussissent
   - Vérifiez le nombre de transactions synchronisées

2. **Vérifiez les transactions** :
   - Accédez à votre compte dans `/accounts/`
   - Vérifiez que les nouvelles transactions apparaissent correctement
   - Vérifiez qu'il n'y a pas de doublons

3. **Comparez avec l'import manuel** :
   - Si vous continuez à importer manuellement, vérifiez que les données correspondent
   - Le système devrait détecter automatiquement les doublons

## Gestion des Doublons

### Comment ça Fonctionne ?

Le système détecte automatiquement les doublons entre import manuel et synchronisation automatique en utilisant :

1. **Transaction ID** : Si disponible (Trade Republic utilise des IDs uniques)
2. **Date + Montant + Description** : Si pas d'ID unique
3. **Numéro de ligne CSV** : Pour les imports CSV

### Éviter les Doublons lors de la Migration

Pour éviter les doublons lors de la migration :

1. **Ne synchronisez pas la même période** :
   - Si vous avez déjà importé les transactions du mois en cours manuellement
   - Configurez la synchronisation pour ne récupérer que les nouvelles transactions
   - Le système utilise automatiquement `last_sync_at` pour ne synchroniser que les nouvelles transactions

2. **Vérifiez la date de dernière synchronisation** :
   - Le système utilise `last_sync_at` pour déterminer depuis quand synchroniser
   - Si vous avez importé manuellement jusqu'au 15 janvier, la synchronisation ne récupérera que les transactions après le 15 janvier

3. **Utilisez les identifiants uniques** :
   - Si votre banque fournit des IDs uniques (comme Trade Republic), le système les utilise pour la déduplication
   - Les transactions avec le même ID ne seront pas créées en double

### Nettoyer les Doublons Existants

Si vous avez déjà des doublons dans votre base de données :

1. **Identifiez les doublons** :
   - Recherchez les transactions avec la même date, montant et description
   - Vérifiez qu'elles proviennent de sources différentes (import manuel vs synchronisation)

2. **Supprimez manuellement** :
   - Depuis l'interface de gestion des transactions
   - Supprimez les doublons en gardant une seule version de chaque transaction

3. **Prévenez les futurs doublons** :
   - Une fois la migration complète, arrêtez d'importer manuellement pour les comptes avec synchronisation automatique
   - Ou utilisez l'import manuel uniquement pour les périodes historiques

## Recommandations

### Transition Progressive

Nous recommandons une transition progressive :

1. **Phase 1 (Semaine 1-2)** : Créez la connexion bancaire et testez la synchronisation manuelle
   - Vérifiez que les données sont correctes
   - Vérifiez qu'il n'y a pas de doublons

2. **Phase 2 (Semaine 3-4)** : Activez la synchronisation automatique mais continuez l'import manuel
   - Comparez les données des deux sources
   - Vérifiez que les doublons sont bien détectés

3. **Phase 3 (Mois 2+)** : Arrêtez l'import manuel une fois la migration validée
   - Utilisez uniquement la synchronisation automatique
   - Gardez l'import manuel uniquement pour les périodes historiques si nécessaire

### Vérification Régulière des Logs

Pendant la période de transition, vérifiez régulièrement les logs :

1. **Quotidiennement** (première semaine) :
   - Vérifiez que les synchronisations réussissent
   - Vérifiez le nombre de transactions synchronisées
   - Vérifiez qu'il n'y a pas d'erreurs

2. **Hebdomadairement** (premier mois) :
   - Comparez les données synchronisées avec vos relevés bancaires
   - Vérifiez qu'il n'y a pas de transactions manquantes
   - Vérifiez qu'il n'y a pas de doublons

3. **Mensuellement** (après stabilisation) :
   - Vérifiez les statistiques de synchronisation
   - Vérifiez les alertes d'échecs répétés si présentes

### Désactivation de l'Import Manuel

Une fois la migration validée et la synchronisation automatique stable :

1. **Arrêtez l'import manuel** pour les comptes avec synchronisation automatique
   - Vous pouvez toujours utiliser l'import manuel pour les périodes historiques
   - Ou pour les comptes sans connexion bancaire

2. **Gardez l'import manuel comme solution de secours** :
   - Si la synchronisation automatique échoue temporairement
   - Vous pouvez toujours importer manuellement pour combler les lacunes

## Dépannage

### Problème : "Doublons détectés après migration"

**Solution** :
- Vérifiez que les transactions ont bien les mêmes identifiants (date, montant, description)
- Le système devrait détecter automatiquement les doublons
- Si des doublons persistent, supprimez-les manuellement

### Problème : "Transactions manquantes après migration"

**Solution** :
- Vérifiez la date de dernière synchronisation (`last_sync_at`)
- La synchronisation ne récupère que les transactions après cette date
- Si vous avez importé manuellement jusqu'au 15 janvier, la synchronisation ne récupérera que les transactions après le 15 janvier
- Pour récupérer les transactions manquantes, vous pouvez :
  - Importer manuellement les transactions manquantes
  - Ou modifier `last_sync_at` pour forcer une synchronisation complète (non recommandé)

### Problème : "Synchronisation échoue régulièrement"

**Solution** :
- Consultez les logs pour voir le message d'erreur détaillé
- Vérifiez vos credentials (peut-être que votre mot de passe a changé)
- Vérifiez que votre compte bancaire est toujours actif
- Pour Trade Republic, vérifiez que votre session n'a pas expiré (refaites l'authentification 2FA)

### Problème : "Je veux revenir à l'import manuel"

**Solution** :
- Vous pouvez désactiver la synchronisation automatique à tout moment
- Désactivez "Synchronisation automatique activée" dans les paramètres de la connexion
- Vous pouvez continuer à utiliser l'import manuel comme avant
- Vos données existantes ne seront pas affectées

## FAQ

**Q : Puis-je avoir les deux méthodes actives en même temps ?**
R : Oui, les deux méthodes sont compatibles. Le système détecte automatiquement les doublons.

**Q : Que se passe-t-il si je supprime la connexion bancaire ?**
R : Votre compte et vos transactions existantes ne seront pas supprimés. Seule la connexion bancaire sera supprimée, et vous pourrez continuer à utiliser l'import manuel.

**Q : Puis-je migrer plusieurs comptes en même temps ?**
R : Oui, vous pouvez créer plusieurs connexions bancaires pour différents comptes. Chaque migration est indépendante.

**Q : Combien de temps prend la migration ?**
R : La création de la connexion prend quelques minutes. La première synchronisation peut prendre quelques secondes à quelques minutes selon le nombre de transactions.

**Q : Mes données existantes seront-elles modifiées ?**
R : Non, vos données existantes ne seront pas modifiées. La synchronisation automatique ajoute seulement de nouvelles transactions.

## Support

Si vous rencontrez des problèmes lors de la migration :

1. Consultez les logs de synchronisation pour les détails d'erreur
2. Vérifiez que votre configuration est correcte
3. Essayez une synchronisation manuelle pour isoler le problème
4. Contactez le support technique avec les détails de l'erreur
