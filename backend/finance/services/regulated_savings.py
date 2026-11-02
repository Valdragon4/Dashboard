"""
Livrets réglementés (Livret A, LEP) que la banque n'expose pas en DSP2.

Le solde d'un livret est reconstitué ainsi :
  1. un point de départ saisi à la main (« Recaler le solde » dans Paramètres) ;
  2. les virements du compte courant synchronisé, recopiés sur le livret :
       « … VIREMENT VERS LEP »          -> +montant sur le LEP
       « … VIREMENT VERS LIVRET A »     -> +montant sur le Livret A
     Un retrait vers le compte courant (« VIREMENT VERS COMPTE DE CHEQUES ») ne
     dit pas de quel livret il vient : il est recopié seulement si son motif
     nomme le livret (« LEP », « LIVRET A »), sinon il attend d'être affecté
     dans Paramètres ;
  3. les intérêts, calculés par quinzaines au taux officiel et crédités au
     31 décembre (la banque les verse début janvier).

Règle des quinzaines (Code monétaire et financier) : un dépôt porte intérêt à
partir de la quinzaine suivante (1er ou 16), un retrait cesse d'en porter dès
la quinzaine où il a lieu. Intérêt d'une quinzaine = solde porteur × taux / 24.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, time as dtime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional

import requests
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from finance.models import Account, RegulatedSavingsRate, Transaction

logger = logging.getLogger(__name__)

LIVRET_A = Account.SavingsProduct.LIVRET_A
LEP = Account.SavingsProduct.LEP

# Libellés Hello Bank observés le 2026-11-02.
DEPOSIT_PATTERNS = {
    LEP: re.compile(r"VIREMENT VERS (?:LE )?LEP\b", re.I),
    LIVRET_A: re.compile(r"VIREMENT VERS (?:LE )?LIVRET A\b", re.I),
}
WITHDRAWAL_PATTERN = re.compile(r"CPTE A CPTE RECU.*VIREMENT VERS COMPTE DE CHEQUES", re.I)
# Motif personnalisé à la saisie du retrait (après /MOTIF).
WITHDRAWAL_MOTIF = {
    LEP: re.compile(r"/MOTIF[^/]*\bLEP\b", re.I),
    LIVRET_A: re.compile(r"/MOTIF[^/]*\bLIVRET A\b", re.I),
}

RATE_SOURCES = {
    LIVRET_A: (
        "https://www.service-public.gouv.fr/particuliers/vosdroits/F2365",
        re.compile(r"taux d.int.r.t annuel du livret A est de (\d+(?:,\d+)?) ?%", re.I),
    ),
    LEP: (
        "https://www.service-public.gouv.fr/particuliers/vosdroits/F2367",
        re.compile(r"taux d.int.r.t du LEP est de (\d+(?:,\d+)?) ?%", re.I),
    ),
}

CENT = Decimal("0.01")


# ----------------------------------------------------------------------
# Taux
# ----------------------------------------------------------------------


def current_period_start(today: date) -> date:
    """Les taux réglementés changent le 1er février et le 1er août."""
    if today >= date(today.year, 8, 1):
        return date(today.year, 8, 1)
    if today >= date(today.year, 2, 1):
        return date(today.year, 2, 1)
    return date(today.year - 1, 8, 1)


def rate_on(product: str, day: date) -> Optional[Decimal]:
    row = (
        RegulatedSavingsRate.objects.filter(product=product, valid_from__lte=day)
        .order_by("-valid_from")
        .first()
    )
    return row.rate if row else None


def parse_rate(product: str, page: str) -> Optional[Decimal]:
    _, pattern = RATE_SOURCES[product]
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    match = pattern.search(text)
    if not match:
        return None
    rate = Decimal(match.group(1).replace(",", "."))
    # Garde-fou : un taux réglementé hors de [0 ; 10] % trahit une page mal lue.
    if not (Decimal("0") <= rate <= Decimal("10")):
        return None
    return rate


def fetch_official_rates(today: Optional[date] = None, http=None) -> Dict[str, str]:
    """
    Relève le taux en vigueur de chaque livret et l'enregistre pour la période
    courante (depuis le dernier 1er février / 1er août). Retourne un bilan.
    """
    today = today or timezone.localdate()
    http = http or requests
    valid_from = current_period_start(today)
    report = {}
    for product, (url, _) in RATE_SOURCES.items():
        try:
            resp = http.get(url, timeout=30, headers={"User-Agent": "finance-dashboard/1.0 (usage personnel)"})
            resp.raise_for_status()
            rate = parse_rate(product, resp.text)
        except Exception as e:  # réseau, HTTP
            logger.warning("taux %s : relevé impossible (%s)", product, e)
            report[product] = f"erreur : {e}"
            continue
        if rate is None:
            logger.warning("taux %s : phrase du taux introuvable sur %s", product, url)
            report[product] = "taux introuvable sur la page"
            continue
        obj, created = RegulatedSavingsRate.objects.update_or_create(
            product=product, valid_from=valid_from, defaults={"rate": rate, "source": url}
        )
        report[product] = f"{rate} % depuis {valid_from}" + (" (nouveau)" if created else "")
    return report


# ----------------------------------------------------------------------
# Recopie des virements
# ----------------------------------------------------------------------


def _savings_accounts(owner) -> Dict[str, Account]:
    return {
        a.savings_product: a
        for a in Account.objects.filter(owner=owner).exclude(savings_product="")
    }


def _mirror(source: Transaction, target: Account, amount: Decimal) -> Transaction:
    """
    Recopie `source` sur le livret. Une opération antérieure au solde saisi y
    est déjà comprise : on la recopie quand même (historique du solde, donc
    intérêts plus justes) en compensant le point de départ, pour que le solde
    saisi reste exact à sa date.
    """
    mirror, created = Transaction.objects.get_or_create(
        account=target,
        raw__mirror_of=source.id,
        defaults={
            "posted_at": source.posted_at,
            "amount": amount,
            "currency": target.currency or "EUR",
            "description": source.description,
            "is_transfer": True,
            "raw": {"source": "savings_mirror", "mirror_of": source.id, "transaction_id": f"mirror:{source.id}"},
        },
    )
    if created and target.balance_snapshot_date and source.posted_at.date() <= target.balance_snapshot_date:
        target.refresh_from_db(fields=["initial_balance"])
        target.initial_balance = Decimal(target.initial_balance or 0) - Decimal(amount)
        target.save(update_fields=["initial_balance"])
    if not source.is_transfer or (source.raw or {}).get("savings_pending"):
        raw = dict(source.raw or {})
        raw.pop("savings_pending", None)
        raw["savings_target"] = target.id
        Transaction.objects.filter(id=source.id).update(is_transfer=True, raw=raw)
    return mirror


def classify(tx: Transaction) -> Optional[str]:
    """Livret visé par une opération du compte courant, '?' si retrait non attribuable."""
    label = tx.description or ""
    if tx.amount < 0:
        for product, pattern in DEPOSIT_PATTERNS.items():
            if pattern.search(label):
                return product
        return None
    if WITHDRAWAL_PATTERN.search(label):
        for product, pattern in WITHDRAWAL_MOTIF.items():
            if pattern.search(label):
                return product
        return "?"
    return None


def mirror_transfers(checking: Account) -> Dict[str, int]:
    """
    Recopie sur les livrets tous les virements du compte courant (voir _mirror
    pour ceux antérieurs au solde saisi). Idempotent.
    """
    targets = _savings_accounts(checking.owner)
    stats = {"recopies": 0, "a_affecter": 0}
    if not targets:
        return stats
    for tx in Transaction.objects.filter(account=checking).order_by("posted_at", "id"):
        product = classify(tx)
        if product is None:
            continue
        if product == "?":
            if not (tx.raw or {}).get("savings_target") and not (tx.raw or {}).get("savings_pending"):
                raw = dict(tx.raw or {})
                raw["savings_pending"] = True
                Transaction.objects.filter(id=tx.id).update(raw=raw, is_transfer=True)
                stats["a_affecter"] += 1
            continue
        target = targets.get(product)
        if target is None or Transaction.objects.filter(account=target, raw__mirror_of=tx.id).exists():
            continue
        with db_transaction.atomic():
            _mirror(tx, target, -tx.amount)
        stats["recopies"] += 1
    return stats


def pending_withdrawals(owner) -> List[Transaction]:
    return list(
        Transaction.objects.filter(account__owner=owner, raw__savings_pending=True)
        .select_related("account")
        .order_by("-posted_at")
    )


def assign_withdrawal(tx: Transaction, target: Account) -> Transaction:
    if tx.amount <= 0:
        raise ValueError("Seul un retrait (crédit du compte courant) s'affecte à un livret.")
    with db_transaction.atomic():
        return _mirror(tx, target, -tx.amount)


def dismiss_withdrawal(tx: Transaction) -> None:
    """Ce crédit ne vient pas d'un livret : on le rend aux statistiques."""
    raw = dict(tx.raw or {})
    raw.pop("savings_pending", None)
    raw["savings_target"] = None
    Transaction.objects.filter(id=tx.id).update(raw=raw, is_transfer=False)


# ----------------------------------------------------------------------
# Solde et intérêts
# ----------------------------------------------------------------------


def balance_on(account: Account, day: date) -> Decimal:
    """Solde en fin de journée `day`."""
    end = timezone.make_aware(datetime.combine(day, dtime.max))
    total = Transaction.objects.filter(account=account, posted_at__lte=end).aggregate(t=Sum("amount"))["t"]
    return Decimal(account.initial_balance or 0) + Decimal(total or 0)


def set_balance(account: Account, amount: Decimal, day: Optional[date] = None) -> None:
    """Recale le livret : solde réel `amount` en fin de journée `day`."""
    day = day or timezone.localdate()
    end = timezone.make_aware(datetime.combine(day, dtime.max))
    total = Transaction.objects.filter(account=account, posted_at__lte=end).aggregate(t=Sum("amount"))["t"] or 0
    account.initial_balance = Decimal(amount) - Decimal(total)
    account.balance_snapshot_date = day
    account.save(update_fields=["initial_balance", "balance_snapshot_date"])


def _fortnights(year: int, until: Optional[date] = None) -> Iterable[tuple]:
    for month in range(1, 13):
        first = date(year, month, 1)
        mid = date(year, month, 16)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        for start, end in ((first, mid - timedelta(days=1)), (mid, nxt - timedelta(days=1))):
            if until and start > until:
                return
            yield start, end


def compute_interest(account: Account, year: int, until: Optional[date] = None) -> Dict:
    """
    Intérêts de l'année `year` par quinzaines (jusqu'à `until` pour un cumul en
    cours d'année). Exclut les intérêts déjà crédités par ce calcul.
    """
    product = account.savings_product
    total = Decimal("0")
    missing_rates = set()
    for start, end in _fortnights(year, until):
        start_dt = timezone.make_aware(datetime.combine(start, dtime.min))
        end_dt = timezone.make_aware(datetime.combine(end, dtime.max))
        # Solde de la veille : dépôts de la quinzaine exclus (valeur à la suivante),
        # intérêts crédités au 31/12 précédent inclus.
        opening = balance_on(account, start - timedelta(days=1))
        withdrawals = Transaction.objects.filter(
            account=account, posted_at__gte=start_dt, posted_at__lte=end_dt, amount__lt=0
        ).aggregate(t=Sum("amount"))["t"] or 0
        bearing = max(Decimal("0"), opening + Decimal(withdrawals))
        rate = rate_on(product, start)
        if rate is None:
            missing_rates.add(start.isoformat())
            continue
        total += bearing * rate / Decimal("100") / Decimal("24")
    return {
        "amount": total.quantize(CENT, rounding=ROUND_HALF_UP),
        "missing_rates": sorted(missing_rates),
    }


def credit_yearly_interest(year: int) -> List[str]:
    """Crédite, au 31 décembre, les intérêts de l'année sur chaque livret. Idempotent."""
    out = []
    for account in Account.objects.exclude(savings_product=""):
        if Transaction.objects.filter(account=account, raw__source="savings_interest", raw__year=year).exists():
            out.append(f"{account.name} : déjà crédités")
            continue
        result = compute_interest(account, year)
        if result["missing_rates"]:
            out.append(f"{account.name} : taux manquants {result['missing_rates'][:3]}… — rien crédité")
            continue
        posted = timezone.make_aware(datetime.combine(date(year, 12, 31), dtime(23, 0)))
        Transaction.objects.create(
            account=account,
            posted_at=posted,
            amount=result["amount"],
            currency=account.currency or "EUR",
            description=f"Intérêts {year} (calculés)",
            raw={"source": "savings_interest", "year": year, "transaction_id": f"interest:{account.id}:{year}"},
        )
        out.append(f"{account.name} : {result['amount']} €")
    return out
