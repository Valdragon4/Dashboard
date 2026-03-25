"""
Tests unitaires pour les vues de logs de synchronisation (Story 1.10).
"""

from datetime import datetime, timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

from finance.models import Account, BankConnection, SyncLog
from finance.services.encryption_service import EncryptionService
import os

User = get_user_model()


class TestSyncLogsViews(TestCase):
    """Tests pour les vues de logs de synchronisation."""

    def setUp(self):
        """Configure l'environnement de test."""
        # Configuration de la clé de chiffrement
        test_key = EncryptionService.generate_key()
        os.environ["ENCRYPTION_KEY"] = test_key

        # Créer un utilisateur
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.other_user = User.objects.create_user(username="otheruser", password="testpass")

        # Créer des credentials
        credentials = {"username": "test_user", "password": "secret123"}
        encrypted_credentials = EncryptionService.encrypt_credentials(credentials)

        # Créer des connexions bancaires
        self.connection1 = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Test Account 1",
            encrypted_credentials=encrypted_credentials,
        )
        self.connection2 = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.BOURSORAMA,
            account_name="Test Account 2",
            encrypted_credentials=encrypted_credentials,
        )
        self.other_connection = BankConnection.objects.create(
            owner=self.other_user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="Other Account",
            encrypted_credentials=encrypted_credentials,
        )

        # Créer des logs de synchronisation
        now = timezone.now()
        self.log1 = SyncLog.objects.create(
            bank_connection=self.connection1,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=1, minutes=55),
            transactions_count=10,
        )
        self.log2 = SyncLog.objects.create(
            bank_connection=self.connection1,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.ERROR,
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=50),
            transactions_count=0,
            error_message="Test error message",
        )
        self.log3 = SyncLog.objects.create(
            bank_connection=self.connection2,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.STARTED,
            started_at=now - timedelta(minutes=30),
            completed_at=None,
            transactions_count=0,
        )
        self.other_log = SyncLog.objects.create(
            bank_connection=self.other_connection,
            sync_type=SyncLog.SyncType.AUTOMATIC,
            status=SyncLog.Status.SUCCESS,
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=50),
            transactions_count=5,
        )

        # Créer un client de test
        self.client = Client()

    def tearDown(self):
        """Nettoie l'environnement de test."""
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    def test_sync_logs_list_requires_login(self):
        """Test que la vue nécessite une authentification."""
        response = self.client.get(reverse("sync_logs_list"))
        self.assertEqual(response.status_code, 302)  # Redirection vers login

    def test_sync_logs_list_only_shows_user_logs(self):
        """Test que seuls les logs de l'utilisateur sont affichés."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"))
        self.assertEqual(response.status_code, 200)
        logs = response.context["logs"]
        log_ids = [log.id for log in logs]
        self.assertIn(self.log1.id, log_ids)
        self.assertIn(self.log2.id, log_ids)
        self.assertIn(self.log3.id, log_ids)
        self.assertNotIn(self.other_log.id, log_ids)

    def test_sync_logs_list_filter_by_connection(self):
        """Test le filtre par connexion bancaire."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"), {"connection_id": self.connection1.id})
        self.assertEqual(response.status_code, 200)
        logs = response.context["logs"]
        log_ids = [log.id for log in logs]
        self.assertIn(self.log1.id, log_ids)
        self.assertIn(self.log2.id, log_ids)
        self.assertNotIn(self.log3.id, log_ids)

    def test_sync_logs_list_filter_by_status(self):
        """Test le filtre par statut."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"), {"status": SyncLog.Status.SUCCESS})
        self.assertEqual(response.status_code, 200)
        logs = response.context["logs"]
        for log in logs:
            self.assertEqual(log.status, SyncLog.Status.SUCCESS)

    def test_sync_logs_list_filter_by_sync_type(self):
        """Test le filtre par type de synchronisation."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"), {"sync_type": SyncLog.SyncType.MANUAL})
        self.assertEqual(response.status_code, 200)
        logs = response.context["logs"]
        for log in logs:
            self.assertEqual(log.sync_type, SyncLog.SyncType.MANUAL)

    def test_sync_logs_list_filter_by_date(self):
        """Test le filtre par date."""
        self.client.login(username="testuser", password="testpass")
        date_from = (timezone.now() - timedelta(hours=2)).strftime("%Y-%m-%d")
        response = self.client.get(reverse("sync_logs_list"), {"date_from": date_from})
        self.assertEqual(response.status_code, 200)
        logs = response.context["logs"]
        for log in logs:
            self.assertGreaterEqual(log.started_at.date(), datetime.strptime(date_from, "%Y-%m-%d").date())

    def test_sync_logs_list_statistics(self):
        """Test le calcul des statistiques."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"))
        self.assertEqual(response.status_code, 200)
        stats = response.context["stats"]
        self.assertIn("total_syncs", stats)
        self.assertIn("success_rate", stats)
        self.assertIn("total_transactions", stats)
        self.assertIn("provider_stats", stats)

    def test_sync_logs_list_pagination(self):
        """Test la pagination."""
        # Créer plus de 20 logs pour tester la pagination
        for i in range(25):
            SyncLog.objects.create(
                bank_connection=self.connection1,
                sync_type=SyncLog.SyncType.AUTOMATIC,
                status=SyncLog.Status.SUCCESS,
                started_at=timezone.now() - timedelta(hours=i),
                completed_at=timezone.now() - timedelta(hours=i, minutes=5),
                transactions_count=1,
            )

        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"))
        self.assertEqual(response.status_code, 200)
        logs = response.context["logs"]
        self.assertTrue(logs.has_other_pages())
        self.assertEqual(len(logs), 20)  # Première page

    def test_sync_log_detail_requires_login(self):
        """Test que la vue de détail nécessite une authentification."""
        response = self.client.get(reverse("sync_log_detail", args=[self.log1.id]))
        self.assertEqual(response.status_code, 302)

    def test_sync_log_detail_only_shows_user_logs(self):
        """Test que seul les logs de l'utilisateur sont accessibles."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_log_detail", args=[self.log1.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["log"].id, self.log1.id)

        # Tester qu'un autre utilisateur ne peut pas voir les logs
        response = self.client.get(reverse("sync_log_detail", args=[self.other_log.id]))
        self.assertEqual(response.status_code, 404)

    def test_sync_log_detail_displays_error_message(self):
        """Test l'affichage du message d'erreur."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_log_detail", args=[self.log2.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("error_message_formatted", response.context)
        self.assertIsNotNone(response.context["error_message_formatted"])

    def test_sync_logs_export_requires_login(self):
        """Test que l'export nécessite une authentification."""
        response = self.client.get(reverse("sync_logs_export"))
        self.assertEqual(response.status_code, 302)

    def test_sync_logs_export_csv_format(self):
        """Test le format de l'export CSV."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("sync_logs_", response["Content-Disposition"])

        # Vérifier le contenu CSV
        content = response.content.decode("utf-8-sig")  # Décoder avec BOM
        lines = content.strip().split("\n")
        self.assertGreater(len(lines), 1)  # Au moins l'en-tête et une ligne de données
        self.assertIn("Date début", lines[0])
        self.assertIn("Connexion", lines[0])

    def test_sync_logs_export_respects_filters(self):
        """Test que l'export respecte les filtres."""
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(
            reverse("sync_logs_export"), {"connection_id": self.connection1.id}
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8-sig")
        lines = content.strip().split("\n")
        # Vérifier que tous les logs exportés appartiennent à connection1
        for line in lines[1:]:  # Ignorer l'en-tête
            if line.strip():
                self.assertIn("Test Account 1", line)

    def test_sync_logs_list_alerts_repeated_failures(self):
        """Test la détection d'échecs répétés."""
        # Créer plusieurs échecs consécutifs pour connection1
        now = timezone.now()
        for i in range(5):
            SyncLog.objects.create(
                bank_connection=self.connection1,
                sync_type=SyncLog.SyncType.AUTOMATIC,
                status=SyncLog.Status.ERROR,
                started_at=now - timedelta(hours=5 - i),
                completed_at=now - timedelta(hours=5 - i, minutes=5),
                transactions_count=0,
                error_message=f"Error {i}",
            )

        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse("sync_logs_list"))
        self.assertEqual(response.status_code, 200)
        alerts = response.context["alerts"]
        self.assertGreater(len(alerts), 0)
        # Vérifier que connection1 est dans les alertes
        alert_connection_ids = [alert["connection_id"] for alert in alerts]
        self.assertIn(self.connection1.id, alert_connection_ids)
