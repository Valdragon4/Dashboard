"""
Tests d'intégration pour vérifier la compatibilité avec le code existant.

Ces tests vérifient les Integration Verifications (IV1, IV2, IV3) :
- IV1: Les comptes existants sans BankConnection continuent de fonctionner
- IV2: Les imports CSV/PDF manuels créent toujours des Account sans BankConnection
- IV3: Les requêtes du dashboard existant ne sont pas impactées
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from finance.models import Account, BankConnection, Transaction

User = get_user_model()


class TestIntegrationVerification(TestCase):
    """Tests pour vérifier la compatibilité avec le code existant."""

    def setUp(self):
        """Configure l'environnement de test."""
        self.user = User.objects.create_user(username="testuser", password="testpass")

    def test_iv1_existing_accounts_without_bank_connection(self):
        """
        IV1: Les comptes existants sans BankConnection continuent de fonctionner normalement.
        """
        # Créer un compte comme le ferait le code existant (sans BankConnection)
        account = Account.objects.create(
            owner=self.user,
            name="Existing Account",
            type=Account.AccountType.CHECKING,
            provider="Some Bank",
        )

        # Vérifier que le compte fonctionne normalement
        self.assertIsNotNone(account.id)
        self.assertIsNone(account.bank_connection)
        self.assertFalse(account.auto_sync_enabled)

        # Vérifier que les requêtes standards fonctionnent
        accounts = Account.objects.filter(owner=self.user)
        self.assertIn(account, accounts)

        # Vérifier que les transactions peuvent être créées
        transaction = Transaction.objects.create(
            account=account,
            posted_at="2025-01-01T10:00:00Z",
            amount=100.00,
            description="Test transaction",
        )
        self.assertIsNotNone(transaction.id)
        self.assertEqual(transaction.account, account)

    def test_iv2_manual_imports_create_accounts_without_bank_connection(self):
        """
        IV2: Les imports CSV/PDF manuels créent toujours des Account sans BankConnection.
        """
        # Simuler un import CSV manuel (comme le ferait loader.py)
        account = Account.objects.create(
            owner=self.user,
            name="Imported Account",
            type=Account.AccountType.CHECKING,
            provider="Hello Bank",
            # Pas de bank_connection, pas de auto_sync_enabled=True
        )

        # Vérifier que le compte est créé sans BankConnection
        self.assertIsNotNone(account.id)
        self.assertIsNone(account.bank_connection)
        self.assertFalse(account.auto_sync_enabled)

        # Vérifier que le compte peut être utilisé normalement
        transactions = Transaction.objects.filter(account=account)
        self.assertEqual(transactions.count(), 0)

    def test_iv3_dashboard_queries_not_impacted(self):
        """
        IV3: Les requêtes du dashboard existant ne sont pas impactées par les nouveaux modèles.
        """
        # Créer des comptes comme le ferait le dashboard
        account1 = Account.objects.create(
            owner=self.user,
            name="Account 1",
            type=Account.AccountType.CHECKING,
            include_in_dashboard=True,
        )

        account2 = Account.objects.create(
            owner=self.user,
            name="Account 2",
            type=Account.AccountType.SAVINGS,
            include_in_dashboard=True,
        )

        # Créer des transactions
        Transaction.objects.create(
            account=account1,
            posted_at="2025-01-01T10:00:00Z",
            amount=100.00,
            description="Transaction 1",
        )

        Transaction.objects.create(
            account=account2,
            posted_at="2025-01-01T11:00:00Z",
            amount=-50.00,
            description="Transaction 2",
        )

        # Requêtes typiques du dashboard
        # 1. Tous les comptes de l'utilisateur
        user_accounts = Account.objects.filter(owner=self.user)
        self.assertEqual(user_accounts.count(), 2)

        # 2. Comptes inclus dans le dashboard
        dashboard_accounts = Account.objects.filter(
            owner=self.user, include_in_dashboard=True
        )
        self.assertEqual(dashboard_accounts.count(), 2)

        # 3. Transactions par compte
        account1_transactions = Transaction.objects.filter(account=account1)
        self.assertEqual(account1_transactions.count(), 1)

        account2_transactions = Transaction.objects.filter(account=account2)
        self.assertEqual(account2_transactions.count(), 1)

        # 4. Vérifier que les nouveaux champs n'interfèrent pas
        # (les requêtes doivent fonctionner même si bank_connection est NULL)
        accounts_without_connection = Account.objects.filter(
            owner=self.user, bank_connection__isnull=True
        )
        self.assertEqual(accounts_without_connection.count(), 2)

        # 5. Vérifier que les comptes avec BankConnection fonctionnent aussi
        # (pour tester la compatibilité dans les deux sens)
        import os
        from finance.services.encryption_service import EncryptionService

        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        credentials = {"username": "test", "password": "secret"}
        encrypted = EncryptionService.encrypt_credentials(credentials)

        bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Connected Account",
            encrypted_credentials=encrypted,
        )

        account3 = Account.objects.create(
            owner=self.user,
            name="Account 3",
            type=Account.AccountType.BROKER,
            bank_connection=bank_connection,
            auto_sync_enabled=True,
        )

        # Les requêtes doivent toujours fonctionner
        all_user_accounts = Account.objects.filter(owner=self.user)
        self.assertEqual(all_user_accounts.count(), 3)

        # Les comptes avec et sans connexion doivent être accessibles
        accounts_with_connection = Account.objects.filter(
            owner=self.user, bank_connection__isnull=False
        )
        self.assertEqual(accounts_with_connection.count(), 1)

        accounts_without_connection = Account.objects.filter(
            owner=self.user, bank_connection__isnull=True
        )
        self.assertEqual(accounts_without_connection.count(), 2)
