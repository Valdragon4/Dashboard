"""
Tests unitaires pour les modèles BankConnection et SyncLog.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from finance.models import Account, BankConnection, SyncLog
from finance.services.encryption_service import EncryptionService

User = get_user_model()


class TestBankConnection(TestCase):
    """Tests pour le modèle BankConnection."""

    def setUp(self):
        """Configure l'environnement de test."""
        import os

        # Générer une clé de chiffrement pour les tests
        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.credentials = {"username": "test_user", "password": "secret123"}

    def test_create_bank_connection(self):
        """Test création d'une BankConnection."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
            auto_sync_enabled=True,
        )

        self.assertIsNotNone(connection.id)
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(connection.provider, BankConnection.Provider.TRADE_REPUBLIC)
        self.assertEqual(connection.account_name, "Test Account")
        self.assertTrue(connection.auto_sync_enabled)
        self.assertEqual(connection.sync_status, BankConnection.SyncStatus.PENDING)

    def test_bank_connection_str(self):
        """Test représentation string de BankConnection."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.BOURSORAMA,
            account_name="Mon Compte",
            encrypted_credentials=encrypted_credentials,
        )

        str_repr = str(connection)
        self.assertIn("Mon Compte", str_repr)
        self.assertIn("BoursoBank", str_repr)

    def test_bank_connection_decrypt_credentials(self):
        """Test déchiffrement des credentials depuis BankConnection."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
        )

        decrypted = EncryptionService.decrypt_credentials(connection.encrypted_credentials)
        self.assertEqual(decrypted, self.credentials)
        self.assertEqual(decrypted["username"], "test_user")

    def test_bank_connection_provider_choices(self):
        """Test que les choix de provider fonctionnent."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        for provider_value, provider_label in BankConnection.Provider.choices:
            connection = BankConnection.objects.create(
                owner=self.user,
                provider=provider_value,
                account_name=f"Account {provider_label}",
                encrypted_credentials=encrypted_credentials,
            )
            self.assertEqual(connection.provider, provider_value)
            connection.delete()

    def test_bank_connection_sync_status_choices(self):
        """Test que les choix de sync_status fonctionnent."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
            sync_status=BankConnection.SyncStatus.SYNCING,
        )

        self.assertEqual(connection.sync_status, BankConnection.SyncStatus.SYNCING)

        connection.sync_status = BankConnection.SyncStatus.SUCCESS
        connection.save()
        self.assertEqual(connection.sync_status, BankConnection.SyncStatus.SUCCESS)

    def test_bank_connection_last_sync_at(self):
        """Test mise à jour de last_sync_at."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
        )

        self.assertIsNone(connection.last_sync_at)

        now = timezone.now()
        connection.last_sync_at = now
        connection.save()

        connection.refresh_from_db()
        self.assertIsNotNone(connection.last_sync_at)


class TestSyncLog(TestCase):
    """Tests pour le modèle SyncLog."""

    def setUp(self):
        """Configure l'environnement de test."""
        import os

        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.credentials = {"username": "test_user", "password": "secret123"}

        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)
        self.bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
        )

    def test_create_sync_log(self):
        """Test création d'un SyncLog."""
        sync_log = SyncLog.objects.create(
            bank_connection=self.bank_connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.STARTED,
        )

        self.assertIsNotNone(sync_log.id)
        self.assertEqual(sync_log.bank_connection, self.bank_connection)
        self.assertEqual(sync_log.sync_type, SyncLog.SyncType.AUTOMATIC)
        self.assertEqual(sync_log.status, SyncLog.Status.STARTED)
        self.assertEqual(sync_log.transactions_count, 0)
        self.assertIsNone(sync_log.completed_at)

    def test_sync_log_str(self):
        """Test représentation string de SyncLog."""
        sync_log = SyncLog.objects.create(
            bank_connection=self.bank_connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.SUCCESS,
            transactions_count=5,
        )

        str_repr = str(sync_log)
        self.assertIn("Test Account", str_repr)
        self.assertIn("Manuelle", str_repr)
        self.assertIn("Succès", str_repr)

    def test_sync_log_completed_at(self):
        """Test mise à jour de completed_at."""
        sync_log = SyncLog.objects.create(
            bank_connection=self.bank_connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.STARTED,
        )

        self.assertIsNone(sync_log.completed_at)

        now = timezone.now()
        sync_log.completed_at = now
        sync_log.status = SyncLog.Status.SUCCESS
        sync_log.save()

        sync_log.refresh_from_db()
        self.assertIsNotNone(sync_log.completed_at)

    def test_sync_log_error_message(self):
        """Test enregistrement d'un message d'erreur."""
        error_msg = "Connection timeout"

        sync_log = SyncLog.objects.create(
            bank_connection=self.bank_connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.ERROR,
            error_message=error_msg,
        )

        self.assertEqual(sync_log.error_message, error_msg)
        self.assertEqual(sync_log.status, SyncLog.Status.ERROR)

    def test_sync_log_relationship(self):
        """Test relation entre SyncLog et BankConnection."""
        sync_log1 = SyncLog.objects.create(
            bank_connection=self.bank_connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            transactions_count=10,
        )

        sync_log2 = SyncLog.objects.create(
            bank_connection=self.bank_connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.SUCCESS,
            transactions_count=5,
        )

        # Vérifier la relation inverse
        logs = self.bank_connection.sync_logs.all()
        self.assertEqual(logs.count(), 2)
        self.assertIn(sync_log1, logs)
        self.assertIn(sync_log2, logs)


class TestAccountExtension(TestCase):
    """Tests pour l'extension du modèle Account."""

    def setUp(self):
        """Configure l'environnement de test."""
        import os

        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.credentials = {"username": "test_user", "password": "secret123"}

    def test_account_without_bank_connection(self):
        """Test qu'un Account peut exister sans BankConnection (IV1)."""
        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
        )

        self.assertIsNone(account.bank_connection)
        self.assertFalse(account.auto_sync_enabled)
        self.assertIsNotNone(account.id)

    def test_account_with_bank_connection(self):
        """Test création d'un Account avec BankConnection."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Bank Account",
            encrypted_credentials=encrypted_credentials,
        )

        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            bank_connection=bank_connection,
            auto_sync_enabled=True,
        )

        self.assertEqual(account.bank_connection, bank_connection)
        self.assertTrue(account.auto_sync_enabled)

    def test_account_auto_sync_enabled_independent(self):
        """Test que auto_sync_enabled sur Account fonctionne indépendamment."""
        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            auto_sync_enabled=True,
        )

        self.assertTrue(account.auto_sync_enabled)
        self.assertIsNone(account.bank_connection)

    def test_account_bank_connection_cascade_delete(self):
        """Test que la suppression d'un BankConnection met à jour Account."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Bank Account",
            encrypted_credentials=encrypted_credentials,
        )

        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            bank_connection=bank_connection,
        )

        self.assertEqual(account.bank_connection, bank_connection)

        # Supprimer la connexion bancaire
        bank_connection.delete()

        # Recharger l'account depuis la DB
        account.refresh_from_db()
        self.assertIsNone(account.bank_connection)

    def test_account_relationship_reverse(self):
        """Test relation inverse Account -> BankConnection."""
        encrypted_credentials = EncryptionService.encrypt_credentials(self.credentials)

        bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Bank Account",
            encrypted_credentials=encrypted_credentials,
        )

        account1 = Account.objects.create(
            owner=self.user,
            name="Account 1",
            type=Account.AccountType.CHECKING,
            bank_connection=bank_connection,
        )

        account2 = Account.objects.create(
            owner=self.user,
            name="Account 2",
            type=Account.AccountType.SAVINGS,
            bank_connection=bank_connection,
        )

        # Vérifier la relation inverse
        accounts = bank_connection.accounts.all()
        self.assertEqual(accounts.count(), 2)
        self.assertIn(account1, accounts)
        self.assertIn(account2, accounts)
