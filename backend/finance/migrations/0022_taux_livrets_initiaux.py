"""Taux officiels connus au 2026-11-02 (service-public.gouv.fr, economie.gouv.fr).

Ensuite, la tâche hebdomadaire fetch_regulated_savings_rates les tient à jour.
"""

from datetime import date
from decimal import Decimal

from django.db import migrations

RATES = [
    ("livret_a", date(2025, 8, 1), Decimal("1.700")),
    ("livret_a", date(2026, 2, 1), Decimal("1.500")),
    ("livret_a", date(2026, 8, 1), Decimal("1.700")),
    ("lep", date(2025, 8, 1), Decimal("2.700")),
    ("lep", date(2026, 2, 1), Decimal("2.500")),
    ("lep", date(2026, 8, 1), Decimal("2.500")),
]


def seed(apps, schema_editor):
    Rate = apps.get_model("finance", "RegulatedSavingsRate")
    for product, valid_from, rate in RATES:
        Rate.objects.get_or_create(
            product=product, valid_from=valid_from, defaults={"rate": rate, "source": "saisie initiale 2026-11-02"}
        )


class Migration(migrations.Migration):
    dependencies = [("finance", "0021_livrets_reglementes")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
