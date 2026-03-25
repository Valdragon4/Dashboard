from __future__ import annotations

from datetime import date
from decimal import Decimal

import re
from django.db.models import QuerySet

from .models import InvestmentHolding, InvestmentPrice, CashflowRule, Transaction


def compute_position_value(symbol: str, qty: Decimal, on: date) -> Decimal:
    price = (
        InvestmentPrice.objects.filter(symbol=symbol, date__lte=on)
        .order_by("-date")
        .first()
    )
    if not price:
        return Decimal("0")
    return (qty or Decimal("0")) * Decimal(price.close)


def savings_from_cashflows(income: Decimal, expenses: Decimal, market_gain: Decimal) -> Decimal:
    return (income + expenses) - market_gain


def apply_categorization_rules(rules: QuerySet[CashflowRule], tx: Transaction) -> None:
    for rule in rules.order_by("priority"):
        ok_desc = True
        ok_cp = True
        if rule.match_description_regex:
            ok_desc = bool(re.search(rule.match_description_regex, tx.description or "", re.IGNORECASE))
        if rule.match_counterparty_regex:
            ok_cp = bool(re.search(rule.match_counterparty_regex, tx.counterparty or "", re.IGNORECASE))
        if ok_desc and ok_cp:
            tx.category = rule.category
            tx.save(update_fields=["category"])
            break


