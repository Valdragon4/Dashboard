"""Renommer un compte depuis l'onglet Comptes."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from finance.models import Account, BankConnection

User = get_user_model()


class AccountUpdateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)
        self.conn = BankConnection.objects.create(owner=self.user, provider="enablebanking", account_name="Revolut")
        self.acc = Account.objects.create(owner=self.user, name="Valentin Marot", type="checking",
                                          bank_connection=self.conn, auto_sync_enabled=True,
                                          initial_balance=Decimal("12.34"), include_in_current_balance=False)

    def test_renommer_garde_la_synchro_et_le_solde(self):
        url = reverse("account_update", args=[self.acc.id])
        self.assertContains(self.client.get(url), 'value="Valentin Marot"')
        resp = self.client.post(url, {"name": "Revolut pro", "type": "checking", "currency": "EUR",
                                      "provider": "", "iban": "", "interest_rate_apy": "0", "portfolio_type": ""})
        self.assertRedirects(resp, reverse("accounts"), fetch_redirect_response=False)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.name, "Revolut pro")
        self.assertEqual(self.acc.bank_connection_id, self.conn.id)
        self.assertTrue(self.acc.auto_sync_enabled)
        self.assertEqual(self.acc.initial_balance, Decimal("12.34"))
        self.assertFalse(self.acc.include_in_current_balance)

    def test_compte_d_un_autre_utilisateur_introuvable(self):
        other = User.objects.create_user(username="o", password="p")
        acc = Account.objects.create(owner=other, name="X", type="checking")
        self.assertEqual(self.client.get(reverse("account_update", args=[acc.id])).status_code, 404)

    def test_lien_modifier_dans_la_liste(self):
        self.assertContains(self.client.get(reverse("accounts")), reverse("account_update", args=[self.acc.id]))
