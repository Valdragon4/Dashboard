"""
Tests unitaires pour les tâches Celery de synchronisation bancaire.

Ces tests utilisent des mocks pour éviter les appels réels à SyncService.
"""

import os
from unittest.mock import Mock, patch, MagicMock
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone as django_timezone

from finance.tasks import sync_all_bank_accounts, sync_bank_account, cleanup_old_sync_logs
from finance.models import Account, BankConnection, SyncLog
from finance.services.sync_service import SyncService
from finance.services.encryption_service import EncryptionService
from finance.connectors.base import ConnectionTimeoutError, InvalidCredentialsError

User = get_user_model()


class TestCeleryTasks(TestCase):
    """Tests pour les tâches Celery de synchronisation."""

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

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_all_bank_accounts_success(self, mock_sync):
        """Test synchronisation de tous les comptes activés."""
        mock_sync.return_value = {
            "success": True,
            "transactions_count": 5,
            "sync_log_id": 1,
        }

        result = sync_all_bank_accounts()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(len(result["errors_details"]), 0)
        mock_sync.assert_called_once_with(
            self.account, sync_type=SyncLog.SyncType.AUTOMATIC
        )

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_all_bank_accounts_ignores_disabled(self, mock_sync):
        """Test ignore des comptes avec auto_sync_enabled=False."""
        # Désactiver la synchronisation automatique
        self.account.auto_sync_enabled = False
        self.account.save()

        result = sync_all_bank_accounts()

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["success"], 0)
        mock_sync.assert_not_called()

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_all_bank_accounts_ignores_no_bank_connection(self, mock_sync):
        """Test ignore des comptes sans bank_connection."""
        # Retirer la bank_connection
        self.account.bank_connection = None
        self.account.save()

        result = sync_all_bank_accounts()

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["success"], 0)
        mock_sync.assert_not_called()

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_all_bank_accounts_continues_on_error(self, mock_sync):
        """Test gestion des erreurs (un compte échoue, les autres continuent)."""
        # Créer un deuxième compte
        account2 = Account.objects.create(
            owner=self.user,
            name="Test Account 2",
            type=Account.AccountType.CHECKING,
            auto_sync_enabled=True,
        )
        bank_connection2 = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.BOURSORAMA,
            account_name="Test Account 2",
            encrypted_credentials="encrypted_creds2",
            auto_sync_enabled=True,
        )
        account2.bank_connection = bank_connection2
        account2.save()

        # Premier compte réussit, deuxième échoue
        mock_sync.side_effect = [
            {"success": True, "transactions_count": 5, "sync_log_id": 1},
            {"success": False, "error": "Test error", "sync_log_id": 2},
        ]

        result = sync_all_bank_accounts()

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(len(result["errors_details"]), 1)
        self.assertEqual(result["errors_details"][0]["account_id"], account2.id)
        self.assertEqual(mock_sync.call_count, 2)

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_all_bank_accounts_handles_exception(self, mock_sync):
        """Test gestion des exceptions (un compte lève une exception)."""
        mock_sync.side_effect = Exception("Test exception")

        result = sync_all_bank_accounts()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(len(result["errors_details"]), 1)
        self.assertIn("Test exception", result["errors_details"][0]["error"])

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_bank_account_success(self, mock_sync):
        """Test synchronisation réussie d'un compte spécifique."""
        mock_sync.return_value = {
            "success": True,
            "transactions_count": 5,
            "sync_log_id": 1,
        }

        result = sync_bank_account(self.account.id)

        self.assertTrue(result["success"])
        self.assertEqual(result["transactions_count"], 5)
        mock_sync.assert_called_once_with(
            self.account, sync_type=SyncLog.SyncType.AUTOMATIC
        )

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_bank_account_with_manual_type(self, mock_sync):
        """Test synchronisation avec sync_type='manual'."""
        mock_sync.return_value = {
            "success": True,
            "transactions_count": 5,
            "sync_log_id": 1,
        }

        result = sync_bank_account(self.account.id, sync_type=SyncLog.SyncType.MANUAL)

        self.assertTrue(result["success"])
        mock_sync.assert_called_once_with(
            self.account, sync_type=SyncLog.SyncType.MANUAL
        )

    def test_sync_bank_account_account_not_found(self):
        """Test erreur si compte n'existe pas."""
        with self.assertRaises(Account.DoesNotExist):
            sync_bank_account(99999)

    def test_sync_bank_account_no_bank_connection(self):
        """Test erreur si compte n'a pas de bank_connection."""
        self.account.bank_connection = None
        self.account.save()

        with self.assertRaises(ValueError) as context:
            sync_bank_account(self.account.id)

        self.assertIn("n'a pas de connexion bancaire associée", str(context.exception))

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_bank_account_handles_timeout_error(self, mock_sync):
        """Test retry automatique en cas d'erreur temporaire (ConnectionTimeoutError)."""
        mock_sync.side_effect = ConnectionTimeoutError("Connection timeout")

        # La tâche doit lever l'exception pour que Celery puisse retry
        with self.assertRaises(ConnectionTimeoutError):
            sync_bank_account(self.account.id)

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_bank_account_no_retry_on_invalid_credentials(self, mock_sync):
        """Test pas de retry en cas d'erreur définitive (InvalidCredentialsError)."""
        mock_sync.side_effect = InvalidCredentialsError("Invalid credentials")

        # La tâche doit lever l'exception mais ne sera pas retentée par Celery
        # car InvalidCredentialsError n'est pas dans autoretry_for
        with self.assertRaises(InvalidCredentialsError):
            sync_bank_account(self.account.id)

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_bank_account_handles_requires_2fa(self, mock_sync):
        """Test gestion de l'authentification 2FA requise."""
        mock_sync.return_value = {
            "success": False,
            "requires_2fa": True,
            "error": "Authentification 2FA requise",
            "sync_log_id": 1,
        }

        result = sync_bank_account(self.account.id)

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_2fa"])
        self.assertIn("2FA", result["error"])

    @patch("finance.tasks.SyncService.sync_account")
    def test_sync_bank_account_logging(self, mock_sync):
        """Test logging des résultats."""
        mock_sync.return_value = {
            "success": True,
            "transactions_count": 10,
            "sync_log_id": 1,
        }

        with patch("finance.tasks.logger") as mock_logger:
            result = sync_bank_account(self.account.id)

            # Vérifier que les logs appropriés sont appelés
            mock_logger.info.assert_called()
            # Vérifier qu'un log de succès est appelé
            success_calls = [
                call
                for call in mock_logger.info.call_args_list
                if "réussie" in str(call)
            ]
            self.assertGreater(len(success_calls), 0)


class TestCleanupOldSyncLogs(TestCase):
    """Tests pour la tâche de nettoyage des anciens logs."""

    @classmethod
    def setUpClass(cls):
        """Configuration initiale pour tous les tests."""
        super().setUpClass()
        cls.test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = cls.test_key

    @classmethod
    def tearDownClass(cls):
        """Nettoyage après tous les tests."""
        super().tearDownClass()
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    def setUp(self):
        """Configuration avant chaque test."""
        self.user = User.objects.create_user(username="testuser", password="testpass")
        credentials = {"username": "test_user", "password": "secret123"}
        encrypted_credentials = EncryptionService.encrypt_credentials(credentials)
        self.connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
        )

    def test_cleanup_old_sync_logs_deletes_old_logs(self):
        """Test suppression des logs anciens selon la rétention."""
        # Créer des logs avec des dates vraiment anciennes pour garantir la suppression
        # même avec un léger décalage temporel entre la création et le nettoyage
        # Log ancien : 100 jours avant maintenant (largement > 30 jours de rétention)
        old_date = django_timezone.now() - timedelta(days=100)
        old_log = SyncLog.objects.create(
            bank_connection=self.connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=old_date,
            completed_at=old_date + timedelta(minutes=5),
            transactions_count=10,
        )
        # Log récent : 10 jours avant maintenant (doit être conservé)
        recent_date = django_timezone.now() - timedelta(days=10)
        recent_log = SyncLog.objects.create(
            bank_connection=self.connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=recent_date,
            completed_at=recent_date + timedelta(minutes=5),
            transactions_count=5,
        )

        # Configurer la rétention à 30 jours
        # Note: Le mock de timezone.now() ne fonctionne pas car timezone est importé
        # localement dans cleanup_old_sync_logs. On utilise donc des dates vraiment
        # anciennes (100 jours) pour garantir la suppression même avec décalage temporel.
        with patch.dict(os.environ, {"SYNC_LOG_RETENTION_DAYS": "30"}):
            result = cleanup_old_sync_logs()

        # Vérifier que le log ancien est supprimé (100 jours >> 30 jours de rétention)
        self.assertFalse(SyncLog.objects.filter(id=old_log.id).exists())
        # Vérifier que le log récent est conservé (10 jours < 30 jours de rétention)
        self.assertTrue(SyncLog.objects.filter(id=recent_log.id).exists())
        # Vérifier le résultat
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["retention_days"], 30)

    def test_cleanup_old_sync_logs_preserves_recent_logs(self):
        """Test conservation des logs récents."""
        # Créer des logs récents (moins de 30 jours)
        for days_ago in [5, 10, 20, 29]:
            log_date = django_timezone.now() - timedelta(days=days_ago)
            SyncLog.objects.create(
                bank_connection=self.connection,
                sync_type=SyncLog.SyncType.AUTOMATIC,
                status=SyncLog.Status.SUCCESS,
                started_at=log_date,
                completed_at=log_date + timedelta(minutes=5),
                transactions_count=1,
            )

        with patch.dict(os.environ, {"SYNC_LOG_RETENTION_DAYS": "30"}):
            result = cleanup_old_sync_logs()

        # Vérifier que tous les logs récents sont conservés (tous < 30 jours)
        self.assertEqual(SyncLog.objects.count(), 4)
        self.assertEqual(result["deleted_count"], 0)

    def test_cleanup_old_sync_logs_disabled_when_retention_zero(self):
        """Test désactivation du nettoyage quand rétention = 0."""
        fixed_now = django_timezone.now()
        # Créer des logs très anciens
        old_log = SyncLog.objects.create(
            bank_connection=self.connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=fixed_now - timedelta(days=100),
            completed_at=fixed_now - timedelta(days=100, minutes=5),
            transactions_count=10,
        )

        # Configurer la rétention à 0 (désactivée)
        with patch.dict(os.environ, {"SYNC_LOG_RETENTION_DAYS": "0"}):
            result = cleanup_old_sync_logs()

        # Vérifier qu'aucun log n'est supprimé
        self.assertTrue(SyncLog.objects.filter(id=old_log.id).exists())
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["retention_days"], 0)

    def test_cleanup_old_sync_logs_default_retention(self):
        """Test utilisation de la rétention par défaut (30 jours) si non configurée."""
        # Créer un log vraiment ancien pour garantir la suppression même avec décalage temporel
        old_date = django_timezone.now() - timedelta(days=100)
        old_log = SyncLog.objects.create(
            bank_connection=self.connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=old_date,
            completed_at=old_date + timedelta(minutes=5),
            transactions_count=10,
        )

        # Ne pas définir SYNC_LOG_RETENTION_DAYS (utiliser la valeur par défaut)
        if "SYNC_LOG_RETENTION_DAYS" in os.environ:
            del os.environ["SYNC_LOG_RETENTION_DAYS"]

        result = cleanup_old_sync_logs()

        # Vérifier que le log ancien est supprimé (rétention par défaut = 30 jours, 100 jours >> 30)
        self.assertFalse(SyncLog.objects.filter(id=old_log.id).exists())
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["retention_days"], 30)

    def test_cleanup_old_sync_logs_invalid_retention(self):
        """Test gestion d'une rétention invalide (utilise la valeur par défaut)."""
        # Créer un log vraiment ancien pour garantir la suppression même avec décalage temporel
        old_date = django_timezone.now() - timedelta(days=100)
        old_log = SyncLog.objects.create(
            bank_connection=self.connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=old_date,
            completed_at=old_date + timedelta(minutes=5),
            transactions_count=10,
        )

        # Configurer une rétention invalide
        with patch.dict(os.environ, {"SYNC_LOG_RETENTION_DAYS": "invalid"}):
            result = cleanup_old_sync_logs()

        # Vérifier que la valeur par défaut est utilisée (30 jours, 100 jours >> 30)
        self.assertFalse(SyncLog.objects.filter(id=old_log.id).exists())
        self.assertEqual(result["retention_days"], 30)
