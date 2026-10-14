"""
signal_mtf.py - Analyse MULTI-TIMEFRAMES : positions virtuelles (observation) et "voie scalp" (signaux M15 / M30 / H1 dans les zones H4).

L'analyse H4 d'origine (biais journalier + retest d'une zone H4 + bougie de rejet) reste inchangee et garde sa propre voie de publication.
En plus, le robot utilise des timeframes plus petits (M15, M30, H1) pour chercher des entrees DANS les zones H4 encore actives, dans le sens
de la tendance journaliere. Ce que fait chaque entree trouvee :
  * OBSERVATION : position VIRTUELLE, suivie comme un vrai signal (TP1 / TP2 / TP3 / stop, memes regles, memes donnees que le robot cloud),
    resultat dans signals_shadow.jsonl ; le proprietaire recoit un bilan PRIVE. Rien n'est publie.
  * VOIE SCALP (optionnelle, desactivee par defaut) : si le profil est autorise ET si le setup est tres confirme (score >= 5), il est propose a
    la publication : d'abord en APERCU dans le chat prive du proprietaire, puis - sur sa decision - dans le groupe VIP. Plafonds quotidiens,
    suivi et bilan a part de ceux des signaux H4, et DISJONCTEUR automatique si les pertes s'accumulent.

Pourquoi cette prudence : les tests sur l'historique (signal_backtest_ltf.py) ne montrent AUCUN avantage a ces timeframes (le spread pese plus
sur les petits stops) ; la voie scalp existe pour garder le groupe vivant, avec des resultats toujours publies, pas parce qu'elle est prouvee.

Reglages (fichier control.json de la branche data, relu a chaque cycle ; sinon variables d'environnement) :
  scalp_profiles : liste (scalp_m15, scalp_m30, intra_h1) ou off        [defaut : aucun = voie scalp eteinte]
  scalp_mode     : "preview" (chat prive) ou "live" (groupe VIP)         [defaut : preview]
  scalp_since    : date ISO ; seuls les trades LIVE clotures apres cette date comptent pour le disjoncteur (remise a zero manuelle)
  scalp_min_score, scalp_max_per_day, scalp_max_active, scalp_day_floor_r, scalp_breaker_min_trades, scalp_breaker_total_r
Observation : variable SIGNALS_OBSERVE (liste ou off ; defaut : les 3 profils).
Ce module ne leve jamais d'exception vers le robot et respecte un budget de temps : le robot principal passe toujours avant.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from signal_data import get_rates, symbol_meta, TF_MIN, TF_LABEL
from signal_engine import Signal, TradeSim, analyze, htf_bias, htf_zones_of, summarize

DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SIGNALS_DATA_DIR") or DIR)
SHADOW_FILE = DATA_DIR / "signals_shadow.jsonl"
CONTROL_FILE = DATA_DIR / "control.json"

# nom -> timeframe d'entree, timeframe du biais de tendance, timeframe des zones exigees (le contexte de l'analyse H4)
PROFILES: Dict[str, dict] = {
    "scalp_m15": dict(label="Scalp M15", tf="15m", bias_tf="1d", zone_tf="4h"),
    "scalp_m30": dict(label="Scalp M30", tf="30m", bias_tf="1d", zone_tf="4h"),
    "intra_h1": dict(label="Intraday H1", tf="1h", bias_tf="1d", zone_tf="4h"),
}
DEFAULT_OBSERVE = ("scalp_m15", "scalp_m30", "intra_h1")
PARAMS = {"require_htf_zone": True}       # l'entree doit toucher une zone active du timeframe superieur
MAX_PER_DAY = 12                          # positions virtuelles par jour et par profil (garde-fou de volume)
MAX_ACTIVE = 80                           # positions virtuelles ouvertes en meme temps (tous profils)
ABANDON_DAYS = 5                          # une position virtuelle sans donnees depuis 5 jours est abandonnee (non comptee)
BUDGET_S = 110.0                          # temps maximum d'un passage d'observation au sein d'un cycle (le cycle complet est limite a 280 s)
MIN_N_VERDICT = 100                       # sous ce nombre de trades clotures, aucun verdict

# voie scalp : valeurs par defaut (modifiables dans control.json, prefixe "scalp_")
LANE = dict(min_score=5, max_per_day=3, max_active=3, day_floor_r=-3.0, breaker_min_trades=12, breaker_total_r=-4.0)

_ZONES: Dict[tuple, object] = {}          # zones H4 par (symbole, timeframe, derniere bougie) : calculees une seule fois par processus


# ------------------------------------------------------------------ reglages
def control() -> dict:
    try:
        d = json.loads(CONTROL_FILE.read_text(encoding="utf-8-sig"))         # (tolere un fichier enregistre avec BOM par un editeur Windows)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _names(raw, allowed) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        if raw.strip().lower() in ("", "off", "none", "non", "0", "false", "no"):
            return []
        raw = raw.split(",")
    return [str(p).strip() for p in raw if str(p).strip() in allowed]


def enabled_profiles() -> List[str]:
    raw = os.environ.get("SIGNALS_OBSERVE")
    if raw is None or not raw.strip():
        return list(DEFAULT_OBSERVE)
    return _names(raw, PROFILES)


def lane_profiles() -> List[str]:
    """Profils autorises dans la voie scalp (publication). Vide = voie eteinte."""
    c = control()
    return _names(c["scalp_profiles"] if "scalp_profiles" in c else os.environ.get("SIGNALS_SCALP_PROFILES"), PROFILES)


def lane_mode() -> str:
    """'live' (groupe VIP) seulement si le reglage le dit EXPLICITEMENT ; sinon 'preview' (chat prive du proprietaire)."""
    c = control()
    raw = c["scalp_mode"] if "scalp_mode" in c else os.environ.get("SIGNALS_SCALP_MODE", "")
    return "live" if str(raw).strip().lower() == "live" else "preview"


def lane_param(name: str):
    return control().get("scalp_" + name, LANE[name])


def _state(st: dict) -> dict:
    m = st.setdefault("mtf", {})
    for k in ("last_bar", "next_check", "active", "per_day"):
        m.setdefault(k, {})
    return m


# ------------------------------------------------------------------ disjoncteur de la voie scalp
def lane_state(rows: List[dict], now: pd.Timestamp) -> dict:
    """rows : trades de l'historique (tous). Seuls comptent les trades scalp / intraday LIVE clotures depuis `scalp_since`.
    Retourne {'open': bool, 'reason': 'ok' | 'breaker' | 'day_floor', 'n', 'total_r', 'day_r'}. Aucune exception."""
    try:
        since = str(control().get("scalp_since") or "")
        mine = [r for r in rows if r.get("profile") and r.get("mode") == "live" and str(r.get("close_time", "")) >= since]
        total = sum(float(r["r"]) for r in mine)
        today = str(now.date())
        day_r = sum(float(r["r"]) for r in mine if str(r.get("close_time", ""))[:10] == today)
        out = {"open": True, "reason": "ok", "n": len(mine), "total_r": total, "day_r": day_r}
        if len(mine) >= int(lane_param("breaker_min_trades")) and total <= float(lane_param("breaker_total_r")):
            out.update(open=False, reason="breaker")
        elif day_r <= float(lane_param("day_floor_r")):
            out.update(open=False, reason="day_floor")
        return out
    except Exception:
        return {"open": False, "reason": "erreur", "n": 0, "total_r": 0.0, "day_r": 0.0}        # dans le doute, la voie reste fermee


# ------------------------------------------------------------------ recherche d'entrees
def _zones(sid: str, cls: str, meta: dict, zone_tf: str, dz: pd.DataFrame):
    key = (sid, zone_tf, dz.index[-1].isoformat())
    if key not in _ZONES:
        if len(_ZONES) > 400:
            _ZONES.clear()
        _ZONES[key] = htf_zones_of(dz, TF_MIN[zone_tf], TF_LABEL[TF_MIN[zone_tf]], meta["point"], cls)
    return _ZONES[key]


def scan_profile(sid: str, cls: str, name: str, st: dict, now: pd.Timestamp, min_score: int) -> List[tuple]:
    """[(signal, bougies d'entree, meta), ...] : signaux formes sur les bougies cloturees depuis le dernier passage (meme logique de
    rattrapage que le robot principal)."""
    prof = PROFILES[name]
    m = _state(st)
    key = f"{sid}|{name}"
    nc = m["next_check"].get(key)
    if nc and pd.Timestamp(nc) > now:
        return []
    tf, tfm = prof["tf"], TF_MIN[prof["tf"]]
    df = get_rates(sid, tf, 700)
    d1 = get_rates(sid, prof["bias_tf"], 800)
    dz = get_rates(sid, prof["zone_tf"], 1000)
    if df is None or d1 is None or dz is None or len(df) < 300 or len(d1) < 260 or len(dz) < 260:
        m["next_check"][key] = (now + pd.Timedelta(minutes=5)).isoformat()
        return []
    last = df.index[-1]
    prev = m["last_bar"].get(key)
    if prev == last.isoformat():                                     # pas de nouvelle bougie (marche ferme / pas encore disponible)
        m["next_check"][key] = (now + pd.Timedelta(minutes=10)).isoformat()
        return []
    meta = symbol_meta(sid)
    if meta is None:
        return []
    hz = _zones(sid, cls, meta, prof["zone_tf"], dz)
    if hz is None:
        m["next_check"][key] = (now + pd.Timedelta(minutes=30)).isoformat()
        return []
    bias = htf_bias(d1, df.index, tfm, htf_minutes=TF_MIN[prof["bias_tf"]])
    sigs = analyze(df, bias, meta["point"], sid, sid, tfm, cls, params=PARAMS, only_last=False, min_score=min_score, htf_zones=hz, profile=name)
    if prev is None:                                                 # premier passage : seulement la derniere bougie (pas d'historique)
        new = [s for s in sigs if s.bar_open == last.isoformat()]
    else:
        pt = pd.Timestamp(prev)
        new = [s for s in sigs if pd.Timestamp(s.bar_open) > pt]
    m["last_bar"][key] = last.isoformat()
    m["next_check"][key] = (last + pd.Timedelta(minutes=2 * tfm + 1)).isoformat()
    return [(s, df, meta) for s in sorted(new, key=lambda x: x.bar_open)]


def is_fresh(sig: Signal, now: pd.Timestamp) -> bool:
    """Meme esprit que le robot principal : un signal de plus d'une bougie est perime."""
    tfm = int(sig.tf.rstrip("m"))
    return (now - pd.Timestamp(sig.signal_time)).total_seconds() / 60 <= tfm


def register(sig: Signal, st: dict, now: pd.Timestamp) -> bool:
    """Ouvre la position virtuelle. False si un garde-fou l'interdit (doublon, plafond du jour, trop de positions ouvertes)."""
    m = _state(st)
    if sig.id in m["active"] or len(m["active"]) >= MAX_ACTIVE:
        return False
    day = f"{sig.profile}|{now.date()}"
    if m["per_day"].get(day, 0) >= MAX_PER_DAY:
        return False
    for rec in m["active"].values():                                  # une seule position virtuelle a la fois par (symbole, profil)
        if rec["profile"] == sig.profile and rec["signal"]["symbol"] == sig.symbol:
            return False
    m["active"][sig.id] = {"signal": sig.to_dict(), "sim": TradeSim(sig).to_dict(), "profile": sig.profile, "opened": now.isoformat()}
    m["per_day"][day] = m["per_day"].get(day, 0) + 1
    old = str((now - pd.Timedelta(days=4)).date())
    for k in [k for k in m["per_day"] if k.split("|", 1)[1] < old]:
        del m["per_day"][k]
    return True


# ------------------------------------------------------------------ suivi des positions virtuelles
def append_shadow(rec: dict) -> None:
    SHADOW_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def load_shadow(path: Optional[Path] = None) -> List[dict]:
    p = path or SHADOW_FILE
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def update_shadow(st: dict, now: pd.Timestamp, deadline: Optional[float] = None) -> None:
    """Fait avancer chaque position virtuelle avec les bougies de 5 minutes (comme le suivi des vrais signaux).
    deadline : heure (time.time()) au-dela de laquelle on s'arrete ; le reste est fait au cycle suivant."""
    m = _state(st)
    cache: Dict[str, Optional[pd.DataFrame]] = {}
    for sid, rec in list(m["active"].items()):
        if deadline is not None and time.time() > deadline:
            break
        sig = Signal.from_dict(rec["signal"])
        sim = TradeSim.from_dict(rec["sim"])
        t0 = pd.Timestamp(sig.signal_time)
        if now - t0 > pd.Timedelta(days=ABANDON_DAYS):
            del m["active"][sid]
            continue
        if sig.symbol not in cache:
            cache[sig.symbol] = get_rates(sig.symbol, "5m", 3000)
        bars = cache[sig.symbol]
        if bars is None:
            continue
        bars = bars[bars.index >= t0]
        if sim.last_ts:
            bars = bars[bars.index > pd.Timestamp(sim.last_ts)]
        for ts, row in bars.iterrows():
            sim.feed(ts, float(row["high"]), float(row["low"]), float(row["close"]), bar_minutes=5)
            if sim.closed:
                break
        rec["sim"] = sim.to_dict()
        if sim.closed:
            append_shadow({"id": sig.id, "profile": sig.profile, "tf": sig.tf, "display": sig.display, "direction": sig.direction,
                           "score": sig.score, "grade": sig.grade, "signal_time": sig.signal_time, "entry": sig.entry, "sl": sig.sl,
                           "risk_atr": round(sig.risk / sig.atr, 2), "zone": (sig.ctx or {}).get("kind"), "outcome": sim.outcome,
                           "r": round(sim.r, 3), "close_time": sim.close_time})
            del m["active"][sid]


# ------------------------------------------------------------------ passage complet (appele par le robot a chaque cycle)
def observe(st: dict, cfg: dict, now: pd.Timestamp, ids: List[str], t_cycle: Optional[float] = None,
            log: Callable[[str], None] = print, logp: Optional[Callable[[str, str], None]] = None,
            lane: Optional[dict] = None) -> List[tuple]:
    """Un passage : suit les positions virtuelles, cherche de nouvelles entrees sur les profils actifs, ouvre les positions virtuelles.
    Retourne les CANDIDATS de la voie scalp [(signal, bougies, meta), ...] (profil autorise, score suffisant, disjoncteur ferme = aucun) :
    c'est le robot qui decide de les publier. NE LEVE JAMAIS d'exception et s'arrete si le budget de temps est depasse."""
    candidates: List[tuple] = []
    observe_on = enabled_profiles()
    lane_on = lane_profiles()
    scan = list(dict.fromkeys(observe_on + lane_on))
    if not scan:
        return candidates
    logp = logp or (lambda msg, detail: log(f"{msg} : {detail}"))
    t0 = t_cycle if t_cycle is not None else time.time()
    try:
        update_shadow(st, now, deadline=t0 + BUDGET_S)
    except Exception as e:
        log(f"[MTF] suivi : erreur ignoree ({type(e).__name__}: {e})")
    min_score = int(cfg.get("min_score", 4))
    lane_min = int(lane_param("min_score"))
    lane_open = bool(lane and lane.get("open"))
    from signal_data import UNIVERSE
    n_new = 0
    for sid in ids:
        if time.time() - t0 > BUDGET_S:
            log("[MTF] budget de temps atteint : le reste au prochain cycle")
            break
        cls = UNIVERSE[sid]["cls"]
        for name in scan:
            try:
                for sig, df, meta in scan_profile(sid, cls, name, st, now, min(min_score, lane_min) if name in lane_on else min_score):
                    if not is_fresh(sig, now):
                        continue
                    if name in lane_on and lane_open and sig.score >= lane_min:
                        candidates.append((sig, df, meta))                         # voie scalp : le robot decide
                    elif name in observe_on and sig.score >= min_score and register(sig, st, now):
                        n_new += 1
                        logp("[MTF] position virtuelle", f"{sig.display} {sig.direction} {PROFILES[name]['label']} score {sig.score}")
            except Exception as e:
                log(f"[MTF] {name} erreur ignoree ({type(e).__name__}: {e})")
    if n_new:
        log(f"[MTF] {n_new} nouvelle(s) position(s) virtuelle(s) - {len(_state(st)['active'])} ouverte(s)")
    return candidates


# ------------------------------------------------------------------ bilans (prives)
def profile_stats(rows: List[dict]) -> Dict[str, dict]:
    out = {}
    for name in PROFILES:
        sub = [r for r in rows if r.get("profile") == name]
        s = summarize([{"r": r["r"], "outcome": r["outcome"]} for r in sub])
        out[name] = s
    return out


def _fmt(label: str, s: dict, n_open: int) -> str:
    if not s.get("n"):
        return f"• {label} : aucun trade terminé" + (f" ({n_open} en cours)" if n_open else "")
    ic = 1.96 * s["avg_r_se"]
    txt = (f"• {label} : {s['n']} trades · TP1 {s['tp1']:.0f} % · stops {s['sl']:.0f} % · moyenne {s['avg_r']:+.2f} R par trade "
           f"(± {ic:.2f}) · total {s['total_r']:+.1f} R")
    return txt + (f" · {n_open} en cours" if n_open else "")


def status_lines(st: dict, rows: Optional[List[dict]] = None) -> List[str]:
    """Lignes pour /etat (message PRIVE du proprietaire)."""
    profs = enabled_profiles()
    if not profs:
        return []
    rows = load_shadow() if rows is None else rows
    stats = profile_stats(rows)
    opened: Dict[str, int] = {}
    for rec in _state(st)["active"].values():
        opened[rec["profile"]] = opened.get(rec["profile"], 0) + 1
    lines = ["", "<b>Observation (positions virtuelles, rien n'est publié)</b>"]
    for name in profs:
        lines.append(_fmt(PROFILES[name]["label"], stats[name], opened.get(name, 0)))
    total = sum(s.get("n", 0) for s in stats.values())
    if total < MIN_N_VERDICT:
        lines.append(f"Trop tôt pour conclure : il faut au moins {MIN_N_VERDICT} trades terminés par timeframe.")
    return lines


def lane_lines(st: dict, history: List[dict], now: pd.Timestamp) -> List[str]:
    """Lignes /etat sur la voie scalp (PRIVE)."""
    names = lane_profiles()
    if not names:
        return []
    ls = lane_state(history, now)
    labels = " + ".join(PROFILES[n]["label"] for n in names)
    mode = "groupe VIP" if lane_mode() == "live" else "aperçu dans ton chat privé seulement"
    lines = ["", f"<b>Voie scalp</b> : {labels} — {mode}"]
    if not ls["open"]:
        lines.append("⛔ Voie fermée : " + ("disjoncteur déclenché (pertes cumulées trop importantes)" if ls["reason"] == "breaker"
                                          else "perte maximale du jour atteinte" if ls["reason"] == "day_floor" else "erreur de lecture"))
    mine = [r for r in history if r.get("profile") and r.get("mode") == "live"]
    s = summarize([{"r": r["r"], "outcome": r["outcome"]} for r in mine])
    if s.get("n"):
        lines.append(f"• Publiés : {s['n']} trades · TP1 {s['tp1']:.0f} % · stops {s['sl']:.0f} % · {s['total_r']:+.1f} R")
    return lines


def digest_text(st: dict, now: pd.Timestamp, rows: Optional[List[dict]] = None) -> Optional[str]:
    """Bilan prive du jour (None s'il n'y a rien a dire)."""
    rows = load_shadow() if rows is None else rows
    day = str(now.date())
    today = [r for r in rows if str(r.get("close_time", ""))[:10] == day]
    if not today:
        return None
    lines = [f"🔭 <b>Observation du jour</b> — positions virtuelles clôturées aujourd'hui (rien n'a été publié)"]
    stats = profile_stats(today)
    for name in enabled_profiles():
        s = stats.get(name, {})
        if s.get("n"):
            lines.append(f"• {PROFILES[name]['label']} : {s['n']} trades · {s['total_r']:+.1f} R")
    lines += status_lines(st, rows)[1:]
    return "\n".join(lines)


def main() -> None:
    """python signal_mtf.py [fichier.jsonl] : bilan en francais des positions virtuelles."""
    import sys
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = load_shadow(path)
    print(f"{len(rows)} positions virtuelles terminées.")
    stats = profile_stats(rows)
    for name, s in stats.items():
        print(_fmt(PROFILES[name]["label"], s, 0).replace("<b>", "").replace("</b>", ""))


if __name__ == "__main__":
    main()
