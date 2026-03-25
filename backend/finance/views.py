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
from django.core.files.uploadedfile import UploadedFile
from django.core.paginator import Paginator
from tempfile import NamedTemporaryFile
from pathlib import Path
import os
import csv
import logging

from django.conf import settings
from django.utils import timezone

from .models import Transaction, Account, InvestmentHolding, Category, BankConnection, SyncLog
from .forms import AccountForm, TransactionForm, ImportStatementForm, BankConnectionForm
from .importers.loader import import_bank_statement_from_csv, import_traderepublic_from_csv
from .importers.traderepublic_scraper import TradeRepublicScraper
from django.http import JsonResponse
from django.utils.safestring import mark_safe
import json
import PyPDF2
import openai
from decimal import Decimal


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
    # Récupérer les paramètres de date
    month_param = request.GET.get("month")  # Format: YYYY-MM (sélection rapide par mois)
    start_date_param = request.GET.get("start_date")  # Format: YYYY-MM-DD (période personnalisée)
    end_date_param = request.GET.get("end_date")  # Format: YYYY-MM-DD (période personnalisée)
    
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
            # En cas d'erreur, utiliser le mois actuel
            today = timezone.now().date()
            start_date = (today.replace(day=1) - relativedelta(months=1)).replace(day=24)
            end_date = today.replace(day=24) + relativedelta(days=1)
    
    # Sinon, si start_date et end_date sont fournis, utiliser la période personnalisée
    elif start_date_param and end_date_param:
        try:
            start_date = datetime.strptime(start_date_param, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_param, "%Y-%m-%d").date()
            # Ajouter 1 jour pour inclure la journée complète
            end_date = end_date + relativedelta(days=1)
        except ValueError:
            # Par défaut : mois actuel (du 24 du mois dernier au 24 de ce mois)
            today = timezone.now().date()
            start_date = (today.replace(day=1) - relativedelta(months=1)).replace(day=24)
            end_date = today.replace(day=24) + relativedelta(days=1)
    
    # Par défaut : mois actuel (du 24 du mois dernier au 24 de ce mois)
    else:
        today = timezone.now().date()
        start_date = (today.replace(day=1) - relativedelta(months=1)).replace(day=24)
        end_date = today.replace(day=24) + relativedelta(days=1)
    
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
    checking_accounts = Account.objects.filter(
        owner=request.user, 
        type=Account.AccountType.CHECKING,
        include_in_dashboard=True
    )
    checking_balance = 0
    checking_balance_at_start = 0
    checking_accounts_list = []
    
    for account in checking_accounts:
        # Trouver la transaction la plus récente jusqu'à la date de fin
        latest_tx = Transaction.objects.filter(
            account=account,
            posted_at__lte=end,
            account_balance__isnull=False
        ).order_by("-posted_at", "-account_balance", "-id").first()
        
        if latest_tx and latest_tx.account_balance is not None:
            account_balance = float(latest_tx.account_balance)
            checking_balance += account_balance
        else:
            account_initial = float(account.initial_balance or 0)
            account_sum = Transaction.objects.filter(
                account=account, posted_at__lte=end
            ).aggregate(total=Sum("amount"))["total"] or 0
            account_balance = account_initial + float(account_sum)
            checking_balance += account_balance
        
        checking_accounts_list.append({
            "name": account.name,
            "provider": account.provider or "generic",
            "balance": account_balance,
        })
        
        # Solde au début de la période
        latest_tx_before = Transaction.objects.filter(
            account=account,
            posted_at__lt=start,
            account_balance__isnull=False
        ).order_by("-posted_at", "-account_balance", "-id").first()
        
        if latest_tx_before and latest_tx_before.account_balance is not None:
            checking_balance_at_start += float(latest_tx_before.account_balance)
        else:
            account_initial = float(account.initial_balance or 0)
            account_sum_before = Transaction.objects.filter(
                account=account, posted_at__lt=start
            ).aggregate(total=Sum("amount"))["total"] or 0
            checking_balance_at_start += account_initial + float(account_sum_before)
    
    # ÉPARGNE (livrets)
    savings_accounts = Account.objects.filter(
        owner=request.user,
        type=Account.AccountType.SAVINGS,
        include_in_dashboard=True
    )
    savings_balance = 0
    savings_accounts_list = []
    
    for account in savings_accounts:
        latest_tx = Transaction.objects.filter(
            account=account,
            posted_at__lte=end,
            account_balance__isnull=False
        ).order_by("-posted_at", "-account_balance", "-id").first()
        
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
        include_in_dashboard=True
    ).filter(
        Q(provider="traderepublic") | Q(type=Account.AccountType.BROKER)
    )
    
    total_invested = 0  # Montant total investi
    current_valuation = 0  # Valorisation actuelle (à la date de fin sélectionnée)
    investment_accounts_list_detailed = []
    global_latest_valuation_date = None  # Date de valorisation la plus récente parmi tous les comptes
    
    for account in investment_accounts:
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
        month_balance = month_income - month_expenses
        
        monthly_stats.append({
            "label": month_start.strftime("%b %Y"),
            "income": month_income,
            "expenses": month_expenses,
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
    savings_rate = (income_float - expenses_float) / income_float * 100 if income_float > 0 else 0
    
    # Projection pour le mois suivant (basée sur la moyenne des 3 derniers mois)
    if len(monthly_stats) >= 3:
        last_3_months = monthly_stats[-3:]
        projected_income = sum(m["income"] for m in last_3_months) / 3
        projected_expenses = sum(m["expenses"] for m in last_3_months) / 3
        projected_balance = projected_income - projected_expenses
    else:
        projected_income = income_float
        projected_expenses = expenses_float
        projected_balance = income_float - expenses_float

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
        "balance": float(real_balance),  # Solde courant uniquement
        "period_balance": float(period_balance),
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
        "end_date": (end_date - relativedelta(days=1)).strftime("%Y-%m-%d"),  # Retirer le jour ajouté pour l'affichage
        "selected_month": month_param,  # Pour le sélecteur de mois
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
    
    # Trier par date (du plus récent au plus ancien) pour l'affichage
    # Si les dates sont identiques, utiliser le solde brut (account_balance) pour déterminer l'ordre logique
    # Les transactions avec un solde plus petit (arrivées avant) viennent en premier
    txs_query = txs_query.order_by("-posted_at", "account_balance", "-id")
    
    # Pagination : 100 transactions par page
    paginator = Paginator(txs_query, 100)
    page_number = request.GET.get("page", 1)
    
    try:
        page = paginator.get_page(page_number)
    except:
        page = paginator.get_page(1)
    
    # Pour calculer le solde, on doit récupérer toutes les transactions jusqu'à la première de la page
    # On récupère les transactions dans l'ordre chronologique (du plus ancien au plus récent)
    # jusqu'à la première transaction de la page actuelle
    # IMPORTANT: Le solde est calculé par compte séparément - chaque compte a son propre solde
    account_balances = {}  # Dictionnaire pour stocker le solde actuel de chaque compte (clé = account_id)
    
    # Pour la première page, initialiser le solde de chaque compte présent sur la page
    if page.number == 1:
        # Récupérer les IDs des comptes présents sur la page actuelle
        accounts_on_page = set(tx.account_id for tx in page.object_list)
        
        # Initialiser le solde initial de chaque compte présent sur la page
        for account_id_on_page in accounts_on_page:
            account = Account.objects.get(id=account_id_on_page)
            
            # Pour Hello Bank et Trade Republic, calculer depuis le début (depuis la transaction la plus ancienne)
            if account.provider in ("hellobank", "traderepublic"):
                # Partir de initial_balance ou 0
                account_balances[account_id_on_page] = float(account.initial_balance or 0)
                
                # Calculer le solde pour toutes les transactions jusqu'à la première de la page
                # Récupérer toutes les transactions de ce compte dans l'ordre chronologique jusqu'à la première de la page
                first_tx_on_page = None
                for tx in page.object_list:
                    if tx.account_id == account_id_on_page:
                        if first_tx_on_page is None:
                            first_tx_on_page = tx
                        elif tx.posted_at < first_tx_on_page.posted_at or (
                            tx.posted_at == first_tx_on_page.posted_at and (
                                (tx.account_balance is not None and first_tx_on_page.account_balance is not None and tx.account_balance < first_tx_on_page.account_balance) or
                                (tx.account_balance is None and first_tx_on_page.account_balance is not None) or
                                (tx.account_balance == first_tx_on_page.account_balance and tx.id < first_tx_on_page.id) or
                                (tx.account_balance is None and first_tx_on_page.account_balance is None and tx.id < first_tx_on_page.id)
                            )
                        ):
                            first_tx_on_page = tx
                
                if first_tx_on_page:
                    # Récupérer toutes les transactions avant la première de la page
                    all_txs_before = Transaction.objects.filter(
                        account_id=account_id_on_page,
                        account__owner=request.user
                    ).order_by("posted_at", "account_balance", "id")
                    
                    for prev_tx in all_txs_before:
                        if prev_tx.posted_at < first_tx_on_page.posted_at or (
                            prev_tx.posted_at == first_tx_on_page.posted_at and (
                                (prev_tx.account_balance is not None and first_tx_on_page.account_balance is not None and prev_tx.account_balance < first_tx_on_page.account_balance) or
                                (prev_tx.account_balance is None and first_tx_on_page.account_balance is not None) or
                                (prev_tx.account_balance == first_tx_on_page.account_balance and prev_tx.id < first_tx_on_page.id) or
                                (prev_tx.account_balance is None and first_tx_on_page.account_balance is None and prev_tx.id < first_tx_on_page.id)
                            )
                        ):
                            account_balances[account_id_on_page] += float(prev_tx.amount)
            else:
                # Pour les autres comptes, utiliser initial_balance
                account_balances[account_id_on_page] = float(account.initial_balance or 0)
    
    # Si ce n'est pas la première page, calculer le solde jusqu'à la première transaction de la page
    # IMPORTANT: Pour chaque compte, on doit compter uniquement les transactions de ce compte
    # et non toutes les transactions (tous comptes confondus)
    elif page.has_other_pages() and page.number > 1:
        # Récupérer les IDs des comptes présents sur la page actuelle
        accounts_on_page = set(tx.account_id for tx in page.object_list)
        
        # Pour chaque compte présent sur la page, calculer le solde initial
        # en comptant uniquement les transactions de ce compte jusqu'à la première transaction de ce compte sur la page
        for account_id_on_page in accounts_on_page:
            # Récupérer toutes les transactions de ce compte dans l'ordre chronologique
            # Si les dates sont identiques, utiliser le solde brut (account_balance) pour déterminer l'ordre logique
            account_txs_query = Transaction.objects.filter(
                account__owner=request.user,
                account_id=account_id_on_page
            ).order_by("posted_at", "account_balance", "id")
            
            # Trouver la première transaction de ce compte sur la page actuelle dans l'ordre chronologique
            # (du plus ancien au plus récent, pas dans l'ordre d'affichage)
            # Si les dates sont identiques, utiliser le solde brut (account_balance) pour déterminer l'ordre logique
            first_tx_on_page_for_account = None
            for tx in page.object_list:
                if tx.account_id == account_id_on_page:
                    if first_tx_on_page_for_account is None:
                        first_tx_on_page_for_account = tx
                    else:
                        # Comparer par date, puis par solde brut, puis par ID
                        tx_balance = tx.account_balance if tx.account_balance is not None else float('inf')
                        first_balance = first_tx_on_page_for_account.account_balance if first_tx_on_page_for_account.account_balance is not None else float('inf')
                        
                        if tx.posted_at < first_tx_on_page_for_account.posted_at:
                            first_tx_on_page_for_account = tx
                        elif tx.posted_at == first_tx_on_page_for_account.posted_at:
                            # Si les dates sont identiques, utiliser le solde brut (plus petit = arrivée avant)
                            if tx_balance < first_balance or (
                                tx_balance == first_balance and tx.id < first_tx_on_page_for_account.id
                            ):
                                first_tx_on_page_for_account = tx
            
            if first_tx_on_page_for_account:
                # Compter toutes les transactions de ce compte jusqu'à (mais pas incluant) la première transaction de ce compte sur la page
                # On récupère toutes les transactions de ce compte avec une date antérieure ou égale à la première transaction
                # mais on exclut la première transaction elle-même
                # Si les dates sont identiques, utiliser le solde brut (account_balance) pour déterminer l'ordre logique
                first_balance = first_tx_on_page_for_account.account_balance
                if first_balance is not None:
                    # Si la première transaction a un solde brut, utiliser le solde brut dans la comparaison
                    txs_before_for_account = account_txs_query.filter(
                        Q(posted_at__lt=first_tx_on_page_for_account.posted_at) |
                        Q(posted_at=first_tx_on_page_for_account.posted_at, account_balance__lt=first_balance) |
                        Q(posted_at=first_tx_on_page_for_account.posted_at, account_balance=first_balance, id__lt=first_tx_on_page_for_account.id) |
                        Q(posted_at=first_tx_on_page_for_account.posted_at, account_balance__isnull=True, id__lt=first_tx_on_page_for_account.id)
                    )
                else:
                    # Si la première transaction n'a pas de solde brut, utiliser l'ID
                    txs_before_for_account = account_txs_query.filter(
                        Q(posted_at__lt=first_tx_on_page_for_account.posted_at) |
                        Q(posted_at=first_tx_on_page_for_account.posted_at, id__lt=first_tx_on_page_for_account.id)
                    )
                
                # Initialiser le solde du compte
                account = Account.objects.get(id=account_id_on_page)
                
                # Pour Hello Bank et Trade Republic, calculer depuis le début (depuis la transaction la plus ancienne)
                if account.provider in ("hellobank", "traderepublic"):
                    # Partir de initial_balance ou 0
                    account_balances[account_id_on_page] = float(account.initial_balance or 0)
                    
                    # Calculer le solde pour toutes les transactions jusqu'à la première de la page
                    for tx in txs_before_for_account:
                        account_balances[account_id_on_page] += float(tx.amount)
                else:
                    # Pour les autres comptes, utiliser initial_balance
                    account_balances[account_id_on_page] = float(account.initial_balance or 0)
                    
                    # Calculer le solde pour ce compte uniquement
                    for tx in txs_before_for_account:
                        account_balances[account_id_on_page] += float(tx.amount)
    
    # Récupérer les transactions de la page dans l'ordre chronologique pour calculer le solde
    # On ne peut pas réordonner page.object_list car c'est déjà une slice
    # On récupère donc les IDs des transactions de la page, puis on les récupère dans l'ordre chronologique
    # Si les dates sont identiques, utiliser le solde brut (account_balance) pour déterminer l'ordre logique
    page_tx_ids = [tx.id for tx in page.object_list]
    page_txs_chronological = Transaction.objects.filter(
        id__in=page_tx_ids
    ).select_related("account", "category").order_by("posted_at", "account_balance", "id")
    
    # Calculer le solde après chaque transaction de la page
    # IMPORTANT: Chaque transaction affiche le solde de son propre compte uniquement
    # On ne mélange pas les soldes de différents comptes (ex: Trade Republic + BoursoBank)
    # Si la transaction a un account_balance (solde brut du CSV), on l'utilise directement
    # Sinon, on calcule le solde en ajoutant le montant de la transaction
    transactions_with_balance = []
    for tx in page_txs_chronological:
        tx_account_id = tx.account_id
        
        # Si la transaction a un solde brut du CSV, l'utiliser directement
        if tx.account_balance is not None:
            balance_after = float(tx.account_balance)
        else:
            # Sinon, calculer le solde en ajoutant le montant de la transaction
            # Initialiser le solde du compte si c'est la première transaction de ce compte
            if tx_account_id not in account_balances:
                account = tx.account
                
                # Pour Hello Bank et Trade Republic, calculer depuis le début (depuis la transaction la plus ancienne)
                if account.provider in ("hellobank", "traderepublic"):
                    # Partir de initial_balance ou 0
                    account_balances[tx_account_id] = float(account.initial_balance or 0)
                    
                    # Calculer le solde pour toutes les transactions jusqu'à celle-ci
                    all_txs_before = Transaction.objects.filter(
                        account_id=tx_account_id,
                        account__owner=request.user
                    ).order_by("posted_at", "account_balance", "id")
                    
                    for prev_tx in all_txs_before:
                        if prev_tx.posted_at < tx.posted_at or (
                            prev_tx.posted_at == tx.posted_at and (
                                (prev_tx.account_balance is not None and tx.account_balance is not None and prev_tx.account_balance < tx.account_balance) or
                                (prev_tx.account_balance is None and tx.account_balance is not None) or
                                (prev_tx.account_balance == tx.account_balance and prev_tx.id < tx.id) or
                                (prev_tx.account_balance is None and tx.account_balance is None and prev_tx.id < tx.id)
                            )
                        ):
                            account_balances[tx_account_id] += float(prev_tx.amount)
                else:
                    # Pour les autres comptes, utiliser initial_balance
                    account_balances[tx_account_id] = float(account.initial_balance or 0)
            
            # Calculer le solde après cette transaction pour ce compte uniquement
            account_balances[tx_account_id] += float(tx.amount)
            balance_after = account_balances[tx_account_id]
        
        transactions_with_balance.append({
            "transaction": tx,
            "balance_after": balance_after,
        })
    
    # Inverser l'ordre pour afficher les plus récentes en premier
    transactions_with_balance.reverse()
    
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
    if request.method == "POST":
        # Gérer l'upload de fichier CSV
        csv_file = request.FILES.get("csv_file")
        account_name = request.POST.get("account_name", "").strip()
        profile = request.POST.get("profile", "generic").strip()
        account_type = request.POST.get("account_type", Account.AccountType.CHECKING)
        
        if not csv_file:
            messages.error(request, "Veuillez sélectionner un fichier CSV.")
        elif not account_name:
            messages.error(request, "Veuillez spécifier un nom de compte.")
        else:
            try:
                # Sauvegarder temporairement le fichier
                with NamedTemporaryFile(delete=False, suffix=".csv") as tmp_file:
                    for chunk in csv_file.chunks():
                        tmp_file.write(chunk)
                    tmp_path = tmp_file.name
                
                try:
                    # Importer selon le profil
                    if profile == "traderepublic":
                        from .importers.loader import import_traderepublic_from_csv
                        count = import_traderepublic_from_csv(
                            user=request.user,
                            csv_path=tmp_path,
                            account_name=account_name,
                            currency="EUR",
                        )
                    else:
                        from .importers.loader import import_bank_statement_from_csv
                        count = import_bank_statement_from_csv(
                            user=request.user,
                            csv_path=tmp_path,
                            account_name=account_name,
                            profile=profile,
                            account_type=account_type,
                        )
                    
                    messages.success(request, f"Import réussi : {count} transaction(s) importée(s) pour le compte '{account_name}'.")
                    return redirect("settings")
                finally:
                    # Nettoyer le fichier temporaire
                    import os
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
            
            except Exception as e:
                messages.error(request, f"Erreur lors de l'import : {str(e)}")
    
    # Récupérer les comptes d'investissement pour l'import PDF
    investment_accounts_list = Account.objects.filter(
        owner=request.user,
        type=Account.AccountType.BROKER
    ).order_by('name')
    
    return render(request, "finance/settings.html", {
        "investment_accounts_list": investment_accounts_list,
    })


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
def import_upload(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ImportStatementForm(request.POST, request.FILES)
        if form.is_valid():
            import_type = form.cleaned_data["import_type"]
            account_name = form.cleaned_data["account_name"]
            currency = form.cleaned_data.get("currency") or "EUR"
            uploaded: UploadedFile = form.cleaned_data["file"]

            tmp_path = None
            try:
                with NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                    for chunk in uploaded.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name

                if import_type == "traderepublic":
                    count = import_traderepublic_from_csv(
                        user=request.user,
                        csv_path=tmp_path,
                        account_name=account_name,
                        currency=currency,
                    )
                else:
                    profile = import_type
                    count = import_bank_statement_from_csv(
                        user=request.user,
                        csv_path=tmp_path,
                        account_name=account_name,
                        profile=profile,
                        account_type=Account.AccountType.CHECKING,
                    )
                messages.success(request, f"Import terminé: {count} lignes traitées.")
                return redirect("transactions")
            except Exception as exc:
                messages.error(request, f"Import impossible: {exc}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
    else:
        form = ImportStatementForm()
    return render(request, "finance/import_form.html", {"form": form})


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
def traderepublic_import(request: HttpRequest) -> HttpResponse:
    """Vue pour afficher le formulaire d'import Trade Republic."""
    return render(request, "finance/traderepublic_import.html")


@login_required
def traderepublic_initiate_login(request: HttpRequest) -> JsonResponse:
    """Initie la connexion Trade Republic et retourne le process_id."""
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    try:
        try:
            data = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Requête JSON invalide ou vide."}, status=400)
        phone_number = data.get("phone_number")
        pin = data.get("pin")
        account_name = data.get("account_name", "Trade Republic")
        currency = data.get("currency", "EUR")
        
        if not phone_number or not pin:
            return JsonResponse({"error": "Numéro de téléphone et PIN requis"}, status=400)
        
        scraper = TradeRepublicScraper(phone_number, pin)
        login_info = scraper.initiate_login()
        
        # Stocker les informations dans la session (numéro normalisé E.164 pour les étapes suivantes)
        request.session["traderepublic_phone"] = scraper.phone_number
        request.session["traderepublic_pin"] = pin
        request.session["traderepublic_account_name"] = account_name
        request.session["traderepublic_currency"] = currency
        request.session["traderepublic_process_id"] = login_info["process_id"]
        request.session["traderepublic_scraper"] = {
            "phone_number": scraper.phone_number,
            "pin": pin,
        }
        request.session["traderepublic_api_cookies"] = scraper.export_api_cookies_for_session()
        request.session["traderepublic_waf_token"] = getattr(scraper, "_waf_token", "") or ""
        request.session["traderepublic_device_info"] = getattr(scraper, "_device_info", "") or ""
        
        return JsonResponse({
            "success": True,
            "process_id": login_info["process_id"],
            "countdown": login_info["countdown"],
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def traderepublic_resend_2fa(request: HttpRequest) -> JsonResponse:
    """Renvoyer le code 2FA par SMS."""
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    try:
        scraper_info = request.session.get("traderepublic_scraper")
        if not scraper_info:
            return JsonResponse({"error": "Session expirée. Réessayez."}, status=400)
        
        api_cookies = request.session.get("traderepublic_api_cookies")
        scraper = TradeRepublicScraper(
            scraper_info["phone_number"],
            scraper_info["pin"],
            api_cookies=api_cookies,
            waf_token=request.session.get("traderepublic_waf_token") or "",
            device_info=request.session.get("traderepublic_device_info") or "",
        )
        scraper.process_id = request.session.get("traderepublic_process_id")
        scraper.resend_2fa()
        request.session["traderepublic_api_cookies"] = scraper.export_api_cookies_for_session()
        request.session["traderepublic_waf_token"] = getattr(scraper, "_waf_token", "") or ""
        request.session["traderepublic_device_info"] = getattr(scraper, "_device_info", "") or ""

        return JsonResponse({"success": True, "message": "Code 2FA renvoyé par SMS"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def traderepublic_verify_and_scrape(request: HttpRequest) -> JsonResponse:
    """Vérifie le code 2FA et lance le scraping."""
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    try:
        data = json.loads(request.body)
        code = data.get("code", "").strip()
        extract_details = data.get("extract_details", False)
        
        if not code:
            return JsonResponse({"error": "Code 2FA requis"}, status=400)
        
        scraper_info = request.session.get("traderepublic_scraper")
        if not scraper_info:
            return JsonResponse({"error": "Session expirée. Réessayez."}, status=400)
        
        process_id = request.session.get("traderepublic_process_id")
        if not process_id:
            return JsonResponse({"error": "Process ID manquant. Réessayez la connexion."}, status=400)
        
        api_cookies = request.session.get("traderepublic_api_cookies")
        scraper = TradeRepublicScraper(
            scraper_info["phone_number"],
            scraper_info["pin"],
            api_cookies=api_cookies,
            waf_token=request.session.get("traderepublic_waf_token") or "",
            device_info=request.session.get("traderepublic_device_info") or "",
        )
        scraper.process_id = process_id
        
        # Vérifier le code 2FA
        token = scraper.verify_2fa(code)
        request.session["traderepublic_api_cookies"] = scraper.export_api_cookies_for_session()
        request.session["traderepublic_waf_token"] = getattr(scraper, "_waf_token", "") or ""
        request.session["traderepublic_device_info"] = getattr(scraper, "_device_info", "") or ""
        
        # Créer un fichier temporaire pour le CSV
        with NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            # Scraper les transactions
            scraper.scrape_and_save(token, Path(tmp_path), extract_details)
            
            # Importer le CSV généré
            account_name = request.session.get("traderepublic_account_name", "Trade Republic")
            currency = request.session.get("traderepublic_currency", "EUR")
            
            count = import_traderepublic_from_csv(
                user=request.user,
                csv_path=tmp_path,
                account_name=account_name,
                currency=currency,
            )
            
            # Récupérer les liquidités disponibles et les stocker dans le compte
            try:
                cash_data = scraper.get_available_cash(token)
                if cash_data:
                    # Chercher le compte Trade Republic
                    account = Account.objects.filter(
                        owner=request.user,
                        name=account_name,
                        provider="traderepublic"
                    ).first()
                    
                    if account:
                        # Extraire le montant des liquidités
                        from decimal import Decimal
                        cash_amount = None
                        
                        # Fonction récursive pour chercher un montant dans les données
                        def find_amount(data, keys_to_try):
                            if isinstance(data, dict):
                                for key in keys_to_try:
                                    if key in data:
                                        value = data[key]
                                        if isinstance(value, (int, float)):
                                            return Decimal(str(value))
                                        elif isinstance(value, str):
                                            try:
                                                return Decimal(value.replace(",", "."))
                                            except:
                                                pass
                                # Chercher récursivement dans les valeurs dict
                                for value in data.values():
                                    result = find_amount(value, keys_to_try)
                                    if result is not None:
                                        return result
                            elif isinstance(data, list) and len(data) > 0:
                                # Chercher dans le premier élément de la liste
                                return find_amount(data[0], keys_to_try)
                            return None
                        
                        # Chercher le montant dans différentes structures possibles
                        keys_to_try = ["value", "amount", "availableCash", "cash", "balance", "available"]
                        cash_amount = find_amount(cash_data, keys_to_try)
                        
                        # Pour Trade Republic, on importe TOUT l'historique depuis le début
                        # donc initial_balance doit être à 0, pas aux liquidités actuelles
                        if cash_amount is not None:
                            account.initial_balance = Decimal("0")  # ✓ Zéro car on importe tout
                            account.balance_snapshot_date = timezone.now().date()
                            account.save(update_fields=["initial_balance", "balance_snapshot_date"])
            except Exception as cash_error:
                # Ne pas bloquer l'import si la récupération des liquidités échoue
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Erreur lors de la récupération des liquidités Trade Republic: {cash_error}")
            
            # Récupérer le portefeuille (CTO/PEA) et stocker le montant total
            try:
                portfolio_data = scraper.get_portfolio(token)
                if portfolio_data:
                    # Chercher le compte Trade Republic
                    account = Account.objects.filter(
                        owner=request.user,
                        name=account_name,
                        provider="traderepublic"
                    ).first()
                    
                    if account:
                        # Extraire le montant du portefeuille (CTO/PEA)
                        from decimal import Decimal
                        portfolio_amount = None
                        
                        # Fonction récursive pour chercher un montant dans les données
                        def find_amount(data, keys_to_try):
                            if isinstance(data, dict):
                                for key in keys_to_try:
                                    if key in data:
                                        value = data[key]
                                        if isinstance(value, (int, float)):
                                            return Decimal(str(value))
                                        elif isinstance(value, str):
                                            try:
                                                return Decimal(value.replace(",", "."))
                                            except:
                                                pass
                                # Chercher récursivement dans les valeurs dict
                                for value in data.values():
                                    result = find_amount(value, keys_to_try)
                                    if result is not None:
                                        return result
                            elif isinstance(data, list) and len(data) > 0:
                                # Chercher dans le premier élément de la liste
                                return find_amount(data[0], keys_to_try)
                            return None
                        
                        # Chercher le montant du portefeuille dans différentes structures possibles
                        # On cherche des clés comme "totalValue", "portfolioValue", "balance", etc.
                        keys_to_try = [
                            "totalValue", "portfolioValue", "total", "value", 
                            "balance", "amount", "equity", "netValue",
                            "total.value", "portfolio.value", "balance.value"
                        ]
                        portfolio_amount = find_amount(portfolio_data, keys_to_try)
                        
                        # Pour Trade Republic, on importe TOUT l'historique depuis le début
                        # donc initial_balance doit rester à 0, même si on récupère la valorisation actuelle
                        # La valorisation actuelle sera calculée depuis les transactions importées
                        if portfolio_amount is not None:
                            # On ne modifie PAS l'initial_balance ici
                            # Il reste à 0 car on importe tout l'historique
                            # La valorisation du portefeuille sera calculée depuis les transactions
                            pass
            except Exception as portfolio_error:
                # Ne pas bloquer l'import si la récupération du portefeuille échoue
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Erreur lors de la récupération du portefeuille Trade Republic: {portfolio_error}")
            
            # Nettoyer la session
            for key in ["traderepublic_phone", "traderepublic_pin", "traderepublic_account_name",
                        "traderepublic_currency", "traderepublic_process_id", "traderepublic_scraper",
                        "traderepublic_api_cookies", "traderepublic_waf_token", "traderepublic_device_info"]:
                request.session.pop(key, None)
            
            return JsonResponse({
                "success": True,
                "message": f"Import terminé: {count} transactions importées.",
                "count": count,
            })
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Données JSON invalides"}, status=400)
    except Exception as e:
        import traceback
        error_msg = str(e)
        if settings.DEBUG:
            error_msg += f"\n{traceback.format_exc()}"
        return JsonResponse({"error": error_msg}, status=500)


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
        
        if not account_id or valuation is None or not valuation_date:
            return JsonResponse({"error": "Paramètres manquants"}, status=400)
        
        # Récupérer le compte
        account = Account.objects.get(id=account_id, owner=request.user)
        
        # Vérifier que c'est un compte d'investissement
        if account.provider != "traderepublic" and account.type != Account.AccountType.BROKER:
            return JsonResponse({"error": "Ce compte n'est pas un compte d'investissement"}, status=400)
        
        # Parser la date
        from datetime import datetime
        valuation_datetime = datetime.strptime(valuation_date, "%Y-%m-%d")
        if settings.USE_TZ:
            valuation_datetime = timezone.make_aware(valuation_datetime, timezone.get_current_timezone())
        
        # Créer une transaction "snapshot" avec amount=0 et account_balance=valuation
        from decimal import Decimal
        Transaction.objects.create(
            account=account,
            posted_at=valuation_datetime,
            amount=Decimal("0"),
            description=f"Valorisation manuelle - {valuation} €",
            account_balance=Decimal(str(valuation)),
            raw={"source": "manual_valuation", "type": "snapshot"}
        )
        
        # Mettre à jour initial_balance si c'est la valorisation la plus récente
        account.initial_balance = Decimal(str(valuation))
        account.balance_snapshot_date = valuation_datetime.date()
        account.save(update_fields=["initial_balance", "balance_snapshot_date"])
        
        messages.success(request, f"Valorisation de {account.name} mise à jour : {valuation} € au {valuation_date}")
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


@login_required
def import_traderepublic_pdf(request: HttpRequest) -> JsonResponse:
    """
    Import et analyse d'un PDF Trade Republic pour mettre à jour la valorisation
    et la composition du portefeuille en utilisant l'API OpenAI
    """
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)
    
    if "pdf_file" not in request.FILES:
        return JsonResponse({"error": "Aucun fichier PDF fourni"}, status=400)
    
    if "account_id" not in request.POST:
        return JsonResponse({"error": "Aucun compte sélectionné"}, status=400)
    
    if "portfolio_type" not in request.POST:
        return JsonResponse({"error": "Aucun type de portefeuille sélectionné"}, status=400)
    
    try:
        # Récupérer le compte et le type de portefeuille
        account_id = int(request.POST["account_id"])
        portfolio_type = request.POST["portfolio_type"]
        account = Account.objects.get(id=account_id, owner=request.user, type=Account.AccountType.BROKER)
        
        # Récupérer le fichier PDF
        pdf_file = request.FILES["pdf_file"]
        
        # Extraire le texte du PDF
        pdf_text = extract_text_from_pdf(pdf_file)
        
        # Analyser avec OpenAI
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            return JsonResponse({"error": "Clé API OpenAI non configurée"}, status=500)
        
        openai.api_key = openai_api_key
        
        # Créer le prompt pour OpenAI
        # Mapper le portfolio_type au format utilisé dans les PDFs Trade Republic
        tax_wrapper_map = {
            "cto": "CTO",
            "pea": "PEA",
            "pea_pme": "PEA-PME",
            "crypto": "CRYPTO",
            "other": "",
            "all": "ALL"
        }
        tax_wrapper = tax_wrapper_map.get(portfolio_type, "")
        
        # Adapter le prompt selon si on veut tout ou un seul portefeuille
        if portfolio_type == "all":
            prompt = f"""
Analyse ce document PDF de Trade Republic et extrait les informations suivantes au format JSON.

IMPORTANT: 
- Ce document contient PLUSIEURS portefeuilles (CTO, PEA, CRYPTO, etc.)
- Extrais TOUS les portefeuilles avec leurs titres/actifs respectifs
- Groupe les actifs par portefeuille (CTO, PEA, PEA-PME, CRYPTO)
- Calcule la valorisation pour CHAQUE portefeuille séparément

⚠️ DATE DU DOCUMENT :
- **NE PAS** utiliser la date en en-tête du document (c'est la date d'impression)
- **CHERCHER** la date après "jusqu'au" (ex: "jusqu'au 08/11/2025")
- Cette date est la VRAIE date de valorisation du portefeuille
- Format attendu : DD/MM/YYYY

STRUCTURE DU PDF TRADE REPUBLIC :
Pour chaque titre, la structure exacte est :
1. Quantité de titres (ex: "5,048182 titre(s)")
2. Nom du titre et ISIN (**CODE DE 12 CARACTÈRES** à IGNORER, ex: "FR0000120578")
3. **COURS PAR TITRE** = prix unitaire (ex: "86,06")
4. **Date de valorisation** (ex: "08/11/2025") ← **UTILISER CETTE DATE**
5. **COURS EN EUR** = valeur totale (ex: "434,45")

📌 FORMAT ISIN : **TOUJOURS 12 caractères** (2 lettres + 10 chiffres/lettres)
   - Exemples : FR0000120578, DE0005190003, US88160R1014
   - L'ISIN peut être collé aux chiffres : "DE00070300091746,50" = ISIN(12 chars) + prix(1746,50)

⚠️ RÈGLES CRITIQUES :
1. **L'ISIN fait TOUJOURS 12 caractères** - Identifie-le et ignore ces 12 caractères complètement
2. Le **prix_unitaire** est le DERNIER nombre AVANT la date (format DD/MM/YYYY)
3. La **valeur_totale** est le PREMIER nombre APRÈS la date
4. **VALIDATION OBLIGATOIRE** : Tu DOIS vérifier que valeur_totale ≈ quantité × prix_unitaire
   - Si l'écart est > 2%, tu as fait une ERREUR, cherche le bon prix !

🔍 MÉTHODE DE DÉTECTION DU PRIX :
1. Cherche la date (format "08/11/2025")
2. Le nombre JUSTE AVANT la date = prix_unitaire
3. Le nombre JUSTE APRÈS la date = valeur_totale
4. VÉRIFIE : quantité × prix_unitaire ≈ valeur_totale

📊 EXEMPLES CONCRETS AVEC VALIDATION :
- "3,114876 titre(s) BMW ISIN:DE000519000386,22 08/11/2025268,56"
  → ISIN = DE0005190003 (12 caractères) à ignorer
  → Après ISIN: 86,22 (prix) | Date: 08/11/2025 | Après date: 268,56 (valeur)
  → Vérif: 3.114876 × 86.22 = 268.56 ✓
  → prix_unitaire=86.22, valeur_totale=268.56

- "0,172485 titre(s) Rheinmetall ISIN:DE00070300091746,50 08/11/2025301,25"
  → ISIN = DE0007030009 (12 caractères) à ignorer
  → Après ISIN: 1746,50 (prix) | Date: 08/11/2025 | Après date: 301,25 (valeur)
  → Vérif: 0.172485 × 1746.50 = 301.25 ✓
  → prix_unitaire=1746.50, valeur_totale=301.25

- "5,048182 titre(s) Sanofi ISIN:FR000012057886,06 08/11/2025434,45"
  → ISIN = FR0000120578 (12 caractères) à ignorer
  → Après ISIN: 86,06 (prix) | Date: 08/11/2025 | Après date: 434,45 (valeur)
  → Vérif: 5.048182 × 86.06 = 434.45 ✓
  → prix_unitaire=86.06, valeur_totale=434.45

Format JSON attendu :
{{
  "date": "<date du document au format YYYY-MM-DD>",
  "portefeuilles": [
    {{
      "type": "<CTO|PEA|PEA-PME|CRYPTO>",
      "valorisation": <montant total en euros pour ce portefeuille>,
      "titres": [
        {{
          "symbole": "<code ISIN ou ticker (pour crypto: symbole comme BTC, ETH)>",
          "nom": "<nom du titre ou de la crypto>",
          "quantite": <nombre d'actions/parts/unités>,
          "prix_unitaire": <COURS PAR TITRE en euros>,
          "valeur_totale": <COURS EN EUR = quantité × prix_unitaire>,
          "type": "<action|etf|obligation|crypto>"
        }}
      ]
    }}
  ]
}}

Texte du document :
""" + pdf_text[:50000]
        else:
            prompt = f"""
Analyse ce document PDF de Trade Republic et extrait les informations suivantes au format JSON.

IMPORTANT: 
- Ce document peut contenir PLUSIEURS portefeuilles (CTO, PEA, PEA-PME, CRYPTO)
- Extrais UNIQUEMENT les actifs du portefeuille "{tax_wrapper}"
- Cherche dans tout le document les sections qui mentionnent "{tax_wrapper}"
- La valorisation doit être celle du portefeuille "{tax_wrapper}" uniquement, PAS la valorisation totale

⚠️ DATE DU DOCUMENT :
- **NE PAS** utiliser la date en en-tête du document (c'est la date d'impression)
- **CHERCHER** la date après "jusqu'au" (ex: "jusqu'au 08/11/2025")
- Cette date est la VRAIE date de valorisation du portefeuille
- Format attendu : DD/MM/YYYY

STRUCTURE DU PDF TRADE REPUBLIC :
Pour chaque titre, la structure exacte est :
1. Quantité de titres (ex: "5,048182 titre(s)")
2. Nom du titre et ISIN (**CODE DE 12 CARACTÈRES** à IGNORER, ex: "FR0000120578")
3. **COURS PAR TITRE** = prix unitaire (ex: "86,06")
4. **Date de valorisation** (ex: "08/11/2025") ← **UTILISER CETTE DATE**
5. **COURS EN EUR** = valeur totale (ex: "434,45")

📌 FORMAT ISIN : **TOUJOURS 12 caractères** (2 lettres + 10 chiffres/lettres)
   - Exemples : FR0000120578, DE0005190003, US88160R1014
   - L'ISIN peut être collé aux chiffres : "DE00070300091746,50" = ISIN(12 chars) + prix(1746,50)

⚠️ RÈGLES CRITIQUES :
1. **L'ISIN fait TOUJOURS 12 caractères** - Identifie-le et ignore ces 12 caractères complètement
2. Le **prix_unitaire** est le DERNIER nombre AVANT la date (format DD/MM/YYYY)
3. La **valeur_totale** est le PREMIER nombre APRÈS la date
4. **VALIDATION OBLIGATOIRE** : Tu DOIS vérifier que valeur_totale ≈ quantité × prix_unitaire
   - Si l'écart est > 2%, tu as fait une ERREUR, cherche le bon prix !

🔍 MÉTHODE DE DÉTECTION DU PRIX :
1. Cherche la date (format "08/11/2025")
2. Le nombre JUSTE AVANT la date = prix_unitaire
3. Le nombre JUSTE APRÈS la date = valeur_totale
4. VÉRIFIE : quantité × prix_unitaire ≈ valeur_totale

📊 EXEMPLES CONCRETS AVEC VALIDATION :
- "3,114876 titre(s) BMW ISIN:DE000519000386,22 08/11/2025268,56"
  → ISIN = DE0005190003 (12 caractères) à ignorer
  → Après ISIN: 86,22 (prix) | Date: 08/11/2025 | Après date: 268,56 (valeur)
  → Vérif: 3.114876 × 86.22 = 268.56 ✓
  → prix_unitaire=86.22, valeur_totale=268.56

- "0,172485 titre(s) Rheinmetall ISIN:DE00070300091746,50 08/11/2025301,25"
  → ISIN = DE0007030009 (12 caractères) à ignorer
  → Après ISIN: 1746,50 (prix) | Date: 08/11/2025 | Après date: 301,25 (valeur)
  → Vérif: 0.172485 × 1746.50 = 301.25 ✓
  → prix_unitaire=1746.50, valeur_totale=301.25

- "5,048182 titre(s) Sanofi ISIN:FR000012057886,06 08/11/2025434,45"
  → ISIN = FR0000120578 (12 caractères) à ignorer
  → Après ISIN: 86,06 (prix) | Date: 08/11/2025 | Après date: 434,45 (valeur)
  → Vérif: 5.048182 × 86.06 = 434.45 ✓
  → prix_unitaire=86.06, valeur_totale=434.45

Format JSON attendu :
{{
  "valorisation_totale": <montant total en euros pour le portefeuille {tax_wrapper} UNIQUEMENT>,
  "date": "<date du document au format YYYY-MM-DD>",
  "titres": [
    {{
      "symbole": "<code ISIN ou ticker (pour crypto: symbole comme BTC, ETH)>",
      "nom": "<nom du titre ou de la crypto>",
      "quantite": <nombre d'actions/parts/unités>,
      "prix_unitaire": <COURS PAR TITRE en euros>,
      "valeur_totale": <COURS EN EUR = quantité × prix_unitaire>,
      "type": "<action|etf|obligation|crypto>",
      "portefeuille": "{tax_wrapper}"
    }}
  ]
}}

Texte du document :
""" + pdf_text[:50000]
        
        # Appeler l'API OpenAI
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Tu es un assistant spécialisé dans l'analyse de documents financiers. Tu dois extraire les données de manière précise et les formater en JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        # Parser la réponse
        result = json.loads(response.choices[0].message.content)
        from django.utils import timezone
        
        # Traiter selon le format (all ou single)
        if portfolio_type == "all":
            # Format multi-portefeuilles
            date_doc = datetime.strptime(result["date"], "%Y-%m-%d").date()
            posted_datetime = timezone.make_aware(datetime.combine(date_doc, datetime.min.time()))
            
            total_valorisation = Decimal("0")
            total_holdings = 0
            portefeuilles_info = []
            
            # Traiter chaque portefeuille
            for pf in result.get("portefeuilles", []):
                pf_type = pf["type"]
                pf_valorisation = Decimal(str(pf["valorisation"]))
                total_valorisation += pf_valorisation
                
                # Supprimer les anciennes positions de ce portefeuille
                InvestmentHolding.objects.filter(
                    account=account,
                    tax_wrapper=pf_type
                ).delete()
                
                # Créer les nouvelles positions
                pf_holdings = 0
                for titre in pf.get("titres", []):
                    InvestmentHolding.objects.create(
                        account=account,
                        symbol=titre["symbole"],
                        name=titre["nom"],
                        instrument_type=titre.get("type", "stock"),
                        quantity=Decimal(str(titre["quantite"])),
                        avg_cost=Decimal(str(titre["prix_unitaire"])),
                        tax_wrapper=pf_type,
                        currency="EUR"
                    )
                    pf_holdings += 1
                
                total_holdings += pf_holdings
                portefeuilles_info.append({
                    "type": pf_type,
                    "valorisation": float(pf_valorisation),
                    "titres": pf_holdings
                })
                
                # Créer une transaction snapshot POUR CE PORTEFEUILLE
                # Cela permet d'avoir une valorisation par type (PEA, CTO, CRYPTO) à une date donnée
                # On utilise la description comme identifiant unique car les requêtes JSONField peuvent être problématiques
                description = f"Snapshot valorisation {pf_type} (import PDF)"
                tx, created = Transaction.objects.update_or_create(
                    account=account,
                    posted_at=posted_datetime,
                    description=description,
                    amount=Decimal("0"),
                    defaults={
                        "account_balance": pf_valorisation,
                        "raw": {
                            "source": "traderepublic_pdf", 
                            "portfolio_type": pf_type,
                            "data": pf
                        }
                    }
                )
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"{'✅ Créée' if created else '🔄 Mise à jour'} - Transaction snapshot {pf_type}: {pf_valorisation}€ à la date {posted_datetime.date()}")
            
            # Mettre à jour le compte avec la valorisation totale
            account.initial_balance = total_valorisation
            account.balance_snapshot_date = date_doc
            account.portfolio_type = "all"
            account.save()
            
            return JsonResponse({
                "success": True,
                "message": f"✅ Tous les portefeuilles mis à jour avec succès",
                "details": {
                    "compte": account.name,
                    "valorisation_totale": float(total_valorisation),
                    "date": date_doc.isoformat(),
                    "titres_importes": total_holdings,
                    "portefeuilles": portefeuilles_info
                }
            })
        
        else:
            # Format single portefeuille (ancien)
            valorisation = Decimal(str(result["valorisation_totale"]))
            date_doc = datetime.strptime(result["date"], "%Y-%m-%d").date()
            posted_datetime = timezone.make_aware(datetime.combine(date_doc, datetime.min.time()))
            
            # Créer une transaction snapshot pour la valorisation
            # On utilise la description comme identifiant unique
            description = f"Snapshot valorisation {tax_wrapper} (import PDF)"
            tx, created = Transaction.objects.update_or_create(
                account=account,
                posted_at=posted_datetime,
                description=description,
                amount=Decimal("0"),
                defaults={
                    "account_balance": valorisation,
                    "raw": {
                        "source": "traderepublic_pdf",
                        "portfolio_type": tax_wrapper,
                        "data": result
                    }
                }
            )
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"{'✅ Créée' if created else '🔄 Mise à jour'} - Transaction snapshot {tax_wrapper}: {valorisation}€ à la date {posted_datetime.date()}")
            
            # Mettre à jour le compte
            account.initial_balance = valorisation
            account.balance_snapshot_date = date_doc
            account.portfolio_type = portfolio_type
            account.save()
            
            # Supprimer les anciennes positions de ce compte avec le même tax_wrapper
            InvestmentHolding.objects.filter(
                account=account, 
                tax_wrapper=tax_wrapper
            ).delete()
            
            # Créer les nouvelles positions
            holdings_created = 0
            for titre in result.get("titres", []):
                # Récupérer le portefeuille du titre (si fourni par l'IA)
                titre_tax_wrapper = titre.get("portefeuille", tax_wrapper)
                
                InvestmentHolding.objects.create(
                    account=account,
                    symbol=titre["symbole"],
                    name=titre["nom"],
                    instrument_type=titre.get("type", "stock"),
                    quantity=Decimal(str(titre["quantite"])),
                    avg_cost=Decimal(str(titre["prix_unitaire"])),
                    tax_wrapper=titre_tax_wrapper,
                    currency="EUR"
                )
                holdings_created += 1
            
            return JsonResponse({
                "success": True,
                "message": f"✅ Portefeuille {tax_wrapper} mis à jour avec succès",
                "details": {
                    "compte": account.name,
                    "portefeuille": tax_wrapper,
                    "valorisation": float(valorisation),
                    "date": date_doc.isoformat(),
                    "titres_importes": holdings_created
                }
            })
        
    except Account.DoesNotExist:
        return JsonResponse({"error": "Compte introuvable"}, status=404)
    except Exception as e:
        import traceback
        error_msg = str(e)
        if settings.DEBUG:
            error_msg += f"\n{traceback.format_exc()}"
        return JsonResponse({"error": f"Erreur lors de l'import: {error_msg}"}, status=500)


def extract_text_from_pdf(pdf_file: UploadedFile) -> str:
    """
    Extrait le texte d'un fichier PDF
    """
    try:
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        raise Exception(f"Erreur lors de l'extraction du PDF: {str(e)}")


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

        # Trouver le compte associé
        account = Account.objects.filter(bank_connection=connection).first()
        connection.linked_account = account

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

            try:
                from finance.services.encryption_service import EncryptionService
                from finance.connectors.traderepublic import TradeRepublicConnector

                credentials = EncryptionService.decrypt_credentials(connection.encrypted_credentials)
                connector = TradeRepublicConnector()
                connector.phone_number = credentials.get("phone_number")
                connector.pin = credentials.get("pin")

                # Initier la connexion pour obtenir un nouveau process_id
                auth_result = connector.authenticate(credentials)
                if auth_result.get("requires_2fa"):
                    messages.success(request, "Code 2FA renvoyé avec succès.")
                else:
                    messages.error(request, "Erreur lors du renvoi du code 2FA.")
            except Exception as e:
                messages.error(request, f"Erreur lors du renvoi du code 2FA : {str(e)}")

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

