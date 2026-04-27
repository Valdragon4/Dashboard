import os
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from finance.models import (
    Account,
    BankConnection,
    TradeRepublicPortfolioSnapshot,
    TradeRepublicValuationSnapshot,
)
from finance.services.encryption_service import EncryptionService
from finance.services.tr_bridge_client import TradeRepublicBridgeAuthRequired
from finance.services.tr_bridge_sync import (
    sync_bridge_snapshot_for_account,
    sync_bridge_snapshot_with_auth_handling,
)

User = get_user_model()


class TestTradeRepublicBridgeSync(TestCase):
    def setUp(self):
        os.environ["ENCRYPTION_KEY"] = EncryptionService.generate_key()
        self.user = User.objects.create_user(username="bridge-user", password="testpass")
        self.account = Account.objects.create(
            owner=self.user,
            name="TR Broker",
            provider="traderepublic",
            type=Account.AccountType.BROKER,
            currency="EUR",
        )
        credentials = {"phone_number": "+33600000000", "pin": "1234"}
        encrypted = EncryptionService.encrypt_credentials(credentials)
        self.connection = BankConnection.objects.create(
            owner=self.user,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
            account_name="TR Broker",
            encrypted_credentials=encrypted,
        )
        self.account.bank_connection = self.connection
        self.account.save(update_fields=["bank_connection"])

    def tearDown(self):
        if "ENCRYPTION_KEY" in os.environ:
            del os.environ["ENCRYPTION_KEY"]

    @patch("finance.services.tr_bridge_sync.fetch_tr_valuation")
    def test_sync_bridge_snapshot_for_account_creates_snapshots(self, mock_fetch):
        mock_fetch.return_value = {
            "timestamp": "2026-04-26T18:25:00.000Z",
            "accounts": [
                {
                    "account": "0276377602",
                    "invested_total": 11399.37,
                    "invested_societes": 7000.12,
                    "invested_crypto": 4399.25,
                    "invested_by_asset_type": {"stock": 4200.12, "fund": 2800.0, "crypto": 4399.25},
                    "societes": 7700.12,
                    "crypto": 3600.45,
                    "positions_total": 11300.57,
                    "cash": 117.16,
                    "total_with_cash": 11417.73,
                }
            ],
            "global": {
                "invested_total": 15009.52,
                "invested_societes": 10610.27,
                "invested_crypto": 4399.25,
                "invested_by_asset_type": {"stock": 6500.12, "fund": 4110.15, "crypto": 4399.25},
                "societes": 12000.11,
                "crypto": 3800.77,
                "positions_total": 15800.88,
                "cash": 117.16,
                "total_with_cash": 15918.04,
            },
        }

        snapshot = sync_bridge_snapshot_for_account(account=self.account)

        self.assertEqual(snapshot.owner, self.user)
        self.assertEqual(snapshot.account, self.account)
        self.assertEqual(snapshot.invested_total, Decimal("15009.52"))
        self.assertEqual(snapshot.total_with_cash, Decimal("15918.04"))
        self.assertEqual(snapshot.account_snapshots.count(), 1)

        global_portfolios = TradeRepublicPortfolioSnapshot.objects.filter(
            snapshot=snapshot,
            account_snapshot__isnull=True,
        )
        self.assertEqual(global_portfolios.count(), 2)
        by_type = {row.portfolio_type: row.current_value for row in global_portfolios}
        self.assertEqual(by_type.get("CTO"), Decimal("7700.12"))
        self.assertEqual(by_type.get("CRYPTO"), Decimal("3600.45"))

    @patch("finance.services.tr_bridge_sync.fetch_tr_auth_status")
    @patch("finance.services.tr_bridge_sync.fetch_tr_valuation")
    def test_sync_bridge_snapshot_with_auth_handling_creates_auth_required_snapshot(
        self,
        mock_fetch_valuation,
        mock_fetch_auth,
    ):
        mock_fetch_valuation.side_effect = TradeRepublicBridgeAuthRequired("auth_required")
        mock_fetch_auth.return_value = {
            "status": "needs_manual_auth",
            "reason": "device_pin_required",
        }

        snapshot = sync_bridge_snapshot_with_auth_handling(account=self.account)

        self.assertEqual(
            snapshot.auth_status,
            TradeRepublicValuationSnapshot.AuthStatus.NEEDS_MANUAL_AUTH,
        )
        self.assertEqual(snapshot.source, TradeRepublicValuationSnapshot.Source.BRIDGE_AUTO)
        self.assertLess(abs((snapshot.source_timestamp - timezone.now()).total_seconds()), 10)
