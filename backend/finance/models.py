from __future__ import annotations

from django.conf import settings
from django.db import models


class Account(models.Model):
    class AccountType(models.TextChoices):
        CHECKING = "checking", "Compte courant"
        SAVINGS = "savings", "Épargne"
        BROKER = "broker", "Courtier"
        CASH = "cash", "Espèces"
        CREDIT = "credit", "Crédit"
    
    class PortfolioType(models.TextChoices):
        CTO = "cto", "Compte-Titres Ordinaire (CTO)"
        PEA = "pea", "Plan d'Épargne en Actions (PEA)"
        PEA_PME = "pea_pme", "PEA-PME"
        CRYPTO = "crypto", "Cryptomonnaies"
        OTHER = "other", "Autre"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    provider = models.CharField(max_length=120, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    currency = models.CharField(max_length=8, default="EUR")
    type = models.CharField(max_length=16, choices=AccountType.choices)
    portfolio_type = models.CharField(max_length=16, choices=PortfolioType.choices, blank=True, help_text="Type de portefeuille pour les comptes courtier")
    interest_rate_apy = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    initial_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0, help_text="Solde initial avant toutes les transactions importées")
    balance_snapshot_date = models.DateField(null=True, blank=True, help_text="Date du snapshot du solde initial")
    include_in_dashboard = models.BooleanField(default=True, help_text="Inclure ce compte dans les statistiques du dashboard")
    # Extension pour synchronisation automatique
    bank_connection = models.ForeignKey(
        "BankConnection",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounts",
        help_text="Connexion bancaire associée pour synchronisation automatique",
    )
    auto_sync_enabled = models.BooleanField(
        default=False,
        help_text="Activer la synchronisation automatique pour ce compte",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.name} ({self.type})"


class Category(models.Model):
    name = models.CharField(max_length=80)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class Transaction(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    posted_at = models.DateTimeField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="EUR")
    description = models.CharField(max_length=255, blank=True)
    counterparty = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    is_transfer = models.BooleanField(default=False)
    account_balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, help_text="Solde brut du compte après cette transaction (depuis le CSV)")
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["posted_at"]),
            models.Index(fields=["account", "posted_at"]),
        ]


class CashflowRule(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    match_description_regex = models.CharField(max_length=255, blank=True)
    match_counterparty_regex = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    priority = models.IntegerField(default=100)


class BudgetGoal(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    amount_monthly = models.DecimalField(max_digits=12, decimal_places=2)


class InvestmentHolding(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    symbol = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    instrument_type = models.CharField(max_length=32, default="stock")
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=6)
    tax_wrapper = models.CharField(max_length=8, blank=True)  # PEA/CTO
    currency = models.CharField(max_length=8, default="EUR")


class InvestmentPrice(models.Model):
    symbol = models.CharField(max_length=32)
    date = models.DateField()
    close = models.DecimalField(max_digits=14, decimal_places=6)
    source = models.CharField(max_length=64, default="yfinance")

    class Meta:
        unique_together = ("symbol", "date")


class NetWorthSnapshot(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField()
    total_assets = models.DecimalField(max_digits=16, decimal_places=2)
    total_liabilities = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    breakdown = models.JSONField(default=dict)


class BankConnection(models.Model):
    """
    Modèle pour stocker les connexions bancaires avec credentials chiffrés.

    Ce modèle remplace l'ancien modèle BankConnection qui était utilisé pour POWENS.
    Les nouveaux champs permettent de stocker les credentials chiffrés et de gérer
    la synchronisation automatique.
    """

    class Provider(models.TextChoices):
        TRADE_REPUBLIC = "trade_republic", "Trade Republic"
        BOURSORAMA = "boursorama", "BoursoBank"
        HELLOBANK = "hellobank", "Hello Bank"
        POWENS = "powens", "Powens (API)"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "En attente"
        SYNCING = "syncing", "Synchronisation en cours"
        SUCCESS = "success", "Succès"
        ERROR = "error", "Erreur"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bank_connections",
        help_text="Propriétaire de la connexion bancaire",
    )
    provider = models.CharField(
        max_length=32,
        choices=Provider.choices,
        default=Provider.TRADE_REPUBLIC,
        help_text="Provider bancaire (Trade Republic, BoursoBank, Hello Bank)",
    )
    account_name = models.CharField(
        max_length=120,
        default="",
        help_text="Nom du compte bancaire associé",
    )
    encrypted_credentials = models.TextField(
        default="",
        help_text="Credentials bancaires chiffrés avec AES-256",
    )
    auto_sync_enabled = models.BooleanField(
        default=True,
        help_text="Activer la synchronisation automatique quotidienne",
    )
    last_sync_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date et heure de la dernière synchronisation réussie",
    )
    sync_status = models.CharField(
        max_length=16,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
        help_text="Statut actuel de la synchronisation",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "provider"]),
            models.Index(fields=["sync_status"]),
            models.Index(fields=["last_sync_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.account_name} ({self.get_provider_display()})"


class SyncLog(models.Model):
    """
    Modèle pour tracer les synchronisations bancaires.

    Chaque synchronisation (manuelle ou automatique) crée un log pour permettre
    le suivi, le debugging et l'audit des synchronisations.
    """

    class SyncType(models.TextChoices):
        MANUAL = "manual", "Manuelle"
        AUTOMATIC = "automatic", "Automatique"

    class Status(models.TextChoices):
        STARTED = "started", "Démarrée"
        SUCCESS = "success", "Succès"
        ERROR = "error", "Erreur"

    bank_connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name="sync_logs",
        help_text="Connexion bancaire synchronisée",
    )
    sync_type = models.CharField(
        max_length=16,
        choices=SyncType.choices,
        help_text="Type de synchronisation (manuelle ou automatique)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.STARTED,
        help_text="Statut de la synchronisation",
    )
    started_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Date et heure de début de la synchronisation",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date et heure de fin de la synchronisation",
    )
    error_message = models.TextField(
        blank=True,
        help_text="Message d'erreur en cas d'échec de la synchronisation",
    )
    transactions_count = models.IntegerField(
        default=0,
        help_text="Nombre de transactions synchronisées",
    )

    class Meta:
        indexes = [
            models.Index(fields=["bank_connection", "-started_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["sync_type"]),
        ]
        ordering = ["-started_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.bank_connection.account_name} - {self.get_sync_type_display()} - {self.get_status_display()} ({self.started_at})"


class BankAccountLink(models.Model):
    connection = models.ForeignKey(BankConnection, on_delete=models.CASCADE, related_name="account_links")
    external_account_id = models.CharField(max_length=64)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=120, blank=True)
    currency = models.CharField(max_length=8, default="EUR")
    type = models.CharField(max_length=32, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    disabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


