"""
signal_backtest.py - Backtest HONNETE de la strategie GOTA Confluence.

Le moteur (signal_engine.analyze) est identique au live. Regles conservatrices :
spread reel de chaque bougie, SL avant TP dans une bougie ambigue, stop a l'entree
seulement apres TP1 (bougie suivante), expiration 48 bougies.
On separe une periode d'ENTRAINEMENT (60%) et de TEST (40%) : ce qui compte, c'est le TEST.

Usage :
    python signal_backtest.py                       # watchlist complete, 26000 bougies H1
    python signal_backtest.py --symbols EURUSD,GOLD # quelques symboles
    python signal_backtest.py --bars 40000
"""
from __future__ import annotations
import sys
import io
import json
import time
import argparse
import collections
from pathlib import Path

import numpy as np
import pandas as pd

import signal_data as sd
from signal_data import connect, shutdown, get_history, symbol_meta, WATCHLIST
from signal_engine import analyze, htf_bias, TradeSim, summarize, PARAMS, SCORED

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

DIR = Path(__file__).parent
MAX_PER_DAY = 3
MAX_ACTIVE = 6


def simulate(sig, hi, lo, cl, idx, i0: int, bar_minutes: int) -> TradeSim:
    ts = TradeSim(sig)
    for k in range(i0 + 1, len(idx)):
        ts.feed(idx[k], hi[k], lo[k], cl[k], bar_minutes=bar_minutes)
        if ts.closed:
            break
    return ts


def line(name: str, s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return f"  {name:<22} n=0"
    pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"  {name:<22} n={s['n']:<5} TP1={s['tp1']:5.1f}%  TP2={s['tp2']:5.1f}%  TP3={s['tp3']:5.1f}%  "
            f"SL={s['sl']:5.1f}%  avgR={s['avg_r']:+.3f} (+/-{1.96 * s['avg_r_se']:.3f})  "
            f"totR={s['total_r']:+7.1f}  PF={pf:>5}  DD={s['max_dd_r']:.1f}R")


def portfolio_filter(recs: list, min_score: int) -> list:
    """Reproduit les regles du live : 1 signal actif/symbole, max 3/jour, max 6 actifs."""
    cand = sorted([r for r in recs if r["score"] >= min_score and r["closed"]],
                  key=lambda r: (r["t"], -r["score"]))
    active, per_day, out = {}, collections.Counter(), []
    for r in cand:
        t = r["t"]
        if r["symbol"] in active and active[r["symbol"]] > t:
            continue
        if sum(1 for e in active.values() if e > t) >= MAX_ACTIVE:
            continue
        day = t.date()
        if per_day[day] >= MAX_PER_DAY:
            continue
        per_day[day] += 1
        active[r["symbol"]] = r["exit"]
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=0, help="nb de bougies d'entree (defaut : 26000 en H1, 12000 en H4)")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--min-score", type=int, default=PARAMS["min_score"])
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--tf", default="1h", choices=["1h", "4h"], help="timeframe d'entree")
    args = ap.parse_args()
    tf_min = {"1h": 60, "4h": 240}[args.tf]
    htf_key, htf_min = ("4h", 240) if args.tf == "1h" else ("1d", 1440)
    if not args.bars:
        args.bars = 26000 if args.tf == "1h" else 12000
    tag = "" if args.tf == "1h" else f"_{args.tf}"
    if sd.BACKEND == "yahoo":                       # ne pas ecraser les rapports MT5
        tag += "_yahoo"

    print(f"=== BACKTEST GOTA CONFLUENCE ({args.tf.upper()}, biais {htf_key.upper()}) ===")
    if not connect():
        print("Connexion MT5 impossible.")
        return
    wl = WATCHLIST
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        wl = {k: v for k, v in WATCHLIST.items() if k.upper() in want or v[0].upper() in want}

    recs = []
    cover = []
    t_all = time.time()
    for sym, (disp, cls) in wl.items():
        t0 = time.time()
        meta = symbol_meta(sym)
        df1 = get_history(sym, args.tf, args.bars, use_cache=not args.no_cache)
        n_htf = args.bars // 4 + 400 if args.tf == "1h" else args.bars // 6 + 300
        df4 = get_history(sym, htf_key, n_htf, use_cache=not args.no_cache)
        if df1 is None or df4 is None or len(df1) < 800 or meta is None:
            print(f"  {disp:8s} donnees insuffisantes - ignore")
            continue
        bias = htf_bias(df4, df1.index, tf_min, htf_minutes=htf_min)
        sigs = analyze(df1, bias, meta["point"], sym, disp, tf_min, cls, min_score=2)
        hi, lo, cl = df1["high"].to_numpy(), df1["low"].to_numpy(), df1["close"].to_numpy()
        idx = df1.index
        n_closed = 0
        for s in sigs:
            i0 = idx.get_loc(pd.Timestamp(s.bar_open))
            ts = simulate(s, hi, lo, cl, idx, i0, tf_min)
            recs.append(dict(symbol=disp, cls=cls, t=pd.Timestamp(s.signal_time), dir=s.direction,
                             score=s.score, r=ts.r, outcome=ts.outcome, closed=ts.closed,
                             exit=pd.Timestamp(ts.close_time) if ts.close_time else None,
                             zone=s.zone_kind, sp_risk=s.spread / s.risk, **{k: s.checks[k] for k in SCORED}))
            n_closed += ts.closed
        cover.append((disp, len(df1), df1.index[0].date(), df1.index[-1].date(), len(sigs)))
        print(f"  {disp:8s} {len(df1):6d} bougies {df1.index[0].date()} -> {df1.index[-1].date()} | "
              f"{len(sigs):4d} signaux bruts (score>=2) | {time.time() - t0:5.1f}s", flush=True)

    if not recs:
        print("Aucun signal.")
        shutdown()
        return
    closed = [r for r in recs if r["closed"]]
    print(f"\nTotal : {len(recs)} signaux bruts, {len(closed)} termines. Duree {time.time() - t_all:.0f}s")

    out = io.StringIO()

    def P(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        out.write(s + "\n")

    P("\n" + "=" * 100)
    P("1) QUALITE DES SIGNAUX PAR SCORE (trades independants, sans limite de portefeuille)")
    P("=" * 100)
    for sc in range(2, 7):
        P(line(f"score = {sc}/6", summarize([r for r in closed if r["score"] == sc])))
    for sc in (3, 4, 5):
        P(line(f"score >= {sc}/6", summarize([r for r in closed if r["score"] >= sc])))

    # Par confirmation (contribution individuelle, score>=3)
    P("\n  Contribution de chaque confirmation (signaux score>=3) : avgR avec / sans")
    base = [r for r in closed if r["score"] >= 3]
    for k in SCORED:
        w = summarize([r for r in base if r[k]])
        wo = summarize([r for r in base if not r[k]])
        if w["n"] and wo["n"]:
            P(f"    {k:<9} avec: n={w['n']:<5} avgR={w['avg_r']:+.3f}   sans: n={wo['n']:<5} avgR={wo['avg_r']:+.3f}")

    # Portefeuille
    tmin, tmax = min(r["t"] for r in closed), max(r["t"] for r in closed)
    split = tmin + (tmax - tmin) * 0.6
    P("\n" + "=" * 100)
    P(f"2) RESULTATS TELS QUE LE CANAL LES AURAIT POSTES (max {MAX_PER_DAY}/jour, 1 actif/symbole, {MAX_ACTIVE} actifs max)")
    P(f"   Periode {tmin.date()} -> {tmax.date()} | ENTRAINEMENT jusqu'au {split.date()} | TEST ensuite")
    P("=" * 100)
    summary = {}
    for ms in (3, 4, 5):
        acc = portfolio_filter(recs, ms)
        tr = [r for r in acc if r["t"] <= split]
        te = [r for r in acc if r["t"] > split]
        P(f"\n  --- score minimum {ms}/6 ---")
        P(line("TOUT", summarize(acc)))
        P(line("entrainement (60%)", summarize(tr)))
        P(line("TEST (40%)", summarize(te)))
        days = max((tmax - tmin).days, 1)
        P(f"  => {len(acc) / days * 7:.1f} signaux / semaine en moyenne")
        summary[str(ms)] = {"all": summarize(acc), "train": summarize(tr), "test": summarize(te),
                            "per_week": len(acc) / days * 7}

    ms = args.min_score
    acc = portfolio_filter(recs, ms)
    P("\n" + "=" * 100)
    P(f"3) DETAIL (score minimum {ms}/6, regles de portefeuille appliquees)")
    P("=" * 100)
    P("  Par symbole :")
    for sy in sorted({r["symbol"] for r in acc}):
        P(line(sy, summarize([r for r in acc if r["symbol"] == sy])))
    P("  Par annee :")
    for y in sorted({r["t"].year for r in acc}):
        P(line(str(y), summarize([r for r in acc if r["t"].year == y])))
    P("  Par direction / type de zone / classe d'actif :")
    for d in ("LONG", "SHORT"):
        P(line(d, summarize([r for r in acc if r["dir"] == d])))
    for z in ("OB", "FVG"):
        P(line("zone " + z, summarize([r for r in acc if r["zone"] == z])))
    for c in sorted({r["cls"] for r in acc}):
        P(line("classe " + c, summarize([r for r in acc if r["cls"] == c])))
    P(f"\n  Spread moyen / risque : {np.mean([r['sp_risk'] for r in acc]) * 100:.1f}%")

    (DIR / f"signal_backtest_report{tag}.txt").write_text(out.getvalue(), encoding="utf-8")
    (DIR / f"signal_backtest_summary{tag}.json").write_text(json.dumps(
        {"tf": args.tf, "htf": htf_key, "bars": args.bars, "period": [str(tmin.date()), str(tmax.date())],
         "split": str(split.date()),
         "params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in PARAMS.items()},
         "by_min_score": summary}, indent=2, default=float), encoding="utf-8")
    print(f"\nRapport ecrit : signal_backtest_report{tag}.txt / signal_backtest_summary{tag}.json")
    shutdown()


if __name__ == "__main__":
    main()
