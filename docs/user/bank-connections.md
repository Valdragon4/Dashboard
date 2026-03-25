# Guide Utilisateur : Connexions Bancaires et Synchronisation Automatique

Ce guide vous explique comment configurer et utiliser la synchronisation automatique de vos comptes bancaires.

## Introduction

La synchronisation bancaire automatique vous permet de récupérer automatiquement vos transactions et soldes depuis vos comptes bancaires sans avoir à importer manuellement des fichiers CSV ou PDF. Le système se connecte directement à vos banques et synchronise vos données quotidiennement.

### Avantages

- ✅ **Automatique** : Vos données sont synchronisées quotidiennement sans intervention
- ✅ **Temps gagné** : Plus besoin d'importer manuellement des fichiers CSV/PDF
- ✅ **Données à jour** : Vos transactions sont toujours à jour
- ✅ **Sécurisé** : Vos credentials sont chiffrés et stockés de manière sécurisée
- ✅ **Traçable** : Historique complet des synchronisations avec logs détaillés

### Providers Supportés

- **Trade Republic** : Courtier en ligne avec support complet (authentification 2FA)
- **BoursoBank** : Banque en ligne française
- **Hello Bank** : Banque en ligne française

## Créer une Nouvelle Connexion Bancaire

### Étapes

1. **Accédez à la page de gestion des connexions**
   - Cliquez sur "Connexions Bancaires" dans le menu de navigation
   - Ou accédez directement à `/bank-connections/`

2. **Cliquez sur "Nouvelle connexion"**
   - Un formulaire s'affiche avec les champs suivants :
     - **Nom du compte** : Nom que vous souhaitez donner à cette connexion (ex: "Compte Courant BoursoBank")
     - **Provider** : Sélectionnez votre banque dans la liste déroulante
     - **Compte associé** (optionnel) : Liez cette connexion à un compte existant

3. **Remplissez les credentials**
   - Les champs requis varient selon le provider (voir sections ci-dessous)
   - Vos credentials sont chiffrés avant stockage dans la base de données

4. **Configurez la synchronisation**
   - **Synchronisation automatique** : Activez pour synchroniser quotidiennement
   - Par défaut, la synchronisation est activée

5. **Sauvegardez**
   - Cliquez sur "Créer la connexion"
   - Le système tentera une première connexion pour valider vos credentials

## Configurer Trade Republic

### Credentials Requis

- **Numéro de téléphone** : Votre numéro de téléphone associé à votre compte Trade Republic (format international, ex: +33123456789)
- **PIN** : Votre code PIN Trade Republic (4 chiffres)
- **Code 2FA** : Code reçu par SMS lors de la première connexion

### Authentification 2FA

Trade Republic utilise une authentification à deux facteurs (2FA) pour sécuriser les connexions.

1. **Première connexion** :
   - Entrez votre numéro de téléphone et votre PIN
   - Cliquez sur "Se connecter"
   - Un code 2FA sera envoyé par SMS à votre téléphone
   - Entrez ce code dans le champ "Code 2FA"
   - Cliquez sur "Vérifier et connecter"

2. **Connexions suivantes** :
   - Si votre session est toujours valide, la synchronisation se fera automatiquement
   - Si votre session a expiré, vous devrez refaire l'authentification 2FA

### Dépannage

**Problème : "Code 2FA invalide"**
- Vérifiez que vous avez entré le code reçu par SMS
- Le code expire après quelques minutes, demandez-en un nouveau
- Vérifiez que votre numéro de téléphone est correct

**Problème : "Authentification échouée"**
- Vérifiez que votre numéro de téléphone est au format international (+33...)
- Vérifiez que votre PIN est correct
- Assurez-vous que votre compte Trade Republic est actif

**Problème : "403 Forbidden" / pare-feu (WAF)**
- Ce message apparaît surtout lorsque le dashboard exécute les requêtes depuis un **VPS / cloud** : Trade Republic peut bloquer l’accès à l’API via un pare-feu (WAF), même si l’interface web `app.traderepublic.com` est accessible dans votre navigateur.
- Le connecteur tente une approche “navigateurs” (Playwright + récupération d’un token WAF) puis fait un fallback, mais le blocage peut persister.
- Solutions possibles :
  - Importer via **CSV** ou **PDF** depuis l’interface (menu Trade Republic).
  - Exécuter le dashboard sur un réseau résidentiel (ou utiliser un **tunnel** / un proxy pour que la sortie Internet passe par votre machine).
  - Si vous diagnostiquez : vous pouvez désactiver l’étape “Playwright pour l’init” avec `TRADEPUBLIC_USE_PLAYWRIGHT_INITIATE=0` (variable d’environnement).

**Problème : "Session expirée"**
- Les sessions Trade Republic expirent après un certain temps
- Vous devrez refaire l'authentification 2FA
- Consultez les logs de synchronisation pour voir la date d'expiration

## Configurer BoursoBank

### Credentials Requis

- **Nom d'utilisateur** : Votre identifiant BoursoBank
- **Mot de passe** : Votre mot de passe BoursoBank
- **Code 2FA** (si requis) : Code reçu par SMS ou application d'authentification

### Authentification

BoursoBank peut requérir une authentification 2FA selon votre configuration de sécurité.

1. **Entrez vos credentials**
   - Nom d'utilisateur et mot de passe
   - Si 2FA est requis, entrez le code reçu

2. **Le système se connecte automatiquement**
   - Le système utilise Playwright pour automatiser la connexion
   - La première connexion peut prendre quelques secondes

### Dépannage

**Problème : "Authentification échouée"**
- Vérifiez que vos credentials sont corrects
- Assurez-vous que votre compte BoursoBank est actif
- Vérifiez que vous n'avez pas de blocage de sécurité actif

**Problème : "Timeout de connexion"**
- Vérifiez votre connexion internet
- Réessayez la synchronisation manuellement
- Consultez les logs pour plus de détails

## Configurer Hello Bank

### Credentials Requis

- **Nom d'utilisateur** : Votre identifiant Hello Bank
- **Mot de passe** : Votre mot de passe Hello Bank

### Authentification

Hello Bank utilise une authentification standard (login/mot de passe).

1. **Entrez vos credentials**
   - Nom d'utilisateur et mot de passe

2. **Le système se connecte automatiquement**
   - Le système utilise Playwright pour automatiser la connexion

### Dépannage

**Problème : "Authentification échouée"**
- Vérifiez que vos credentials sont corrects
- Assurez-vous que votre compte Hello Bank est actif

**Problème : "Timeout de connexion"**
- Vérifiez votre connexion internet
- Réessayez la synchronisation manuellement

## Gérer la Synchronisation

### Activer/Désactiver la Synchronisation Automatique

1. **Depuis la liste des connexions** :
   - Accédez à `/bank-connections/`
   - Pour chaque connexion, vous pouvez voir si la synchronisation automatique est activée
   - Cliquez sur "Modifier" pour changer ce paramètre

2. **Dans le formulaire de modification** :
   - Cochez/décochez "Synchronisation automatique activée"
   - Sauvegardez les modifications

**Note** : La synchronisation automatique s'exécute quotidiennement à l'heure configurée (par défaut 2h du matin). Vous pouvez changer cette heure via la variable d'environnement `BANK_SYNC_SCHEDULE_HOUR`.

### Synchronisation Manuelle

Vous pouvez forcer une synchronisation manuelle à tout moment :

1. **Depuis la liste des connexions** :
   - Cliquez sur le bouton "Sync" à côté de la connexion
   - La synchronisation démarre immédiatement

2. **Depuis la page de gestion des comptes** :
   - Accédez à `/accounts/`
   - Pour les comptes avec connexion bancaire, cliquez sur "Sync"
   - La synchronisation démarre immédiatement

3. **Pendant la synchronisation** :
   - Un indicateur visuel vous montre que la synchronisation est en cours
   - Vous recevrez une notification une fois terminée

### Consulter les Logs de Synchronisation

Pour consulter l'historique des synchronisations :

1. **Accédez aux logs** :
   - Depuis la liste des connexions, cliquez sur "Voir les logs" pour une connexion spécifique
   - Ou accédez directement à `/bank-connections/logs/` pour voir tous les logs

2. **Informations affichées** :
   - Date et heure de début/fin
   - Durée de synchronisation
   - Nombre de transactions synchronisées
   - Statut (Succès/Erreur/En cours)
   - Message d'erreur si échec

3. **Filtres disponibles** :
   - Par connexion bancaire
   - Par statut (Succès/Erreur/En cours)
   - Par type (Manuelle/Automatique)
   - Par date (période)

4. **Export CSV** :
   - Cliquez sur "Exporter en CSV" pour télécharger les logs
   - L'export respecte les filtres appliqués

## FAQ

### Questions Fréquentes

**Q : Mes credentials sont-ils sécurisés ?**
R : Oui, vos credentials sont chiffrés avec AES-256 avant stockage dans la base de données. La clé de chiffrement est stockée dans les variables d'environnement et n'est jamais exposée dans le code.

**Q : Que se passe-t-il si ma session expire ?**
R : Si votre session expire, vous devrez refaire l'authentification 2FA (pour Trade Republic). Le système vous notifiera si une authentification est requise.

**Q : Combien de temps les logs sont-ils conservés ?**
R : Par défaut, les logs sont conservés pendant 30 jours. Vous pouvez changer cette durée via la variable d'environnement `SYNC_LOG_RETENTION_DAYS`.

**Q : Puis-je utiliser à la fois l'import manuel et la synchronisation automatique ?**
R : Oui, les deux méthodes sont compatibles. Le système détecte automatiquement les doublons entre import manuel et synchronisation automatique.

**Q : Que faire si une synchronisation échoue ?**
R : Consultez les logs de synchronisation pour voir le message d'erreur détaillé. Les erreurs courantes sont :
- Credentials invalides → Vérifiez vos identifiants
- Session expirée → Refaites l'authentification 2FA
- Problème de connexion → Vérifiez votre internet et réessayez

**Q : La synchronisation automatique fonctionne-t-elle si le serveur est éteint ?**
R : Non, le serveur doit être allumé pour que la synchronisation automatique fonctionne. Les synchronisations manquées ne seront pas récupérées automatiquement, mais vous pouvez toujours synchroniser manuellement.

**Q : Puis-je synchroniser plusieurs comptes de la même banque ?**
R : Oui, vous pouvez créer plusieurs connexions bancaires pour différents comptes de la même banque. Chaque connexion est indépendante.

### Problèmes Courants et Solutions

**Problème : "Aucune transaction synchronisée"**
- Vérifiez que votre compte a bien des transactions récentes
- Vérifiez la date de dernière synchronisation dans les logs
- Essayez une synchronisation manuelle

**Problème : "Doublons détectés"**
- C'est normal si vous importez manuellement ET synchronisez automatiquement
- Le système détecte automatiquement les doublons et ne crée qu'une seule transaction
- Si vous voyez des doublons, vérifiez que les transactions ont bien les mêmes identifiants

**Problème : "Synchronisation très lente"**
- Certaines banques peuvent prendre du temps à répondre
- Vérifiez les logs pour voir la durée de synchronisation
- Si c'est systématiquement lent, contactez le support de votre banque

**Problème : "Alertes d'échecs répétés"**
- Si vous voyez une alerte sur la page des logs, cela signifie que plusieurs synchronisations ont échoué consécutivement
- Vérifiez vos credentials
- Vérifiez que votre compte bancaire est toujours actif
- Consultez les messages d'erreur détaillés dans les logs

## Support

Si vous rencontrez des problèmes non résolus par ce guide :

1. Consultez les logs de synchronisation pour les détails d'erreur
2. Vérifiez que votre configuration est correcte
3. Essayez une synchronisation manuelle pour isoler le problème
4. Contactez le support technique avec les détails de l'erreur
