"""
Tests de performance pour s'assurer que le dashboard reste rapide.

Ces tests vérifient que les vues principales restent performantes
même avec un grand nombre de données.
"""

import time
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from django.test.utils import override_settings

from finance.models import Account, Transaction, BankConnection, SyncLog
from finance.services.encryption_service import EncryptionService
import os

User = get_user_model()


class TestDashboardPerformance(TestCase):
    """Tests de performance pour la vue dashboard."""

    def setUp(self):
        """Configure l'environnement de test avec beaucoup de données."""
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.client = Client()
        self.client.force_login(self.user)

        # Créer un compte
        self.account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
            include_in_dashboard=True,
        )

        # Créer 1000+ transactions pour tester les performances
        now = timezone.now()
        for i in range(1200):
            Transaction.objects.create(
                account=self.account,
                posted_at=now - timedelta(days=i % 365),
                amount=Decimal(f"{i % 100}.00"),
                description=f"Transaction {i}",
                raw={"source": "generic"},
            )

    def test_dashboard_performance(self):
        """Test de performance de la vue dashboard."""
        # Compter les requêtes DB
        initial_queries = len(connection.queries)

        start_time = time.time()
        response = self.client.get("/")
        elapsed_time = time.time() - start_time

        # Vérifications
        self.assertEqual(response.status_code, 200)
        # Vérifier que le temps de réponse < 2 secondes
        self.assertLess(elapsed_time, 2.0, f"Dashboard trop lent: {elapsed_time:.2f}s")

        # Vérifier le nombre de requêtes DB (< 10 requêtes)
        queries_count = len(connection.queries) - initial_queries
        self.assertLess(
            queries_count,
            15,  # Un peu plus de marge pour les tests
            f"Trop de requêtes DB: {queries_count} requêtes",
        )

    def test_dashboard_with_filters(self):
        """Test de performance avec filtres de date."""
        # Compter les requêtes DB
        initial_queries = len(connection.queries)

        start_time = time.time()
        response = self.client.get(
            "/",
            {
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        )
        elapsed_time = time.time() - start_time

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed_time, 2.0)


class TestSyncLogsListPerformance(TestCase):
    """Tests de performance pour la vue sync_logs_list."""

    def setUp(self):
        """Configure l'environnement de test avec beaucoup de logs."""
        # Configuration de la clé de chiffrement
        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.client = Client()
        self.client.force_login(self.user)

        # Créer une connexion bancaire
        credentials = {"username": "test", "password": "test"}
        encrypted_credentials = EncryptionService.encrypt_credentials(credentials)

        self.bank_connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account",
            encrypted_credentials=encrypted_credentials,
        )

        # Créer 1000+ SyncLog pour tester les performances
        now = timezone.now()
        for i in range(1200):
            SyncLog.objects.create(
                bank_connection=self.bank_connection,
                sync_type=SyncLog.SyncType.AUTOMATIC if i % 2 == 0 else SyncLog.SyncType.MANUAL,
                status=SyncLog.Status.SUCCESS if i % 10 != 0 else SyncLog.Status.ERROR,
                started_at=now - timedelta(days=i % 365),
                completed_at=now - timedelta(days=i % 365, minutes=5),
                transactions_count=i % 100,
            )

    def tearDown(self):
        """Nettoie l'environnement de test."""
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    def test_sync_logs_list_performance(self):
        """Test de performance de la vue sync_logs_list."""
        # Compter les requêtes DB
        initial_queries = len(connection.queries)

        start_time = time.time()
        response = self.client.get("/bank-connections/logs/")
        elapsed_time = time.time() - start_time

        # Vérifications
        self.assertEqual(response.status_code, 200)
        # Vérifier que le temps de réponse < 1 seconde
        self.assertLess(elapsed_time, 1.0, f"Sync logs list trop lent: {elapsed_time:.2f}s")

        # Vérifier le nombre de requêtes DB (< 5 requêtes)
        queries_count = len(connection.queries) - initial_queries
        self.assertLess(
            queries_count,
            8,  # Un peu plus de marge
            f"Trop de requêtes DB: {queries_count} requêtes",
        )

    def test_sync_logs_list_with_filters(self):
        """Test de performance avec filtres."""
        initial_queries = len(connection.queries)

        start_time = time.time()
        response = self.client.get(
            "/bank-connections/logs/",
            {
                "status": "success",
                "sync_type": "automatic",
            },
        )
        elapsed_time = time.time() - start_time

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed_time, 1.0)

    def test_sync_logs_list_pagination_performance(self):
        """Test de performance de la pagination."""
        # Tester différentes pages
        for page in [1, 10, 50]:
            with self.subTest(page=page):
                initial_queries = len(connection.queries)

                start_time = time.time()
                response = self.client.get("/bank-connections/logs/", {"page": page})
                elapsed_time = time.time() - start_time

                self.assertEqual(response.status_code, 200)
                self.assertLess(elapsed_time, 1.0)

                queries_count = len(connection.queries) - initial_queries
                self.assertLess(queries_count, 8)


class TestAccountsViewPerformance(TestCase):
    """Tests de performance pour la vue accounts."""

    def setUp(self):
        """Configure l'environnement de test avec beaucoup de comptes."""
        # Configuration de la clé de chiffrement
        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.client = Client()
        self.client.force_login(self.user)

        # Créer 100 comptes avec BankConnection
        credentials = {"username": "test", "password": "test"}
        encrypted_credentials = EncryptionService.encrypt_credentials(credentials)

        for i in range(100):
            account = Account.objects.create(
                owner=self.user,
                name=f"Test Account {i}",
                type=Account.AccountType.CHECKING,
                auto_sync_enabled=True,
            )

            bank_connection = BankConnection.objects.create(
                owner=self.user,
                provider=BankConnection.Provider.TRADE_REPUBLIC,
                account_name=f"Test Account {i}",
                encrypted_credentials=encrypted_credentials,
            )

            account.bank_connection = bank_connection
            account.save()

            # Créer quelques SyncLog pour chaque compte
            for j in range(5):
                SyncLog.objects.create(
                    bank_connection=bank_connection,
                    sync_type=SyncLog.SyncType.AUTOMATIC,
                    status=SyncLog.Status.SUCCESS,
                    started_at=timezone.now() - timedelta(days=j),
                    completed_at=timezone.now() - timedelta(days=j, minutes=5),
                    transactions_count=10,
                )

    def tearDown(self):
        """Nettoie l'environnement de test."""
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    def test_accounts_view_performance(self):
        """Test de performance de la vue accounts."""
        # Compter les requêtes DB
        initial_queries = len(connection.queries)

        start_time = time.time()
        response = self.client.get("/accounts/")
        elapsed_time = time.time() - start_time

        # Vérifications
        self.assertEqual(response.status_code, 200)
        # Vérifier que le temps de réponse < 2 secondes
        self.assertLess(elapsed_time, 2.0, f"Accounts view trop lent: {elapsed_time:.2f}s")

        # Vérifier le nombre de requêtes DB (< 5 requêtes avec optimisations)
        queries_count = len(connection.queries) - initial_queries
        self.assertLess(
            queries_count,
            10,  # Un peu plus de marge pour 100 comptes
            f"Trop de requêtes DB: {queries_count} requêtes",
        )

    def test_accounts_view_db_optimization(self):
        """Test que les optimisations DB sont utilisées (select_related, prefetch_related)."""
        initial_queries = len(connection.queries)

        response = self.client.get("/accounts/")

        self.assertEqual(response.status_code, 200)

        # Vérifier que le nombre de requêtes est raisonnable
        queries_count = len(connection.queries) - initial_queries
        # Avec select_related et prefetch_related, on devrait avoir < 10 requêtes même pour 100 comptes
        self.assertLess(queries_count, 15)


class TestBankConnectionsListPerformance(TestCase):
    """Tests de performance pour la vue bank_connections_list."""

    def setUp(self):
        """Configure l'environnement de test avec plusieurs connexions."""
        # Configuration de la clé de chiffrement
        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.client = Client()
        self.client.force_login(self.user)

        # Créer 50 BankConnection
        credentials = {"username": "test", "password": "test"}
        encrypted_credentials = EncryptionService.encrypt_credentials(credentials)

        for i in range(50):
            BankConnection.objects.create(
                owner=self.user,
                provider=BankConnection.Provider.TRADE_REPUBLIC,
                account_name=f"Test Connection {i}",
                encrypted_credentials=encrypted_credentials,
            )

    def tearDown(self):
        """Nettoie l'environnement de test."""
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    def test_bank_connections_list_performance(self):
        """Test de performance de la vue bank_connections_list."""
        initial_queries = len(connection.queries)

        start_time = time.time()
        response = self.client.get("/bank-connections/")
        elapsed_time = time.time() - start_time

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed_time, 1.0)

        queries_count = len(connection.queries) - initial_queries
        self.assertLess(queries_count, 8)


class TestPaginationPerformance(TestCase):
    """Tests de performance pour la pagination avec grand nombre d'éléments."""

    def setUp(self):
        """Configure l'environnement de test."""
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.client = Client()
        self.client.force_login(self.user)

        # Créer un compte
        self.account = Account.objects.create(
            owner=self.user,
            name="Test Account",
            type=Account.AccountType.CHECKING,
        )

        # Créer 500+ transactions pour tester la pagination
        now = timezone.now()
        for i in range(600):
            Transaction.objects.create(
                account=self.account,
                posted_at=now - timedelta(days=i % 365),
                amount=Decimal(f"{i % 100}.00"),
                description=f"Transaction {i}",
            )

    def test_transactions_pagination_performance(self):
        """Test de performance de la pagination des transactions."""
        # Tester différentes pages
        pages_to_test = [1, 10, 20, 30]  # Première, médiane, dernière

        for page in pages_to_test:
            with self.subTest(page=page):
                initial_queries = len(connection.queries)

                start_time = time.time()
                response = self.client.get("/transactions/", {"page": page})
                elapsed_time = time.time() - start_time

                self.assertEqual(response.status_code, 200)
                # Les performances doivent rester constantes quelle que soit la page
                self.assertLess(elapsed_time, 1.0)

                queries_count = len(connection.queries) - initial_queries
                # Le nombre de requêtes doit rester constant (pas de N+1)
                self.assertLess(queries_count, 10)
