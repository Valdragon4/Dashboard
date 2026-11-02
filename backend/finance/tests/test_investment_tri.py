"""
Tests du périmètre du TRI (XIRR).

La valeur terminale du TRI provient exclusivement des snapshots Trade Republic.
Tout compte courtier valorisé autrement (Bitstack, valorisé au cours BTC/EUR)
apporterait ses versements aux flux sans apporter sa valeur au terme : le TRI
serait mécaniquement écrasé. Ces tests verrouillent cette exclusion.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finance.models import Account, Transaction, TradeRepublicValuationSnapshot


class InvestmentTriScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tri-user", password="x")
        self.client.force_login(self.user)
        self.now = timezone.now()

        self.tr_account = Account.objects.create(
            owner=self.user,
            name="Traderepublic",
            type=Account.AccountType.BROKER,
            provider="traderepublic",
        )
        # Un versement TR il y a un an, valorisé aujourd'hui à 1 200 €.
        Transaction.objects.create(
            account=self.tr_account,
            posted_at=self.now - timedelta(days=365),
            amount=Decimal("1000.00"),
            description="Versement",
        )
        TradeRepublicValuationSnapshot.objects.create(
            owner=self.user,
            account=self.tr_account,
            source_timestamp=self.now,
            invested_total=Decimal("1000.00"),
            positions_total=Decimal("1200.00"),
            total_with_cash=Decimal("1200.00"),
        )

        self.bitstack = Account.objects.create(
            owner=self.user,
            name="Bitstack",
            type=Account.AccountType.BROKER,
            portfolio_type=Account.PortfolioType.CRYPTO,
            provider=Account.Provider.BITSTACK,
        )

    def _tri(self):
        response = self.client.get(reverse("api_investment_tri"), {"period": "since_inception"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _add_bitstack_contribution(self, amount="5000.00"):
        Transaction.objects.create(
            account=self.bitstack,
            posted_at=self.now - timedelta(days=180),
            amount=Decimal(amount),
            description="Bitstack — Échange (Achat par carte)",
            raw={"source": "bitstack_csv", "transaction_id": "id-1", "kind": "buy"},
        )

    def test_bitstack_contribution_does_not_move_global_tri(self):
        before = self._tri()["global"]["rate"]
        self._add_bitstack_contribution()
        after = self._tri()["global"]["rate"]

        self.assertIsNotNone(before)
        self.assertEqual(before, after)

    def test_bitstack_absent_from_per_account_tri(self):
        self._add_bitstack_contribution()

        by_account = self._tri()["by_account"]
        names = {entry["name"] for entry in by_account.values()}
        self.assertEqual(names, {"Traderepublic"})
        self.assertNotIn(str(self.bitstack.id), by_account)

    def test_bitstack_valuation_snapshot_is_not_a_cash_flow(self):
        """Le snapshot de valorisation Bitstack (amount=0) ne doit rien injecter."""
        before = self._tri()["global"]["rate"]
        Transaction.objects.create(
            account=self.bitstack,
            posted_at=self.now,
            amount=Decimal("0"),
            description="Valorisation Bitstack",
            account_balance=Decimal("5400.00"),
            raw={"source": "bitstack_valuation", "portfolio_type": "CRYPTO"},
        )

        self.assertEqual(self._tri()["global"]["rate"], before)
