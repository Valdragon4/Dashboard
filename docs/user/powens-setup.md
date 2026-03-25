# Guide : Configuration Powens pour Synchronisation Bancaire

## Introduction

Powens (anciennement Budget Insight) est un service d'agrégation bancaire qui permet de se connecter à plus de 1800 banques en Europe via leur API. Cette solution est recommandée pour remplacer le scraping qui peut être fragile et nécessiter beaucoup de maintenance.

## Avantages de Powens

- ✅ **Fiabilité** : API officielle, moins de casse que le scraping
- ✅ **Maintenance réduite** : Géré par Powens, pas besoin de maintenir le code de scraping
- ✅ **Conformité PSD2** : Respect des réglementations européennes
- ✅ **Large couverture** : 1800+ banques supportées
- ✅ **Performance** : API REST rapide, pas besoin de navigateur headless

## Obtenir un Compte Powens

### Étape 1 : Créer un Compte Développeur

1. Allez sur [https://www.powens.com/](https://www.powens.com/)
2. Cliquez sur "Get Started" ou "Developer Portal"
3. Créez un compte développeur
4. Vérifiez votre email

### Étape 2 : Obtenir les Credentials API

1. Connectez-vous au [Dashboard Powens](https://dashboard.powens.com/)
2. Allez dans la section "API Keys" ou "Credentials"
3. Créez une nouvelle application/clé API
4. Notez :
   - **API Key** (Client ID)
   - **API Secret** (Client Secret)

### Étape 3 : Plan Tarifaire

Pour un projet personnel avec un budget de 5€/mois :

- **Sandbox/Test** : Généralement gratuit pour tester
- **Production** : Contactez Powens pour un plan développeur individuel
  - Email : support@powens.com
  - Mentionnez que c'est pour un projet personnel
  - Demandez s'il y a un plan "Developer" ou "Starter" à prix réduit

**Note** : Powens peut proposer des tarifs spéciaux pour les développeurs individuels ou projets open-source.

## Configuration dans l'Application

### Étape 1 : Ajouter les Variables d'Environnement

Ajoutez ces variables dans votre fichier `.env` :

```bash
# ============================================
# POWENS API CONFIGURATION
# ============================================
POWENS_API_KEY=your_api_key_here
POWENS_API_SECRET=your_api_secret_here
POWENS_BASE_URL=https://api.powens.com
# Pour le sandbox/test : https://api-sandbox.powens.com
```

### Étape 2 : Configurer le Connecteur

Le connecteur Powens est déjà intégré dans l'application. Il sera automatiquement disponible dans l'interface de gestion des connexions bancaires.

## Utilisation

### Créer une Connexion Bancaire via Powens

1. **Accédez à la page "Connexions Bancaires"** dans l'application
2. **Cliquez sur "Nouvelle Connexion"**
3. **Sélectionnez "Powens" comme provider**
4. **Sélectionnez votre banque** (ex: Boursorama, Hello Bank)
5. **Autorisez l'accès** : Vous serez redirigé vers Powens pour autoriser l'accès à votre compte
6. **Confirmez** : Une fois autorisé, la connexion sera créée automatiquement

### Synchronisation Automatique

Une fois la connexion créée, la synchronisation se fera automatiquement selon la planification configurée (par défaut : quotidienne à 2h du matin).

## Banques Supportées

Powens supporte plus de 1800 banques en Europe, incluant :

- **France** : Boursorama, Hello Bank, Crédit Agricole, BNP Paribas, Société Générale, etc.
- **Europe** : La plupart des banques européennes majeures

Pour vérifier si votre banque est supportée, consultez la [documentation Powens](https://docs.powens.com/) ou contactez leur support.

## Coûts

### Estimation pour un Utilisateur Unique

- **Sandbox/Test** : Gratuit (limité)
- **Production (Starter)** : ~5-20€/mois selon le volume
- **Production (Developer)** : Contactez Powens pour un tarif personnalisé

**Recommandation** : Commencez avec le sandbox pour tester, puis contactez Powens pour négocier un tarif adapté à votre usage personnel.

## Support et Documentation

- **Documentation API** : [https://docs.powens.com/](https://docs.powens.com/)
- **Support** : support@powens.com
- **Dashboard** : [https://dashboard.powens.com/](https://dashboard.powens.com/)

## Dépannage

### Erreur "Invalid API credentials"

- Vérifiez que `POWENS_API_KEY` et `POWENS_API_SECRET` sont correctement configurés dans `.env`
- Vérifiez que vous utilisez les bonnes credentials (sandbox vs production)

### Erreur "Rate limit exceeded"

- Vous avez atteint la limite de requêtes de votre plan
- Attendez quelques minutes ou contactez Powens pour augmenter votre quota

### La banque n'apparaît pas dans la liste

- Vérifiez sur [docs.powens.com](https://docs.powens.com/) si la banque est supportée
- Contactez le support Powens si nécessaire

## Migration depuis le Scraping

Si vous migrez depuis le scraping BoursoBank vers Powens :

1. **Créez une nouvelle connexion Powens** pour votre compte Boursorama
2. **Laissez l'ancienne connexion scraping** inactive (ou supprimez-la)
3. **Vérifiez que les transactions se synchronisent correctement**
4. **Une fois validé, supprimez l'ancienne connexion**

Les transactions existantes ne seront pas dupliquées grâce au système de détection de doublons.
