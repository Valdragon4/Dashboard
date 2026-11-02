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
    TradeRepublicValuationSnapshot,
    TradeRepublicPortfolioSnapshot,
    TradeRepublicSubAccountMapping,
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


class TradeRepublicPortfolioSnapshotInline(admin.TabularInline):
    model = TradeRepublicPortfolioSnapshot
    extra = 0
    fields = ("portfolio_type", "currency", "invested_total", "current_value")


@admin.register(TradeRepublicValuationSnapshot)
class TradeRepublicValuationSnapshotAdmin(admin.ModelAdmin):
    """
    Permet d'ajouter manuellement des points de valorisation historiques
    (source="manual", ex. à partir d'anciens relevés PDF) pour étoffer
    l'historique utilisé par le calcul de TRI.
    """

    list_display = (
        "source_timestamp", "owner", "account", "source",
        "total_with_cash", "invested_total", "positions_total", "cash",
    )
    list_filter = ("source", "auth_status", "account")
    date_hierarchy = "source_timestamp"
    search_fields = ("owner__username", "owner__email")
    inlines = [TradeRepublicPortfolioSnapshotInline]
    fieldsets = (
        (None, {
            "fields": ("owner", "account", "source", "auth_status", "source_timestamp", "currency"),
        }),
        ("Valorisation", {
            "fields": ("total_with_cash", "positions_total", "cash", "invested_total"),
        }),
        ("Détail (optionnel)", {
            "classes": ("collapse",),
            "fields": (
                "invested_societes", "invested_crypto", "invested_by_asset_type",
                "societes", "crypto", "bridge_latency_ms", "raw",
            ),
        }),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("source", TradeRepublicValuationSnapshot.Source.MANUAL)
        initial.setdefault("auth_status", TradeRepublicValuationSnapshot.AuthStatus.AUTHENTICATED)
        initial.setdefault("owner", request.user.pk)
        return initial


admin.site.register(Category)
admin.site.register(CashflowRule)
admin.site.register(BudgetGoal)
admin.site.register(InvestmentHolding)
admin.site.register(InvestmentPrice)
admin.site.register(NetWorthSnapshot)


@admin.register(TradeRepublicSubAccountMapping)
class TradeRepublicSubAccountMappingAdmin(admin.ModelAdmin):
    list_display = ("owner", "external_account_id", "portfolio_type", "updated_at")
    list_filter = ("portfolio_type",)
    search_fields = ("external_account_id", "owner__username")


