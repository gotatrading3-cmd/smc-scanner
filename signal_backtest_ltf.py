"""
signal_backtest_ltf.py - Backtest HONNETE de l'analyse multi-timeframes : entrees de scalp (M15 / H1) dans les zones de l'analyse H4.

L'analyse d'origine (H4, biais journalier) n'est PAS modifiee. Ici on mesure ce qu'apporte l'utilisation des timeframes plus petits :
  - "zone H4" : on n'accepte une entree M15 / H1 que si la bougie d'entree touche une zone H4 (OB / FVG) encore active, dans le sens de
    la tendance journaliere - donc dans le contexte de l'analyse H4 ;
  - on compare TOUJOURS a la meme entree SANS cette condition (meme timeframe, meme biais), pour savoir si la condition aide vraiment.
Memes regles conservatrices que le backtest d'origine : spread, SL avant TP dans une bougie ambigue, stop a l'entree apres TP1, expiration.
Option --delay 1 : entree decalee d'une bougie (cloture de la bougie suivante), prix ecarte de plus de 0.35R => trade non pris
(ce que vivent des abonnes qui recopient le signal quelques minutes apres sa publication).
Separation ENTRAINEMENT (60 %) / TEST (40 %) : ce qui compte, c'est le TEST. Un trade par symbole a la fois.

Usage :
    python signal_backtest_ltf.py --source cache            # historique MT5 deja en cache (H4 / H1, ~4 ans) : AUCUN acces MT5
    python signal_backtest_ltf.py --source yahoo --delay 1  # donnees Yahoo = celles du robot en cloud (M15 : 60 jours seulement)
    python signal_backtest_ltf.py --source yahoo --symbols EURUSD,GBPUSD --variants "M15/D1,M15/D1 + zone H4"
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

if "--source" in sys.argv and sys.argv[sys.argv.index("--source") + 1] == "yahoo":
    os.environ["SIGNAL_BACKEND"] = "yahoo"           # avant l'import de signal_data : aucune connexion MT5, jamais

import numpy as np
import pandas as pd

import signal_data as sd
from signal_engine import analyze, htf_bias, htf_zones_of, TradeSim, summarize, PARAMS, SCORED

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

DIR = Path(__file__).parent
MAX_ENTRY_DRIFT_R = 0.35
TFM = {"15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
LABEL = {"15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4", "1d": "D1"}

# nom, timeframe d'entree, timeframes de biais (tous d'accord), timeframe des zones exigees (None = pas de condition), parametres propres
VARIANTS = [
    dict(name="H4/D1 (analyse actuelle)", entry="4h", bias=["1d"], zone=None),
    dict(name="H1/H4", entry="1h", bias=["4h"], zone=None),
    dict(name="H1/D1", entry="1h", bias=["1d"], zone=None),
    dict(name="H1/D1 + zone H4", entry="1h", bias=["1d"], zone="4h"),
    dict(name="H1/H4 + zone H4", entry="1h", bias=["4h"], zone="4h"),
    dict(name="M15/H1", entry="15m", bias=["1h"], zone=None),
    dict(name="M15/D1", entry="15m", bias=["1d"], zone=None),
    dict(name="M15/D1 + zone H4", entry="15m", bias=["1d"], zone="4h"),
    dict(name="M15/D1 + zone H1", entry="15m", bias=["1d"], zone="1h"),
    dict(name="M15/D1+H4 + zone H4", entry="15m", bias=["1d", "4h"], zone="4h"),
    dict(name="M30/H1", entry="30m", bias=["1h"], zone=None),
    dict(name="M30/D1", entry="30m", bias=["1d"], zone=None),
    dict(name="M30/D1 + zone H4", entry="30m", bias=["1d"], zone="4h"),
]


def load_tf(source: str, mt5_name: str, sid: str, tf: str):
    if source == "cache":
        fs = sorted(sd.CACHE_DIR.glob(f"{mt5_name}_{tf}_*.pkl"), key=lambda f: int(f.stem.rsplit("_", 1)[-1]))
        return pd.read_pickle(fs[-1]) if fs else None
    n = {"15m": 10000, "30m": 6000, "1h": 20000, "4h": 6000, "1d": 4000}[tf]
    return sd.get_rates(sid, tf, n, closed_only=True)


def combined_bias(entry_df: pd.DataFrame, tfm: int, bias_tfs: list, data: dict) -> np.ndarray:
    out = None
    for tf in bias_tfs:
        b = htf_bias(data[tf], entry_df.index, tfm, htf_minutes=TFM[tf])
        out = b if out is None else np.where(b == out, b, 0).astype(np.int8)
    return out


def simulate(sig, o, h, l, c, idx, i0: int, tfm: int, delay: int):
    """Trade simule a partir de la bougie du signal i0. delay=1 : entree a la cloture de la bougie suivante (None si le prix s'est trop ecarte)."""
    ts = TradeSim(sig)
    start = i0 + 1
    if delay:
        j = i0 + delay
        if j + 1 >= len(idx):
            return None
        px = c[j]
        if abs(px - sig.entry) > MAX_ENTRY_DRIFT_R * sig.risk:
            return None
        ts.entry_eff = px + (sig.spread if ts.long else 0.0)
        start = j + 1
    for k in range(start, len(idx)):
        ts.feed(idx[k], h[k], l[k], c[k], bar_minutes=tfm)
        if ts.closed:
            break
    return ts


def one_at_a_time(recs: list) -> list:
    """Un trade a la fois par symbole (comme le robot : pas de nouveau signal tant que le precedent n'est pas termine)."""
    out, busy = [], {}
    for r in sorted(recs, key=lambda r: r["t"]):
        if r["symbol"] in busy and busy[r["symbol"]] > r["t"]:
            continue
        busy[r["symbol"]] = r["exit"]
        out.append(r)
    return out


def line(name: str, s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return f"  {name:<34} n=0"
    pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"  {name:<34} n={s['n']:<5} TP1={s['tp1']:5.1f}% SL={s['sl']:5.1f}%  avgR={s['avg_r']:+.3f} (+/-{1.96 * s['avg_r_se']:.3f})  "
            f"totR={s['total_r']:+7.1f}  PF={pf:>5}  DD={s['max_dd_r']:.1f}R")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["cache", "yahoo"], default="cache")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--variants", default="", help="noms exacts, separes par des virgules (defaut : tous ceux que les donnees permettent)")
    ap.add_argument("--delay", type=int, default=0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    tag = args.tag or f"{args.source}{'_delay' if args.delay else ''}"
    want_v = [v.strip() for v in args.variants.split(",") if v.strip()]
    variants = [v for v in VARIANTS if not want_v or v["name"] in want_v]
    want_s = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}

    out = io.StringIO()

    def P(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        out.write(s + "\n")

    P(f"=== BACKTEST MULTI-TIMEFRAMES ({args.source}, delai {args.delay} bougie) ===")
    recs = {v["name"]: [] for v in variants}
    cover = {}
    t_all = time.time()
    for sid, cfg in sd.UNIVERSE.items():
        mt5n = cfg["mt5"]
        if want_s and sid.upper() not in want_s and mt5n.upper() not in want_s:
            continue
        if args.source == "yahoo" and not cfg.get("yahoo"):
            continue
        if args.source == "cache" and sid in ("XAGUSD", "GER40", "USOIL"):
            continue
        meta = sd._meta_all().get(sid)
        if not meta:
            continue
        data = {tf: load_tf(args.source, mt5n, sid, tf) for tf in TFM}
        if any(data[tf] is None or len(data[tf]) < 300 for tf in ("4h", "1d")):
            continue
        zone_cache = {}
        t0 = time.time()
        for v in variants:
            e, tfm = v["entry"], TFM[v["entry"]]
            need = [e] + v["bias"] + ([v["zone"]] if v["zone"] else [])
            if any(data[t] is None or len(data[t]) < 300 for t in need):
                continue
            df = data[e]
            bias = combined_bias(df, tfm, v["bias"], data)
            hz = None
            if v["zone"]:
                if v["zone"] not in zone_cache:
                    zone_cache[v["zone"]] = htf_zones_of(data[v["zone"]], TFM[v["zone"]], LABEL[v["zone"]], meta["point"], cfg["cls"])
                hz = zone_cache[v["zone"]]
                if hz is None:
                    continue
            prm = {"require_htf_zone": True} if v["zone"] else None
            sigs = analyze(df, bias, meta["point"], sid, sid, tfm, cfg["cls"], params=prm, min_score=2, htf_zones=hz)
            o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            idx = df.index
            for s in sigs:
                i0 = idx.get_loc(pd.Timestamp(s.bar_open))
                ts = simulate(s, o, h, l, c, idx, i0, tfm, args.delay)
                if ts is None or not ts.closed:
                    continue
                recs[v["name"]].append(dict(symbol=sid, cls=cfg["cls"], t=pd.Timestamp(s.signal_time), score=s.score, r=ts.r, outcome=ts.outcome,
                                            exit=pd.Timestamp(ts.close_time), risk_atr=s.risk / s.atr, dir=s.direction,
                                            htf=bool(s.checks.get("htf_zone", False)), **{k: s.checks[k] for k in SCORED}))
            cover.setdefault(v["name"], []).append((sid, str(df.index[0].date()), str(df.index[-1].date()), len(df)))
        P(f"  {sid:8s} {time.time() - t0:5.1f}s")

    P(f"\nDuree totale {time.time() - t_all:.0f}s")
    P("\nCouverture (premier symbole -> periode et nombre de bougies d'entree) :")
    for name, cv in cover.items():
        first = cv[0]
        P(f"  {name:<34} {len(cv):2d} symboles | ex. {first[0]} {first[1]} -> {first[2]} ({first[3]} bougies)")

    summary = {}
    for ms in (3, 4, 5):
        P("\n" + "=" * 118)
        P(f"RESULTATS - un trade a la fois par symbole - score minimum {ms} (sur 7)  |  ENTRAINEMENT 60 % / TEST 40 % (ce qui compte : TEST)")
        P("=" * 118)
        for v in variants:
            allr = one_at_a_time([r for r in recs[v["name"]] if r["score"] >= ms])
            if not allr:
                P(line(v["name"], {}))
                continue
            tmin, tmax = min(r["t"] for r in allr), max(r["t"] for r in allr)
            split = tmin + (tmax - tmin) * 0.6
            tr = [r for r in allr if r["t"] <= split]
            te = [r for r in allr if r["t"] > split]
            weeks = max((tmax - tmin).days / 7.0, 1.0)
            P(line(v["name"] + " : TOUT", summarize(allr)) + f"   {len(allr) / weeks:5.1f} signaux/sem.")
            P(line("      entrainement", summarize(tr)))
            P(line("      TEST", summarize(te)))
            summary.setdefault(v["name"], {})[str(ms)] = {"all": summarize(allr), "train": summarize(tr), "test": summarize(te),
                                                           "per_week": len(allr) / weeks, "period": [str(tmin.date()), str(tmax.date())]}
    (DIR / f"signal_backtest_ltf_{tag}.txt").write_text(out.getvalue(), encoding="utf-8")
    (DIR / f"signal_backtest_ltf_{tag}.json").write_text(json.dumps(summary, indent=1, default=float), encoding="utf-8")
    print(f"\nRapport : signal_backtest_ltf_{tag}.txt")


if __name__ == "__main__":
    main()
