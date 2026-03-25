from django import template
from decimal import Decimal

register = template.Library()


@register.filter
def mul(value, arg):
    """Multiplie deux valeurs"""
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except (ValueError, TypeError, ArithmeticError):
        return 0


@register.filter
def get_item(dictionary, key):
    """Récupère un item d'un dictionnaire"""
    return dictionary.get(key, 0)


@register.filter
def timesince_fr(value):
    """
    Formate une date en format relatif français.
    
    Exemples:
    - "Il y a 2 heures"
    - "Aujourd'hui à 14:30"
    - "Hier à 10:15"
    - "05/01/2025 à 14:30" (si > 7 jours)
    - "Jamais" (si None)
    """
    if not value:
        return "Jamais"
    
    from django.utils import timezone
    from datetime import timedelta
    
    now = timezone.now()
    
    # S'assurer que value est timezone-aware
    if not timezone.is_aware(value):
        value = timezone.make_aware(value)
    
    diff = now - value
    
    # Moins d'une heure
    if diff < timedelta(hours=1):
        minutes = int(diff.total_seconds() / 60)
        if minutes < 1:
            return "À l'instant"
        return f"Il y a {minutes} minute{'s' if minutes > 1 else ''}"
    
    # Moins de 24 heures
    if diff < timedelta(days=1):
        hours = int(diff.total_seconds() / 3600)
        return f"Il y a {hours} heure{'s' if hours > 1 else ''}"
    
    # Aujourd'hui
    if value.date() == now.date():
        return f"Aujourd'hui à {value.strftime('%H:%M')}"
    
    # Hier
    yesterday = now.date() - timedelta(days=1)
    if value.date() == yesterday:
        return f"Hier à {value.strftime('%H:%M')}"
    
    # Moins de 7 jours
    if diff < timedelta(days=7):
        days = diff.days
        return f"Il y a {days} jour{'s' if days > 1 else ''}"
    
    # Plus de 7 jours : format complet
    return f"{value.strftime('%d/%m/%Y')} à {value.strftime('%H:%M')}"


@register.filter
def format_amount_6digits(value):
    """Formate un montant avec un maximum de 7 chiffres au total, en affichant le nombre de chiffres juste suffisant."""
    if value is None:
        return "0,00"
    
    try:
        amount = float(value)
        abs_amount = abs(amount)
        
        # Compter le nombre de chiffres avant la virgule
        int_part = int(abs_amount)
        int_digits = len(str(int_part))
        
        # Déterminer le nombre de décimales à afficher pour avoir max 7 chiffres au total
        # On affiche le nombre de décimales nécessaire, mais pas plus que nécessaire
        if int_digits >= 7:
            # 7 chiffres ou plus : pas de décimales
            formatted = f"{abs_amount:.0f}"
        elif int_digits == 6:
            # 6 chiffres : 1 décimale max
            formatted = f"{abs_amount:.1f}"
        elif int_digits == 5:
            # 5 chiffres : 2 décimales max
            formatted = f"{abs_amount:.2f}"
        elif int_digits == 4:
            # 4 chiffres : 3 décimales max
            formatted = f"{abs_amount:.3f}"
        elif int_digits == 3:
            # 3 chiffres : 4 décimales max
            formatted = f"{abs_amount:.4f}"
        elif int_digits == 2:
            # 2 chiffres : 5 décimales max
            formatted = f"{abs_amount:.5f}"
        elif int_digits == 1:
            # 1 chiffre : 6 décimales max
            formatted = f"{abs_amount:.6f}"
        else:
            # 0 chiffre : 7 décimales max
            formatted = f"{abs_amount:.7f}"
        
        # Remplacer le point par une virgule (format français)
        formatted = formatted.replace(".", ",")
        
        # Supprimer les zéros inutiles à la fin
        # Si le nombre est un entier, ne pas afficher de décimales
        if "," in formatted:
            parts = formatted.split(",")
            if len(parts) == 2:
                decimal_part = parts[1]
                # Supprimer les zéros à la fin
                decimal_part = decimal_part.rstrip("0")
                
                # Si toutes les décimales sont des zéros, ne pas afficher de décimales
                if len(decimal_part) == 0:
                    formatted = parts[0]
                else:
                    # Garder les décimales significatives (sans zéros inutiles)
                    formatted = parts[0] + "," + decimal_part
        
        # Ajouter le signe négatif si nécessaire
        if amount < 0:
            formatted = f"-{formatted}"
        
        return formatted
    except (ValueError, TypeError):
        return str(value)

