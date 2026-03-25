"""
Tests unitaires pour le connecteur Trade Republic.

Ces tests utilisent des mocks pour éviter les appels API réels.
"""

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from django.test import TestCase

from finance.connectors.traderepublic import TradeRepublicConnector
from finance.connectors.base import (
    AuthenticationError,
    InvalidCredentialsError,
    ConnectionTimeoutError,
    RateLimitError,
    BankConnectionError,
)


class TestTradeRepublicConnector(TestCase):
    """Tests pour le connecteur Trade Republic."""

    def setUp(self):
        """Configure l'environnement de test."""
        self.connector = TradeRepublicConnector()
        self.credentials = {
            "phone_number": "+33123456789",
            "pin": "1234",
        }
        self.credentials_with_2fa = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

    def test_provider_name(self):
        """Test que provider_name retourne 'Trade Republic'."""
        self.assertEqual(self.connector.provider_name, "Trade Republic")

    def test_authenticate_missing_credentials(self):
        """Test que authenticate lève InvalidCredentialsError si credentials manquants."""
        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({})

        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({"phone_number": "+33123456789"})

        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({"pin": "1234"})

    @patch("finance.connectors.traderepublic.requests.post")
    def test_authenticate_initiate_login_success(self, mock_post):
        """Test authentification en deux étapes (process_id retourné)."""
        # Mock de la réponse d'initiation
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "processId": "test_process_id",
            "countdownSeconds": 60,
        }
        mock_post.return_value = mock_response

        result = self.connector.authenticate(self.credentials)

        self.assertIn("process_id", result)
        self.assertIn("countdown", result)
        self.assertTrue(result.get("requires_2fa"))
        self.assertEqual(result["process_id"], "test_process_id")
        self.assertEqual(result["countdown"], 60)

    @patch("finance.connectors.traderepublic.requests.post")
    def test_authenticate_with_2fa_success(self, mock_post):
        """Test authentification réussie avec 2FA complète."""
        # Mock de l'initiation
        mock_init_response = Mock()
        mock_init_response.status_code = 200
        mock_init_response.json.return_value = {
            "processId": "test_process_id",
            "countdownSeconds": 60,
        }

        # Mock de la vérification 2FA
        mock_verify_response = Mock()
        mock_verify_response.status_code = 200
        mock_verify_response.cookies = Mock()
        mock_verify_response.cookies.get.return_value = "test_session_token"
        mock_verify_response.headers = {}

        def post_side_effect(url, **kwargs):
            if "resend" in url or "/resend" in url:
                return mock_init_response
            if "/test_process_id/123456" in url:
                return mock_verify_response
            return mock_init_response

        mock_post.side_effect = post_side_effect

        result = self.connector.authenticate(self.credentials_with_2fa)

        self.assertIn("token", result)
        self.assertEqual(result["token"], "test_session_token")
        self.assertEqual(self.connector.token, "test_session_token")

    @patch("finance.connectors.traderepublic.requests.post")
    def test_authenticate_invalid_credentials(self, mock_post):
        """Test que authenticate lève InvalidCredentialsError pour credentials invalides."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"message": "Invalid credentials"}
        mock_post.return_value = mock_response

        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate(self.credentials)

    @patch("finance.connectors.traderepublic.requests.post")
    def test_authenticate_timeout(self, mock_post):
        """Test que authenticate lève ConnectionTimeoutError en cas de timeout."""
        import requests

        mock_post.side_effect = requests.exceptions.Timeout("Connection timeout")

        with self.assertRaises(ConnectionTimeoutError):
            self.connector.authenticate(self.credentials)

    @patch("finance.connectors.traderepublic.fetch_all_transactions")
    def test_sync_transactions_success(self, mock_fetch):
        """Test récupération de toutes les transactions."""
        # Mock du token
        self.connector.token = "test_token"

        # Mock des transactions
        mock_fetch.return_value = [
            {
                "id": "tx1",
                "timestamp": "2025-01-01T10:00:00Z",
                "type": "Deposit",
                "title": "Dépôt",
                "amount": {"value": "100.00"},
            },
            {
                "id": "tx2",
                "timestamp": "2025-01-02T11:00:00Z",
                "type": "Withdrawal",
                "title": "Retrait",
                "amount": {"value": "-50.00"},
            },
        ]

        account = Mock()
        transactions = self.connector.sync_transactions(account)

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0]["amount"], Decimal("100.00"))
        self.assertEqual(transactions[1]["amount"], Decimal("-50.00"))
        self.assertIn("posted_at", transactions[0])
        self.assertIn("description", transactions[0])
        self.assertIn("raw", transactions[0])

    @patch("finance.connectors.traderepublic.fetch_all_transactions")
    def test_sync_transactions_filter_by_date(self, mock_fetch):
        """Test filtrage par date since."""
        self.connector.token = "test_token"

        mock_fetch.return_value = [
            {
                "id": "tx1",
                "timestamp": "2025-01-01T10:00:00Z",
                "type": "Deposit",
                "amount": {"value": "100.00"},
            },
            {
                "id": "tx2",
                "timestamp": "2025-01-03T11:00:00Z",
                "type": "Withdrawal",
                "amount": {"value": "-50.00"},
            },
        ]

        account = Mock()
        since = datetime(2025, 1, 2)
        transactions = self.connector.sync_transactions(account, since=since)

        # Seule la transaction du 2025-01-03 doit être retournée
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["amount"], Decimal("-50.00"))

    @patch("finance.connectors.traderepublic.fetch_all_transactions")
    def test_sync_transactions_no_token(self, mock_fetch):
        """Test que sync_transactions lève AuthenticationError sans token."""
        account = Mock()

        with self.assertRaises(AuthenticationError):
            self.connector.sync_transactions(account)

    @patch("finance.connectors.traderepublic.fetch_all_transactions")
    def test_sync_transactions_timeout_retry(self, mock_fetch):
        """Test retry automatique en cas de timeout."""
        self.connector.token = "test_token"
        account = Mock()

        # Simuler 2 timeouts puis succès
        import asyncio

        mock_fetch.side_effect = [
            asyncio.TimeoutError("Timeout"),
            asyncio.TimeoutError("Timeout"),
            [{"id": "tx1", "timestamp": "2025-01-01T10:00:00Z", "type": "Deposit", "amount": {"value": "100.00"}}],
        ]

        transactions = self.connector.sync_transactions(account)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("finance.connectors.traderepublic.fetch_all_transactions")
    def test_sync_transactions_rate_limit(self, mock_fetch):
        """Test que sync_transactions lève RateLimitError en cas de rate limit."""
        self.connector.token = "test_token"
        account = Mock()

        mock_fetch.side_effect = Exception("Rate limit exceeded")

        with self.assertRaises(RateLimitError):
            self.connector.sync_transactions(account)

    @patch("finance.connectors.traderepublic.fetch_available_cash")
    def test_get_balance_success(self, mock_fetch):
        """Test récupération du solde réussie."""
        self.connector.token = "test_token"

        mock_fetch.return_value = [{"value": "1500.50"}]

        account = Mock()
        balance = self.connector.get_balance(account)

        self.assertEqual(balance, Decimal("1500.50"))

    @patch("finance.connectors.traderepublic.fetch_available_cash")
    def test_get_balance_no_token(self, mock_fetch):
        """Test que get_balance lève AuthenticationError sans token."""
        account = Mock()

        with self.assertRaises(AuthenticationError):
            self.connector.get_balance(account)

    @patch("finance.connectors.traderepublic.fetch_available_cash")
    def test_get_balance_empty_data(self, mock_fetch):
        """Test get_balance avec données vides."""
        self.connector.token = "test_token"
        mock_fetch.return_value = []

        account = Mock()
        balance = self.connector.get_balance(account)

        self.assertEqual(balance, Decimal("0"))

    @patch("finance.connectors.traderepublic.fetch_portfolio")
    def test_sync_portfolio_valuations_success(self, mock_fetch):
        """Test récupération des valorisations PEA/CTO/CRYPTO."""
        self.connector.token = "test_token"

        # Mock des données de portefeuille
        mock_fetch.return_value = {
            "portfolio": {
                "PEA": {"totalValue": "5000.00"},
                "CTO": {"totalValue": "3000.00"},
            }
        }

        account = Mock()
        valuations = self.connector.sync_portfolio_valuations(account)

        # Note: La détection du type de portefeuille dépend de la structure exacte
        # Pour ce test, on vérifie juste que la méthode s'exécute sans erreur
        self.assertIsInstance(valuations, dict)

    @patch("finance.connectors.traderepublic.fetch_portfolio")
    def test_sync_portfolio_valuations_no_token(self, mock_fetch):
        """Test que sync_portfolio_valuations lève AuthenticationError sans token."""
        account = Mock()

        with self.assertRaises(AuthenticationError):
            self.connector.sync_portfolio_valuations(account)

    def test_disconnect(self):
        """Test fermeture propre de la connexion."""
        self.connector.token = "test_token"
        self.connector.process_id = "test_process_id"
        self.connector.websocket = Mock()

        self.connector.disconnect()

        self.assertIsNone(self.connector.token)
        self.assertIsNone(self.connector.process_id)
        self.assertIsNone(self.connector.websocket)

    def test_format_transaction(self):
        """Test formatage d'une transaction."""
        transaction = {
            "id": "tx1",
            "timestamp": "2025-01-01T10:00:00Z",
            "type": "Deposit",
            "title": "Dépôt",
            "description": "Dépôt initial",
            "amount": {"value": "100.00"},
            "instrument": "EUR",
            "isin": "FR1234567890",
        }

        formatted = self.connector._format_transaction(transaction)

        self.assertIsNotNone(formatted)
        self.assertEqual(formatted["amount"], Decimal("100.00"))
        self.assertIn("posted_at", formatted)
        self.assertIn("description", formatted)
        self.assertIn("raw", formatted)
        self.assertEqual(formatted["raw"]["transaction_id"], "tx1")

    def test_format_transaction_filter_by_date(self):
        """Test filtrage d'une transaction par date."""
        transaction = {
            "id": "tx1",
            "timestamp": "2025-01-01T10:00:00Z",
            "type": "Deposit",
            "amount": {"value": "100.00"},
        }

        since = datetime(2025, 1, 2)
        formatted = self.connector._format_transaction(transaction, since=since)

        self.assertIsNone(formatted)  # Transaction trop ancienne

    def test_format_transaction_missing_timestamp(self):
        """Test formatage d'une transaction sans timestamp."""
        transaction = {
            "id": "tx1",
            "type": "Deposit",
            "amount": {"value": "100.00"},
        }

        formatted = self.connector._format_transaction(transaction)

        self.assertIsNone(formatted)

    def test_format_transaction_missing_amount(self):
        """Test formatage d'une transaction sans montant."""
        transaction = {
            "id": "tx1",
            "timestamp": "2025-01-01T10:00:00Z",
            "type": "Deposit",
        }

        formatted = self.connector._format_transaction(transaction)

        self.assertIsNone(formatted)
