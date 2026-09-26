"""
signal_runner.py - Canal de signaux GOTA Confluence, DEUX audiences. Tourne :
  * en CLOUD (GitHub Actions, PC eteint)  : python signal_runner.py --once     (backend yahoo)
  * en LOCAL (PC Windows + MT5)          : python signal_runner.py            (boucle 60 s)

  GROUPE VIP    : TOUS les signaux, en detail (fiche + analyse), suivi de chaque objectif / stop, bilan hebdo
  GROUPE PUBLIC : QUELQUES signaux par jour en entier ("deja dans le VIP") + leur suivi (gains ET pertes), point du matin,
                  New York, bilan du soir, week-end, conseils ; chaque image porte un lien pour nous ecrire
                  -> un groupe qui vit toute la journee et qui pousse vers le VIP

    python signal_runner.py --once          # un seul cycle (mode cloud)
    python signal_runner.py --test          # TEST 'vue abonnes' (VIP puis public) dans ton chat prive
    python signal_runner.py --demo [--no-send]   # un exemple historique complet (apercu)
    python signal_runner.py --brief|--edu|--cta|--promo|--account|--recap|--pinned   # apercus de contenu (ton chat prive)
    python signal_runner.py --post <type>   # poste MAINTENANT dans le groupe public : edu brief ny cta promo account sat sun movers crypto scan
    python signal_runner.py --post-pinned   # guide VIP + accueil public epingles (modifie les messages epingles s'ils existent)
    python signal_runner.py --check-channel # verifie que le bot peut publier dans les 2 groupes (ne poste rien)

MODE APERCU PAR DEFAUT : chaque audience n'est publiee pour de bon que si elle est activee (SIGNALS_LIVE pour le VIP,
PUBLIC_LIVE pour le public) ET qu'un groupe est configure. LECTURE SEULE cote marche : aucun ordre.
"""
from __future__ import annotations
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import signal_data as sd
from signal_data import connect, shutdown, get_rates, get_tick, symbol_meta, UNIVERSE, available_ids, utc_now, TF_MIN
from signal_engine import Signal, TradeSim, analyze, htf_bias, summarize
from signal_publisher import Publisher, load_cfg, save_cfg, pinned_text, CHANNEL_ABOUT_PUBLIC
import signal_content as content
import signal_market as market
from chart_render import render_signal_chart, render_recap_card, GREEN, RED, GOLD, BLUE, WHITE, TF_LABEL

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("SIGNALS_DATA_DIR") or DIR)          # etat + historique (branche "data" en cloud)
STATE_FILE = DATA_DIR / "signals_state.json"
HISTORY_FILE = DATA_DIR / "signals_history.jsonl"
LOG_FILE = DIR / "signals.log"
OUT_DIR = DIR / "signal_out"
PAUSE_FILE = DIR / ".signals_pause"
HTF = {"1h": ("4h", 240), "4h": ("1d", 1440)}                        # biais de tendance par timeframe d'entree
MAX_ENTRY_DRIFT_R = 0.35         # pas de publication si le prix s'est trop eloigne de l'entree
MAX_NET_EXPOSURE = 2             # max de trades actifs dans le meme sens sur une devise / un cluster
CYCLE_SECONDS = 60
OUTAGE_ALERT_RUNS = 4            # nb d'executions consecutives sans aucune donnee avant alerte au proprietaire
_stats = {"ok": 0, "fail": 0}

# Depot PUBLIC (GitHub gratuit) : ce qui concerne les signaux en cours reste PRIVE.
#  - SIGNALS_STATE_KEY : cle Fernet ; les signaux actifs (niveaux) et les dates de signal sont CHIFFRES dans la branche "data"
#    (l'historique des trades clotures reste en clair : c'est la piste d'audit publique)
#  - SIGNALS_QUIET_LOG=1 : paire / sens / score des signaux absents de la sortie des journaux GitHub
STATE_KEY = os.environ.get("SIGNALS_STATE_KEY", "").strip()
QUIET_LOG = str(os.environ.get("SIGNALS_QUIET_LOG", "")).strip().lower() in ("1", "true", "yes")
PRIVATE_KEYS = ("active", "last_signal")
_PRIV_SEEN: dict = {}            # dernier bloc prive lu (texte canonique -> jeton chiffre) : evite de re-chiffrer, donc un commit, sans changement


def _stamp() -> str:
    return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"


def _to_file(line: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log(msg: str) -> None:
    line = f"{_stamp()} {msg}"
    print(line, flush=True)
    _to_file(line)


def logp(msg: str, detail: str) -> None:
    """Journal avec detail PRIVE (paire, sens, score) : le detail va dans signals.log mais pas dans la sortie si SIGNALS_QUIET_LOG=1."""
    full = f"{_stamp()} {msg} : {detail}"
    print(f"{_stamp()} {msg}" if QUIET_LOG else full, flush=True)
    _to_file(full)


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(STATE_KEY.encode())


# ------------------------------------------------------------------ etat / historique
def load_state() -> dict:
    st = {"version": 3, "started": utc_now().isoformat(), "last_bar": {}, "next_check": {}, "active": {}, "per_day": {},
          "last_signal": {}, "sched": {}, "previewed": [], "data_fail_runs": 0, "outage_alerted": False,
          "watch": {}, "copy_idx": {}, "pub_posts": {}, "pub_sig": {}, "snap": {}}
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"[STATE] illisible ({e}) - etat neuf")
            return st
        enc = raw.pop("private_enc", None)
        st.update(raw)
        if enc:                                             # donnees privees chiffrees : jamais d'etat neuf en silence
            if not STATE_KEY:
                raise RuntimeError("etat chiffre mais SIGNALS_STATE_KEY est absente : arret pour ne pas perdre les signaux en cours")
            try:
                priv = json.loads(_fernet().decrypt(enc.encode()).decode())
                st.update(priv)
                _PRIV_SEEN.clear()
                _PRIV_SEEN[json.dumps(priv, default=str, sort_keys=True)] = enc
            except Exception as e:
                raise RuntimeError(f"etat chiffre illisible ({type(e).__name__}) : cle incorrecte ? arret") from None
    return st


def save_state(st: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = dict(st)
    if STATE_KEY:
        priv = {k: out.pop(k) for k in PRIVATE_KEYS if k in out}
        canon = json.dumps(json.loads(json.dumps(priv, default=str)), sort_keys=True)
        out["private_enc"] = _PRIV_SEEN.get(canon) or _fernet().encrypt(json.dumps(priv, default=str).encode()).decode()
        _PRIV_SEEN.clear()
        _PRIV_SEEN[canon] = out["private_enc"]
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def append_history(rec: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def load_history() -> List[dict]:
    if not HISTORY_FILE.exists():
        return []
    out = []
    for ln in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def scoped_history(pubs: tuple) -> List[dict]:
    """Historique a annoncer : quand le VIP est en direct, seulement les trades reellement publies aux membres (pas les apercus)."""
    hist = load_history()
    return [r for r in hist if r.get("mode") == "live"] if pubs[0].live else hist


def tf_key_of(sig: Signal) -> str:
    return {60: "1h", 240: "4h"}[int(sig.tf.rstrip("m"))]


def make_pubs(cfg: dict, st: Optional[dict] = None) -> tuple:
    """(publieur VIP, publieur PUBLIC). L'etat `st` est branche sur le publieur public : il compte les messages pour ne mettre
    l'invitation « nous ecrire » que sur 1 message sur 7."""
    pub_vip, pub_pub = Publisher(cfg, log=log, audience="vip"), Publisher(cfg, log=log, audience="public")
    pub_pub.state = st
    return pub_vip, pub_pub


# ------------------------------------------------------------------ exposition (garde-fou de correlation)
def exposures(display: str, direction: str) -> Dict[str, int]:
    s = 1 if direction == "LONG" else -1
    if display in ("US100", "US30", "US500"):
        return {"US_EQUITY": s}
    if display == "GER40":
        return {"EU_EQUITY": s}
    if display == "USOIL":
        return {"OIL": s}
    if len(display) == 6:
        return {display[:3]: s, display[3:]: -s}
    return {display: s}


def net_exposure(active: Dict[str, dict]) -> Dict[str, int]:
    tot: Dict[str, int] = {}
    for rec in active.values():
        sg = rec["signal"]
        for k, v in exposures(sg["display"], sg["direction"]).items():
            tot[k] = tot.get(k, 0) + v
    return tot


# ------------------------------------------------------------------ scan
def scan_symbol(sid: str, cls: str, st: dict, cfg: dict, now: pd.Timestamp) -> list:
    """Retourne [(signal, df, meta), ...] : signaux formes sur les bougies cloturees DEPUIS le dernier passage.
    RATTRAPAGE : le planificateur gratuit de GitHub est irregulier, donc on n'ignore aucune bougie ; un signal en
    retard n'est publie que s'il est encore valable (voir is_fresh). Memorise aussi la zone d'interet la plus proche
    (posts 'on surveille')."""
    tf = cfg["timeframe"]
    tfm = TF_MIN[tf]
    htf, htfm = HTF[tf]
    nc = st["next_check"].get(sid)
    if nc and pd.Timestamp(nc) > now:
        return []
    df1 = get_rates(sid, tf, 700)
    dfh = get_rates(sid, htf, 1000 if htf == "4h" else 800)
    if df1 is None or dfh is None or len(df1) < 300 or len(dfh) < 260:
        _stats["fail"] += 1
        st["next_check"][sid] = (now + pd.Timedelta(minutes=5)).isoformat()
        return []
    _stats["ok"] += 1
    last = df1.index[-1]
    prev = st["last_bar"].get(sid)
    if prev == last.isoformat():                             # pas de nouvelle bougie (marche ferme / pas encore dispo)
        st["next_check"][sid] = (now + pd.Timedelta(minutes=10)).isoformat()
        return []
    meta = symbol_meta(sid)
    if meta is None:
        return []
    bias = htf_bias(dfh, df1.index, tfm, htf_minutes=htfm)
    wl: list = []
    sigs = analyze(df1, bias, meta["point"], sid, sid, tfm, cls, only_last=False, min_score=cfg["min_score"], watch=wl)
    st["watch"][sid] = wl[0] if wl else None
    if prev is None:                                         # premier passage : seulement la derniere bougie (pas d'historique)
        new = [s for s in sigs if s.bar_open == last.isoformat()]
    else:
        pt = pd.Timestamp(prev)
        new = [s for s in sigs if pd.Timestamp(s.bar_open) > pt]
    st["last_bar"][sid] = last.isoformat()
    st["next_check"][sid] = (last + pd.Timedelta(minutes=2 * tfm + 1)).isoformat()
    return [(s, df1, meta) for s in sorted(new, key=lambda x: x.bar_open)]


def is_fresh(sig: Signal, now: pd.Timestamp, cfg: dict) -> Optional[str]:
    """None si le signal est encore publiable, sinon la raison du rejet.
    Un signal en retard reste valable tant que (1) il a moins d'une bougie, (2) le trade n'a pas deja atteint TP1 ou
    son stop, (3) le prix est proche de l'entree."""
    tfm = int(sig.tf.rstrip("m"))
    max_age = float(cfg.get("max_signal_age_min") or (tfm if sd.BACKEND == "yahoo" else 25))
    age = (now - pd.Timestamp(sig.signal_time)).total_seconds() / 60
    if age > max_age:
        return f"perime ({age:.0f} min)"
    if age > 10:                                             # signal en retard : le setup est-il deja consomme ?
        bars = get_rates(sig.symbol, "5m", 3000)
        if bars is not None:
            sim = TradeSim(sig)
            for ts, row in bars[bars.index >= pd.Timestamp(sig.signal_time)].iterrows():
                sim.feed(ts, float(row["high"]), float(row["low"]), float(row["close"]), bar_minutes=5)
                if sim.closed or sim.hit[0]:
                    return "setup deja consomme (TP1 ou stop deja touche)"
    tk = get_tick(sig.symbol)
    if tk is None:
        return "prix indisponible"
    price = tk[1] if sig.direction == "LONG" else tk[0]
    if abs(price - sig.entry) > MAX_ENTRY_DRIFT_R * sig.risk:
        return f"prix trop eloigne de l'entree ({abs(price - sig.entry) / sig.risk:.2f}R)"
    return None


def choose(cands: list, st: dict, cfg: dict, now: pd.Timestamp) -> list:
    day = str(now.date())
    n_today = st["per_day"].get(day, 0)
    active_syms = {rec["signal"]["symbol"] for rec in st["active"].values()}
    net = net_exposure(st["active"])
    chosen = []
    for c in sorted(cands, key=lambda c: (-c[0].score, c[0].risk / c[0].atr)):
        s = c[0]
        why = None
        if s.symbol in active_syms:
            why = "un signal est deja actif sur ce symbole"
        elif st["last_signal"].get(s.symbol) and (now - pd.Timestamp(st["last_signal"][s.symbol])) < pd.Timedelta(hours=6):
            why = "cooldown 6h sur ce symbole"
        elif n_today + len(chosen) >= cfg["max_signals_per_day"]:
            why = f"plafond du jour atteint ({cfg['max_signals_per_day']})"
        elif len(st["active"]) + len(chosen) >= cfg["max_active"]:
            why = "trop de signaux actifs"
        else:
            for k, v in exposures(s.display, s.direction).items():
                if abs(net.get(k, 0) + v) > MAX_NET_EXPOSURE:
                    why = f"exposition {k} deja au maximum"
                    break
        if why:
            logp("[SKIP] signal ecarte", f"{s.display} {s.direction} score {s.score}/6 : {why}")
            continue
        chosen.append(c)
        for k, v in exposures(s.display, s.direction).items():
            net[k] = net.get(k, 0) + v
        active_syms.add(s.symbol)
    return chosen


def publish_signal(sig: Signal, df1: pd.DataFrame, meta: dict, st: dict, pubs: tuple, cfg: dict) -> bool:
    """VIP : TOUS les signaux (fiche complete + analyse).
    PUBLIC : seulement les premiers signaux du jour (public_signals_per_day), en entier, avec "deja dans le VIP" et un lien
    pour nous ecrire sous l'image. Les suivants restent reserves au VIP."""
    pub_vip, pub_pub = pubs
    OUT_DIR.mkdir(exist_ok=True)
    img = OUT_DIR / f"{sig.id}.png"
    render_signal_chart(df1, sig, str(img), digits=meta["digits"], brand=cfg["brand"])
    mid = pub_vip.post_signal(sig, str(img), meta["digits"])
    if mid is None:
        log(f"[PUB] ECHEC publication VIP {sig.id} (Telegram) - non enregistre")
        return False
    now = utc_now()
    day = str(now.date())
    public_id = None
    if st["pub_sig"].get(day, 0) < int(cfg.get("public_signals_per_day", 2)):
        try:
            pimg = OUT_DIR / f"{sig.id}_public.png"
            cta = pub_pub.cta_due()                            # l'invitation n'est sur l'image que 1 message sur 7
            render_signal_chart(df1, sig, str(pimg), digits=meta["digits"], brand=cfg["brand"],
                                footer=content.footer("new", 0.0, cta), contact=pub_pub.handle if cta else "")
            text = content.public_signal(st, sig.display, TF_LABEL.get(sig.tf, sig.tf), sig.direction)
            public_id = pub_pub.post_public(text, img=str(pimg), notify=True, cta=cta)
            if public_id:
                st["pub_sig"][day] = st["pub_sig"].get(day, 0) + 1
        except Exception as e:
            log(f"[PUB] signal public non publie ({e})")
    st["active"][sig.id] = {"signal": sig.to_dict(), "sim": TradeSim(sig).to_dict(), "msg_id": mid,
                            "public_msg_id": public_id, "digits": meta["digits"], "published": now.isoformat(),
                            "mode": "live" if pub_vip.live else "preview"}
    st["per_day"][day] = st["per_day"].get(day, 0) + 1
    st["last_signal"][sig.symbol] = now.isoformat()
    logp(f"[SIGNAL] VIP {'LIVE' if pub_vip.live else 'APERCU'} / PUBLIC "
         f"{('LIVE' if pub_pub.live else 'APERCU') if public_id else 'non montre'}",
         f"{sig.display} {sig.direction} grade {sig.grade} ({sig.score}/6)")
    return True


# ------------------------------------------------------------------ suivi des signaux actifs
def update_active(st: dict, pubs: tuple, cfg: dict) -> None:
    pub_vip, pub_pub = pubs
    for sid, rec in list(st["active"].items()):
        sig = Signal.from_dict(rec["signal"])
        sim = TradeSim.from_dict(rec["sim"])
        bars = get_rates(sig.symbol, "5m", 3000)
        if bars is None:
            _stats["fail"] += 1
            continue
        _stats["ok"] += 1
        t0 = pd.Timestamp(sig.signal_time)
        bars = bars[bars.index >= t0]
        if sim.last_ts:
            bars = bars[bars.index > pd.Timestamp(sim.last_ts)]
        events: List[dict] = []
        for ts, row in bars.iterrows():
            new = sim.feed(ts, float(row["high"]), float(row["low"]), float(row["close"]), bar_minutes=5)
            for ev in new:                                     # etat du trade AU MOMENT de l'evenement (execution en retard : plusieurs d'un coup)
                ev["r_at"], ev["hit_at"] = sim.r, list(sim.hit)
            events += new
            if sim.closed:
                break
        rec["sim"] = sim.to_dict()
        # signal ne en APERCU alors que le VIP est maintenant en direct : jamais annonce aux membres -> suivi silencieux
        orphan = rec.get("mode", "preview") == "preview" and pub_vip.live
        for ev in events:
            if not orphan:
                img = _result_image(sig, ev, sim, rec["digits"], cfg) if ev["type"] in _IMG_EVENTS else None
                pub_vip.post_update(sig, ev, ev.get("r_at", sim.r), rec["msg_id"], img)   # VIP : suivi detaille
                if rec.get("public_msg_id") and cfg.get("public_progress", True):         # PUBLIC : seulement les signaux montres
                    _public_progress(sig, ev, sim, rec["public_msg_id"], rec["digits"], st, pub_pub, cfg)
            logp("[SUIVI] evenement" + (" (apercu, non publie)" if orphan else ""), f"{sig.display} {ev['type']} (R cumule {sim.r:+.2f})")
        if sim.closed:
            append_history({"id": sig.id, "display": sig.display, "direction": sig.direction, "grade": sig.grade,
                            "score": sig.score, "signal_time": sig.signal_time, "entry": sig.entry, "sl": sig.sl,
                            "tps": sig.tps, "outcome": sim.outcome, "r": round(sim.r, 3),
                            "close_time": sim.close_time, "mode": rec.get("mode", "preview"),
                            "public": bool(rec.get("public_msg_id"))})
            del st["active"][sid]
            logp("[CLOTURE] trade clos", f"{sig.display} {sim.outcome} {sim.r:+.2f}R")


_LABELS = {"TP1": ("TP1 ATTEINT", GREEN), "TP3": ("TP3 ATTEINT", GREEN), "SL": ("STOP TOUCHÉ", RED),
           "BE": ("CLÔTURE À L'ENTRÉE", BLUE), "EXPIRED": ("CLÔTURE AU TEMPS", GOLD)}
_IMG_EVENTS = tuple(_LABELS)                     # evenements qui ont une carte de resultat (TP2 : texte seul)


def _result_payload(ev: dict, sim: TradeSim) -> dict:
    """Bandeau de la carte de resultat : a TP1 on annonce +1R sur 40 % ; le resultat TOTAL n'apparait qu'a la cloture.
    Utilise l'etat du trade AU MOMENT de l'evenement (r_at / hit_at) quand il est connu."""
    ev_type = ev["type"]
    r_ev, hit = ev.get("r_at", sim.r), ev.get("hit_at", list(sim.hit))
    label, color = _LABELS.get(ev_type, (ev_type, GOLD))
    sub = "+1R sur 40 % · stop à l'entrée" if ev_type == "TP1" else (
        f"Résultat : {r_ev:+.2f} R" if ev_type == "SL" else f"Résultat final : {r_ev:+.2f} R")
    return {"label": label, "r": r_ev, "sub": sub, "color": color, "hit": hit, "stopped": ev_type == "SL"}


def _result_image(sig: Signal, ev: dict, sim: TradeSim, digits: int, cfg: dict, footer: str = "", contact: str = "") -> Optional[str]:
    """Carte de resultat (avec tous les niveaux). footer / contact : version PUBLIQUE (barre d'invitation en bas)."""
    try:
        df = get_rates(sig.symbol, tf_key_of(sig), 400)
        if df is None:
            return None
        k = df.index.get_loc(pd.Timestamp(sig.bar_open))
        OUT_DIR.mkdir(exist_ok=True)
        p = OUT_DIR / f"{sig.id}_{ev['type']}{'_public' if footer else ''}.png"
        render_signal_chart(df, sig, str(p), digits=digits, n_after=min(len(df) - 1 - k, 40), brand=cfg["brand"],
                            result=_result_payload(ev, sim), footer=footer, contact=contact)
        return str(p)
    except Exception as e:
        log(f"[IMG] resultat sans image ({e})")
        return None


def _public_progress(sig: Signal, ev: dict, sim: TradeSim, reply_to: Optional[int], digits: int, st: dict,
                     pub_pub: Publisher, cfg: dict) -> None:
    """Suivi PUBLIC d'un signal montre (gains ET pertes, meme traitement) : texte humain + carte de resultat + lien."""
    try:
        kind = ev["type"]
        r_ev = ev.get("r_at", sim.r)
        text = content.progress(st, kind, sig.display, r_ev)
        cta = pub_pub.cta_due()
        img = (_result_image(sig, ev, sim, digits, cfg, footer=content.footer(kind, r_ev, cta), contact=pub_pub.handle if cta else "")
               if kind in _IMG_EVENTS else None)
        pub_pub.post_public(text, img=img, reply_to=reply_to, cta=cta)
    except Exception as e:
        log(f"[PUB] suivi public non publie ({e})")


# ------------------------------------------------------------------ bilans
def _label_outcome(o: str) -> str:
    return {"SL": "stop touché", "TP1": "TP1 puis stop à l'entrée", "TP2": "TP2 atteint", "TP3": "TP3 atteint",
            "EXPIRED": "clôturé au temps"}.get(o, o)


def _stats_line(recs: List[dict]) -> str:
    s = summarize([{"r": r["r"], "outcome": r["outcome"]} for r in recs])
    if not s.get("n"):
        return "aucun trade clôturé"
    return f"{s['n']} {'signaux' if s['n'] > 1 else 'signal'} · TP1 {s['tp1']:.0f} % · stops {s['sl']:.0f} % · <b>{s['total_r']:+.1f}R</b>"


def build_recap_image(st: dict, hist: List[dict], now: pd.Timestamp, week_only: bool, cfg: dict) -> Optional[str]:
    recs = hist
    title, sub = "BILAN DEPUIS LE LANCEMENT", f"Suivi depuis le {str(st['started'])[:10]} · pertes incluses"
    if week_only:
        mon = (now - pd.Timedelta(days=now.weekday())).normalize()
        recs = [r for r in hist if pd.Timestamp(r["close_time"]) >= mon]
        title, sub = "BILAN DE LA SEMAINE", f"Semaine du {mon.strftime('%d/%m/%Y')} · pertes incluses"
    if not recs:
        return None
    recs = sorted(recs, key=lambda r: r["close_time"])
    s = summarize([{"r": r["r"], "outcome": r["outcome"]} for r in recs])
    cum, curve = 0.0, [0.0]
    for r in recs:
        cum += r["r"]
        curve.append(cum)
    kpis = [("Signaux clôturés", s["n"], WHITE), ("Atteignent TP1", f"{s['tp1']:.0f}%", GREEN),
            ("Stops touchés", f"{s['sl']:.0f}%", RED), ("Résultat", f"{s['total_r']:+.1f}R", GREEN if s["total_r"] >= 0 else RED)]
    rows = [{"date": pd.Timestamp(r["close_time"]).strftime("%d/%m"), "symbol": r["display"], "dir": r["direction"],
             "outcome": _label_outcome(r["outcome"]).capitalize(), "r": r["r"]} for r in reversed(recs)]
    OUT_DIR.mkdir(exist_ok=True)
    p = OUT_DIR / f"recap_{'week' if week_only else 'all'}_{now:%Y%m%d}.png"
    render_recap_card(title, sub, kpis, rows, curve, str(p), brand=cfg["brand"])
    return str(p)


def post_weekly_recap(st: dict, pubs: tuple, cfg: dict, now: pd.Timestamp) -> bool:
    """Bilan de la semaine : VIP (detail) et PUBLIC (meme carte, ton humain). Gains ET pertes."""
    pub_vip, pub_pub = pubs
    hist = scoped_history(pubs)
    img = build_recap_image(st, hist, now, True, cfg)
    if not img:
        return False
    wk = [r for r in hist if pd.Timestamp(r["close_time"]) >= (now - pd.Timedelta(days=now.weekday())).normalize()]
    pub_vip.post_recap_image(img, f"📊 <b>Bilan de la semaine</b>\nSemaine : {_stats_line(wk)}\nCumul : {_stats_line(hist)}")
    pub_pub.post_public(f"📊 <b>Bilan de la semaine côté VIP</b>\n{_stats_line(wk)}\n\nOn publie tout : les gains comme les pertes.", img=img)
    return True


# ------------------------------------------------------------------ animation du groupe public
def _snapshot_cached(st: dict, now: pd.Timestamp) -> dict:
    """Etat REEL du marche (force des devises, tendances), rafraichi au plus toutes les 6 h."""
    s = st.get("snap") or {}
    if s.get("asof") and s.get("data") and (now - pd.Timestamp(s["asof"])) < pd.Timedelta(hours=6):
        return s["data"]
    data = market.snapshot()
    st["snap"] = {"asof": now.isoformat(), "data": data}
    return data


def _watch_names(st: dict) -> List[str]:
    return [w["display"] for w in (st.get("watch") or {}).values() if w]


def _day_outcomes(day: pd.Timestamp, hist: List[dict]) -> List[dict]:
    return [{"display": r["display"], "label": _label_outcome(r["outcome"]), "r": r["r"]}
            for r in hist if str(r["close_time"])[:10] == str(day.date())]


def _movers_text(st: dict) -> Optional[str]:
    mv = market.movers()
    return content.movers(st, mv) if (mv.get("up") or mv.get("down")) else None


def _crypto_text(st: dict) -> Optional[str]:
    c = market.crypto()
    return content.crypto(st, c) if c else None


def scheduled(st: dict, pubs: tuple, cfg: dict, now: pd.Timestamp) -> None:
    """Calendrier du groupe PUBLIC (heures UTC). Fenetres LARGES : un post part a la premiere execution qui tombe dedans (le
    planificateur gratuit de GitHub peut espacer les executions). Tout est reel : marche calcule, resultats de l'historique.
    Un post n'est marque 'fait' que si Telegram l'a accepte (sinon nouvel essai au cycle suivant).

    semaine  : 06:30 point du matin | 07:30 conseil | 09:05, 13:05, 17:05 scan H4 | 10:30 et 16:30 mouvements du jour |
               12:30 New York | 14:00 rappel compte (mercredi) | 15:00 invitation | 21:30 bilan (+ bilan de la semaine le vendredi)
    week-end : 07:30 conseil | samedi 09:00 mot du week-end | 12:00 point crypto | 15:00 invitation | dimanche 17:30 plan de la semaine"""
    pub_vip, pub_pub = pubs
    sch, hm, wd = st["sched"], now.hour * 60 + now.minute, now.weekday()
    today = str(now.date())
    handle = pub_pub.handle
    cap = int(cfg.get("public_daily_max", 14))

    def in_win(h: int, m: int, length: int) -> bool:
        s0 = h * 60 + m
        return s0 <= hm < s0 + length

    def can(kind: str, key: str) -> bool:
        if sch.get(kind) == key:
            return False
        if not pub_pub.live and kind in st["previewed"]:      # en apercu : un seul exemplaire de chaque type
            return False
        if pub_pub.live and st["pub_posts"].get(today, 0) >= cap:
            return False
        return True

    def push(kind: str, key: str, text: Optional[str], **kw) -> None:
        """Poste ; marque 'fait' seulement si Telegram a accepte le message."""
        if text and pub_pub.post_public(text, **kw):
            sch[kind] = key
            if kind not in st["previewed"]:
                st["previewed"].append(kind)
            st["pub_posts"][today] = st["pub_posts"].get(today, 0) + 1

    n_sig, n_active = st["per_day"].get(today, 0), len(st["active"])
    watch = _watch_names(st)
    # invitation a nous ecrire : c'est LE 7e message (les 6 precedents n'en portent pas). Dans l'apres-midi, ce 7e message est le
    # post d'invitation dedie (une fois par jour) ; a d'autres heures, c'est un message normal qui porte le bouton / le lien.
    if in_win(14, 0, 360) and can("cta", today) and pub_pub.cta_due():
        push("cta", today, content.promo(st, cfg.get("vip_perks", []), handle) if wd in (1, 4) else content.cta(st, handle), cta=True)
    if wd < 5 and in_win(6, 30, 300) and can("morning", today):                           # point du matin (donnees reelles)
        push("morning", today, content.morning(st, _snapshot_cached(st, now), watch, handle))
    if in_win(7, 30, 420) and can("edu", today):                                          # conseil du jour
        push("edu", today, content.education(st))
    # compte rendu apres les clotures H4 de 09:00, 13:00 et 17:00 UTC (bougies ouvertes a 05, 09 et 13 h)
    lb = [pd.Timestamp(v) for k, v in st.get("last_bar", {}).items() if v and UNIVERSE.get(k, {}).get("cls") == "fx"]
    if wd < 5 and lb:
        lo = max(lb)
        if lo.hour in (5, 9, 13) and lo.normalize() == now.normalize() and (now - lo) < pd.Timedelta(hours=8) and can("scan", lo.isoformat()):
            push("scan", lo.isoformat(), content.scan_report(st, (lo + pd.Timedelta(hours=4)).hour,
                                                              int(st.get("n_symbols") or len(lb)), n_sig, n_active, watch))
    if wd < 5 and in_win(10, 30, 120) and can("movers_am", today):                        # mouvements du jour (matin)
        push("movers_am", today, _movers_text(st))
    if wd < 5 and in_win(12, 30, 240) and can("ny", today):                               # ouverture de New York
        push("ny", today, content.new_york(st, n_sig, n_active, len(watch)))
    if wd == 2 and cfg.get("account_link") and in_win(14, 0, 240) and can("account", today):   # rappel : ouvrir un compte
        push("account", today, content.account(st), account=True)
    if wd < 5 and in_win(16, 30, 150) and can("movers_pm", today):                        # mouvements du jour (apres-midi)
        push("movers_pm", today, _movers_text(st))
    if wd == 5 and in_win(9, 0, 300) and can("sat", today):                               # samedi
        push("sat", today, content.weekend_saturday(st, handle))
    if wd >= 5 and in_win(12, 0, 240) and can("crypto", today):                           # point crypto du week-end
        push("crypto", today, _crypto_text(st))
    if wd == 5 and in_win(16, 0, 240) and can("ranking", today):                          # samedi : classement des devises de la semaine
        push("ranking", today, content.ranking(st, _snapshot_cached(st, now)))
    if wd == 6 and in_win(17, 30, 240) and can("sun", today):                             # dimanche soir : plan de la semaine
        push("sun", today, content.weekend_sunday(st, _snapshot_cached(st, now)))

    # bilans : journee de trading qui vient de finir (fenetre 21:30 -> 05:30 UTC le lendemain)
    rd = now.normalize() if hm >= 21 * 60 + 30 else ((now - pd.Timedelta(days=1)).normalize() if hm < 5 * 60 + 30 else None)
    if rd is not None and rd.weekday() < 5:
        dkey = str(rd.date())
        if cfg.get("daily_recap") and can("evening", dkey):
            push("evening", dkey, content.evening(st, _day_outcomes(rd, scoped_history(pubs)), st["per_day"].get(dkey, 0), n_active))
        wkey = f"{rd.isocalendar()[0]}-W{rd.isocalendar()[1]}"
        if cfg.get("weekly_recap") and rd.weekday() == 4 and can("weekly", wkey):
            if post_weekly_recap(st, pubs, cfg, now):
                sch["weekly"] = wkey
                st["pub_posts"][today] = st["pub_posts"].get(today, 0) + 1


def post_now(kind: str) -> None:
    """Poste TOUT DE SUITE un contenu dans le groupe public (reel si PUBLIC_LIVE, sinon apercu dans ton chat) et le marque
    comme fait pour ce creneau : le calendrier ne le reposte pas. kind : edu, brief, ny, cta, promo, account, sat, sun,
    movers, crypto, scan."""
    cfg = load_cfg()
    if not connect(log=log):
        log("Source de donnees indisponible")
        return
    try:
        st = load_state()
        pub_pub = make_pubs(cfg, st)[1]
        now = utc_now()
        today = str(now.date())
        handle = pub_pub.handle
        watch = _watch_names(st)
        n_sig, n_active = st["per_day"].get(today, 0), len(st["active"])
        lb = [pd.Timestamp(v) for k, v in st.get("last_bar", {}).items() if v and UNIVERSE.get(k, {}).get("cls") == "fx"]
        specs = {
            "edu": ("edu", today, lambda: content.education(st), {}),
            "brief": ("morning", today, lambda: content.morning(st, _snapshot_cached(st, now), watch, handle), {}),
            "ny": ("ny", today, lambda: content.new_york(st, n_sig, n_active, len(watch)), {}),
            "cta": ("cta", today, lambda: content.cta(st, handle), {"cta": True}),
            "promo": ("cta", today, lambda: content.promo(st, cfg.get("vip_perks", []), handle), {"cta": True}),
            "account": ("account", today, lambda: content.account(st), {"account": True}),
            "sat": ("sat", today, lambda: content.weekend_saturday(st, handle), {}),
            "sun": ("sun", today, lambda: content.weekend_sunday(st, _snapshot_cached(st, now)), {}),
            "movers": ("movers_am" if now.hour < 14 else "movers_pm", today, lambda: _movers_text(st), {}),
            "crypto": ("crypto", today, lambda: _crypto_text(st), {}),
            "ranking": ("ranking", today, lambda: content.ranking(st, _snapshot_cached(st, now)), {}),
        }
        if lb:
            lo = max(lb)
            specs["scan"] = ("scan", lo.isoformat(), lambda: content.scan_report(
                st, (lo + pd.Timedelta(hours=4)).hour, int(st.get("n_symbols") or len(lb)), n_sig, n_active, watch), {})
        if kind not in specs:
            log(f"Type inconnu : '{kind}'. Types : {', '.join(sorted(specs))}")
            return
        sched_kind, key, build, opts = specs[kind]
        text = build()
        if not text:
            log(f"[POST] '{kind}' : donnees indisponibles, rien publie")
            return
        if pub_pub.post_public(text, **opts):
            st["sched"][sched_kind] = key
            st["pub_posts"][today] = st["pub_posts"].get(today, 0) + 1
            log(f"[POST] '{kind}' publie ({'LIVE' if pub_pub.live else 'APERCU'})")
        else:
            log(f"[POST] '{kind}' : Telegram a refuse le message")
        save_state(st)
    finally:
        shutdown()


# ------------------------------------------------------------------ cycle principal
def run_cycle(st: dict, cfg: dict, pubs: tuple, ids: List[str]) -> None:
    """Un cycle complet. L'etat est TOUJOURS sauvegarde, meme apres une erreur : jamais de publication en double."""
    try:
        _run_cycle(st, cfg, pubs, ids)
    finally:
        save_state(st)


def _run_cycle(st: dict, cfg: dict, pubs: tuple, ids: List[str]) -> None:
    now = utc_now()
    _stats["ok"] = _stats["fail"] = 0
    st["n_symbols"] = len(ids)
    try:
        update_active(st, pubs, cfg)
    except Exception as e:                                    # un suivi en erreur ne bloque pas le scan
        log(f"[SUIVI] erreur : {e} | {traceback.format_exc()[-300:]!r}")
    cands = []
    for sid in ids:
        try:
            found = scan_symbol(sid, UNIVERSE[sid]["cls"], st, cfg, now)
        except Exception as e:
            log(f"[SCAN] {sid} erreur : {e}")
            _stats["fail"] += 1
            continue
        for r in found:
            reason = is_fresh(r[0], now, cfg)
            if reason:
                logp("[SKIP] signal ecarte", f"{sid} {r[0].direction} {r[0].score}/6 : {reason}")
                continue
            cands.append(r)
            logp("[CANDIDAT] setup valide", f"{sid} {r[0].direction} score {r[0].score}/6 grade {r[0].grade}")
    for sig, df1, meta in choose(cands, st, cfg, now):
        publish_signal(sig, df1, meta, st, pubs, cfg)
    try:
        scheduled(st, pubs, cfg, now)
    except Exception as e:
        log(f"[SCHED] erreur : {e}\n{traceback.format_exc()[-400:]}")
    # alerte si la source de prix est indisponible depuis longtemps (au proprietaire uniquement)
    if _stats["ok"] == 0 and _stats["fail"] > 0:
        st["data_fail_runs"] = st.get("data_fail_runs", 0) + 1
        if st["data_fail_runs"] >= OUTAGE_ALERT_RUNS and not st.get("outage_alerted"):
            pubs[0].notify_owner("⚠️ <b>GOTA Signaux</b> : la source de prix ne répond plus depuis "
                                 f"{st['data_fail_runs']} exécutions. Aucun signal ne peut être produit. Je réessaie automatiquement.")
            st["outage_alerted"] = True
    elif _stats["ok"] > 0:
        st["data_fail_runs"], st["outage_alerted"] = 0, False


def loop(once: bool = False) -> None:
    cfg = load_cfg()
    log("=== GOTA SIGNAUX - DEMARRAGE ===")
    pubs = make_pubs(cfg)                                     # (apercu du mode ; l'etat est branche apres son chargement)
    log(f"  backend : {sd.BACKEND} | VIP : {'LIVE' if pubs[0].live else 'APERCU'} | PUBLIC : {'LIVE' if pubs[1].live else 'APERCU'}")
    log(f"  timeframe {cfg['timeframe']} (biais {HTF[cfg['timeframe']][0]}) | score min {cfg['min_score']}/6 | max {cfg['max_signals_per_day']}/jour")
    if not connect(log=log):
        log("Source de donnees indisponible - arret")
        return
    st = load_state()
    while True:
        try:
            if PAUSE_FILE.exists() or str(os.environ.get("SIGNALS_PAUSED", "")).lower() in ("1", "true", "yes"):
                log("[PAUSE] signaux en pause - aucun scan")
            else:
                cfg = load_cfg()
                pubs = make_pubs(cfg, st)
                want = cfg.get("symbols") or []
                ids = [i for i in available_ids() if not want or i in want]
                run_cycle(st, cfg, pubs, ids)
                log(f"[CYCLE] {len(ids)} symboles | donnees ok={_stats['ok']} echec={_stats['fail']} | actifs={len(st['active'])}")
        except Exception as e:
            log(f"[ERREUR] {e}\n{traceback.format_exc()[-600:]}")
            if not once:
                try:
                    shutdown()
                    connect(log=log)
                except Exception:
                    pass
        if once:
            break
        time.sleep(CYCLE_SECONDS)
    shutdown()


# ------------------------------------------------------------------ exemples / tests (jamais de vrai envoi public)
def _closed_examples(cfg: dict) -> list:
    """Signaux HISTORIQUES recents (score >= 4) dont le trade est termine : [(heure, sig, df, meta, sim, tfm)], du plus ancien au plus recent."""
    tf = cfg["timeframe"]
    tfm = TF_MIN[tf]
    htf, htfm = HTF[tf]
    out = []
    for sid in available_ids():
        df1, dfh = get_rates(sid, tf, 900), get_rates(sid, htf, 1000 if htf == "4h" else 800)
        meta = symbol_meta(sid)
        if df1 is None or dfh is None or meta is None:
            continue
        bias = htf_bias(dfh, df1.index, tfm, htf_minutes=htfm)
        for s in analyze(df1, bias, meta["point"], sid, sid, tfm, UNIVERSE[sid]["cls"], min_score=4):
            k = df1.index.get_loc(pd.Timestamp(s.bar_open))
            sim = TradeSim(s)
            for j in range(k + 1, len(df1)):
                sim.feed(df1.index[j], float(df1["high"].iloc[j]), float(df1["low"].iloc[j]), float(df1["close"].iloc[j]), bar_minutes=tfm)
                if sim.closed:
                    break
            if sim.closed:
                out.append((pd.Timestamp(s.signal_time), s, df1, meta, sim, tfm))
    out.sort(key=lambda t: t[0])
    return out


def _demo_result_image(df, sig, ev, sim, digits, cfg, footer: str = "", contact: str = ""):
    k = df.index.get_loc(pd.Timestamp(sig.bar_open))
    tev = pd.Timestamp(ev["time"])
    j = df.index.get_loc(tev) if tev in df.index else len(df) - 1
    p = OUT_DIR / f"demo_{sig.id}_{ev['type']}{'_public' if footer else ''}.png"
    render_signal_chart(df.iloc[: j + 1], sig, str(p), digits=digits, n_after=min(j - k, 40), brand=cfg["brand"],
                        result=_result_payload(ev, sim), footer=footer, contact=contact)
    return str(p)


def _post_example(pubs: tuple, cfg: dict, st: dict, sig: Signal, df1: pd.DataFrame, meta: dict, tfm: int,
                  send: bool = True, max_updates: int = 2, do_vip: bool = True, do_public: bool = True) -> None:
    """Publie un signal HISTORIQUE comme en conditions reelles : VIP (fiche + analyse + suivi) et/ou PUBLIC (fiche + lien + suivi)."""
    pub_vip, pub_pub = pubs
    OUT_DIR.mkdir(exist_ok=True)
    mid = pid = None
    if do_vip:
        img = OUT_DIR / f"demo_{sig.id}.png"
        render_signal_chart(df1, sig, str(img), digits=meta["digits"], brand=cfg["brand"])
        log(f"[EXEMPLE] image VIP : {img}")
        if send:
            mid = pub_vip.post_signal(sig, str(img), meta["digits"])
    if do_public:
        pimg = OUT_DIR / f"demo_{sig.id}_public.png"
        cta = pub_pub.cta_due()
        render_signal_chart(df1, sig, str(pimg), digits=meta["digits"], brand=cfg["brand"],
                            footer=content.footer("new", 0.0, cta), contact=pub_pub.handle if cta else "")
        log(f"[EXEMPLE] image PUBLIC : {pimg}")
        if send:
            pid = pub_pub.post_public(content.public_signal(st, sig.display, TF_LABEL.get(sig.tf, sig.tf), sig.direction),
                                      img=str(pimg), notify=True, cta=cta)
    sim = TradeSim(sig)
    k0 = df1.index.get_loc(pd.Timestamp(sig.bar_open))
    shown = 0
    for j in range(k0 + 1, len(df1)):
        for ev in sim.feed(df1.index[j], float(df1["high"].iloc[j]), float(df1["low"].iloc[j]), float(df1["close"].iloc[j]), bar_minutes=tfm):
            if ev["type"] in _IMG_EVENTS and shown < max_updates:
                if do_vip:
                    p = _demo_result_image(df1, sig, ev, sim, meta["digits"], cfg)
                    if send:
                        pub_vip.post_update(sig, ev, sim.r, mid, p)
                if do_public and cfg.get("public_progress", True):
                    cta = pub_pub.cta_due()
                    pp = _demo_result_image(df1, sig, ev, sim, meta["digits"], cfg, footer=content.footer(ev["type"], sim.r, cta),
                                            contact=pub_pub.handle if cta else "")
                    log(f"[EXEMPLE] image PUBLIC {ev['type']} : {pp}")
                    if send:
                        pub_pub.post_public(content.progress(st, ev["type"], sig.display, sim.r), img=pp, reply_to=pid, cta=cta)
                shown += 1
        if sim.closed:
            break


def _preview_pubs(cfg: dict, native: bool = False, st: Optional[dict] = None) -> tuple:
    """Deux publieurs forces en APERCU (destination = TON chat prive, sans sonnerie). native=True : aucune mention 'apercu'
    (rendu identique aux groupes)."""
    cfg = dict(cfg, live=False, public_live=False)
    pubs = make_pubs(cfg, st)
    for p in pubs:
        p.live, p.target = False, p.owner_chat
        if native:
            p._tag = lambda: ""
    return pubs


def demo() -> None:
    """Envoie UN exemple historique complet en APERCU (VIP + public)."""
    cfg = load_cfg()
    st = load_state()
    pubs = _preview_pubs(cfg, st=st)
    if not connect(log=log):
        log("Source de donnees indisponible")
        return
    ex = _closed_examples(cfg)
    if not ex:
        log("Aucun exemple trouve.")
        shutdown()
        return
    best = max(ex, key=lambda t: (t[0], t[1].score))
    _post_example(pubs, cfg, st, best[1], best[2], best[3], best[5], send="--no-send" not in sys.argv)
    shutdown()


def followers_test() -> None:
    """TEST 'VUE ABONNES' : envoie dans TON chat prive (sans sonnerie, sans mention d'apercu) ce que verra chaque groupe :
    1/2 le groupe VIP : un signal complet (fiche + analyse + suivi)
    2/2 le groupe PUBLIC : les signaux montres en entier ("deja dans le VIP") + leur suivi (un gain, une perte) + le rythme de la journee.
    Exemples HISTORIQUES : rien n'est publie dans les vrais groupes."""
    cfg = load_cfg()
    st = load_state()
    st["copy_idx"] = {}                                   # le test montre toujours les premieres variantes
    st["cta_seq"] = 0                                     # ... et le rythme des invitations depuis le debut (1 message sur 7)
    pubs = _preview_pubs(cfg, native=True, st=st)
    pub_vip, pub_pub = pubs
    send = "--no-send" not in sys.argv
    if not connect(log=log):
        log("Source de donnees indisponible")
        return
    ex = _closed_examples(cfg)
    wins = [e for e in ex if e[4].outcome in ("TP2", "TP3")] or [e for e in ex if e[4].outcome == "TP1"]
    loss = [e for e in ex if e[4].outcome == "SL"]
    if not wins or not loss:
        log(f"Exemples insuffisants (gagnants={len(wins)}, perdants={len(loss)})")
        shutdown()
        return
    picks = (("GAGNANT", max(wins, key=lambda t: t[0])), ("PERDANT", max(loss, key=lambda t: t[0])))
    if send:
        pub_vip.send_text("🧪 <b>Test « vue abonnés » — 1/2 : le groupe VIP</b>\nLe VIP reçoit <b>tous</b> les signaux : la fiche complète, "
                          "l'analyse détaillée, puis le suivi. Exemple historique (un gagnant), visible uniquement par toi.")
    log(f"[TEST] VIP : {picks[0][1][1].id} -> {picks[0][1][4].outcome} {picks[0][1][4].r:+.2f}R")
    e0 = picks[0][1]
    _post_example(pubs, cfg, st, e0[1], e0[2], e0[3], e0[5], send=send, max_updates=1, do_vip=True, do_public=False)
    if send:
        pub_pub.send_text(f"🧪 <b>Test « vue abonnés » — 2/2 : le groupe public</b>\nLe public voit <b>quelques signaux par jour</b> "
                          f"({cfg.get('public_signals_per_day', 2)} maximum), en entier, avec « déjà dans le VIP », le lien pour nous écrire sous "
                          "chaque image et le suivi (gain comme perte). Puis le rythme de la journée. Exemples historiques.")
    for label, e in picks:
        log(f"[TEST] PUBLIC {label} : {e[1].id} -> {e[4].outcome} {e[4].r:+.2f}R")
        _post_example(pubs, cfg, st, e[1], e[2], e[3], e[5], send=send, max_updates=2 if label == "GAGNANT" else 1, do_vip=False, do_public=True)
    if send:
        snap = market.snapshot()
        pub_pub.post_public(content.morning(st, snap, ["EURJPY", "AUDUSD"], pub_pub.handle))
        outs = [{"display": e[1].display, "label": _label_outcome(e[4].outcome), "r": e[4].r} for _, e in picks]
        pub_pub.post_public(content.evening(st, outs, 2, 0))
        pub_pub.post_public(content.education(st))
        pub_pub.post_public(content.cta(st, pub_pub.handle), cta=True)
    shutdown()


def setup_wizard() -> None:
    cfg = load_cfg()
    print("\n=== Assistant GOTA Signaux ===\n")
    print("Ajoute le bot comme ADMINISTRATEUR de tes groupes (Telegram > groupe > Administrateurs > Ajouter).\n")
    for key, label in (("vip_chat_id", "Identifiant du groupe VIP (-100...)"), ("channel_id", "Groupe public (@nom ou -100...)"),
                       ("contact_link", "Lien de contact pour 'nous ecrire' (https://t.me/ton_utilisateur)")):
        cfg[key] = input(f"{label} [{cfg.get(key) or 'vide'}] : ").strip() or cfg.get(key, "")
    save_cfg(cfg)
    for aud in ("vip", "public"):
        print(Publisher(cfg, audience=aud).check_target()["detail"])
    print("\nMode actuel : APERCU. Pour publier pour de bon, mets \"live\" (VIP) et/ou \"public_live\" (public) a true dans channel.json.")


KNOWN_ARGS = {"--once", "--setup", "--check-channel", "--test", "--demo", "--brief", "--edu", "--cta", "--promo", "--recap",
              "--pinned", "--post-pinned", "--post", "--account", "--no-send"}


def main() -> None:
    a = sys.argv[1:]
    unknown = [x for x in a if x.startswith("--") and x not in KNOWN_ARGS]
    if unknown:                                               # jamais de boucle infinie sur une faute de frappe
        print("Option inconnue : " + " ".join(unknown) + "\n\n" + (__doc__ or ""))
        return
    if "--setup" in a:
        return setup_wizard()
    if "--check-channel" in a:
        cfg = load_cfg()
        for aud in ("vip", "public"):
            print(Publisher(cfg, audience=aud).check_target()["detail"])
        return
    if "--post" in a:                                         # poste tout de suite un contenu du groupe public
        i = a.index("--post")
        return post_now(a[i + 1] if i + 1 < len(a) else "")
    if "--post-pinned" in a:                                  # guide VIP + accueil public, epingles (reel si le groupe est en direct)
        pub_vip, pub_pub = make_pubs(load_cfg())
        pub_vip.post_pinned()
        pub_pub.post_welcome()
        return
    if "--test" in a:
        return followers_test()
    if "--demo" in a:
        return demo()
    if any(x in a for x in ("--brief", "--edu", "--cta", "--promo", "--account", "--recap", "--pinned")):
        cfg = load_cfg()
        st = load_state()
        pub_vip, pub_pub = _preview_pubs(cfg, st=st)
        now = utc_now()
        if "--pinned" in a:
            pub_vip.send_text(pinned_text(cfg["brand"], cfg.get("account_link", "")), account=True)
            pub_pub.send_text("<b>Description du groupe public</b> (Telegram > groupe > Modifier > Description) :\n\n" + CHANNEL_ABOUT_PUBLIC)
        if "--brief" in a:
            pub_pub.post_public(content.morning(st, market.snapshot(), _watch_names(st), pub_pub.handle))
        if "--edu" in a:
            pub_pub.post_public(content.education(st))
        if "--cta" in a:
            pub_pub.post_public(content.cta(st, pub_pub.handle), cta=True)
        if "--promo" in a:
            pub_pub.post_public(content.promo(st, cfg.get("vip_perks", []), pub_pub.handle), cta=True)
        if "--account" in a:
            pub_pub.post_public(content.account(st), account=True)
        if "--recap" in a:
            img = build_recap_image(st, load_history(), now, False, cfg)
            if img:
                pub_pub.post_recap_image(img, "📊 <b>Bilan depuis le lancement</b>")
            else:
                print("Aucun trade clôturé dans l'historique pour l'instant.")
        return
    loop(once="--once" in a)


if __name__ == "__main__":
    main()
