from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from finance.models import Account
from finance.services import bitstack_import


class Command(BaseCommand):
    help = (
        "Importe un relevé CSV Bitstack (profil → Documents → Relevés de compte). "
        "Réimporter le même fichier est sans effet : la déduplication se fait sur "
        "la colonne « ID Externe »."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")
        parser.add_argument("csv_path")
        parser.add_argument(
            "--account-name",
            default="Bitstack",
            help="Nom du compte à créer ou à alimenter (défaut: Bitstack).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Analyse le fichier et affiche le bilan sans rien écrire.",
        )
        parser.add_argument(
            "--no-valuation",
            action="store_true",
            help="N'écrit pas le snapshot de valorisation après l'import.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f"Utilisateur introuvable : {options['username']}") from exc

        account = Account.objects.filter(owner=user, name=options["account_name"]).first()
        if account is None:
            if options["dry_run"]:
                # Un dry-run ne doit rien écrire, pas même le compte : on
                # travaille sur une instance non sauvegardée.
                account = Account(
                    owner=user,
                    name=options["account_name"],
                    type=Account.AccountType.BROKER,
                    portfolio_type=Account.PortfolioType.CRYPTO,
                    provider=Account.Provider.BITSTACK,
                    currency="EUR",
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"[dry-run] Le compte « {options['account_name']} » serait créé."
                    )
                )
            else:
                account = Account.objects.create(
                    owner=user,
                    name=options["account_name"],
                    type=Account.AccountType.BROKER,
                    portfolio_type=Account.PortfolioType.CRYPTO,
                    provider=Account.Provider.BITSTACK,
                    currency="EUR",
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Compte créé : {account.name} (id={account.id})")
                )

        try:
            with open(options["csv_path"], newline="", encoding="utf-8-sig") as handle:
                result = bitstack_import.import_csv(
                    account, handle, dry_run=options["dry_run"]
                )
        except FileNotFoundError as exc:
            raise CommandError(f"Fichier introuvable : {options['csv_path']}") from exc
        except bitstack_import.BitstackImportError as exc:
            raise CommandError(str(exc)) from exc

        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"⚠ {warning}"))

        prefix = "[dry-run] " if options["dry_run"] else ""
        self.stdout.write(
            f"{prefix}{result.created} créée(s), {result.updated} mise(s) à jour, "
            f"{result.skipped} ignorée(s)"
        )
        self.stdout.write(f"{prefix}Delta BTC du fichier : {result.btc_delta}")
        self.stdout.write(f"{prefix}Euros investis dans le fichier : {result.eur_invested} €")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run : aucune écriture en base."))
            return

        holding = bitstack_import.sync_holding(account)
        position = bitstack_import.compute_btc_position(account)
        self.stdout.write(
            self.style.SUCCESS(
                f"Position totale du compte : {position} BTC "
                f"(prix de revient moyen {holding.avg_cost} €)"
            )
        )

        if options["no_valuation"]:
            return

        snapshot = bitstack_import.sync_valuation(account)
        if snapshot is None:
            self.stdout.write(
                self.style.WARNING(
                    "Valorisation non écrite (cours BTC/EUR indisponible ou position vide)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Valorisation : {snapshot.account_balance} €")
            )
