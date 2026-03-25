from django.core.management.base import BaseCommand
from finance.models import Transaction, Category
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = "Assigne automatiquement des catégories de test aux transactions basées sur les descriptions"

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Nom d\'utilisateur')
        parser.add_argument('--limit', type=int, default=50, help='Nombre de transactions à traiter')

    def handle(self, *args, **options):
        username = options['username']
        limit = options['limit']
        
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ Utilisateur '{username}' introuvable"))
            return
        
        # Récupérer les catégories
        try:
            salaire = Category.objects.get(name="Salaire")
            alimentation = Category.objects.get(name="Alimentation")
            transport = Category.objects.get(name="Transport")
            logement = Category.objects.get(name="Logement")
            loisirs = Category.objects.get(name="Restaurants")
            shopping = Category.objects.get(name="Vêtements")
            sante = Category.objects.get(name="Santé")
            remboursement = Category.objects.get(name="Remboursement")
            famille = Category.objects.get(name="Famille")
            sans_cat = None
        except Category.DoesNotExist as e:
            self.stdout.write(self.style.ERROR(f"❌ Catégorie introuvable: {e}"))
            self.stdout.write(self.style.WARNING("Exécutez d'abord: python manage.py init_categories"))
            return
        
        # Règles de catégorisation basées sur les mots-clés
        rules = [
            # Revenus
            (["SALAIRE", "VIREMENT SALAIRE", "PAYE", "REMUNERATION"], salaire),
            (["REMBOURSEMENT", "REMB", "REMBOURS"], remboursement),
            
            # Dépenses
            (["CARREFOUR", "AUCHAN", "LECLERC", "LIDL", "INTERMARCHE", "CASINO", "MONOPRIX", "SUPER U", "ALIMENTATION"], alimentation),
            (["ESSENCE", "TOTAL", "BP", "SHELL", "ESSO", "CARBURANT", "STATION", "SNCF", "RATP", "UBER", "TAXI"], transport),
            (["LOYER", "EDF", "GDF", "EAU", "ELECTRICITE", "GAZ", "ASSURANCE HABITATION"], logement),
            (["RESTAURANT", "MCDO", "MCDONALD", "KFC", "BURGER", "PIZZA", "CAFE", "BAR"], loisirs),
            (["ZARA", "H&M", "KIABI", "DECATHLON", "NIKE", "ADIDAS"], shopping),
            (["PHARMACIE", "MEDECIN", "DOCTEUR", "CLINIQUE", "HOPITAL", "MUTUELLE"], sante),
            (["VIR", "VIREMENT", "VERS", "DE M", "MAROT", "VALENTIN"], famille),
        ]
        
        # Récupérer les transactions sans catégorie
        transactions = Transaction.objects.filter(
            account__owner=user,
            category__isnull=True
        ).order_by('-posted_at')[:limit]
        
        count = 0
        for tx in transactions:
            description_upper = tx.description.upper()
            
            # Chercher une correspondance avec les règles
            matched = False
            for keywords, category in rules:
                if any(keyword in description_upper for keyword in keywords):
                    tx.category = category
                    tx.save(update_fields=['category'])
                    count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"✓ {tx.description[:50]} → {category.name}")
                    )
                    matched = True
                    break
        
        self.stdout.write(
            self.style.SUCCESS(f"\n✅ {count} transactions catégorisées sur {len(transactions)} traitées")
        )
        
        if count == 0:
            self.stdout.write(
                self.style.WARNING("💡 Aucune correspondance trouvée. Assignez manuellement les catégories depuis l'interface.")
            )

