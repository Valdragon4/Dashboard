"""Livrets réglementés : recopie des virements, quinzaines, taux officiels."""

from datetime import date, datetime, time as dtime
from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finance.models import Account, RegulatedSavingsRate, Transaction
from finance.services import regulated_savings as rs

User = get_user_model()


def at(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, dtime(12, 0)))


class InterestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        RegulatedSavingsRate.objects.all().delete()
        RegulatedSavingsRate.objects.create(product="livret_a", valid_from=date(2025, 1, 1), rate=Decimal("2.4"))
        self.la = Account.objects.create(owner=self.user, name="LA", type=Account.AccountType.SAVINGS,
                                         savings_product="livret_a", initial_balance=Decimal("1000"))

    def test_quinzaines(self):
        # 1000 € toute l'année à 2,4 % = 24,00
        # +240 le 10/03 : porte intérêt dès le 16/03, 19 quinzaines = 4,56
        # −100 le 20/06 : cesse dès le 16/06, 13 quinzaines = −1,30
        Transaction.objects.create(account=self.la, posted_at=at(date(2026, 3, 10)), amount=Decimal("240"))
        Transaction.objects.create(account=self.la, posted_at=at(date(2026, 6, 20)), amount=Decimal("-100"))
        self.assertEqual(rs.compute_interest(self.la, 2026)["amount"], Decimal("27.26"))

    def test_changement_de_taux_et_credit_annuel_idempotent(self):
        RegulatedSavingsRate.objects.create(product="livret_a", valid_from=date(2026, 8, 1), rate=Decimal("1.2"))
        # 7 mois à 2,4 % (14 q.) + 5 mois à 1,2 % (10 q.) = 14 + 5 = 19,00
        self.assertEqual(rs.compute_interest(self.la, 2026)["amount"], Decimal("19.00"))
        rs.credit_yearly_interest(2026)
        rs.credit_yearly_interest(2026)
        credits = Transaction.objects.filter(account=self.la, raw__source="savings_interest")
        self.assertEqual(credits.count(), 1)
        self.assertEqual(credits.get().posted_at.date(), date(2026, 12, 31))

    def test_taux_manquant_rien_credite(self):
        RegulatedSavingsRate.objects.all().delete()
        out = rs.credit_yearly_interest(2026)
        self.assertIn("taux manquants", out[0])
        self.assertFalse(Transaction.objects.filter(raw__source="savings_interest").exists())


class MirrorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.cc = Account.objects.create(owner=self.user, name="CC", type=Account.AccountType.CHECKING)
        self.lep = Account.objects.create(owner=self.user, name="LEP", type=Account.AccountType.SAVINGS,
                                          savings_product="lep")
        self.la = Account.objects.create(owner=self.user, name="LA", type=Account.AccountType.SAVINGS,
                                         savings_product="livret_a")
        rs.set_balance(self.lep, Decimal("9999.01"), date(2026, 11, 2))
        rs.set_balance(self.la, Decimal("1037.02"), date(2026, 11, 2))

    def _cc(self, d, amount, label):
        return Transaction.objects.create(account=self.cc, posted_at=at(d), amount=Decimal(amount), description=label)

    def test_recopie_et_solde_saisi_preserve(self):
        self._cc(date(2026, 11, 2), "-850", "VIR CPTE A CPTE EMIS /MOTIF VIREMENT VERS LIVRET A /BEN M X")
        self._cc(date(2026, 11, 5), "-150", "VIR CPTE A CPTE EMIS /MOTIF VIREMENT VERS LEP /BEN M X")
        self._cc(date(2026, 11, 6), "-120", "VIR CPTE A CPTE EMIS /MOTIF VIREMENT VERS LIVRET A /BEN M X")
        self._cc(date(2026, 11, 7), "-749", "VIR SEPA INST EMIS /MOTIF LOYER")
        rs.mirror_transfers(self.cc)
        rs.mirror_transfers(self.cc)  # idempotent
        self.lep.refresh_from_db()
        self.la.refresh_from_db()
        today = date(2026, 11, 30)
        self.assertEqual(rs.balance_on(self.lep, today), Decimal("10149.01"))
        self.assertEqual(rs.balance_on(self.la, today), Decimal("1157.02"), "le 850 du 02/11 est déjà dans le solde saisi")
        self.assertEqual(rs.balance_on(self.la, date(2026, 11, 2)), Decimal("1037.02"), "solde saisi intact")
        self.assertEqual(rs.balance_on(self.la, date(2026, 11, 1)), Decimal("187.02"), "historique reconstitué")
        self.assertTrue(Transaction.objects.get(account=self.cc, amount=Decimal("-150")).is_transfer)
        self.assertFalse(Transaction.objects.get(account=self.cc, amount=Decimal("-749")).is_transfer)

    def test_retraits(self):
        motif = self._cc(date(2026, 11, 8), "50", "VIR CPTE A CPTE RECU /DE M X /MOTIF LEP VIREMENT VERS COMPTE DE CHEQUES /REF")
        flou = self._cc(date(2026, 11, 9), "110", "VIR CPTE A CPTE RECU /DE M X /MOTIF VIREMENT VERS COMPTE DE CHEQUES /REF")
        rs.mirror_transfers(self.cc)
        self.lep.refresh_from_db()
        self.assertEqual(rs.balance_on(self.lep, date(2026, 11, 30)), Decimal("9949.01"))
        self.assertEqual([t.id for t in rs.pending_withdrawals(self.user)], [flou.id])

        rs.assign_withdrawal(Transaction.objects.get(id=flou.id), self.la)
        rs.mirror_transfers(self.cc)
        self.la.refresh_from_db()
        self.assertEqual(rs.balance_on(self.la, date(2026, 11, 30)), Decimal("927.02"))
        self.assertEqual(rs.pending_withdrawals(self.user), [])
        self.assertTrue(Transaction.objects.get(id=motif.id).is_transfer)


class RateFetchTests(TestCase):
    PAGES = {
        "F2365": "<p>Le <strong>taux d'intérêt annuel du livret A</strong> est de 1,7&nbsp;%.</p>",
        "F2367": "<h2>Taux actuel</h2><p>Le taux d'intérêt du LEP est de 2,50 %.</p>",
    }

    def _http(self, pages):
        http = MagicMock()

        def get(url, **kw):
            resp = MagicMock()
            resp.text = pages[url.rsplit("/", 1)[1]]
            return resp

        http.get.side_effect = get
        return http

    def test_releve_et_periode(self):
        RegulatedSavingsRate.objects.all().delete()
        report = rs.fetch_official_rates(date(2026, 11, 2), http=self._http(self.PAGES))
        self.assertEqual(rs.rate_on("livret_a", date(2026, 11, 2)), Decimal("1.7"))
        self.assertEqual(rs.rate_on("lep", date(2026, 8, 1)), Decimal("2.5"))
        self.assertIsNone(rs.rate_on("lep", date(2026, 7, 31)))
        self.assertIn("nouveau", report["lep"])

    def test_page_changee_ne_casse_rien(self):
        before = RegulatedSavingsRate.objects.count()
        report = rs.fetch_official_rates(date(2026, 11, 2), http=self._http({"F2365": "<p>refonte</p>", "F2367": "x"}))
        self.assertEqual(RegulatedSavingsRate.objects.count(), before)
        self.assertIn("introuvable", report["livret_a"])

    def test_periodes(self):
        self.assertEqual(rs.current_period_start(date(2026, 1, 15)), date(2025, 8, 1))
        self.assertEqual(rs.current_period_start(date(2026, 2, 1)), date(2026, 2, 1))
        self.assertEqual(rs.current_period_start(date(2026, 11, 2)), date(2026, 8, 1))

    def test_taux_initiaux_migres(self):
        self.assertEqual(rs.rate_on("livret_a", date(2026, 3, 1)), Decimal("1.5"))
        self.assertEqual(rs.rate_on("lep", date(2026, 1, 1)), Decimal("2.7"))


class SettingsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.client.force_login(self.user)
        self.la = Account.objects.create(owner=self.user, name="LA", type=Account.AccountType.SAVINGS)

    def test_type_et_recalage(self):
        self.client.post(reverse("settings"), {"form": "savings", f"product_{self.la.id}": "livret_a",
                                               f"balance_{self.la.id}": "1 037,02"})
        self.la.refresh_from_db()
        self.assertEqual(self.la.savings_product, "livret_a")
        self.assertEqual(rs.balance_on(self.la, timezone.localdate()), Decimal("1037.02"))
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Livrets réglementés")
        self.assertContains(resp, "intérêts")
