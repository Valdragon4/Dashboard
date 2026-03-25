"""
Tests unitaires pour le service de synchronisation bancaire.

Ces tests utilisent des mocks pour éviter les appels réels aux connecteurs et à EncryptionService.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from finance.services.sync_service import SyncService
from finance.models import Account, BankConnection, SyncLog, Transaction
from finance.services.encryption_service import EncryptionService, EncryptionError
from finance.connectors.base import (
    AuthenticationError,
    InvalidCredentialsError,
    ConnectionTimeoutError,
    RateLimitError,
)

User = get_user_model()


class TestSyncService(TestCase):
    """Tests pour le service de synchronisation."""

    def setUp(self):
        """Configure l'environnement de test."""
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            auto_sync_enabled=True,
        )
        self.bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials="encrypted_creds",
            auto_sync_enabled=True,
        )
        self.account.bank_connection = self.bank_connection
        self.account.save()

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_success_trade_republic(self, mock_connector_class, mock_decrypt):
        """Test synchronisation réussie avec Trade Republic."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"token": "test_token"}
        mock_connector.sync_transactions.return_value = [
            {
                "posted_at": timezone.now(),
                "amount": Decimal("100.00"),
                "description": "Test transaction",
                "raw": {"transaction_id": "123", "source": "trade_republic"},
            }
        ]
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertTrue(result["success"])
        self.assertEqual(result["transactions_count"], 1)
        self.assertIsNotNone(result["sync_log_id"])

        # Vérifier que SyncLog a été créé
        sync_log = SyncLog.objects.get(id=result["sync_log_id"])
        self.assertEqual(sync_log.status, SyncLog.Status.SUCCESS)
        self.assertEqual(sync_log.transactions_count, 1)

        # Vérifier que BankConnection a été mise à jour
        self.bank_connection.refresh_from_db()
        self.assertEqual(self.bank_connection.sync_status, BankConnection.SyncStatus.SUCCESS)
        self.assertIsNotNone(self.bank_connection.last_sync_at)

        # Vérifier que la transaction a été créée
        transaction = Transaction.objects.get(account=self.account)
        self.assertEqual(transaction.amount, Decimal("100.00"))
        self.assertEqual(transaction.raw["source"], "trade_republic")

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.BoursoBankConnector")
    @patch("finance.services.sync_service.PLAYWRIGHT_AVAILABLE", True)
    def test_sync_account_success_boursorama(self, mock_connector_class, mock_decrypt):
        """Test synchronisation réussie avec BoursoBank."""
        # Changer le provider
        self.bank_connection.provider = BankConnection.Provider.BOURSORAMA
        self.bank_connection.save()

        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "username": "test_user",
            "password": "test_password",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"session_id": "test_session"}
        mock_connector.sync_transactions.return_value = [
            {
                "posted_at": timezone.now(),
                "amount": Decimal("-50.00"),
                "description": "Test transaction BoursoBank",
                "raw": {"source": "boursorama"},
            }
        ]
        mock_connector.get_balance.return_value = Decimal("500.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertTrue(result["success"])
        self.assertEqual(result["transactions_count"], 1)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.HelloBankConnector")
    @patch("finance.services.sync_service.PLAYWRIGHT_AVAILABLE", True)
    def test_sync_account_success_hellobank(self, mock_connector_class, mock_decrypt):
        """Test synchronisation réussie avec Hello Bank."""
        # Changer le provider
        self.bank_connection.provider = BankConnection.Provider.HELLOBANK
        self.bank_connection.save()

        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "username": "test_user",
            "password": "test_password",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"session_id": "test_session"}
        mock_connector.sync_transactions.return_value = [
            {
                "posted_at": timezone.now(),
                "amount": Decimal("-25.00"),
                "description": "Test transaction Hello Bank",
                "raw": {"source": "hellobank"},
            }
        ]
        mock_connector.get_balance.return_value = Decimal("250.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertTrue(result["success"])
        self.assertEqual(result["transactions_count"], 1)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_detection_doublons(self, mock_connector_class, mock_decrypt):
        """Test détection de doublons (transaction déjà existante)."""
        # Créer une transaction existante avec transaction_id
        existing_transaction = Transaction.objects.create(
            account=self.account,
            posted_at=timezone.now(),
            amount=Decimal("100.00"),
            description="Test transaction",
            raw={"transaction_id": "123", "source": "trade_republic"},
        )

        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"token": "test_token"}
        mock_connector.sync_transactions.return_value = [
            {
                "posted_at": timezone.now(),
                "amount": Decimal("100.00"),
                "description": "Test transaction updated",
                "raw": {"transaction_id": "123", "source": "trade_republic"},
            }
        ]
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertTrue(result["success"])
        self.assertEqual(result["transactions_count"], 1)

        # Vérifier que la transaction existante a été mise à jour (pas créée en double)
        transactions = Transaction.objects.filter(account=self.account)
        self.assertEqual(transactions.count(), 1)
        updated_transaction = transactions.first()
        self.assertEqual(updated_transaction.description, "Test transaction updated")

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_filtrage_date_since(self, mock_connector_class, mock_decrypt):
        """Test filtrage par date since (ne synchronise que les nouvelles transactions)."""
        # Définir une date de dernière synchronisation
        last_sync = timezone.now() - timedelta(days=7)
        self.bank_connection.last_sync_at = last_sync
        self.bank_connection.save()

        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"token": "test_token"}
        # Retourner des transactions avec des dates différentes
        mock_connector.sync_transactions.return_value = [
            {
                "posted_at": timezone.now() - timedelta(days=10),  # Ancienne transaction
                "amount": Decimal("50.00"),
                "description": "Old transaction",
                "raw": {"transaction_id": "old1", "source": "trade_republic"},
            },
            {
                "posted_at": timezone.now() - timedelta(days=3),  # Nouvelle transaction
                "amount": Decimal("100.00"),
                "description": "New transaction",
                "raw": {"transaction_id": "new1", "source": "trade_republic"},
            },
        ]
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifier que sync_transactions a été appelé avec since
        mock_connector.sync_transactions.assert_called_once()
        call_args = mock_connector.sync_transactions.call_args
        self.assertIsNotNone(call_args.kwargs.get("since"))

        # Vérifications
        self.assertTrue(result["success"])
        # Les deux transactions devraient être synchronisées (le filtrage par date est fait par le connecteur)
        self.assertEqual(result["transactions_count"], 2)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_creation_synclog_success(self, mock_connector_class, mock_decrypt):
        """Test création de SyncLog en cas de succès."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"token": "test_token"}
        mock_connector.sync_transactions.return_value = []
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifier que SyncLog a été créé avec les bons attributs
        sync_log = SyncLog.objects.get(id=result["sync_log_id"])
        self.assertEqual(sync_log.status, SyncLog.Status.SUCCESS)
        self.assertEqual(sync_log.sync_type, SyncLog.SyncType.AUTOMATIC)
        self.assertIsNotNone(sync_log.started_at)
        self.assertIsNotNone(sync_log.completed_at)
        self.assertEqual(sync_log.transactions_count, 0)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_creation_synclog_error(self, mock_connector_class, mock_decrypt):
        """Test création de SyncLog en cas d'erreur."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur qui lève une erreur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.side_effect = InvalidCredentialsError("Invalid credentials")

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertFalse(result["success"])
        self.assertIsNotNone(result["sync_log_id"])
        self.assertIsNotNone(result["error"])

        # Vérifier que SyncLog a été créé avec les bons attributs
        sync_log = SyncLog.objects.get(id=result["sync_log_id"])
        self.assertEqual(sync_log.status, SyncLog.Status.ERROR)
        self.assertIsNotNone(sync_log.started_at)
        self.assertIsNotNone(sync_log.completed_at)
        self.assertIsNotNone(sync_log.error_message)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_update_bank_connection(self, mock_connector_class, mock_decrypt):
        """Test mise à jour de BankConnection (sync_status, last_sync_at)."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"token": "test_token"}
        mock_connector.sync_transactions.return_value = []
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Vérifier l'état initial
        self.assertEqual(self.bank_connection.sync_status, BankConnection.SyncStatus.PENDING)
        self.assertIsNone(self.bank_connection.last_sync_at)

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifier que BankConnection a été mise à jour
        self.bank_connection.refresh_from_db()
        self.assertEqual(self.bank_connection.sync_status, BankConnection.SyncStatus.SUCCESS)
        self.assertIsNotNone(self.bank_connection.last_sync_at)

    def test_sync_account_missing_bank_connection(self):
        """Test erreur si bank_connection manquant (lève ValueError)."""
        # Retirer la bank_connection
        self.account.bank_connection = None
        self.account.save()

        # Appeler le service
        with self.assertRaises(ValueError) as context:
            SyncService.sync_account(self.account)

        self.assertIn("n'a pas de connexion bancaire associée", str(context.exception))

    def test_sync_account_auto_sync_disabled(self):
        """Test early return si auto_sync_enabled=False."""
        # Désactiver la synchronisation automatique
        self.account.auto_sync_enabled = False
        self.account.save()

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertFalse(result["success"])
        self.assertEqual(result["transactions_count"], 0)
        self.assertIsNone(result["sync_log_id"])
        self.assertIn("désactivée", result["error"])

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_requires_2fa(self, mock_connector_class, mock_decrypt):
        """Test authentification 2FA requise (retourne {"requires_2fa": True})."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            # Pas de 2fa_code
        }

        # Mock du connecteur qui retourne requires_2fa
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {
            "process_id": "test_process_id",
            "countdown": 60,
            "requires_2fa": True,
        }

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertFalse(result["success"])
        self.assertTrue(result["requires_2fa"])
        self.assertIn("2FA", result["error"])

        # Vérifier que BankConnection a été mise à jour avec ERROR
        self.bank_connection.refresh_from_db()
        self.assertEqual(self.bank_connection.sync_status, BankConnection.SyncStatus.ERROR)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_retry_on_temporary_error(self, mock_connector_class, mock_decrypt):
        """Test retry automatique en cas d'erreur temporaire."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur qui lève une erreur temporaire puis réussit
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.side_effect = [
            ConnectionTimeoutError("Timeout"),
            {"token": "test_token"},
        ]
        mock_connector.sync_transactions.return_value = []
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {}

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertTrue(result["success"])
        # Vérifier que authenticate a été appelé 2 fois (1 retry)
        self.assertEqual(mock_connector.authenticate.call_count, 2)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_no_retry_on_definitive_error(self, mock_connector_class, mock_decrypt):
        """Test pas de retry en cas d'erreur définitive (InvalidCredentialsError)."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur qui lève une erreur définitive
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.side_effect = InvalidCredentialsError("Invalid credentials")

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertFalse(result["success"])
        # Vérifier que authenticate a été appelé seulement 1 fois (pas de retry)
        self.assertEqual(mock_connector.authenticate.call_count, 1)

    @patch("finance.services.sync_service.EncryptionService.decrypt_credentials")
    @patch("finance.services.sync_service.TradeRepublicConnector")
    def test_sync_account_portfolio_valuations(self, mock_connector_class, mock_decrypt):
        """Test synchronisation des valorisations de portefeuille pour Trade Republic."""
        # Mock des credentials déchiffrés
        mock_decrypt.return_value = {
            "phone_number": "+33123456789",
            "pin": "1234",
            "2fa_code": "123456",
        }

        # Mock du connecteur
        mock_connector = Mock()
        mock_connector_class.return_value = mock_connector
        mock_connector.authenticate.return_value = {"token": "test_token"}
        mock_connector.sync_transactions.return_value = []
        mock_connector.get_balance.return_value = Decimal("1000.00")
        mock_connector.sync_portfolio_valuations.return_value = {
            "PEA": Decimal("5000.00"),
            "CTO": Decimal("3000.00"),
            "CRYPTO": Decimal("1000.00"),
        }

        # Appeler le service
        result = SyncService.sync_account(self.account)

        # Vérifications
        self.assertTrue(result["success"])
        self.assertIn("portfolio_valuations", result)
        self.assertEqual(result["portfolio_valuations"]["PEA"], Decimal("5000.00"))
        self.assertEqual(result["portfolio_valuations"]["CTO"], Decimal("3000.00"))
        self.assertEqual(result["portfolio_valuations"]["CRYPTO"], Decimal("1000.00"))

    def test_upsert_transaction_from_sync_new_transaction(self):
        """Test création de nouvelle transaction."""
        transaction_data = {
            "posted_at": timezone.now(),
            "amount": Decimal("100.00"),
            "description": "Test transaction",
            "raw": {"transaction_id": "123", "source": "trade_republic"},
        }

        # Appeler la méthode
        transaction = SyncService._upsert_transaction_from_sync(
            self.account, transaction_data, "trade_republic"
        )

        # Vérifications
        self.assertIsNotNone(transaction.id)
        self.assertEqual(transaction.amount, Decimal("100.00"))
        self.assertEqual(transaction.raw["source"], "trade_republic")
        self.assertEqual(transaction.raw["transaction_id"], "123")

    def test_upsert_transaction_from_sync_update_existing(self):
        """Test mise à jour de transaction existante (doublon détecté)."""
        # Créer une transaction existante
        posted_at = timezone.now()
        existing_transaction = Transaction.objects.create(
            account=self.account,
            posted_at=posted_at,
            amount=Decimal("100.00"),
            description="Old description",
            raw={"transaction_id": "123", "source": "trade_republic"},
        )

        transaction_data = {
            "posted_at": posted_at,
            "amount": Decimal("100.00"),
            "description": "New description",
            "raw": {"transaction_id": "123", "source": "trade_republic"},
        }

        # Appeler la méthode
        transaction = SyncService._upsert_transaction_from_sync(
            self.account, transaction_data, "trade_republic"
        )

        # Vérifications
        self.assertEqual(transaction.id, existing_transaction.id)
        self.assertEqual(transaction.description, "New description")
        # Vérifier qu'il n'y a qu'une seule transaction
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 1)

    def test_upsert_transaction_from_sync_deduplication_by_transaction_id(self):
        """Test déduplication par transaction_id dans raw."""
        # Créer une transaction existante avec transaction_id
        existing_transaction = Transaction.objects.create(
            account=self.account,
            posted_at=timezone.now(),
            amount=Decimal("100.00"),
            description="Existing transaction",
            raw={"transaction_id": "unique_123", "source": "trade_republic"},
        )

        transaction_data = {
            "posted_at": timezone.now() + timedelta(days=1),  # Date différente
            "amount": Decimal("200.00"),  # Montant différent
            "description": "Different description",
            "raw": {"transaction_id": "unique_123", "source": "trade_republic"},
        }

        # Appeler la méthode
        transaction = SyncService._upsert_transaction_from_sync(
            self.account, transaction_data, "trade_republic"
        )

        # Vérifications : la transaction existante doit être mise à jour (même transaction_id)
        self.assertEqual(transaction.id, existing_transaction.id)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 1)

    def test_upsert_transaction_from_sync_deduplication_by_fallback(self):
        """Test déduplication par posted_at + amount + description (fallback)."""
        # Créer une transaction existante sans transaction_id
        posted_at = timezone.now()
        existing_transaction = Transaction.objects.create(
            account=self.account,
            posted_at=posted_at,
            amount=Decimal("100.00"),
            description="Test transaction",
            raw={"source": "boursorama"},
        )

        transaction_data = {
            "posted_at": posted_at,  # Même date exacte
            "amount": Decimal("100.00"),  # Même montant
            "description": "Test transaction",  # Même description
            "raw": {"source": "boursorama"},
        }

        # Appeler la méthode
        transaction = SyncService._upsert_transaction_from_sync(
            self.account, transaction_data, "boursorama"
        )

        # Vérifications : la transaction existante doit être mise à jour
        self.assertEqual(transaction.id, existing_transaction.id)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 1)
