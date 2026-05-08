"""
SMC / ICT - detection des concepts Smart Money / Inner Circle Trader.

Concepts couverts :
- Order Blocks (OB) bull/bear
- Fair Value Gaps (FVG) bull/bear
- Liquidity Sweeps (raids sur swing high/low)
- Break of Structure (BoS) et Change of Character (CHoCH)
- Equal Highs/Lows (zones de liquidite)

Tous renvoient des structures de donnees, JAMAIS des signaux d'achat/vente.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional

from indicators import atr, swing_points


# =============================================================
#                       DATA CLASSES
# =============================================================

@dataclass
class OrderBlock:
    time: pd.Timestamp
    direction: str        # 'bull' ou 'bear'
    top: float
    bottom: float
    mitigated: bool = False   # True si le prix est revenu dans la zone

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class FairValueGap:
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    direction: str        # 'bull' ou 'bear'
    top: float
    bottom: float
    filled: bool = False


@dataclass
class LiquiditySweep:
    time: pd.Timestamp
    direction: str        # 'bull_sweep' (raid des lows) ou 'bear_sweep'
    swept_level: float    # le niveau qui a ete pris
    close: float


@dataclass
class StructureEvent:
    time: pd.Timestamp
    type: str             # 'BoS' ou 'CHoCH'
    direction: str        # 'bull' ou 'bear'
    broken_level: float


@dataclass
class EqualLevel:
    direction: str        # 'highs' ou 'lows'
    level: float
    times: List[pd.Timestamp] = field(default_factory=list)


# =============================================================
#                       ORDER BLOCKS
# =============================================================

def detect_order_blocks(
    df: pd.DataFrame,
    atr_mult: float = 2.0,
    lookforward: int = 3,
) -> List[OrderBlock]:
    """
    Bull OB : derniere bougie baissiere avant un mouvement haussier > atr_mult * ATR.
    Bear OB : derniere bougie haussiere avant un mouvement baissier > atr_mult * ATR.
    """
    df = df.copy()
    a = atr(df, 14)
    obs: List[OrderBlock] = []
    last_price = df["close"].iloc[-1]

    for i in range(15, len(df) - lookforward):
        if pd.isna(a.iloc[i]):
            continue
        threshold = atr_mult * a.iloc[i]

        future_high = df["high"].iloc[i + 1:i + 1 + lookforward].max()
        future_low = df["low"].iloc[i + 1:i + 1 + lookforward].min()

        # impulsion haussiere
        if future_high - df["close"].iloc[i] > threshold:
            for j in range(i, max(i - 3, -1), -1):
                if df["close"].iloc[j] < df["open"].iloc[j]:
                    obs.append(OrderBlock(
                        time=df.index[j],
                        direction="bull",
                        top=df["high"].iloc[j],
                        bottom=df["low"].iloc[j],
                    ))
                    break
        # impulsion baissiere
        if df["close"].iloc[i] - future_low > threshold:
            for j in range(i, max(i - 3, -1), -1):
                if df["close"].iloc[j] > df["open"].iloc[j]:
                    obs.append(OrderBlock(
                        time=df.index[j],
                        direction="bear",
                        top=df["high"].iloc[j],
                        bottom=df["low"].iloc[j],
                    ))
                    break

    # dedupe (meme bougie peut etre detectee plusieurs fois)
    seen = set()
    deduped: List[OrderBlock] = []
    for ob in obs:
        key = (ob.time, ob.direction)
        if key not in seen:
            seen.add(key)
            deduped.append(ob)

    # marquer si mitige (prix est revenu dans la zone apres formation)
    for ob in deduped:
        idx = df.index.get_loc(ob.time)
        post = df.iloc[idx + 1:]
        if ob.direction == "bull":
            ob.mitigated = bool((post["low"] <= ob.top).any())
        else:
            ob.mitigated = bool((post["high"] >= ob.bottom).any())

    # garder les "frais" (non-mitiges) en priorite, plus quelques recents mitiges
    fresh = [ob for ob in deduped if not ob.mitigated]
    return fresh[-10:] if len(fresh) >= 5 else deduped[-10:]


# =============================================================
#                    FAIR VALUE GAPS
# =============================================================

def detect_fvg(df: pd.DataFrame, max_age_bars: int = 100) -> List[FairValueGap]:
    """
    Bull FVG : high de bougie[i] < low de bougie[i+2]    -> gap a combler par dessus
    Bear FVG : low de bougie[i]  > high de bougie[i+2]   -> gap a combler par dessous
    """
    fvgs: List[FairValueGap] = []
    n = len(df)
    start = max(0, n - max_age_bars - 2)
    for i in range(start, n - 2):
        h0 = df["high"].iloc[i]
        l0 = df["low"].iloc[i]
        h2 = df["high"].iloc[i + 2]
        l2 = df["low"].iloc[i + 2]

        if h0 < l2:
            fvgs.append(FairValueGap(
                start_time=df.index[i], end_time=df.index[i + 2],
                direction="bull", top=l2, bottom=h0,
            ))
        if l0 > h2:
            fvgs.append(FairValueGap(
                start_time=df.index[i], end_time=df.index[i + 2],
                direction="bear", top=l0, bottom=h2,
            ))

    # check fill
    for fvg in fvgs:
        idx = df.index.get_loc(fvg.end_time)
        post = df.iloc[idx + 1:]
        if fvg.direction == "bull":
            fvg.filled = bool((post["low"] <= fvg.bottom).any())
        else:
            fvg.filled = bool((post["high"] >= fvg.top).any())

    unfilled = [f for f in fvgs if not f.filled]
    return unfilled[-10:]


# =============================================================
#                  LIQUIDITY SWEEPS
# =============================================================

def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 20) -> List[LiquiditySweep]:
    """
    Bull sweep : meche transperce le low recent puis cloture au-dessus -> faux breakout baissier.
    Bear sweep : meche transperce le high recent puis cloture en-dessous -> faux breakout haussier.
    """
    sweeps: List[LiquiditySweep] = []
    for i in range(lookback, len(df)):
        prior_high = df["high"].iloc[i - lookback:i].max()
        prior_low = df["low"].iloc[i - lookback:i].min()
        bar_low = df["low"].iloc[i]
        bar_high = df["high"].iloc[i]
        bar_close = df["close"].iloc[i]

        if bar_low < prior_low and bar_close > prior_low:
            sweeps.append(LiquiditySweep(
                time=df.index[i], direction="bull_sweep",
                swept_level=prior_low, close=bar_close,
            ))
        if bar_high > prior_high and bar_close < prior_high:
            sweeps.append(LiquiditySweep(
                time=df.index[i], direction="bear_sweep",
                swept_level=prior_high, close=bar_close,
            ))
    return sweeps[-10:]


# =============================================================
#                BoS  /  CHoCH (structure)
# =============================================================

def detect_structure_events(df: pd.DataFrame, lookback: int = 5) -> List[StructureEvent]:
    """
    Suit les swings confirmes et detecte :
    - BoS  : cassure dans le sens de la tendance (continuation)
    - CHoCH: cassure dans le sens inverse (changement de regime)
    """
    sh, sl = swing_points(df, lookback)
    events: List[StructureEvent] = []

    last_sh: Optional[float] = None
    last_sl: Optional[float] = None
    trend: Optional[str] = None  # 'up' ou 'down'

    for i in range(len(df)):
        close = df["close"].iloc[i]

        # cassure d'un swing high
        if last_sh is not None and close > last_sh:
            ev_type = "BoS" if trend == "up" else "CHoCH"
            events.append(StructureEvent(
                time=df.index[i], type=ev_type,
                direction="bull", broken_level=last_sh,
            ))
            trend = "up"
            last_sh = None

        # cassure d'un swing low
        if last_sl is not None and close < last_sl:
            ev_type = "BoS" if trend == "down" else "CHoCH"
            events.append(StructureEvent(
                time=df.index[i], type=ev_type,
                direction="bear", broken_level=last_sl,
            ))
            trend = "down"
            last_sl = None

        # mise a jour des swings de reference
        if sh.iloc[i]:
            last_sh = df["high"].iloc[i]
        if sl.iloc[i]:
            last_sl = df["low"].iloc[i]

    return events[-10:]


# =============================================================
#                EQUAL HIGHS / EQUAL LOWS
# =============================================================

def detect_equal_levels(df: pd.DataFrame, tol: float = 0.001, lookback: int = 5) -> List[EqualLevel]:
    """
    Niveaux ou plusieurs swing highs (ou lows) sont quasi-identiques.
    Ce sont des reservoirs de liquidite (stops empiles juste au-dessus/dessous).
    """
    sh, sl = swing_points(df, lookback)

    def cluster(levels: List[tuple]) -> List[EqualLevel]:
        out: List[EqualLevel] = []
        sorted_lvls = sorted(levels, key=lambda x: x[1])
        i = 0
        while i < len(sorted_lvls):
            base_t, base_p = sorted_lvls[i]
            group = [(base_t, base_p)]
            j = i + 1
            while j < len(sorted_lvls):
                t2, p2 = sorted_lvls[j]
                if abs(p2 - base_p) / base_p <= tol:
                    group.append((t2, p2))
                    j += 1
                else:
                    break
            if len(group) >= 2:
                out.append(EqualLevel(
                    direction="",
                    level=round(np.mean([p for _, p in group]), 2),
                    times=[t for t, _ in group],
                ))
            i = j
        return out

    high_lvls = [(df.index[i], df["high"].iloc[i]) for i in range(len(df)) if sh.iloc[i]]
    low_lvls = [(df.index[i], df["low"].iloc[i]) for i in range(len(df)) if sl.iloc[i]]

    eqh = cluster(high_lvls[-30:])
    eql = cluster(low_lvls[-30:])
    for e in eqh:
        e.direction = "highs"
    for e in eql:
        e.direction = "lows"
    return eqh + eql


# =============================================================
#                       SUMMARY PRINT
# =============================================================

def print_smc_summary(df: pd.DataFrame) -> None:
    last_price = df["close"].iloc[-1]
    obs = detect_order_blocks(df)
    fvgs = detect_fvg(df)
    sweeps = detect_liquidity_sweeps(df)
    events = detect_structure_events(df)
    eq = detect_equal_levels(df)

    print(f"\n[SMC / ICT]   prix actuel : {last_price:,.2f}")

    if obs:
        print(f"\n  Order Blocks (frais en priorite, max 10) :")
        for ob in obs[-6:]:
            tag = "MITIGE" if ob.mitigated else "frais"
            dist = (ob.mid - last_price) / last_price * 100
            print(f"    {ob.direction.upper():4s}  [{tag:6s}]  "
                  f"{ob.bottom:>10,.2f} - {ob.top:<10,.2f}  "
                  f"({abs(dist):4.1f}% {'au-dessus' if dist>0 else 'en-dessous'})")

    if fvgs:
        print(f"\n  Fair Value Gaps non combles :")
        for f in fvgs[-6:]:
            dist = ((f.top + f.bottom) / 2 - last_price) / last_price * 100
            print(f"    {f.direction.upper():4s}  "
                  f"{f.bottom:>10,.2f} - {f.top:<10,.2f}  "
                  f"({abs(dist):4.1f}% {'au-dessus' if dist>0 else 'en-dessous'})")

    if sweeps:
        print(f"\n  Liquidity Sweeps recents :")
        for s in sweeps[-5:]:
            print(f"    {s.time}  {s.direction:13s}  niveau pris : {s.swept_level:,.2f}  "
                  f"close : {s.close:,.2f}")

    if events:
        print(f"\n  Evenements de structure :")
        for e in events[-5:]:
            print(f"    {e.time}  {e.type:6s}  {e.direction:4s}  niveau casse : {e.broken_level:,.2f}")

    if eq:
        print(f"\n  Equal Highs/Lows (reservoirs de liquidite) :")
        for e in eq[-6:]:
            dist = (e.level - last_price) / last_price * 100
            print(f"    Equal {e.direction:5s}  {e.level:>10,.2f}  "
                  f"({len(e.times)} touches, {abs(dist):4.1f}% {'au-dessus' if dist>0 else 'en-dessous'})")
