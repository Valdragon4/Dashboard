"""
Tests unitaires pour le connecteur BoursoBank.

Ces tests utilisent des mocks pour éviter les appels réels à Playwright et à l'interface web.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock

from django.test import TestCase

from finance.connectors.boursorama import BoursoBankConnector, PLAYWRIGHT_AVAILABLE
from finance.connectors.base import (
    AuthenticationError,
    InvalidCredentialsError,
    ConnectionTimeoutError,
    RateLimitError,
    BankConnectionError,
)


class TestBoursoBankConnector(TestCase):
    """Tests pour le connecteur BoursoBank."""

    def setUp(self):
        """Configure l'environnement de test."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("Playwright n'est pas disponible")
        
        self.connector = BoursoBankConnector()
        self.credentials = {
            "username": "test_user",
            "password": "test_password",
        }
        self.credentials_with_2fa = {
            "username": "test_user",
            "password": "test_password",
            "2fa_code": "123456",
        }

    def test_provider_name(self):
        """Test que provider_name retourne 'BoursoBank'."""
        self.assertEqual(self.connector.provider_name, "BoursoBank")

    def test_authenticate_missing_credentials(self):
        """Test que authenticate lève InvalidCredentialsError si credentials manquants."""
        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({})

        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({"username": "test"})

        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate({"password": "test"})

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_authenticate_success(self, mock_playwright):
        """Test authentification réussie."""
        # Mock Playwright
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        
        # Mock de la navigation et de la connexion réussie
        mock_page.url = "https://www.boursorama.com/espace-client"
        mock_browser.contexts = [MagicMock()]
        mock_browser.contexts[0].cookies.return_value = [{"name": "session", "value": "test_session"}]
        
        # Mock des sélecteurs
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        result = self.connector.authenticate(self.credentials)
        
        self.assertIn("session_id", result)
        self.assertTrue(self.connector._authenticated)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_authenticate_requires_2fa(self, mock_playwright):
        """Test authentification nécessitant 2FA."""
        # Mock Playwright
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        
        # Mock de la page demandant 2FA
        mock_page.url = "https://www.boursorama.com/connexion"
        mock_page.content.return_value = "code authentification sms"
        
        # Mock des sélecteurs
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        result = self.connector.authenticate(self.credentials)
        
        self.assertTrue(result.get("requires_2fa"))

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_authenticate_invalid_credentials(self, mock_playwright):
        """Test que authenticate lève InvalidCredentialsError pour credentials invalides."""
        # Mock Playwright
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        
        # Mock de la page avec erreur
        mock_page.content.return_value = "erreur incorrect invalid"
        mock_page.query_selector_all.return_value = [MagicMock(inner_text=lambda: "Identifiant ou mot de passe incorrect")]
        
        # Mock des sélecteurs
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        with self.assertRaises(InvalidCredentialsError):
            self.connector.authenticate(self.credentials)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_authenticate_timeout(self, mock_playwright):
        """Test que authenticate lève ConnectionTimeoutError en cas de timeout."""
        from finance.connectors.boursorama import PlaywrightTimeoutError
        
        # Mock Playwright avec timeout
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        
        # Simuler un timeout
        mock_page.goto.side_effect = PlaywrightTimeoutError("Timeout")
        
        with self.assertRaises(ConnectionTimeoutError):
            self.connector.authenticate(self.credentials)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_sync_transactions_success(self, mock_playwright):
        """Test récupération de toutes les transactions."""
        # Setup: Authentifier d'abord
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        mock_page.url = "https://www.boursorama.com/espace-client"
        mock_browser.contexts = [MagicMock()]
        mock_browser.contexts[0].cookies.return_value = []
        
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        self.connector.authenticate(self.credentials)
        
        # Mock de la page des transactions
        mock_table = MagicMock()
        mock_row = MagicMock()
        mock_cells = [
            MagicMock(inner_text=lambda: "01/01/2025"),
            MagicMock(inner_text=lambda: "Virement"),
            MagicMock(inner_text=lambda: "100,00 €"),
        ]
        mock_row.query_selector_all.return_value = mock_cells
        mock_table.query_selector_all.return_value = [mock_row]
        mock_page.query_selector.return_value = mock_table
        
        account = Mock()
        transactions = self.connector.sync_transactions(account)
        
        self.assertIsInstance(transactions, list)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_sync_transactions_filter_by_date(self, mock_playwright):
        """Test filtrage par date since."""
        # Setup: Authentifier d'abord
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        mock_page.url = "https://www.boursorama.com/espace-client"
        mock_browser.contexts = [MagicMock()]
        mock_browser.contexts[0].cookies.return_value = []
        
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        self.connector.authenticate(self.credentials)
        
        # Mock de la page des transactions avec deux transactions
        mock_table = MagicMock()
        mock_row1 = MagicMock()
        mock_row2 = MagicMock()
        mock_cells1 = [
            MagicMock(inner_text=lambda: "01/01/2025"),
            MagicMock(inner_text=lambda: "Transaction 1"),
            MagicMock(inner_text=lambda: "100,00 €"),
        ]
        mock_cells2 = [
            MagicMock(inner_text=lambda: "15/01/2025"),
            MagicMock(inner_text=lambda: "Transaction 2"),
            MagicMock(inner_text=lambda: "-50,00 €"),
        ]
        mock_row1.query_selector_all.return_value = mock_cells1
        mock_row2.query_selector_all.return_value = mock_cells2
        mock_table.query_selector_all.return_value = [mock_row1, mock_row2]
        mock_page.query_selector.return_value = mock_table
        
        account = Mock()
        since = datetime(2025, 1, 10)
        transactions = self.connector.sync_transactions(account, since=since)
        
        # Seule la transaction du 15/01 doit être retournée
        self.assertEqual(len(transactions), 1)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_sync_transactions_no_session(self, mock_playwright):
        """Test que sync_transactions lève AuthenticationError sans session."""
        account = Mock()
        
        with self.assertRaises(AuthenticationError):
            self.connector.sync_transactions(account)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_get_balance_success(self, mock_playwright):
        """Test récupération du solde réussie."""
        # Setup: Authentifier d'abord
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        mock_page.url = "https://www.boursorama.com/espace-client"
        mock_browser.contexts = [MagicMock()]
        mock_browser.contexts[0].cookies.return_value = []
        
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        self.connector.authenticate(self.credentials)
        
        # Mock de la page avec solde
        mock_balance_elem = MagicMock()
        mock_balance_elem.inner_text.return_value = "1500,50 €"
        mock_page.query_selector_all.return_value = [mock_balance_elem]
        mock_page.inner_text.return_value = "Solde: 1500,50 €"
        
        account = Mock()
        balance = self.connector.get_balance(account)
        
        self.assertIsInstance(balance, Decimal)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_get_balance_no_session(self, mock_playwright):
        """Test que get_balance lève AuthenticationError sans session."""
        account = Mock()
        
        with self.assertRaises(AuthenticationError):
            self.connector.get_balance(account)

    @patch('finance.connectors.boursorama.sync_playwright')
    def test_disconnect(self, mock_playwright):
        """Test fermeture propre de la connexion."""
        # Setup: Authentifier d'abord
        mock_playwright_instance = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        
        mock_playwright.return_value.__enter__.return_value = mock_playwright_instance
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        mock_page.url = "https://www.boursorama.com/espace-client"
        mock_browser.contexts = [MagicMock()]
        mock_browser.contexts[0].cookies.return_value = []
        
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()
        mock_page.query_selector.side_effect = lambda selector: {
            'input[name="username"]': mock_username_input,
            'input[type="password"]': mock_password_input,
        }.get(selector, None)
        
        self.connector.authenticate(self.credentials)
        
        self.connector.disconnect()
        
        self.assertFalse(self.connector._authenticated)
        self.assertIsNone(self.connector.browser)
        self.assertIsNone(self.connector.page)

    def test_parse_date(self):
        """Test parsing de dates."""
        # Test différents formats de date
        test_cases = [
            ("01/01/2025", datetime(2025, 1, 1)),
            ("15-01-2025", datetime(2025, 1, 15)),
            ("2025-01-01", datetime(2025, 1, 1)),
        ]
        
        for date_str, expected in test_cases:
            result = self.connector._parse_date(date_str)
            self.assertIsNotNone(result)
            self.assertEqual(result.date(), expected.date())

    def test_extract_amount_from_text(self):
        """Test extraction de montant depuis un texte."""
        test_cases = [
            ("1500,50 €", Decimal("1500.50")),
            ("€ 1500,50", Decimal("1500.50")),
            ("-100,00", Decimal("-100.00")),
        ]
        
        for text, expected in test_cases:
            result = self.connector._extract_amount_from_text(text)
            self.assertIsNotNone(result)
            self.assertEqual(result, expected)

    def test_looks_like_amount(self):
        """Test détection de montant."""
        self.assertTrue(self.connector._looks_like_amount("1500,50 €"))
        self.assertTrue(self.connector._looks_like_amount("-100,00"))
        self.assertFalse(self.connector._looks_like_amount("Transaction"))

    def test_looks_like_date(self):
        """Test détection de date."""
        self.assertTrue(self.connector._looks_like_date("01/01/2025"))
        self.assertTrue(self.connector._looks_like_date("2025-01-01"))
        self.assertFalse(self.connector._looks_like_amount("Transaction"))
