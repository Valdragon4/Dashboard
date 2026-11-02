"""
Connecteur DSP2 via Enable Banking (Hello Bank, Revolut perso/pro, …).

Une BankConnection = un consentement auprès d'une banque (session Enable Banking,
valable 90 à 180 jours). Chaque compte de la banque est rattaché à un Account
Django par un BankAccountLink :
  - external_account_id = empreinte stable du compte (identification_hash),
    qui survit au renouvellement du consentement ;
  - raw["uid"] = identifiant du compte dans la session courante (change à chaque
    renouvellement, réécrit par le callback).

Credentials chiffrés de la connexion :
  {"session_id", "aspsp", "country", "psu_type", "valid_until"}

Choix de données :
  - seules les opérations comptabilisées (BOOK) sont importées ; les opérations
    en attente changent souvent de libellé ou de montant en passant ;
  - le solde est le solde comptable, cohérent avec ces seules opérations ;
  - certaines banques (Hello Bank) ne donnent aucun identifiant d'opération :
    on en fabrique un à partir de (date, montant, libellé, rang du doublon).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from finance.connectors.base import (
    AuthenticationError,
    BankConnectionError,
    BaseBankConnector,
    ConnectionTimeoutError,
    InvalidCredentialsError,
    RateLimitError,
)
from finance.models import BankAccountLink
from finance.services.enablebanking_client import (
    EnableBankingClient,
    EnableBankingError,
    EnableBankingRateLimited,
    EnableBankingSessionExpired,
)

logger = logging.getLogger(__name__)

# Ordre de préférence des soldes : comptable d'abord (cohérent avec les seules
# opérations BOOK importées), puis disponible/attendu en dernier recours.
BOOKED_BALANCE_TYPES = ("ITBD", "CLBD", "OPBD", "PRCD")
FALLBACK_BALANCE_TYPES = ("XPCD", "ITAV", "CLAV", "OTHR")

EXPIRED_MESSAGE = (
    "Consentement bancaire expiré ou révoqué : cliquer sur « Renouveler l'accès » "
    "dans Connexions bancaires."
)


def account_fingerprint(account_data: Dict) -> str:
    """Identifiant stable d'un compte bancaire, indépendant de la session."""
    basis = account_data.get("identification_hash")
    if not basis:
        ident = account_data.get("account_id") or {}
        basis = ident.get("iban") or (ident.get("other") or {}).get("identification") or account_data.get("uid", "")
    return hashlib.sha256(str(basis).encode()).hexdigest()


def _to_decimal(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_label(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _signed_amount(tx: Dict) -> Optional[Decimal]:
    amount = _to_decimal((tx.get("transaction_amount") or {}).get("amount"))
    if amount is None:
        return None
    amount = abs(amount)
    return -amount if tx.get("credit_debit_indicator") == "DBIT" else amount


def _description(tx: Dict) -> str:
    parts = [p.strip() for p in (tx.get("remittance_information") or []) if p and p.strip()]
    if parts:
        return " ".join(parts)
    party = tx.get("creditor") if tx.get("credit_debit_indicator") == "DBIT" else tx.get("debtor")
    return ((party or {}).get("name") or tx.get("note") or "Opération").strip()


def _booking_day(tx: Dict) -> Optional[date]:
    return _parse_date(tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date"))


def normalize_transactions(raw_transactions: List[Dict], aspsp: str) -> List[Dict]:
    """
    Convertit les opérations Enable Banking au format attendu par SyncService.

    Ignore les opérations non comptabilisées. Fabrique un identifiant stable
    quand la banque n'en fournit pas : deux opérations identiques le même jour
    sont distinguées par leur rang (#1, #2…), ce qui reste stable d'une synchro
    à l'autre puisque la fenêtre couvre toujours des journées entières.
    """
    out: List[Dict] = []
    seen: Dict[str, int] = {}
    tz = timezone.get_current_timezone()

    for tx in raw_transactions:
        status = tx.get("status")
        if status and status != "BOOK":
            continue
        day = _booking_day(tx)
        amount = _signed_amount(tx)
        if day is None or amount is None:
            continue
        description = _description(tx)

        upstream_id = tx.get("entry_reference") or tx.get("transaction_id")
        if upstream_id:
            transaction_id = f"eb:{aspsp}:{upstream_id}"
        else:
            key = f"{day.isoformat()}|{amount}|{_normalize_label(description)}"
            seen[key] = seen.get(key, 0) + 1
            digest = hashlib.sha256(f"{aspsp}|{key}".encode()).hexdigest()[:24]
            transaction_id = f"eb:{digest}#{seen[key]}"

        counterparty = tx.get("creditor") if amount < 0 else tx.get("debtor")
        out.append(
            {
                "posted_at": timezone.make_aware(datetime.combine(day, dtime(12, 0)), tz),
                "amount": amount,
                "description": description,
                "booking_day": day,
                "raw": {
                    "source": "enablebanking",
                    "aspsp": aspsp,
                    "transaction_id": transaction_id,
                    "bank_transaction_code": (tx.get("bank_transaction_code") or {}).get("description")
                    or (tx.get("bank_transaction_code") or {}).get("code"),
                    "counterparty": (counterparty or {}).get("name"),
                    "value_date": tx.get("value_date"),
                },
            }
        )
    return out


def pick_booked_balance(balances: List[Dict], booked_tx: List[Dict]) -> Optional[Decimal]:
    """
    Solde comptable à jour.

    Un solde de clôture (CLBD) date de la veille ou d'avant : on lui ajoute les
    opérations comptabilisées après sa date de référence pour retomber sur le
    solde réel, cohérent avec les opérations importées.
    """
    by_type = {b.get("balance_type"): b for b in balances}
    for btype in BOOKED_BALANCE_TYPES:
        bal = by_type.get(btype)
        if not bal:
            continue
        amount = _to_decimal((bal.get("balance_amount") or {}).get("amount"))
        if amount is None:
            continue
        ref = _parse_date(bal.get("reference_date"))
        if btype in ("CLBD", "OPBD", "PRCD") and ref is not None:
            # OPBD = solde d'ouverture du jour de référence : ses opérations comptent.
            include_ref_day = btype == "OPBD"
            for tx in booked_tx:
                day = tx.get("booking_day")
                if day and (day > ref or (include_ref_day and day == ref)):
                    amount += tx["amount"]
        return amount
    for btype in FALLBACK_BALANCE_TYPES:
        bal = by_type.get(btype)
        if bal:
            amount = _to_decimal((bal.get("balance_amount") or {}).get("amount"))
            if amount is not None:
                return amount
    return None


class EnableBankingConnector(BaseBankConnector):
    def __init__(self, client: Optional[EnableBankingClient] = None):
        self._client = client
        self._credentials: Dict = {}
        self._booked_cache: Dict[int, List[Dict]] = {}

    @property
    def provider_name(self) -> str:
        return f"Enable Banking ({self._credentials.get('aspsp', '?')})"

    @property
    def client(self) -> EnableBankingClient:
        if self._client is None:
            self._client = EnableBankingClient()
        return self._client

    # ------------------------------------------------------------------

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EnableBankingSessionExpired as e:
            raise AuthenticationError(f"{EXPIRED_MESSAGE} ({e})") from e
        except EnableBankingRateLimited as e:
            raise RateLimitError(f"Quota DSP2 de la banque atteint : {e}") from e
        except EnableBankingError as e:
            if "timeout" in str(e).lower():
                raise ConnectionTimeoutError(str(e)) from e
            raise BankConnectionError(str(e)) from e

    def authenticate(self, credentials: Dict) -> Dict:
        session_id = credentials.get("session_id")
        if not session_id:
            raise InvalidCredentialsError(
                "Aucun consentement Enable Banking : cliquer sur « Renouveler l'accès »."
            )
        self._credentials = credentials

        valid_until = credentials.get("valid_until")
        if valid_until:
            try:
                expiry = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
                if expiry <= timezone.now():
                    raise AuthenticationError(EXPIRED_MESSAGE)
            except ValueError:
                pass

        session = self._call(self.client.get_session, session_id)
        status = (session.get("status") or "").upper()
        if status and status != "AUTHORIZED":
            raise AuthenticationError(f"{EXPIRED_MESSAGE} (statut {status})")
        return {"session_id": session_id, "expires_at": valid_until}

    def _link_for(self, account) -> BankAccountLink:
        link = (
            BankAccountLink.objects.filter(
                connection=account.bank_connection, account=account, disabled=False
            )
            .order_by("-updated_at")
            .first()
        )
        if not link or not (link.raw or {}).get("uid"):
            raise BankConnectionError(
                f"Compte « {account.name} » non rattaché à un compte bancaire Enable Banking."
            )
        return link

    def sync_transactions(self, account, since: Optional[datetime] = None) -> List[Dict]:
        link = self._link_for(account)
        max_days = int(getattr(settings, "ENABLEBANKING_MAX_HISTORY_DAYS", 89))
        margin = int(getattr(settings, "ENABLEBANKING_SINCE_MARGIN_DAYS", 5))
        floor = timezone.localdate() - timedelta(days=max_days)
        date_from = floor
        if since is not None:
            date_from = max(floor, timezone.localtime(since).date() - timedelta(days=margin))

        raw = self._call(self.client.get_transactions, link.raw["uid"], date_from.isoformat())
        txs = normalize_transactions(raw, self._credentials.get("aspsp", ""))
        self._booked_cache[account.id] = txs
        logger.info(
            "enablebanking: %d opération(s) brute(s), %d comptabilisée(s) depuis %s (compte %s)",
            len(raw), len(txs), date_from, account.id,
        )
        return txs

    def get_balance(self, account) -> Decimal:
        link = self._link_for(account)
        balances = self._call(self.client.get_balances, link.raw["uid"])
        booked = self._booked_cache.get(account.id)
        if booked is None:
            # Solde demandé sans passer par sync_transactions : fenêtre courte suffisante
            # pour rattraper le dernier solde de clôture.
            raw = self._call(
                self.client.get_transactions,
                link.raw["uid"],
                (timezone.localdate() - timedelta(days=7)).isoformat(),
            )
            booked = normalize_transactions(raw, self._credentials.get("aspsp", ""))
        amount = pick_booked_balance(balances, booked)
        if amount is None:
            raise BankConnectionError("Aucun solde exploitable renvoyé par la banque.")
        return amount

    def disconnect(self) -> None:
        self._booked_cache.clear()
