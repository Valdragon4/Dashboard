"""
Service de synchronisation centralisé pour les connexions bancaires.

Ce service orchestre les connecteurs bancaires pour synchroniser automatiquement
les transactions, soldes et valorisations de portefeuille depuis les différents providers.
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

# Import conditionnel des connecteurs
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


class SyncService:
    """
    Service centralisé de synchronisation bancaire.

    Ce service orchestre les connecteurs bancaires pour synchroniser automatiquement
    les comptes bancaires. Il gère la détection de doublons, les erreurs avec retry,
    et la création de logs de synchronisation.
    """

    MAX_RETRIES = 3
    BASE_RETRY_DELAY = 1.0  # secondes

    @staticmethod
    def _get_connector_for_provider(provider: str) -> BaseBankConnector:
        """
        Retourne le connecteur approprié selon le provider.

        Args:
            provider: Nom du provider ("trade_republic", "boursorama", "hellobank", "powens")

        Returns:
            BaseBankConnector: Instance du connecteur approprié

        Raises:
            ValueError: Si le provider est inconnu
            ImportError: Si Playwright est requis mais non disponible
        """
        import os
        
        if provider == BankConnection.Provider.TRADE_REPUBLIC:
            return TradeRepublicConnector()
        elif provider == BankConnection.Provider.BOURSORAMA:
            if BoursoBankConnector is None:
                raise ImportError(
                    "Le connecteur BoursoBank nécessite boursobank-scraper "
                    "(et Python >= 3.13)."
                )
            return BoursoBankConnector()
        elif provider == BankConnection.Provider.HELLOBANK:
            if not PLAYWRIGHT_AVAILABLE or HelloBankConnector is None:
                raise ImportError(
                    "Playwright n'est pas installé. Installez-le avec: pip install playwright && playwright install"
                )
            return HelloBankConnector()
        elif provider == BankConnection.Provider.POWENS:
            api_key = os.getenv("POWENS_API_KEY")
            api_secret = os.getenv("POWENS_API_SECRET")
            base_url = os.getenv("POWENS_BASE_URL", "https://api.powens.com")
            
            if not api_key or not api_secret:
                raise ValueError(
                    "POWENS_API_KEY et POWENS_API_SECRET doivent être configurés dans les variables d'environnement"
                )
            
            return PowensConnector(api_key=api_key, api_secret=api_secret, base_url=base_url)
        else:
            raise ValueError(f"Provider inconnu: {provider}")

    @staticmethod
    def _should_retry_error(error: Exception) -> bool:
        """
        Détermine si une erreur doit être retentée.

        Args:
            error: Exception levée

        Returns:
            bool: True si l'erreur doit être retentée, False sinon
        """
        if isinstance(error, (ConnectionTimeoutError, RateLimitError)):
            return True
        if isinstance(error, InvalidCredentialsError):
            return False
        if isinstance(error, AuthenticationError):
            return False  # Ne pas retry les erreurs d'authentification
        return False  # Par défaut, ne pas retry

    # Cache module-level pour éviter N requêtes Category par sync
    _category_cache: Dict[str, Optional["Category"]] = {}

    @staticmethod
    def _resolve_category(raw: Dict) -> Optional["Category"]:
        """
        Résout la catégorie à partir des labels BoursoBank stockés dans raw.
        Cherche d'abord par (label exact, parent_label), puis par label seul.
        Utilise un cache en mémoire pour éviter une requête par transaction.
        """
        from finance.models import Category as Cat

        label = (raw.get("boursobank_category_label") or "").strip()
        if not label:
            return None

        cache_key = label
        if cache_key in SyncService._category_cache:
            return SyncService._category_cache[cache_key]

        parent_label = (raw.get("boursobank_category_parent_label") or "").strip()

        cat = None
        if parent_label:
            cat = Cat.objects.filter(
                name=label,
                parent__name=parent_label,
            ).first()
        if cat is None:
            cat = Cat.objects.filter(name=label).first()

        SyncService._category_cache[cache_key] = cat
        return cat

    @staticmethod
    def _upsert_transaction_from_sync(
        account: Account,
        transaction_data: Dict,
        source: str,
    ) -> Transaction:
        """
        Crée ou met à jour une transaction depuis une synchronisation.

        La catégorie est automatiquement résolue depuis les labels BoursoBank
        présents dans raw (boursobank_category_label / boursobank_category_parent_label).

        Args:
            account: Compte associé à la transaction
            transaction_data: Dictionnaire contenant les données de la transaction :
                            {"posted_at": datetime, "amount": Decimal, "description": str, "raw": dict}
            source: Source de la transaction ("trade_republic", "boursorama", "hellobank")

        Returns:
            Transaction: Transaction créée ou mise à jour
        """
        posted_at = transaction_data["posted_at"]
        amount = transaction_data["amount"]
        description = transaction_data.get("description", "")
        raw = transaction_data.get("raw", {})

        # S'assurer que posted_at est timezone-aware pour Django
        from django.conf import settings
        if settings.USE_TZ and timezone.is_naive(posted_at):
            posted_at = timezone.make_aware(posted_at, timezone.get_current_timezone())

        # S'assurer que "source" est dans raw
        if "source" not in raw:
            raw["source"] = source

        # Résoudre la catégorie depuis les labels BoursoBank
        category = SyncService._resolve_category(raw)

        # Préparer les defaults pour update_or_create
        defaults = {
            "currency": account.currency or "EUR",
            "raw": raw,
        }
        if category is not None:
            defaults["category"] = category

        # Priorité de déduplication :
        # 1. Si on a un transaction_id → utiliser celui-ci (priorité 1)
        # 2. Sinon, utiliser posted_at + amount + description (fallback)

        transaction_id = raw.get("transaction_id") if raw else None

        if transaction_id:
            # Utiliser l'ID unique pour la déduplication (MEILLEUR)
            existing = Transaction.objects.filter(
                account=account,
                raw__transaction_id=transaction_id,
            ).first()

            if existing:
                # Mettre à jour la transaction existante
                for key, value in defaults.items():
                    setattr(existing, key, value)
                existing.description = description
                existing.posted_at = posted_at
                existing.amount = Decimal(amount)
                existing.save()
                return existing
            else:
                # Créer une nouvelle transaction
                return Transaction.objects.create(
                    account=account,
                    posted_at=posted_at,
                    amount=Decimal(amount),
                    description=description,
                    **defaults,
                )
        else:
            # Utiliser posted_at + amount + description comme clé de déduplication
            return Transaction.objects.update_or_create(
                account=account,
                posted_at=posted_at,
                amount=Decimal(amount),
                description=description,
                defaults=defaults,
            )[0]

    @staticmethod
    def sync_account(
        account: Account,
        sync_type: str = SyncLog.SyncType.AUTOMATIC,
    ) -> Dict:
        """
        Synchronise un compte bancaire avec son provider.

        Cette méthode orchestre toute la synchronisation :
        - Authentification avec le connecteur
        - Récupération des transactions
        - Mise à jour du solde
        - Synchronisation des valorisations de portefeuille (si supporté)
        - Détection de doublons
        - Création de logs

        Args:
            account: Compte à synchroniser
            sync_type: Type de synchronisation ("manual" ou "automatic")

        Returns:
            dict: Résultat de la synchronisation :
                  {
                      "success": bool,
                      "transactions_count": int,
                      "sync_log_id": int,
                      "requires_2fa": bool (optionnel),
                      "error": str (optionnel),
                  }

        Raises:
            ValueError: Si le compte n'a pas de bank_connection ou si auto_sync_enabled=False

        Note:
            Le SyncLog initial (status=STARTED) et la mise à jour du statut SYNCING
            sont committés immédiatement (hors transaction atomique) pour être visibles
            en temps réel dans le dashboard pendant la sync. Seules les écritures
            de transactions sont atomiques.
        """
        # Vérifications préliminaires
        if not account.bank_connection:
            raise ValueError(f"Le compte {account.id} n'a pas de connexion bancaire associée")

        if not account.auto_sync_enabled:
            logger.info(f"Sync désactivée pour le compte {account.id}")
            return {
                "success": False,
                "transactions_count": 0,
                "sync_log_id": None,
                "error": "Synchronisation désactivée pour ce compte",
            }

        bank_connection = account.bank_connection
        connector = None
        transactions_count = 0

        # Créer le SyncLog et mettre à jour le statut AVANT toute opération longue.
        # Ces writes sont committés immédiatement (pas dans un bloc atomic) pour
        # être visibles dans le dashboard en temps réel pendant la sync.
        sync_log = SyncLog.objects.create(
            bank_connection=bank_connection,
            sync_type=sync_type,
            status=SyncLog.Status.STARTED,
            started_at=timezone.now(),
        )
        bank_connection.sync_status = BankConnection.SyncStatus.SYNCING
        bank_connection.save()

        logger.info(
            f"Début de synchronisation pour le compte {account.id} "
            f"(provider: {bank_connection.provider}, sync_type: {sync_type})"
        )

        try:
            # Déchiffrer les credentials
            try:
                credentials = EncryptionService.decrypt_credentials(
                    bank_connection.encrypted_credentials
                )
            except EncryptionError as e:
                error_msg = f"Erreur de déchiffrement des credentials: {str(e)}"
                logger.error(error_msg)
                raise ValueError(error_msg) from e

            # Sélectionner le connecteur approprié
            try:
                connector = SyncService._get_connector_for_provider(bank_connection.provider)
            except (ValueError, ImportError) as e:
                error_msg = f"Impossible de créer le connecteur: {str(e)}"
                logger.error(error_msg)
                raise ValueError(error_msg) from e

            # Authentification avec retry
            auth_result = None
            last_error = None

            for attempt in range(SyncService.MAX_RETRIES):
                try:
                    auth_result = connector.authenticate(credentials)
                    break
                except Exception as e:
                    last_error = e
                    if not SyncService._should_retry_error(e):
                        raise
                    if attempt < SyncService.MAX_RETRIES - 1:
                        wait_time = SyncService.BASE_RETRY_DELAY * (2 ** attempt)
                        logger.warning(
                            f"Tentative d'authentification {attempt + 1}/{SyncService.MAX_RETRIES} "
                            f"échouée, retry dans {wait_time}s: {str(e)}"
                        )
                        time.sleep(wait_time)
                    else:
                        raise

            # Gérer l'authentification 2FA si nécessaire
            if auth_result and auth_result.get("requires_2fa"):
                logger.info("Authentification 2FA requise")
                try:
                    connector.disconnect()
                except Exception:
                    pass
                sync_log.status = SyncLog.Status.ERROR
                sync_log.completed_at = timezone.now()
                sync_log.error_message = "Authentification 2FA requise"
                sync_log.save()

                bank_connection.sync_status = BankConnection.SyncStatus.ERROR
                bank_connection.save()

                return {
                    "success": False,
                    "transactions_count": 0,
                    "sync_log_id": sync_log.id,
                    "requires_2fa": True,
                    "error": "Authentification 2FA requise",
                }

            # Récupérer les transactions avec retry
            transactions_data = []
            since = bank_connection.last_sync_at

            for attempt in range(SyncService.MAX_RETRIES):
                try:
                    transactions_data = connector.sync_transactions(account, since=since)
                    break
                except Exception as e:
                    last_error = e
                    if not SyncService._should_retry_error(e):
                        raise
                    if attempt < SyncService.MAX_RETRIES - 1:
                        wait_time = SyncService.BASE_RETRY_DELAY * (2 ** attempt)
                        logger.warning(
                            f"Tentative de récupération des transactions {attempt + 1}/{SyncService.MAX_RETRIES} "
                            f"échouée, retry dans {wait_time}s: {str(e)}"
                        )
                        time.sleep(wait_time)
                    else:
                        raise

            # Récupérer le solde avec retry
            balance = None
            for attempt in range(SyncService.MAX_RETRIES):
                try:
                    balance = connector.get_balance(account)
                    break
                except Exception as e:
                    last_error = e
                    if not SyncService._should_retry_error(e):
                        raise
                    if attempt < SyncService.MAX_RETRIES - 1:
                        wait_time = SyncService.BASE_RETRY_DELAY * (2 ** attempt)
                        logger.warning(
                            f"Tentative de récupération du solde {attempt + 1}/{SyncService.MAX_RETRIES} "
                            f"échouée, retry dans {wait_time}s: {str(e)}"
                        )
                        time.sleep(wait_time)
                    else:
                        raise

            # Insérer chaque transaction dans son propre savepoint atomique.
            # Si une transaction échoue (contrainte, données invalides…), seul ce
            # savepoint est annulé — les autres continuent. Sans ce découpage, la
            # première exception marque le bloc atomique parent comme "needs rollback"
            # et toutes les insertions suivantes échouent avec
            # "An error occurred in the current transaction".
            source = bank_connection.provider

            for transaction_data in transactions_data:
                try:
                    with transaction.atomic():
                        SyncService._upsert_transaction_from_sync(
                            account, transaction_data, source
                        )
                    transactions_count += 1
                except Exception as e:
                    logger.warning(
                        f"Erreur lors de la création/mise à jour d'une transaction: {str(e)}"
                    )
                    continue

            # Synchroniser les valorisations de portefeuille si supporté
            portfolio_valuations = {}
            try:
                portfolio_result = connector.sync_portfolio_valuations(account)
                if portfolio_result:
                    portfolio_valuations = portfolio_result
                    logger.info(
                        f"Valorisations de portefeuille synchronisées: {portfolio_valuations}"
                    )
            except Exception as e:
                # Ne pas échouer la synchronisation si les valorisations échouent
                logger.warning(
                    f"Erreur lors de la synchronisation des valorisations de portefeuille: {str(e)}"
                )

            # Déconnecter le connecteur
            try:
                connector.disconnect()
            except Exception as e:
                logger.warning(f"Erreur lors de la déconnexion du connecteur: {str(e)}")

            # Ancrer le solde live sur le compte.
            # Le dashboard calcule : initial_balance + sum(transactions).
            # Pour que ce calcul donne toujours le vrai solde bancaire, on recalcule
            # initial_balance = live_balance - sum(toutes les transactions du compte).
            # Ainsi la formule est toujours exacte, même si des transactions manquent.
            if balance is not None:
                from django.db.models import Sum as _Sum
                tx_sum = Transaction.objects.filter(account=account).aggregate(
                    total=_Sum("amount")
                )["total"] or Decimal("0")
                account.initial_balance = balance - tx_sum
                account.balance_snapshot_date = timezone.now().date()
                account.save(update_fields=["initial_balance", "balance_snapshot_date"])
                logger.info(
                    f"Solde ancré pour le compte {account.id}: "
                    f"live={balance}, tx_sum={tx_sum}, initial_balance={account.initial_balance}"
                )

            # Mettre à jour le SyncLog en cas de succès
            sync_log.status = SyncLog.Status.SUCCESS
            sync_log.completed_at = timezone.now()
            sync_log.transactions_count = transactions_count
            sync_log.save()

            # Mettre à jour la BankConnection en cas de succès
            bank_connection.sync_status = BankConnection.SyncStatus.SUCCESS
            bank_connection.last_sync_at = timezone.now()
            bank_connection.save()

            logger.info(
                f"Synchronisation réussie pour le compte {account.id}: "
                f"{transactions_count} transactions synchronisées"
            )

            return {
                "success": True,
                "transactions_count": transactions_count,
                "sync_log_id": sync_log.id,
                "portfolio_valuations": portfolio_valuations,
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Erreur lors de la synchronisation du compte {account.id}: {error_msg}",
                exc_info=True,
            )

            # Mettre à jour le SyncLog en cas d'erreur
            sync_log.status = SyncLog.Status.ERROR
            sync_log.completed_at = timezone.now()
            sync_log.error_message = error_msg[:1000]  # Tronquer si trop long
            sync_log.transactions_count = transactions_count
            sync_log.save()

            # Mettre à jour la BankConnection en cas d'erreur
            bank_connection.sync_status = BankConnection.SyncStatus.ERROR
            bank_connection.save()

            # Déconnecter le connecteur en cas d'erreur
            if connector:
                try:
                    connector.disconnect()
                except Exception:
                    pass  # Ignorer les erreurs de déconnexion

            return {
                "success": False,
                "transactions_count": transactions_count,
                "sync_log_id": sync_log.id,
                "error": error_msg,
            }
