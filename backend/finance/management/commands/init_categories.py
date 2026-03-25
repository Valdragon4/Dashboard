from django.core.management.base import BaseCommand
from finance.models import Category


class Command(BaseCommand):
    help = "Initialise les catégories par défaut pour le cashflow"

    def handle(self, *args, **options):
        categories = [
            # Revenus
            {"name": "Salaire", "parent": "Revenus"},
            {"name": "Remboursement", "parent": "Revenus"},
            {"name": "Cadeaux reçus", "parent": "Revenus"},
            {"name": "Vente", "parent": "Revenus"},
            {"name": "Intérêts", "parent": "Revenus"},
            {"name": "Dividendes", "parent": "Revenus"},
            {"name": "Autres revenus", "parent": "Revenus"},
            
            # Dépenses - Essentiel
            {"name": "Alimentation", "parent": "Essentiel"},
            {"name": "Logement", "parent": "Essentiel"},
            {"name": "Transport", "parent": "Essentiel"},
            {"name": "Santé", "parent": "Essentiel"},
            {"name": "Assurances", "parent": "Essentiel"},
            {"name": "Éducation", "parent": "Essentiel"},
            
            # Dépenses - Loisirs
            {"name": "Restaurants", "parent": "Loisirs"},
            {"name": "Sorties", "parent": "Loisirs"},
            {"name": "Voyages", "parent": "Loisirs"},
            {"name": "Sport", "parent": "Loisirs"},
            {"name": "Abonnements", "parent": "Loisirs"},
            
            # Dépenses - Shopping
            {"name": "Vêtements", "parent": "Shopping"},
            {"name": "Électronique", "parent": "Shopping"},
            {"name": "Maison", "parent": "Shopping"},
            {"name": "Cadeaux offerts", "parent": "Shopping"},
            
            # Transferts
            {"name": "Épargne", "parent": "Transferts"},
            {"name": "Investissements", "parent": "Transferts"},
            {"name": "Famille", "parent": "Transferts"},
            {"name": "Amis", "parent": "Transferts"},
            
            # Autres
            {"name": "Impôts", "parent": "Autres dépenses"},
            {"name": "Frais bancaires", "parent": "Autres dépenses"},
            {"name": "Divers", "parent": "Autres dépenses"},
        ]
        
        created_count = 0
        for cat_data in categories:
            # Créer la catégorie parente si elle n'existe pas
            parent_obj = None
            if cat_data["parent"]:
                parent_obj, parent_created = Category.objects.get_or_create(
                    name=cat_data["parent"]
                )
                if parent_created:
                    self.stdout.write(
                        self.style.SUCCESS(f"✓ Catégorie parente créée: {cat_data['parent']}")
                    )
            
            # Créer la catégorie enfant
            category, created = Category.objects.get_or_create(
                name=cat_data["name"],
                defaults={"parent": parent_obj}
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Catégorie créée: {cat_data['name']}")
                )
        
        if created_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f"\n✅ {created_count} nouvelles catégories créées")
            )
        else:
            self.stdout.write(
                self.style.WARNING("ℹ️  Toutes les catégories existent déjà")
            )

