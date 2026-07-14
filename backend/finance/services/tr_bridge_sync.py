from __future__ import annotations

import logging
import os
import time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from finance.models import (
    Account,
    BankConnection,
    TradeRepublicAccountValuationSnapshot,
    TradeRepublicPortfolioSnapshot,
    TradeRepublicSubAccountMapping,
    TradeRepublicValuationSnapshot,
)
from finance.services.encryption_service import EncryptionError, EncryptionService
from finance.services.sync_service import SyncService
from finance.services.tr_bridge_client import (
    TradeRepublicBridgeAuthRequired,
    TradeRepublicBridgeError,
    fetch_tr_auth_status,
    fetch_tr_valuation,
)

logger = logging.getLogger(__name__)


def _extract_nested(row: dict, field: str, subkey: str = "valuation"):
    """Extrait une valeur d'un champ qui peut être un nombre ou un objet {"invested": x, "valuation": y}."""
    v = row.get(field)
    if isinstance(v, dict):
        return v.get(subkey)
    return v


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "n/a", "undefined"):
        return Decimal("0")
    try:
        return Decimal(s)
    except Exception:
        import traceback as _tb
        logger.warning(
            "tr_bridge_invalid_decimal value=%r caller=%s",
            value,
            "".join(_tb.format_stack()[-3:-1]).strip().replace("\n", " | "),
        )
        return Decimal("0")


def _resolve_trade_republic_credentials(account: Account) -> tuple[str, str]:
    # 1) Source principale: connexion associée au compte
    candidate_connections = []
    direct_connection = getattr(account, "bank_connection", None)
    if direct_connection:
        candidate_connections.append(direct_connection)

    # 2) Fallback: n'importe quelle connexion Trade Republic du même owner
    owner_connection = (
        BankConnection.objects.filter(
            owner=account.owner,
            provider=BankConnection.Provider.TRADE_REPUBLIC,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if owner_connection and owner_connection not in candidate_connections:
        candidate_connections.append(owner_connection)

    for connection in candidate_connections:
        if not connection.encrypted_credentials:
            continue
        try:
            credentials = EncryptionService.decrypt_credentials(connection.encrypted_credentials)
        except EncryptionError:
            continue

        phone = (credentials.get("phone_number") or credentials.get("username") or "").strip()
        pin = (credentials.get("pin") or credentials.get("password") or "").strip()
        if phone and pin:
            return phone, pin

    raise TradeRepublicBridgeError(
        f"missing_trade_republic_credentials: aucun identifiant Trade Republic trouvé "
        f"pour le compte '{account.name}' (owner: {account.owner}). "
        f"Associe une BankConnection TR avec phone/pin à ce compte."
    )


def _parse_source_timestamp(payload: dict) -> timezone.datetime:
    ts_raw = payload.get("timestamp")
    parsed = parse_datetime(ts_raw) if ts_raw else None
    if parsed is None:
        parsed = timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise TradeRepublicBridgeError("invalid_payload_root")
    if "global" not in payload or not isinstance(payload.get("global"), dict):
        raise TradeRepublicBridgeError("invalid_payload_global")
    if "accounts" not in payload or not isinstance(payload.get("accounts"), list):
        raise TradeRepublicBridgeError("invalid_payload_accounts")


def _resolve_subaccount_portfolio_type(owner, external_account_id: str) -> str:
    """
    Retourne le type de portefeuille (PEA/CTO/...) choisi par l'utilisateur pour ce
    sous-compte Trade Republic. Découvre automatiquement le sous-compte (mapping vide
    = "à classer") s'il n'existe pas encore, et retourne CTO par défaut tant qu'il
    n'a pas été classé.
    """
    mapping, _ = TradeRepublicSubAccountMapping.objects.get_or_create(
        owner=owner,
        external_account_id=external_account_id,
    )
    return mapping.portfolio_type or TradeRepublicPortfolioSnapshot.PortfolioType.CTO


def _create_portfolio_snapshot(
    *,
    snapshot: TradeRepublicValuationSnapshot,
    account_snapshot: TradeRepublicAccountValuationSnapshot | None,
    portfolio_type: str,
    invested_total,
    current_value,
    currency: str,
    raw: dict | None = None,
) -> None:
    TradeRepublicPortfolioSnapshot.objects.create(
        snapshot=snapshot,
        account_snapshot=account_snapshot,
        portfolio_type=portfolio_type,
        invested_total=_to_decimal(invested_total),
        current_value=_to_decimal(current_value),
        currency=currency or "EUR",
        raw=raw or {},
    )


def _sync_bridge_transactions(*, account: Account, payload: dict) -> int:
    raw_transactions = payload.get("transactions")
    if not isinstance(raw_transactions, list):
        return 0

    synced = 0
    for item in raw_transactions:
        if not isinstance(item, dict):
            continue
        if item.get("deleted") is True:
            continue

        ts_raw = item.get("timestamp")
        posted_at = parse_datetime(ts_raw) if isinstance(ts_raw, str) else None
        if posted_at is None:
            posted_at = timezone.now()
        elif timezone.is_naive(posted_at):
            posted_at = timezone.make_aware(posted_at, timezone.get_current_timezone())

        amount_raw = item.get("amount") if isinstance(item.get("amount"), dict) else {}
        amount_value = _to_decimal((amount_raw or {}).get("value"))
        currency = (amount_raw or {}).get("currency") or account.currency or "EUR"

        title = str(item.get("title") or "").strip()
        subtitle = str(item.get("subtitle") or "").strip()
        description = " - ".join(part for part in [title, subtitle] if part)[:512]
        if not description:
            description = str(item.get("eventType") or "Trade Republic")

        tx_raw = {
            **item,
            "transaction_id": item.get("id"),
            "source": "trade_republic_bridge",
        }

        SyncService._upsert_transaction_from_sync(
            account=account,
            transaction_data={
                "posted_at": posted_at,
                "amount": amount_value,
                "description": description,
                "raw": tx_raw,
                "currency": currency,
            },
            source="trade_republic_bridge",
        )
        synced += 1

    return synced


@transaction.atomic
def sync_bridge_snapshot_for_account(
    *,
    account: Account,
    source: str = TradeRepublicValuationSnapshot.Source.BRIDGE_AUTO,
    device_pin: str | None = None,
) -> TradeRepublicValuationSnapshot:
    t0 = time.monotonic()
    phone_number, pin = _resolve_trade_republic_credentials(account)
    payload = fetch_tr_valuation(phone_number=phone_number, pin=pin, device_pin=device_pin)
    import json as _json
    # On logue uniquement le diagnostic pagination (pas le payload complet).
    debug_tx = payload.get("debug_tx_pagination") if isinstance(payload, dict) else None
    # if isinstance(debug_tx, dict):
    #     logger.info(
    #         "tr_bridge_debug_tx_pagination account_id=%s debug=%s",
    #         account.pk,
    #         _json.dumps(debug_tx, ensure_ascii=False, default=str),
    #     )
    _validate_payload(payload)
    source_timestamp = _parse_source_timestamp(payload)
    global_data = payload.get("global") or {}
    account_rows = payload.get("accounts") or []

    snapshot = TradeRepublicValuationSnapshot.objects.create(
        owner=account.owner,
        account=account,
        source=source,
        auth_status=TradeRepublicValuationSnapshot.AuthStatus.AUTHENTICATED,
        source_timestamp=source_timestamp,
        currency=account.currency or "EUR",
        invested_total=_to_decimal(global_data.get("invested_total")),
        invested_societes=_to_decimal(global_data.get("invested_societes")),
        invested_crypto=_to_decimal(global_data.get("invested_crypto")),
        invested_by_asset_type=global_data.get("invested_by_asset_type") or {},
        societes=_to_decimal(global_data.get("societes")),
        crypto=_to_decimal(global_data.get("crypto")),
        positions_total=_to_decimal(global_data.get("positions_total")),
        cash=_to_decimal(global_data.get("cash")),
        total_with_cash=_to_decimal(global_data.get("total_with_cash")),
        raw=payload,
    )

    for row in account_rows:
        row_snapshot = TradeRepublicAccountValuationSnapshot.objects.create(
            snapshot=snapshot,
            external_account_id=str(row.get("account") or "default"),
            currency=account.currency or "EUR",
            invested_total=_to_decimal(row.get("invested_total")),
            invested_societes=_to_decimal(row.get("invested_societes") or _extract_nested(row, "societes", "invested")),
            invested_crypto=_to_decimal(row.get("invested_crypto") or _extract_nested(row, "crypto", "invested")),
            invested_by_asset_type=row.get("invested_by_asset_type") or {},
            societes=_to_decimal(_extract_nested(row, "societes")),
            crypto=_to_decimal(_extract_nested(row, "crypto")),
            positions_total=_to_decimal(row.get("positions_total")),
            cash=_to_decimal(row.get("cash")),
            total_with_cash=_to_decimal(row.get("total_with_cash")),
            raw=row,
        )

        # Résoudre le type de portefeuille (PEA/CTO) depuis le mapping par sous-compte
        tr_account_id = str(row.get("account") or "")
        societes_type = _resolve_subaccount_portfolio_type(account.owner, tr_account_id)

        portfolios = row.get("portfolios") if isinstance(row.get("portfolios"), dict) else {}
        if portfolios:
            for p_type, p_data in portfolios.items():
                _create_portfolio_snapshot(
                    snapshot=snapshot,
                    account_snapshot=row_snapshot,
                    portfolio_type=str(p_type).upper(),
                    invested_total=(p_data or {}).get("invested_total"),
                    current_value=(p_data or {}).get("current_value"),
                    currency=account.currency or "EUR",
                    raw=p_data or {},
                )
        else:
            # Societes: type déterminé par le mapping (CTO ou PEA selon le compte TR)
            _create_portfolio_snapshot(
                snapshot=snapshot,
                account_snapshot=row_snapshot,
                portfolio_type=societes_type,
                invested_total=row.get("invested_societes") or _extract_nested(row, "societes", "invested"),
                current_value=_extract_nested(row, "societes"),
                currency=account.currency or "EUR",
                raw={"inferred": True, "mapped_type": societes_type},
            )
            # Crypto: uniquement si ce compte en a
            if _extract_nested(row, "crypto"):
                _create_portfolio_snapshot(
                    snapshot=snapshot,
                    account_snapshot=row_snapshot,
                    portfolio_type=TradeRepublicPortfolioSnapshot.PortfolioType.CRYPTO,
                    invested_total=row.get("invested_crypto") or _extract_nested(row, "crypto", "invested"),
                    current_value=_extract_nested(row, "crypto"),
                    currency=account.currency or "EUR",
                    raw={"inferred": True},
                )

    # Snapshots globaux: agrégés depuis les snapshots de compte (respecte les types PEA/CTO/CRYPTO)
    global_portfolios = (
        payload.get("global_portfolios")
        if isinstance(payload.get("global_portfolios"), dict)
        else {}
    )
    if global_portfolios:
        for p_type, p_data in global_portfolios.items():
            _create_portfolio_snapshot(
                snapshot=snapshot,
                account_snapshot=None,
                portfolio_type=str(p_type).upper(),
                invested_total=(p_data or {}).get("invested_total"),
                current_value=(p_data or {}).get("current_value"),
                currency=account.currency or "EUR",
                raw={"scope": "global", **(p_data or {})},
            )
    else:
        # Reconstruire les totaux globaux par type depuis les comptes avec mapping
        global_by_type: dict[str, dict] = {}
        for row in account_rows:
            tr_id = str(row.get("account") or "")
            soc_type = _resolve_subaccount_portfolio_type(account.owner, tr_id)
            soc_val = _extract_nested(row, "societes") or 0
            soc_inv = _extract_nested(row, "societes", "invested") or row.get("invested_societes") or 0
            cry_val = _extract_nested(row, "crypto") or 0
            cry_inv = _extract_nested(row, "crypto", "invested") or row.get("invested_crypto") or 0
            if soc_type not in global_by_type:
                global_by_type[soc_type] = {"valuation": 0, "invested": 0}
            global_by_type[soc_type]["valuation"] += float(soc_val)
            global_by_type[soc_type]["invested"] += float(soc_inv)
            if cry_val:
                pt_crypto = TradeRepublicPortfolioSnapshot.PortfolioType.CRYPTO
                if pt_crypto not in global_by_type:
                    global_by_type[pt_crypto] = {"valuation": 0, "invested": 0}
                global_by_type[pt_crypto]["valuation"] += float(cry_val)
                global_by_type[pt_crypto]["invested"] += float(cry_inv)

        if global_by_type:
            for p_type, totals in global_by_type.items():
                _create_portfolio_snapshot(
                    snapshot=snapshot,
                    account_snapshot=None,
                    portfolio_type=p_type,
                    invested_total=totals["invested"],
                    current_value=totals["valuation"],
                    currency=account.currency or "EUR",
                    raw={"scope": "global", "inferred": True, "from_account_map": True},
                )
        else:
            # Fallback ultime: pas de mapping, tout en CTO + CRYPTO
            _create_portfolio_snapshot(
                snapshot=snapshot,
                account_snapshot=None,
                portfolio_type=TradeRepublicPortfolioSnapshot.PortfolioType.CTO,
                invested_total=global_data.get("invested_societes"),
                current_value=global_data.get("societes"),
                currency=account.currency or "EUR",
                raw={"scope": "global", "inferred": True},
            )
            _create_portfolio_snapshot(
                snapshot=snapshot,
                account_snapshot=None,
                portfolio_type=TradeRepublicPortfolioSnapshot.PortfolioType.CRYPTO,
                invested_total=global_data.get("invested_crypto"),
                current_value=global_data.get("crypto"),
                currency=account.currency or "EUR",
                raw={"scope": "global", "inferred": True},
            )

    synced_transactions = _sync_bridge_transactions(account=account, payload=payload)

    snapshot.bridge_latency_ms = int((time.monotonic() - t0) * 1000)
    snapshot.save(update_fields=["bridge_latency_ms"])
    logger.info(
        "tr_bridge_snapshot_created account_id=%s snapshot_id=%s source_ts=%s latency_ms=%s total=%s tx_synced=%s",
        account.id,
        snapshot.id,
        snapshot.source_timestamp.isoformat(),
        snapshot.bridge_latency_ms,
        snapshot.total_with_cash,
        synced_transactions,
    )
    return snapshot


def get_bridge_auth_status_safe(*, account: Account | None = None) -> dict:
    try:
        if account is not None:
            try:
                phone, pin = _resolve_trade_republic_credentials(account)
                return fetch_tr_auth_status(phone_number=phone, pin=pin)
            except TradeRepublicBridgeError:
                pass
        return fetch_tr_auth_status()
    except TradeRepublicBridgeError as exc:
        logger.warning("bridge_auth_status_unavailable error=%s", exc)
        return {"status": "failed", "reason": str(exc)}


def sync_bridge_snapshot_with_auth_handling(
    *,
    account: Account,
    source: str = TradeRepublicValuationSnapshot.Source.BRIDGE_AUTO,
    device_pin: str | None = None,
) -> TradeRepublicValuationSnapshot:
    try:
        return sync_bridge_snapshot_for_account(
            account=account,
            source=source,
            device_pin=device_pin,
        )
    except TradeRepublicBridgeAuthRequired as exc:
        source_timestamp = timezone.now()
        status_payload = get_bridge_auth_status_safe(account=account)
        snapshot = TradeRepublicValuationSnapshot.objects.create(
            owner=account.owner,
            account=account,
            source=source,
            auth_status=TradeRepublicValuationSnapshot.AuthStatus.NEEDS_MANUAL_AUTH,
            source_timestamp=source_timestamp,
            currency=account.currency or "EUR",
            raw={"error": str(exc), "auth_status": status_payload},
        )
        logger.warning(
            "tr_bridge_auth_required account_id=%s snapshot_id=%s reason=%s",
            account.id,
            snapshot.id,
            status_payload,
        )
        return snapshot
