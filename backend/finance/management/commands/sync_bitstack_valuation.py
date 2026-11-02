from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser

from finance.models import Account
from finance.services import bitstack_import


class Command(BaseCommand):
    help = (
        "Revalorise les comptes Bitstack au cours BTC/EUR spot. "
        "Les transactions viennent de l'import CSV, seule la valorisation est automatique."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--account-id",
            type=int,
            default=None,
            help="Limite la revalorisation à ce compte.",
        )

    def handle(self, *args, **options):
        queryset = Account.objects.filter(provider=Account.Provider.BITSTACK)
        if options["account_id"]:
            queryset = queryset.filter(id=options["account_id"])

        accounts = list(queryset.select_related("owner"))
        if not accounts:
            raise CommandError("Aucun compte Bitstack trouvé.")

        for account in accounts:
            snapshot = bitstack_import.sync_valuation(account)
            if snapshot is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"account={account.id} : valorisation non écrite "
                        "(cours indisponible ou position vide)."
                    )
                )
                continue
            self.stdout.write(
                self.style.SUCCESS(
                    f"account={account.id} {account.name} : {snapshot.account_balance} €"
                )
            )
