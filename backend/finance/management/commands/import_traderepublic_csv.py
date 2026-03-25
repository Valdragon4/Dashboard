from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser

from finance.importers.loader import import_traderepublic_from_csv


class Command(BaseCommand):
    help = "Importe un export CSV Trade Republic (mouvements de cash) dans un compte broker."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username", help="Utilisateur propriétaire")
        parser.add_argument("csv_path", help="Chemin vers le fichier CSV exporté de Trade Republic")
        parser.add_argument(
            "account_name",
            help="Nom du compte courtage local (créé s'il n'existe pas)",
        )
        parser.add_argument(
            "--currency",
            default="EUR",
            help="Devise du compte (par défaut EUR)",
        )

    def handle(self, *args, **opts):
        username: str = opts["username"]
        csv_path: str = opts["csv_path"]
        account_name: str = opts["account_name"]
        currency: str = opts["currency"]

        User = get_user_model()
        user = User.objects.get(username=username)  # noqa: N806

        imported = import_traderepublic_from_csv(
            user=user,
            csv_path=csv_path,
            account_name=account_name,
            currency=currency,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{imported} lignes Trade Republic importées dans le compte '{account_name}'."
            )
        )

