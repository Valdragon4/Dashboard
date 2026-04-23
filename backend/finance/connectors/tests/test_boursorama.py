from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from finance.connectors.base import AuthenticationError, InvalidCredentialsError
from finance.connectors.boursorama import BoursoBankConnector


class TestBoursoBankConnector(TestCase):
    def setUp(self):
        self.credentials = {"username": "12345678", "password": "12345678"}

    @patch("finance.connectors.boursorama.BOURSOBANK_SCRAPER_AVAILABLE", True)
    @patch("finance.connectors.boursorama.BoursoScraper")
    def test_authenticate_success(self, mock_scraper_class):
        mock_scraper = Mock()
        mock_scraper.connect.return_value = True
        mock_scraper.listAccounts.return_value = [Mock(id="acc-1", name="Compte courant", balance="123.45")]
        mock_scraper_class.return_value = mock_scraper

        connector = BoursoBankConnector(data_path=Path("/tmp/boursobank-test"))
        result = connector.authenticate(self.credentials)

        self.assertEqual(result["session_id"], "boursobank_scraper_session")
        self.assertEqual(result["accounts_count"], 1)

    @patch("finance.connectors.boursorama.BOURSOBANK_SCRAPER_AVAILABLE", True)
    @patch("finance.connectors.boursorama.BoursoScraper")
    def test_authenticate_invalid_credentials(self, mock_scraper_class):
        mock_scraper = Mock()
        mock_scraper.connect.return_value = False
        mock_scraper_class.return_value = mock_scraper

        connector = BoursoBankConnector(data_path=Path("/tmp/boursobank-test"))
        with self.assertRaises(InvalidCredentialsError):
            connector.authenticate(self.credentials)

    @patch("finance.connectors.boursorama.BOURSOBANK_SCRAPER_AVAILABLE", True)
    @patch("finance.connectors.boursorama.BoursoScraper")
    def test_sync_transactions_filters_since(self, mock_scraper_class):
        operation_old = {
            "operation": {
                "id": "op-old",
                "amount": -10.0,
                "dates": [{"type": "operation_date", "date": "2025-01-01T10:00:00+01:00"}],
                "labels": [{"body": "Ancienne"}],
                "status": {"id": "booked"},
            }
        }
        operation_new = {
            "operation": {
                "id": "op-new",
                "amount": 20.0,
                "dates": [{"type": "operation_date", "date": "2025-01-15T10:00:00+01:00"}],
                "labels": [{"body": "Nouvelle"}],
                "status": {"id": "booked"},
            }
        }

        mock_scraper = Mock()
        mock_scraper.connect.return_value = True
        mock_scraper.listAccounts.return_value = [Mock(id="acc-1", name="Compte courant", balance="123.45")]
        mock_scraper.transactionsPath = Path("/tmp/unused")
        mock_scraper_class.return_value = mock_scraper

        connector = BoursoBankConnector(data_path=Path("/tmp/boursobank-test"))
        connector.authenticate(self.credentials)
        connector._load_operations_for_account = Mock(return_value=[operation_old, operation_new])  # type: ignore[attr-defined]

        account = Mock(name="Compte courant", iban="")
        since = timezone.make_aware(datetime(2025, 1, 10, 0, 0, 0), timezone.get_current_timezone())
        transactions = connector.sync_transactions(account, since=since)

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["raw"]["transaction_id"], "op-new")
        self.assertEqual(transactions[0]["amount"], Decimal("20.00"))

    @patch("finance.connectors.boursorama.BOURSOBANK_SCRAPER_AVAILABLE", True)
    @patch("finance.connectors.boursorama.BoursoScraper")
    def test_get_balance(self, mock_scraper_class):
        mock_scraper = Mock()
        mock_scraper.connect.return_value = True
        mock_scraper.listAccounts.return_value = [Mock(id="acc-1", name="Compte courant", balance="1500.50")]
        mock_scraper_class.return_value = mock_scraper

        connector = BoursoBankConnector(data_path=Path("/tmp/boursobank-test"))
        connector.authenticate(self.credentials)

        account = Mock(name="Compte courant", iban="")
        balance = connector.get_balance(account)
        self.assertEqual(balance, Decimal("1500.50"))

    @patch("finance.connectors.boursorama.BOURSOBANK_SCRAPER_AVAILABLE", True)
    @patch("finance.connectors.boursorama.BoursoScraper")
    def test_methods_require_auth(self, mock_scraper_class):
        mock_scraper_class.return_value = Mock()
        connector = BoursoBankConnector(data_path=Path("/tmp/boursobank-test"))
        account = Mock(name="Compte courant", iban="")

        with self.assertRaises(AuthenticationError):
            connector.sync_transactions(account)
        with self.assertRaises(AuthenticationError):
            connector.get_balance(account)

    @patch("finance.connectors.boursorama.BOURSOBANK_SCRAPER_AVAILABLE", True)
    @patch("finance.connectors.boursorama.BoursoScraper")
    def test_authenticate_returns_requires_2fa_on_securisation_timeout(self, mock_scraper_class):
        mock_scraper = Mock()
        mock_scraper.connect.side_effect = Exception(
            'Timeout ... navigated to "https://clients.boursobank.com/securisation?org=/budget/mouvements"'
        )
        mock_scraper_class.return_value = mock_scraper

        connector = BoursoBankConnector(data_path=Path("/tmp/boursobank-test"))
        result = connector.authenticate(self.credentials)

        self.assertTrue(result.get("requires_2fa"))
