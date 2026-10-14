"""
signal_events.py - NFP (emploi americain) : calendrier officiel, pause de securite autour de la publication, messages d'information pour le
groupe VIP et le groupe public, lecture du resultat et de la reaction du marche.

CE QUE FAIT LE ROBOT LES JOURS DE NFP
  veille au soir   : message « demain NFP » (ce que c'est, a quelle heure, comment il reagit d'habitude, ce que fait le robot)
  1 h avant        : rappel avec la prevision et le chiffre precedent ; le VIP recoit en plus les zones H4 proches du prix
  T-30 min -> T+30 : PAUSE de securite : aucun nouveau signal n'est cherche ni publie (les ecarts achat / vente s'elargissent) ; les trades deja
                     ouverts continuent d'etre suivis normalement
  des la publication : le chiffre reel (emplois, chomage, salaires) et la reaction des premieres minutes
  T+30 min         : reprise + lecture du marche ; le VIP recoit les zones H4 a surveiller

CE QU'IL NE FAIT PAS : trader le pic de la publication lui-meme. Mesure sur les 43 NFP de 2023 a 2026 : l'heure de la publication bouge 2,3 a 3 fois plus
qu'une heure normale, et la direction de cette premiere heure ne prevoit pas la suite (37 a 59 % de continuation selon l'instrument = pile ou face).
Les signaux normaux reprennent apres la pause.

Sources gratuites : dates officielles du BLS (08:30, heure de New York) ; prevision / precedent : flux hebdomadaire du calendrier economique
(sans cle) ; chiffre reel : API publique du BLS (sans cle) ; reaction : bougies de 5 minutes Yahoo (les memes que le robot).
Tout est facultatif : si une source ne repond pas, le message part sans ce chiffre (jamais un chiffre invente).

Reglages (control.json de la branche data, ou variables d'environnement) :
  nfp_mode     : "preview" (defaut : les messages vont dans TON chat prive, etiquetes VIP / public), "live" (groupes), "off" (rien du tout)
  nfp_blackout : true par defaut (pause des nouveaux signaux autour de la publication)

    python signal_events.py --replay 2026-10-02        # affiche la serie de messages d'une publication passee
    python signal_events.py --replay 2026-10-02 --send # ... et l'envoie en apercu dans ton chat prive
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SIGNALS_DATA_DIR") or DIR)
CONTROL_FILE = DATA_DIR / "control.json"

# date de publication -> mois de reference (calendrier officiel du BLS ; 08:30 heure de New York). A completer chaque annee.
NFP_RELEASES: Dict[str, str] = {
    "2026-01-09": "2025-12", "2026-02-11": "2026-01", "2026-03-06": "2026-02", "2026-04-03": "2026-03", "2026-05-08": "2026-04",
    "2026-06-05": "2026-05", "2026-07-02": "2026-06", "2026-08-07": "2026-07", "2026-09-04": "2026-08", "2026-10-02": "2026-09",
    "2026-11-06": "2026-10", "2026-12-04": "2026-11",
    "2027-01-08": "2026-12", "2027-02-05": "2027-01", "2027-03-05": "2027-02", "2027-04-02": "2027-03", "2027-05-07": "2027-04",
    "2027-06-04": "2027-05", "2027-07-02": "2027-06", "2027-08-06": "2027-07", "2027-09-03": "2027-08", "2027-10-08": "2027-09",
    "2027-11-05": "2027-10", "2027-12-03": "2027-11",
}
BEFORE_H = 22.0                  # le message de la veille part a partir de 22 h avant la publication
T60 = (70, 30)                   # rappel : de T-70 min a T-30 min
BLACKOUT = (30, 30)              # pause des nouveaux signaux : de T-30 min a T+30 min
RESULT_UNTIL = 90                # le chiffre reel est attendu jusqu'a T+90 min
RESUME_UNTIL_H = 3.0             # le message de reprise peut partir jusqu'a T+3 h
VIP_ONLY_H = 4.0                 # signaux de trade pris dans la foulee du NFP (jusqu'a T+4 h) : groupe VIP uniquement
# Mesure sur 43 publications (2023-2026), historique H1 : amplitude mediane de l'heure de la publication et rapport a une heure normale
STATS = {"EURUSD": (57, 2.8), "GBPUSD": (63, 2.7), "USDJPY": (92, 2.8), "XAUUSD": (245, 2.5), "US100": (141, 2.3)}
WATCH_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD", "US100")
REACTION_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")
USD_UP = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "USDJPY": 1, "USDCAD": 1, "USDCHF": 1}      # sens du dollar quand la paire monte

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
BLS_API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SERIES = {"payrolls": "CES0000000001", "unemployment": "LNS14000000", "earnings": "CES0500000003"}

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


# ------------------------------------------------------------------ reglages
def control() -> dict:
    try:
        d = json.loads(CONTROL_FILE.read_text(encoding="utf-8-sig"))         # (tolere un fichier enregistre avec BOM par un editeur Windows)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def nfp_mode() -> str:
    c = control()
    raw = str(c["nfp_mode"] if "nfp_mode" in c else os.environ.get("SIGNALS_NFP_MODE", "")).strip().lower()
    return raw if raw in ("off", "live") else "preview"


def blackout_enabled() -> bool:
    c = control()
    return str(c.get("nfp_blackout", True)).strip().lower() not in ("false", "0", "off", "non", "no")


# ------------------------------------------------------------------ calendrier
def release_utc(date: str) -> pd.Timestamp:
    """Heure de publication (UTC, naif) : 08:30 a New York, heure d'ete / d'hiver comprise."""
    return pd.Timestamp(f"{date} 08:30", tz="America/New_York").tz_convert("UTC").tz_localize(None)


def window(now: pd.Timestamp) -> Optional[dict]:
    """Publication NFP dont la fenetre d'interet (de T-22 h a T+3 h) couvre `now`, sinon None."""
    for d, ref in NFP_RELEASES.items():
        t = release_utc(d)
        if t - pd.Timedelta(hours=BEFORE_H) <= now < t + pd.Timedelta(hours=RESUME_UNTIL_H):
            return {"date": d, "ref": ref, "release": t, "minutes": (now - t).total_seconds() / 60.0}
    return None


def next_release(now: pd.Timestamp) -> Optional[dict]:
    for d, ref in sorted(NFP_RELEASES.items()):
        t = release_utc(d)
        if t > now:
            return {"date": d, "ref": ref, "release": t}
    return None


def in_blackout(now: pd.Timestamp) -> bool:
    """Vrai de T-30 min a T+30 min autour d'une publication NFP : on ne cherche ni ne publie de nouveau signal."""
    if nfp_mode() == "off" or not blackout_enabled():
        return False
    w = window(now)
    return bool(w) and -BLACKOUT[0] <= w["minutes"] < BLACKOUT[1]


def vip_only(now: pd.Timestamp) -> bool:
    """Vrai de la publication NFP jusqu'a T+4 h : les signaux de trade pris dans la foulee du NFP sont reserves au groupe VIP (demande du proprietaire,
    2026-10-14) ; le groupe public continue de recevoir les messages d'information. Aucun signal n'est publie avant T+30 min (pause de securite)."""
    if nfp_mode() == "off":
        return False
    for d in NFP_RELEASES:
        t = release_utc(d)
        if t <= now < t + pd.Timedelta(hours=VIP_ONLY_H):
            return True
    return False


def due_kinds(st: dict, now: pd.Timestamp) -> List[str]:
    """Messages a envoyer maintenant (chacun une seule fois par publication)."""
    w = window(now)
    if not w:
        return []
    done = ((st.get("events") or {}).get("nfp") or {}).get(w["date"], {})
    m, out = w["minutes"], []
    if -BEFORE_H * 60 <= m < -T60[0] and not (done.get("pre_vip") and done.get("pre_public")):
        out.append("pre")
    if -T60[0] <= m < -T60[1] and not (done.get("t60_vip") and done.get("t60_public")):
        out.append("t60")
    if 0 <= m < RESULT_UNTIL and not (done.get("result_vip") and done.get("result_public")):
        out.append("result")
    if BLACKOUT[1] <= m < RESUME_UNTIL_H * 60 and not (done.get("resume_vip") and done.get("resume_public")):
        out.append("resume")
    return out


# ------------------------------------------------------------------ donnees (toutes facultatives)
def _http_json(url: str, payload: Optional[dict] = None, timeout: int = 20):
    import requests
    h = {"User-Agent": "Mozilla/5.0 (GOTA signals)"}
    r = requests.post(url, json=payload, headers=h, timeout=timeout) if payload is not None else requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _num(txt) -> Optional[float]:
    """'70K' -> 70.0 ; '4.1%' -> 4.1 ; '-12K' -> -12.0 ; autre -> None."""
    try:
        s = str(txt).strip().replace("%", "").replace(",", "")
        mult = 1.0
        if s[-1:] in ("K", "k"):
            s = s[:-1]
        elif s[-1:] in ("M", "m"):
            s, mult = s[:-1], 1000.0
        return float(s) * mult
    except Exception:
        return None


def parse_consensus(events: list, release: pd.Timestamp) -> dict:
    """Prevision / precedent de l'emploi, du chomage et des salaires pour la publication `release` (UTC naif), depuis le flux du calendrier."""
    wanted = {"Non-Farm Employment Change": "payrolls", "Unemployment Rate": "unemployment", "Average Hourly Earnings m/m": "earnings"}
    out: dict = {}
    for e in events or []:
        try:
            if e.get("country") != "USD" or e.get("title") not in wanted:
                continue
            t = pd.Timestamp(e["date"]).tz_convert("UTC").tz_localize(None)
            if abs((t - release).total_seconds()) > 3600:
                continue
            out[wanted[e["title"]]] = {"forecast": _num(e.get("forecast")), "previous": _num(e.get("previous"))}
        except Exception:
            continue
    return out


def fetch_consensus(release: pd.Timestamp) -> dict:
    try:
        return parse_consensus(_http_json(FEED), release)
    except Exception:
        return {}


def parse_actual(payload: dict, ref: str) -> Optional[dict]:
    """Chiffres reels pour le mois de reference `ref` ('2026-09'), depuis la reponse de l'API du BLS. None tant que ce mois n'y figure pas."""
    y, m = int(ref[:4]), int(ref[5:7])
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    ppy, ppm = (py, pm - 1) if pm > 1 else (py - 1, 12)

    def series(code):
        for s in (payload.get("Results") or {}).get("series", []):
            if s.get("seriesID") == code:
                return {(int(d["year"]), int(d["period"][1:])): float(d["value"]) for d in s.get("data", []) if str(d.get("period", "")).startswith("M") and d.get("value") not in ("-", "")}
        return {}

    pay, unr, ahe = series(SERIES["payrolls"]), series(SERIES["unemployment"]), series(SERIES["earnings"])
    if (y, m) not in pay or (py, pm) not in pay:
        return None
    out = {"change_k": pay[(y, m)] - pay[(py, pm)]}
    if (ppy, ppm) in pay:
        out["previous_change_k"] = pay[(py, pm)] - pay[(ppy, ppm)]
    if (y, m) in unr:
        out["unemployment"] = unr[(y, m)]
        if (py, pm) in unr:
            out["unemployment_prev"] = unr[(py, pm)]
    if (y, m) in ahe and (py, pm) in ahe and ahe[(py, pm)] > 0:
        out["earnings_mom"] = (ahe[(y, m)] / ahe[(py, pm)] - 1.0) * 100.0
    return out


def fetch_actual(ref: str) -> Optional[dict]:
    try:
        y = int(ref[:4])
        j = _http_json(BLS_API, {"seriesid": list(SERIES.values()), "startyear": str(y - 1), "endyear": str(y)})
        if j.get("status") != "REQUEST_SUCCEEDED":
            return None
        return parse_actual(j, ref)
    except Exception:
        return None


def reaction(release: pd.Timestamp, horizon_min: float = 15, symbols=REACTION_SYMBOLS, get_rates: Optional[Callable] = None,
             count: int = 600) -> dict:
    """Mouvement des paires entre la derniere bougie de 5 minutes CLOTUREE AVANT la publication et la derniere cloturee dans les `horizon_min`
    minutes qui suivent, en pips (ou points) : {'EURUSD': (+35.0, minutes mesurees)}. Seulement des bougies reelles ; rien si elles manquent."""
    from signal_data import get_rates as _gr, symbol_meta
    gr = get_rates or _gr
    out = {}
    lim = release + pd.Timedelta(minutes=horizon_min)
    for s in symbols:
        try:
            df = gr(s, "5m", count)
            meta = symbol_meta(s) or {}
            if df is None or not meta:
                continue
            close_t = df.index + pd.Timedelta(minutes=5)
            before = df[close_t <= release]
            after = df[(close_t > release) & (close_t <= lim)]
            if not len(before) or not len(after):
                continue
            p0, p1 = float(before["close"].iloc[-1]), float(after["close"].iloc[-1])
            unit = float(meta["point"]) * 10 if s not in ("US100", "US30", "US500", "BTCUSD") else 1.0
            out[s] = ((p1 - p0) / unit, (after.index[-1] + pd.Timedelta(minutes=5) - release).total_seconds() / 60.0)
        except Exception:
            continue
    return out


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    h, l, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    tr = np.maximum.reduce([h - l, np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])])
    return float(pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().iloc[-1])


def zones_to_watch(now: pd.Timestamp, symbols=WATCH_SYMBOLS, max_atr: float = 1.5, get_rates: Optional[Callable] = None) -> List[dict]:
    """Zones H4 encore actives, dans le sens de la tendance journaliere, proches du prix (moins de `max_atr` ATR H4). Calculees a la demande
    (jamais enregistrees dans l'etat public). Les plus proches d'abord."""
    from signal_data import get_rates as _gr, symbol_meta, UNIVERSE
    from signal_engine import htf_bias, htf_zones_of
    gr = get_rates or _gr
    out = []
    for s in symbols:
        try:
            meta = symbol_meta(s)
            if not meta or s not in UNIVERSE:
                continue
            d4, d1 = gr(s, "4h", 1000), gr(s, "1d", 800)
            if d4 is None or d1 is None or len(d4) < 270 or len(d1) < 270:
                continue
            hz = htf_zones_of(d4, 240, "H4", meta["point"], UNIVERSE[s]["cls"])
            d = int(htf_bias(d1, d4.index, 240, htf_minutes=1440)[-1])
            if hz is None or d == 0:
                continue
            price, atr = float(d4["close"].iloc[-1]), _atr(d4)
            t_ns = int((d4.index[-1] + pd.Timedelta(minutes=240)).value)
            best = None
            for z in hz.active(d, t_ns):
                dist = max(0.0, (price - z["hi"]) if d == 1 else (z["lo"] - price))
                if dist <= max_atr * atr and (best is None or dist < best[0]):
                    best = (dist, z)
            if best:
                out.append({"display": s, "dir": d, "kind": best[1]["kind"], "lo": best[1]["lo"], "hi": best[1]["hi"],
                            "dist_atr": best[0] / atr, "price": price, "digits": int(meta["digits"])})
        except Exception:
            continue
    return sorted(out, key=lambda z: z["dist_atr"])


# ------------------------------------------------------------------ mise en forme
def hhmm(ts: pd.Timestamp) -> str:
    return f"{ts.hour}h{ts.minute:02d}"


def fr_date(ts: pd.Timestamp) -> str:
    return f"{_JOURS[ts.weekday()]} {ts.day} {_MOIS[ts.month - 1]}"


def fmt_k(k: Optional[float]) -> str:
    """Emplois, donnes en MILLIERS : 29 -> '+29 000' ; -12 -> '−12 000'."""
    if k is None:
        return "?"
    return f"{k * 1000:+,.0f}".replace(",", " ").replace("-", "−")


def fmt_pct(x: Optional[float], plus: bool = False) -> str:
    if x is None:
        return "?"
    return (f"{x:+.1f}" if plus else f"{x:.1f}").replace(".", ",") + " %"


def fmt_pct2(x: Optional[float]) -> str:
    return "?" if x is None else f"{x:+.2f}".replace(".", ",") + " %"


def _p(x: float, digits: int) -> str:
    return f"{x:,.{digits}f}".replace(",", " ")


def _pips(x: float) -> str:
    return f"{x:+.0f}".replace("-", "−")


def _explainer() -> str:
    return ("Le NFP, c'est le nombre d'emplois créés aux États-Unis le mois dernier (hors agriculture). Un chiffre plus fort que prévu soutient en général le dollar, "
            "un chiffre plus faible le pénalise — en général seulement : il arrive que le marché fasse l'inverse.")


def _short_stats() -> str:
    """Version courte pour le groupe public (petits messages)."""
    return f"Sur l'euro/dollar, l'heure de la publication bouge en moyenne {str(STATS['EURUSD'][1]).replace('.', ',')} fois plus qu'une heure normale."


def _stats_line() -> str:
    a, r = STATS["EURUSD"]
    g, rg = STATS["XAUUSD"]
    return (f"Sur plus de trois ans, l'heure de la publication a bougé en moyenne {str(r).replace('.', ',')} fois plus qu'une heure normale sur l'euro/dollar "
            f"(environ {a} pips d'amplitude) et {str(rg).replace('.', ',')} fois plus sur l'or. La direction de cette première heure n'a pas permis de deviner la suite "
            f"(à peu près une fois sur deux), et dans environ un cas sur quatre la moitié du mouvement est effacée dans l'heure d'après.")


def _consensus_line(c: dict) -> str:
    p = c.get("payrolls") or {}
    parts = []
    if p.get("forecast") is not None:
        parts.append(f"prévision {fmt_k(p['forecast'])} emplois")
    if p.get("previous") is not None:
        parts.append(f"précédent {fmt_k(p['previous'])}")
    u = c.get("unemployment") or {}
    if u.get("forecast") is not None:
        parts.append(f"chômage attendu à {fmt_pct(u['forecast'])}")
    return ("Ce que le marché attend : " + ", ".join(parts) + ".") if parts else ""


def usd_read(react: dict) -> str:
    """Lecture du dollar d'apres les paires mesurees : 'en hausse', 'en baisse' ou '' si la reaction est partagee."""
    votes = [USD_UP[s] * (1 if mv > 0 else -1) for s, (mv, _) in react.items() if s in USD_UP and abs(mv) >= 3]
    if len(votes) >= 2 and all(v == votes[0] for v in votes):
        return "en hausse" if votes[0] > 0 else "en baisse"
    return ""


def _reaction_line(react: dict) -> str:
    names = {"EURUSD": "l'euro/dollar", "GBPUSD": "la livre/dollar", "USDJPY": "le dollar/yen", "XAUUSD": "l'or"}
    bits = [f"{names.get(s, s)} {_pips(mv)} pips" for s, (mv, _) in react.items() if s in names]
    if not bits:
        return ""
    mins = max(m for _, m in react.values())
    d = usd_read(react)
    return (f"Réaction depuis la publication ({mins:.0f} min) : " + ", ".join(bits) + ". " + (f"Le dollar est {d}." if d else "La réaction du dollar est partagée pour l'instant."))


def _watch_lines(zs: List[dict], n: int = 5) -> str:
    out = []
    for z in zs[:n]:
        kind = "Order Block" if z["kind"] == "OB" else "Fair Value Gap"
        out.append(f"• {z['display']} — {'achat' if z['dir'] == 1 else 'vente'} : {kind} H4 {_p(z['lo'], z['digits'])} – {_p(z['hi'], z['digits'])} "
                   f"(prix à {z['dist_atr']:.1f} ATR)")
    return "\n".join(out)


def build(kind: str, st: dict, w: dict, consensus: Optional[dict] = None, actual: Optional[dict] = None, react: Optional[dict] = None,
          zones: Optional[List[dict]] = None) -> Optional[Dict[str, str]]:
    """Textes {'vip': ..., 'public': ...} d'un message. None si le message n'a pas lieu d'etre (ex. chiffre reel pas encore publie).
    Voix du trader (comme signal_content), horaires en GMT, jamais de promesse, jamais le detail des zones dans le public."""
    from signal_content import pick
    t = w["release"]
    consensus = consensus or {}
    heure = hhmm(t) + " GMT"
    pause_a, pause_b = hhmm(t - pd.Timedelta(minutes=BLACKOUT[0])), hhmm(t + pd.Timedelta(minutes=BLACKOUT[1]))
    plan = (f"Mon plan : aucun nouveau trade de {pause_a} à {pause_b} GMT. À ce moment-là, les écarts entre prix d'achat et de vente s'élargissent et "
            f"ça part dans tous les sens ; je préfère attendre que ça se stabilise. Les trades déjà ouverts restent suivis normalement.")
    cons = _consensus_line(consensus)
    if kind == "pre":
        # petit message pour le public ; le VIP recoit la version complete (mesures detaillees, zones H4, conseil pour les trades ouverts)
        tmpl = pick(st, "nfp_pre", [
            f"🗓️ {fr_date(t).capitalize()}, c'est NFP : les chiffres de l'emploi américain, à {heure}.\n\n{{body}}\n\nJe vous donne le chiffre dès qu'il tombe, puis ma lecture 👀",
            f"📅 Rendez-vous important {fr_date(t)} : le NFP (emploi américain) tombe à {heure}.\n\n{{body}}\n\nJe reste devant mes écrans et je vous dis tout ici.",
        ])
        short = f"{_explainer()} {_short_stats()}\n\n{plan}"
        full = f"{_explainer()}\n\n{_stats_line()}\n\n{plan}"
        pub = tmpl.replace("{body}", short) + ("\n\n" + cons if cons else "")
        vip = tmpl.replace("{body}", full) + ("\n\n" + cons if cons else "") + ("\n\nZones H4 proches du prix en ce moment :\n" + _watch_lines(zones) if zones else "") + \
            "\n\nSi tu as un trade ouvert : pense à le sécuriser ou à réduire avant la publication."
        return {"vip": vip, "public": pub}
    if kind == "t60":
        pub = pick(st, "nfp_t60", [
            f"⏳ Dans environ une heure : NFP à {heure}. {cons}".rstrip() + f"\n\n{plan}",
            f"🔔 NFP dans une heure ({heure}). {cons}".rstrip() + f"\n\n{plan}",
        ])
        vip = pub + ("\n\nZones H4 à surveiller :\n" + _watch_lines(zones) if zones else "") + \
            "\n\nAprès la pause, je reprends mes analyses normalement : tout signal arrive ici comme d'habitude."
        return {"vip": vip, "public": pub}
    if kind == "result":
        if not actual:
            return None
        ch, prev = actual.get("change_k"), (consensus.get("payrolls") or {}).get("forecast")
        ligne = f"Emplois créés : {fmt_k(ch)}"
        if prev is not None:
            sens = "au-dessus de" if ch > prev + 5 else "en dessous de" if ch < prev - 5 else "proche de"
            ligne += f" ({sens} la prévision : {fmt_k(prev)})"
        elif actual.get("previous_change_k") is not None:
            ligne += f" (mois précédent révisé : {fmt_k(actual['previous_change_k'])})"
        extras = []
        if actual.get("unemployment") is not None:
            extras.append(f"Chômage : {fmt_pct(actual['unemployment'])}" + (f" (avant : {fmt_pct(actual['unemployment_prev'])})" if actual.get("unemployment_prev") is not None else ""))
        if actual.get("earnings_mom") is not None:
            extras.append(f"Salaires horaires : {fmt_pct2(actual['earnings_mom'])} sur un mois")
        r = _reaction_line(react or {})
        txt = "📊 Le NFP vient de tomber.\n\n" + ligne + ("\n" + "\n".join(extras) if extras else "") + ("\n\n" + r if r else "") + \
            f"\n\nPas de nouveau trade avant {pause_b} GMT : je laisse les prix se stabiliser."
        return {"vip": txt, "public": txt}
    if kind == "resume":
        r = _reaction_line(react or {})
        pub = pick(st, "nfp_resume", [
            "✅ Trente minutes après le NFP, je reprends mes analyses normalement. " + (r + " " if r else "") + "Si un trade se met en place, vous le verrez ici.",
            "🟢 Les prix se sont calmés après le NFP, je reprends. " + (r + " " if r else "") + "Je vous préviens dès qu'il y a quelque chose de propre.",
        ])
        vip = pub + ("\n\nZones H4 à surveiller maintenant :\n" + _watch_lines(zones) if zones else "")
        return {"vip": vip, "public": pub}
    return None


# ------------------------------------------------------------------ envoi
def _mark(st: dict, date: str, key: str) -> None:
    st.setdefault("events", {}).setdefault("nfp", {}).setdefault(date, {})[key] = datetime.utcnow().isoformat(timespec="minutes")
    nf = st["events"]["nfp"]
    for d in [d for d in nf if d < str((pd.Timestamp(date) - pd.Timedelta(days=200)).date())]:
        del nf[d]


def deliver(kind: str, texts: Dict[str, str], st: dict, w: dict, cfg: dict, pubs: tuple, mode: str, log: Callable = print) -> None:
    """mode 'live' : VIP puis groupe public. mode 'preview' : les deux versions dans le chat PRIVE du proprietaire (etiquetees)."""
    from signal_publisher import Publisher
    done = ((st.get("events") or {}).get("nfp") or {}).get(w["date"], {})
    if mode == "live":
        vip, pub = pubs[0], pubs[1]
    else:
        vip = Publisher({**cfg, "live": False}, log=log, audience="vip")
        pub = Publisher({**cfg, "public_live": False}, log=log, audience="public")
    if not done.get(f"{kind}_vip"):
        if vip.send_text(texts["vip"], notify=False):
            _mark(st, w["date"], f"{kind}_vip")
    if not done.get(f"{kind}_public"):
        if pub.post_public(texts["public"], notify=False, cta=False):
            _mark(st, w["date"], f"{kind}_public")
            if mode == "live":
                today = str(pd.Timestamp.utcnow().date())
                st.setdefault("pub_posts", {})[today] = st.setdefault("pub_posts", {}).get(today, 0) + 1


def run_step(st: dict, cfg: dict, pubs: tuple, now: pd.Timestamp, log: Callable = print) -> None:
    """Un passage a chaque cycle : envoie les messages NFP dus. Ne leve jamais d'exception."""
    try:
        mode = nfp_mode()
        if mode == "off":
            return
        kinds = due_kinds(st, now)
        if not kinds:
            return
        w = window(now)
        ev = st.setdefault("events", {})
        for kind in kinds:
            consensus = (ev.get("consensus") or {}).get(w["date"]) or {}
            if not consensus:
                consensus = fetch_consensus(w["release"])
                if consensus:
                    ev.setdefault("consensus", {})[w["date"]] = consensus
            actual = react = zones = None
            if kind == "result":
                actual = fetch_actual(w["ref"])
                if not actual:
                    continue                                    # pas encore publie : on reessaie au cycle suivant
                react = reaction(w["release"], 15)
            if kind == "resume":
                react = reaction(w["release"], 30)
            if kind in ("pre", "t60", "resume"):
                zones = zones_to_watch(now)
            texts = build(kind, st, w, consensus, actual, react, zones)
            if texts:
                deliver(kind, texts, st, w, cfg, pubs, mode, log)
                log(f"[NFP] message « {kind} » ({mode})")
    except Exception as e:
        log(f"[NFP] erreur ignoree : {type(e).__name__}: {e}")


def recap_text(st: dict, now: pd.Timestamp, get_actual: Optional[Callable] = None, get_reaction: Optional[Callable] = None) -> Optional[str]:
    """Petit message PUBLIC « retour sur le dernier NFP + prochain rendez-vous » (chiffres reels du BLS, reaction mesuree). None si les chiffres
    ne sont pas disponibles : on ne publie jamais un chiffre invente. Voix du trader, heures en GMT."""
    from signal_content import pick
    past = [d for d in sorted(NFP_RELEASES) if release_utc(d) < now - pd.Timedelta(hours=3)]
    nxt = next_release(now)
    if not past or not nxt:
        return None
    d = past[-1]
    t, ref = release_utc(d), NFP_RELEASES[d]
    actual = (get_actual or fetch_actual)(ref)
    if not actual or actual.get("change_k") is None:
        return None
    ligne = f"{fmt_k(actual['change_k'])} emplois créés"
    if actual.get("previous_change_k") is not None:
        ligne += f" (le mois d'avant revu à {fmt_k(actual['previous_change_k'])})"
    bits = [ligne]
    if actual.get("unemployment") is not None:
        bits.append(f"chômage à {fmt_pct(actual['unemployment'])}" + (f" contre {fmt_pct(actual['unemployment_prev'])} avant" if actual.get("unemployment_prev") is not None else ""))
    if actual.get("earnings_mom") is not None:
        bits.append(f"salaires horaires {fmt_pct2(actual['earnings_mom'])} sur un mois")
    react = (get_reaction or reaction)(t, 30, count=9000) if (now - t) < pd.Timedelta(days=25) else {}
    r = _reaction_line(react)
    pause_a, pause_b = hhmm(nxt["release"] - pd.Timedelta(minutes=BLACKOUT[0])), hhmm(nxt["release"] + pd.Timedelta(minutes=BLACKOUT[1]))
    return pick(st, "nfp_recap", [
        f"🔙 Retour sur le dernier NFP ({fr_date(t)}) : " + ", ".join(bits) + "." + (f"\n\n{r}" if r else "") +
        f"\n\nProchain rendez-vous : {fr_date(nxt['release'])} à {hhmm(nxt['release'])} GMT. Je ne prends aucun nouveau trade de {pause_a} à {pause_b} GMT, "
        f"et je vous tiens au courant ici avant, pendant et après 👀",
        f"📊 Petit rappel sur le NFP : le dernier ({fr_date(t)}) donnait " + ", ".join(bits) + "." + (f"\n\n{r}" if r else "") +
        f"\n\nLe prochain tombe {fr_date(nxt['release'])} à {hhmm(nxt['release'])} GMT : pause de mes nouveaux trades de {pause_a} à {pause_b} GMT, et je vous dis tout ici.",
    ])


def status_line(now: pd.Timestamp) -> str:
    n = next_release(now)
    if not n:
        return ""
    jours = (n["release"] - now).total_seconds() / 86400
    delai = f"dans {jours:.0f} jour{'s' if jours >= 1.5 else ''}" if jours >= 1 else f"dans {jours * 24:.0f} h"
    return f"Prochain NFP : {fr_date(n['release'])} à {hhmm(n['release'])} GMT ({delai})."


# ------------------------------------------------------------------ repetition : rejouer une publication passee
def replay(date: str, send: bool = False) -> None:
    """Affiche (et, avec send=True, envoie en apercu dans le chat PRIVE) la serie de messages d'une publication passee, avec les vrais
    chiffres du BLS et la vraie reaction du marche. La prevision n'est pas connue pour le passe : le message part sans elle."""
    os.environ.setdefault("SIGNAL_BACKEND", "yahoo")
    import signal_data as sd
    w = {"date": date, "ref": NFP_RELEASES[date], "release": release_utc(date), "minutes": 0.0}
    st: dict = {"copy_idx": {}}
    actual = fetch_actual(w["ref"])
    react = reaction(w["release"], 30, count=9000)
    react15 = reaction(w["release"], 15, count=9000)
    print(f"Publication du {fr_date(w['release'])} {hhmm(w['release'])} GMT - mois de reference {w['ref']}")
    print("chiffres reels :", actual)
    print("reaction mesuree :", {k: (round(v[0], 1), round(v[1])) for k, v in react.items()})
    cfg = None
    if send:
        from signal_publisher import load_cfg
        cfg = load_cfg()
    for kind in ("pre", "t60", "result", "resume"):
        texts = build(kind, st, w, {}, actual, react if kind == "resume" else react15, [])
        if not texts:
            continue
        print(f"\n===== {kind.upper()} (public) =====\n{texts['public']}")
        if texts["vip"] != texts["public"]:
            print(f"\n----- {kind.upper()} (VIP) -----\n{texts['vip']}")
        if send:
            deliver(kind, texts, {"events": {}}, w, cfg, (None, None), "preview")


if __name__ == "__main__":
    if "--replay" in sys.argv:
        replay(sys.argv[sys.argv.index("--replay") + 1], send="--send" in sys.argv)
    else:
        print(__doc__)
