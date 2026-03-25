import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

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


