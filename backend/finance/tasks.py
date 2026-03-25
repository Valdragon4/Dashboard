"""
Tâches Celery pour la synchronisation automatique des comptes bancaires.

Ces tâches permettent de synchroniser automatiquement les comptes bancaires
via Celery Beat (synchronisation quotidienne) ou manuellement (synchronisation à la demande).
"""

import logging
from typing import Dict, Optional

from celery import shared_task

from finance.connectors.base import (
    ConnectionTimeoutError,
    RateLimitError,
    InvalidCredentialsError,
)
from finance.models import Account, SyncLog
from finance.services.sync_service import SyncService

logger = logging.getLogger(__name__)


@shared_task
def sync_all_bank_accounts() -> Dict:
    """
    Synchronise tous les comptes bancaires avec auto_sync_enabled=True.

    Cette tâche est appelée automatiquement par Celery Beat quotidiennement.
    Elle synchronise tous les comptes activés pour la synchronisation automatique.

    Returns:
        dict: Résultat de la synchronisation avec statistiques :
              {
                  "total": int,
                  "success": int,
                  "errors": int,
                  "errors_details": list[dict],
              }
    """
    accounts = Account.objects.filter(
        auto_sync_enabled=True, bank_connection__isnull=False
    ).select_related("bank_connection")

    results = {
        "total": accounts.count(),
        "success": 0,
        "errors": 0,
        "errors_details": [],
    }

    logger.info(
        f"Début de synchronisation automatique de {results['total']} comptes bancaires"
    )

    for account in accounts:
        try:
            logger.info(
                f"Synchronisation du compte {account.id} "
                f"(provider: {account.bank_connection.provider})"
            )

            result = SyncService.sync_account(account, sync_type=SyncLog.SyncType.AUTOMATIC)

            if result["success"]:
                results["success"] += 1
                logger.info(
                    f"Synchronisation réussie du compte {account.id}: "
                    f"{result['transactions_count']} transactions synchronisées"
                )
            else:
                results["errors"] += 1
                error_msg = result.get("error", "Unknown error")
                results["errors_details"].append(
                    {
                        "account_id": account.id,
                        "account_name": account.name,
                        "provider": account.bank_connection.provider,
                        "error": error_msg,
                    }
                )
                logger.error(
                    f"Erreur lors de la synchronisation du compte {account.id}: {error_msg}"
                )

        except Exception as e:
            results["errors"] += 1
            error_msg = str(e)
            results["errors_details"].append(
                {
                    "account_id": account.id,
                    "account_name": account.name,
                    "provider": account.bank_connection.provider if account.bank_connection else None,
                    "error": error_msg,
                }
            )
            logger.error(
                f"Exception lors de la synchronisation du compte {account.id}: {error_msg}",
                exc_info=True,
            )

    logger.info(
        f"Synchronisation automatique terminée: {results['success']} succès, "
        f"{results['errors']} erreurs sur {results['total']} comptes"
    )

    return results


@shared_task(
    autoretry_for=(ConnectionTimeoutError, RateLimitError),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
)
def sync_bank_account(account_id: int, sync_type: str = SyncLog.SyncType.AUTOMATIC) -> Dict:
    """
    Synchronise un compte bancaire spécifique.

    Cette tâche peut être appelée manuellement pour synchroniser un compte spécifique,
    ou automatiquement via Celery Beat pour les synchronisations quotidiennes.

    Args:
        account_id: ID du compte à synchroniser
        sync_type: Type de synchronisation ("manual" ou "automatic"), défaut: "automatic"

    Returns:
        dict: Résultat de la synchronisation depuis SyncService.sync_account()

    Raises:
        Account.DoesNotExist: Si le compte n'existe pas
        ValueError: Si le compte n'a pas de bank_connection ou si auto_sync_enabled=False
        ConnectionTimeoutError: Si la connexion timeout (retry automatique)
        RateLimitError: Si le rate limit est atteint (retry automatique)
    """
    try:
        account = Account.objects.select_related("bank_connection").get(id=account_id)
    except Account.DoesNotExist:
        logger.error(f"Compte {account_id} introuvable")
        raise

    if not account.bank_connection:
        error_msg = f"Le compte {account_id} n'a pas de connexion bancaire associée"
        logger.error(error_msg)
        raise ValueError(error_msg)

    logger.info(
        f"Début de synchronisation du compte {account_id} "
        f"(provider: {account.bank_connection.provider}, sync_type: {sync_type})"
    )

    try:
        result = SyncService.sync_account(account, sync_type=sync_type)

        if result["success"]:
            logger.info(
                f"Synchronisation réussie du compte {account_id}: "
                f"{result['transactions_count']} transactions synchronisées"
            )
        else:
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Synchronisation échouée du compte {account_id}: {error_msg}")

            # Ne pas retry pour les erreurs définitives (credentials invalides, etc.)
            if result.get("requires_2fa"):
                # L'authentification 2FA requise n'est pas une erreur temporaire
                logger.warning(
                    f"Authentification 2FA requise pour le compte {account_id}, "
                    f"pas de retry automatique"
                )
            elif "Invalid credentials" in error_msg or "credentials" in error_msg.lower():
                # Ne pas retry pour les erreurs de credentials
                logger.warning(
                    f"Erreur de credentials pour le compte {account_id}, pas de retry automatique"
                )

        return result

    except (ConnectionTimeoutError, RateLimitError) as e:
        # Ces erreurs sont gérées par Celery avec retry automatique
        logger.warning(
            f"Erreur temporaire lors de la synchronisation du compte {account_id}: {str(e)}, "
            f"retry automatique prévu"
        )
        raise

    except InvalidCredentialsError as e:
        # Ne pas retry pour les credentials invalides
        logger.error(
            f"Credentials invalides pour le compte {account_id}: {str(e)}, pas de retry"
        )
        raise

    except Exception as e:
        # Pour les autres erreurs, logger et lever l'exception
        logger.error(
            f"Erreur lors de la synchronisation du compte {account_id}: {str(e)}",
            exc_info=True,
        )
        raise


# Tâche existante conservée pour compatibilité
@shared_task
def snapshot_networth() -> str:
    """Placeholder: calculs implémentés plus tard"""
    return "ok"


@shared_task
def cleanup_old_sync_logs() -> Dict:
    """
    Nettoie les anciens logs de synchronisation selon la rétention configurée.
    
    Cette tâche est appelée automatiquement par Celery Beat quotidiennement.
    Elle supprime les logs avec `started_at` plus ancien que la rétention configurée.
    
    Configuration :
    - Variable d'environnement `SYNC_LOG_RETENTION_DAYS` : Nombre de jours de rétention (défaut: 30)
    - Si `SYNC_LOG_RETENTION_DAYS=0` ou non défini comme nombre valide, aucun nettoyage n'est effectué
    
    Returns:
        dict: Résultat du nettoyage :
              {
                  "deleted_count": int,
                  "retention_days": int,
                  "cutoff_date": str,
              }
    """
    import os
    from datetime import timedelta
    from django.utils import timezone
    
    # Récupérer la rétention depuis les variables d'environnement
    retention_days_str = os.getenv("SYNC_LOG_RETENTION_DAYS", "30")
    
    try:
        retention_days = int(retention_days_str)
    except (ValueError, TypeError):
        logger.warning(
            f"SYNC_LOG_RETENTION_DAYS invalide ('{retention_days_str}'), "
            f"utilisation de la valeur par défaut: 30 jours"
        )
        retention_days = 30
    
    # Si rétention = 0 ou négative, ne pas nettoyer
    if retention_days <= 0:
        logger.info("Rétention des logs désactivée (SYNC_LOG_RETENTION_DAYS <= 0), aucun nettoyage effectué")
        return {
            "deleted_count": 0,
            "retention_days": retention_days,
            "cutoff_date": None,
        }
    
    # Calculer la date limite
    cutoff_date = timezone.now() - timedelta(days=retention_days)
    
    logger.info(
        f"Début du nettoyage des logs de synchronisation "
        f"(rétention: {retention_days} jours, date limite: {cutoff_date})"
    )
    
    # Supprimer les logs plus anciens que la date limite
    deleted_count = SyncLog.objects.filter(started_at__lt=cutoff_date).count()
    SyncLog.objects.filter(started_at__lt=cutoff_date).delete()
    
    logger.info(
        f"Nettoyage des logs terminé: {deleted_count} logs supprimés "
        f"(logs avec started_at < {cutoff_date})"
    )
    
    return {
        "deleted_count": deleted_count,
        "retention_days": retention_days,
        "cutoff_date": cutoff_date.isoformat(),
    }
