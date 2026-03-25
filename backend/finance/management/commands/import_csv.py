from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandParser
from django.contrib.auth import get_user_model

from finance.models import Account, Transaction


class Command(BaseCommand):
    help = "Importe un CSV générique (colonnes: date,amount,description,account)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")
        parser.add_argument("csv_path")
        parser.add_argument("account_name")

    def handle(self, *args, **opts):
        User = get_user_model()
        user = User.objects.get(username=opts["username"])  # noqa: N806
        account, _ = Account.objects.get_or_create(
            owner=user,
            name=opts["account_name"],
            defaults={"type": Account.AccountType.CHECKING},
        )
        with open(opts["csv_path"], newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                posted_at = datetime.fromisoformat(row["date"])  # YYYY-MM-DD
                amount = Decimal(row["amount"])  # positif=revenu, négatif=dépense
                Transaction.objects.get_or_create(
                    account=account,
                    posted_at=posted_at,
                    amount=amount,
                    description=row.get("description", ""),
                )
        self.stdout.write(self.style.SUCCESS("Import terminé"))


