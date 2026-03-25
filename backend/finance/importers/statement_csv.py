from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


from django.conf import settings
from django.utils import timezone


logger = logging.getLogger(__name__)


@dataclass
class StatementEntry:
    posted_at: datetime
    amount: Decimal
    description: str
    category_name: str | None = None
    category_parent: str | None = None
    counterparty: str | None = None
    account_balance: Decimal | None = None
    csv_line_number: int | None = None  # Numéro de ligne pour garantir l'unicité


def _parse_date(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            if settings.USE_TZ and timezone.is_naive(parsed):
                return timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except ValueError:
            continue
    raise ValueError(f"Format de date inconnu: {value}")


def _parse_amount(value: str) -> Decimal:
    cleaned = value.replace("\xa0", " ").strip()
    cleaned = cleaned.replace("€", "").replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Montant invalide: {value}") from exc


def parse_statement_csv(path: str | Path, profile: str = "generic") -> Iterable[StatementEntry]:
    """Parse un relevé CSV et retourne des StatementEntry normalisés.

    `profile` peut valoir "generic", "boursobank", "hellobank" ou "hellobank_livret".
    """

    profile = (profile or "generic").lower()
    
    # Pour Hello Bank Livret, même format que Hello Bank mais avec ajustement du solde initial
    # La première ligne contient le solde actuel dans la 6ème colonne
    if profile == "hellobank_livret":
        with open(path, newline="", encoding="utf-8") as fh:
            # Forcer le délimiteur à point-virgule pour Hello Bank
            class SemicolonDialect(csv.excel):
                delimiter = ';'
            reader = csv.reader(fh, dialect=SemicolonDialect())
            
            # Lire la première ligne pour extraire le solde actuel
            try:
                header_line = next(reader)
                current_balance_str = header_line[5].strip() if len(header_line) > 5 else "0"
                current_balance = _parse_amount(current_balance_str)
                logger.info(f"Hello Bank Livret: Solde actuel extrait = {current_balance}")
            except (StopIteration, IndexError, ValueError) as e:
                logger.error(f"Impossible de lire le solde actuel depuis la première ligne: {e}")
                current_balance = Decimal("0")
            
            # Parser toutes les transactions
            transactions = []
            entry_count = 0
            for line_number, row_values in enumerate(reader, start=2):  # start=2 car ligne 1 est l'en-tête
                # Ignorer les lignes vides ou incomplètes
                if len(row_values) < 5:
                    continue
                
                # Format Hello Bank : Date;Type;Libellé court;Libellé détaillé;Montant
                date_str = row_values[0].strip() if len(row_values) > 0 else ""
                operation_type = row_values[1].strip() if len(row_values) > 1 else ""
                label_short = row_values[2].strip() if len(row_values) > 2 else ""
                label_detailed = row_values[3].strip() if len(row_values) > 3 else ""
                amount_str = row_values[4].strip() if len(row_values) > 4 else ""
                
                # Ignorer les lignes sans date ou sans montant
                if not date_str or not amount_str:
                    continue
                
                # Construire la description en combinant les libellés
                description_parts = []
                if label_short:
                    description_parts.append(label_short)
                if label_detailed:
                    description_parts.append(label_detailed)
                if not description_parts and operation_type:
                    description_parts.append(operation_type)
                description = " - ".join(description_parts) if description_parts else "Transaction"
                
                try:
                    # Parser la date
                    posted_at = _parse_date(date_str)
                    
                    # Parser le montant (virgule comme séparateur décimal)
                    amount = _parse_amount(amount_str)
                    
                    transactions.append(StatementEntry(
                        posted_at=posted_at,
                        amount=amount,
                        description=description,
                        category_name=None,
                        category_parent=None,
                        counterparty=None,
                        account_balance=None,
                        csv_line_number=line_number,  # Ajouter le numéro de ligne
                    ))
                    entry_count += 1
                except (ValueError, InvalidOperation) as e:
                    logger.warning(f"Erreur lors du parsing de la ligne Hello Bank Livret: {e}. Ligne: {row_values}")
                    continue
            
            # Calculer la somme de toutes les transactions
            total_transactions = sum(t.amount for t in transactions)
            logger.info(f"Hello Bank Livret: {entry_count} transactions parsées, somme = {total_transactions}")
            
            # Pour le livret, on stocke le solde cible et la somme des transactions dans les métadonnées
            # Le loader utilisera ces infos pour calculer l'initial_balance correctement
            # initial_balance = current_balance - total_transactions
            calculated_initial_balance = current_balance - total_transactions
            logger.info(f"Hello Bank Livret: Solde initial calculé = {calculated_initial_balance}")
            
            # Stocker ces métadonnées dans le premier entry pour que le loader puisse les récupérer
            if transactions:
                # On va stocker ces infos dans un champ spécial du premier entry
                # En créant un nouvel attribut temporaire
                transactions[0]._livret_metadata = {
                    "current_balance": current_balance,
                    "calculated_initial_balance": calculated_initial_balance
                }
            
            # Retourner toutes les transactions
            for entry in transactions:
                yield entry
            
            logger.info(f"Hello Bank Livret: Import terminé avec {len(transactions)} transactions")
            return
    
    # Pour Hello Bank, le format est différent : pas d'en-têtes de colonnes standard
    # Format : Date;Type;Libellé court;Libellé détaillé;Montant
    if profile == "hellobank":
        with open(path, newline="", encoding="utf-8") as fh:
            # Forcer le délimiteur à point-virgule pour Hello Bank
            class SemicolonDialect(csv.excel):
                delimiter = ';'
            reader = csv.reader(fh, dialect=SemicolonDialect())
            # Ignorer la première ligne (en-tête du compte)
            try:
                next(reader)
            except StopIteration:
                pass
            
            entry_count = 0
            for row_values in reader:
                # Ignorer les lignes vides ou incomplètes
                if len(row_values) < 5:
                    continue
                
                # Format Hello Bank : Date;Type;Libellé court;Libellé détaillé;Montant
                date_str = row_values[0].strip() if len(row_values) > 0 else ""
                operation_type = row_values[1].strip() if len(row_values) > 1 else ""
                label_short = row_values[2].strip() if len(row_values) > 2 else ""
                label_detailed = row_values[3].strip() if len(row_values) > 3 else ""
                amount_str = row_values[4].strip() if len(row_values) > 4 else ""
                
                # Ignorer les lignes sans date ou sans montant
                if not date_str or not amount_str:
                    continue
                
                # Construire la description en combinant les libellés
                description_parts = []
                if label_short:
                    description_parts.append(label_short)
                if label_detailed:
                    description_parts.append(label_detailed)
                if not description_parts and operation_type:
                    description_parts.append(operation_type)
                description = " - ".join(description_parts) if description_parts else "Transaction"
                
                try:
                    # Parser la date
                    posted_at = _parse_date(date_str)
                    
                    # Parser le montant (virgule comme séparateur décimal)
                    amount = _parse_amount(amount_str)
                except (ValueError, InvalidOperation) as e:
                    logger.warning(f"Erreur lors du parsing de la ligne Hello Bank: {e}. Ligne: {row_values}")
                    continue
                
                yield StatementEntry(
                    posted_at=posted_at,
                    amount=amount,
                    description=description,
                    category_name=None,
                    category_parent=None,
                    counterparty=None,
                    account_balance=None,
                )
                entry_count += 1
            
            logger.info(f"Hello Bank: {entry_count} transactions parsées depuis le CSV")
            return
    
    # Pour les autres profils, utiliser DictReader avec en-têtes
    with open(path, newline="", encoding="utf-8") as fh:
        sample = fh.read(4096)
        fh.seek(0)

        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV sans en-têtes : impossible de parser")
        fieldnames_original = [fn.lstrip("\ufeff") if isinstance(fn, str) else fn for fn in reader.fieldnames]
        reader.fieldnames = fieldnames_original
        fields = [f.strip().lower() for f in fieldnames_original]

        def get(row: dict[str, str], *candidates: str) -> str:
            for key in candidates:
                if key in row and row[key].strip():
                    return row[key]
            logger.warning(
                "Colonnes disponibles dans le CSV: %s",
                ", ".join(fn or "<vide>" for fn in fieldnames_original),
            )
            raise KeyError(
                f"Colonne manquante ({candidates}) dans le CSV. Colonnes disponibles: {fieldnames_original}"
            )

        for raw_row in reader:
            # Normaliser les clés/valeurs (assure chaîne vide si None)
            row = {
                (k or "").strip().lower(): (raw_row[k] or "")
                for k in raw_row.keys()
            }

            if profile == "boursobank":
                date_str = get(row, "dateop", "date operation", "date", "dateval")
                description_parts = [
                    row.get("label", "").strip(),
                    row.get("comment", "").strip(),
                ]
                description = " - ".join(part for part in description_parts if part)
                if not description:
                    description = get(row, "label", "description", "libellé", "libelle")
                amount_str = get(row, "amount", "montant")
                category_name = (row.get("category", "") or "").strip() or None
                category_parent = (row.get("categoryparent", "") or "").strip() or None
                counterparty = (row.get("supplierfound", "") or "").strip() or None
                account_balance_str = (row.get("accountbalance", "") or "").strip()
                account_balance = _parse_amount(account_balance_str) if account_balance_str else None
            else:
                date_str = get(row, "date", "posted_at")
                description = row.get("description", "")
                amount_str = get(row, "amount", "montant")
                category_name = None
                category_parent = None
                counterparty = None
                account_balance = None

            posted_at = _parse_date(date_str)

            if profile == "boursobank" and amount_str == "":
                credit = row.get("credit", "")
                debit = row.get("debit", "")
                amount_str = credit or debit or "0"
                if debit:
                    amount = -_parse_amount(debit)
                elif credit:
                    amount = _parse_amount(credit)
                else:
                    amount = Decimal("0")
            else:
                amount = _parse_amount(amount_str)

            if profile == "boursobank" and row.get("debit") and not row.get("credit"):
                amount = -_parse_amount(row["debit"])

            yield StatementEntry(
                posted_at=posted_at,
                amount=amount,
                description=description.strip(),
                category_name=category_name,
                category_parent=category_parent,
                counterparty=counterparty,
                account_balance=account_balance,
            )


