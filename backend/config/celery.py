import os
import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

logger = logging.getLogger(__name__)

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def cleanup_stuck_syncs_on_startup(sender, **kwargs):
    """
    Au démarrage du worker, clore toutes les syncs restées en état STARTED.
    Cela arrive quand le worker est redémarré ou crashe en cours de sync :
    le SyncLog reste avec completed_at=None pour toujours, affichant
    faussement "en cours" dans le dashboard.
    """
    try:
        import django
        django.setup()
        from django.utils import timezone
        from finance.models import SyncLog, BankConnection

        stuck = SyncLog.objects.filter(status=SyncLog.Status.STARTED, completed_at__isnull=True)
        count = stuck.count()
        if count:
            stuck.update(
                status=SyncLog.Status.ERROR,
                completed_at=timezone.now(),
                error_message="Sync interrompue (worker redémarré ou crash)",
            )
            # Remettre les BankConnections concernées en état d'erreur
            connection_ids = stuck.values_list("bank_connection_id", flat=True)
            BankConnection.objects.filter(
                id__in=connection_ids,
                sync_status=BankConnection.SyncStatus.SYNCING,
            ).update(sync_status=BankConnection.SyncStatus.ERROR)
            logger.warning(
                "cleanup_stuck_syncs: %d sync(s) fantôme(s) closée(s) au démarrage du worker.",
                count,
            )
    except Exception:
        logger.exception("cleanup_stuck_syncs: erreur lors du nettoyage au démarrage.")

# Configuration Celery Beat pour les tâches périodiques
# Heure de synchronisation configurable via variables d'environnement
# Défaut: 2h du matin (heure de faible trafic)
BANK_SYNC_SCHEDULE_HOUR = int(os.getenv("BANK_SYNC_SCHEDULE_HOUR", "2"))
BANK_SYNC_SCHEDULE_MINUTE = int(os.getenv("BANK_SYNC_SCHEDULE_MINUTE", "0"))

app.conf.beat_schedule = {
    "sync-all-bank-accounts": {
        "task": "finance.tasks.sync_all_bank_accounts",
        "schedule": crontab(
            hour=BANK_SYNC_SCHEDULE_HOUR, minute=BANK_SYNC_SCHEDULE_MINUTE
        ),
    },
    "cleanup-old-sync-logs": {
        "task": "finance.tasks.cleanup_old_sync_logs",
        "schedule": crontab(hour=3, minute=0),  # Tous les jours à 3h du matin
        # Configuration de la rétention via variable d'environnement SYNC_LOG_RETENTION_DAYS (défaut: 30 jours)
        # Pour désactiver le nettoyage, définir SYNC_LOG_RETENTION_DAYS=0
    },
    "snapshot-net-worth-daily": {
        "task": "finance.tasks.snapshot_networth",
        "schedule": crontab(hour=3, minute=0),  # Tous les jours à 3h du matin
    },
}


