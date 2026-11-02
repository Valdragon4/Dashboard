"""Choix, dans Paramètres, des comptes additionnés dans la carte « Solde courant »."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finance.models import Account, Transaction

User = get_user_model()


class CurrentBalanceSettingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)
        self.perso = Account.objects.create(owner=self.user, name="Perso", type=Account.AccountType.CHECKING,
                                            initial_balance=Decimal("100"))
        self.pro = Account.objects.create(owner=self.user, name="Pro", type=Account.AccountType.CHECKING,
                                          initial_balance=Decimal("1000"))
        Transaction.objects.create(account=self.pro, posted_at=timezone.now() - timedelta(days=1),
                                   amount=Decimal("50"), description="x")

    def _dashboard(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        return resp.context

    def test_par_defaut_tous_les_comptes(self):
        ctx = self._dashboard()
        self.assertEqual(ctx["balance"], 1150.0)

    def test_compte_decoche_hors_solde_courant_mais_dans_le_patrimoine(self):
        resp = self.client.post(reverse("settings"), {"form": "current_balance",
                                                      "current_balance_accounts": [str(self.perso.id)]})
        self.assertRedirects(resp, reverse("settings"), fetch_redirect_response=False)
        self.pro.refresh_from_db()
        self.assertFalse(self.pro.include_in_current_balance)

        ctx = self._dashboard()
        self.assertEqual(ctx["balance"], 100.0)
        self.assertEqual(float(ctx["total_wealth"]), 1150.0)

    def test_page_parametres_liste_les_comptes_courants(self):
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Solde courant")
        self.assertContains(resp, f'value="{self.pro.id}"')
