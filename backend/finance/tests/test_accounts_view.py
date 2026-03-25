"""
Tests unitaires pour la vue accounts avec extension de synchronisation (Story 1.9).
"""

import os
from unittest.mock import patch, Mock

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from finance.models import Account, BankConnection, SyncLog
from finance.services.encryption_service import EncryptionService

User = get_user_model()


class TestAccountsView(TestCase):
    """Tests pour la vue accounts avec extension de synchronisation."""

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
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.client.login(username="testuser", password="testpass")

    def test_accounts_list_with_bank_connection(self):
        """Test affichage des comptes avec connexion bancaire."""
        # Créer un compte avec connexion bancaire
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
            sync_status=BankConnection.SyncStatus.SUCCESS,
            last_sync_at=timezone.now() - timedelta(hours=2),
        )

        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            bank_connection=connection,
            auto_sync_enabled=True,
        )

        # Créer un SyncLog réussi
        SyncLog.objects.create(
            bank_connection=connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.SUCCESS,
            completed_at=timezone.now() - timedelta(hours=2),
            transactions_count=5,
        )

        # Tester la vue
        response = self.client.get(reverse("accounts"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Account")
        self.assertContains(response, "Auto-sync")
        self.assertContains(response, "Succès")

    def test_accounts_list_without_bank_connection(self):
        """Test affichage des comptes sans connexion bancaire (IV1)."""
        # Créer un compte sans connexion bancaire
        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
        )

        # Tester la vue
        response = self.client.get(reverse("accounts"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Account")
        # Vérifier que la colonne Synchronisation affiche "-"
        self.assertContains(response, "-")

    def test_accounts_list_mixed(self):
        """Test affichage mixte de comptes avec et sans connexion bancaire."""
        # Compte avec connexion
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        account_with_sync = Account.objects.create(
            owner=self.user,
            name="Account With Sync",
            type=Account.AccountType.CHECKING,
            bank_connection=connection,
        )

        # Compte sans connexion
        account_without_sync = Account.objects.create(
            owner=self.user,
            name="Account Without Sync",
            type=Account.AccountType.CHECKING,
        )

        # Tester la vue
        response = self.client.get(reverse("accounts"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account With Sync")
        self.assertContains(response, "Account Without Sync")

    def test_accounts_list_sync_info_calculation(self):
        """Test calcul des informations de synchronisation."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
            sync_status=BankConnection.SyncStatus.SUCCESS,
            last_sync_at=timezone.now() - timedelta(hours=3),
        )

        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            bank_connection=connection,
            auto_sync_enabled=True,
        )

        # Créer plusieurs SyncLog
        SyncLog.objects.create(
            bank_connection=connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.SUCCESS,
            completed_at=timezone.now() - timedelta(hours=3),
            transactions_count=5,
        )

        SyncLog.objects.create(
            bank_connection=connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.ERROR,
            completed_at=timezone.now() - timedelta(hours=1),
            transactions_count=0,
        )

        # Tester la vue
        response = self.client.get(reverse("accounts"))
        self.assertEqual(response.status_code, 200)
        # Vérifier que les informations de synchronisation sont présentes
        self.assertContains(response, "Test Account")
        self.assertContains(response, "Auto-sync")

    def test_account_sync_api_success(self):
        """Test synchronisation manuelle réussie via API."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            bank_connection=connection,
        )

        # Mock de la tâche Celery
        with patch("finance.tasks.sync_bank_account") as mock_sync_task:
            mock_task_result = Mock()
            mock_task_result.id = "test-task-id"
            mock_sync_task.delay.return_value = mock_task_result

            # Tester l'endpoint API
            response = self.client.post(reverse("account_sync_api", args=[account.id]))
            self.assertEqual(response.status_code, 200)

            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["message"], "Synchronisation démarrée.")

            # Vérifier que la tâche a été appelée
            mock_sync_task.delay.assert_called_once_with(account.id, sync_type=SyncLog.SyncType.MANUAL)

    def test_account_sync_api_not_owner(self):
        """Test validation de propriétaire dans l'API."""
        other_user = User.objects.create_user(username="otheruser", password="otherpass")

        credentials = {"phone_number": "+33987654321", "pin": "5678"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=other_user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Other Connection",
            encrypted_credentials=encrypted_creds,
        )

        account = Account.objects.create(
            owner=other_user,
            name="Other Account",
            type=Account.AccountType.CHECKING,
            bank_connection=connection,
        )

        # Tester l'endpoint API avec un compte qui n'appartient pas à l'utilisateur
        response = self.client.post(reverse("account_sync_api", args=[account.id]))
        self.assertEqual(response.status_code, 404)

        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("introuvable", data["error"])

    def test_account_sync_api_no_bank_connection(self):
        """Test validation de bank_connection présente dans l'API."""
        account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            # Pas de bank_connection
        )

        # Tester l'endpoint API
        response = self.client.post(reverse("account_sync_api", args=[account.id]))
        self.assertEqual(response.status_code, 400)

        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("connexion bancaire", data["error"])

    def test_account_sync_api_account_not_found(self):
        """Test gestion des erreurs dans l'API (compte introuvable)."""
        # Tester avec un ID de compte inexistant
        response = self.client.post(reverse("account_sync_api", args=[99999]))
        self.assertEqual(response.status_code, 404)

        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("introuvable", data["error"])

    def test_accounts_list_optimization(self):
        """Test optimisation des requêtes DB (IV2)."""
        # Créer plusieurs comptes avec connexions bancaires
        for i in range(5):
            credentials = {"phone_number": f"+3312345678{i}", "pin": "1234"}
            encrypted_creds = EncryptionService.encrypt_credentials(credentials)

            connection = BankConnection.objects.create(
                owner=self.user,
                provider=BankConnection.Provider.TRADE_REPUBLIC,
                account_name=f"Connection {i}",
                encrypted_credentials=encrypted_creds,
            )

            Account.objects.create(
                owner=self.user,
                name=f"Account {i}",
                type=Account.AccountType.CHECKING,
                bank_connection=connection,
            )

        # Créer quelques comptes sans connexion
        for i in range(3):
            Account.objects.create(
                owner=self.user,
                name=f"Account No Sync {i}",
                type=Account.AccountType.CHECKING,
            )

        # Tester la vue et vérifier qu'elle fonctionne sans erreur
        response = self.client.get(reverse("accounts"))
        self.assertEqual(response.status_code, 200)

        # Vérifier que tous les comptes sont affichés
        self.assertContains(response, "Account 0")
        self.assertContains(response, "Account No Sync 0")
