"""
Import des relevés CSV Bitstack.

Bitstack (PSAN agréé AMF) n'expose aucune API : le seul canal de données est
l'export CSV du relevé de compte (profil → Documents → Relevés de compte).
Ce module transforme ce CSV en transactions du dashboard.

Conventions de modélisation retenues (alignées sur les comptes courtier existants) :

- Le compte Bitstack est un compte `BROKER` / `portfolio_type=CRYPTO`. Le
  "solde" d'un tel compte n'est pas la somme des transactions mais une
  valorisation (transaction `amount=0` + `account_balance`), cf.
  `sync_bitstack_valuation`.
- `Transaction.amount` porte donc le **flux en euros**, c'est-à-dire le montant
  investi (positif) ou désinvesti (négatif) — c'est ce que le dashboard somme
  pour obtenir le "montant investi" d'un compte courtier.
- Les entrées de BTC sans contrepartie en euros (cadeaux, récompenses, réception
  on-chain) valent `amount=0` : aucun euro n'a été investi. Leur valeur
  apparaît donc intégralement en plus-value, ce qui est le comportement voulu.
- La quantité de BTC de chaque ligne est conservée dans `raw["btc_delta"]`.
  C'est la source de vérité pour la position : elle est sommée en pleine
  précision (8 décimales) au moment de valoriser.
"""

from __future__ import annotations

import csv
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import IO, Iterable, Optional

from django.db import transaction as db_transaction
from django.utils import timezone

from finance.models import Account, InvestmentHolding, Transaction

logger = logging.getLogger(__name__)

SOURCE = "bitstack_csv"
BTC_SYMBOL = "BTC"
BTC_PRECISION = Decimal("0.00000001")  # 1 satoshi
EUR_PRECISION = Decimal("0.01")


class BitstackImportError(Exception):
    """Erreur de format du CSV Bitstack."""


# Colonnes de l'export Bitstack -> clés canoniques.
# Les clés sont normalisées (sans accent, minuscules) car l'export mélange
# "Montant reçu" et "Prix du jeton du montant recu" (sans cédille).
_COLUMN_MAP = {
    "type": "type",
    "date": "date",
    "fuseau horaire": "tz",
    "montant recu": "received_amount",
    "monnaie ou jeton recu": "received_currency",
    "montant envoye": "sent_amount",
    "monnaie ou jeton envoye": "sent_currency",
    "frais": "fee_amount",
    "monnaie ou jeton des frais": "fee_currency",
    "description": "description",
    "prix du jeton du montant envoye": "sent_token_price",
    "prix du jeton du montant recu": "received_token_price",
    "prix du jeton des frais": "fee_token_price",
    "adresse": "address",
    "transaction hash": "tx_hash",
    "id externe": "external_id",
}

_REQUIRED_KEYS = {"type", "date", "external_id"}


def _norm(value: str) -> str:
    """Normalise un en-tête de colonne : sans accent, minuscule, sans espace superflu."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _decimal(value: Optional[str]) -> Optional[Decimal]:
    """Parse un montant. Renvoie None si la cellule est vide."""
    if value is None:
        return None
    cleaned = value.strip().replace(" ", "").replace(" ", "")
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise BitstackImportError(f"Montant illisible : {value!r}") from exc


def _parse_date(value: str) -> datetime:
    """Parse un horodatage ISO 8601 UTC ('2026-08-23T15:44:06Z')."""
    raw = (value or "").strip()
    if not raw:
        raise BitstackImportError("Colonne 'Date' vide")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BitstackImportError(f"Date illisible : {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


@dataclass
class ImportResult:
    """Bilan d'un import, affiché par la commande et utilisé par les tests."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    btc_delta: Decimal = Decimal("0")
    eur_invested: Decimal = Decimal("0")
    warnings: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return self.created + self.updated


def parse_rows(fileobj: IO[str]) -> Iterable[dict]:
    """
    Lit le CSV et renvoie des dicts aux clés canoniques.

    Lève BitstackImportError si l'en-tête ne ressemble pas à un export Bitstack.
    """
    reader = csv.DictReader(fileobj)
    if not reader.fieldnames:
        raise BitstackImportError("CSV vide : aucun en-tête trouvé.")

    header = {}
    for column in reader.fieldnames:
        key = _COLUMN_MAP.get(_norm(column))
        if key:
            header[column] = key

    missing = _REQUIRED_KEYS - set(header.values())
    if missing:
        raise BitstackImportError(
            "En-tête inattendu, ce fichier ne ressemble pas à un export Bitstack. "
            f"Colonnes manquantes : {', '.join(sorted(missing))}."
        )

    for line_number, raw_row in enumerate(reader, start=2):
        row = {key: (raw_row.get(column) or "").strip() for column, key in header.items()}
        if not any(row.values()):
            continue  # ligne vide de fin de fichier
        row["_line"] = line_number
        yield row


def _build_transaction_data(row: dict) -> Optional[dict]:
    """
    Traduit une ligne du relevé en flux euro + delta BTC.

    Renvoie None si la ligne ne porte aucun mouvement exploitable.
    """
    received = _decimal(row.get("received_amount")) or Decimal("0")
    received_ccy = (row.get("received_currency") or "").upper()
    sent = _decimal(row.get("sent_amount")) or Decimal("0")
    sent_ccy = (row.get("sent_currency") or "").upper()
    fee = _decimal(row.get("fee_amount")) or Decimal("0")
    fee_ccy = (row.get("fee_currency") or "").upper()

    fee_eur = fee if fee_ccy == "EUR" else Decimal("0")
    fee_btc = fee if fee_ccy == BTC_SYMBOL else Decimal("0")

    btc_delta = Decimal("0")
    if received_ccy == BTC_SYMBOL:
        btc_delta += received
    if sent_ccy == BTC_SYMBOL:
        btc_delta -= sent
    btc_delta -= fee_btc

    warning = None

    if received_ccy == BTC_SYMBOL and sent_ccy == "EUR":
        # Achat : les euros sortent de la poche (montant envoyé + frais).
        eur_flow = sent + fee_eur
        kind = "buy"
    elif sent_ccy == BTC_SYMBOL and received_ccy == "EUR":
        # Vente : les euros reviennent. Bitstack facture les frais à part, on
        # retient donc le net encaissé. Aucune ligne de vente n'était présente
        # dans l'export de référence : à vérifier lors de la première vente.
        eur_flow = -(received - fee_eur)
        kind = "sell"
        warning = (
            "Ligne de vente détectée : le net encaissé est calculé comme "
            "« montant reçu − frais ». Vérifier sur le relevé Bitstack."
        )
    elif received_ccy == "EUR" and not sent_ccy:
        eur_flow = -received  # retrait d'euros du compte
        kind = "eur_out"
    elif sent_ccy == "EUR" and not received_ccy:
        eur_flow = sent  # dépôt d'euros sur le compte
        kind = "eur_in"
    elif btc_delta > 0:
        # Cadeau, récompense, réception on-chain : aucun euro engagé.
        eur_flow = Decimal("0")
        kind = "btc_in"
    elif btc_delta < 0:
        # Retrait de BTC vers un wallet externe : sort de la valorisation.
        eur_flow = Decimal("0")
        kind = "btc_out"
    else:
        return None

    return {
        "eur_flow": eur_flow.quantize(EUR_PRECISION),
        "btc_delta": btc_delta,
        "kind": kind,
        "fee_eur": fee_eur,
        "gross_eur": sent if sent_ccy == "EUR" else (received if received_ccy == "EUR" else Decimal("0")),
        "btc_price_eur": _decimal(row.get("received_token_price")) or _decimal(row.get("sent_token_price")),
        "warning": warning,
    }


def import_csv(account: Account, fileobj: IO[str], *, dry_run: bool = False) -> ImportResult:
    """
    Importe un relevé CSV Bitstack dans un compte existant.

    La déduplication s'appuie sur `raw["transaction_id"]` alimenté par la colonne
    « ID Externe » du relevé : réimporter le même fichier, ou un export annuel
    recouvrant un export mensuel déjà importé, met à jour les lignes au lieu
    de les dupliquer.
    """
    result = ImportResult()
    seen_warnings: set[str] = set()

    with db_transaction.atomic():
        for row in parse_rows(fileobj):
            external_id = row.get("external_id") or ""
            if not external_id:
                result.skipped += 1
                result.warnings.append(f"Ligne {row['_line']} ignorée : pas d'ID externe.")
                continue

            data = _build_transaction_data(row)
            if data is None:
                result.skipped += 1
                result.warnings.append(
                    f"Ligne {row['_line']} ignorée : aucun mouvement exploitable "
                    f"(type={row.get('type')!r})."
                )
                continue

            if data["warning"] and data["warning"] not in seen_warnings:
                seen_warnings.add(data["warning"])
                result.warnings.append(data["warning"])

            posted_at = _parse_date(row["date"])
            operation = row.get("type") or "Opération"
            label = row.get("description") or ""
            description = f"Bitstack — {operation}" + (f" ({label})" if label else "")

            raw = {
                "source": SOURCE,
                "transaction_id": external_id,
                "operation_type": operation,
                "operation_label": label,
                "kind": data["kind"],
                "btc_delta": str(data["btc_delta"]),
                "fee_eur": str(data["fee_eur"]),
                "gross_eur": str(data["gross_eur"]),
            }
            if data["btc_price_eur"] is not None:
                raw["btc_price_eur"] = str(data["btc_price_eur"])
            if row.get("tx_hash"):
                raw["tx_hash"] = row["tx_hash"]
            if row.get("address"):
                raw["address"] = row["address"]

            result.btc_delta += data["btc_delta"]
            if data["eur_flow"] > 0:
                result.eur_invested += data["eur_flow"]

            if dry_run:
                # Le compte peut ne pas exister encore (dry-run sur un nouveau
                # compte) : Django refuse de filtrer sur une instance non sauvée.
                exists = account.pk is not None and Transaction.objects.filter(
                    account=account, raw__transaction_id=external_id
                ).exists()
                result.updated += 1 if exists else 0
                result.created += 0 if exists else 1
                continue

            # `update_or_create` ne sait pas créer depuis un lookup JSON
            # (`raw__transaction_id`), on fait donc le get/update à la main.
            existing = Transaction.objects.filter(
                account=account, raw__transaction_id=external_id
            ).first()
            if existing is None:
                Transaction.objects.create(
                    account=account,
                    posted_at=posted_at,
                    amount=data["eur_flow"],
                    currency="EUR",
                    description=description[:512],
                    raw=raw,
                )
                result.created += 1
            else:
                existing.posted_at = posted_at
                existing.amount = data["eur_flow"]
                existing.currency = "EUR"
                existing.description = description[:512]
                existing.raw = raw
                existing.save(
                    update_fields=["posted_at", "amount", "currency", "description", "raw"]
                )
                result.updated += 1

        if dry_run:
            db_transaction.set_rollback(True)

    logger.info(
        "bitstack_import account=%s created=%s updated=%s skipped=%s btc_delta=%s dry_run=%s",
        account.id,
        result.created,
        result.updated,
        result.skipped,
        result.btc_delta,
        dry_run,
    )
    return result


def compute_btc_position(account: Account) -> Decimal:
    """
    Somme en pleine précision les deltas BTC des transactions importées.

    `InvestmentHolding.quantity` ne stocke que 6 décimales, insuffisant pour les
    micro-récompenses Bitstack (jusqu'à 0.0000025 BTC). Cette fonction est donc
    la référence pour valoriser la position ; le holding n'est qu'un miroir
    d'affichage.
    """
    total = Decimal("0")
    values = Transaction.objects.filter(
        account=account, raw__source=SOURCE
    ).values_list("raw", flat=True)
    for raw in values:
        delta = (raw or {}).get("btc_delta")
        if delta in (None, ""):
            continue
        try:
            total += Decimal(str(delta))
        except InvalidOperation:
            logger.warning("btc_delta illisible sur le compte %s : %r", account.id, delta)
    return total.quantize(BTC_PRECISION)


def sync_holding(account: Account) -> InvestmentHolding:
    """Met à jour le `InvestmentHolding` BTC miroir (quantité + prix de revient)."""
    quantity = compute_btc_position(account)
    invested = Transaction.objects.filter(
        account=account, raw__source=SOURCE, amount__gt=0
    ).values_list("amount", flat=True)
    total_invested = sum(invested, Decimal("0"))

    avg_cost = (total_invested / quantity) if quantity > 0 else Decimal("0")

    holding, _ = InvestmentHolding.objects.update_or_create(
        account=account,
        symbol=BTC_SYMBOL,
        defaults={
            "name": "Bitcoin",
            "instrument_type": "crypto",
            "quantity": quantity.quantize(Decimal("0.000001")),
            "avg_cost": avg_cost.quantize(Decimal("0.000001")),
            "tax_wrapper": "CRYPTO",
            "currency": "EUR",
        },
    )
    return holding


VALUATION_SOURCE = "bitstack_valuation"
VALUATION_PORTFOLIO_TYPE = "CRYPTO"


def sync_valuation(account: Account, *, price_service=None) -> Optional[Transaction]:
    """
    Valorise la position BTC du compte au cours spot et écrit le snapshot.

    Le dashboard lit la valorisation d'un compte courtier via une transaction
    `amount=0` portant `account_balance` et `raw["portfolio_type"]` — même
    convention que la valorisation manuelle et que Trade Republic.

    Un seul snapshot par jour : les appels suivants mettent à jour celui du jour
    plutôt que d'en empiler. Renvoie None si le cours est indisponible (on
    préfère une valorisation absente à une valorisation fausse) ou si la
    position est vide.
    """
    quantity = compute_btc_position(account)
    if quantity <= 0:
        logger.info("bitstack_valuation account=%s ignorée : position BTC nulle", account.id)
        return None

    service = price_service or _default_price_service()
    quote = service.get_crypto_price_eur(BTC_SYMBOL)
    if not quote or quote.get("price") is None:
        logger.error("bitstack_valuation account=%s : cours BTC/EUR indisponible", account.id)
        return None

    price = Decimal(str(quote["price"]))
    valuation = (quantity * price).quantize(EUR_PRECISION)
    now = timezone.now()

    raw = {
        "source": VALUATION_SOURCE,
        "type": "snapshot",
        "portfolio_type": VALUATION_PORTFOLIO_TYPE,
        "btc_quantity": str(quantity),
        "btc_price_eur": str(price),
        "price_source": quote.get("source"),
        "priced_at": quote.get("priced_at"),
    }
    description = f"Valorisation Bitstack — {quantity} BTC à {price} €"

    existing = Transaction.objects.filter(
        account=account,
        raw__source=VALUATION_SOURCE,
        posted_at__date=now.date(),
    ).first()

    if existing is None:
        snapshot = Transaction.objects.create(
            account=account,
            posted_at=now,
            amount=Decimal("0"),
            currency="EUR",
            description=description[:512],
            account_balance=valuation,
            raw=raw,
        )
    else:
        existing.posted_at = now
        existing.description = description[:512]
        existing.account_balance = valuation
        existing.raw = raw
        existing.save(
            update_fields=["posted_at", "description", "account_balance", "raw"]
        )
        snapshot = existing

    account.initial_balance = valuation
    account.balance_snapshot_date = now.date()
    account.save(update_fields=["initial_balance", "balance_snapshot_date"])

    sync_holding(account)

    logger.info(
        "bitstack_valuation account=%s quantity=%s price=%s valuation=%s source=%s",
        account.id,
        quantity,
        price,
        valuation,
        quote.get("source"),
    )
    return snapshot


def _default_price_service():
    from finance.services.market_price_service import MarketPriceService

    return MarketPriceService()
