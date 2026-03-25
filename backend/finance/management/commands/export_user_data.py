from __future__ import annotations

import json
from django.core.management.base import BaseCommand, CommandParser
from django.contrib.auth import get_user_model

from finance.models import Account, Transaction, InvestmentHolding


class Command(BaseCommand):
    help = "Exporte les données d'un utilisateur (RGPD) en JSON sur stdout"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")

    def handle(self, *args, **opts):
        User = get_user_model()
        user = User.objects.get(username=opts["username"])  # noqa: N806
        data = {
            "user": {"id": user.id, "username": user.username, "email": user.email},
            "accounts": list(Account.objects.filter(owner=user).values()),
            "transactions": list(
                Transaction.objects.filter(account__owner=user)
                .values()
                .order_by("-posted_at")
            ),
            "holdings": list(InvestmentHolding.objects.filter(account__owner=user).values()),
        }
        self.stdout.write(json.dumps(data, default=str))


