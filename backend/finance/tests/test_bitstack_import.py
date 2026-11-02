"""Tests de l'import des relevés CSV Bitstack."""

from __future__ import annotations

import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from finance.models import Account, InvestmentHolding, Transaction
from finance.services import bitstack_import

HEADER = (
    "Type,Date,Fuseau horaire,Montant reçu,Monnaie ou jeton reçu,Montant envoyé,"
    "Monnaie ou jeton envoyé,Frais,Monnaie ou jeton des frais,Description,"
    "Prix du jeton du montant envoyé,Prix du jeton du montant recu,"
    "Prix du jeton des frais,Adresse,Transaction hash,ID Externe"
)

# Deux achats et un cadeau, calqués sur un export réel.
SAMPLE = "\n".join(
    [
        HEADER,
        "Échange,2026-08-23T15:44:06Z,GMT,0.00023951,BTC,16.26,EUR,0.29,EUR,"
        "Achat par carte,,66677.8,,,,id-achat-1",
        "Échange,2026-08-16T15:44:06Z,GMT,0.00014939,BTC,8.52,EUR,0.29,EUR,"
        "Achat par carte,,55090.7,,,,id-achat-2",
        "Dépôt,2026-01-04T15:44:36Z,GMT,0.0000025,BTC,,,,,Cadeau,,77864,,,,id-cadeau-1",
        "",
    ]
)


class StubPriceService:
    """Cours figé, pour ne pas dépendre du réseau dans les tests."""

    def __init__(self, price="60000"):
        self.price = price
        self.calls = 0

    def get_crypto_price_eur(self, symbol):
        self.calls += 1
        return {
            "price": Decimal(self.price),
            "source": "stub",
            "symbol": f"{symbol}-EUR",
            "priced_at": "2026-08-28T00:00:00+00:00",
        }


class FailingPriceService:
    def get_crypto_price_eur(self, symbol):
        return None


class BitstackImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bitstack-user", password="x"
        )
        self.account = Account.objects.create(
            owner=self.user,
            name="Bitstack",
            type=Account.AccountType.BROKER,
            portfolio_type=Account.PortfolioType.CRYPTO,
            provider=Account.Provider.BITSTACK,
        )

    def _import(self, payload=SAMPLE, **kwargs):
        return bitstack_import.import_csv(self.account, io.StringIO(payload), **kwargs)

    def test_import_creates_one_transaction_per_row(self):
        result = self._import()

        self.assertEqual(result.created, 3)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 3)

    def test_purchase_amount_includes_fee(self):
        """Le flux euro est ce qui quitte réellement la poche : montant + frais."""
        self._import()

        purchase = Transaction.objects.get(raw__transaction_id="id-achat-1")
        self.assertEqual(purchase.amount, Decimal("16.55"))
        self.assertEqual(purchase.raw["kind"], "buy")
        self.assertEqual(purchase.raw["fee_eur"], "0.29")
        self.assertEqual(purchase.raw["btc_delta"], "0.00023951")

    def test_gift_has_no_euro_flow(self):
        """Un cadeau en BTC n'est pas de l'argent investi : amount = 0."""
        self._import()

        gift = Transaction.objects.get(raw__transaction_id="id-cadeau-1")
        self.assertEqual(gift.amount, Decimal("0"))
        self.assertEqual(gift.raw["kind"], "btc_in")
        self.assertEqual(gift.raw["btc_delta"], "0.0000025")
        # Un cadeau ne doit pas être confondu avec un snapshot de valorisation.
        self.assertIsNone(gift.account_balance)

    def test_eur_invested_excludes_gifts(self):
        result = self._import()
        self.assertEqual(result.eur_invested, Decimal("16.55") + Decimal("8.81"))

    def test_reimport_is_idempotent(self):
        self._import()
        result = self._import()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 3)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 3)

    def test_dry_run_writes_nothing(self):
        result = self._import(dry_run=True)

        self.assertEqual(result.created, 3)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 0)

    def test_btc_position_keeps_satoshi_precision(self):
        """La position est sommée en 8 décimales, pas aux 6 du InvestmentHolding."""
        self._import()

        position = bitstack_import.compute_btc_position(self.account)
        self.assertEqual(position, Decimal("0.00039140"))

    def test_sale_is_negative_and_warns(self):
        payload = "\n".join(
            [
                HEADER,
                "Échange,2026-08-24T10:00:00Z,GMT,100.00,EUR,0.00200000,BTC,1.50,EUR,"
                "Vente,60000,,,,,id-vente-1",
            ]
        )
        result = self._import(payload)

        sale = Transaction.objects.get(raw__transaction_id="id-vente-1")
        self.assertEqual(sale.amount, Decimal("-98.50"))
        self.assertEqual(sale.raw["btc_delta"], "-0.00200000")
        self.assertTrue(any("vente" in w.lower() for w in result.warnings))

    def test_rejects_foreign_csv(self):
        with self.assertRaises(bitstack_import.BitstackImportError):
            self._import("date,amount,description\n2026-01-01,10,Test\n")

    def test_holding_mirrors_position(self):
        self._import()
        holding = bitstack_import.sync_holding(self.account)

        self.assertEqual(holding.symbol, "BTC")
        self.assertEqual(holding.tax_wrapper, "CRYPTO")
        self.assertEqual(holding.quantity, Decimal("0.000391"))
        self.assertEqual(InvestmentHolding.objects.filter(account=self.account).count(), 1)


class BitstackValuationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bitstack-valuation", password="x"
        )
        self.account = Account.objects.create(
            owner=self.user,
            name="Bitstack",
            type=Account.AccountType.BROKER,
            portfolio_type=Account.PortfolioType.CRYPTO,
            provider=Account.Provider.BITSTACK,
        )
        bitstack_import.import_csv(self.account, io.StringIO(SAMPLE))

    def test_valuation_snapshot_follows_broker_convention(self):
        """Le dashboard lit un compte courtier via amount=0 + account_balance."""
        snapshot = bitstack_import.sync_valuation(
            self.account, price_service=StubPriceService("60000")
        )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.amount, Decimal("0"))
        self.assertEqual(snapshot.account_balance, Decimal("23.48"))  # 0.0003914 * 60000
        self.assertEqual(snapshot.raw["portfolio_type"], "CRYPTO")

    def test_valuation_updates_account_balance(self):
        bitstack_import.sync_valuation(
            self.account, price_service=StubPriceService("60000")
        )
        self.account.refresh_from_db()

        self.assertEqual(self.account.initial_balance, Decimal("23.48"))
        self.assertIsNotNone(self.account.balance_snapshot_date)

    def test_valuation_is_capped_at_one_snapshot_per_day(self):
        bitstack_import.sync_valuation(self.account, price_service=StubPriceService("60000"))
        bitstack_import.sync_valuation(self.account, price_service=StubPriceService("70000"))

        snapshots = Transaction.objects.filter(
            account=self.account, raw__source=bitstack_import.VALUATION_SOURCE
        )
        self.assertEqual(snapshots.count(), 1)
        self.assertEqual(snapshots.first().account_balance, Decimal("27.40"))

    def test_no_snapshot_when_price_unavailable(self):
        """Mieux vaut pas de valorisation qu'une valorisation inventée."""
        snapshot = bitstack_import.sync_valuation(
            self.account, price_service=FailingPriceService()
        )

        self.assertIsNone(snapshot)
        self.assertFalse(
            Transaction.objects.filter(
                account=self.account, raw__source=bitstack_import.VALUATION_SOURCE
            ).exists()
        )

    def test_valuation_ignored_on_empty_position(self):
        empty = Account.objects.create(
            owner=self.user,
            name="Bitstack vide",
            type=Account.AccountType.BROKER,
            provider=Account.Provider.BITSTACK,
        )
        self.assertIsNone(
            bitstack_import.sync_valuation(empty, price_service=StubPriceService())
        )
