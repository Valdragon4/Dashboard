"""
Tests unitaires pour les vues de gestion des connexions bancaires (Story 1.8).
"""

import os
from unittest.mock import patch, Mock

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

from finance.models import Account, BankConnection, SyncLog
from finance.services.encryption_service import EncryptionService

User = get_user_model()


class TestBankConnectionViews(TestCase):
    """Tests pour les vues de gestion des connexions bancaires."""

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

        self.account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
        )

    def test_bank_connections_list_get(self):
        """Test affichage de la liste des connexions."""
        # Créer une connexion bancaire
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Créer un SyncLog réussi
        SyncLog.objects.create(
            bank_connection=connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.SUCCESS,
            transactions_count=5,
        )

        # Associer le compte à la connexion
        self.account.bank_connection = connection
        self.account.save()

        # Tester la vue
        response = self.client.get(reverse("bank_connections_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Connection")
        self.assertContains(response, "5 transaction")

    def test_bank_connections_list_empty(self):
        """Test affichage de la liste vide."""
        response = self.client.get(reverse("bank_connections_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucune connexion bancaire")

    def test_bank_connections_list_filtered_by_user(self):
        """Test que seules les connexions de l'utilisateur sont affichées."""
        # Créer un autre utilisateur avec une connexion
        other_user = User.objects.create_user(username="otheruser", password="otherpass")
        credentials = {"phone_number": "+33987654321", "pin": "5678"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        BankConnection.objects.create(
            owner=other_user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Other Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Créer une connexion pour l'utilisateur actuel
        credentials2 = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds2 = EncryptionService.encrypt_credentials(credentials2)

        BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="My Connection",
            encrypted_credentials=encrypted_creds2,
        )

        # Tester la vue
        response = self.client.get(reverse("bank_connections_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Connection")
        self.assertNotContains(response, "Other Connection")

    @patch("finance.forms.EncryptionService.encrypt_credentials")
    def test_bank_connection_create_success(self, mock_encrypt):
        """Test création réussie d'une connexion bancaire."""
        mock_encrypt.return_value = "encrypted_creds"

        response = self.client.post(
            reverse("bank_connection_create"),
            {
                "provider": BankConnection.Provider.TRADE_REPUBLIC,
                "account": self.account.id,
                "account_name": "My Trade Republic Account",
                "phone_number": "+33123456789",
                "pin": "1234",
                "auto_sync_enabled": True,
            },
        )

        self.assertEqual(response.status_code, 302)  # Redirect
        self.assertTrue(BankConnection.objects.filter(owner=self.user).exists())
        connection = BankConnection.objects.get(owner=self.user)
        self.assertEqual(connection.account_name, "My Trade Republic Account")
        self.assertEqual(connection.provider, BankConnection.Provider.TRADE_REPUBLIC)

        # Vérifier que le compte est associé
        self.account.refresh_from_db()
        self.assertEqual(self.account.bank_connection, connection)

    def test_bank_connection_create_validation_errors(self):
        """Test validation des champs requis selon le provider."""
        # Test Trade Republic sans phone_number
        response = self.client.post(
            reverse("bank_connection_create"),
            {
                "provider": BankConnection.Provider.TRADE_REPUBLIC,
                "account": self.account.id,
                "pin": "1234",
            },
        )

        self.assertEqual(response.status_code, 200)  # Formulaire avec erreurs
        self.assertFalse(BankConnection.objects.filter(owner=self.user).exists())

        # Test BoursoBank sans username
        response = self.client.post(
            reverse("bank_connection_create"),
            {
                "provider": BankConnection.Provider.BOURSORAMA,
                "account": self.account.id,
                "password": "password123",
            },
        )

        self.assertEqual(response.status_code, 200)  # Formulaire avec erreurs
        self.assertFalse(BankConnection.objects.filter(owner=self.user).exists())

    def test_bank_connection_create_get(self):
        """Test affichage du formulaire de création."""
        response = self.client.get(reverse("bank_connection_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nouvelle connexion bancaire")

    def test_bank_connection_update_get(self):
        """Test affichage du formulaire de modification."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        response = self.client.get(reverse("bank_connection_update", args=[connection.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modifier la connexion")

    def test_bank_connection_update_success(self):
        """Test mise à jour réussie d'une connexion bancaire."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Old Name",
            encrypted_credentials=encrypted_creds,
        )

        with patch("finance.forms.EncryptionService.encrypt_credentials") as mock_encrypt:
            mock_encrypt.return_value = "new_encrypted_creds"

            response = self.client.post(
                reverse("bank_connection_update", args=[connection.id]),
                {
                    "provider": BankConnection.Provider.TRADE_REPUBLIC,
                    "account": self.account.id,
                    "account_name": "New Name",
                    "phone_number": "+33987654321",
                    "pin": "5678",
                    "auto_sync_enabled": True,
                },
            )

            self.assertEqual(response.status_code, 302)  # Redirect
            connection.refresh_from_db()
            self.assertEqual(connection.account_name, "New Name")

    def test_bank_connection_update_not_owner(self):
        """Test qu'un utilisateur ne peut pas modifier une connexion d'un autre utilisateur."""
        other_user = User.objects.create_user(username="otheruser", password="otherpass")
        credentials = {"phone_number": "+33987654321", "pin": "5678"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=other_user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Other Connection",
            encrypted_credentials=encrypted_creds,
        )

        response = self.client.get(reverse("bank_connection_update", args=[connection.id]))
        self.assertEqual(response.status_code, 302)  # Redirect vers la liste
        self.assertRedirects(response, reverse("bank_connections_list"))

    def test_bank_connection_delete_get(self):
        """Test affichage de la confirmation de suppression."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        response = self.client.get(reverse("bank_connection_delete", args=[connection.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmer la suppression")

    def test_bank_connection_delete_success(self):
        """Test suppression réussie d'une connexion bancaire."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Créer un SyncLog associé
        sync_log = SyncLog.objects.create(
            bank_connection=connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.SUCCESS,
        )

        # Associer le compte à la connexion
        self.account.bank_connection = connection
        self.account.save()

        # Supprimer la connexion
        response = self.client.post(reverse("bank_connection_delete", args=[connection.id]))
        self.assertEqual(response.status_code, 302)  # Redirect

        # Vérifier que la connexion est supprimée
        self.assertFalse(BankConnection.objects.filter(id=connection.id).exists())

        # Vérifier que les SyncLog sont supprimés (CASCADE)
        self.assertFalse(SyncLog.objects.filter(id=sync_log.id).exists())

        # Vérifier que le compte n'a plus de bank_connection
        self.account.refresh_from_db()
        self.assertIsNone(self.account.bank_connection)

    def test_bank_connection_delete_not_owner(self):
        """Test qu'un utilisateur ne peut pas supprimer une connexion d'un autre utilisateur."""
        other_user = User.objects.create_user(username="otheruser", password="otherpass")
        credentials = {"phone_number": "+33987654321", "pin": "5678"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=other_user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Other Connection",
            encrypted_credentials=encrypted_creds,
        )

        response = self.client.post(reverse("bank_connection_delete", args=[connection.id]))
        self.assertEqual(response.status_code, 302)  # Redirect vers la liste
        self.assertTrue(BankConnection.objects.filter(id=connection.id).exists())  # Connexion toujours présente

    @patch("finance.tasks.sync_bank_account")
    def test_bank_connection_sync_success(self, mock_sync_task):
        """Test synchronisation manuelle réussie."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Associer le compte à la connexion
        self.account.bank_connection = connection
        self.account.save()

        # Mock de la tâche Celery
        mock_task_result = Mock()
        mock_sync_task.delay.return_value = mock_task_result

        # Tester la synchronisation
        response = self.client.post(reverse("bank_connection_sync", args=[connection.id]))
        self.assertEqual(response.status_code, 302)  # Redirect

        # Vérifier que la tâche a été appelée
        mock_sync_task.delay.assert_called_once_with(self.account.id, sync_type=SyncLog.SyncType.MANUAL)

    def test_bank_connection_sync_no_account(self):
        """Test synchronisation sans compte associé."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Ne pas associer de compte

        # Tester la synchronisation
        response = self.client.post(reverse("bank_connection_sync", args=[connection.id]))
        self.assertEqual(response.status_code, 302)  # Redirect avec message d'erreur

    def test_bank_connection_2fa_get(self):
        """Test affichage du formulaire 2FA."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        response = self.client.get(reverse("bank_connection_2fa", args=[connection.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Authentification 2FA")

    def test_bank_connection_2fa_success(self):
        """Test validation réussie du code 2FA."""
        # Créer la connexion AVANT d'appliquer les patches
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Associer le compte à la connexion
        self.account.bank_connection = connection
        self.account.save()

        # Appliquer les mocks avec context manager
        with patch("finance.tasks.sync_bank_account") as mock_sync_task, \
             patch("finance.services.encryption_service.EncryptionService.decrypt_credentials") as mock_decrypt, \
             patch("finance.services.encryption_service.EncryptionService.encrypt_credentials") as mock_encrypt:
            
            mock_decrypt.return_value = credentials
            mock_encrypt.return_value = "new_encrypted_creds"
            mock_task_result = Mock()
            mock_sync_task.delay.return_value = mock_task_result

            # Tester la validation du code 2FA
            response = self.client.post(
                reverse("bank_connection_2fa", args=[connection.id]),
                {"two_fa_code": "123456"},
            )

            self.assertEqual(response.status_code, 302)  # Redirect

            # Vérifier que les credentials ont été mis à jour avec le code 2FA
            mock_decrypt.assert_called_once()
            updated_credentials = credentials.copy()
            updated_credentials["2fa_code"] = "123456"
            mock_encrypt.assert_called_once_with(updated_credentials)

            # Vérifier que la synchronisation a été démarrée
            mock_sync_task.delay.assert_called_once_with(self.account.id, sync_type=SyncLog.SyncType.MANUAL)

    def test_bank_connection_2fa_empty_code(self):
        """Test validation du code 2FA vide."""
        credentials = {"phone_number": "+33123456789", "pin": "1234"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Connection",
            encrypted_credentials=encrypted_creds,
        )

        # Tester avec un code vide
        response = self.client.post(
            reverse("bank_connection_2fa", args=[connection.id]),
            {"two_fa_code": ""},
        )

        self.assertEqual(response.status_code, 302)  # Redirect avec message d'erreur

    def test_bank_connection_2fa_not_owner(self):
        """Test qu'un utilisateur ne peut pas accéder au 2FA d'une connexion d'un autre utilisateur."""
        other_user = User.objects.create_user(username="otheruser", password="otherpass")
        credentials = {"phone_number": "+33987654321", "pin": "5678"}
        encrypted_creds = EncryptionService.encrypt_credentials(credentials)

        connection = BankConnection.objects.create(
            owner=other_user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Other Connection",
            encrypted_credentials=encrypted_creds,
        )

        response = self.client.get(reverse("bank_connection_2fa", args=[connection.id]))
        self.assertEqual(response.status_code, 302)  # Redirect vers la liste
