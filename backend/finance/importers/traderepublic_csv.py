from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.utils import timezone


@dataclass
class TradeRepublicEntry:
    posted_at: datetime
    amount: Decimal
    description: str
    instrument: str | None
    isin: str | None
    quantity: Decimal | None
    transaction_id: str | None  # ID unique de Trade Republic pour déduplication


def _parse_date(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M", "%d.%m.%Y", "%d.%m.%Y %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            if settings.USE_TZ and timezone.is_naive(parsed):
                return timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except ValueError:
            continue
    raise ValueError(f"Impossible de parser la date Trade Republic: {value}")


def _parse_decimal(value: str) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.replace("\xa0", " ").strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("€", "").replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Impossible de parser le montant Trade Republic: {value}")


def parse_traderepublic_csv(path: str | Path) -> Iterable[TradeRepublicEntry]:
    """Parse un export CSV Trade Republic (cash movements)."""
    with open(path, newline="", encoding="utf-8") as fh:
        # Détecter le séparateur (virgule ou point-virgule)
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV Trade Republic sans en-têtes")

        def get(row: dict[str, str], *candidates: str) -> str:
            for key in candidates:
                if key in row and row[key].strip():
                    return row[key]
            return ""

        for raw in reader:
            row = {k: v for k, v in raw.items()}  # conserver la casse originale
            # Normaliser les clés en minuscules pour la recherche
            row_lower = {k.lower(): v for k, v in row.items()}
            
            # Chercher la date dans différentes colonnes possibles
            date_str = (
                get(row, "Date", "date")
                or get(row, "Time", "time")
                or get(row, "Timestamp", "timestamp")
                or row_lower.get("timestamp", "")
            )
            if not date_str:
                continue  # Ignorer les lignes sans date
            posted_at = _parse_date(date_str)

            # Récupérer l'ID unique de Trade Republic (si disponible)
            transaction_id = (
                get(row, "ID", "id", "Id") 
                or row_lower.get("id", "")
            ) or None
            
            # Chercher la description
            description = get(row, "Type", "type") or row_lower.get("type", "")
            details = get(row, "Description", "description") or row_lower.get("description", "")
            instrument = (
                get(row, "Instrument", "instrument", "Name", "name")
                or row_lower.get("instrument", "")
                or row_lower.get("name", "")
            ) or None
            isin = get(row, "ISIN", "isin") or row_lower.get("isin", "") or None
            if details:
                description = f"{description} - {details}" if description else details
            if not description:
                description = row_lower.get("title", "") or "Transaction Trade Republic"

            # Chercher le montant dans différentes colonnes possibles
            amount_str = (
                get(row, "Amount", "amount", "Value", "value")
                or row_lower.get("amount.value", "")
                or row_lower.get("value", "")
            )
            # Si le montant est dans amount.value, il faut le convertir
            if amount_str and "," in amount_str:
                amount_str = amount_str.replace(",", ".")
            amount = _parse_decimal(amount_str) if amount_str else Decimal("0")
            
            # Inverser le signe : les sorties du compte espèce sont des investissements (patrimoine)
            # On transforme les dépenses (-) en investissements (+)
            amount = -amount
            
            quantity = _parse_decimal(
                get(row, "Quantity", "quantity", "Shares", "shares")
                or row_lower.get("quantity", "")
                or row_lower.get("shares", "")
            )
            
            # Filtrer les transactions non-investissement : virements personnels, versements, intérêts
            # Note : on ne filtre pas les doublons ici car on a l'ID de transaction pour la déduplication
            description_clean = description.strip().lower()
            filtered_keywords = [
                "valentin marot",
                "m valentin marot",
                "versement",
                "versements",
                "intérêts",
                "interet",
                "interets",
                "interest",
                "interests"
            ]
            
            # Vérifier si la transaction doit être filtrée
            is_filtered = any(keyword in description_clean for keyword in filtered_keywords)
            
            if is_filtered:
                import logging
                logger = logging.getLogger(__name__)
                matched_keyword = next((kw for kw in filtered_keywords if kw in description_clean), None)
                logger.info(f"🚫 TRANSACTION IGNORÉE - Mot-clé: '{matched_keyword}'")
                logger.info(f"   📅 Date: {posted_at}")
                logger.info(f"   💰 Montant: {amount}")
                logger.info(f"   📝 Description: {description}")
                logger.info("-" * 80)
                continue  # Ignorer cette transaction

            yield TradeRepublicEntry(
                posted_at=posted_at,
                amount=amount,
                description=description.strip(),
                instrument=instrument,
                isin=isin,
                quantity=quantity,
                transaction_id=transaction_id,
            )


