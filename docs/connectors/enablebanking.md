# Connecteur Enable Banking (DSP2)

Accès officiel en **lecture seule** aux comptes de paiement, via l'agrégateur
Enable Banking en mode « restreint » (gratuit, comptes de l'utilisateur seul).
Utilisé pour **Hello Bank**, et prévu pour Revolut (perso et pro) et le compte
espèces Trade Republic.

## Pourquoi pas un scraper

L'espace client Hello Bank répond `403 Access Denied` (anti-robot) à tout
navigateur automatisé, quelle que soit l'adresse IP (VPS, proxy résidentiel) —
constaté le 2026-11-02. L'ancien connecteur Playwright n'a jamais fonctionné et
a été retiré.

## Configuration

| Variable | Rôle |
|----------|------|
| `ENABLEBANKING_APP_ID` | identifiant de l'application (panneau Enable Banking) |
| `ENABLEBANKING_KEY_PATH` | clé privée RS256, défaut `/secrets/enablebanking/private.pem` |
| `ENABLEBANKING_REDIRECT_URL` | doit figurer dans les « Redirect URLs » de l'application |

La clé vit dans `./secrets/enablebanking/` (ignoré par git), montée en lecture
seule dans `web`, `worker` et `beat`. Les comptes accessibles doivent être
**reliés dans le panneau** Enable Banking (« Link accounts ») : c'est la règle du
mode restreint.

## Parcours

1. *Connexions bancaires → Connecter une banque (DSP2)* : choix du pays, du type
   (particulier / professionnel) et de la banque ;
2. validation dans l'application de la banque, retour sur
   `/finance/enablebanking/callback/` ;
3. rattachement de chaque compte bancaire à un compte du tableau de bord.

Le consentement dure 90 à 180 jours selon la banque. La date d'expiration est
affichée sur la carte de la connexion (en orange à moins de 14 jours) ;
« Renouveler l'accès » refait le parcours **en gardant les rattachements** : un
compte est reconnu par son `identification_hash`, stable entre sessions, alors
que son `uid` change à chaque session.

## Données

- **Opérations** : seules les comptabilisées (`BOOK`) sont importées. Les banques
  refusent en général plus de 90 jours d'historique (`422`) ; une synchro
  automatique ne relit que depuis la dernière synchro moins 5 jours.
- **Identifiant** : celui de la banque s'il existe ; sinon (Hello Bank)
  `sha256(banque|date|montant|libellé)#rang`, le rang distinguant deux
  opérations identiques le même jour.
- **Solde** : solde comptable ; un solde de clôture (`CLBD`) est complété des
  opérations comptabilisées après sa date. Le solde initial du compte est
  recalé à chaque synchro (solde réel − somme des opérations importées).
- **Livrets** : Livret A, LEP… ne sont pas des comptes de paiement ; la DSP2
  n'oblige pas la banque à les exposer et Hello Bank ne le fait pas. Ils restent
  en saisie manuelle.
- **Quotas** : la DSP2 limite les accès sans l'utilisateur à ~4 par jour et par
  compte ; la synchro de nuit en consomme un.

## Session ouverte hors interface

```bash
python manage.py enablebanking_import_session /secrets/enablebanking/session.json \
    --aspsp "Hello Bank" --owner 1 --account 26
```
