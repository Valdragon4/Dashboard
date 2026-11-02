from __future__ import annotations

from django import forms

from .models import Account, Transaction, BankConnection
from .services.encryption_service import EncryptionService


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "type", "portfolio_type", "currency", "provider", "iban", "interest_rate_apy"]
        widgets = {
            "portfolio_type": forms.Select(attrs={"class": "portfolio-type-field"}),
            "provider": forms.Select(attrs={"class": "provider-field"}),
        }


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["account", "posted_at", "amount", "currency", "description", "counterparty", "category"]
        widgets = {
            "posted_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class BankConnectionForm(forms.ModelForm):
    """
    Formulaire pour créer/modifier une connexion bancaire.

    Gère les champs conditionnels selon le provider et chiffre
    les credentials avant sauvegarde.
    """

    # Sélection multiple de comptes à associer à cette connexion
    accounts = forms.ModelMultipleChoiceField(
        queryset=Account.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        help_text="Comptes associés à cette connexion bancaire (un ou plusieurs)",
    )

    # Champs conditionnels selon le provider
    phone_number = forms.CharField(
        max_length=20,
        required=False,
        help_text="Numéro de téléphone (Trade Republic uniquement)",
    )
    pin = forms.CharField(
        max_length=10,
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        help_text="Code PIN (Trade Republic uniquement)",
    )
    username = forms.CharField(
        max_length=120,
        required=False,
        help_text="Nom d'utilisateur (BoursoBank/Hello Bank uniquement)",
    )
    password = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        help_text="Mot de passe (BoursoBank/Hello Bank uniquement)",
    )
    two_fa_code = forms.CharField(
        max_length=10,
        required=False,
        help_text="Code 2FA (optionnel, pour authentification complète)",
    )

    class Meta:
        model = BankConnection
        fields = ["provider", "account_name", "auto_sync_enabled"]
        widgets = {
            "provider": forms.Select(attrs={"class": "provider-field"}),
            "account_name": forms.TextInput(
                attrs={"placeholder": "Nom du compte (optionnel, défaut: nom du compte sélectionné)"}
            ),
        }

    def __init__(self, user, *args, **kwargs):
        """
        Initialise le formulaire avec l'utilisateur.

        Args:
            user: Utilisateur Django pour filtrer les comptes disponibles
        """
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["accounts"].queryset = Account.objects.filter(owner=user)

        # Si on modifie une connexion existante, pré-sélectionner les comptes déjà liés
        if self.instance and self.instance.pk:
            self.fields["accounts"].initial = Account.objects.filter(bank_connection=self.instance)

    def clean(self):
        """
        Valide les champs requis selon le provider sélectionné.
        """
        cleaned_data = super().clean()
        provider = cleaned_data.get("provider")

        if not provider:
            return cleaned_data

        # Valider les champs requis selon le provider
        if provider == BankConnection.Provider.TRADE_REPUBLIC:
            if not cleaned_data.get("phone_number"):
                self.add_error("phone_number", "Le numéro de téléphone est requis pour Trade Republic")
            if not cleaned_data.get("pin"):
                self.add_error("pin", "Le code PIN est requis pour Trade Republic")
        elif provider in [BankConnection.Provider.BOURSORAMA, BankConnection.Provider.HELLOBANK]:
            if not cleaned_data.get("username"):
                self.add_error("username", "Le nom d'utilisateur est requis")
            if not cleaned_data.get("password"):
                self.add_error("password", "Le mot de passe est requis")

        return cleaned_data

    def save(self, commit=True):
        """
        Sauvegarde la connexion bancaire avec credentials chiffrés.

        Args:
            commit: Si True, sauvegarde immédiatement en base de données

        Returns:
            BankConnection: Instance de la connexion bancaire créée/modifiée
        """
        instance = super().save(commit=False)
        instance.owner = self.user

        selected_accounts = self.cleaned_data.get("accounts", [])

        # account_name par défaut : noms des comptes sélectionnés
        if not instance.account_name and selected_accounts:
            instance.account_name = ", ".join(a.name for a in selected_accounts)

        # Construire le dictionnaire de credentials selon le provider
        provider = self.cleaned_data["provider"]
        credentials = {}

        if provider == BankConnection.Provider.TRADE_REPUBLIC:
            credentials = {
                "phone_number": self.cleaned_data["phone_number"],
                "pin": self.cleaned_data["pin"],
            }
            if self.cleaned_data.get("two_fa_code"):
                credentials["2fa_code"] = self.cleaned_data["two_fa_code"]
        elif provider in [BankConnection.Provider.BOURSORAMA, BankConnection.Provider.HELLOBANK]:
            credentials = {
                "username": self.cleaned_data["username"],
                "password": self.cleaned_data["password"],
            }
            if self.cleaned_data.get("two_fa_code"):
                credentials["2fa_code"] = self.cleaned_data["two_fa_code"]

        if credentials:
            instance.encrypted_credentials = EncryptionService.encrypt_credentials(credentials)

        if commit:
            instance.save()

            selected_ids = {a.id for a in selected_accounts}

            # Délier les comptes qui ne sont plus sélectionnés
            Account.objects.filter(bank_connection=instance).exclude(id__in=selected_ids).update(
                bank_connection=None, auto_sync_enabled=False
            )

            # Associer/mettre à jour les comptes sélectionnés
            for account in selected_accounts:
                account.bank_connection = instance
                account.auto_sync_enabled = instance.auto_sync_enabled
                account.save(update_fields=["bank_connection", "auto_sync_enabled"])

        return instance

