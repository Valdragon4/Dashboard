from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from finance.models import Account
from finance.services.tr_bridge_sync import sync_bridge_snapshot_with_auth_handling


class Command(BaseCommand):
    help = "Synchronise la valorisation Trade Republic depuis le bridge Bun/TS."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, default=None, help="ID du compte broker Trade Republic")

    def handle(self, *args, **options):
        account_id = options.get("account_id")
        queryset = Account.objects.filter(
            provider="traderepublic",
            type=Account.AccountType.BROKER,
        )
        if account_id:
            queryset = queryset.filter(id=account_id)

        accounts = list(queryset.select_related("owner"))
        if not accounts:
            raise CommandError("Aucun compte Trade Republic broker trouvé.")

        for account in accounts:
            snapshot = sync_bridge_snapshot_with_auth_handling(account=account)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Snapshot créé account={account.id} snapshot={snapshot.id} status={snapshot.auth_status}"
                )
            )
