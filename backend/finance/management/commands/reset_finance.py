from __future__ import annotations

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from finance.models import (
    Transaction,
    Account,
    BankAccountLink,
    BankConnection,
    InvestmentHolding,
    InvestmentPrice,
    NetWorthSnapshot,
    CashflowRule,
    BudgetGoal,
    Category,
)


class Command(BaseCommand):
    help = "Purge toutes les données financières (tous utilisateurs). Attention: destructif."

    def add_arguments(self, parser) -> None:  # pragma: no cover
        parser.add_argument(
            "--only-user",
            type=str,
            help="Email ou ID utilisateur à purger uniquement.",
        )

    def handle(self, *args, **options) -> None:
        only_user = options.get("only_user")
        if only_user:
            user = None
            User = get_user_model()
            user = User.objects.filter(email=only_user).first() or User.objects.filter(id=only_user).first()
            if not user:
                self.stdout.write(self.style.ERROR("Utilisateur introuvable."))
                return
            self._purge_user(user.id)
            self.stdout.write(self.style.SUCCESS(f"Purge terminée pour l'utilisateur {user.id}."))
            return

        # Purge globale
        self._purge_all()
        self.stdout.write(self.style.SUCCESS("Purge financière globale terminée."))

    def _purge_user(self, user_id: int) -> None:
        Transaction.objects.filter(account__owner_id=user_id).delete()
        Account.objects.filter(owner_id=user_id).delete()
        BankAccountLink.objects.filter(connection__owner_id=user_id).delete()
        BankConnection.objects.filter(owner_id=user_id).delete()
        InvestmentHolding.objects.filter(account__owner_id=user_id).delete()
        NetWorthSnapshot.objects.filter(owner_id=user_id).delete()
        CashflowRule.objects.filter(owner_id=user_id).delete()
        BudgetGoal.objects.filter(owner_id=user_id).delete()

    def _purge_all(self) -> None:
        Transaction.objects.all().delete()
        Account.objects.all().delete()
        BankAccountLink.objects.all().delete()
        BankConnection.objects.all().delete()
        InvestmentHolding.objects.all().delete()
        InvestmentPrice.objects.all().delete()
        NetWorthSnapshot.objects.all().delete()
        CashflowRule.objects.all().delete()
        BudgetGoal.objects.all().delete()
        Category.objects.all().delete()


