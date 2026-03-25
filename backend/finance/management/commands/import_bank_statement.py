from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser

from finance.importers.loader import import_bank_statement_from_csv
from finance.models import Account


class Command(BaseCommand):
    help = (
        "Importe un relevé bancaire CSV (Boursobank, Hello bank ou générique) "
        "et le stocke dans la base."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username", help="Utilisateur propriétaire des comptes")
        parser.add_argument("csv_path", help="Chemin vers le fichier CSV")
        parser.add_argument(
            "account_name",
            help="Nom du compte local à alimenter (sera créé si nécessaire)",
        )
        parser.add_argument(
            "--profile",
            choices=["generic", "boursobank", "hellobank"],
            default="generic",
            help="Type de fichier CSV traité",
        )
        parser.add_argument(
            "--account-type",
            choices=[choice[0] for choice in Account.AccountType.choices],
            default=Account.AccountType.CHECKING,
            help="Type de compte local",
        )

    def handle(self, *args, **opts):
        username: str = opts["username"]
        csv_path: str = opts["csv_path"]
        account_name: str = opts["account_name"]
        profile: str = opts["profile"]
        account_type: str = opts["account_type"]

        User = get_user_model()
        user = User.objects.get(username=username)  # noqa: N806

        imported = import_bank_statement_from_csv(
            user=user,
            csv_path=csv_path,
            account_name=account_name,
            profile=profile,
            account_type=account_type,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{imported} lignes importées dans le compte '{account_name}'."
            )
        )


