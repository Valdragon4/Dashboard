"""
Vues du parcours Enable Banking (DSP2) :

  1. enablebanking_connect   : choix de la banque, redirection vers sa page de validation ;
  2. enablebanking_callback  : retour de la banque, ouverture de la session ;
  3. enablebanking_accounts  : rattachement des comptes bancaires aux comptes du dashboard.

Le renouvellement d'un consentement (tous les 90 à 180 jours) repasse par les
mêmes étapes avec l'identifiant de la connexion existante : les rattachements
sont conservés, car un compte est reconnu par son empreinte stable.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .connectors.enablebanking import account_fingerprint
from .models import Account, BankAccountLink, BankConnection, SyncLog
from .services.enablebanking_client import EnableBankingClient, EnableBankingError
from .services.encryption_service import EncryptionService

logger = logging.getLogger(__name__)

SESSION_KEY = "enablebanking_auth"
COUNTRIES = [("FR", "France"), ("LT", "Lituanie (Revolut)"), ("DE", "Allemagne"), ("BE", "Belgique")]
PSU_TYPES = [("personal", "Particulier"), ("business", "Professionnel")]
CASH_ACCOUNT_TYPES = {"CACC": Account.AccountType.CHECKING, "SVGS": Account.AccountType.SAVINGS, "CARD": Account.AccountType.CREDIT}


def suggested_account_name(connection: BankConnection, link: BankAccountLink) -> str:
    """Nom distinctif : les banques renvoient souvent le titulaire pour tous les comptes."""
    parts = [connection.account_name]
    if link.currency:
        parts.append(link.currency)
    last4 = (link.raw or {}).get("iban_last4")
    if last4:
        parts.append(f"…{last4}")
    return " ".join(parts)[:120]


def connection_credentials(connection: BankConnection) -> dict:
    try:
        return EncryptionService.decrypt_credentials(connection.encrypted_credentials) or {}
    except Exception:
        return {}


def consent_status(connection: BankConnection) -> dict:
    """Date d'expiration et jours restants du consentement (pour l'affichage)."""
    creds = connection_credentials(connection)
    out = {"aspsp": creds.get("aspsp"), "valid_until": None, "days_left": None, "expiring": False, "expired": False}
    raw = creds.get("valid_until")
    if raw:
        try:
            until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            days = (until - timezone.now()).days
            out.update(
                valid_until=until,
                days_left=days,
                expired=until <= timezone.now(),
                expiring=days < int(getattr(settings, "ENABLEBANKING_EXPIRY_WARNING_DAYS", 14)),
            )
        except ValueError:
            pass
    return out


def _start(request: HttpRequest, aspsp: str, country: str, psu_type: str, connection_id: int | None) -> HttpResponse:
    state = secrets.token_urlsafe(24)
    pending = request.session.get(SESSION_KEY, {})
    pending[state] = {"aspsp": aspsp, "country": country, "psu_type": psu_type, "connection_id": connection_id}
    request.session[SESSION_KEY] = pending
    try:
        client = EnableBankingClient()
        max_days = 180
        for item in client.list_aspsps(country, psu_type):
            if item.get("name") == aspsp:
                max_days = max(1, int(item.get("maximum_consent_validity") or 15552000) // 86400 - 1)
                break
        auth = client.start_authorization(
            aspsp_name=aspsp,
            country=country,
            redirect_url=settings.ENABLEBANKING_REDIRECT_URL,
            state=state,
            psu_type=psu_type,
            valid_days=max_days,
        )
    except EnableBankingError as e:
        logger.warning("enablebanking start failed: %s", e)
        messages.error(request, f"Impossible de joindre Enable Banking : {e}")
        return redirect("bank_connections_list")
    return redirect(auth["url"])


@login_required
@require_http_methods(["GET", "POST"])
def enablebanking_connect(request: HttpRequest) -> HttpResponse:
    country = request.GET.get("country") or request.POST.get("country") or "FR"
    psu_type = request.GET.get("psu_type") or request.POST.get("psu_type") or "personal"
    if country not in dict(COUNTRIES):
        country = "FR"
    if psu_type not in dict(PSU_TYPES):
        psu_type = "personal"

    if request.method == "POST":
        aspsp = (request.POST.get("aspsp") or "").strip()
        if not aspsp:
            messages.error(request, "Choisissez une banque.")
        else:
            return _start(request, aspsp, country, psu_type, None)

    aspsps, error = [], None
    try:
        aspsps = sorted(
            (a for a in EnableBankingClient().list_aspsps(country, psu_type) if not a.get("beta")),
            key=lambda a: a.get("name", "").lower(),
        )
    except EnableBankingError as e:
        error = str(e)

    return render(
        request,
        "finance/enablebanking_connect.html",
        {
            "aspsps": aspsps,
            "error": error,
            "country": country,
            "psu_type": psu_type,
            "countries": COUNTRIES,
            "psu_types": PSU_TYPES,
        },
    )


@login_required
@require_http_methods(["POST"])
def enablebanking_renew(request: HttpRequest, connection_id: int) -> HttpResponse:
    connection = get_object_or_404(
        BankConnection, id=connection_id, owner=request.user, provider=BankConnection.Provider.ENABLEBANKING
    )
    creds = connection_credentials(connection)
    if not creds.get("aspsp"):
        messages.error(request, "Banque inconnue pour cette connexion : recréez-la.")
        return redirect("bank_connections_list")
    return _start(request, creds["aspsp"], creds.get("country", "FR"), creds.get("psu_type", "personal"), connection.id)


def store_session(owner, ctx: dict, session: dict) -> BankConnection:
    """
    Enregistre une session Enable Banking : crée ou met à jour la connexion
    (credentials chiffrés) et les rattachements de comptes, reconnus par leur
    empreinte stable. `ctx` = {"aspsp", "country", "psu_type", "connection_id"}.
    """
    credentials = {
        "session_id": session["session_id"],
        "aspsp": ctx["aspsp"],
        "country": ctx["country"],
        "psu_type": ctx["psu_type"],
        "valid_until": (session.get("access") or {}).get("valid_until"),
    }

    with transaction.atomic():
        connection = None
        if ctx.get("connection_id"):
            connection = BankConnection.objects.filter(
                id=ctx["connection_id"], owner=owner, provider=BankConnection.Provider.ENABLEBANKING
            ).first()
        if connection is None:
            suffix = " (pro)" if ctx["psu_type"] == "business" else ""
            connection = BankConnection(
                owner=owner,
                provider=BankConnection.Provider.ENABLEBANKING,
                account_name=f"{ctx['aspsp']}{suffix}",
                auto_sync_enabled=True,
            )
        connection.encrypted_credentials = EncryptionService.encrypt_credentials(credentials)
        connection.sync_status = BankConnection.SyncStatus.PENDING
        connection.save()

        seen = set()
        for acc in session.get("accounts") or []:
            fingerprint = account_fingerprint(acc)
            seen.add(fingerprint)
            iban = ((acc.get("account_id") or {}).get("iban") or "")
            link, _ = BankAccountLink.objects.get_or_create(
                connection=connection, external_account_id=fingerprint
            )
            link.name = (acc.get("name") or acc.get("product") or acc.get("details") or "Compte")[:120]
            link.currency = acc.get("currency") or "EUR"
            link.type = acc.get("cash_account_type") or ""
            link.raw = {
                "uid": acc.get("uid"),
                "iban_last4": iban[-4:],
                "product": acc.get("product"),
                "usage": acc.get("usage"),
                "source": "enablebanking",
            }
            link.disabled = False
            link.save()
        # Comptes absents du nouveau consentement : plus accessibles.
        BankAccountLink.objects.filter(connection=connection).exclude(external_account_id__in=seen).update(disabled=True)
    return connection


@login_required
def enablebanking_callback(request: HttpRequest) -> HttpResponse:
    state = request.GET.get("state", "")
    pending = request.session.get(SESSION_KEY, {})
    ctx = pending.pop(state, None)
    request.session[SESSION_KEY] = pending
    if ctx is None:
        messages.error(request, "Retour bancaire inattendu (lien expiré ou déjà utilisé). Recommencez.")
        return redirect("bank_connections_list")

    if request.GET.get("error"):
        detail = request.GET.get("error_description") or request.GET.get("error")
        messages.error(request, f"Validation refusée ou annulée côté banque : {detail}")
        return redirect("bank_connections_list")

    code = request.GET.get("code")
    if not code:
        messages.error(request, "La banque n'a pas renvoyé de code d'autorisation.")
        return redirect("bank_connections_list")

    try:
        session = EnableBankingClient().create_session(code)
    except EnableBankingError as e:
        logger.warning("enablebanking session failed: %s", e)
        messages.error(request, f"Ouverture de la session bancaire impossible : {e}")
        return redirect("bank_connections_list")

    connection = store_session(request.user, ctx, session)
    credentials = connection_credentials(connection)

    messages.success(
        request,
        f"Accès {ctx['aspsp']} validé jusqu'au "
        f"{consent_status(connection)['valid_until']:%d/%m/%Y}." if credentials["valid_until"] else f"Accès {ctx['aspsp']} validé.",
    )
    return redirect("enablebanking_accounts", connection_id=connection.id)


@login_required
@require_http_methods(["GET", "POST"])
def enablebanking_accounts(request: HttpRequest, connection_id: int) -> HttpResponse:
    connection = get_object_or_404(
        BankConnection, id=connection_id, owner=request.user, provider=BankConnection.Provider.ENABLEBANKING
    )
    links = list(BankAccountLink.objects.filter(connection=connection, disabled=False).order_by("name"))
    user_accounts = list(Account.objects.filter(owner=request.user).order_by("name"))
    by_id = {a.id: a for a in user_accounts}

    if request.method == "POST":
        to_sync = []
        with transaction.atomic():
            chosen_ids = set()
            for link in links:
                choice = request.POST.get(f"link_{link.id}", "")
                account = None
                if choice == "new":
                    account = Account.objects.create(
                        owner=request.user,
                        name=suggested_account_name(connection, link),
                        type=CASH_ACCOUNT_TYPES.get(link.type, Account.AccountType.CHECKING),
                        currency=link.currency or "EUR",
                    )
                elif choice.isdigit() and int(choice) in by_id:
                    account = by_id[int(choice)]
                if account is not None and account.id in chosen_ids:
                    messages.error(request, f"Le compte « {account.name} » est choisi deux fois.")
                    transaction.set_rollback(True)
                    return redirect("enablebanking_accounts", connection_id=connection.id)
                link.account = account
                link.save(update_fields=["account", "updated_at"])
                if account is not None:
                    chosen_ids.add(account.id)
                    account.bank_connection = connection
                    account.auto_sync_enabled = True
                    fields = ["bank_connection", "auto_sync_enabled"]
                    new_name = (request.POST.get(f"name_{link.id}") or "").strip()[:120]
                    if new_name and new_name != account.name:
                        account.name = new_name
                        fields.append("name")
                    account.save(update_fields=fields)
                    to_sync.append(account.id)
            # Comptes qui pointaient vers cette connexion mais ne sont plus choisis.
            Account.objects.filter(bank_connection=connection).exclude(id__in=chosen_ids).update(
                bank_connection=None, auto_sync_enabled=False
            )

        if request.POST.get("sync_now") and to_sync:
            from .tasks import sync_bank_account

            for account_id in to_sync:
                sync_bank_account.delay(account_id, sync_type=SyncLog.SyncType.MANUAL)
            messages.success(request, f"{len(to_sync)} compte(s) rattaché(s), synchronisation lancée.")
        else:
            messages.success(request, f"{len(to_sync)} compte(s) rattaché(s).")
        return redirect("bank_connections_list")

    return render(
        request,
        "finance/enablebanking_accounts.html",
        {
            "connection": connection,
            "links": links,
            "user_accounts": user_accounts,
            "consent": consent_status(connection),
            "suggested_names": {link.id: suggested_account_name(connection, link) for link in links},
        },
    )
