"""
BTC/USD Technical Analysis Toolkit  (orchestrateur)

Lecture multi-timeframes complete :
- Tendance / Momentum / Volatilite (indicators.py)
- Structure swings + S/R + Fibonacci
- Volume Profile (volume_profile.py)
- SMC / ICT : Order Blocks, FVG, Sweeps, BoS/CHoCH (smc.py)
- Zones de confluence (multi-indicateurs)

CE SCRIPT EST UN OUTIL DE LECTURE.
Aucun signal d'achat/vente. Decisions = responsabilite du trader.

    pip install ccxt pandas numpy
    python btc_analyzer.py
"""
from __future__ import annotations
import ccxt
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Tuple

from indicators import (
    ema, rsi, macd, stochastic, atr, bbands, adx, swing_points
)
from smc import print_smc_summary
from volume_profile import compute_volume_profile, print_volume_profile_summary


SYMBOL = "BTC/USDT"
TIMEFRAMES = ["1h", "4h", "1d"]
LIMIT = 500
SWING_LOOKBACK = 5
CONFLUENCE_TOLERANCE = 0.015


# --------------------------- DATA -------------------------------

def fetch_ohlcv(exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return df


# --------------------------- STRUCTURE --------------------------

def support_resistance_levels(df: pd.DataFrame, lookback: int = SWING_LOOKBACK, top_n: int = 8):
    sh, sl = swing_points(df, lookback)
    highs = df.loc[sh, "high"].tail(20).tolist()
    lows = df.loc[sl, "low"].tail(20).tolist()
    return {
        "resistances": _cluster(highs, CONFLUENCE_TOLERANCE)[:top_n],
        "supports": _cluster(lows, CONFLUENCE_TOLERANCE)[:top_n],
    }


def _cluster(values: List[float], tol: float) -> List[float]:
    if not values:
        return []
    values = sorted(values)
    clusters: List[List[float]] = [[values[0]]]
    for v in values[1:]:
        if abs(v - clusters[-1][-1]) / clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [round(float(np.mean(c)), 2) for c in clusters]


def fib_retracement(high: float, low: float) -> Dict[str, float]:
    diff = high - low
    return {
        "0.0 (low)":  round(low, 2),
        "0.236":      round(low + 0.236 * diff, 2),
        "0.382":      round(low + 0.382 * diff, 2),
        "0.5":        round(low + 0.5 * diff, 2),
        "0.618":      round(low + 0.618 * diff, 2),
        "0.705":      round(low + 0.705 * diff, 2),
        "0.786":      round(low + 0.786 * diff, 2),
        "1.0 (high)": round(high, 2),
    }


# --------------------------- DIVERGENCES ------------------------

def detect_rsi_divergence(df: pd.DataFrame, rsi_series: pd.Series) -> List[str]:
    sh, sl = swing_points(df, SWING_LOOKBACK)
    msgs: List[str] = []
    rsh = df[sh].tail(2)
    rsl = df[sl].tail(2)
    if len(rsh) == 2:
        p1, p2 = rsh["high"].iloc[0], rsh["high"].iloc[1]
        r1, r2 = rsi_series.loc[rsh.index[0]], rsi_series.loc[rsh.index[1]]
        if p2 > p1 and r2 < r1:
            msgs.append(f"Divergence BAISSIERE RSI : prix {p1:.0f} -> {p2:.0f}, RSI {r1:.1f} -> {r2:.1f}")
    if len(rsl) == 2:
        p1, p2 = rsl["low"].iloc[0], rsl["low"].iloc[1]
        r1, r2 = rsi_series.loc[rsl.index[0]], rsi_series.loc[rsl.index[1]]
        if p2 < p1 and r2 > r1:
            msgs.append(f"Divergence HAUSSIERE RSI : prix {p1:.0f} -> {p2:.0f}, RSI {r1:.1f} -> {r2:.1f}")
    return msgs


# --------------------------- CONFLUENCE -------------------------

@dataclass
class ConfluenceZone:
    price: float
    sources: List[str]

    @property
    def strength(self) -> int:
        return len(self.sources)


def find_confluence_zones(df: pd.DataFrame, fib: Dict[str, float], sr, vp=None) -> List[ConfluenceZone]:
    last = df.iloc[-1]
    cands: List[Tuple[float, str]] = []

    cands.append((last["ema20"], "EMA 20"))
    cands.append((last["ema50"], "EMA 50"))
    cands.append((last["ema200"], "EMA 200"))
    cands.append((last["bb_low"], "BB inferieure"))
    cands.append((last["bb_up"], "BB superieure"))
    cands.append((last["bb_mid"], "BB mediane"))

    for k, v in fib.items():
        if any(t in k for t in ["0.382", "0.5", "0.618", "0.786"]):
            cands.append((v, f"Fib {k}"))
    for s in sr["supports"]:
        cands.append((s, "Support swing"))
    for r in sr["resistances"]:
        cands.append((r, "Resistance swing"))

    # ajout des niveaux Volume Profile
    if vp is not None:
        cands.append((vp.poc, "POC (Volume Profile)"))
        cands.append((vp.vah, "VAH (Volume Profile)"))
        cands.append((vp.val, "VAL (Volume Profile)"))
        for h in vp.hvn[:5]:
            cands.append((h, "HVN"))

    cands = [(p, s) for p, s in cands if not pd.isna(p) and p > 0]
    cands.sort(key=lambda x: x[0])

    zones: List[ConfluenceZone] = []
    used = [False] * len(cands)
    for i, (p, src) in enumerate(cands):
        if used[i]:
            continue
        cluster_p = [p]
        cluster_s = [src]
        used[i] = True
        for j in range(i + 1, len(cands)):
            if used[j]:
                continue
            p2, src2 = cands[j]
            if abs(p2 - cluster_p[0]) / cluster_p[0] <= CONFLUENCE_TOLERANCE:
                cluster_p.append(p2)
                cluster_s.append(src2)
                used[j] = True
        if len(cluster_s) >= 2:
            zones.append(ConfluenceZone(
                price=round(float(np.mean(cluster_p)), 2),
                sources=cluster_s,
            ))
    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones


# --------------------------- ANALYSE ----------------------------

def analyze_timeframe(df: pd.DataFrame, label: str, with_vp: bool = True, with_smc: bool = True) -> dict:
    df = df.copy()
    close = df["close"]
    df["ema20"] = ema(close, 20)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["rsi"] = rsi(close)
    df["macd"], df["macd_sig"], df["macd_hist"] = macd(close)
    df["stoch_k"], df["stoch_d"] = stochastic(df)
    df["atr"] = atr(df)
    df["bb_low"], df["bb_mid"], df["bb_up"] = bbands(close)
    df["adx"] = adx(df)

    last = df.iloc[-1]
    price = last["close"]

    print(f"\n{'='*70}")
    print(f"  BTC/USDT  |  {label}  |  Prix : {price:,.2f}  |  {df.index[-1]}")
    print(f"{'='*70}")

    # Tendance
    trend_long = "HAUSSIERE" if price > last["ema200"] else "BAISSIERE"
    trend_med = "HAUSSIERE" if last["ema20"] > last["ema50"] else "BAISSIERE"
    print(f"\n[TENDANCE]")
    print(f"  EMA20 : {last['ema20']:,.2f}   EMA50 : {last['ema50']:,.2f}   EMA200 : {last['ema200']:,.2f}")
    print(f"  Long terme (vs EMA200)   : {trend_long}")
    print(f"  Moyen terme (EMA20 vs 50): {trend_med}")
    print(f"  ADX(14) : {last['adx']:.1f}  ->  "
          f"{'tendance forte' if last['adx']>25 else 'range / faible tendance'}")

    # Momentum
    rsi_state = "SURVENTE" if last["rsi"] < 30 else "SURACHAT" if last["rsi"] > 70 else "neutre"
    print(f"\n[MOMENTUM]")
    print(f"  RSI(14) : {last['rsi']:.1f}  ->  {rsi_state}")
    print(f"  MACD : {last['macd']:.2f}   signal : {last['macd_sig']:.2f}   hist : {last['macd_hist']:.2f}  "
          f"({'haussier' if last['macd_hist']>0 else 'baissier'})")
    print(f"  Stoch %K : {last['stoch_k']:.1f}   %D : {last['stoch_d']:.1f}")

    # Volatilite
    print(f"\n[VOLATILITE]")
    print(f"  ATR(14) : {last['atr']:,.2f}   ({last['atr']/price*100:.2f}% du prix)")
    print(f"  BB : low {last['bb_low']:,.2f} | mid {last['bb_mid']:,.2f} | up {last['bb_up']:,.2f}")
    bb_pos = (price - last["bb_low"]) / (last["bb_up"] - last["bb_low"]) * 100
    print(f"  Position dans BB : {bb_pos:.1f}%  (0 = bande basse, 100 = bande haute)")

    # Structure
    sr = support_resistance_levels(df)
    print(f"\n[STRUCTURE]")
    print(f"  Resistances : {sr['resistances']}")
    print(f"  Supports    : {sr['supports']}")

    # Fibonacci
    sh, sl = swing_points(df)
    fib: Dict[str, float] = {}
    if sh.any() and sl.any():
        recent_high = df.loc[sh, "high"].tail(5).max()
        recent_low = df.loc[sl, "low"].tail(5).min()
        fib = fib_retracement(recent_high, recent_low)
        print(f"\n[FIBONACCI]  swing low {recent_low:,.2f}  ->  swing high {recent_high:,.2f}")
        for k, v in fib.items():
            mark = "  <- prix actuel" if abs(v - price) / price < 0.005 else ""
            print(f"  {k:14s} : {v:>10,.2f}{mark}")

    # Divergences
    divs = detect_rsi_divergence(df, df["rsi"])
    if divs:
        print(f"\n[DIVERGENCES]")
        for d in divs:
            print(f"  - {d}")

    # Volume Profile
    vp = None
    if with_vp:
        try:
            vp = print_volume_profile_summary(df, bins=30)
        except Exception as e:
            print(f"\n[VOLUME PROFILE] erreur : {e}")

    # SMC
    if with_smc:
        try:
            print_smc_summary(df)
        except Exception as e:
            print(f"\n[SMC] erreur : {e}")

    # Confluence (avec VP integre)
    zones = find_confluence_zones(df, fib, sr, vp)
    if zones:
        print(f"\n[ZONES DE CONFLUENCE]  (>=2 indicateurs alignes)")
        for z in zones[:8]:
            dist = (z.price - price) / price * 100
            direction = "au-dessus" if dist > 0 else "en-dessous"
            print(f"  {z.price:>10,.2f}  ({abs(dist):4.1f}% {direction})  "
                  f"[force x{z.strength}]  : {', '.join(z.sources)}")

    return {
        "price": price,
        "trend_long": trend_long,
        "trend_med": trend_med,
        "rsi": last["rsi"],
        "atr": last["atr"],
        "confluence_zones": zones,
        "supports": sr["supports"],
        "resistances": sr["resistances"],
    }


# --------------------------- MAIN -------------------------------

def analyze_symbol(exchange, symbol: str, brief: bool = False) -> dict:
    """
    Lance l'analyse multi-TF pour un symbole donne.
    brief = True : skip SMC/VP (synthese rapide pour multi-actifs).
    """
    print(f"\n{'#'*70}")
    print(f"#   {symbol}")
    print(f"{'#'*70}")

    results = {}
    for tf in TIMEFRAMES:
        try:
            df = fetch_ohlcv(exchange, symbol, tf, LIMIT)
            with_vp = (tf in ("4h", "1d")) and not brief
            with_smc = (tf in ("4h", "1d")) and not brief
            results[tf] = analyze_timeframe(df, tf.upper(), with_vp=with_vp, with_smc=with_smc)
        except Exception as e:
            print(f"[{tf}] erreur : {e}")

    # Synthese multi-TF
    print(f"\n{'='*70}")
    print(f"  SYNTHESE {symbol}")
    print(f"{'='*70}")
    for tf, r in results.items():
        agree = "+" if r["trend_long"] == r["trend_med"] else "/"
        print(f"  {tf.upper():4s}  prix {r['price']:>12,.2f}  |  "
              f"long {r['trend_long']:9s} {agree} med {r['trend_med']:9s}  |  "
              f"RSI {r['rsi']:5.1f}  |  {len(r['confluence_zones'])} zones")

    # Confluence inter-timeframe
    if len(results) >= 2:
        print(f"\n  Zones de confluence INTER-timeframe (>= 2 TF) :")
        all_zones: List[Tuple[str, "ConfluenceZone"]] = []
        for tf, r in results.items():
            for z in r["confluence_zones"]:
                all_zones.append((tf, z))
        clusters: List[List[Tuple[str, "ConfluenceZone"]]] = []
        all_zones.sort(key=lambda x: x[1].price)
        for tf_z in all_zones:
            placed = False
            for cl in clusters:
                if abs(tf_z[1].price - cl[0][1].price) / cl[0][1].price <= 0.01:
                    cl.append(tf_z)
                    placed = True
                    break
            if not placed:
                clusters.append([tf_z])
        for cl in sorted(clusters, key=lambda c: -len(set(t for t, _ in c))):
            tfs = sorted(set(t for t, _ in cl))
            if len(tfs) >= 2:
                avg_price = np.mean([z.price for _, z in cl])
                print(f"    {avg_price:>12,.2f}  presente en {tfs}  "
                      f"(force totale x{sum(z.strength for _,z in cl)})")

    return results


def cross_asset_summary(results_by_symbol: dict) -> None:
    """
    Synthese inter-actifs : RSI, tendance, alignement.
    """
    print(f"\n{'='*70}")
    print(f"  SYNTHESE INTER-ACTIFS")
    print(f"{'='*70}")
    print(f"  {'Asset':<12s}  {'TF':<5s}  {'Prix':>14s}  {'LT':<10s}  {'MT':<10s}  {'RSI':>5s}")
    for symbol, results in results_by_symbol.items():
        for tf, r in results.items():
            print(f"  {symbol:<12s}  {tf.upper():<5s}  {r['price']:>14,.2f}  "
                  f"{r['trend_long']:<10s}  {r['trend_med']:<10s}  {r['rsi']:>5.1f}")
        print()

    # alignement : quels actifs ont 1H + 4H + 1D haussier (ou baissier) en meme temps ?
    aligned_bull = []
    aligned_bear = []
    for symbol, results in results_by_symbol.items():
        if not results:
            continue
        all_long = [r["trend_long"] for r in results.values()]
        all_med = [r["trend_med"] for r in results.values()]
        if all(t == "HAUSSIERE" for t in all_long) and all(t == "HAUSSIERE" for t in all_med):
            aligned_bull.append(symbol)
        elif all(t == "BAISSIERE" for t in all_long) and all(t == "BAISSIERE" for t in all_med):
            aligned_bear.append(symbol)

    if aligned_bull:
        print(f"  Tendance haussiere alignee sur tous les TF : {aligned_bull}")
    if aligned_bear:
        print(f"  Tendance baissiere alignee sur tous les TF : {aligned_bear}")
    if not aligned_bull and not aligned_bear:
        print(f"  Aucun actif n'a une tendance pleinement alignee multi-TF")


def main():
    import sys

    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        symbols = [SYMBOL]

    multi = len(symbols) > 1
    exchange = ccxt.binance({"enableRateLimit": True})
    print(f"\nAnalyse technique multi-timeframes")
    print(f"Source : Binance (ccxt)")
    print(f"Symboles : {symbols}")

    all_results = {}
    for symbol in symbols:
        try:
            all_results[symbol] = analyze_symbol(exchange, symbol, brief=multi)
        except Exception as e:
            print(f"[{symbol}] erreur : {e}")

    if multi:
        cross_asset_summary(all_results)

    print("\nRappel : sorties = LECTURES, pas des recommandations.\n")


if __name__ == "__main__":
    main()
