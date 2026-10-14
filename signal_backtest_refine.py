"""
signal_backtest_refine.py - Les timeframes plus petits peuvent-ils AFFINER l'analyse H4 existante (sans la changer) ?

On prend les signaux H4 (biais journalier) exactement tels que le robot les produit, et on regarde, au moment du signal, si H1 / H4
sont d'accord avec le sens du trade (moyennes 50 / 200). Question posee : les signaux ou les timeframes sont d'accord font-ils mieux
que les autres ? Si non, cette "confirmation multi-timeframes" ne sert a rien et on ne la presente pas comme un atout.
Historique MT5 deja en cache (H4 ~7 ans, H1 ~4 ans) : AUCUN acces MT5.

Usage : python signal_backtest_refine.py
"""
from __future__ import annotations
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import signal_data as sd
from signal_engine import analyze, htf_bias, TradeSim, summarize

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

DIR = Path(__file__).parent


def cache(mt5n: str, tf: str):
    fs = sorted(sd.CACHE_DIR.glob(f"{mt5n}_{tf}_*.pkl"), key=lambda f: int(f.stem.rsplit("_", 1)[-1]))
    return pd.read_pickle(fs[-1]) if fs else None


def line(name: str, s: dict) -> str:
    if not s or s.get("n", 0) == 0:
        return f"  {name:<44} n=0"
    return (f"  {name:<44} n={s['n']:<5} TP1={s['tp1']:5.1f}% SL={s['sl']:5.1f}%  avgR={s['avg_r']:+.3f} (+/-{1.96 * s['avg_r_se']:.3f})  "
            f"totR={s['total_r']:+7.1f}  PF={s['profit_factor']:.2f}")


def main():
    rows = []
    for sid, cfg in sd.UNIVERSE.items():
        if sid in ("XAGUSD", "GER40", "USOIL"):
            continue
        d4, dd, d1h = cache(cfg["mt5"], "4h"), cache(cfg["mt5"], "1d"), cache(cfg["mt5"], "1h")
        meta = sd._meta_all().get(sid)
        if d4 is None or dd is None or d1h is None or not meta:
            continue
        bias_d1 = htf_bias(dd, d4.index, 240, htf_minutes=1440)
        b_h1 = htf_bias(d1h, d4.index, 240, htf_minutes=60)             # tendance H1 (moyennes 50/200) au moment de la cloture H4
        b_h4 = htf_bias(d4, d4.index, 240, htf_minutes=240)             # tendance H4 elle-meme
        sigs = analyze(d4, bias_d1, meta["point"], sid, sid, 240, cfg["cls"], min_score=2)
        o, h, l, c = (d4[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        idx = d4.index
        first_h1 = d1h.index[0] + pd.Timedelta(days=60)                   # les moyennes H1 ont besoin de leur propre historique
        for s in sigs:
            t = pd.Timestamp(s.signal_time)
            if t < first_h1:
                continue
            i0 = idx.get_loc(pd.Timestamp(s.bar_open))
            ts = TradeSim(s)
            for k in range(i0 + 1, len(idx)):
                ts.feed(idx[k], h[k], l[k], c[k], bar_minutes=240)
                if ts.closed:
                    break
            if not ts.closed:
                continue
            d = 1 if s.direction == "LONG" else -1
            rows.append(dict(symbol=sid, t=t, score=s.score, r=ts.r, outcome=ts.outcome,
                             h1=int(b_h1[i0]) == d, h1_against=int(b_h1[i0]) == -d, h4=int(b_h4[i0]) == d, h4_against=int(b_h4[i0]) == -d))
    df = pd.DataFrame(rows)
    print(f"{len(df)} signaux H4 (score >= 2) avec historique H1 disponible, {df['symbol'].nunique()} instruments, "
          f"{df['t'].min().date()} -> {df['t'].max().date()}\n")

    def S(sub):
        return summarize([{"r": r, "outcome": o} for r, o in zip(sub["r"], sub["outcome"])])

    for ms in (3, 4):
        base = df[df["score"] >= ms]
        print("=" * 112)
        print(f"Signaux H4 de score >= {ms} : les petits timeframes sont-ils d'accord avec le sens du trade ?")
        print("=" * 112)
        print(line("TOUS", S(base)))
        print(line("H1 d'accord avec le sens du trade", S(base[base["h1"]])))
        print(line("H1 neutre ou contre", S(base[~base["h1"]])))
        print(line("H1 CONTRE le sens du trade", S(base[base["h1_against"]])))
        print(line("H4 d'accord (moyennes 50/200 H4)", S(base[base["h4"]])))
        print(line("H4 neutre ou contre", S(base[~base["h4"]])))
        print(line("H1 ET H4 d'accord", S(base[base["h1"] & base["h4"]])))
        print(line("ni H1 ni H4 d'accord", S(base[~base["h1"] & ~base["h4"]])))
        te = base[base["t"] > base["t"].min() + (base["t"].max() - base["t"].min()) * 0.6]
        print("  -- TEST seul (derniers 40 % du temps) --")
        print(line("TOUS", S(te)))
        print(line("H1 ET H4 d'accord", S(te[te["h1"] & te["h4"]])))
        print(line("ni H1 ni H4 d'accord", S(te[~te["h1"] & ~te["h4"]])))
        print()
    # le score de confirmation lui-meme (7 confirmations) : plus de confirmations = meilleur ?
    print("=" * 112)
    print("Le nombre de confirmations du moteur (score sur 7) annonce-t-il un meilleur trade ?  (signaux H4, memes donnees)")
    print("=" * 112)
    for sc in range(2, 7):
        print(line(f"score = {sc}", S(df[df["score"] == sc])))


if __name__ == "__main__":
    main()
