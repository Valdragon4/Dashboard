"""
Tests du connecteur DSP2 Enable Banking.

Les réponses d'API reprennent la forme réelle observée chez Hello Bank le
2026-11-02 (aucun identifiant d'opération, soldes CLBD + XPCD, opérations PDNG).
"""

import base64
import os
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from finance.connectors.base import AuthenticationError
from finance.connectors.enablebanking import (
    EnableBankingConnector,
    account_fingerprint,
    normalize_transactions,
    pick_booked_balance,
)
from finance.models import Account, BankAccountLink, BankConnection, SyncLog, Transaction
from finance.services.enablebanking_client import EnableBankingClient, EnableBankingSessionExpired
from finance.services.encryption_service import EncryptionService
from finance.services.sync_service import SyncService
from finance.views_enablebanking import store_session

User = get_user_model()


def _tx(day, amount, indicator, label, status="BOOK", **extra):
    return {
        "booking_date": day,
        "value_date": day,
        "credit_debit_indicator": indicator,
        "transaction_amount": {"amount": str(amount), "currency": "EUR"},
        "remittance_information": [label],
        "status": status,
        **extra,
    }


class NormalizeTransactionsTests(TestCase):
    def test_signes_statuts_et_identifiants_de_doublons(self):
        raw = [
            _tx("2026-10-30", "12.50", "DBIT", "CB CAFE"),
            _tx("2026-10-30", "12.50", "DBIT", "CB  cafe"),  # même opération le même jour
            _tx("2026-10-31", "100", "CRDT", "VIR SEPA RECU"),
            _tx("2026-11-02", "5", "DBIT", "CB EN ATTENTE", status="PDNG"),
        ]
        out = normalize_transactions(raw, "Hello Bank")
        self.assertEqual(len(out), 3, "l'opération en attente est ignorée")
        self.assertEqual(out[0]["amount"], Decimal("-12.50"))
        self.assertEqual(out[2]["amount"], Decimal("100"))
        ids = [t["raw"]["transaction_id"] for t in out]
        self.assertEqual(len(set(ids)), 3, "deux opérations identiques gardent deux identifiants")
        self.assertTrue(ids[0].endswith("#1") and ids[1].endswith("#2"))

    def test_identifiants_stables_entre_deux_synchros(self):
        raw = [_tx("2026-10-30", "1", "DBIT", "A"), _tx("2026-10-30", "1", "DBIT", "A")]
        first = [t["raw"]["transaction_id"] for t in normalize_transactions(raw, "Hello Bank")]
        second = [t["raw"]["transaction_id"] for t in normalize_transactions(list(reversed(raw)), "Hello Bank")]
        self.assertEqual(sorted(first), sorted(second))

    def test_identifiant_banque_en_cle_secondaire(self):
        sans = normalize_transactions([_tx("2026-10-30", "1", "DBIT", "A")], "Hello Bank")[0]["raw"]
        avec = normalize_transactions([_tx("2026-10-30", "1.00", "DBIT", "A", entry_reference="R1")], "Hello Bank")[0]["raw"]
        self.assertEqual(sans["transaction_id"], avec["transaction_id"], "même opération, numéro apparu après coup")
        self.assertEqual(avec["operation_id"], "eb:Hello Bank:R1")
        self.assertNotIn("operation_id", sans)


class PickBalanceTests(TestCase):
    def test_solde_de_cloture_complete_par_les_operations_posterieures(self):
        balances = [
            {"balance_type": "CLBD", "balance_amount": {"amount": "35.81"}, "reference_date": "2026-10-30"},
            {"balance_type": "XPCD", "balance_amount": {"amount": "30.81"}, "reference_date": "2026-11-02"},
        ]
        booked = normalize_transactions(
            [_tx("2026-10-30", "10", "DBIT", "deja dans le solde"), _tx("2026-10-31", "4.19", "CRDT", "apres")],
            "Hello Bank",
        )
        self.assertEqual(pick_booked_balance(balances, booked), Decimal("40.00"))

    def test_repli_sur_solde_attendu(self):
        balances = [{"balance_type": "XPCD", "balance_amount": {"amount": "30.81"}}]
        self.assertEqual(pick_booked_balance(balances, []), Decimal("30.81"))


class ClientJwtTests(TestCase):
    def test_jwt_rs256_verifiable(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with TemporaryDirectory() as d:
            path = Path(d) / "k.pem"
            path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            token = EnableBankingClient(app_id="app-1", key_path=str(path))._jwt()
        import base64, json
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        head, body, sig = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(head)))["kid"], "app-1")
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(body)))["aud"], "api.enablebanking.com")
        key.public_key().verify(
            base64.urlsafe_b64decode(pad(sig)), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )


SESSION = {
    "session_id": "sess-1",
    "access": {"valid_until": "2027-04-30T17:12:47Z"},
    "accounts": [
        {
            "uid": "uid-1",
            "identification_hash": "hash-courant",
            "account_id": {"iban": "FR7600000000000000000004997"},
            "name": "MAROT VALENTIN",
            "cash_account_type": "CACC",
            "currency": "EUR",
        }
    ],
}
CTX = {"aspsp": "Hello Bank", "country": "FR", "psu_type": "personal", "connection_id": None}


def _with_encryption_key(testcase):
    """Clé Fernet de test : d'autres fichiers de tests retirent ENCRYPTION_KEY de l'environnement."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    patcher = patch.dict(os.environ, {"ENCRYPTION_KEY": key})
    patcher.start()
    testcase.addCleanup(patcher.stop)


class SessionAndSyncTests(TestCase):
    def setUp(self):
        _with_encryption_key(self)
        self.user = User.objects.create_user(username="u", password="p")
        self.account = Account.objects.create(
            owner=self.user,
            name="Hellobank",
            type=Account.AccountType.CHECKING,
            initial_balance=Decimal("2799.65"),
            balance_snapshot_date=date(2025, 11, 1),
        )

    def _link_account(self):
        connection = store_session(self.user, CTX, SESSION)
        link = BankAccountLink.objects.get(connection=connection)
        link.account = self.account
        link.save()
        self.account.bank_connection = connection
        self.account.auto_sync_enabled = True
        self.account.save()
        return connection, link

    def test_store_session_puis_renouvellement_garde_le_rattachement(self):
        connection, link = self._link_account()
        self.assertEqual(link.external_account_id, account_fingerprint(SESSION["accounts"][0]))
        self.assertEqual(link.raw["uid"], "uid-1")
        self.assertEqual(link.raw["iban_last4"], "4997")
        self.assertEqual(EncryptionService.decrypt_credentials(connection.encrypted_credentials)["session_id"], "sess-1")

        renewed = {**SESSION, "session_id": "sess-2", "accounts": [{**SESSION["accounts"][0], "uid": "uid-2"}]}
        same = store_session(self.user, {**CTX, "connection_id": connection.id}, renewed)
        self.assertEqual(same.id, connection.id)
        link.refresh_from_db()
        self.assertEqual(link.raw["uid"], "uid-2")
        self.assertEqual(link.account_id, self.account.id, "le rattachement survit au renouvellement")

    def test_sync_complete_importe_dedoublonne_et_recale_le_solde(self):
        self._link_account()
        today = timezone.localdate()
        d1, d2 = (today - timedelta(days=3)).isoformat(), (today - timedelta(days=1)).isoformat()
        client = MagicMock()
        client.get_session.return_value = {"status": "AUTHORIZED"}
        client.get_transactions.return_value = [
            _tx(d1, "20", "DBIT", "CB A"),
            _tx(d1, "20", "DBIT", "CB A"),
            _tx(d2, "50", "CRDT", "VIR"),
            _tx(d2, "9", "DBIT", "PENDING", status="PDNG"),
        ]
        client.get_balances.return_value = [
            {"balance_type": "CLBD", "balance_amount": {"amount": "35.81"}, "reference_date": d2},
        ]
        with patch.object(SyncService, "_get_connector_for_provider", return_value=EnableBankingConnector(client)):
            result = SyncService.sync_account(self.account, sync_type=SyncLog.SyncType.MANUAL)
        self.assertTrue(result["success"], result)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 3)

        # Deuxième passage : aucun doublon.
        with patch.object(SyncService, "_get_connector_for_provider", return_value=EnableBankingConnector(client)):
            SyncService.sync_account(self.account, sync_type=SyncLog.SyncType.AUTOMATIC)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 3)

        self.account.refresh_from_db()
        tx_sum = sum(t.amount for t in Transaction.objects.filter(account=self.account))
        self.assertEqual(self.account.initial_balance + tx_sum, Decimal("35.81"))

    def test_consentement_expire(self):
        self._link_account()
        client = MagicMock()
        client.get_session.side_effect = EnableBankingSessionExpired("expired", 401)
        with self.assertRaises(AuthenticationError):
            EnableBankingConnector(client).authenticate(
                {"session_id": "sess-1", "aspsp": "Hello Bank", "valid_until": "2027-04-30T17:12:47Z"}
            )
        with self.assertRaises(AuthenticationError):
            EnableBankingConnector(client).authenticate(
                {"session_id": "sess-1", "aspsp": "Hello Bank", "valid_until": "2020-01-01T00:00:00Z"}
            )


class ViewsTests(TestCase):
    def setUp(self):
        _with_encryption_key(self)
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)

    def test_callback_refuse_un_state_inconnu(self):
        resp = self.client.get(reverse("enablebanking_callback"), {"state": "x", "code": "y"})
        self.assertRedirects(resp, reverse("bank_connections_list"), fetch_redirect_response=False)
        self.assertFalse(BankConnection.objects.exists())

    @patch("finance.views_enablebanking.EnableBankingClient")
    def test_parcours_complet(self, client_cls):
        client = client_cls.return_value
        client.list_aspsps.return_value = [{"name": "Hello Bank", "maximum_consent_validity": 15552000}]
        client.start_authorization.return_value = {"url": "https://bank.example/auth"}
        client.create_session.return_value = SESSION

        resp = self.client.post(reverse("enablebanking_connect"), {"aspsp": "Hello Bank", "country": "FR", "psu_type": "personal"})
        self.assertEqual(resp["Location"], "https://bank.example/auth")
        state = client.start_authorization.call_args.kwargs["state"]

        resp = self.client.get(reverse("enablebanking_callback"), {"state": state, "code": "c"})
        connection = BankConnection.objects.get(provider=BankConnection.Provider.ENABLEBANKING)
        self.assertRedirects(
            resp, reverse("enablebanking_accounts", args=[connection.id]), fetch_redirect_response=False
        )
        link = BankAccountLink.objects.get(connection=connection)

        resp = self.client.post(reverse("enablebanking_accounts", args=[connection.id]), {f"link_{link.id}": "new"})
        account = Account.objects.get(owner=self.user)
        self.assertEqual(account.bank_connection_id, connection.id)
        self.assertEqual(account.type, Account.AccountType.CHECKING)
        self.assertTrue(account.auto_sync_enabled)

        # Le state est à usage unique.
        resp = self.client.get(reverse("enablebanking_callback"), {"state": state, "code": "c"})
        self.assertEqual(client.create_session.call_count, 1)

        resp = self.client.get(reverse("bank_connections_list"))
        self.assertContains(resp, "Renouveler l")
        self.assertContains(resp, "30/04/2027")


class RenameTests(TestCase):
    def setUp(self):
        _with_encryption_key(self)
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)
        two = {
            **SESSION,
            "accounts": [
                SESSION["accounts"][0],
                {**SESSION["accounts"][0], "uid": "uid-2", "identification_hash": "h2", "currency": "GBP"},
            ],
        }
        self.connection = store_session(self.user, {**CTX, "aspsp": "Revolut"}, two)
        self.links = list(BankAccountLink.objects.filter(connection=self.connection).order_by("currency"))

    def test_noms_proposes_distincts_et_renommage(self):
        url = reverse("enablebanking_accounts", args=[self.connection.id])
        eur, gbp = self.links
        self.client.post(url, {f"link_{eur.id}": "new", f"link_{gbp.id}": "new", f"name_{gbp.id}": ""})
        names = set(Account.objects.filter(owner=self.user).values_list("name", flat=True))
        self.assertEqual(names, {"Revolut EUR …4997", "Revolut GBP …4997"})

        eur.refresh_from_db()
        self.client.post(url, {f"link_{eur.id}": str(eur.account_id), f"name_{eur.id}": "Revolut perso",
                               f"link_{gbp.id}": str(Account.objects.get(name="Revolut GBP …4997").id)})
        self.assertTrue(Account.objects.filter(owner=self.user, name="Revolut perso").exists())
        self.assertTrue(Account.objects.filter(owner=self.user, name="Revolut GBP …4997").exists())


class ConnectListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)

    @patch("finance.views_enablebanking.EnableBankingClient")
    def test_banques_beta_proposees(self, client_cls):
        client_cls.return_value.list_aspsps.return_value = [
            {"name": "Trade Republic", "beta": True, "maximum_consent_validity": 7776000},
            {"name": "Hello Bank", "beta": False},
        ]
        resp = self.client.get(reverse("enablebanking_connect"))
        self.assertContains(resp, "Trade Republic (bêta)")
        self.assertContains(resp, "Hello Bank")


class ReferenceTardiveTests(TestCase):
    """Hello Bank : opération du jour sans numéro, puis avec le lendemain."""

    def setUp(self):
        _with_encryption_key(self)
        self.user = User.objects.create_user(username="u", password="p")
        self.account = Account.objects.create(owner=self.user, name="HB", type=Account.AccountType.CHECKING)
        connection = store_session(self.user, CTX, SESSION)
        link = BankAccountLink.objects.get(connection=connection)
        link.account = self.account
        link.save()
        self.account.bank_connection = connection
        self.account.auto_sync_enabled = True
        self.account.save()

    def _sync(self, txs):
        client = MagicMock()
        client.get_session.return_value = {"status": "AUTHORIZED"}
        client.get_transactions.return_value = txs
        client.get_balances.return_value = [{"balance_type": "XPCD", "balance_amount": {"amount": "0"}}]
        with patch.object(SyncService, "_get_connector_for_provider", return_value=EnableBankingConnector(client)):
            return SyncService.sync_account(self.account, sync_type=SyncLog.SyncType.MANUAL)

    def test_pas_de_doublon_quand_le_numero_apparait(self):
        day = (timezone.localdate() - timedelta(days=1)).isoformat()
        self._sync([_tx(day, "850.00", "DBIT", "VERS LIVRET A")])
        self._sync([_tx(day, "850.00", "DBIT", "VERS LIVRET A", entry_reference="000025")])
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 1)
        self.assertEqual(Transaction.objects.get(account=self.account).raw["operation_id"], "eb:Hello Bank:000025")

    def test_reindexation_des_anciens_identifiants(self):
        from django.core.management import call_command

        day = timezone.localdate() - timedelta(days=1)
        posted = timezone.make_aware(datetime.combine(day, dtime(12, 0)))
        Transaction.objects.create(account=self.account, posted_at=posted, amount=Decimal("-120.00"),
                                   description="VERS LIVRET A",
                                   raw={"source": "enablebanking", "aspsp": "Hello Bank",
                                        "transaction_id": "eb:Hello Bank:R9", "sync_fingerprint": "eb:Hello Bank:R9"})
        call_command("enablebanking_reindex")
        self._sync([_tx(day.isoformat(), "120", "DBIT", "VERS LIVRET A", entry_reference="R9")])
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 1)
