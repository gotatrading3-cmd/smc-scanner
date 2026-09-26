"""
signal_market.py - Etat REEL du marche pour les posts publics (point du matin, New York, bilan, week-end).

Tout est calcule sur les vraies donnees journalieres des paires de devises (aucun chiffre invente) :
- force de chaque devise sur N jours (moyenne des variations des paires ou elle apparait)
- tendance de fond journaliere par paire (EMA50 / EMA200)
"""
from __future__ import annotations
from typing import Dict, List, Optional

from signal_data import UNIVERSE, available_ids, get_rates

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
# nom lisible + article (pour des phrases naturelles en francais)
NAMES = {"USD": "le dollar", "EUR": "l'euro", "GBP": "la livre", "JPY": "le yen", "AUD": "le dollar australien",
         "CAD": "le dollar canadien", "CHF": "le franc suisse", "NZD": "le dollar néo-zélandais"}


def snapshot(ids: Optional[List[str]] = None, days: int = 5) -> dict:
    """Retourne {'ranked': [(devise, %)...], 'up': n, 'down': n, 'n_pairs': n, 'days': days}. Ne plante jamais."""
    ids = [i for i in (ids or available_ids()) if UNIVERSE[i]["cls"] == "fx"]
    strength: Dict[str, List[float]] = {c: [] for c in CURRENCIES}
    up = down = n = 0
    for sid in ids:
        try:
            d = get_rates(sid, "1d", 320)
            if d is None or len(d) < 210:
                continue
            c = d["close"]
            ret = float(c.iloc[-1] / c.iloc[-1 - days] - 1.0) * 100.0
            base, quote = sid[:3], sid[3:]
            if base in strength:
                strength[base].append(ret)
            if quote in strength:
                strength[quote].append(-ret)
            ef = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
            es = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
            last = float(c.iloc[-1])
            n += 1
            if ef > es and last > es:
                up += 1
            elif ef < es and last < es:
                down += 1
        except Exception:
            continue
    avg = {k: (sum(v) / len(v) if v else 0.0) for k, v in strength.items()}
    ranked = sorted(avg.items(), key=lambda kv: kv[1], reverse=True)
    return {"ranked": ranked, "up": up, "down": down, "n_pairs": n, "days": days}


def pct(x: float) -> str:
    return f"{x:+.1f} %".replace(".", ",")
