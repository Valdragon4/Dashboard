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
        }


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["account", "posted_at", "amount", "currency", "description", "counterparty", "category"]
        widgets = {
            "posted_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class ImportStatementForm(forms.Form):
    IMPORT_CHOICES = (
        ("boursobank", "Boursobank CSV"),
        ("hellobank", "Hello bank CSV"),
        ("generic", "CSV générique"),
        ("traderepublic", "Trade Republic CSV"),
    )

    import_type = forms.ChoiceField(choices=IMPORT_CHOICES)
    account_name = forms.CharField(max_length=120)
    currency = forms.CharField(
        max_length=8,
        required=False,
        help_text="Optionnel pour Trade Republic",
    )
    file = forms.FileField()


class BankConnectionForm(forms.ModelForm):
    """
    Formulaire pour créer/modifier une connexion bancaire.

    Gère les champs conditionnels selon le provider et chiffre
    les credentials avant sauvegarde.
    """

    # Champs communs
    account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=True,
        help_text="Compte associé à cette connexion bancaire",
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
        self.fields["account"].queryset = Account.objects.filter(owner=user)

        # Si on modifie une connexion existante, pré-remplir le compte
        if self.instance and self.instance.pk:
            self.fields["account"].initial = (
                Account.objects.filter(bank_connection=self.instance).first()
            )

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

        # Si account_name n'est pas fourni, utiliser le nom du compte sélectionné
        account = self.cleaned_data.get("account")
        if account and not instance.account_name:
            instance.account_name = account.name

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

        # Chiffrer les credentials avant sauvegarde
        if credentials:
            instance.encrypted_credentials = EncryptionService.encrypt_credentials(credentials)

        if commit:
            instance.save()

            # Associer le compte à la connexion bancaire
            if account:
                account.bank_connection = instance
                # Activer la synchronisation sur le compte si elle est activée sur la connexion
                account.auto_sync_enabled = instance.auto_sync_enabled
                account.save()

        return instance

