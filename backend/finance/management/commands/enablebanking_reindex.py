"""
Recalcule l'identifiant des opérations déjà importées via Enable Banking selon la
règle actuelle (contenu + rang, voir content_transaction_id), et conserve
l'éventuel identifiant de la banque en clé secondaire (`operation_id`).

Idempotent. --dry-run pour voir sans écrire.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from finance.connectors.enablebanking import _content_key, content_transaction_id
from finance.models import Transaction


class Command(BaseCommand):
    help = "Réindexe les opérations Enable Banking (identifiant tiré du contenu)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = (
            Transaction.objects.filter(raw__source="enablebanking")
            .select_related("account")
            .order_by("account_id", "posted_at", "id")
        )
        changed = 0
        seen = {}
        with transaction.atomic():
            for t in qs:
                raw = dict(t.raw or {})
                aspsp = raw.get("aspsp", "")
                day = t.posted_at.date() if t.posted_at else None
                if day is None:
                    continue
                key = (t.account_id, _content_key(day, t.amount, t.description))
                seen[key] = seen.get(key, 0) + 1
                new_id = content_transaction_id(aspsp, day, t.amount, t.description, seen[key])
                old_id = raw.get("transaction_id", "")
                if old_id == new_id and raw.get("sync_fingerprint") == new_id:
                    continue
                if old_id.startswith(f"eb:{aspsp}:") and not raw.get("operation_id"):
                    raw["operation_id"] = old_id
                raw["transaction_id"] = new_id
                raw["sync_fingerprint"] = new_id
                changed += 1
                if not opts["dry_run"]:
                    Transaction.objects.filter(id=t.id).update(raw=raw)
            if opts["dry_run"]:
                transaction.set_rollback(True)
        self.stdout.write(f"{qs.count()} opération(s) Enable Banking, {changed} réindexée(s)"
                          + (" (essai à blanc)" if opts["dry_run"] else ""))
