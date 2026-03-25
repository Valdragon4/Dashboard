from django.contrib import admin

from .models import (
    Account,
    Transaction,
    Category,
    CashflowRule,
    BudgetGoal,
    InvestmentHolding,
    InvestmentPrice,
    NetWorthSnapshot,
)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "currency", "provider", "owner")
    list_filter = ("type", "currency")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("posted_at", "account", "amount", "description", "category")
    list_filter = ("account", "category")
    search_fields = ("description", "counterparty")


admin.site.register(Category)
admin.site.register(CashflowRule)
admin.site.register(BudgetGoal)
admin.site.register(InvestmentHolding)
admin.site.register(InvestmentPrice)
admin.site.register(NetWorthSnapshot)


