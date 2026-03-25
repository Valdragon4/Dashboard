from django.core.management.base import BaseCommand
from finance.models import Transaction
from django.contrib.auth import get_user_model
from datetime import datetime

User = get_user_model()


class Command(BaseCommand):
    help = "Vérifie les transactions et trouve les transactions 'fantômes'"

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Nom d\'utilisateur')
        parser.add_argument('--description', type=str, help='Rechercher par description')
        parser.add_argument('--amount', type=float, help='Rechercher par montant')
        parser.add_argument('--date', type=str, help='Rechercher par date (DD/MM/YYYY)')

    def handle(self, *args, **options):
        username = options['username']
        
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ Utilisateur '{username}' introuvable"))
            return
        
        # Construire la requête
        query = Transaction.objects.filter(account__owner=user)
        
        if options['description']:
            query = query.filter(description__icontains=options['description'])
        
        if options['amount']:
            query = query.filter(amount=options['amount'])
        
        if options['date']:
            try:
                date_obj = datetime.strptime(options['date'], '%d/%m/%Y').date()
                query = query.filter(posted_at__date=date_obj)
            except ValueError:
                self.stdout.write(self.style.ERROR("❌ Format de date invalide. Utilisez DD/MM/YYYY"))
                return
        
        # Trier par date décroissante
        transactions = query.select_related('account', 'category').order_by('-posted_at')
        
        if transactions.count() == 0:
            self.stdout.write(self.style.WARNING("Aucune transaction trouvée"))
            return
        
        self.stdout.write(self.style.SUCCESS(f"\n✅ {transactions.count()} transaction(s) trouvée(s)\n"))
        self.stdout.write("=" * 100)
        
        for tx in transactions[:20]:  # Limiter à 20 pour ne pas surcharger
            category_name = tx.category.name if tx.category else "Sans catégorie"
            
            self.stdout.write(f"""
ID: {tx.id}
Date: {tx.posted_at.strftime('%d/%m/%Y %H:%M')}
Compte: {tx.account.name} (ID: {tx.account.id})
  - Type: {tx.account.type}
  - Provider: {tx.account.provider or 'manuel'}
  - Inclus dans dashboard: {'✓' if tx.account.include_in_dashboard else '✗'}
Montant: {tx.amount} {tx.currency}
Description: {tx.description[:80]}
Catégorie: {category_name}
Contrepartie: {tx.counterparty or 'N/A'}
Solde après: {tx.account_balance or 'N/A'}
""")
            self.stdout.write("-" * 100)
        
        if transactions.count() > 20:
            self.stdout.write(self.style.WARNING(f"\n... et {transactions.count() - 20} autre(s) transaction(s)"))

