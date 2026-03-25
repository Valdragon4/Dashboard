from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.db import transaction
from django.db.models import Max

from finance.importers.statement_csv import StatementEntry, parse_statement_csv
from finance.importers.traderepublic_csv import TradeRepublicEntry, parse_traderepublic_csv
from finance.models import Account, Category, Transaction


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
        }
        if entry.quantity is not None:
            raw["quantity"] = str(entry.quantity)
        # Ajouter l'ID unique Trade Republic pour déduplication
        if entry.transaction_id:
            raw["transaction_id"] = entry.transaction_id
            
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
    
    return imported_count


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


