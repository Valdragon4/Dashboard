from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum, Count, Avg, F, Case, When, IntegerField
from django.db.models.functions import TruncDay, TruncMonth
from django.contrib import messages
from django.shortcuts import redirect, render, get_object_or_404
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.urls import reverse
from django.core.paginator import Paginator
import os
import csv
import logging

from django.conf import settings
from django.utils import timezone

from .models import (
    Transaction,
    Account,
    InvestmentHolding,
    Category,
    BankConnection,
    SyncLog,
    TradeRepublicValuationSnapshot,
    TradeRepublicPortfolioSnapshot,
    InvitationToken,
)
from .forms import AccountForm, TransactionForm, BankConnectionForm
from .services.tr_bridge_sync import sync_bridge_snapshot_with_auth_handling, get_bridge_auth_status_safe
from .services.tr_bridge_client import TradeRepublicBridgeError
from .services.encryption_service import EncryptionService
from django.http import JsonResponse
from django.utils.safestring import mark_safe
import json
from decimal import Decimal


def _safe_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _get_latest_bridge_snapshot(account: Account, valuation_end: datetime):
    return (
        TradeRepublicValuationSnapshot.objects.filter(
            account=account,
            source=TradeRepublicValuationSnapshot.Source.BRIDGE_AUTO,
            auth_status=TradeRepublicValuationSnapshot.AuthStatus.AUTHENTICATED,
            source_timestamp__lte=valuation_end,
        )
        .order_by("-source_timestamp", "-id")
        .first()
    )


def _bridge_portfolio_totals(snapshot: TradeRepublicValuationSnapshot) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    portfolio_rows = TradeRepublicPortfolioSnapshot.objects.filter(
        snapshot=snapshot,
        account_snapshot__isnull=True,
    )
    for row in portfolio_rows:
        totals[row.portfolio_type] = (totals.get(row.portfolio_type, Decimal("0")) + row.current_value)
    return totals


def _bridge_portfolio_breakdown(snapshot: TradeRepublicValuationSnapshot) -> list[dict]:
    """Retourne le détail par type de portefeuille (valuation + investi + plus-value)."""
    aggregated: dict[str, dict] = {}
    portfolio_rows = TradeRepublicPortfolioSnapshot.objects.filter(
        snapshot=snapshot,
        account_snapshot__isnull=True,
        current_value__gt=0,
    )
    for row in portfolio_rows:
        pt = row.portfolio_type
        if pt not in aggregated:
            aggregated[pt] = {"valuation": Decimal("0"), "invested": Decimal("0")}
        aggregated[pt]["valuation"] += row.current_value or Decimal("0")
        aggregated[pt]["invested"] += row.invested_total or Decimal("0")

    result = []
    type_order = ["CTO", "PEA", "PEA-PME", "CRYPTO"]
    for pt in type_order:
        if pt not in aggregated:
            continue
        val = aggregated[pt]["valuation"]
        inv = aggregated[pt]["invested"]
        gain = val - inv
        gain_pct = float(gain / inv * 100) if inv > 0 else 0
        result.append({
            "type": pt,
            "valuation": float(val),
            "invested": float(inv),
            "gain": float(gain),
            "gain_pct": gain_pct,
        })
    # Types non prévus (ordre alphabétique en fin)
    for pt, data in sorted(aggregated.items()):
        if pt not in type_order:
            val, inv = data["valuation"], data["invested"]
            gain = val - inv
            gain_pct = float(gain / inv * 100) if inv > 0 else 0
            result.append({"type": pt, "valuation": float(val), "invested": float(inv),
                            "gain": float(gain), "gain_pct": gain_pct})
    return result


def _compute_traderepublic_positions_valuation(account: Account, valuation_end: datetime) -> dict | None:
    """
    Calcule une valorisation "logique" en agrégeant les positions nettes par ISIN
    depuis les transactions enrichies (prix/quantité) importées de Trade Republic.
    """
    candidate_txs = (
        Transaction.objects.filter(
            account=account,
            posted_at__lte=valuation_end,
        )
        .exclude(amount=Decimal("0"))
        .order_by("posted_at", "id")
    )

    positions: dict[str, dict] = {}
    latest_price_ts = None
    has_enriched_data = False

    for tx in candidate_txs:
        raw = tx.raw if isinstance(tx.raw, dict) else {}
        isin = raw.get("isin")
        quantity = _safe_decimal(raw.get("investment_quantity"))
        price = _safe_decimal(raw.get("current_price"))
        invested_total = _safe_decimal(raw.get("investment_total")) or abs(tx.amount)

        if not isin or quantity is None or price is None:
            continue

        has_enriched_data = True
        sign = Decimal("1") if tx.amount > 0 else Decimal("-1")
        signed_quantity = quantity * sign
        signed_invested = invested_total * sign

        if isin not in positions:
            positions[isin] = {
                "quantity": Decimal("0"),
                "invested": Decimal("0"),
                "latest_price": price,
                "latest_price_at": tx.posted_at,
            }

        positions[isin]["quantity"] += signed_quantity
        positions[isin]["invested"] += signed_invested

        if tx.posted_at >= positions[isin]["latest_price_at"]:
            positions[isin]["latest_price"] = price
            positions[isin]["latest_price_at"] = tx.posted_at
            if latest_price_ts is None or tx.posted_at > latest_price_ts:
                latest_price_ts = tx.posted_at

    if not has_enriched_data:
        return None

    total_valuation = Decimal("0")
    total_invested = Decimal("0")
    for data in positions.values():
        net_qty = data["quantity"]
        if net_qty <= 0:
            continue
        total_valuation += net_qty * data["latest_price"]
        if data["invested"] > 0:
            total_invested += data["invested"]

    if total_valuation <= 0:
        return None

    latest_sync_snapshot = (
        Transaction.objects.filter(
            account=account,
            posted_at__lte=valuation_end,
            amount=Decimal("0"),
            raw__is_sync_valuation_snapshot=True,
        )
        .order_by("-posted_at", "-id")
        .first()
    )
    as_of = latest_sync_snapshot.posted_at if latest_sync_snapshot else (latest_price_ts or valuation_end)

    return {
        "valuation": total_valuation,
        "invested": total_invested,
        "as_of": as_of,
    }


def month_range(target: date) -> tuple[datetime, datetime]:
    start_date = target.replace(day=1)
    end_date = (start_date + relativedelta(months=1))
    # Convertir en datetime aware pour éviter les warnings
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
    if settings.USE_TZ:
        start_dt = timezone.make_aware(start_dt, timezone.get_current_timezone())
        end_dt = timezone.make_aware(end_dt, timezone.get_current_timezone())
    return start_dt, end_dt


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    current_balance_date_param = request.GET.get("current_balance_date")  # Format: YYYY-MM-DD (jour unique)
    try:
        current_balance_date = (
            datetime.strptime(current_balance_date_param, "%Y-%m-%d").date()
            if current_balance_date_param
            else timezone.now().date()
        )
    except ValueError:
        current_balance_date = timezone.now().date()

    # Récupérer les paramètres de date
    month_param = request.GET.get("month")  # Format: YYYY-MM (sélection rapide par mois)
    start_date_param = request.GET.get("start_date")  # Format: YYYY-MM-DD (période personnalisée)
    end_date_param = request.GET.get("end_date")  # Format: YYYY-MM-DD (période personnalisée)

    def _current_cycle_dates(today: date) -> tuple[date, date]:
        """
        Retourne la période courante basée sur la règle métier "du 24 au 24".
        - Si on est le 24 ou après: période [24 du mois courant -> 24 du mois suivant]
        - Sinon: période [24 du mois précédent -> 24 du mois courant]
        """
        if today.day >= 24:
            start_d = today.replace(day=24)
            end_d = (start_d + relativedelta(months=1)) + relativedelta(days=1)
        else:
            end_anchor = today.replace(day=24)
            start_d = (end_anchor - relativedelta(months=1)).replace(day=24)
            end_d = end_anchor + relativedelta(days=1)
        return start_d, end_d
    
    # Priorité : si month est fourni, l'utiliser pour calculer la période (24 du mois précédent au 24 du mois)
    if month_param:
        try:
            # Parser le mois sélectionné (YYYY-MM)
            selected_year, selected_month = map(int, month_param.split("-"))
            selected_date = date(selected_year, selected_month, 1)
            
            # Période : du 24 du mois précédent au 24 du mois sélectionné
            start_date = (selected_date - relativedelta(months=1)).replace(day=24)
            end_date = selected_date.replace(day=24) + relativedelta(days=1)  # Inclure le 24
        except (ValueError, AttributeError):
            # En cas d'erreur, utiliser la période courante
            today = timezone.now().date()
            start_date, end_date = _current_cycle_dates(today)
    
    # Sinon, si start_date et end_date sont fournis, utiliser la période personnalisée
    elif start_date_param and end_date_param:
        try:
            start_date = datetime.strptime(start_date_param, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_param, "%Y-%m-%d").date()
            # Ajouter 1 jour pour inclure la journée complète
            end_date = end_date + relativedelta(days=1)
        except ValueError:
            # Par défaut : période courante
            today = timezone.now().date()
            start_date, end_date = _current_cycle_dates(today)
    
    # Par défaut : période courante (cycle actuel)
    else:
        today = timezone.now().date()
        start_date, end_date = _current_cycle_dates(today)
    
    # Convertir en datetime aware
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
    if settings.USE_TZ:
        start_dt = timezone.make_aware(start_dt, timezone.get_current_timezone())
        end_dt = timezone.make_aware(end_dt, timezone.get_current_timezone())
    
    start, end = start_dt, end_dt

    # Exclure les transactions Trade Republic et autres comptes broker des calculs de revenus/dépenses
    # (ce sont des investissements, pas des dépenses/revenus)
    # Exclure aussi les comptes SAVINGS (livrets) car ce sont des transferts internes
    # Exclure aussi les comptes non inclus dans le dashboard
    # Exclure les snapshots de valorisation (amount=0)
    qs = Transaction.objects.filter(
        posted_at__gte=start,
        posted_at__lt=end,
        account__owner=request.user,
        account__include_in_dashboard=True,
    ).exclude(account__provider="traderepublic").exclude(account__type=Account.AccountType.SAVINGS).exclude(account__type=Account.AccountType.BROKER).exclude(amount=Decimal("0"))

    income = qs.filter(amount__gt=0).aggregate(total=Sum("amount"))["total"] or 0
    expenses_raw = qs.filter(amount__lt=0).aggregate(total=Sum("amount"))["total"] or 0
    expenses = abs(expenses_raw)
    
    # SOLDE COURANT (comptes chèques uniquement)
    # Certains providers (ex: BoursoBank) n'enrichissent pas toujours account_balance
    # sur chaque ligne; on applique donc la même logique que la vue Transactions:
    # - utiliser account_balance quand il est fiable et présent
    # - sinon recalculer avec le cumul des montants.
    trusted_raw_balance_providers = {"boursobank", "boursorama"}

    def _balance_as_of(account: Account, cutoff: datetime | None = None) -> float:
        running_balance = float(account.initial_balance or 0)
        tx_qs = Transaction.objects.filter(account=account)
        if cutoff is not None:
            tx_qs = tx_qs.filter(posted_at__lte=cutoff)
        account_txs = tx_qs.order_by("posted_at", "id").only("id", "amount", "account_balance")
        for tx in account_txs:
            if (
                account.provider in trusted_raw_balance_providers
                and tx.account_balance is not None
            ):
                running_balance = float(tx.account_balance)
            else:
                running_balance += float(tx.amount)
        return running_balance

    checking_accounts = Account.objects.filter(
        owner=request.user,
        type=Account.AccountType.CHECKING,
        include_in_dashboard=True,
    )
    checking_balance = 0
    checking_balance_at_start = 0
    checking_accounts_list = []
    
    current_balance_cutoff = datetime.combine(current_balance_date, datetime.max.time())
    if settings.USE_TZ:
        current_balance_cutoff = timezone.make_aware(
            current_balance_cutoff, timezone.get_current_timezone()
        )

    for account in checking_accounts:
        # Solde courant: toujours prendre la transaction la plus récente (jusqu'à maintenant),
        # indépendamment de la période affichée.
        account_balance = _balance_as_of(account, current_balance_cutoff)
        checking_balance += account_balance
        
        checking_accounts_list.append({
            "name": account.name,
            "provider": account.provider or "generic",
            "balance": account_balance,
        })
        
        # Solde au début de la période
        previous_day = current_balance_date - timedelta(days=1)
        start_cutoff = datetime.combine(previous_day, datetime.max.time())
        if settings.USE_TZ:
            start_cutoff = timezone.make_aware(start_cutoff, timezone.get_current_timezone())
        checking_balance_at_start += _balance_as_of(account, start_cutoff)
    
    # ÉPARGNE (livrets)
    savings_accounts = Account.objects.filter(
        owner=request.user,
        type=Account.AccountType.SAVINGS,
        include_in_dashboard=True,
    )
    savings_balance = 0
    savings_accounts_list = []
    
    for account in savings_accounts:
        latest_tx = Transaction.objects.filter(
            account=account,
            posted_at__lte=end,
            account_balance__isnull=False
        ).order_by("-posted_at", "-id").first()
        
        if latest_tx and latest_tx.account_balance is not None:
            account_balance = float(latest_tx.account_balance)
            savings_balance += account_balance
        else:
            account_initial = float(account.initial_balance or 0)
            account_sum = Transaction.objects.filter(
                account=account, posted_at__lte=end
            ).aggregate(total=Sum("amount"))["total"] or 0
            account_balance = account_initial + float(account_sum)
            savings_balance += account_balance
        
        savings_accounts_list.append({
            "name": account.name,
            "provider": account.provider or "generic",
            "balance": account_balance,
        })
    
    # INVESTISSEMENTS (Trade Republic et comptes broker)
    investment_accounts = Account.objects.filter(
        owner=request.user,
        include_in_dashboard=True,
    ).filter(
        Q(provider="traderepublic") | Q(type=Account.AccountType.BROKER)
    )
    
    total_invested = 0  # Montant total investi
    current_valuation = 0  # Valorisation actuelle (à la date de fin sélectionnée)
    investment_accounts_list_detailed = []
    global_latest_valuation_date = None  # Date de valorisation la plus récente parmi tous les comptes
    
    for account in investment_accounts:
        bridge_snapshot = None
        if account.provider == "traderepublic" and settings.TR_BRIDGE_ENABLED and not settings.TR_BRIDGE_SHADOW_MODE:
            # Pour le bridge, utiliser max(end, now) afin d'inclure les syncs d'aujourd'hui
            # même quand la période affichée se termine hier (ex: "24 mars → 24 avril").
            bridge_cutoff = max(end, timezone.now())
            bridge_snapshot = _get_latest_bridge_snapshot(account, bridge_cutoff)
        if bridge_snapshot:
            account_total_valuation = float(bridge_snapshot.total_with_cash or bridge_snapshot.positions_total)
            invested_sum = bridge_snapshot.invested_total
            latest_valuation_date = bridge_snapshot.source_timestamp
            current_valuation += account_total_valuation
            total_invested += float(invested_sum)
            if global_latest_valuation_date is None or latest_valuation_date > global_latest_valuation_date:
                global_latest_valuation_date = latest_valuation_date
            investment_accounts_list_detailed.append(
                {
                    "name": account.name,
                    "provider": account.provider or "generic",
                    "balance": account_total_valuation,
                    "invested": float(invested_sum),
                    "bridge_snapshot": bridge_snapshot,
                }
            )
            continue

        tr_positions = None
        if account.provider == "traderepublic":
            tr_positions = _compute_traderepublic_positions_valuation(account, end)
        if tr_positions:
            account_total_valuation = float(tr_positions["valuation"])
            invested_sum = tr_positions["invested"]
            latest_valuation_date = tr_positions["as_of"]
            current_valuation += account_total_valuation
            total_invested += float(invested_sum)
            if global_latest_valuation_date is None or latest_valuation_date > global_latest_valuation_date:
                global_latest_valuation_date = latest_valuation_date
            investment_accounts_list_detailed.append(
                {
                    "name": account.name,
                    "provider": account.provider or "generic",
                    "balance": account_total_valuation,
                    "invested": float(invested_sum),
                }
            )
            continue

        # Pour chaque compte, on agrège les valorisations par type de portefeuille
        # On cherche la dernière valorisation de CHAQUE type (PEA, CTO, CRYPTO) avant la date sélectionnée
        portfolio_types = ["PEA", "CTO", "CRYPTO", "PEA-PME"]
        account_total_valuation = 0
        latest_valuation_date = None  # Pour synchroniser le calcul du montant investi
        
        for portfolio_type in portfolio_types:
            # Trouver la transaction de valorisation la plus récente pour ce type de portefeuille
            latest_valuation_tx = Transaction.objects.filter(
                account=account,
                posted_at__lte=end,
                account_balance__isnull=False,
                raw__portfolio_type=portfolio_type
            ).order_by("-posted_at", "-id").first()
            
            if latest_valuation_tx and latest_valuation_tx.account_balance is not None:
                account_total_valuation += float(latest_valuation_tx.account_balance)
                # Garder la date de valorisation la plus récente
                if latest_valuation_date is None or latest_valuation_tx.posted_at > latest_valuation_date:
                    latest_valuation_date = latest_valuation_tx.posted_at
                # Mettre à jour la date de valorisation globale la plus récente
                if global_latest_valuation_date is None or latest_valuation_tx.posted_at > global_latest_valuation_date:
                    global_latest_valuation_date = latest_valuation_tx.posted_at
        
        # Si aucune valorisation par type n'a été trouvée, utiliser l'ancienne méthode (fallback)
        if account_total_valuation == 0:
            latest_valuation_tx = Transaction.objects.filter(
                account=account,
                posted_at__lte=end,
                account_balance__isnull=False
            ).order_by("-posted_at", "-id").first()
            
            if latest_valuation_tx and latest_valuation_tx.account_balance is not None:
                account_total_valuation = float(latest_valuation_tx.account_balance)
                latest_valuation_date = latest_valuation_tx.posted_at
            else:
                # Sinon utiliser initial_balance
                account_total_valuation = float(account.initial_balance or 0)
                latest_valuation_date = end  # Par défaut, utiliser la date de fin
        
        # Mettre à jour la date de valorisation globale la plus récente
        if latest_valuation_date:
            if global_latest_valuation_date is None or latest_valuation_date > global_latest_valuation_date:
                global_latest_valuation_date = latest_valuation_date
        
        current_valuation += account_total_valuation
        
        # IMPORTANT : Le montant investi doit être calculé À LA DATE DE LA DERNIÈRE VALORISATION
        # pour éviter les incohérences (comparer une valorisation du 05/11 avec un investi du 10/11)
        # Exclure les snapshots de valorisation (amount=0) car ce ne sont pas des opérations d'investissement
        invested_sum = Transaction.objects.filter(
            account=account,
            posted_at__lte=latest_valuation_date
        ).exclude(
            amount=Decimal("0")
        ).aggregate(total=Sum("amount"))["total"] or 0
        total_invested += float(invested_sum)
        
        investment_accounts_list_detailed.append({
            "name": account.name,
            "provider": account.provider or "generic",
            "balance": account_total_valuation,
            "invested": float(invested_sum),
        })
    
    # Calculer la plus-value et le pourcentage
    investment_gain = current_valuation - total_invested
    investment_gain_percent = (investment_gain / total_invested * 100) if total_invested > 0 else 0
    
    # Solde réel = solde courant uniquement
    real_balance = checking_balance
    period_balance = checking_balance - checking_balance_at_start
    
    # Patrimoine total = solde courant + épargne + valorisation des investissements
    total_wealth = checking_balance + savings_balance + current_valuation

    # Calculer history_start en soustrayant 5 mois depuis la date de début
    history_start_date = start_date - relativedelta(months=5)
    history_start_dt = datetime.combine(history_start_date, datetime.min.time())
    if settings.USE_TZ:
        history_start_dt = timezone.make_aware(history_start_dt, timezone.get_current_timezone())
    history_qs = (
        Transaction.objects.filter(
            account__owner=request.user,
            account__include_in_dashboard=True,
            posted_at__gte=history_start_dt,
            posted_at__lt=end,
        )
        .exclude(account__provider="traderepublic")
        .exclude(account__type=Account.AccountType.SAVINGS)
        .exclude(account__type=Account.AccountType.BROKER)
        .exclude(amount=Decimal("0"))
        .annotate(month=TruncMonth("posted_at"))
        .values("month")
        .annotate(
            income=Sum("amount", filter=Q(amount__gt=0)),
            expenses=Sum("amount", filter=Q(amount__lt=0)),
        )
        .order_by("month")
    )

    history_labels: list[str] = []
    history_income: list[float] = []
    history_expenses: list[float] = []
    history_balance: list[float] = []
    for bucket in history_qs:
        month_label = bucket["month"].strftime("%b %Y") if bucket["month"] else "?"
        history_labels.append(month_label)
        month_income = float(bucket["income"] or 0)
        month_expenses_raw = float(bucket["expenses"] or 0)
        month_expenses = abs(month_expenses_raw)
        history_income.append(month_income)
        history_expenses.append(month_expenses)
        history_balance.append(month_income + month_expenses_raw)

    daily_qs = (
        qs.filter(amount__lt=0)
        .annotate(day=TruncDay("posted_at"))
        .values("day")
        .annotate(total=Sum("amount"))
        .order_by("day")
    )
    daily_labels = [item["day"].strftime("%d/%m") for item in daily_qs]
    daily_values = [abs(float(item["total"] or 0)) for item in daily_qs]

    category_qs = (
        qs.filter(amount__lt=0)
        .values("category__name", "category__parent__name")
        .annotate(total=Sum("amount"))
        .order_by("total")[:8]
    )
    category_labels = [item["category__name"] or "Sans catégorie" for item in category_qs]
    category_values = [abs(float(item["total"] or 0)) for item in category_qs]
    category_parents = [item["category__parent__name"] for item in category_qs]

    # Métriques supplémentaires
    transaction_count = qs.count()
    income_count = qs.filter(amount__gt=0).count()
    expense_count = qs.filter(amount__lt=0).count()
    
    # Calculer le nombre de jours dans la période
    days_in_period = (end_date - start_date).days
    if days_in_period == 0:
        days_in_period = 1
    avg_daily_expense = expenses / days_in_period if days_in_period > 0 else 0
    
    # Calculer la période précédente pour la comparaison
    period_duration = end_date - start_date
    prev_start = start_date - period_duration
    prev_end = start_date
    prev_start_dt = datetime.combine(prev_start, datetime.min.time())
    prev_end_dt = datetime.combine(prev_end, datetime.min.time())
    if settings.USE_TZ:
        prev_start_dt = timezone.make_aware(prev_start_dt, timezone.get_current_timezone())
        prev_end_dt = timezone.make_aware(prev_end_dt, timezone.get_current_timezone())
    
    prev_qs = Transaction.objects.filter(
        posted_at__gte=prev_start_dt,
        posted_at__lt=prev_end_dt,
        account__owner=request.user,
        account__include_in_dashboard=True,
    ).exclude(account__provider="traderepublic").exclude(account__type=Account.AccountType.SAVINGS).exclude(account__type=Account.AccountType.BROKER).exclude(amount=Decimal("0"))
    
    prev_expenses = abs(float(prev_qs.filter(amount__lt=0).aggregate(total=Sum("amount"))["total"] or 0))
    prev_income = float(prev_qs.filter(amount__gt=0).aggregate(total=Sum("amount"))["total"] or 0)
    
    # Convertir expenses et income en float pour les calculs de tendances
    expenses_float = float(expenses)
    income_float = float(income)
    
    # Calculer les tendances
    expense_trend = 0
    if prev_expenses > 0:
        expense_trend = ((expenses_float - prev_expenses) / prev_expenses) * 100
    
    income_trend = 0
    if prev_income > 0:
        income_trend = ((income_float - prev_income) / prev_income) * 100
    
    # Regex partagée pour détecter les catégories d'épargne/placement
    savings_category_regex = r"(^|[^a-zA-ZÀ-ÿ])(epargnes?|épargnes?|investissements?|placements?)([^a-zA-ZÀ-ÿ]|$)"

    def savings_transfer_total(queryset):
        return abs(
            float(
                queryset.filter(amount__lt=0)
                .filter(
                    Q(category__name__iregex=savings_category_regex)
                    | Q(category__parent__name__iregex=savings_category_regex)
                )
                .aggregate(total=Sum("amount"))["total"]
                or 0
            )
        )

    # STATISTIQUES AVANCÉES - Analyse sur 6 mois
    stats_start = start_date - relativedelta(months=6)
    stats_start_dt = datetime.combine(stats_start, datetime.min.time())
    if settings.USE_TZ:
        stats_start_dt = timezone.make_aware(stats_start_dt, timezone.get_current_timezone())
    
    # Récupérer les données mensuelles sur 6 mois
    monthly_stats = []
    for i in range(6):
        month_start = start_date - relativedelta(months=6-i)
        month_end = month_start + relativedelta(months=1)
        month_start_dt = datetime.combine(month_start, datetime.min.time())
        month_end_dt = datetime.combine(month_end, datetime.min.time())
        if settings.USE_TZ:
            month_start_dt = timezone.make_aware(month_start_dt, timezone.get_current_timezone())
            month_end_dt = timezone.make_aware(month_end_dt, timezone.get_current_timezone())
        
        month_qs = Transaction.objects.filter(
            posted_at__gte=month_start_dt,
            posted_at__lt=month_end_dt,
            account__owner=request.user,
            account__include_in_dashboard=True,
        ).exclude(account__provider="traderepublic").exclude(account__type=Account.AccountType.SAVINGS).exclude(account__type=Account.AccountType.BROKER).exclude(amount=Decimal("0"))
        
        month_income = float(month_qs.filter(amount__gt=0).aggregate(total=Sum("amount"))["total"] or 0)
        month_expenses = abs(float(month_qs.filter(amount__lt=0).aggregate(total=Sum("amount"))["total"] or 0))
        month_savings_transfer = savings_transfer_total(month_qs)
        month_effective_expenses = month_expenses - month_savings_transfer
        month_balance = month_income - month_effective_expenses
        
        monthly_stats.append({
            "label": month_start.strftime("%b %Y"),
            "income": month_income,
            "expenses": month_effective_expenses,
            "balance": month_balance,
        })
    
    # Calculer les moyennes sur 6 mois
    avg_6m_income = sum(m["income"] for m in monthly_stats) / len(monthly_stats) if monthly_stats else 0
    avg_6m_expenses = sum(m["expenses"] for m in monthly_stats) / len(monthly_stats) if monthly_stats else 0
    avg_6m_balance = sum(m["balance"] for m in monthly_stats) / len(monthly_stats) if monthly_stats else 0
    
    # Trouver le meilleur et pire mois
    best_month = max(monthly_stats, key=lambda x: x["balance"]) if monthly_stats else None
    worst_month = min(monthly_stats, key=lambda x: x["balance"]) if monthly_stats else None
    
    # Taux d'épargne
    # Les versements vers l'épargne/placements sont enregistrés comme dépenses,
    # mais doivent compter comme épargne dans ce ratio.
    savings_transfer_expenses = savings_transfer_total(qs)
    stats_expenses = expenses_float - savings_transfer_expenses
    stats_period_balance = income_float - stats_expenses
    net_savings = stats_period_balance
    savings_rate = (net_savings / income_float) * 100 if income_float > 0 else 0
    
    # Projection pour le mois suivant (basée sur la moyenne des 3 derniers mois)
    if len(monthly_stats) >= 3:
        last_3_months = monthly_stats[-3:]
        projected_income = sum(m["income"] for m in last_3_months) / 3
        projected_expenses = sum(m["expenses"] for m in last_3_months) / 3
        projected_balance = projected_income - projected_expenses
    else:
        projected_income = income_float
        projected_expenses = stats_expenses
        projected_balance = stats_period_balance

    # Générer le label de période
    if start_date == end_date - relativedelta(days=1):
        # Même jour
        period_label = start_date.strftime("%d %B %Y")
    elif start_date.month == (end_date - relativedelta(days=1)).month and start_date.year == (end_date - relativedelta(days=1)).year:
        # Même mois
        period_label = start_date.strftime("%B %Y")
    else:
        # Période personnalisée
        end_display = end_date - relativedelta(days=1)
        period_label = f"{start_date.strftime('%d %b %Y')} - {end_display.strftime('%d %b %Y')}"

    # Préparer la liste des comptes d'investissement pour le modal
    investment_accounts_list = []
    for account in investment_accounts:
        investment_accounts_list.append({
            "id": account.id,
            "name": account.name,
            "provider": account.provider,
            "balance_snapshot_date": account.balance_snapshot_date.strftime("%Y-%m-%d") if account.balance_snapshot_date else None,
        })
    
    # Préparer les données pour le diagramme de Sankey (cashflow par catégorie)
    # IMPORTANT : Exclure les comptes SAVINGS (livrets) car ce sont des mouvements internes
    # Le Sankey doit montrer uniquement les flux de trésorerie réels (revenus/dépenses)
    # pas les transferts entre vos propres comptes
    qs_sankey = qs.exclude(account__type=Account.AccountType.SAVINGS)
    
    # Récupérer les revenus par catégorie (uniquement comptes courants)
    income_by_category = (
        qs_sankey.filter(amount__gt=0)
        .values("category__name", "category__parent__name")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    
    # Récupérer les dépenses par catégorie (uniquement comptes courants)
    expenses_by_category = (
        qs_sankey.filter(amount__lt=0)
        .values("category__name", "category__parent__name")
        .annotate(total=Sum("amount"))
        .order_by("total")
    )
    
    # Construire les données pour le Sankey
    # Format: [{source: "Salaire", target: "Revenus", value: 2500}, ...]
    sankey_data = []
    
    # Préparer les détails des transactions par catégorie pour le panneau
    sankey_details = {
        "income": [],
        "expenses": []
    }
    
    # Revenus: Catégories → "Revenus totaux"
    # On préfixe avec "💰 " pour éviter les cycles avec les dépenses
    for item in income_by_category:
        category_name = item["category__name"] or "Sans catégorie"
        value = float(item["total"])
        sankey_data.append({
            "source": f"💰 {category_name}",
            "target": "Revenus",
            "value": value,
        })
        
        # Récupérer les transactions de cette catégorie (uniquement comptes courants)
        if item["category__name"]:
            transactions = qs_sankey.filter(
                amount__gt=0,
                category__name=item["category__name"]
            ).values("posted_at", "description", "amount", "account__name").order_by("-posted_at")[:10]
        else:
            transactions = qs_sankey.filter(
                amount__gt=0,
                category__isnull=True
            ).values("posted_at", "description", "amount", "account__name").order_by("-posted_at")[:10]
        
        # Convertir les dates en chaînes pour JSON
        transactions_list = []
        for tx in transactions:
            transactions_list.append({
                "date": tx["posted_at"].strftime("%d/%m/%Y"),
                "description": tx["description"],
                "amount": float(tx["amount"]),
                "account": tx["account__name"]
            })
        
        sankey_details["income"].append({
            "category": category_name,
            "total": value,
            "count": qs_sankey.filter(amount__gt=0, category__name=item["category__name"]).count() if item["category__name"] else qs_sankey.filter(amount__gt=0, category__isnull=True).count(),
            "transactions": transactions_list
        })
    
    # Dépenses: "Revenus totaux" → Catégories de dépenses
    # On préfixe avec "💸 " pour éviter les cycles avec les revenus
    for item in expenses_by_category:
        category_name = item["category__name"] or "Sans catégorie"
        value = abs(float(item["total"]))
        sankey_data.append({
            "source": "Revenus",
            "target": f"💸 {category_name}",
            "value": value,
        })
        
        # Récupérer les transactions de cette catégorie (uniquement comptes courants)
        if item["category__name"]:
            transactions = qs_sankey.filter(
                amount__lt=0,
                category__name=item["category__name"]
            ).values("posted_at", "description", "amount", "account__name").order_by("-posted_at")[:10]
        else:
            transactions = qs_sankey.filter(
                amount__lt=0,
                category__isnull=True
            ).values("posted_at", "description", "amount", "account__name").order_by("-posted_at")[:10]
        
        # Convertir les dates en chaînes pour JSON
        transactions_list = []
        for tx in transactions:
            transactions_list.append({
                "date": tx["posted_at"].strftime("%d/%m/%Y"),
                "description": tx["description"],
                "amount": float(tx["amount"]),
                "account": tx["account__name"]
            })
        
        sankey_details["expenses"].append({
            "category": category_name,
            "total": value,
            "count": qs_sankey.filter(amount__lt=0, category__name=item["category__name"]).count() if item["category__name"] else qs_sankey.filter(amount__lt=0, category__isnull=True).count(),
            "transactions": transactions_list
        })
    
    context = {
        "income": float(income),
        "expenses": float(expenses),
        "stats_expenses": float(stats_expenses),
        "balance": float(real_balance),  # Solde courant uniquement
        "period_balance": float(period_balance),
        "current_balance_date": current_balance_date.strftime("%Y-%m-%d"),
        "stats_period_balance": float(stats_period_balance),
        "checking_balance": float(checking_balance),
        "savings_balance": float(savings_balance),
        "total_invested": float(total_invested),
        "current_valuation": float(current_valuation),
        "investment_gain": float(investment_gain),
        "investment_gain_percent": float(investment_gain_percent),
        "total_wealth": float(total_wealth),
        "investment_accounts": investment_accounts_list,
        "checking_accounts_list": checking_accounts_list,
        "savings_accounts_list": savings_accounts_list,
        "investment_accounts_list_detailed": investment_accounts_list_detailed,
        "bridge_portfolio_breakdown": next(
            (_bridge_portfolio_breakdown(acc["bridge_snapshot"])
             for acc in investment_accounts_list_detailed
             if acc.get("bridge_snapshot")),
            []
        ),
        "transaction_count": transaction_count,
        "income_count": income_count,
        "expense_count": expense_count,
        "avg_daily_expense": float(avg_daily_expense),
        "expense_trend": float(expense_trend),
        "income_trend": float(income_trend),
        "period_label": period_label,
        # Statistiques avancées
        "savings_rate": float(savings_rate),
        "avg_6m_income": float(avg_6m_income),
        "avg_6m_expenses": float(avg_6m_expenses),
        "avg_6m_balance": float(avg_6m_balance),
        "best_month": best_month,
        "worst_month": worst_month,
        "projected_income": float(projected_income),
        "projected_expenses": float(projected_expenses),
        "projected_balance": float(projected_balance),
        "monthly_stats": monthly_stats,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": (end_date - relativedelta(days=1)).strftime("%Y-%m-%d"),
        "selected_month": month_param,
        "is_custom_period": not month_param and (start_date_param and end_date_param),  # Indique si c'est une période personnalisée (False si month est fourni)
        "valuation_date_display": global_latest_valuation_date.strftime("%d/%m/%Y") if global_latest_valuation_date else (end_date - relativedelta(days=1)).strftime("%d/%m/%Y"),  # Date de valorisation pour l'affichage
        "history_chart": {
            "labels": history_labels,
            "income": history_income,
            "expenses": history_expenses,
            "balance": history_balance,
        },
        "daily_chart": {
            "labels": daily_labels,
            "values": daily_values,
        },
        "category_chart": {
            "labels": category_labels,
            "values": category_values,
            "parents": category_parents,
        },
        "sankey_data": mark_safe(json.dumps(sankey_data)),
        "sankey_details": mark_safe(json.dumps(sankey_details)),
    }
    return render(request, "finance/dashboard.html", context)


@login_required
def transactions(request: HttpRequest) -> HttpResponse:
    # Récupérer le filtre par compte depuis les paramètres GET
    account_id = request.GET.get("account")
    
    # Récupérer tous les comptes de l'utilisateur pour le sélecteur
    accounts = Account.objects.filter(owner=request.user).order_by("name")
    
    # Construire la requête de base
    txs_query = Transaction.objects.filter(account__owner=request.user).select_related("account", "category")
    
    # Filtrer par compte si un compte est sélectionné
    if account_id:
        try:
            account_id_int = int(account_id)
            txs_query = txs_query.filter(account_id=account_id_int)
        except (ValueError, TypeError):
            pass  # Ignorer les valeurs invalides
    
    # Trier par date (du plus récent au plus ancien) pour l'affichage.
    # On évite d'utiliser account_balance pour l'ordre global car certaines sources
    # (ex: Trade Republic) peuvent fournir des soldes bruts non adaptés à cette vue.
    txs_query = txs_query.order_by("-posted_at", "-id")
    
    # Pagination : 100 transactions par page
    paginator = Paginator(txs_query, 100)
    page_number = request.GET.get("page", 1)
    
    try:
        page = paginator.get_page(page_number)
    except:
        page = paginator.get_page(1)
    
    # Calcul du solde "après transaction" par compte, avec ordre chronologique stable.
    # On n'utilise account_balance brut que pour des providers où ce champ est fiable.
    trusted_raw_balance_providers = {"boursobank", "boursorama"}
    page_tx_ids = {tx.id for tx in page.object_list}
    accounts_on_page = {tx.account_id for tx in page.object_list}
    balance_after_by_tx_id: dict[int, float] = {}

    for account_obj in accounts.filter(id__in=accounts_on_page):
        running_balance = float(account_obj.initial_balance or 0)
        account_txs = (
            Transaction.objects.filter(account_id=account_obj.id, account__owner=request.user)
            .order_by("posted_at", "id")
            .only("id", "amount", "account_balance")
        )

        for tx in account_txs:
            if (
                account_obj.provider in trusted_raw_balance_providers
                and tx.account_balance is not None
            ):
                running_balance = float(tx.account_balance)
            else:
                running_balance += float(tx.amount)

            if tx.id in page_tx_ids:
                balance_after_by_tx_id[tx.id] = running_balance

    # Restituer dans l'ordre d'affichage (plus récent -> plus ancien)
    transactions_with_balance = []
    for tx in page.object_list:
        fallback = float((tx.account.initial_balance or 0) + tx.amount)
        transactions_with_balance.append(
            {
                "transaction": tx,
                "balance_after": balance_after_by_tx_id.get(tx.id, fallback),
            }
        )
    
    # Convertir l'ID du compte sélectionné en entier pour la comparaison dans le template
    selected_account_id_int = None
    if account_id:
        try:
            selected_account_id_int = int(account_id)
        except (ValueError, TypeError):
            pass
    
    # Récupérer toutes les catégories pour le sélecteur
    from .models import Category
    categories = Category.objects.all().select_related("parent").order_by("parent__name", "name")
    
    return render(request, "finance/transactions.html", {
        "transactions": transactions_with_balance,
        "page": page,
        "accounts": accounts,
        "selected_account_id": selected_account_id_int,
        "categories": categories,
    })


@login_required
def accounts(request: HttpRequest) -> HttpResponse:
    """Affiche la liste des comptes avec statut de synchronisation."""
    # Optimiser les requêtes avec select_related et prefetch_related pour éviter les requêtes N+1
    accts = (
        Account.objects.filter(owner=request.user)
        .select_related("bank_connection")
        .prefetch_related("bank_connection__sync_logs")
        .order_by("-created_at")
    )

    # Pour chaque compte, calculer les informations de synchronisation
    for account in accts:
        if account.bank_connection:
            # Récupérer le dernier SyncLog réussi depuis le prefetch
            last_success_log = None
            for log in account.bank_connection.sync_logs.all():
                if log.status == SyncLog.Status.SUCCESS and log.completed_at:
                    if not last_success_log or log.completed_at > last_success_log.completed_at:
                        last_success_log = log

            account.sync_info = {
                "status": account.bank_connection.sync_status,
                "last_sync_at": account.bank_connection.last_sync_at,
                "auto_sync_enabled": account.auto_sync_enabled,
                "last_success_log": last_success_log,
                "bank_connection_id": account.bank_connection.id,
            }
        else:
            account.sync_info = None

    return render(request, "finance/accounts.html", {"accounts": accts})


@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    return render(request, "finance/settings.html")


@login_required
def account_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            account = form.save(commit=False)
            account.owner = request.user
            account.save()
            messages.success(request, "Compte créé.")
            return redirect("accounts")
    else:
        form = AccountForm()
    return render(request, "finance/account_form.html", {"form": form, "title": "Nouveau compte"})


@login_required
def transaction_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = TransactionForm(request.POST)
        form.fields["account"].queryset = Account.objects.filter(owner=request.user)
        if form.is_valid():
            tx = form.save(commit=False)
            if tx.account.owner_id != request.user.id:
                messages.error(request, "Compte invalide.")
            else:
                tx.save()
                messages.success(request, "Transaction ajoutée.")
                return redirect("transactions")
    else:
        form = TransactionForm()
        form.fields["account"].queryset = Account.objects.filter(owner=request.user)
    return render(request, "finance/transaction_form.html", {"form": form, "title": "Nouvelle transaction"})


@login_required
def account_delete(request: HttpRequest, account_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("accounts")
    acc = Account.objects.filter(id=account_id, owner=request.user).first()
    if not acc:
        messages.error(request, "Compte introuvable.")
        return redirect("accounts")
    acc.delete()
    messages.success(request, "Compte supprimé.")
    return redirect("accounts")


@login_required
def account_detail(request: HttpRequest, account_id: int) -> HttpResponse:
    """
    Affiche les détails d'un compte d'investissement avec ses holdings
    groupés par type de portefeuille (CTO/PEA) et les opérations d'investissement
    """
    account = Account.objects.filter(id=account_id, owner=request.user, type=Account.AccountType.BROKER).first()
    if not account:
        messages.error(request, "Compte introuvable.")
        return redirect("accounts")
    
    # Récupérer le paramètre de date (optionnel)
    valuation_date_param = request.GET.get("valuation_date")
    
    # Définir la date de valorisation à utiliser
    if valuation_date_param:
        try:
            valuation_date = datetime.strptime(valuation_date_param, "%Y-%m-%d").date()
            # Convertir en datetime aware pour les requêtes
            valuation_dt = datetime.combine(valuation_date, datetime.max.time())
            if settings.USE_TZ:
                valuation_dt = timezone.make_aware(valuation_dt, timezone.get_current_timezone())
        except ValueError:
            valuation_date = timezone.now().date()
            valuation_dt = timezone.now()
    else:
        valuation_date = timezone.now().date()
        valuation_dt = timezone.now()
    
    # Calculer la valorisation par type de portefeuille à la date sélectionnée
    portfolio_types = ["PEA", "CTO", "CRYPTO", "PEA-PME"]
    portfolio_totals = {}
    latest_valuation_date = None
    total_value = Decimal("0")
    # Dictionnaire pour stocker les détails des titres par portefeuille depuis les snapshots
    portfolio_holdings = defaultdict(list)

    if account.provider == "traderepublic" and settings.TR_BRIDGE_ENABLED and not settings.TR_BRIDGE_SHADOW_MODE:
        bridge_snapshot = _get_latest_bridge_snapshot(account, valuation_dt)
        if bridge_snapshot:
            portfolio_totals = _bridge_portfolio_totals(bridge_snapshot)
            total_value = sum(portfolio_totals.values(), Decimal("0"))
            latest_valuation_date = bridge_snapshot.source_timestamp

    if total_value == 0:
        for portfolio_type in portfolio_types:
            # Trouver la transaction de valorisation la plus récente pour ce type avant la date
            latest_valuation_tx = Transaction.objects.filter(
                account=account,
                posted_at__lte=valuation_dt,
                account_balance__isnull=False,
                raw__portfolio_type=portfolio_type
            ).order_by("-posted_at", "-id").first()
            
            if latest_valuation_tx and latest_valuation_tx.account_balance is not None:
                portfolio_totals[portfolio_type] = latest_valuation_tx.account_balance
                total_value += latest_valuation_tx.account_balance
                # Garder la date de valorisation la plus récente
                if latest_valuation_date is None or latest_valuation_tx.posted_at > latest_valuation_date:
                    latest_valuation_date = latest_valuation_tx.posted_at
                
                # Extraire les détails des titres depuis le snapshot
                raw_data = latest_valuation_tx.raw or {}
                if isinstance(raw_data, dict) and "data" in raw_data:
                    pf_data = raw_data["data"]
                    # Format multi-portefeuilles : raw.data est un portefeuille avec type, valorisation, titres
                    if isinstance(pf_data, dict) and pf_data.get("type") == portfolio_type:
                        # Extraire les titres de ce portefeuille
                        for titre in pf_data.get("titres", []):
                            portfolio_holdings[portfolio_type].append({
                                "symbol": titre.get("symbole", ""),
                                "name": titre.get("nom", ""),
                                "instrument_type": titre.get("type", "stock"),
                                "quantity": Decimal(str(titre.get("quantite", 0))),
                                "unit_price": Decimal(str(titre.get("prix_unitaire", 0))),
                                "total_value": Decimal(str(titre.get("valeur_totale", 0))),
                            })
                    # Format single portefeuille : raw.data contient directement les titres (liste)
                    elif isinstance(pf_data, list):
                        for titre in pf_data:
                            portfolio_holdings[portfolio_type].append({
                                "symbol": titre.get("symbole", ""),
                                "name": titre.get("nom", ""),
                                "instrument_type": titre.get("type", "stock"),
                                "quantity": Decimal(str(titre.get("quantite", 0))),
                                "unit_price": Decimal(str(titre.get("prix_unitaire", 0))),
                                "total_value": Decimal(str(titre.get("valeur_totale", 0))),
                            })
    
    # Si aucune valorisation trouvée, utiliser les holdings actuels comme fallback
    if total_value == 0:
        holdings = InvestmentHolding.objects.filter(account=account).order_by('tax_wrapper', 'name')
        for holding in holdings:
            wrapper = holding.tax_wrapper or "Autre"
            value = holding.quantity * holding.avg_cost
            if wrapper not in portfolio_totals:
                portfolio_totals[wrapper] = Decimal("0")
            portfolio_totals[wrapper] += value
            total_value += value
            # Ajouter aux holdings pour l'affichage
            portfolio_holdings[wrapper].append({
                "symbol": holding.symbol,
                "name": holding.name,
                "instrument_type": holding.instrument_type,
                "quantity": holding.quantity,
                "unit_price": holding.avg_cost,
                "total_value": value,
            })
        latest_valuation_date = valuation_dt
    
    # Convertir en dictionnaire pour le template
    holdings_by_portfolio = dict(portfolio_holdings)
    
    # Récupérer les transactions (opérations d'investissement)
    # Exclure les snapshots (amount = 0) pour ne garder que les vraies opérations
    transactions = Transaction.objects.filter(
        account=account
    ).exclude(
        amount=Decimal("0")
    ).order_by('-posted_at')  # Afficher TOUTES les transactions (pas de limite)
    
    # Calculer le montant total investi À LA DATE DE LA DERNIÈRE VALORISATION
    # pour synchroniser avec la valorisation affichée
    total_invested = Transaction.objects.filter(
        account=account,
        posted_at__lte=latest_valuation_date
    ).exclude(
        amount=Decimal("0")
    ).aggregate(
        total=Sum('amount')
    )['total'] or Decimal("0")
    
    # Compter le nombre total de transactions
    transaction_count = transactions.count()
    
    # Calculer la plus/moins-value
    gain_loss = total_value - abs(total_invested)
    gain_loss_percent = (gain_loss / abs(total_invested) * 100) if total_invested != 0 else Decimal("0")
    
    return render(request, "finance/account_detail.html", {
        "account": account,
        "holdings_by_portfolio": dict(holdings_by_portfolio),
        "portfolio_totals": dict(portfolio_totals),
        "total_value": total_value,
        "transactions": transactions,
        "transaction_count": transaction_count,
        "total_invested": abs(total_invested),
        "gain_loss": gain_loss,
        "gain_loss_percent": gain_loss_percent,
        "valuation_date": valuation_date.strftime("%Y-%m-%d"),
        "valuation_date_display": latest_valuation_date.strftime("%d/%m/%Y") if latest_valuation_date else valuation_date.strftime("%d/%m/%Y"),
    })


@login_required
def transaction_delete(request: HttpRequest, transaction_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("transactions")
    tx = Transaction.objects.filter(id=transaction_id, account__owner=request.user).first()
    if not tx:
        messages.error(request, "Transaction introuvable.")
        return redirect("transactions")
    
    # Sauvegarder l'ID du compte avant de supprimer
    account_id = tx.account.id
    tx.delete()
    messages.success(request, "Transaction supprimée.")
    
    # Vérifier si on doit retourner vers account_detail
    return_to = request.POST.get("return_to")
    if return_to == "account_detail":
        return_account_id = request.POST.get("account_id", account_id)
        return redirect("account_detail", account_id=return_account_id)
    
    # Conserver les paramètres de pagination et de filtre lors de la redirection
    # Récupérer depuis POST (champs cachés du formulaire) ou GET (URL précédente)
    redirect_url = reverse("transactions")
    params = []
    page_param = request.POST.get("page") or request.GET.get("page")
    account_param = request.POST.get("account") or request.GET.get("account")
    if page_param:
        params.append(f"page={page_param}")
    if account_param:
        params.append(f"account={account_param}")
    if params:
        redirect_url += "?" + "&".join(params)
    
    return redirect(redirect_url)


@login_required
def delete_all_investment_transactions(request: HttpRequest, account_id: int) -> HttpResponse:
    """Supprime toutes les opérations d'investissement (transactions non-snapshot) d'un compte."""
    if request.method != "POST":
        return redirect("account_detail", account_id=account_id)
    
    # Vérifier que le compte appartient bien à l'utilisateur
    account = Account.objects.filter(id=account_id, owner=request.user).first()
    if not account:
        messages.error(request, "Compte introuvable.")
        return redirect("accounts")
    
    # Supprimer toutes les transactions avec amount != 0 (les opérations d'investissement)
    # Les transactions avec amount = 0 sont des snapshots (valorisation), on les garde
    deleted_count = Transaction.objects.filter(
        account=account
    ).exclude(
        amount=Decimal("0")
    ).delete()[0]
    
    messages.success(request, f"✅ {deleted_count} opération(s) d'investissement supprimée(s).")
    return redirect("account_detail", account_id=account_id)


@login_required
def reset_user_finance(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("accounts")
    # Purge locale pour l'utilisateur courant
    Transaction.objects.filter(account__owner=request.user).delete()
    Account.objects.filter(owner=request.user).delete()
    messages.success(request, "Données financières utilisateur vidées.")
    return redirect("accounts")


@login_required
def update_investment_valuation(request: HttpRequest) -> HttpResponse:
    """Met à jour la valorisation d'un compte d'investissement."""
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        account_id = data.get("account_id")
        valuation = data.get("valuation")
        valuation_date = data.get("date")
        
        if not account_id:
            return JsonResponse({"error": "Paramètres manquants"}, status=400)
        
        # Récupérer le compte
        account = Account.objects.get(id=account_id, owner=request.user)
        
        # Vérifier que c'est un compte d'investissement
        if account.provider != "traderepublic" and account.type != Account.AccountType.BROKER:
            return JsonResponse({"error": "Ce compte n'est pas un compte d'investissement"}, status=400)
        
        if settings.TR_BRIDGE_ENABLED and account.provider == "traderepublic":
            logger = logging.getLogger(__name__)
            logger.info("tr_bridge_sync_requested source=update_investment_valuation account_id=%s", account.id)
            snapshot = sync_bridge_snapshot_with_auth_handling(account=account)
            if (
                snapshot.auth_status
                == TradeRepublicValuationSnapshot.AuthStatus.NEEDS_MANUAL_AUTH
            ):
                logger.warning("tr_bridge_sync_auth_required source=update_investment_valuation account_id=%s", account.id)
                auth_status = get_bridge_auth_status_safe()
                return JsonResponse(
                    {
                        "error": "Authentification Trade Republic requise",
                        "auth_status": auth_status,
                    },
                    status=409,
                )
            return JsonResponse(
                {
                    "success": True,
                    "message": "Valorisation synchronisée depuis le bridge",
                    "snapshot_id": snapshot.id,
                    "value": float(snapshot.total_with_cash),
                    "date": snapshot.source_timestamp.isoformat(),
                }
            )

        if valuation is None or not valuation_date:
            return JsonResponse({"error": "Paramètres manquants"}, status=400)

        # Parser la date
        from datetime import datetime

        valuation_datetime = datetime.strptime(valuation_date, "%Y-%m-%d")
        if settings.USE_TZ:
            valuation_datetime = timezone.make_aware(
                valuation_datetime, timezone.get_current_timezone()
            )

        # Créer une transaction "snapshot" avec amount=0 et account_balance=valuation
        from decimal import Decimal

        Transaction.objects.create(
            account=account,
            posted_at=valuation_datetime,
            amount=Decimal("0"),
            description=f"Valorisation manuelle - {valuation} €",
            account_balance=Decimal(str(valuation)),
            raw={"source": "manual_valuation", "type": "snapshot"},
        )

        # Mettre à jour initial_balance si c'est la valorisation la plus récente
        account.initial_balance = Decimal(str(valuation))
        account.balance_snapshot_date = valuation_datetime.date()
        account.save(update_fields=["initial_balance", "balance_snapshot_date"])

        messages.success(
            request,
            f"Valorisation de {account.name} mise à jour : {valuation} € au {valuation_date}",
        )
        return JsonResponse({"success": True, "message": "Valorisation mise à jour avec succès"})
        
    except Account.DoesNotExist:
        return JsonResponse({"error": "Compte introuvable"}, status=404)
    except ValueError as e:
        return JsonResponse({"error": f"Erreur de format : {str(e)}"}, status=400)
    except Exception as e:
        import traceback
        error_msg = str(e)
        if settings.DEBUG:
            error_msg += f"\n{traceback.format_exc()}"
        return JsonResponse({"error": error_msg}, status=500)


@login_required
def toggle_account_in_dashboard(request: HttpRequest, account_id: int) -> JsonResponse:
    """API endpoint pour activer/désactiver un compte dans le dashboard"""
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    try:
        account = Account.objects.get(id=account_id, owner=request.user)
        
        # Toggle le paramètre
        account.include_in_dashboard = not account.include_in_dashboard
        account.save(update_fields=["include_in_dashboard"])
        
        status_text = "inclus dans" if account.include_in_dashboard else "exclu du"
        messages.success(request, f"Le compte {account.name} est maintenant {status_text} dashboard")
        
        return JsonResponse({
            "success": True,
            "include_in_dashboard": account.include_in_dashboard,
            "message": f"Le compte {account.name} est maintenant {status_text} dashboard"
        })
        
    except Account.DoesNotExist:
        return JsonResponse({"error": "Compte introuvable"}, status=404)
    except Exception as e:
        import traceback
        error_msg = str(e)
        if settings.DEBUG:
            error_msg += f"\n{traceback.format_exc()}"
        return JsonResponse({"error": error_msg}, status=500)


@login_required
def update_transaction_category(request: HttpRequest, transaction_id: int) -> JsonResponse:
    """API endpoint pour mettre à jour la catégorie d'une transaction"""
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    try:
        from .models import Category
        
        transaction = Transaction.objects.get(id=transaction_id, account__owner=request.user)
        
        data = json.loads(request.body)
        category_id = data.get("category_id")
        
        if category_id:
            category = Category.objects.get(id=category_id)
            transaction.category = category
            category_name = category.name
        else:
            transaction.category = None
            category_name = "Sans catégorie"
        
        transaction.save(update_fields=["category"])
        
        return JsonResponse({
            "success": True,
            "category_id": category_id,
            "category_name": category_name,
            "message": f"Catégorie mise à jour: {category_name}"
        })
        
    except Transaction.DoesNotExist:
        return JsonResponse({"error": "Transaction introuvable"}, status=404)
    except Category.DoesNotExist:
        return JsonResponse({"error": "Catégorie introuvable"}, status=404)
    except Exception as e:
        import traceback
        error_msg = str(e)
        if settings.DEBUG:
            error_msg += f"\n{traceback.format_exc()}"
        return JsonResponse({"error": error_msg}, status=500)


# ============================================================================
# Vues pour la gestion des connexions bancaires (Story 1.8)
# ============================================================================


@login_required
def bank_connections_list(request: HttpRequest) -> HttpResponse:
    """Affiche la liste des connexions bancaires de l'utilisateur."""
    connections = BankConnection.objects.filter(owner=request.user).select_related("owner").order_by("-created_at")

    # Récupérer les derniers SyncLog pour chaque connexion
    for connection in connections:
        last_success_log = (
            SyncLog.objects.filter(bank_connection=connection, status=SyncLog.Status.SUCCESS)
            .order_by("-completed_at")
            .first()
        )

        connection.last_success_log = last_success_log
        connection.transactions_count = last_success_log.transactions_count if last_success_log else 0

        # Trouver TOUS les comptes liés à cette connexion
        connection.linked_accounts = list(Account.objects.filter(bank_connection=connection).order_by("name"))

    return render(request, "finance/bank_connections.html", {"connections": connections})


@login_required
def bank_connection_create(request: HttpRequest) -> HttpResponse:
    """Crée une nouvelle connexion bancaire."""
    if request.method == "POST":
        form = BankConnectionForm(user=request.user, data=request.POST)
        if form.is_valid():
            try:
                connection = form.save()
                messages.success(request, f"Connexion bancaire '{connection.account_name}' créée avec succès.")
                return redirect("bank_connections_list")
            except Exception as e:
                messages.error(request, f"Erreur lors de la création de la connexion : {str(e)}")
    else:
        form = BankConnectionForm(user=request.user)

    return render(request, "finance/bank_connection_form.html", {"form": form, "title": "Nouvelle connexion bancaire"})


@login_required
def bank_connection_update(request: HttpRequest, connection_id: int) -> HttpResponse:
    """Modifie une connexion bancaire existante."""
    try:
        connection = BankConnection.objects.get(id=connection_id, owner=request.user)
    except BankConnection.DoesNotExist:
        messages.error(request, "Connexion bancaire introuvable.")
        return redirect("bank_connections_list")

    if request.method == "POST":
        form = BankConnectionForm(user=request.user, data=request.POST, instance=connection)
        if form.is_valid():
            try:
                connection = form.save()
                messages.success(request, f"Connexion bancaire '{connection.account_name}' mise à jour avec succès.")
                return redirect("bank_connections_list")
            except Exception as e:
                messages.error(request, f"Erreur lors de la mise à jour de la connexion : {str(e)}")
    else:
        form = BankConnectionForm(user=request.user, instance=connection)

    return render(
        request,
        "finance/bank_connection_form.html",
        {"form": form, "title": f"Modifier la connexion '{connection.account_name}'", "connection": connection},
    )


@login_required
def bank_connection_delete(request: HttpRequest, connection_id: int) -> HttpResponse:
    """Supprime une connexion bancaire."""
    try:
        connection = BankConnection.objects.get(id=connection_id, owner=request.user)
    except BankConnection.DoesNotExist:
        messages.error(request, "Connexion bancaire introuvable.")
        return redirect("bank_connections_list")

    if request.method == "POST":
        account_name = connection.account_name

        # Mettre à jour les comptes associés (retirer bank_connection)
        Account.objects.filter(bank_connection=connection).update(bank_connection=None)

        # Supprimer la connexion (les SyncLog seront supprimés automatiquement via CASCADE)
        connection.delete()

        messages.success(request, f"Connexion bancaire '{account_name}' supprimée avec succès.")
        return redirect("bank_connections_list")

    # GET : afficher la confirmation
    return render(request, "finance/bank_connection_delete.html", {"connection": connection})


@login_required
def bank_connection_sync(request: HttpRequest, connection_id: int) -> HttpResponse:
    """Synchronise manuellement une connexion bancaire."""
    try:
        connection = BankConnection.objects.get(id=connection_id, owner=request.user)
    except BankConnection.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Connexion bancaire introuvable."}, status=404)
        messages.error(request, "Connexion bancaire introuvable.")
        return redirect("bank_connections_list")

    # Trouver le compte associé
    account = Account.objects.filter(bank_connection=connection).first()
    if not account:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Aucun compte associé à cette connexion."}, status=400)
        messages.error(request, "Aucun compte associé à cette connexion.")
        return redirect("bank_connections_list")

    if (
        settings.TR_BRIDGE_ENABLED
        and connection.provider == BankConnection.Provider.TRADE_REPUBLIC
        and account.type == Account.AccountType.BROKER
    ):
        try:
            snapshot = sync_bridge_snapshot_with_auth_handling(account=account)
            if (
                snapshot.auth_status
                == TradeRepublicValuationSnapshot.AuthStatus.NEEDS_MANUAL_AUTH
            ):
                messages.warning(
                    request,
                    "Authentification Trade Republic requise avant de synchroniser la valorisation bridge.",
                )
            else:
                messages.success(
                    request,
                    f"Snapshot bridge créé pour '{account.name}' ({snapshot.source_timestamp.strftime('%d/%m/%Y %H:%M')}).",
                )
        except TradeRepublicBridgeError as e:
            messages.error(request, f"Erreur bridge lors de la synchronisation : {str(e)}")
    else:
        # Appeler la tâche Celery de manière asynchrone
        from finance.tasks import sync_bank_account

        try:
            task_result = sync_bank_account.delay(account.id, sync_type=SyncLog.SyncType.MANUAL)
            messages.success(request, f"Synchronisation du compte '{account.name}' démarrée.")
        except Exception as e:
            messages.error(request, f"Erreur lors du démarrage de la synchronisation : {str(e)}")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": "Synchronisation démarrée."})

    return redirect("bank_connections_list")


@login_required
def bank_connection_2fa(request: HttpRequest, connection_id: int) -> HttpResponse:
    """Gère l'authentification 2FA pour une connexion bancaire."""
    try:
        connection = BankConnection.objects.get(id=connection_id, owner=request.user)
    except BankConnection.DoesNotExist:
        messages.error(request, "Connexion bancaire introuvable.")
        return redirect("bank_connections_list")

    if request.method == "POST":
        two_fa_code = request.POST.get("two_fa_code", "").strip()
        resend = request.POST.get("resend") == "true"

        if resend:
            # Renvoyer le code 2FA (uniquement pour Trade Republic)
            if connection.provider != BankConnection.Provider.TRADE_REPUBLIC:
                messages.error(request, "Le renvoi de code 2FA n'est disponible que pour Trade Republic.")
                return redirect("bank_connection_2fa", connection_id=connection_id)
            messages.warning(
                request,
                "Le flux 2FA legacy est désactivé. Relancez la synchronisation depuis le panel Connexions Bancaires.",
            )
            return redirect("bank_connection_2fa", connection_id=connection_id)

        if not two_fa_code:
            messages.error(request, "Veuillez saisir le code 2FA.")
            return redirect("bank_connection_2fa", connection_id=connection_id)

        # Mettre à jour les credentials avec le code 2FA et re-synchroniser
        try:
            from finance.services.encryption_service import EncryptionService
            from finance.services.sync_service import SyncService

            # Déchiffrer les credentials existants
            credentials = EncryptionService.decrypt_credentials(connection.encrypted_credentials)
            credentials["2fa_code"] = two_fa_code

            # Chiffrer à nouveau avec le code 2FA
            connection.encrypted_credentials = EncryptionService.encrypt_credentials(credentials)
            connection.save()

            # Trouver le compte associé et synchroniser
            account = Account.objects.filter(bank_connection=connection).first()
            if account:
                from finance.tasks import sync_bank_account

                sync_bank_account.delay(account.id, sync_type=SyncLog.SyncType.MANUAL)
                messages.success(request, "Code 2FA validé. Synchronisation démarrée.")
            else:
                messages.error(request, "Aucun compte associé à cette connexion.")
        except Exception as e:
            messages.error(request, f"Erreur lors de la validation du code 2FA : {str(e)}")

        return redirect("bank_connections_list")

    # GET : afficher le formulaire 2FA
    return render(request, "finance/bank_connection_2fa.html", {"connection": connection})


# ============================================================================
# API Endpoints pour la gestion des comptes (Story 1.9)
# ============================================================================


@login_required
def account_sync_api(request: HttpRequest, account_id: int) -> JsonResponse:
    """API endpoint pour synchroniser manuellement un compte."""
    try:
        account = Account.objects.select_related("bank_connection").get(id=account_id, owner=request.user)
    except Account.DoesNotExist:
        return JsonResponse({"success": False, "error": "Compte introuvable."}, status=404)

    if not account.bank_connection:
        return JsonResponse(
            {"success": False, "error": "Ce compte n'a pas de connexion bancaire."}, status=400
        )

    if (
        settings.TR_BRIDGE_ENABLED
        and account.provider == "traderepublic"
        and account.type == Account.AccountType.BROKER
    ):
        bank_connection = account.bank_connection
        sync_log = SyncLog.objects.create(
            bank_connection=bank_connection,
            sync_type=SyncLog.SyncType.MANUAL,
            status=SyncLog.Status.STARTED,
        )
        bank_connection.sync_status = BankConnection.SyncStatus.SYNCING
        bank_connection.save(update_fields=["sync_status", "updated_at"])
        try:
            payload = {}
            if request.body:
                try:
                    payload = json.loads(request.body)
                except json.JSONDecodeError:
                    payload = {}
            device_pin = (payload.get("two_fa_code") or "").strip() or None
            logger = logging.getLogger(__name__)
            logger.info(
                "tr_bridge_sync_payload source=account_sync_api account_id=%s has_two_fa=%s two_fa_len=%s",
                account.id,
                bool(device_pin),
                len(device_pin) if device_pin else 0,
            )
            logger.info("tr_bridge_sync_requested source=account_sync_api account_id=%s", account.id)
            snapshot = sync_bridge_snapshot_with_auth_handling(account=account, device_pin=device_pin)
            if (
                snapshot.auth_status
                == TradeRepublicValuationSnapshot.AuthStatus.NEEDS_MANUAL_AUTH
            ):
                logger.warning("tr_bridge_sync_auth_required source=account_sync_api account_id=%s", account.id)
                # Demande 2FA : ce n'est pas une erreur métier.
                # Pour éviter une double ligne "sync" (1ère requête 409 auth_required,
                # puis 2ème requête après saisie du code), on supprime le SyncLog
                # créé pour cette tentative.
                try:
                    sync_log.delete()
                except Exception:
                    sync_log.status = SyncLog.Status.STARTED
                    sync_log.error_message = ""
                    sync_log.transactions_count = 0
                    sync_log.completed_at = timezone.now()
                    sync_log.save(
                        update_fields=["status", "error_message", "transactions_count", "completed_at"]
                    )
                bank_connection.sync_status = BankConnection.SyncStatus.PENDING
                bank_connection.save(update_fields=["sync_status", "updated_at"])
                return JsonResponse(
                    {
                        "success": False,
                        "auth_required": True,
                        "error": "Authentification Trade Republic requise",
                        "auth_status": get_bridge_auth_status_safe(),
                    },
                    status=409,
                )
            bridge_tx_count = 0
            if isinstance(snapshot.raw, dict):
                try:
                    bridge_tx_count = int(snapshot.raw.get("total_items") or 0)
                except (TypeError, ValueError):
                    bridge_tx_count = 0

            sync_log.status = SyncLog.Status.SUCCESS
            sync_log.error_message = ""
            sync_log.transactions_count = bridge_tx_count
            sync_log.completed_at = timezone.now()
            sync_log.save(
                update_fields=["status", "error_message", "transactions_count", "completed_at"]
            )
            bank_connection.sync_status = BankConnection.SyncStatus.SUCCESS
            bank_connection.last_sync_at = timezone.now()
            bank_connection.save(update_fields=["sync_status", "last_sync_at", "updated_at"])
            return JsonResponse(
                {
                    "success": True,
                    "message": "Synchronisation Trade Republic bridge terminée.",
                    "snapshot_id": snapshot.id,
                    "snapshot_date": snapshot.source_timestamp.isoformat(),
                }
            )
        except TradeRepublicBridgeError as exc:
            logger = logging.getLogger(__name__)
            logger.warning(
                "tr_bridge_sync_failed source=account_sync_api account_id=%s error=%s",
                account.id,
                exc,
            )
            sync_log.status = SyncLog.Status.ERROR
            sync_log.error_message = f"Bridge Trade Republic indisponible: {exc}"
            sync_log.transactions_count = 0
            sync_log.completed_at = timezone.now()
            sync_log.save(
                update_fields=["status", "error_message", "transactions_count", "completed_at"]
            )
            bank_connection.sync_status = BankConnection.SyncStatus.ERROR
            bank_connection.save(update_fields=["sync_status", "updated_at"])
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Bridge Trade Republic indisponible: {exc}",
                },
                status=400,
            )
        except Exception as exc:
            sync_log.status = SyncLog.Status.ERROR
            sync_log.error_message = f"Erreur inattendue sync bridge: {exc}"
            sync_log.transactions_count = 0
            sync_log.completed_at = timezone.now()
            sync_log.save(
                update_fields=["status", "error_message", "transactions_count", "completed_at"]
            )
            bank_connection.sync_status = BankConnection.SyncStatus.ERROR
            bank_connection.save(update_fields=["sync_status", "updated_at"])
            raise

    from finance.tasks import sync_bank_account

    try:
        task_result = sync_bank_account.delay(account.id, sync_type=SyncLog.SyncType.MANUAL)
        return JsonResponse(
            {
                "success": True,
                "message": "Synchronisation démarrée.",
                "task_id": str(task_result.id) if hasattr(task_result, "id") else None,
            }
        )
    except Exception as e:
        return JsonResponse(
            {"success": False, "error": f"Erreur lors du démarrage de la synchronisation : {str(e)}"},
            status=500,
        )


# ============================================================================
# Vues pour le monitoring et logging des synchronisations (Story 1.10)
# ============================================================================


@login_required
def sync_logs_list(request: HttpRequest) -> HttpResponse:
    """
    Affiche la liste paginée des logs de synchronisation avec filtres et statistiques.
    
    Filtres disponibles :
    - connection_id : Filtrer par connexion bancaire
    - status : Filtrer par statut (started, success, error)
    - sync_type : Filtrer par type (manual, automatic)
    - date_from : Date de début (format YYYY-MM-DD)
    - date_to : Date de fin (format YYYY-MM-DD)
    """
    # Récupérer tous les logs de l'utilisateur avec optimisation DB
    logs_query = SyncLog.objects.filter(
        bank_connection__owner=request.user
    ).select_related("bank_connection").order_by("-started_at")
    
    # Récupérer toutes les connexions de l'utilisateur pour le filtre
    connections = BankConnection.objects.filter(owner=request.user).order_by("account_name")
    
    # Appliquer les filtres depuis les query parameters
    connection_id = request.GET.get("connection_id")
    if connection_id:
        try:
            connection_id_int = int(connection_id)
            logs_query = logs_query.filter(bank_connection_id=connection_id_int)
        except (ValueError, TypeError):
            pass
    
    status_filter = request.GET.get("status")
    if status_filter and status_filter in [s[0] for s in SyncLog.Status.choices]:
        logs_query = logs_query.filter(status=status_filter)
    
    sync_type_filter = request.GET.get("sync_type")
    if sync_type_filter and sync_type_filter in [t[0] for t in SyncLog.SyncType.choices]:
        logs_query = logs_query.filter(sync_type=sync_type_filter)
    
    date_from = request.GET.get("date_from")
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            if settings.USE_TZ:
                date_from_dt = timezone.make_aware(date_from_dt, timezone.get_current_timezone())
            logs_query = logs_query.filter(started_at__gte=date_from_dt)
        except ValueError:
            pass
    
    date_to = request.GET.get("date_to")
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
            # Ajouter 23h59:59 pour inclure toute la journée
            date_to_dt = date_to_dt.replace(hour=23, minute=59, second=59)
            if settings.USE_TZ:
                date_to_dt = timezone.make_aware(date_to_dt, timezone.get_current_timezone())
            logs_query = logs_query.filter(started_at__lte=date_to_dt)
        except ValueError:
            pass
    
    # Calculer les statistiques AVANT la pagination (sur tous les logs filtrés)
    stats = {}
    logs_for_stats = logs_query
    
    # Taux de succès global
    total_logs = logs_for_stats.count()
    completed_logs = logs_for_stats.exclude(completed_at__isnull=True)
    success_logs = completed_logs.filter(status=SyncLog.Status.SUCCESS)
    stats["total_syncs"] = total_logs
    stats["success_count"] = success_logs.count()
    stats["error_count"] = completed_logs.filter(status=SyncLog.Status.ERROR).count()
    stats["in_progress_count"] = logs_for_stats.filter(status=SyncLog.Status.STARTED, completed_at__isnull=True).count()
    
    if completed_logs.count() > 0:
        stats["success_rate"] = round((success_logs.count() / completed_logs.count()) * 100, 1)
    else:
        stats["success_rate"] = 0.0
    
    # Taux de succès par provider
    provider_stats = {}
    for provider_code, provider_name in BankConnection.Provider.choices:
        provider_logs = logs_for_stats.filter(bank_connection__provider=provider_code)
        provider_completed = provider_logs.exclude(completed_at__isnull=True)
        provider_success = provider_completed.filter(status=SyncLog.Status.SUCCESS)
        if provider_completed.count() > 0:
            provider_stats[provider_code] = {
                "name": provider_name,
                "total": provider_logs.count(),
                "success_rate": round((provider_success.count() / provider_completed.count()) * 100, 1),
            }
        elif provider_logs.count() > 0:
            provider_stats[provider_code] = {
                "name": provider_name,
                "total": provider_logs.count(),
                "success_rate": 0.0,
            }
    stats["provider_stats"] = provider_stats
    
    # Temps moyen de synchronisation (en secondes)
    completed_with_duration = completed_logs.exclude(completed_at__isnull=True).exclude(started_at__isnull=True)
    if completed_with_duration.exists():
        durations = []
        for log in completed_with_duration:
            if log.completed_at and log.started_at:
                duration = (log.completed_at - log.started_at).total_seconds()
                durations.append(duration)
        if durations:
            stats["avg_duration_seconds"] = round(sum(durations) / len(durations), 1)
        else:
            stats["avg_duration_seconds"] = 0.0
    else:
        stats["avg_duration_seconds"] = 0.0
    
    # Nombre total de transactions synchronisées
    stats["total_transactions"] = logs_for_stats.aggregate(
        total=Sum("transactions_count")
    )["total"] or 0
    
    # Détecter les connexions avec échecs répétés
    failure_threshold = int(os.getenv("SYNC_FAILURE_ALERT_THRESHOLD", "3"))
    alerts = []
    for connection in connections:
        # Récupérer les logs récents de cette connexion (30 derniers jours)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        connection_logs = logs_query.filter(
            bank_connection=connection,
            started_at__gte=thirty_days_ago
        ).order_by("-started_at")
        
        # Compter les échecs consécutifs
        consecutive_failures = 0
        for log in connection_logs:
            if log.status == SyncLog.Status.ERROR:
                consecutive_failures += 1
            elif log.status == SyncLog.Status.SUCCESS:
                break  # Arrêter au premier succès
        
        if consecutive_failures >= failure_threshold:
            alerts.append({
                "connection": connection,
                "connection_id": connection.id,
                "failures": consecutive_failures,
            })
    
    # Pagination (20 logs par page)
    paginator = Paginator(logs_query, 20)
    page_number = request.GET.get("page", 1)
    
    try:
        page = paginator.get_page(page_number)
    except:
        page = paginator.get_page(1)
    
    # Calculer la durée pour chaque log de la page
    for log in page.object_list:
        if log.completed_at and log.started_at:
            duration = log.completed_at - log.started_at
            log.duration_seconds = duration.total_seconds()
            log.duration_formatted = _format_duration(duration)
        else:
            log.duration_seconds = None
            log.duration_formatted = "En cours"
    
    # Construire l'URL de base pour les filtres (pour la pagination)
    filter_params = {}
    if connection_id:
        filter_params["connection_id"] = connection_id
    if status_filter:
        filter_params["status"] = status_filter
    if sync_type_filter:
        filter_params["sync_type"] = sync_type_filter
    if date_from:
        filter_params["date_from"] = date_from
    if date_to:
        filter_params["date_to"] = date_to
    
    return render(request, "finance/sync_logs.html", {
        "logs": page,
        "connections": connections,
        "stats": stats,
        "alerts": alerts,
        "filters": {
            "connection_id": connection_id,
            "status": status_filter,
            "sync_type": sync_type_filter,
            "date_from": date_from,
            "date_to": date_to,
        },
        "filter_params": filter_params,
    })


def _format_duration(duration: timedelta) -> str:
    """Formate une durée en format lisible (ex: "2h 15m 30s" ou "45s")."""
    total_seconds = int(duration.total_seconds())
    
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        if seconds > 0:
            return f"{minutes}m {seconds}s"
        return f"{minutes}m"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        parts = [f"{hours}h"]
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0:
            parts.append(f"{seconds}s")
        return " ".join(parts)


@login_required
def sync_log_detail(request: HttpRequest, log_id: int) -> HttpResponse:
    """Affiche le détail d'un log de synchronisation avec message d'erreur formaté."""
    log = get_object_or_404(
        SyncLog.objects.select_related("bank_connection"),
        id=log_id,
        bank_connection__owner=request.user,
    )
    
    # Calculer la durée
    if log.completed_at and log.started_at:
        duration = log.completed_at - log.started_at
        log.duration_seconds = duration.total_seconds()
        log.duration_formatted = _format_duration(duration)
    else:
        log.duration_seconds = None
        log.duration_formatted = "En cours"
    
    # Formater le message d'erreur pour améliorer la lisibilité
    error_message_formatted = None
    if log.error_message:
        # Détecter si c'est une stack trace (contient "Traceback" ou "File")
        if "Traceback" in log.error_message or "File \"" in log.error_message:
            # Formater comme une stack trace (préserver les sauts de ligne)
            error_message_formatted = log.error_message
        else:
            # Message d'erreur simple
            error_message_formatted = log.error_message
    
    return render(request, "finance/sync_log_detail.html", {
        "log": log,
        "error_message_formatted": error_message_formatted,
    })


@login_required
def sync_logs_export(request: HttpRequest) -> HttpResponse:
    """
    Exporte les logs de synchronisation en CSV avec les mêmes filtres que la vue de liste.
    """
    # Appliquer les mêmes filtres que sync_logs_list
    logs_query = SyncLog.objects.filter(
        bank_connection__owner=request.user
    ).select_related("bank_connection").order_by("-started_at")
    
    # Appliquer les filtres depuis les query parameters
    connection_id = request.GET.get("connection_id")
    if connection_id:
        try:
            connection_id_int = int(connection_id)
            logs_query = logs_query.filter(bank_connection_id=connection_id_int)
        except (ValueError, TypeError):
            pass
    
    status_filter = request.GET.get("status")
    if status_filter and status_filter in [s[0] for s in SyncLog.Status.choices]:
        logs_query = logs_query.filter(status=status_filter)
    
    sync_type_filter = request.GET.get("sync_type")
    if sync_type_filter and sync_type_filter in [t[0] for t in SyncLog.SyncType.choices]:
        logs_query = logs_query.filter(sync_type=sync_type_filter)
    
    date_from = request.GET.get("date_from")
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
            if settings.USE_TZ:
                date_from_dt = timezone.make_aware(date_from_dt, timezone.get_current_timezone())
            logs_query = logs_query.filter(started_at__gte=date_from_dt)
        except ValueError:
            pass
    
    date_to = request.GET.get("date_to")
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
            date_to_dt = date_to_dt.replace(hour=23, minute=59, second=59)
            if settings.USE_TZ:
                date_to_dt = timezone.make_aware(date_to_dt, timezone.get_current_timezone())
            logs_query = logs_query.filter(started_at__lte=date_to_dt)
        except ValueError:
            pass
    
    # Générer le CSV
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    response["Content-Disposition"] = f'attachment; filename="sync_logs_{timestamp}.csv"'
    
    # Ajouter BOM pour Excel UTF-8
    response.write("\ufeff")
    
    writer = csv.writer(response, delimiter=";")
    
    # En-têtes
    writer.writerow([
        "Date début",
        "Date fin",
        "Connexion",
        "Provider",
        "Type",
        "Statut",
        "Durée (secondes)",
        "Transactions",
        "Message d'erreur",
    ])
    
    # Données
    for log in logs_query:
        started_at_str = log.started_at.strftime("%Y-%m-%d %H:%M:%S") if log.started_at else ""
        completed_at_str = log.completed_at.strftime("%Y-%m-%d %H:%M:%S") if log.completed_at else ""
        
        duration_seconds = ""
        if log.completed_at and log.started_at:
            duration_seconds = str(int((log.completed_at - log.started_at).total_seconds()))
        
        writer.writerow([
            started_at_str,
            completed_at_str,
            log.bank_connection.account_name,
            log.bank_connection.get_provider_display(),
            log.get_sync_type_display(),
            log.get_status_display(),
            duration_seconds,
            log.transactions_count,
            log.error_message.replace("\n", " ").replace("\r", " ") if log.error_message else "",
        ])
    
    return response


# ============================================================================
# Gestion des invitations utilisateur
# ============================================================================


def _superuser_required(view_func):
    """Décorateur : accès réservé aux superusers."""
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if not request.user.is_superuser:
            messages.error(request, "Accès réservé aux administrateurs.")
            return redirect("/")
        return view_func(request, *args, **kwargs)

    return _wrapped


@_superuser_required
def invitation_list(request: HttpRequest) -> HttpResponse:
    """Liste des invitations (superuser uniquement)."""
    invitations = InvitationToken.objects.select_related("created_by", "used_by").all()
    return render(request, "finance/invitations.html", {"invitations": invitations})


@_superuser_required
def invitation_create(request: HttpRequest) -> HttpResponse:
    """Crée un nouveau token d'invitation (superuser uniquement)."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        days = int(request.POST.get("expires_days", 7) or 7)
        expires_at = timezone.now() + timedelta(days=days)
        invitation = InvitationToken.objects.create(
            created_by=request.user,
            email=email,
            expires_at=expires_at,
        )
        messages.success(request, f"Invitation créée. Lien valide {days} jours.")
        return redirect("invitation_list")
    return render(request, "finance/invitation_create.html")


@_superuser_required
def invitation_delete(request: HttpRequest, token: str) -> HttpResponse:
    """Supprime une invitation non utilisée (superuser uniquement)."""
    inv = get_object_or_404(InvitationToken, token=token, created_by=request.user)
    if request.method == "POST":
        if inv.is_used:
            messages.error(request, "Cette invitation a déjà été utilisée.")
        else:
            inv.delete()
            messages.success(request, "Invitation supprimée.")
    return redirect("invitation_list")


def register_with_invitation(request: HttpRequest, token: str) -> HttpResponse:
    """Page d'inscription via token d'invitation."""
    from django.contrib.auth import login, get_user_model
    from django.contrib.auth.forms import SetPasswordForm

    User = get_user_model()

    invitation = get_object_or_404(InvitationToken, token=token)
    if not invitation.is_valid:
        return render(request, "finance/invitation_invalid.html", {"invitation": invitation})

    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")

        if not username:
            error = "Le nom d'utilisateur est requis."
        elif User.objects.filter(username=username).exists():
            error = "Ce nom d'utilisateur est déjà pris."
        elif len(password1) < 8:
            error = "Le mot de passe doit contenir au moins 8 caractères."
        elif password1 != password2:
            error = "Les mots de passe ne correspondent pas."
        else:
            user = User.objects.create_user(
                username=username,
                password=password1,
                email=invitation.email or "",
            )
            invitation.used_at = timezone.now()
            invitation.used_by = user
            invitation.save(update_fields=["used_at", "used_by"])
            login(request, user)
            messages.success(request, f"Bienvenue, {username} ! Votre compte a été créé.")
            return redirect("/")

    return render(request, "finance/register.html", {
        "invitation": invitation,
        "error": error,
    })
