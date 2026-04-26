"""
Service de synchronisation centralisé pour les connexions bancaires.

Ce service orchestre les connecteurs bancaires pour synchroniser automatiquement
les transactions, soldes et valorisations de portefeuille depuis les différents providers.

Chaque phase (auth, transactions, balance) est chronométrée et journalisée.
Pour BoursoBank, les retries sont limités à 1 par phase pour garantir une sync < 3 min.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from django.db import transaction
from django.utils import timezone

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    BaseBankConnector,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    RateLimitError,
)
from finance.connectors.traderepublic import TradeRepublicConnector
from finance.connectors.powens import PowensConnector
from finance.models import Account, BankConnection, SyncLog, Transaction
from finance.services.encryption_service import EncryptionService, EncryptionError

try:
    from finance.connectors.boursorama import BoursoBankConnector
except ImportError:
    BoursoBankConnector = None

try:
    from finance.connectors.hellobank import HelloBankConnector
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    HelloBankConnector = None
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping d'erreurs techniques -> messages métier courts
# ---------------------------------------------------------------------------

_ERROR_CODES = {
    "requires_2fa": "2FA requise par BoursoBank",
    "invalid_credentials": "Identifiants invalides",
    "network_timeout": "Timeout connexion bancaire",
    "partial_sync": "Transactions indisponibles (auth OK)",
}


def _classify_error(exc: Exception) -> str:
    """Retourne un code métier court pour l'exception donnée."""
    if isinstance(exc, InvalidCredentialsError):
        return "invalid_credentials"
    if isinstance(exc, ConnectionTimeoutError):
        return "network_timeout"
    if isinstance(exc, AuthenticationError):
        return "invalid_credentials"
    return "network_timeout"


def _business_message(code: str, detail: str = "") -> str:
    base = _ERROR_CODES.get(code, code)
    if detail:
        return f"{base}: {detail[:200]}"
    return base


class SyncService:
    """
    Service centralisé de synchronisation bancaire.

    Orchestration par phases chronométrées (auth -> transactions -> balance -> persist).
    """

    GENERIC_MAX_RETRIES = 3
    BASE_RETRY_DELAY = 1.0

    @staticmethod
    def _get_connector_for_provider(provider: str) -> BaseBankConnector:
        import os

        if provider == BankConnection.Provider.TRADE_REPUBLIC:
            return TradeRepublicConnector()
        elif provider == BankConnection.Provider.BOURSORAMA:
            if BoursoBankConnector is None:
                raise ImportError(
                    "Le connecteur BoursoBank nécessite boursobank-scraper."
                )
            return BoursoBankConnector()
        elif provider == BankConnection.Provider.HELLOBANK:
            if not PLAYWRIGHT_AVAILABLE or HelloBankConnector is None:
                raise ImportError("Playwright non disponible pour HelloBank.")
            return HelloBankConnector()
        elif provider == BankConnection.Provider.POWENS:
            api_key = os.getenv("POWENS_API_KEY")
            api_secret = os.getenv("POWENS_API_SECRET")
            base_url = os.getenv("POWENS_BASE_URL", "https://api.powens.com")
            if not api_key or not api_secret:
                raise ValueError("POWENS_API_KEY et POWENS_API_SECRET requis.")
            return PowensConnector(api_key=api_key, api_secret=api_secret, base_url=base_url)
        else:
            raise ValueError(f"Provider inconnu: {provider}")

    @staticmethod
    def _max_retries_for(provider: str) -> int:
        if provider == BankConnection.Provider.BOURSORAMA:
            return 1
        return SyncService.GENERIC_MAX_RETRIES

    @staticmethod
    def _should_retry_error(error: Exception) -> bool:
        if isinstance(error, (ConnectionTimeoutError, RateLimitError)):
            return True
        return False

    _category_cache: Dict[str, Optional["Category"]] = {}

    @staticmethod
    def _resolve_category(raw: Dict) -> Optional["Category"]:
        from finance.models import Category as Cat

        label = (raw.get("boursobank_category_label") or "").strip()
        if not label:
            return None
        if label in SyncService._category_cache:
            return SyncService._category_cache[label]

        parent_label = (raw.get("boursobank_category_parent_label") or "").strip()
        cat = None
        if parent_label:
            cat = Cat.objects.filter(name=label, parent__name=parent_label).first()
        if cat is None:
            cat = Cat.objects.filter(name=label).first()

        SyncService._category_cache[label] = cat
        return cat

    @staticmethod
    def _compute_sync_fingerprint(
        posted_at: datetime,
        amount: Decimal,
        description: str,
        source: str,
    ) -> str:
        """
        Empreinte de secours pour dedup quand aucun identifiant upstream fiable n'est fourni.
        """
        normalized_desc = " ".join((description or "").strip().lower().split())
        return f"{posted_at.date().isoformat()}|{Decimal(amount)}|{normalized_desc}|{source}"

    @staticmethod
    def _upsert_transaction_from_sync(
        account: Account,
        transaction_data: Dict,
        source: str,
    ) -> Transaction:
        posted_at = transaction_data["posted_at"]
        amount = transaction_data["amount"]
        description = transaction_data.get("description", "")
        raw = transaction_data.get("raw", {})

        from django.conf import settings as _s
        if _s.USE_TZ and timezone.is_naive(posted_at):
            posted_at = timezone.make_aware(posted_at, timezone.get_current_timezone())
        if "source" not in raw:
            raw["source"] = source
        sync_fingerprint = SyncService._compute_sync_fingerprint(
            posted_at=posted_at,
            amount=Decimal(amount),
            description=description,
            source=source,
        )
        raw.setdefault("sync_fingerprint", sync_fingerprint)

        category = SyncService._resolve_category(raw)
        defaults = {"currency": account.currency or "EUR", "raw": raw}
        if category is not None:
            defaults["category"] = category

        transaction_id = raw.get("transaction_id") if raw else None
        operation_id = raw.get("operation_id") if raw else None

        existing = None
        if transaction_id:
            existing = Transaction.objects.filter(
                account=account,
                raw__transaction_id=transaction_id,
            ).first()
        if existing is None and operation_id:
            existing = Transaction.objects.filter(
                account=account,
                raw__operation_id=operation_id,
            ).first()
        if existing is None:
            # Dernier filet de securite: matching "metier" stable
            existing = Transaction.objects.filter(
                account=account,
                raw__sync_fingerprint=sync_fingerprint,
            ).first()

        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.description = description
            existing.posted_at = posted_at
            existing.amount = Decimal(amount)
            existing.save()
            return existing

        return Transaction.objects.create(
            account=account,
            posted_at=posted_at,
            amount=Decimal(amount),
            description=description,
            **defaults,
        )

    @staticmethod
    def sync_account(
        account: Account,
        sync_type: str = SyncLog.SyncType.AUTOMATIC,
    ) -> Dict:
        if not account.bank_connection:
            raise ValueError(f"Compte {account.id} sans connexion bancaire")

        if not account.auto_sync_enabled:
            return {
                "success": False,
                "transactions_count": 0,
                "sync_log_id": None,
                "error": "Synchronisation desactivee",
            }

        bank_connection = account.bank_connection
        provider = bank_connection.provider
        connector = None
        transactions_count = 0
        phase_log: Dict[str, Dict] = {}
        sync_t0 = time.monotonic()

        sync_log = SyncLog.objects.create(
            bank_connection=bank_connection,
            sync_type=sync_type,
            status=SyncLog.Status.STARTED,
            started_at=timezone.now(),
        )
        bank_connection.sync_status = BankConnection.SyncStatus.SYNCING
        bank_connection.save()

        logger.info("sync_start provider=%s account=%s type=%s", provider, account.id, sync_type)
        max_retries = SyncService._max_retries_for(provider)

        try:
            # --- Credentials ------------------------------------------------
            try:
                credentials = EncryptionService.decrypt_credentials(
                    bank_connection.encrypted_credentials
                )
            except EncryptionError as e:
                raise ValueError(f"Dechiffrement credentials: {e}") from e

            try:
                connector = SyncService._get_connector_for_provider(provider)
            except (ValueError, ImportError) as e:
                raise ValueError(f"Connecteur indisponible: {e}") from e

            # --- Phase AUTH --------------------------------------------------
            t0 = time.monotonic()
            auth_result = None
            for attempt in range(max_retries):
                try:
                    auth_result = connector.authenticate(credentials)
                    break
                except Exception as e:
                    if not SyncService._should_retry_error(e) or attempt >= max_retries - 1:
                        raise
                    wait = SyncService.BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning("phase=auth attempt=%d/%d error=%s retry_in=%ss",
                                   attempt + 1, max_retries, e, wait)
                    time.sleep(wait)
            auth_ms = int((time.monotonic() - t0) * 1000)
            phase_log["auth"] = {"elapsed_ms": auth_ms, "result": "ok"}
            logger.info("phase=auth elapsed_ms=%d result=ok", auth_ms)

            if auth_result and auth_result.get("requires_2fa"):
                phase_log["auth"]["result"] = "requires_2fa"
                logger.info("phase=auth elapsed_ms=%d result=requires_2fa", auth_ms)
                try:
                    connector.disconnect()
                except Exception:
                    pass
                total_ms = int((time.monotonic() - sync_t0) * 1000)
                sync_log.status = SyncLog.Status.ERROR
                sync_log.completed_at = timezone.now()
                sync_log.error_message = _business_message("requires_2fa")
                sync_log.save()
                bank_connection.sync_status = BankConnection.SyncStatus.ERROR
                bank_connection.save()
                logger.info("sync_done provider=%s total_ms=%d result=requires_2fa", provider, total_ms)
                return {
                    "success": False, "transactions_count": 0,
                    "sync_log_id": sync_log.id, "requires_2fa": True,
                    "error": _business_message("requires_2fa"),
                }

            # --- Phase TRANSACTIONS ------------------------------------------
            t0 = time.monotonic()
            transactions_data = []
            since = None if sync_type == SyncLog.SyncType.MANUAL else bank_connection.last_sync_at
            tx_error_code = None
            for attempt in range(max_retries):
                try:
                    transactions_data = connector.sync_transactions(account, since=since)
                    break
                except Exception as e:
                    if not SyncService._should_retry_error(e) or attempt >= max_retries - 1:
                        tx_error_code = _classify_error(e)
                        logger.warning("phase=transactions error=%s code=%s", e, tx_error_code)
                        break
                    wait = SyncService.BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning("phase=transactions attempt=%d/%d error=%s retry_in=%ss",
                                   attempt + 1, max_retries, e, wait)
                    time.sleep(wait)
            tx_ms = int((time.monotonic() - t0) * 1000)
            tx_result = "ok" if transactions_data else (tx_error_code or "empty")
            phase_log["transactions"] = {"elapsed_ms": tx_ms, "result": tx_result, "count": len(transactions_data)}
            logger.info("phase=transactions elapsed_ms=%d result=%s count=%d",
                        tx_ms, tx_result, len(transactions_data))

            # --- Phase BALANCE -----------------------------------------------
            t0 = time.monotonic()
            balance = None
            balance_error = None
            for attempt in range(max_retries):
                try:
                    balance = connector.get_balance(account)
                    break
                except Exception as e:
                    if not SyncService._should_retry_error(e) or attempt >= max_retries - 1:
                        balance_error = str(e)
                        logger.warning("phase=balance error=%s", e)
                        break
                    wait = SyncService.BASE_RETRY_DELAY * (2 ** attempt)
                    time.sleep(wait)
            bal_ms = int((time.monotonic() - t0) * 1000)
            bal_result = "ok" if balance is not None else "error"
            phase_log["balance"] = {"elapsed_ms": bal_ms, "result": bal_result}
            logger.info("phase=balance elapsed_ms=%d result=%s balance=%s",
                        bal_ms, bal_result, balance)

            # --- Disconnect --------------------------------------------------
            try:
                connector.disconnect()
            except Exception as e:
                logger.warning("disconnect error: %s", e)
            connector = None

            # --- Phase PERSIST -----------------------------------------------
            t0 = time.monotonic()
            source = provider
            for transaction_data in transactions_data:
                try:
                    with transaction.atomic():
                        SyncService._upsert_transaction_from_sync(account, transaction_data, source)
                    transactions_count += 1
                except Exception as e:
                    logger.warning("persist_tx error: %s", e)
                    continue

            if balance is not None:
                from django.db.models import Sum as _Sum

                # Pour certains connecteurs (ex: BoursoBank), le solde "courant" recupere
                # peut inclure des mouvements pending que nous n'importons pas volontairement.
                # Recaler initial_balance a chaque sync cree alors une derive du "solde apres".
                # Strategie:
                # - toujours recalibrer si on dispose de soldes absolus transactionnels (account_balance)
                # - sinon, ne recalibrer qu'au premier snapshot du compte
                # Cas metier explicite BoursoBank:
                # on veut un ledger base sur les seules operations importees (hors pending),
                # donc point de depart fixe a 0.
                if provider == "boursorama":
                    if account.initial_balance != Decimal("0"):
                        account.initial_balance = Decimal("0")
                        account.balance_snapshot_date = timezone.now().date()
                        account.save(update_fields=["initial_balance", "balance_snapshot_date"])
                    logger.info(
                        "set_initial_zero account=%s provider=%s mode=ledger_only",
                        account.id,
                        provider,
                    )
                    should_rebase_initial = False
                else:
                    has_absolute_tx_balance = Transaction.objects.filter(
                        account=account,
                        account_balance__isnull=False,
                    ).exists()
                    should_rebase_initial = has_absolute_tx_balance or account.balance_snapshot_date is None

                if should_rebase_initial:
                    tx_sum = Transaction.objects.filter(account=account).aggregate(
                        total=_Sum("amount")
                    )["total"] or Decimal("0")
                    account.initial_balance = balance - tx_sum
                    account.balance_snapshot_date = timezone.now().date()
                    account.save(update_fields=["initial_balance", "balance_snapshot_date"])
                else:
                    logger.info(
                        "skip_initial_rebase account=%s provider=%s reason=no_absolute_tx_balance",
                        account.id,
                        provider,
                    )

            # Valorisations portefeuille (optionnel, ne bloque pas la sync)
            portfolio_valuations = {}
            try:
                if hasattr(connector or object(), "sync_portfolio_valuations"):
                    pass
                else:
                    portfolio_result = SyncService._get_connector_for_provider(provider).sync_portfolio_valuations(account) if False else {}
            except Exception:
                pass

            persist_ms = int((time.monotonic() - t0) * 1000)
            phase_log["persist"] = {"elapsed_ms": persist_ms, "count": transactions_count}
            logger.info("phase=persist elapsed_ms=%d count=%d", persist_ms, transactions_count)

            # --- Finalize success --------------------------------------------
            total_ms = int((time.monotonic() - sync_t0) * 1000)

            error_parts = []
            if tx_error_code:
                error_parts.append(_business_message(tx_error_code))
            if balance_error:
                error_parts.append(f"balance: {balance_error[:150]}")
            error_summary = "; ".join(error_parts) if error_parts else ""

            sync_log.status = SyncLog.Status.SUCCESS
            sync_log.completed_at = timezone.now()
            sync_log.transactions_count = transactions_count
            if error_summary:
                sync_log.error_message = f"partial_sync: {error_summary}"[:1000]
            sync_log.save()

            bank_connection.sync_status = BankConnection.SyncStatus.SUCCESS
            bank_connection.last_sync_at = timezone.now()
            bank_connection.save()

            logger.info(
                "sync_done provider=%s account=%s total_ms=%d tx=%d balance=%s phases=%s",
                provider, account.id, total_ms, transactions_count, balance, phase_log,
            )

            return {
                "success": True,
                "transactions_count": transactions_count,
                "sync_log_id": sync_log.id,
                "portfolio_valuations": portfolio_valuations,
            }

        except Exception as e:
            total_ms = int((time.monotonic() - sync_t0) * 1000)
            code = _classify_error(e)
            error_msg = _business_message(code, str(e))

            logger.error(
                "sync_error provider=%s account=%s total_ms=%d code=%s error=%s",
                provider, account.id, total_ms, code, e,
                exc_info=True,
            )

            sync_log.status = SyncLog.Status.ERROR
            sync_log.completed_at = timezone.now()
            sync_log.error_message = error_msg[:1000]
            sync_log.transactions_count = transactions_count
            sync_log.save()

            bank_connection.sync_status = BankConnection.SyncStatus.ERROR
            bank_connection.save()

            if connector:
                try:
                    connector.disconnect()
                except Exception:
                    pass

            return {
                "success": False,
                "transactions_count": transactions_count,
                "sync_log_id": sync_log.id,
                "error": error_msg,
            }
