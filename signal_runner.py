"""
signal_runner.py - Canal de signaux GOTA Confluence. Tourne :
  * en CLOUD (GitHub Actions, gratuit, PC eteint)  : python signal_runner.py --once     (backend yahoo)
  * en LOCAL (PC Windows + MT5)                   : python signal_runner.py            (boucle 60 s)

    python signal_runner.py --once          # un seul cycle (mode cloud)
    python signal_runner.py --demo          # envoie un EXEMPLE (signal historique) en APERCU
    python signal_runner.py --demo --no-send# genere les images en local sans rien envoyer
    python signal_runner.py --test          # TEST 'vue abonnes' dans ton chat prive (gagnant + perdant + conseil)
    python signal_runner.py --check-channel # verifie que le bot peut publier (ne poste rien)
    python signal_runner.py --setup         # assistant : canal, lien prive, activation
    python signal_runner.py --recap|--promo|--edu   # apercus de contenu

MODE APERCU PAR DEFAUT : rien n'est publie a ta communaute tant que "live" n'est pas active (channel.json
ou variable GitHub SIGNALS_LIVE=true) ET qu'un canal est configure. LECTURE SEULE cote marche : aucun ordre.
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
from signal_publisher import Publisher, load_cfg, save_cfg, pinned_text, CHANNEL_ABOUT
from chart_render import render_signal_chart, render_recap_card, GREEN, RED, GOLD, BLUE, WHITE

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


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ etat / historique
def load_state() -> dict:
    st = {"version": 2, "started": utc_now().isoformat(), "last_bar": {}, "next_check": {}, "active": {},
          "per_day": {}, "last_signal": {}, "sched": {}, "previewed": [], "data_fail_runs": 0, "outage_alerted": False}
    if STATE_FILE.exists():
        try:
            st.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            log(f"[STATE] illisible ({e}) - etat neuf")
    return st


def save_state(st: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
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


def tf_key_of(sig: Signal) -> str:
    return {60: "1h", 240: "4h"}[int(sig.tf.rstrip("m"))]


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
    RATTRAPAGE : le planificateur gratuit de GitHub est irregulier (en pratique ~1 execution / 1h30 au lieu de 15 min),
    donc on n'ignore aucune bougie ; un signal en retard n'est publie que s'il est encore valable (voir is_fresh)."""
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
    sigs = analyze(df1, bias, meta["point"], sid, sid, tfm, cls, only_last=False, min_score=cfg["min_score"])
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
            log(f"[SKIP] {s.display} {s.direction} score {s.score}/6 : {why}")
            continue
        chosen.append(c)
        for k, v in exposures(s.display, s.direction).items():
            net[k] = net.get(k, 0) + v
        active_syms.add(s.symbol)
    return chosen


def publish_signal(sig: Signal, df1: pd.DataFrame, meta: dict, st: dict, pub: Publisher, cfg: dict) -> bool:
    OUT_DIR.mkdir(exist_ok=True)
    img = OUT_DIR / f"{sig.id}.png"
    render_signal_chart(df1, sig, str(img), digits=meta["digits"], brand=cfg["brand"])
    mid = pub.post_signal(sig, str(img), meta["digits"])
    if mid is None:
        log(f"[PUB] ECHEC publication {sig.id} (Telegram) - non enregistre")
        return False
    now = utc_now()
    st["active"][sig.id] = {"signal": sig.to_dict(), "sim": TradeSim(sig).to_dict(), "msg_id": mid,
                            "digits": meta["digits"], "published": now.isoformat(),
                            "mode": "live" if pub.live else "preview"}
    st["per_day"][str(now.date())] = st["per_day"].get(str(now.date()), 0) + 1
    st["last_signal"][sig.symbol] = now.isoformat()
    log(f"[SIGNAL] {'LIVE' if pub.live else 'APERCU'} {sig.display} {sig.direction} grade {sig.grade} ({sig.score}/6) "
        f"entree {sig.entry:.{meta['digits']}f} SL {sig.sl:.{meta['digits']}f} TP1 {sig.tps[0]:.{meta['digits']}f}")
    return True


# ------------------------------------------------------------------ suivi des signaux actifs
def update_active(st: dict, pub: Publisher, cfg: dict) -> None:
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
            events += sim.feed(ts, float(row["high"]), float(row["low"]), float(row["close"]), bar_minutes=5)
            if sim.closed:
                break
        rec["sim"] = sim.to_dict()
        for ev in events:
            img = None
            if ev["type"] in ("TP1", "TP3", "SL", "BE", "EXPIRED"):
                img = _result_image(sig, ev, sim, rec["digits"], cfg)
            pub.post_update(sig, ev, sim.r, rec["msg_id"], img)
            log(f"[SUIVI] {sig.display} {ev['type']} @ {ev['price']:.{rec['digits']}f}  (R cumule {sim.r:+.2f})")
        if sim.closed:
            append_history({"id": sig.id, "display": sig.display, "direction": sig.direction, "grade": sig.grade,
                            "score": sig.score, "signal_time": sig.signal_time, "entry": sig.entry, "sl": sig.sl,
                            "tps": sig.tps, "outcome": sim.outcome, "r": round(sim.r, 3),
                            "close_time": sim.close_time, "mode": rec.get("mode", "preview")})
            del st["active"][sid]
            log(f"[CLOTURE] {sig.display} {sim.outcome} {sim.r:+.2f}R")


_LABELS = {"TP1": ("TP1 ATTEINT", GREEN), "TP3": ("TP3 ATTEINT", GREEN), "SL": ("STOP TOUCHÉ", RED),
           "BE": ("CLÔTURE À L'ENTRÉE", BLUE), "EXPIRED": ("SETUP EXPIRÉ", GOLD)}


def _result_payload(ev_type: str, sim: TradeSim) -> dict:
    """Bandeau de la carte de resultat : a TP1 on annonce +1R sur 40 % ; le resultat TOTAL n'apparait qu'a la cloture."""
    label, color = _LABELS.get(ev_type, (ev_type, GOLD))
    sub = "+1R sur 40 % · stop à l'entrée" if ev_type == "TP1" else (
        f"Résultat : {sim.r:+.2f} R" if ev_type == "SL" else f"Résultat final : {sim.r:+.2f} R")
    return {"label": label, "r": sim.r, "sub": sub, "color": color, "hit": list(sim.hit), "stopped": ev_type == "SL"}


def _result_image(sig: Signal, ev: dict, sim: TradeSim, digits: int, cfg: dict) -> Optional[str]:
    try:
        df = get_rates(sig.symbol, tf_key_of(sig), 400)
        if df is None:
            return None
        k = df.index.get_loc(pd.Timestamp(sig.bar_open))
        OUT_DIR.mkdir(exist_ok=True)
        p = OUT_DIR / f"{sig.id}_{ev['type']}.png"
        render_signal_chart(df, sig, str(p), digits=digits, n_after=min(len(df) - 1 - k, 40), brand=cfg["brand"],
                            result=_result_payload(ev["type"], sim))
        return str(p)
    except Exception as e:
        log(f"[IMG] resultat sans image ({e})")
        return None


# ------------------------------------------------------------------ bilans
def _label_outcome(o: str) -> str:
    return {"SL": "Stop touché", "TP1": "TP1 puis stop à l'entrée", "TP2": "TP2 atteint", "TP3": "TP3 atteint",
            "EXPIRED": "Expiré"}.get(o, o)


def _stats_line(recs: List[dict]) -> str:
    s = summarize([{"r": r["r"], "outcome": r["outcome"]} for r in recs])
    if not s.get("n"):
        return "aucun trade clôturé"
    return f"{s['n']} signaux · TP1 {s['tp1']:.0f} % · stops {s['sl']:.0f} % · <b>{s['total_r']:+.1f}R</b>"


def post_daily_recap(st: dict, pub: Publisher, now: pd.Timestamp) -> bool:
    hist = load_history()
    today = [r for r in hist if str(r["close_time"])[:10] == str(now.date())]
    if not today:
        return False
    tot = sum(r["r"] for r in today)
    rows = "\n".join(f"• {r['display']} {'ACHAT' if r['direction'] == 'LONG' else 'VENTE'} → {_label_outcome(r['outcome'])} ({r['r']:+.2f}R)"
                     for r in today)
    text = (f"📊 <b>Bilan du {now.strftime('%d/%m')}</b>\n{rows}\n\n"
            f"Jour : <b>{tot:+.2f}R</b>  ·  Cumul : {_stats_line(hist)}")
    return pub.send_text(text, button=True) is not None


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
             "outcome": _label_outcome(r["outcome"]), "r": r["r"]} for r in reversed(recs)]
    OUT_DIR.mkdir(exist_ok=True)
    p = OUT_DIR / f"recap_{'week' if week_only else 'all'}_{now:%Y%m%d}.png"
    render_recap_card(title, sub, kpis, rows, curve, str(p), brand=cfg["brand"])
    return str(p)


def post_weekly_recap(st: dict, pub: Publisher, cfg: dict, now: pd.Timestamp) -> bool:
    hist = load_history()
    img = build_recap_image(st, hist, now, True, cfg)
    if not img:
        return False
    wk = [r for r in hist if pd.Timestamp(r["close_time"]) >= (now - pd.Timedelta(days=now.weekday())).normalize()]
    cap = f"📊 <b>Bilan de la semaine</b>\nSemaine : {_stats_line(wk)}\nCumul : {_stats_line(hist)}"
    return pub.post_recap_image(img, cap) is not None


# ------------------------------------------------------------------ posts programmes
def scheduled(st: dict, pub: Publisher, cfg: dict, now: pd.Timestamp) -> None:
    """Posts programmes. Fenetres LARGES : le planificateur gratuit de GitHub peut espacer les executions de plusieurs heures."""
    sch, hm = st["sched"], now.hour * 60 + now.minute
    today = str(now.date())

    def in_win(h: int, m: int, length: int) -> bool:
        start = h * 60 + m
        return start <= hm < start + length

    def once_ok(kind: str, key: str) -> bool:
        if sch.get(kind) == key:
            return False
        if not pub.live and kind in st["previewed"]:      # en apercu : un seul exemplaire de chaque type
            return False
        return True

    if cfg.get("education_posts_per_day", 0) and in_win(8, 30, 360) and once_ok("edu", today):
        pub.post_education(now.timetuple().tm_yday)
        sch["edu"] = today
        st["previewed"].append("edu")
    if (cfg.get("promo_posts_per_day", 0) and str(cfg.get("private_link", "")).startswith("http")
            and in_win(10, 30, 360) and once_ok("promo", today)):
        pub.post_promo()
        sch["promo"] = today
        st["previewed"].append("promo")

    # bilans : journee de trading qui vient de finir (fenetre 21:30 -> 05:30 UTC le lendemain)
    if hm >= 21 * 60 + 30:
        rd = now.normalize()
    elif hm < 5 * 60 + 30:
        rd = (now - pd.Timedelta(days=1)).normalize()
    else:
        rd = None
    if rd is not None and rd.weekday() < 5:
        dkey = str(rd.date())
        if cfg.get("daily_recap") and once_ok("daily", dkey):
            post_daily_recap(st, pub, rd)
            sch["daily"] = dkey
            st["previewed"].append("daily")
        wkey = f"{rd.isocalendar()[0]}-W{rd.isocalendar()[1]}"
        if cfg.get("weekly_recap") and rd.weekday() == 4 and once_ok("weekly", wkey):
            post_weekly_recap(st, pub, cfg, now)
            sch["weekly"] = wkey
            st["previewed"].append("weekly")


# ------------------------------------------------------------------ cycle principal
def run_cycle(st: dict, cfg: dict, pub: Publisher, ids: List[str]) -> None:
    now = utc_now()
    _stats["ok"] = _stats["fail"] = 0
    update_active(st, pub, cfg)
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
                log(f"[SKIP] {sid} {r[0].direction} {r[0].score}/6 : {reason}")
                continue
            cands.append(r)
            log(f"[CANDIDAT] {sid} {r[0].direction} score {r[0].score}/6 grade {r[0].grade}")
    for sig, df1, meta in choose(cands, st, cfg, now):
        publish_signal(sig, df1, meta, st, pub, cfg)
    scheduled(st, pub, cfg, now)
    # alerte si la source de prix est indisponible depuis longtemps (au proprietaire uniquement)
    if _stats["ok"] == 0 and _stats["fail"] > 0:
        st["data_fail_runs"] = st.get("data_fail_runs", 0) + 1
        if st["data_fail_runs"] >= OUTAGE_ALERT_RUNS and not st.get("outage_alerted"):
            pub.notify_owner("⚠️ <b>GOTA Signaux</b> : la source de prix ne répond plus depuis "
                             f"{st['data_fail_runs']} cycles. Aucun signal ne peut être produit. Je réessaie automatiquement.")
            st["outage_alerted"] = True
    elif _stats["ok"] > 0:
        st["data_fail_runs"], st["outage_alerted"] = 0, False
    save_state(st)


def loop(once: bool = False) -> None:
    cfg = load_cfg()
    log("=== GOTA SIGNAUX - DEMARRAGE ===")
    pub = Publisher(cfg, log=log)
    log(f"  backend : {sd.BACKEND} | mode : {'LIVE (canal ' + str(cfg['channel_id']) + ')' if pub.live else 'APERCU (chat prive uniquement)'}")
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
                pub = Publisher(cfg, log=log)
                want = cfg.get("symbols") or []
                ids = [i for i in available_ids() if not want or i in want]
                run_cycle(st, cfg, pub, ids)
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


# ------------------------------------------------------------------ demo / outils
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


def _post_example(pub: Publisher, cfg: dict, sig: Signal, df1: pd.DataFrame, meta: dict, tfm: int,
                  send: bool = True, max_updates: int = 2) -> None:
    """Publie un signal historique (image + legende) puis ses mises a jour de resultat, comme en conditions reelles."""
    OUT_DIR.mkdir(exist_ok=True)
    img = OUT_DIR / f"demo_{sig.id}.png"
    render_signal_chart(df1, sig, str(img), digits=meta["digits"], brand=cfg["brand"])
    log(f"[EXEMPLE] image : {img}")
    print("\n--- LEGENDE ---\n" + pub.signal_caption(sig, meta["digits"]).replace("<b>", "").replace("</b>", "")
          .replace("<code>", "").replace("</code>", "").replace("<i>", "").replace("</i>", "") + "\n")
    mid = pub.post_signal(sig, str(img), meta["digits"]) if send else None
    if send:
        log(f"[EXEMPLE] signal {sig.id} envoye (message {mid})")
    sim = TradeSim(sig)
    k0 = df1.index.get_loc(pd.Timestamp(sig.bar_open))
    shown = 0
    for j in range(k0 + 1, len(df1)):
        for ev in sim.feed(df1.index[j], float(df1["high"].iloc[j]), float(df1["low"].iloc[j]), float(df1["close"].iloc[j]), bar_minutes=tfm):
            if ev["type"] in ("TP1", "TP3", "SL", "BE", "EXPIRED") and shown < max_updates:
                p = _demo_result_image(df1, sig, ev, sim, meta["digits"], cfg)
                log(f"[EXEMPLE] image resultat ({ev['type']}) : {p}")
                if send:
                    pub.post_update(sig, ev, sim.r, mid, p)
                shown += 1
        if sim.closed:
            break


def demo() -> None:
    """Envoie un EXEMPLE en APERCU : un signal historique recent (avec son resultat) de la watchlist."""
    cfg = load_cfg()
    cfg["live"] = False
    pub = Publisher(cfg, log=log)
    pub.live, pub.target = False, pub.owner_chat
    pub._tag = lambda: ("🧪 <b>EXEMPLE</b> · signal <u>historique</u> pour montrer le rendu — "
                        "ce n'est PAS un signal en cours, et il n'est visible que par toi\n\n")
    if not connect(log=log):
        log("Source de donnees indisponible")
        return
    ex = _closed_examples(cfg)
    if not ex:
        log("Aucun exemple trouve.")
        shutdown()
        return
    best = max(ex, key=lambda t: (t[0], t[1].score))
    _post_example(pub, cfg, best[1], best[2], best[3], best[5], send="--no-send" not in sys.argv)
    shutdown()


def followers_test() -> None:
    """TEST 'VUE ABONNES' : envoie dans TON chat prive exactement ce que verront tes abonnes (aucune mention d'apercu) :
    un signal gagnant + ses resultats, un signal perdant + son stop, un conseil du jour. Exemples historiques."""
    cfg = load_cfg()
    cfg["live"] = False
    if not str(cfg.get("private_link", "")).startswith("http"):
        cfg["private_link"] = "https://t.me/GotatradingBot"        # juste pour montrer le bouton (sera ton canal prive)
    pub = Publisher(cfg, log=log)
    pub.live, pub.target = False, pub.owner_chat                   # envoi UNIQUEMENT dans ton chat prive
    pub._tag = lambda: ""                                          # rendu natif : identique a ce que voit un abonne
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
    if send:
        pub.send_text("🧪 <b>Test « vue abonnés »</b>\nVoici exactement ce que verront tes abonnés : un signal gagnant, "
                      "un signal perdant, un conseil du jour. Exemples historiques, visibles uniquement par toi.")
    for label, e in (("GAGNANT", max(wins, key=lambda t: t[0])), ("PERDANT", max(loss, key=lambda t: t[0]))):
        log(f"[TEST] exemple {label} : {e[1].id} -> {e[4].outcome} {e[4].r:+.2f}R")
        _post_example(pub, cfg, e[1], e[2], e[3], e[5], send=send, max_updates=2 if label == "GAGNANT" else 1)
    if send:
        pub.post_education(utc_now().timetuple().tm_yday)
    shutdown()


def _demo_result_image(df, sig, ev, sim, digits, cfg):
    k = df.index.get_loc(pd.Timestamp(sig.bar_open))
    tev = pd.Timestamp(ev["time"])
    j = df.index.get_loc(tev) if tev in df.index else len(df) - 1
    p = OUT_DIR / f"demo_{sig.id}_{ev['type']}.png"
    render_signal_chart(df.iloc[: j + 1], sig, str(p), digits=digits, n_after=min(j - k, 40), brand=cfg["brand"],
                        result=_result_payload(ev["type"], sim))
    return str(p)


def setup_wizard() -> None:
    cfg = load_cfg()
    print("\n=== Assistant du canal de signaux GOTA TRADING ===\n")
    print("Avant de continuer, fais ceci dans Telegram :")
    print("  1. Cree ton canal PUBLIC (ex: @gota_signaux)")
    print("  2. Canal -> Administrateurs -> Ajouter -> choisis TON bot -> coche 'Publier des messages'")
    print("  3. Cree ton canal PRIVE et copie son lien d'invitation (https://t.me/+xxxx)\n")
    ch = input(f"Canal public (@nom ou -100...) [{cfg.get('channel_id') or 'vide'}] : ").strip() or cfg.get("channel_id", "")
    lk = input(f"Lien du canal prive [{cfg.get('private_link') or 'vide'}] : ").strip() or cfg.get("private_link", "")
    cfg["channel_id"], cfg["private_link"] = ch, lk
    save_cfg(cfg)
    pub = Publisher(cfg)
    res = pub.check_channel()
    print("\nVerification :", res["detail"])
    if not res["ok"]:
        print("Corrige puis relance l'assistant. Le mode reste en APERCU (rien n'est publie).")
        return
    if input("\nEnvoyer un message de test dans le canal ? (o/N) : ").strip().lower() == "o":
        Publisher(dict(cfg, live=True)).send_text("✅ Connexion OK — le bot est prêt à publier ici.")
    print("\nMode actuel : APERCU. Les signaux arrivent seulement dans ton chat prive.")
    print("Conseil : laisse tourner en apercu plusieurs semaines pour juger les resultats REELS avant de publier.")
    if input("Tape PUBLIER pour activer la publication REELLE dans le canal (Entree = rester en apercu) : ").strip() == "PUBLIER":
        cfg["live"] = True
        save_cfg(cfg)
        print("Publication reelle ACTIVEE. (Pour revenir en apercu : \"live\": false dans channel.json)")
    else:
        cfg["live"] = False
        save_cfg(cfg)
        print("Reste en APERCU.")


def main() -> None:
    a = sys.argv[1:]
    if "--setup" in a:
        return setup_wizard()
    if "--check-channel" in a:
        print(Publisher(load_cfg()).check_channel()["detail"])
        return
    if "--test" in a:
        return followers_test()
    if "--demo" in a:
        return demo()
    if any(x in a for x in ("--recap", "--promo", "--edu", "--pinned")):
        cfg = load_cfg()
        cfg["live"] = False
        pub = Publisher(cfg, log=log)
        pub.live, pub.target = False, pub.owner_chat
        st = load_state()
        now = utc_now()
        if "--pinned" in a:
            # apercu du message a epingler + du texte de description du canal (a coller a la main dans Telegram)
            pub.send_text(pinned_text(cfg["brand"]))
            pub.send_text("<b>Description du canal</b> (Telegram → canal → Modifier → Description) :\n\n" + CHANNEL_ABOUT)
        if "--promo" in a:
            pub.post_promo()
        if "--edu" in a:
            pub.post_education(now.timetuple().tm_yday)
        if "--recap" in a:
            img = build_recap_image(st, load_history(), now, False, cfg)
            if img:
                pub.post_recap_image(img, "📊 <b>Bilan depuis le lancement</b> (aperçu)")
            else:
                print("Aucun trade clôturé dans l'historique pour l'instant.")
        return
    loop(once="--once" in a)


if __name__ == "__main__":
    main()
