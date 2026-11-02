"""
Enregistre une session Enable Banking ouverte hors de l'interface (fichier JSON
renvoyé par POST /sessions) et rattache, au besoin, son compte à un Account.

Usage :
  python manage.py enablebanking_import_session /secrets/enablebanking/session-hellobank.json \
      --aspsp "Hello Bank" --owner 1 --account 26
"""

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finance.models import Account, BankAccountLink
from finance.views_enablebanking import store_session


class Command(BaseCommand):
    help = "Importe une session Enable Banking (JSON) dans une connexion bancaire."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--aspsp", required=True)
        parser.add_argument("--country", default="FR")
        parser.add_argument("--psu-type", default="personal")
        parser.add_argument("--owner", type=int, required=True)
        parser.add_argument("--account", type=int, help="Account à rattacher (session à un seul compte)")

    def handle(self, *args, **opts):
        with open(opts["path"]) as fh:
            session = json.load(fh)
        if "session_id" not in session:
            raise CommandError("Fichier sans session_id.")
        owner = get_user_model().objects.get(id=opts["owner"])
        ctx = {"aspsp": opts["aspsp"], "country": opts["country"], "psu_type": opts["psu_type"], "connection_id": None}
        connection = store_session(owner, ctx, session)
        self.stdout.write(f"Connexion {connection.id} « {connection.account_name} » enregistrée.")

        if opts["account"]:
            links = list(BankAccountLink.objects.filter(connection=connection, disabled=False))
            if len(links) != 1:
                raise CommandError(f"{len(links)} comptes dans la session : rattacher depuis l'interface.")
            account = Account.objects.get(id=opts["account"], owner=owner)
            links[0].account = account
            links[0].save(update_fields=["account", "updated_at"])
            account.bank_connection = connection
            account.auto_sync_enabled = True
            account.save(update_fields=["bank_connection", "auto_sync_enabled"])
            self.stdout.write(f"Compte « {account.name} » (id {account.id}) rattaché.")
