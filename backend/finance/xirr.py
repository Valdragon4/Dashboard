from __future__ import annotations

from datetime import date
from typing import Sequence


def _newton(amounts, years, guess: float) -> float | None:
    rate = guess
    for _ in range(500):
        try:
            f = sum(amt / (1.0 + rate) ** yr for amt, yr in zip(amounts, years))
            df = sum(-yr * amt / (1.0 + rate) ** (yr + 1) for amt, yr in zip(amounts, years))
        except (ZeroDivisionError, OverflowError):
            return None
        if df == 0:
            break
        new_rate = rate - f / df
        if not (-0.9999 < new_rate < 1000):
            return None
        if abs(new_rate - rate) < 1e-7:
            return new_rate
        rate = new_rate
    return None


def xirr(cash_flows: Sequence[tuple[date, float]], guess: float = 0.1) -> float | None:
    """
    Calcule le XIRR (TRI annualisé pour des flux à dates irrégulières).

    cash_flows : liste de (date, montant)
        montant négatif = décaissement (investissement)
        montant positif = encaissement (dividende, valorisation terminale)

    Retourne le taux annualisé (ex. 0.127 = 12.7%) ou None si pas de convergence.
    Essaie plusieurs points de départ pour couvrir les cas extrêmes (ex. -97% annualisé).
    """
    if len(cash_flows) < 2:
        return None

    dates, amounts = zip(*sorted(cash_flows, key=lambda x: x[0]))
    min_date = min(dates)
    years = [(d - min_date).days / 365.0 for d in dates]

    has_pos = any(a > 0 for a in amounts)
    has_neg = any(a < 0 for a in amounts)
    if not has_pos or not has_neg:
        return None

    # Multi-start : couvre les cas normaux et les pertes extrêmes (~-97% annualisé)
    for start in (guess, -0.95, -0.5, 0.5, 2.0, -0.1, -0.99):
        result = _newton(amounts, years, start)
        if result is not None:
            return result

    return None
