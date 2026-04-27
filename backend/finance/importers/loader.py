from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import logging

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from finance.importers.statement_csv import StatementEntry, parse_statement_csv
from finance.importers.traderepublic_csv import TradeRepublicEntry, parse_traderepublic_csv
from finance.models import Account, Category, Transaction
from finance.services.market_price_service import MarketPriceService

logger = logging.getLogger(__name__)


def _safe_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _normalize_portfolio_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper().replace("_", "-")
    if normalized in {"CTO", "PEA", "CRYPTO", "PEA-PME"}:
        return normalized
    return None


def _build_isin_portfolio_type_map(account: Account) -> dict[str, str]:
    """
    Construit une table ISIN -> type de portefeuille à partir des snapshots existants
    (notamment ceux importés depuis PDF) pour stabiliser la classification auto.
    """
    isin_map: dict[str, str] = {}
    snapshot_txs = (
        Transaction.objects.filter(account=account, amount=Decimal("0"))
        .order_by("-posted_at", "-id")
    )
    for tx in snapshot_txs:
        raw = tx.raw if isinstance(tx.raw, dict) else {}
        snapshot_type = _normalize_portfolio_type(raw.get("portfolio_type"))
        if not snapshot_type:
            continue

        data = raw.get("data")
        titres = []
        if isinstance(data, dict):
            titres = data.get("titres", []) if isinstance(data.get("titres"), list) else []
        elif isinstance(data, list):
            titres = data

        for titre in titres:
            if not isinstance(titre, dict):
                continue
            isin = (titre.get("isin") or titre.get("symbole") or "").strip().upper()
            if isin and isin not in isin_map:
                isin_map[isin] = snapshot_type
    return isin_map


def _guess_portfolio_type_from_raw(raw: dict, tx: Transaction, isin_portfolio_type_map: dict[str, str] | None = None) -> str:
    explicit = _normalize_portfolio_type(raw.get("portfolio_type"))
    if explicit:
        return explicit

    event_type = (raw.get("eventType") or "").upper()
    if event_type.startswith("PEA_"):
        return "PEA"

    isin = (raw.get("isin") or "").upper()
    if isin and isin_portfolio_type_map and isin in isin_portfolio_type_map:
        return isin_portfolio_type_map[isin]
    text = f"{raw.get('instrument') or ''} {tx.description or ''}".lower()
    crypto_keywords = ("btc", "eth", "crypto", "bitcoin", "ethereum", "solana", "xrp")
    if isin.startswith("XF000") or any(keyword in text for keyword in crypto_keywords):
        return "CRYPTO"

    return "CTO"


def _compute_tr_positions_valuation_for_snapshot(
    account: Account,
    isin_portfolio_type_map: dict[str, str] | None = None,
) -> dict[str, Decimal]:
    """
    Calcule la valorisation nette du portefeuille Trade Republic
    à partir des transactions enrichies (ISIN, quantité, prix).
    """
    positions_by_type: dict[str, dict[str, dict]] = {}
    has_enriched_data = False
    transactions = (
        Transaction.objects.filter(account=account)
        .exclude(amount=Decimal("0"))
        .order_by("posted_at", "id")
    )
    for tx in transactions:
        raw = tx.raw if isinstance(tx.raw, dict) else {}
        isin = raw.get("isin")
        quantity = _safe_decimal(raw.get("investment_quantity"))
        price = _safe_decimal(raw.get("current_price"))
        if not isin or quantity is None or price is None:
            continue

        has_enriched_data = True
        sign = Decimal("1") if tx.amount > 0 else Decimal("-1")
        signed_quantity = quantity * sign
        portfolio_type = _guess_portfolio_type_from_raw(raw, tx, isin_portfolio_type_map)
        type_positions = positions_by_type.setdefault(portfolio_type, {})
        bucket = type_positions.setdefault(
            isin,
            {"quantity": Decimal("0"), "latest_price": price},
        )
        bucket["quantity"] += signed_quantity
        bucket["latest_price"] = price

    if not has_enriched_data:
        return {}

    totals: dict[str, Decimal] = {}
    for portfolio_type, type_positions in positions_by_type.items():
        total = Decimal("0")
        for data in type_positions.values():
            if data["quantity"] > 0:
                total += data["quantity"] * data["latest_price"]
        if total > 0:
            totals[portfolio_type] = total
    return totals


@transaction.atomic
def import_bank_statement_from_csv(
    *,
    user,
    csv_path: str | Path,
    account_name: str,
    profile: str = "generic",
    account_type: str | None = None,
) -> int:
    profile = (profile or "generic").lower()
    account_type = account_type or Account.AccountType.CHECKING

    account, created = Account.objects.get_or_create(
        owner=user,
        name=account_name,
        defaults={
            "type": account_type,
            "currency": "EUR",
            "provider": profile,
        },
    )
    updates: list[str] = []
    if not created:
        if account.provider != profile:
            account.provider = profile
            updates.append("provider")
        if account.type != account_type:
            account.type = account_type
            updates.append("type")
        if updates:
            account.save(update_fields=updates)

    # Collecter toutes les entrées pour trouver le solde initial
    entries = list(parse_statement_csv(csv_path, profile=profile))
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🔍 Import avec profil: {profile}, {len(entries)} entrées")
    
    # Pour hellobank_livret, utiliser les métadonnées pour définir l'initial_balance
    if profile == "hellobank_livret":
        logger.info(f"✅ Profil hellobank_livret détecté")
        if entries and hasattr(entries[0], '_livret_metadata'):
            metadata = entries[0]._livret_metadata
            calculated_initial_balance = metadata["calculated_initial_balance"]
            current_balance = metadata["current_balance"]
            
            logger.info(f"📊 Métadonnées trouvées:")
            logger.info(f"   - Solde cible (CSV): {current_balance}")
            logger.info(f"   - Solde initial calculé: {calculated_initial_balance}")
            
            # Mettre à jour l'initial_balance du compte
            account.initial_balance = calculated_initial_balance
            # Utiliser la date de la transaction la plus ancienne
            if entries:
                oldest_date = min(e.posted_at for e in entries)
                account.balance_snapshot_date = oldest_date.date()
            account.save(update_fields=["initial_balance", "balance_snapshot_date"])
            
            logger.info(f"✅ initial_balance mis à jour à {calculated_initial_balance}")
            
            # Afficher aussi dans les logs de manière plus visible
            print(f"\n{'='*60}")
            print(f"HELLO BANK LIVRET - Configuration du solde")
            print(f"{'='*60}")
            print(f"Solde cible (CSV):        {current_balance} €")
            print(f"Somme des transactions:   {current_balance - calculated_initial_balance} €")
            print(f"Solde initial calculé:    {calculated_initial_balance} €")
            print(f"{'='*60}\n")
        else:
            error_msg = f"❌ ERREUR LIVRET: Pas de métadonnées! entries={len(entries) if entries else 0}"
            logger.error(error_msg)
            print(f"\n{'='*60}")
            print(f"ERREUR: {error_msg}")
            print(f"{'='*60}\n")
            if entries:
                logger.error(f"   - hasattr _livret_metadata: {hasattr(entries[0], '_livret_metadata')}")
                print(f"Attributs de la première entrée: {dir(entries[0])}")
    else:
        # Logique existante pour les autres profils
        # Trouver la transaction la plus ancienne avec un solde
        oldest_entry = None
        oldest_date = None
        for entry in entries:
            if entry.account_balance is not None:
                if oldest_date is None or entry.posted_at < oldest_date:
                    oldest_date = entry.posted_at
                    oldest_entry = entry
        
        # Calculer le solde initial si on a trouvé une transaction avec solde
        if oldest_entry and oldest_entry.account_balance is not None:
            # Le solde initial = solde après la transaction - montant de la transaction
            # Mais il faut aussi tenir compte des transactions précédentes dans le CSV
            # qui n'ont pas de solde mais qui sont antérieures à la transaction avec solde
            initial_balance = oldest_entry.account_balance - oldest_entry.amount
            
            # Soustraire toutes les transactions qui sont antérieures à la transaction avec solde
            # pour obtenir le vrai solde initial
            for entry in entries:
                if entry.posted_at < oldest_date:
                    initial_balance -= entry.amount
            
            # Mettre à jour le solde initial seulement si :
            # - Le compte n'a pas encore de solde initial, OU
            # - La date de la transaction la plus ancienne est antérieure à la date du snapshot actuel
            should_update_balance = False
            if account.balance_snapshot_date is None:
                should_update_balance = True
            elif oldest_date.date() < account.balance_snapshot_date:
                should_update_balance = True
            
            if should_update_balance:
                account.initial_balance = initial_balance
                account.balance_snapshot_date = oldest_date.date()
                account.save(update_fields=["initial_balance", "balance_snapshot_date"])
    
    count = 0
    for entry in entries:
        _upsert_transaction(
            account,
            entry.posted_at,
            entry.amount,
            entry.description,
            source=profile,
            category_name=entry.category_name,
            category_parent=entry.category_parent,
            counterparty=entry.counterparty,
            account_balance=entry.account_balance,
            csv_line_number=entry.csv_line_number,
        )
        count += 1
    return count


@transaction.atomic
def import_traderepublic_from_csv(
    *,
    user,
    csv_path: str | Path,
    account_name: str,
    currency: str = "EUR",
) -> int:
    import logging
    logger = logging.getLogger(__name__)
    
    account, created = Account.objects.get_or_create(
        owner=user,
        name=account_name,
        defaults={
            "type": Account.AccountType.BROKER,
            "currency": currency,
            "provider": "traderepublic",
        },
    )
    updates: list[str] = []
    if not created:
        if account.provider != "traderepublic":
            account.provider = "traderepublic"
            updates.append("provider")
        if account.currency != currency:
            account.currency = currency
            updates.append("currency")
        if account.type != Account.AccountType.BROKER:
            account.type = Account.AccountType.BROKER
            updates.append("type")
        if updates:
            account.save(update_fields=updates)

    logger.info("=" * 100)
    logger.info(f"📥 IMPORT CSV → BASE DE DONNÉES - Compte: {account_name}")
    logger.info(f"📂 Fichier CSV: {csv_path}")
    logger.info("=" * 100)

    latest_posted_at = (
        Transaction.objects.filter(account=account)
        .exclude(amount=Decimal("0"))
        .aggregate(max_date=Max("posted_at"))
        .get("max_date")
    )
    
    logger.info("🔄 SYNCHRONISATION INCRÉMENTALE ACTIVÉE")
    if latest_posted_at:
        logger.info(f"🕒 Dernière transaction en base : {latest_posted_at}")
    else:
        logger.info("🆕 Aucune transaction existante : import complet")

    existing_transaction_ids = set(
        Transaction.objects.filter(account=account)
        .exclude(raw__transaction_id__isnull=True)
        .values_list("raw__transaction_id", flat=True)
    )
    
    imported_count = 0
    skipped_count = 0
    price_service = MarketPriceService()
    isin_portfolio_type_map = _build_isin_portfolio_type_map(account)

    for entry in parse_traderepublic_csv(csv_path):
        if latest_posted_at and entry.posted_at <= latest_posted_at:
            if entry.transaction_id and entry.transaction_id not in existing_transaction_ids:
                logger.info(
                    "⚠️ Transaction plus ancienne mais nouvel ID détecté, import malgré tout - ID: %s",
                    entry.transaction_id,
                )
            else:
                skipped_count += 1
                logger.info(
                    "⏭️ Transaction ignorée (ancienne) - Date: %s | Description: %s | Montant: %s",
                    entry.posted_at,
                    entry.description,
                    entry.amount,
                )
                continue
        
        raw = {
            "source": "traderepublic",
            "instrument": entry.instrument,
            "isin": entry.isin,
            "eventType": entry.event_type,
            "icon": entry.icon,
        }
        if entry.quantity is not None:
            raw["quantity"] = str(entry.quantity)
            raw["investment_quantity"] = str(entry.quantity)
        if entry.unit_price is not None:
            raw["unit_price_at_trade"] = str(entry.unit_price)
        if entry.total_invested is not None:
            raw["investment_total"] = str(entry.total_invested)
        if entry.portfolio_type:
            raw["portfolio_type"] = entry.portfolio_type
        elif entry.isin and entry.isin.upper() in isin_portfolio_type_map:
            raw["portfolio_type"] = isin_portfolio_type_map[entry.isin.upper()]
        # Ajouter l'ID unique Trade Republic pour déduplication
        if entry.transaction_id:
            raw["transaction_id"] = entry.transaction_id

        _enrich_traderepublic_valuation(raw=raw, entry=entry, price_service=price_service)
        if raw.get("is_investment_event"):
            logger.info(
                "TR valuation tx_id=%s event=%s isin=%s qty=%s invested=%s current_price=%s partial=%s unavailable=%s",
                entry.transaction_id,
                entry.event_type,
                raw.get("isin"),
                raw.get("investment_quantity"),
                raw.get("investment_total"),
                raw.get("current_price"),
                raw.get("valuation_partial"),
                raw.get("pricing_unavailable"),
            )
        else:
            logger.info(
                "TR non-investment tx_id=%s event=%s title=%s",
                entry.transaction_id,
                entry.event_type,
                entry.description,
            )
            
        _upsert_transaction(
            account,
            entry.posted_at,
            entry.amount,
            entry.description,
            source="traderepublic",
            raw=raw,
        )
        imported_count += 1
        if entry.transaction_id:
            existing_transaction_ids.add(entry.transaction_id)
    
    logger.info("=" * 100)
    logger.info(
        "✅ IMPORT TERMINÉ - %s transaction(s) importée(s), %s ignorée(s)",
        imported_count,
        skipped_count,
    )
    logger.info("=" * 100)
    snapshot_valuations = _compute_tr_positions_valuation_for_snapshot(
        account,
        isin_portfolio_type_map=isin_portfolio_type_map,
    )
    if snapshot_valuations:
        sync_time = timezone.now()
        for portfolio_type, snapshot_valuation in snapshot_valuations.items():
            Transaction.objects.create(
                account=account,
                posted_at=sync_time,
                amount=Decimal("0"),
                description=f"Snapshot valorisation {portfolio_type} (sync auto)",
                currency=account.currency or "EUR",
                account_balance=snapshot_valuation,
                raw={
                    "source": "traderepublic_valuation_sync",
                    "is_sync_valuation_snapshot": True,
                    "valuation_method": "net_positions_by_isin",
                    "valuation_total": str(snapshot_valuation),
                    "portfolio_type": portfolio_type,
                },
            )
            logger.info(
                "📸 Snapshot %s TR enregistré à %s : %s",
                portfolio_type,
                sync_time.isoformat(),
                snapshot_valuation,
            )

    return imported_count


def _enrich_traderepublic_valuation(
    *,
    raw: dict,
    entry: TradeRepublicEntry,
    price_service: MarketPriceService,
) -> None:
    investable_event_types = {
        "TRADE_INVOICE",
        "SAVINGS_PLAN_INVOICE_CREATED",
        "TRADING_SAVINGSPLAN_EXECUTED",
        "PEA_SAVINGS_PLAN_PAY_IN",
        "TRADING_TRADE_EXECUTED",
    }
    has_investment_shape = bool(entry.isin and entry.quantity is not None)
    raw["is_investment_event"] = (
        entry.event_type in investable_event_types or has_investment_shape
    )
    if not raw["is_investment_event"]:
        return

    if not entry.isin:
        raw["pricing_unavailable"] = True
        logger.warning(
            "TR valuation skipped: missing ISIN tx_id=%s event=%s description=%s",
            entry.transaction_id,
            entry.event_type,
            entry.description,
        )
        return

    invested_total = entry.total_invested
    if invested_total is None:
        invested_total = abs(entry.amount)
    raw["investment_total"] = str(invested_total)

    price_data = price_service.get_price_for_isin(entry.isin)
    if (not price_data) and entry.unit_price is not None:
        # Fallback non-live pour garantir un minimum de valorisation
        # si la résolution Yahoo/API échoue pour un ISIN exotique.
        price_data = {
            "price": entry.unit_price,
            "source": "transaction_unit_price_fallback",
            "symbol": entry.isin,
            "priced_at": None,
        }
        logger.warning(
            "TR live price unavailable, fallback unit price used tx_id=%s isin=%s unit_price=%s",
            entry.transaction_id,
            entry.isin,
            entry.unit_price,
        )
    if not price_data:
        raw["pricing_unavailable"] = True
        logger.error(
            "TR valuation unavailable: no price source tx_id=%s isin=%s event=%s",
            entry.transaction_id,
            entry.isin,
            entry.event_type,
        )
        return

    current_price = price_data["price"]
    raw["current_price"] = str(current_price)
    raw["pricing_source"] = price_data.get("source")
    raw["pricing_symbol"] = price_data.get("symbol")
    raw["priced_at"] = price_data.get("priced_at")
    raw["pricing_unavailable"] = False

    effective_quantity = entry.quantity
    if effective_quantity is None and entry.unit_price is not None and entry.unit_price > 0:
        effective_quantity = invested_total / entry.unit_price
        raw["investment_quantity"] = str(effective_quantity)
        raw["quantity_inferred"] = True
        logger.info(
            "TR valuation inferred quantity from unit price tx_id=%s isin=%s qty=%s invested=%s unit_price=%s",
            entry.transaction_id,
            entry.isin,
            effective_quantity,
            invested_total,
            entry.unit_price,
        )
    if effective_quantity is None and current_price > 0:
        # Fallback d'estimation pour les événements où TR ne renvoie pas la quantité.
        # On garde une trace explicite pour distinguer cette valeur d'une quantité source.
        effective_quantity = invested_total / current_price
        raw["investment_quantity"] = str(effective_quantity)
        raw["quantity_estimated_from_live_price"] = True
        logger.warning(
            "TR valuation estimated quantity from live price tx_id=%s isin=%s qty=%s invested=%s current_price=%s",
            entry.transaction_id,
            entry.isin,
            effective_quantity,
            invested_total,
            current_price,
        )

    if effective_quantity is not None:
        current_value = effective_quantity * current_price
        profit_loss = current_value - invested_total
        raw["current_value"] = str(current_value)
        raw["profit_loss"] = str(profit_loss)
        raw.pop("valuation_partial", None)
    else:
        raw["current_value"] = None
        raw["profit_loss"] = None
        raw["valuation_partial"] = True
        logger.warning(
            "TR valuation partial: missing quantity tx_id=%s isin=%s event=%s",
            entry.transaction_id,
            entry.isin,
            entry.event_type,
        )


def _upsert_transaction(
    account: Account,
    posted_at,
    amount,
    description: str,
    *,
    source: str,
    raw: dict | None = None,
    category_name: str | None = None,
    category_parent: str | None = None,
    counterparty: str | None = None,
    account_balance: Decimal | None = None,
    csv_line_number: int | None = None,
) -> None:
    # On n'inclut pas 'description' dans defaults car elle fait partie des critères de recherche
    defaults = {
        "currency": account.currency or "EUR",
        "raw": raw or {"source": source},
    }
    if "source" not in defaults["raw"]:
        defaults["raw"]["source"] = source
    
    # Ajouter le numéro de ligne dans raw pour traçabilité
    if csv_line_number is not None:
        defaults["raw"]["csv_line_number"] = csv_line_number

    if counterparty:
        defaults["counterparty"] = counterparty

    if category_name:
        defaults["category"] = _get_or_create_category(category_name, category_parent)
    
    if account_balance is not None:
        defaults["account_balance"] = account_balance

    # Priorité de déduplication :
    # 1. Si on a un transaction_id Trade Republic (ID unique) → utiliser celui-ci
    # 2. Sinon, si on a un csv_line_number → utiliser celui-ci
    # 3. Sinon, utiliser date + montant + description (ancien comportement)
    
    transaction_id = raw.get("transaction_id") if raw else None
    
    if transaction_id:
        # Utiliser l'ID unique de Trade Republic pour la déduplication (MEILLEUR)
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
        else:
            # Créer une nouvelle transaction
            Transaction.objects.create(
                account=account,
                posted_at=posted_at,
                amount=Decimal(amount),
                description=description,
                **defaults,
            )
    elif csv_line_number is not None:
        # Vérifier si une transaction existe déjà avec ce numéro de ligne
        existing = Transaction.objects.filter(
            account=account,
            posted_at=posted_at,
            amount=Decimal(amount),
            raw__csv_line_number=csv_line_number,
        ).first()
        
        if existing:
            # Mettre à jour la transaction existante
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.description = description
            existing.save()
        else:
            # Créer une nouvelle transaction
            Transaction.objects.create(
                account=account,
                posted_at=posted_at,
                amount=Decimal(amount),
                description=description,
                **defaults,
            )
    else:
        # Utiliser la description dans les critères de recherche pour éviter les collisions
        # entre transactions avec le même compte, date et montant
        Transaction.objects.update_or_create(
            account=account,
            posted_at=posted_at,
            amount=Decimal(amount),
            description=description,
            defaults=defaults,
        )


def _get_or_create_category(name: str, parent_name: str | None = None) -> Category:
    parent_obj = None
    if parent_name:
        parent_obj, _ = Category.objects.get_or_create(name=parent_name)

    category, created = Category.objects.get_or_create(
        name=name,
        defaults={"parent": parent_obj},
    )
    if not created and category.parent_id != (parent_obj.id if parent_obj else None):
        category.parent = parent_obj
        category.save(update_fields=["parent"])
    return category


