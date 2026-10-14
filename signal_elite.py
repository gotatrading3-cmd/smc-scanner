"""
signal_elite.py - Le « groupe VIP 📊 » (cree le 2026-10-14) : UNIQUEMENT les positions tres confirmees (5 confirmations sur 7 ou plus, grade A+), plus un peu
d'animation REELLE : bienvenue + historique reel des positions closes (une fois), agenda economique et zones a surveiller du matin, point marche, bilans, jours de NFP.

Vocabulaire du proprietaire (ne JAMAIS les confondre) :
  « groupe prive » = celui ou arrivent TOUS les signaux (dans le code : audience "vip") ;
  « groupe VIP »   = celui-ci, le nouveau (dans le code : audience "elite") ;
  « groupe public ».
Ce module ne remplace rien : les deux autres groupes continuent exactement comme avant. Eteint par defaut ; toute erreur est ignoree.

Reglages (control.json de la branche data, relu a chaque cycle ; sinon variables d'environnement) :
  elite_mode      : off (defaut) | preview (TOUT arrive dans TON chat prive, etiquete « groupe VIP 📊 ») | live
  elite_chat_id   : identifiant du groupe (-100...)                      [variable ELITE_CHAT_ID]
  elite_min_score : 5 par defaut (position envoyee ici si score >= 5)    [variable SIGNALS_ELITE_MIN_SCORE]

Regles d'ecriture : tout chiffre vient de l'historique reel du robot ou du marche ; gains ET pertes ; jamais « 100 % », jamais de promesse ; PAS de fausse
anciennete : le groupe annonce son vrai point de depart (28 septembre 2026, premier signal reel du robot) et ses vrais chiffres.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SIGNALS_DATA_DIR") or DIR)
CONTROL_FILE = DATA_DIR / "control.json"
DEPART = "2026-09-28"                         # premier signal reel du robot (historique public)
MAX_POSTS_PER_DAY = 8                         # animation : jamais plus de 8 messages par jour (les positions et leur suivi ne comptent pas)
AGENDA_TRIES = 3                              # le calendrier gratuit est non officiel : 3 essais par jour au plus, puis on s'en passe
MOIS_COURT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
WATCH_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "XAUUSD", "US100")


# ------------------------------------------------------------------ reglages
def control() -> dict:
    try:
        d = json.loads(CONTROL_FILE.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def settings() -> dict:
    c = control()
    mode = str(c["elite_mode"] if "elite_mode" in c else os.environ.get("SIGNALS_ELITE_MODE", "")).strip().lower()
    chat = str(c["elite_chat_id"] if "elite_chat_id" in c else os.environ.get("ELITE_CHAT_ID", "")).strip()
    try:
        mini = int(c["elite_min_score"] if "elite_min_score" in c else os.environ.get("SIGNALS_ELITE_MIN_SCORE", "5"))
    except (TypeError, ValueError):
        mini = 5
    mode = mode if mode in ("preview", "live") else "off"
    return {"mode": mode if chat else "off", "chat_id": chat, "min_score": max(1, mini)}


def elite_cfg(cfg: dict) -> dict:
    s = settings()
    return {**cfg, "elite_chat_id": s["chat_id"], "elite_live": s["mode"] == "live"}


def publisher(cfg: dict, log: Callable = print):
    """Editeur du groupe VIP (apercu prive ou groupe, selon le mode) ; None si le groupe est eteint."""
    s = settings()
    if s["mode"] == "off":
        return None
    from signal_publisher import Publisher
    return Publisher(elite_cfg(cfg), log=log, audience="elite")


def wants(score: int) -> bool:
    s = settings()
    return s["mode"] != "off" and int(score) >= s["min_score"]


# ------------------------------------------------------------------ chiffres reels
def a_plus(rows: List[dict], min_score: Optional[int] = None) -> List[dict]:
    """Positions reelles (mode live) de score >= min, signaux H4 et scalps compris."""
    mini = settings()["min_score"] if min_score is None else min_score
    return [r for r in rows if r.get("mode") == "live" and int(r.get("score", 0) or 0) >= mini]


def _stats(rows: List[dict]) -> dict:
    from signal_engine import summarize
    return summarize([{"r": r["r"], "outcome": r["outcome"]} for r in rows])


def _fr_r(r: float) -> str:
    return f"{r:+.2f}".replace(".", ",").replace("-", "−") + " R"


def stats_line(rows: List[dict]) -> str:
    s = _stats(rows)
    if not s.get("n"):
        return "aucune position clôturée"
    return f"{s['n']} position{'s' if s['n'] > 1 else ''} · TP1 {s['tp1']:.0f} % · stops {s['sl']:.0f} % · {_fr_r(s['total_r'])}"


def _label(o: str) -> str:
    """« TPn » = le plus haut objectif atteint (le reste a pu se clore a l'entree ou au temps : le chiffre en R, affiche a cote, dit le resultat net)."""
    return {"SL": "stop touché", "TP1": "TP1 atteint", "TP2": "TP2 atteint", "TP3": "TP3 atteint", "EXPIRED": "clôturé au temps"}.get(o, o)


def _fr_date(iso) -> str:
    d = pd.Timestamp(iso)
    return f"{d.day} {MOIS_COURT[d.month - 1]}"


# ------------------------------------------------------------------ textes (tutoiement, comme le groupe prive)
def welcome_text(all_rows: List[dict]) -> str:
    live = [r for r in all_rows if r.get("mode") == "live"]
    ap = a_plus(all_rows)
    mini = settings()["min_score"]
    base = (f"📌 <b>Groupe VIP — mode d'emploi</b>\n\n"
            f"Ici, uniquement les positions les plus confirmées du robot : au moins <b>{mini} confirmations sur 7</b> (grade A+). Rien d'autre : pas de remplissage.\n\n"
            "Pour chaque position :\n"
            "• la <b>fiche</b> : entrée, stop loss, TP1 · TP2 · TP3\n"
            "• l'<b>analyse</b> détaillée\n"
            "• le <b>suivi</b> : chaque objectif atteint ou stop touché, annoncé ici\n\n"
            "<b>Gestion suggérée</b> : 40 % à TP1 (puis stop à l'entrée) · 30 % à TP2 · 30 % à TP3 · risque conseillé : 1 % du capital maximum par trade.\n\n"
            "J'y publie aussi les zones à surveiller du jour, un point marché, les jours de NFP et les bilans. Gains et pertes, toujours.\n\n"
            f"<b>Historique réel depuis le 28 septembre 2026</b> (premier signal du robot) :\n"
            f"• positions {mini}/7 et plus : {stats_line(ap)}\n"
            f"• tous les signaux : {stats_line(live)}\n\n"
            f"{mini} sur 7, c'est plus de confirmations, pas une garantie : chaque position garde son stop.")
    return base


def record_text(all_rows: List[dict], n: int = 12) -> Optional[str]:
    """Le vrai passe du robot : les positions A+ deja closes, pertes comprises (aucune anciennete inventee). Une seule fois, juste apres la bienvenue."""
    ap = sorted(a_plus(all_rows), key=lambda r: str(r.get("signal_time") or r.get("close_time") or ""))
    if not ap:
        return None
    lignes = []
    for r in ap[-n:]:
        sens = {"LONG": " achat", "SHORT": " vente"}.get(str(r.get("direction", "")).upper(), "")
        lignes.append(f"• {_fr_date(r.get('signal_time') or r['close_time'])} · {r['display']}{sens} — {_label(r['outcome'])} ({_fr_r(r['r'])})")
    return ("🧾 <b>Les positions déjà clôturées</b>\n<i>Réel, depuis le 28 septembre 2026 — pertes comprises</i>\n\n" + "\n".join(lignes) +
            f"\n\nTotal : {stats_line(ap)}\n\nTout est publié tel quel, gains comme pertes.")


def _qualifier(title: str) -> str:
    """« Core CPI m/m » -> « sous-jacent, mensuel » : distingue les rendez-vous qui ont le meme nom francais a la meme heure."""
    t = title.lower()
    p = ["sous-jacent"] if re.search(r"\bcore\b", t) else []
    for rx, mot in ((r"\bm/m\b", "mensuel"), (r"\by/y\b", "annuel"), (r"\bq/q\b", "trimestriel"), (r"\bw/w\b", "hebdomadaire")):
        if re.search(rx, t):
            p.append(mot)
            break
    return ", ".join(p)


def agenda_text(now: pd.Timestamp, evs: Optional[list], max_lines: int = 18) -> Optional[str]:
    """Agenda economique du jour (rendez-vous a impact fort / moyen, heures UTC), avec les noms francais et le glossaire du canal d'actualites. None s'il n'y a rien.
    Les chiffres (prevu / precedent) viennent du calendrier ; aucun chiffre n'est invente."""
    if not evs:
        return None
    import news_calendar as nc
    todays = [e for e in evs if e["time_utc"].date() == now.date() and e["impact"] in ("High", "Medium")]
    if not todays:
        return None
    if len(todays) > max_lines:                                  # journee chargee : tous les impacts forts d'abord, puis ce qui reste de place
        fort = [e for e in todays if e["impact"] == "High"]
        todays = fort + [e for e in todays if e["impact"] != "High"][:max(0, max_lines - len(fort))]
    noms = []
    for e in todays:
        ex = nc.explain(e["title"])
        noms.append(ex[0] if ex else e["title"])
    vus: Dict[tuple, int] = {}
    for e, nom in zip(todays, noms):
        vus[(e["time_utc"], nom)] = vus.get((e["time_utc"], nom), 0) + 1
    lignes = []
    for e, nom in sorted(zip(todays, noms), key=lambda x: (x[0]["time_utc"], x[0]["impact"] != "High")):
        if vus[(e["time_utc"], nom)] > 1:
            nom = f"{nom} : {_qualifier(e['title']) or e['title']}"
        nums = " · ".join(x for x in ((f"prévu {nc._esc(e['forecast'])}" if e["forecast"] else ""),
                                      (f"précédent {nc._esc(e['previous'])}" if e["previous"] else "")) if x)
        lignes.append(f"<b>{e['time_utc'].strftime('%H:%M')}</b> {nc.IMPACT_DOT[e['impact']]} {nc.FLAGS.get(e['ccy'], '')} {nc._esc(nom)}" + (f" — {nums}" if nums else ""))
    d = now
    return (f"📅 <b>Agenda du jour — {nc.JOURS[d.weekday()]} {d.day} {nc.MOIS[d.month - 1]}</b>\n<i>Heures UTC · 🔴 impact fort · 🟠 impact moyen</i>\n\n" + "\n".join(lignes) +
            "\n\nLes positions prises autour de ces horaires arrivent ici, avec leur analyse.")


def zones_text(zones: List[dict], n: int = 6) -> Optional[str]:
    if not zones:
        return None
    from signal_events import _watch_lines
    return ("🔭 <b>Zones à surveiller aujourd'hui</b>\n\n" + _watch_lines(zones, n) +
            "\n\nQuand le prix touche une zone et que le setup est assez confirmé, la position arrive ici avec son analyse.")


def market_text(snap: dict, mv: dict) -> Optional[str]:
    from signal_market import NAMES, pct
    ranked = snap.get("ranked") or []
    lignes = []
    if len(ranked) >= 2:
        (t, tp), (b, bp) = ranked[0], ranked[-1]
        lignes.append(f"Sur la semaine : {NAMES.get(t, t)} en tête ({pct(tp)}), {NAMES.get(b, b)} en retrait ({pct(bp)}).")
    up = ", ".join(f"{s} {pct(v)}" for s, v in (mv.get("up") or [])[:3])
    down = ", ".join(f"{s} {pct(v)}" for s, v in (mv.get("down") or [])[:3])
    if up:
        lignes.append(f"Aujourd'hui, ça monte : {up}.")
    if down:
        lignes.append(f"Aujourd'hui, ça baisse : {down}.")
    if not lignes:
        return None
    return "📈 <b>Point marché</b>\n\n" + "\n".join(lignes)


def evening_text(closed_today: List[dict], n_open: int) -> Optional[str]:
    if not closed_today and not n_open:
        return None
    parts = ["🌙 <b>Bilan du jour</b> (positions A+)\n"]
    for r in closed_today:
        parts.append(f"• {r['display']} — {_label(r['outcome'])} ({_fr_r(r['r'])})")
    if n_open:
        parts.append(f"\nEncore {n_open} position{'s' if n_open > 1 else ''} ouverte{'s' if n_open > 1 else ''} : je te tiens au courant ici.")
    parts.append("\nTout est publié, gains comme pertes.")
    return "\n".join(parts)


def weekly_text(week_rows: List[dict], all_rows: List[dict]) -> Optional[str]:
    if not week_rows:
        return None
    return (f"📊 <b>Bilan de la semaine</b> (positions A+)\n\nSemaine : {stats_line(week_rows)}\n"
            f"Depuis le 28 septembre : {stats_line(all_rows)}\n\nGains comme pertes : tout est publié.")


# ------------------------------------------------------------------ envoi
def _send(pub, text: str, pin: bool = False) -> bool:
    mid = pub.send_text(text, notify=False)
    if mid and pin:
        try:
            pub._pin(mid)
        except Exception:
            pass
    return bool(mid)


def run_step(st: dict, cfg: dict, now: pd.Timestamp, history: List[dict], n_open: int = 0, t_cycle: Optional[float] = None,
             log: Callable = print, zones_fn: Optional[Callable] = None, market_fn: Optional[Callable] = None,
             calendar_fn: Optional[Callable] = None) -> None:
    """Animation du groupe VIP : message de bienvenue et historique reel (une fois), agenda et zones du matin, point marche, bilan du soir, bilan de la semaine.
    Chaque message part UNE seule fois par creneau, seulement si Telegram l'a accepte. Ne leve jamais d'exception."""
    try:
        pub = publisher(cfg, log)
        if pub is None:
            return
        el = st.setdefault("elite", {})
        today, hm, wd = str(now.date()), now.hour * 60 + now.minute, now.weekday()
        posts = el.setdefault("posts", {})
        if posts.get(today, 0) >= MAX_POSTS_PER_DAY:
            return

        def done(kind: str, key: str, text: Optional[str], pin: bool = False) -> None:
            if text is None:
                el[kind] = key                                   # rien a dire pour ce creneau : on ne recalcule pas
                return
            if _send(pub, text, pin):
                el[kind] = key
                posts[today] = posts.get(today, 0) + 1
                for d in [d for d in posts if d < str((now - pd.Timedelta(days=10)).date())]:
                    del posts[d]
                log(f"[VIP] message « {kind} » ({settings()['mode']})")

        import time
        budget_ok = lambda: t_cycle is None or time.time() - t_cycle < 100          # noqa: E731 - le robot principal passe toujours avant
        if not el.get("welcome"):
            done("welcome", today, welcome_text(history), pin=True)
            return
        if not el.get("record"):                                 # le vrai passe du robot (aucune anciennete inventee), une fois, juste apres la bienvenue
            done("record", today, record_text(history))
            return
        fails = el.setdefault("fails", {})                       # echecs de SOURCE de donnees (pas de Telegram) : on retente quelques fois, pas toute la matinee
        for k in [k for k in fails if not k.endswith(today)]:
            del fails[k]
        nb_fails = lambda kind: fails.get(f"{kind}|{today}", 0)  # noqa: E731

        def fetch(kind: str, fn: Callable, *args):
            """Appelle une source de donnees. None = echec (exception ou source injoignable), compte pour le plafond du jour."""
            try:
                res = fn(*args)
            except Exception as e:
                log(f"[VIP] {kind} : erreur ignoree ({type(e).__name__}: {e})")
                res = None
            if res is None:
                fails[f"{kind}|{today}"] = nb_fails(kind) + 1
            return res

        if wd < 5 and 6 * 60 + 45 <= hm < 11 * 60 + 30 and el.get("agenda") != today and budget_ok() and nb_fails("agenda") < AGENDA_TRIES:
            evs = fetch("agenda", calendar_fn or _calendar)      # None = calendrier injoignable (on reessaiera) ; liste vide = rien d'important aujourd'hui
            if evs is not None:
                sent_before = posts.get(today, 0)
                done("agenda", today, agenda_text(now, evs))
                if posts.get(today, 0) > sent_before:
                    return                                       # un message par passage : les zones suivront au prochain
        if wd < 5 and 7 * 60 + 30 <= hm < 11 * 60 + 30 and el.get("zones") != today and budget_ok() and nb_fails("zones") < 4:
            zs = fetch("zones", zones_fn or _zones, now)
            if zs is not None:
                done("zones", today, zones_text(zs))
        if wd < 5 and 12 * 60 <= hm < 16 * 60 and el.get("market") != today and budget_ok() and nb_fails("market") < 4:
            res = fetch("market", market_fn or _market)
            if res is not None:
                done("market", today, market_text(*res))
        rd = now.normalize() if hm >= 21 * 60 + 30 else ((now - pd.Timedelta(days=1)).normalize() if hm < 5 * 60 + 30 else None)
        if rd is not None and rd.weekday() < 5 and el.get("evening") != str(rd.date()):
            ap = a_plus(history)
            closed = [r for r in ap if str(r.get("close_time", ""))[:10] == str(rd.date())]
            done("evening", str(rd.date()), evening_text(closed, n_open))
            wk = f"{rd.isocalendar()[0]}-W{rd.isocalendar()[1]}"
            if rd.weekday() == 4 and el.get("weekly") != wk:
                start = rd - pd.Timedelta(days=rd.weekday())
                week_rows = [r for r in ap if str(r.get("close_time", ""))[:10] >= str(start.date())]
                done("weekly", wk, weekly_text(week_rows, ap))
    except Exception as e:
        log(f"[VIP] erreur ignoree : {type(e).__name__}: {e}")


def _calendar() -> Optional[list]:
    import news_calendar as nc
    return nc.fetch_calendar()


def _zones(now: pd.Timestamp) -> list:
    from signal_events import zones_to_watch
    return zones_to_watch(now, symbols=WATCH_SYMBOLS)


def _market():
    import signal_market as market
    return market.snapshot(), market.movers()


def status_line(history: List[dict]) -> str:
    s = settings()
    if s["mode"] == "off":
        return ""
    ap = a_plus(history)
    return (f"Groupe VIP 📊 : {'en direct' if s['mode'] == 'live' else 'aperçu dans ton chat privé'} — positions {s['min_score']}/7 et plus ; "
            f"publiées jusqu'ici : {stats_line(ap)}.")
