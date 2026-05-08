"""
Backtester educatif - moteur evenementiel + 3 strategies d'exemple.

Ne fait PAS de prediction. Mesure le comportement passe d'une regle.

Lancement direct :
    python backtester.py
(charge BTC/USDT 4h via ccxt, lance les 3 strategies, imprime les metriques)
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Callable

from indicators import ema, rsi, atr, bbands, adx
from volume_profile import compute_volume_profile


# =============================================================
#                          DATA TYPES
# =============================================================

@dataclass
class Signal:
    direction: str          # 'long' ou 'short'
    sl: float
    tp: float
    reason: str = ""


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    reason: str = ""


@dataclass
class BacktestResult:
    initial_capital: float
    final_equity: float
    trades: List[Trade]
    equity_curve: pd.Series
    metrics: dict


# =============================================================
#                       STRATEGIES
# =============================================================

class Strategy:
    name = "base"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def entry_signal(self, df: pd.DataFrame, i: int) -> Optional[Signal]:
        return None


class EmaCrossover(Strategy):
    """
    Long quand EMA20 croise EMA50 ET prix > EMA200 (filtre tendance).
    Short quand EMA20 croise sous EMA50 ET prix < EMA200.
    SL = 1.5 ATR.   TP = 3 ATR  (R:R = 1:2).
    """
    name = "EMA_Crossover_20_50"

    def prepare(self, df):
        df = df.copy()
        df["ema20"] = ema(df["close"], 20)
        df["ema50"] = ema(df["close"], 50)
        df["ema200"] = ema(df["close"], 200)
        df["atr"] = atr(df, 14)
        return df

    def entry_signal(self, df, i):
        if i < 200:
            return None
        prev = df.iloc[i - 1]
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]):
            return None

        cross_up = prev["ema20"] <= prev["ema50"] and cur["ema20"] > cur["ema50"]
        cross_dn = prev["ema20"] >= prev["ema50"] and cur["ema20"] < cur["ema50"]

        if cross_up and cur["close"] > cur["ema200"]:
            entry = cur["close"]
            return Signal("long", sl=entry - 1.5 * cur["atr"], tp=entry + 3 * cur["atr"],
                          reason="EMA20 cross EMA50 up + above EMA200")
        if cross_dn and cur["close"] < cur["ema200"]:
            entry = cur["close"]
            return Signal("short", sl=entry + 1.5 * cur["atr"], tp=entry - 3 * cur["atr"],
                          reason="EMA20 cross EMA50 down + below EMA200")
        return None


class RsiMeanReversion(Strategy):
    """
    Long  : RSI < 30 + prix au-dessus de EMA200 (achat des creux en tendance haussiere).
    Short : RSI > 70 + prix en-dessous de EMA200.
    SL = 1.5 ATR.   TP = 2.5 ATR.
    """
    name = "RSI_MeanReversion"

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)
        return df

    def entry_signal(self, df, i):
        if i < 200:
            return None
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]):
            return None

        # entree quand RSI ressort de la zone extreme
        long_setup = prev["rsi"] < 30 and cur["rsi"] >= 30 and cur["close"] > cur["ema200"]
        short_setup = prev["rsi"] > 70 and cur["rsi"] <= 70 and cur["close"] < cur["ema200"]

        if long_setup:
            entry = cur["close"]
            return Signal("long", sl=entry - 1.5 * cur["atr"], tp=entry + 2.5 * cur["atr"],
                          reason="RSI sort de survente + above EMA200")
        if short_setup:
            entry = cur["close"]
            return Signal("short", sl=entry + 1.5 * cur["atr"], tp=entry - 2.5 * cur["atr"],
                          reason="RSI sort de surachat + below EMA200")
        return None


class Confluence(Strategy):
    """
    Achete uniquement si >= 3 criteres haussiers s'alignent :
      1) prix > EMA200          (tendance long-terme haussiere)
      2) RSI entre 35 et 50     (recul sain, ni surachete ni casse)
      3) prix touche EMA50 ou bande basse Bollinger     (zone d'achat)
      4) bougie verte de retournement (close > open)
    Idem inverse pour le short.
    """
    name = "Confluence_3_of_4"

    def prepare(self, df):
        df = df.copy()
        df["ema50"] = ema(df["close"], 50)
        df["ema200"] = ema(df["close"], 200)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)
        df["bb_low"], df["bb_mid"], df["bb_up"] = bbands(df["close"], 20, 2.0)
        return df

    def entry_signal(self, df, i):
        if i < 200:
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["bb_low"]) or pd.isna(cur["atr"]):
            return None

        # criteres long
        c1 = cur["close"] > cur["ema200"]
        c2 = 35 < cur["rsi"] < 55
        c3 = (cur["low"] <= cur["ema50"] <= cur["high"]) or cur["low"] <= cur["bb_low"] * 1.005
        c4 = cur["close"] > cur["open"]
        long_score = sum([c1, c2, c3, c4])

        # criteres short
        s1 = cur["close"] < cur["ema200"]
        s2 = 45 < cur["rsi"] < 65
        s3 = (cur["low"] <= cur["ema50"] <= cur["high"]) or cur["high"] >= cur["bb_up"] * 0.995
        s4 = cur["close"] < cur["open"]
        short_score = sum([s1, s2, s3, s4])

        if long_score >= 3:
            entry = cur["close"]
            return Signal("long", sl=entry - 1.5 * cur["atr"], tp=entry + 3 * cur["atr"],
                          reason=f"Confluence long {long_score}/4")
        if short_score >= 3:
            entry = cur["close"]
            return Signal("short", sl=entry + 1.5 * cur["atr"], tp=entry - 3 * cur["atr"],
                          reason=f"Confluence short {short_score}/4")
        return None


class FvgRetest(Strategy):
    """
    SMC : trade le retest des Fair Value Gaps non combles.

    Long  : prix > EMA200 + bougie courante touche un FVG haussier non comble.
    Short : prix < EMA200 + bougie courante touche un FVG baissier non comble.

    SL : juste au-dela de la limite opposee du FVG (+ 0.5 ATR de buffer).
    TP : 2x la distance entry-SL  (R:R 1:2).
    """
    name = "SMC_FVG_Retest"
    LOOKBACK_FVG = 30  # FVG cherche dans les N dernieres bougies

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["atr"] = atr(df, 14)

        bull_top = [np.nan] * len(df)
        bull_bot = [np.nan] * len(df)
        bear_top = [np.nan] * len(df)
        bear_bot = [np.nan] * len(df)
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        for i in range(len(df) - 2):
            if highs[i] < lows[i + 2]:
                bull_top[i + 2] = lows[i + 2]
                bull_bot[i + 2] = highs[i]
            if lows[i] > highs[i + 2]:
                bear_top[i + 2] = lows[i]
                bear_bot[i + 2] = highs[i + 2]
        df["bull_fvg_top"] = bull_top
        df["bull_fvg_bot"] = bull_bot
        df["bear_fvg_top"] = bear_top
        df["bear_fvg_bot"] = bear_bot
        return df

    def entry_signal(self, df, i):
        if i < 200:
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]):
            return None

        # cherche FVG haussier non-comble que le prix touche
        if cur["close"] > cur["ema200"]:
            start = max(0, i - self.LOOKBACK_FVG)
            for j in range(start, i):
                top = df["bull_fvg_top"].iloc[j]
                bot = df["bull_fvg_bot"].iloc[j]
                if pd.isna(top):
                    continue
                # FVG comble si une bougie posterieure a casse en dessous du bottom
                if (df["low"].iloc[j + 1:i] <= bot).any():
                    continue
                # bougie courante touche la zone par le haut
                if cur["low"] <= top and cur["low"] >= bot * 0.998:
                    entry = cur["close"]
                    sl = bot - 0.5 * cur["atr"]
                    if entry <= sl:
                        continue
                    tp = entry + (entry - sl) * 2
                    return Signal("long", sl=sl, tp=tp,
                                  reason=f"Retest FVG bull {bot:.0f}-{top:.0f}")

        if cur["close"] < cur["ema200"]:
            start = max(0, i - self.LOOKBACK_FVG)
            for j in range(start, i):
                top = df["bear_fvg_top"].iloc[j]
                bot = df["bear_fvg_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["high"].iloc[j + 1:i] >= top).any():
                    continue
                if cur["high"] >= bot and cur["high"] <= top * 1.002:
                    entry = cur["close"]
                    sl = top + 0.5 * cur["atr"]
                    if sl <= entry:
                        continue
                    tp = entry - (sl - entry) * 2
                    return Signal("short", sl=sl, tp=tp,
                                  reason=f"Retest FVG bear {bot:.0f}-{top:.0f}")

        return None


class OrderBlockRetest(Strategy):
    """
    SMC : trade le retest des Order Blocks frais.

    Bull OB  = derniere bougie rouge avant impulsion verte > 2 ATR.
    Bear OB  = derniere bougie verte avant impulsion rouge > 2 ATR.

    Long  : prix > EMA200 + courante touche un Bull OB non-mitige.
    Short : prix < EMA200 + courante touche un Bear OB non-mitige.

    SL : OB.bottom - 0.3 ATR (long) / OB.top + 0.3 ATR (short).
    TP : R:R 1:2.
    """
    name = "SMC_OrderBlock_Retest"
    LOOKBACK_OB = 50
    ATR_MULT = 2.0
    LOOKFORWARD = 3

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["atr"] = atr(df, 14)

        bull_top = [np.nan] * len(df)
        bull_bot = [np.nan] * len(df)
        bear_top = [np.nan] * len(df)
        bear_bot = [np.nan] * len(df)

        opens = df["open"].to_numpy()
        closes = df["close"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        atrs = df["atr"].to_numpy()

        for i in range(15, len(df) - self.LOOKFORWARD):
            a = atrs[i]
            if pd.isna(a):
                continue
            future_h = highs[i + 1:i + 1 + self.LOOKFORWARD].max()
            future_l = lows[i + 1:i + 1 + self.LOOKFORWARD].min()
            thr = self.ATR_MULT * a

            # bull OB : derniere bougie rouge avant impulsion haussiere
            if future_h - closes[i] > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] < opens[j]:
                        # marquer cette bougie comme bull OB
                        bull_top[j] = highs[j]
                        bull_bot[j] = lows[j]
                        break
            # bear OB
            if closes[i] - future_l > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] > opens[j]:
                        bear_top[j] = highs[j]
                        bear_bot[j] = lows[j]
                        break

        df["bull_ob_top"] = bull_top
        df["bull_ob_bot"] = bull_bot
        df["bear_ob_top"] = bear_top
        df["bear_ob_bot"] = bear_bot
        return df

    def entry_signal(self, df, i):
        if i < 200:
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]):
            return None

        if cur["close"] > cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bull_ob_top"].iloc[j]
                bot = df["bull_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                # OB mitige si bougie posterieure a casse en dessous du bottom
                if (df["close"].iloc[j + 1:i] < bot).any():
                    continue
                if cur["low"] <= top and cur["low"] >= bot * 0.997:
                    entry = cur["close"]
                    sl = bot - 0.3 * cur["atr"]
                    if entry <= sl:
                        continue
                    tp = entry + (entry - sl) * 2
                    return Signal("long", sl=sl, tp=tp,
                                  reason=f"Retest Bull OB {bot:.0f}-{top:.0f}")

        if cur["close"] < cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bear_ob_top"].iloc[j]
                bot = df["bear_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] > top).any():
                    continue
                if cur["high"] >= bot and cur["high"] <= top * 1.003:
                    entry = cur["close"]
                    sl = top + 0.3 * cur["atr"]
                    if sl <= entry:
                        continue
                    tp = entry - (sl - entry) * 2
                    return Signal("short", sl=sl, tp=tp,
                                  reason=f"Retest Bear OB {bot:.0f}-{top:.0f}")

        return None


class OBRetestVPFiltered(Strategy):
    """
    Hybride : OrderBlock Retest filtre par le Volume Profile.

    On part de la strategie la plus robuste (OB Retest) et on n'autorise les
    entrees QUE si l'OB se situe dans la value area (entre VAL et VAH) ou tres
    proche du POC. Hypothese : un OB ancre sur une zone de forte activite
    institutionnelle a une plus grande probabilite de tenir.

    SL : OB +/- 0.3 ATR.   TP : R:R 1:2.
    """
    name = "OB_Retest_VP_Filtered"
    LOOKBACK_OB = 50
    ATR_MULT = 2.0
    LOOKFORWARD = 3
    VP_LOOKBACK = 250
    VP_BINS = 30
    VP_RECOMPUTE_EVERY = 25
    POC_PROXIMITY_PCT = 0.02

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["atr"] = atr(df, 14)

        # OBs
        bull_top = [np.nan] * len(df)
        bull_bot = [np.nan] * len(df)
        bear_top = [np.nan] * len(df)
        bear_bot = [np.nan] * len(df)
        opens = df["open"].to_numpy()
        closes = df["close"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        atrs = df["atr"].to_numpy()
        for i in range(15, len(df) - self.LOOKFORWARD):
            a = atrs[i]
            if pd.isna(a):
                continue
            future_h = highs[i + 1:i + 1 + self.LOOKFORWARD].max()
            future_l = lows[i + 1:i + 1 + self.LOOKFORWARD].min()
            thr = self.ATR_MULT * a
            if future_h - closes[i] > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] < opens[j]:
                        bull_top[j] = highs[j]
                        bull_bot[j] = lows[j]
                        break
            if closes[i] - future_l > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] > opens[j]:
                        bear_top[j] = highs[j]
                        bear_bot[j] = lows[j]
                        break
        df["bull_ob_top"] = bull_top
        df["bull_ob_bot"] = bull_bot
        df["bear_ob_top"] = bear_top
        df["bear_ob_bot"] = bear_bot

        # VP par paliers
        n = len(df)
        poc_col = [np.nan] * n
        vah_col = [np.nan] * n
        val_col = [np.nan] * n
        for i in range(self.VP_LOOKBACK, n, self.VP_RECOMPUTE_EVERY):
            window = df.iloc[i - self.VP_LOOKBACK:i]
            try:
                vp = compute_volume_profile(window, bins=self.VP_BINS)
                end = min(i + self.VP_RECOMPUTE_EVERY, n)
                for k in range(i, end):
                    poc_col[k] = vp.poc
                    vah_col[k] = vp.vah
                    val_col[k] = vp.val
            except Exception:
                pass
        df["poc"] = poc_col
        df["vah"] = vah_col
        df["val"] = val_col
        return df

    def _vp_passes(self, level: float, cur) -> bool:
        """OB doit etre dans la value area ou tres proche du POC."""
        if pd.isna(cur["poc"]):
            return True
        in_va = cur["val"] <= level <= cur["vah"]
        near_poc = abs(level - cur["poc"]) / cur["poc"] < self.POC_PROXIMITY_PCT
        return in_va or near_poc

    def entry_signal(self, df, i):
        if i < max(200, self.VP_LOOKBACK):
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]):
            return None

        if cur["close"] > cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bull_ob_top"].iloc[j]
                bot = df["bull_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] < bot).any():
                    continue
                if cur["low"] <= top and cur["low"] >= bot * 0.997:
                    if not self._vp_passes((top + bot) / 2, cur):
                        continue
                    entry = cur["close"]
                    sl = bot - 0.3 * cur["atr"]
                    if entry <= sl:
                        continue
                    tp = entry + (entry - sl) * 2
                    return Signal("long", sl=sl, tp=tp,
                                  reason=f"OB+VP bull {bot:.0f}-{top:.0f}")

        if cur["close"] < cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bear_ob_top"].iloc[j]
                bot = df["bear_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] > top).any():
                    continue
                if cur["high"] >= bot and cur["high"] <= top * 1.003:
                    if not self._vp_passes((top + bot) / 2, cur):
                        continue
                    entry = cur["close"]
                    sl = top + 0.3 * cur["atr"]
                    if sl <= entry:
                        continue
                    tp = entry - (sl - entry) * 2
                    return Signal("short", sl=sl, tp=tp,
                                  reason=f"OB+VP bear {bot:.0f}-{top:.0f}")

        return None


class OBRetestV3LargeTP(Strategy):
    """
    V3 = OBRetestVPFiltered avec R:R 1:3 (TP plus loin) au lieu de 1:2.
    Hypothese : laisser plus courir les gains paye, vu la qualite du setup.
    """
    name = "OB_Retest_V3_RR3"
    LOOKBACK_OB = 50
    ATR_MULT = 2.0
    LOOKFORWARD = 3
    VP_LOOKBACK = 250
    VP_BINS = 30
    VP_RECOMPUTE_EVERY = 25
    POC_PROXIMITY_PCT = 0.02
    RR_TARGET = 3.0  # <-- la seule difference vs V1

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["atr"] = atr(df, 14)
        bull_top = [np.nan] * len(df)
        bull_bot = [np.nan] * len(df)
        bear_top = [np.nan] * len(df)
        bear_bot = [np.nan] * len(df)
        opens = df["open"].to_numpy()
        closes = df["close"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        atrs = df["atr"].to_numpy()
        for i in range(15, len(df) - self.LOOKFORWARD):
            a = atrs[i]
            if pd.isna(a):
                continue
            future_h = highs[i + 1:i + 1 + self.LOOKFORWARD].max()
            future_l = lows[i + 1:i + 1 + self.LOOKFORWARD].min()
            thr = self.ATR_MULT * a
            if future_h - closes[i] > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] < opens[j]:
                        bull_top[j] = highs[j]
                        bull_bot[j] = lows[j]
                        break
            if closes[i] - future_l > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] > opens[j]:
                        bear_top[j] = highs[j]
                        bear_bot[j] = lows[j]
                        break
        df["bull_ob_top"] = bull_top
        df["bull_ob_bot"] = bull_bot
        df["bear_ob_top"] = bear_top
        df["bear_ob_bot"] = bear_bot
        n = len(df)
        poc_col = [np.nan] * n
        vah_col = [np.nan] * n
        val_col = [np.nan] * n
        for i in range(self.VP_LOOKBACK, n, self.VP_RECOMPUTE_EVERY):
            window = df.iloc[i - self.VP_LOOKBACK:i]
            try:
                vp = compute_volume_profile(window, bins=self.VP_BINS)
                end = min(i + self.VP_RECOMPUTE_EVERY, n)
                for k in range(i, end):
                    poc_col[k] = vp.poc
                    vah_col[k] = vp.vah
                    val_col[k] = vp.val
            except Exception:
                pass
        df["poc"] = poc_col
        df["vah"] = vah_col
        df["val"] = val_col
        return df

    def _vp_passes(self, level: float, cur) -> bool:
        if pd.isna(cur["poc"]):
            return True
        in_va = cur["val"] <= level <= cur["vah"]
        near_poc = abs(level - cur["poc"]) / cur["poc"] < self.POC_PROXIMITY_PCT
        return in_va or near_poc

    def entry_signal(self, df, i):
        if i < max(200, self.VP_LOOKBACK):
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]):
            return None

        if cur["close"] > cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bull_ob_top"].iloc[j]
                bot = df["bull_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] < bot).any():
                    continue
                if cur["low"] <= top and cur["low"] >= bot * 0.997:
                    if not self._vp_passes((top + bot) / 2, cur):
                        continue
                    entry = cur["close"]
                    sl = bot - 0.3 * cur["atr"]
                    if entry <= sl:
                        continue
                    tp = entry + (entry - sl) * self.RR_TARGET
                    return Signal("long", sl=sl, tp=tp,
                                  reason=f"OB+VP bull RR1:{self.RR_TARGET:.0f}")
        if cur["close"] < cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bear_ob_top"].iloc[j]
                bot = df["bear_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] > top).any():
                    continue
                if cur["high"] >= bot and cur["high"] <= top * 1.003:
                    if not self._vp_passes((top + bot) / 2, cur):
                        continue
                    entry = cur["close"]
                    sl = top + 0.3 * cur["atr"]
                    if sl <= entry:
                        continue
                    tp = entry - (sl - entry) * self.RR_TARGET
                    return Signal("short", sl=sl, tp=tp,
                                  reason=f"OB+VP bear RR1:{self.RR_TARGET:.0f}")
        return None


class OBRetestV2Premium(Strategy):
    """
    V2 = OBRetestVPFiltered + ADX > 20 + volume > 1.3x moyenne 20p.

    Idee : etre encore plus selectif. ADX filtre les ranges plats (faux signaux),
    volume confirme l'interet institutionnel sur la bougie de retest.

    SL : OB +/- 0.3 ATR.   TP : R:R 1:2.
    """
    name = "OB_Retest_V2_Premium"
    LOOKBACK_OB = 50
    ATR_MULT = 2.0
    LOOKFORWARD = 3
    VP_LOOKBACK = 250
    VP_BINS = 30
    VP_RECOMPUTE_EVERY = 25
    POC_PROXIMITY_PCT = 0.02
    ADX_MIN = 20.0
    VOLUME_MULT = 1.3

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["atr"] = atr(df, 14)
        df["adx"] = adx(df, 14)
        df["vol_ma20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_ma20"]

        # OBs (memes formules que V1)
        bull_top = [np.nan] * len(df)
        bull_bot = [np.nan] * len(df)
        bear_top = [np.nan] * len(df)
        bear_bot = [np.nan] * len(df)
        opens = df["open"].to_numpy()
        closes = df["close"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        atrs = df["atr"].to_numpy()
        for i in range(15, len(df) - self.LOOKFORWARD):
            a = atrs[i]
            if pd.isna(a):
                continue
            future_h = highs[i + 1:i + 1 + self.LOOKFORWARD].max()
            future_l = lows[i + 1:i + 1 + self.LOOKFORWARD].min()
            thr = self.ATR_MULT * a
            if future_h - closes[i] > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] < opens[j]:
                        bull_top[j] = highs[j]
                        bull_bot[j] = lows[j]
                        break
            if closes[i] - future_l > thr:
                for j in range(i, max(i - 3, -1), -1):
                    if closes[j] > opens[j]:
                        bear_top[j] = highs[j]
                        bear_bot[j] = lows[j]
                        break
        df["bull_ob_top"] = bull_top
        df["bull_ob_bot"] = bull_bot
        df["bear_ob_top"] = bear_top
        df["bear_ob_bot"] = bear_bot

        # VP par paliers
        n = len(df)
        poc_col = [np.nan] * n
        vah_col = [np.nan] * n
        val_col = [np.nan] * n
        for i in range(self.VP_LOOKBACK, n, self.VP_RECOMPUTE_EVERY):
            window = df.iloc[i - self.VP_LOOKBACK:i]
            try:
                vp = compute_volume_profile(window, bins=self.VP_BINS)
                end = min(i + self.VP_RECOMPUTE_EVERY, n)
                for k in range(i, end):
                    poc_col[k] = vp.poc
                    vah_col[k] = vp.vah
                    val_col[k] = vp.val
            except Exception:
                pass
        df["poc"] = poc_col
        df["vah"] = vah_col
        df["val"] = val_col
        return df

    def _vp_passes(self, level: float, cur) -> bool:
        if pd.isna(cur["poc"]):
            return True
        in_va = cur["val"] <= level <= cur["vah"]
        near_poc = abs(level - cur["poc"]) / cur["poc"] < self.POC_PROXIMITY_PCT
        return in_va or near_poc

    def entry_signal(self, df, i):
        if i < max(200, self.VP_LOOKBACK):
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["atr"]) or pd.isna(cur["adx"]):
            return None

        # Filtres premium V2
        if cur["adx"] < self.ADX_MIN:
            return None
        if pd.isna(cur["vol_ratio"]) or cur["vol_ratio"] < self.VOLUME_MULT:
            return None

        if cur["close"] > cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bull_ob_top"].iloc[j]
                bot = df["bull_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] < bot).any():
                    continue
                if cur["low"] <= top and cur["low"] >= bot * 0.997:
                    if not self._vp_passes((top + bot) / 2, cur):
                        continue
                    entry = cur["close"]
                    sl = bot - 0.3 * cur["atr"]
                    if entry <= sl:
                        continue
                    tp = entry + (entry - sl) * 2
                    return Signal("long", sl=sl, tp=tp,
                                  reason=f"OB+VP+ADX+Vol bull {bot:.0f}-{top:.0f}")

        if cur["close"] < cur["ema200"]:
            start = max(0, i - self.LOOKBACK_OB)
            for j in range(start, i):
                top = df["bear_ob_top"].iloc[j]
                bot = df["bear_ob_bot"].iloc[j]
                if pd.isna(top):
                    continue
                if (df["close"].iloc[j + 1:i] > top).any():
                    continue
                if cur["high"] >= bot and cur["high"] <= top * 1.003:
                    if not self._vp_passes((top + bot) / 2, cur):
                        continue
                    entry = cur["close"]
                    sl = top + 0.3 * cur["atr"]
                    if sl <= entry:
                        continue
                    tp = entry - (sl - entry) * 2
                    return Signal("short", sl=sl, tp=tp,
                                  reason=f"OB+VP+ADX+Vol bear {bot:.0f}-{top:.0f}")

        return None


class VolumeProfileRetest(Strategy):
    """
    Trade les retests des niveaux cles du Volume Profile : POC, VAH, VAL.

    Le VP est recalcule tous les VP_RECOMPUTE_EVERY bougies sur une fenetre
    glissante de VP_LOOKBACK pour suivre l'evolution du marche.

    Long  : prix > EMA200 + retest VAL ou POC depuis au-dessus + RSI 35-55.
    Short : prix < EMA200 + retest VAH ou POC depuis en-dessous + RSI 45-65.

    SL : 1 ATR au-dela du niveau.   TP : R:R 1:2.
    """
    name = "VolumeProfile_Retest"
    VP_LOOKBACK = 250
    VP_BINS = 30
    VP_RECOMPUTE_EVERY = 25

    def prepare(self, df):
        df = df.copy()
        df["ema200"] = ema(df["close"], 200)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)

        n = len(df)
        poc_col = [np.nan] * n
        vah_col = [np.nan] * n
        val_col = [np.nan] * n

        for i in range(self.VP_LOOKBACK, n, self.VP_RECOMPUTE_EVERY):
            window = df.iloc[i - self.VP_LOOKBACK:i]
            try:
                vp = compute_volume_profile(window, bins=self.VP_BINS)
                end = min(i + self.VP_RECOMPUTE_EVERY, n)
                for k in range(i, end):
                    poc_col[k] = vp.poc
                    vah_col[k] = vp.vah
                    val_col[k] = vp.val
            except Exception:
                pass

        df["poc"] = poc_col
        df["vah"] = vah_col
        df["val"] = val_col
        return df

    def entry_signal(self, df, i):
        if i < self.VP_LOOKBACK:
            return None
        cur = df.iloc[i]
        if pd.isna(cur["ema200"]) or pd.isna(cur["poc"]) or pd.isna(cur["atr"]):
            return None

        price = cur["close"]
        tol = cur["atr"] * 0.5

        # LONG : retest VAL ou POC en venant d'au-dessus
        if price > cur["ema200"] and 35 < cur["rsi"] < 55:
            for level_name, level in [("VAL", cur["val"]), ("POC", cur["poc"])]:
                if cur["low"] <= level + tol and cur["close"] > level:
                    sl = level - 1.0 * cur["atr"]
                    if price <= sl:
                        continue
                    tp = price + 2 * (price - sl)
                    return Signal("long", sl=sl, tp=tp, reason=f"Retest {level_name} {level:.0f}")

        # SHORT : retest VAH ou POC en venant d'en-dessous
        if price < cur["ema200"] and 45 < cur["rsi"] < 65:
            for level_name, level in [("VAH", cur["vah"]), ("POC", cur["poc"])]:
                if cur["high"] >= level - tol and cur["close"] < level:
                    sl = level + 1.0 * cur["atr"]
                    if sl <= price:
                        continue
                    tp = price - 2 * (sl - price)
                    return Signal("short", sl=sl, tp=tp, reason=f"Retest {level_name} {level:.0f}")

        return None


# =============================================================
#                       BACKTESTER
# =============================================================

class Backtester:
    def __init__(
        self,
        initial_capital: float = 10_000,
        risk_pct: float = 0.01,           # 1% de l'equity risque par trade
        commission_pct: float = 0.001,    # 0.10% par trade (taker Binance)
        slippage_pct: float = 0.0005,     # 0.05% slippage
        allow_short: bool = True,
        trailing_atr: float = 0.0,        # 0 = pas de trailing, >0 = trail le SL de N x ATR
    ):
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.commission = commission_pct
        self.slippage = slippage_pct
        self.allow_short = allow_short
        self.trailing_atr = trailing_atr

    def run(self, df: pd.DataFrame, strategy: Strategy) -> BacktestResult:
        df = strategy.prepare(df)
        equity = self.initial_capital
        position: Optional[dict] = None
        trades: List[Trade] = []
        eq_curve = []

        for i in range(len(df)):
            bar = df.iloc[i]

            # 1) gestion d'une position ouverte (SL/TP intrabar)
            if position is not None:
                # Trailing stop : remonte le SL si le prix avance favorablement
                if self.trailing_atr > 0 and "atr" in df.columns:
                    a = df["atr"].iloc[i]
                    if not pd.isna(a):
                        if position["direction"] == "long":
                            new_sl = bar["high"] - self.trailing_atr * a
                            if new_sl > position["sl"]:
                                position["sl"] = new_sl
                        else:
                            new_sl = bar["low"] + self.trailing_atr * a
                            if new_sl < position["sl"]:
                                position["sl"] = new_sl

                exit_price = None
                exit_reason = None
                if position["direction"] == "long":
                    if bar["low"] <= position["sl"]:
                        exit_price = position["sl"] * (1 - self.slippage)
                        exit_reason = "SL"
                    elif bar["high"] >= position["tp"]:
                        exit_price = position["tp"] * (1 - self.slippage)
                        exit_reason = "TP"
                else:
                    if bar["high"] >= position["sl"]:
                        exit_price = position["sl"] * (1 + self.slippage)
                        exit_reason = "SL"
                    elif bar["low"] <= position["tp"]:
                        exit_price = position["tp"] * (1 + self.slippage)
                        exit_reason = "TP"

                if exit_price is not None:
                    if position["direction"] == "long":
                        pnl = (exit_price - position["entry_price"]) * position["size"]
                    else:
                        pnl = (position["entry_price"] - exit_price) * position["size"]
                    fee_exit = exit_price * position["size"] * self.commission
                    pnl_net = pnl - fee_exit
                    equity += pnl_net
                    trades.append(Trade(
                        entry_time=position["entry_time"],
                        exit_time=bar.name,
                        direction=position["direction"],
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        size=position["size"],
                        pnl=pnl_net,
                        pnl_pct=pnl_net / (position["entry_price"] * position["size"]),
                        bars_held=i - position["entry_idx"],
                        exit_reason=exit_reason,
                        reason=position.get("reason", ""),
                    ))
                    position = None

            # 2) evaluation du signal d'entree
            if position is None:
                sig = strategy.entry_signal(df, i)
                if sig is not None:
                    if sig.direction == "short" and not self.allow_short:
                        sig = None
                if sig is not None:
                    raw_entry = bar["close"]
                    entry = raw_entry * (1 + self.slippage if sig.direction == "long" else 1 - self.slippage)
                    risk_per_unit = abs(entry - sig.sl)
                    if risk_per_unit <= 0:
                        eq_curve.append(equity)
                        continue
                    size = (equity * self.risk_pct) / risk_per_unit
                    fee_entry = entry * size * self.commission
                    equity -= fee_entry
                    position = {
                        "entry_price": entry,
                        "sl": sig.sl,
                        "tp": sig.tp,
                        "size": size,
                        "direction": sig.direction,
                        "entry_time": bar.name,
                        "entry_idx": i,
                        "reason": sig.reason,
                    }

            # 3) equity curve (mark-to-market)
            unreal = 0.0
            if position is not None:
                if position["direction"] == "long":
                    unreal = (bar["close"] - position["entry_price"]) * position["size"]
                else:
                    unreal = (position["entry_price"] - bar["close"]) * position["size"]
            eq_curve.append(equity + unreal)

        eq_series = pd.Series(eq_curve, index=df.index)
        metrics = self._compute_metrics(trades, eq_series)
        return BacktestResult(
            initial_capital=self.initial_capital,
            final_equity=eq_series.iloc[-1],
            trades=trades,
            equity_curve=eq_series,
            metrics=metrics,
        )

    def _compute_metrics(self, trades: List[Trade], equity: pd.Series) -> dict:
        n = len(trades)
        if n == 0:
            return {"trades": 0}

        pnls = np.array([t.pnl for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]

        total_return = (equity.iloc[-1] / self.initial_capital - 1) * 100
        win_rate = len(wins) / n * 100
        profit_factor = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
        avg_win = wins.mean() if len(wins) else 0.0
        avg_loss = losses.mean() if len(losses) else 0.0
        expectancy = pnls.mean()

        returns = equity.pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(365 * 6)) if returns.std() > 0 else 0.0  # 6 bougies 4h/jour

        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max * 100
        max_dd = drawdown.min()

        avg_bars = np.mean([t.bars_held for t in trades])

        return {
            "trades": n,
            "win_rate_pct": round(win_rate, 2),
            "total_return_pct": round(total_return, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy_per_trade": round(expectancy, 2),
            "sharpe_annualized": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_bars_held": round(avg_bars, 1),
        }


# =============================================================
#                       PRESENTATION
# =============================================================

def print_result(name: str, res: BacktestResult) -> None:
    print(f"\n{'='*70}")
    print(f"  Strategie : {name}")
    print(f"{'='*70}")
    print(f"  Capital initial    : {res.initial_capital:,.0f}")
    print(f"  Capital final      : {res.final_equity:,.2f}")
    print(f"  Nombre de trades   : {res.metrics.get('trades', 0)}")
    if res.metrics.get("trades", 0) == 0:
        print("  (aucun trade declenche sur cette periode)")
        return
    print(f"  Win rate           : {res.metrics['win_rate_pct']}%")
    print(f"  Profit factor      : {res.metrics['profit_factor']}")
    print(f"  Total return       : {res.metrics['total_return_pct']}%")
    print(f"  Max drawdown       : {res.metrics['max_drawdown_pct']}%")
    print(f"  Sharpe (annualise) : {res.metrics['sharpe_annualized']}")
    print(f"  Esperance / trade  : {res.metrics['expectancy_per_trade']:.2f}")
    print(f"  Trade moyen tenu   : {res.metrics['avg_bars_held']} bougies")

    # repartition exit_reason
    sl_count = sum(1 for t in res.trades if t.exit_reason == "SL")
    tp_count = sum(1 for t in res.trades if t.exit_reason == "TP")
    print(f"  Sorties            : TP={tp_count}  SL={sl_count}")

    # ASCII equity curve (50 bins)
    eq = res.equity_curve.values
    if len(eq) > 50:
        step = len(eq) // 50
        sampled = eq[::step][:50]
    else:
        sampled = eq
    lo, hi = sampled.min(), sampled.max()
    if hi > lo:
        norm = ((sampled - lo) / (hi - lo) * 12).astype(int)
        rows = []
        for level in range(12, -1, -1):
            row = "  " + "".join("#" if v >= level else " " for v in norm)
            rows.append(row)
        print(f"\n  Courbe d'equity :  ({lo:,.0f} -> {hi:,.0f})")
        for row in rows:
            print(row)


# =============================================================
#                  WALK-FORWARD / ROBUSTESSE
# =============================================================

def rolling_backtest(df: pd.DataFrame, strategy_cls, bt: "Backtester",
                     window: int = 300, step: int = 100, min_trades: int = 3) -> List[dict]:
    """
    Re-run la strategie sur des fenetres glissantes pour mesurer la consistance.
    Une edge solide doit rester positive sur la majorite des fenetres.
    """
    results = []
    for start in range(0, len(df) - window + 1, step):
        win_df = df.iloc[start:start + window]
        s = strategy_cls()
        res = bt.run(win_df, s)
        if res.metrics.get("trades", 0) >= min_trades:
            results.append({
                "start": win_df.index[0],
                "end": win_df.index[-1],
                "metrics": res.metrics,
            })
    return results


def print_walk_forward(name: str, results: List[dict]) -> None:
    print(f"\n{'-'*70}")
    print(f"  WALK-FORWARD : {name}")
    print(f"{'-'*70}")
    if not results:
        print("  pas assez de trades pour evaluer la robustesse")
        return
    print(f"  {len(results)} fenetres analysees")
    print(f"  {'Debut':<14s}  {'Trades':>7s}  {'Win%':>6s}  {'PF':>5s}  {'Return%':>8s}  {'DD%':>6s}")
    rets = []
    pfs = []
    wrs = []
    for r in results:
        m = r["metrics"]
        pf = m["profit_factor"]
        pf_disp = pf if pf != float("inf") else 99.0
        print(f"  {str(r['start'])[:10]:<14s}  {m['trades']:>7d}  "
              f"{m['win_rate_pct']:>5.1f}  {pf_disp:>5.2f}  "
              f"{m['total_return_pct']:>7.1f}  {m['max_drawdown_pct']:>6.1f}")
        rets.append(m["total_return_pct"])
        wrs.append(m["win_rate_pct"])
        if pf != float("inf"):
            pfs.append(pf)

    positive = sum(1 for r in rets if r > 0)
    print(f"\n  Robustesse :")
    print(f"    Fenetres rentables : {positive}/{len(rets)} ({positive / len(rets) * 100:.0f}%)")
    if pfs:
        print(f"    PF moyen           : {np.mean(pfs):.2f}  (ecart-type {np.std(pfs):.2f})")
    print(f"    Win rate moyen     : {np.mean(wrs):.1f}%")
    print(f"    Return moyen       : {np.mean(rets):+.2f}%  (ecart-type {np.std(rets):.2f})")


# =============================================================
#                          MAIN
# =============================================================

def fetch_data(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    import ccxt
    ex = ccxt.binance({"enableRateLimit": True})
    bars = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return df


def main():
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "4h"

    print(f"\nChargement {symbol} {timeframe} depuis Binance ...")
    df = fetch_data(symbol, timeframe, 1000)
    print(f"  {len(df)} bougies chargees, du {df.index[0]} au {df.index[-1]}\n")

    bt = Backtester(initial_capital=10_000, risk_pct=0.01)
    strategies = [EmaCrossover, RsiMeanReversion, Confluence, FvgRetest, OrderBlockRetest, VolumeProfileRetest, OBRetestVPFiltered, OBRetestV2Premium, OBRetestV3LargeTP]

    # Backtest plein
    full_results: dict = {}
    for strat_cls in strategies:
        s = strat_cls()
        res = bt.run(df, s)
        print_result(s.name, res)
        full_results[s.name] = res

    # Classement par profit factor (sur >= 5 trades)
    ranked = sorted(
        [(name, r) for name, r in full_results.items() if r.metrics.get("trades", 0) >= 5],
        key=lambda x: x[1].metrics.get("profit_factor", 0),
        reverse=True,
    )

    print(f"\n{'='*70}")
    print(f"  CLASSEMENT (par profit factor, min 5 trades)")
    print(f"{'='*70}")
    for name, r in ranked:
        m = r.metrics
        print(f"  {name:<26s}  PF {m['profit_factor']:>5.2f}  "
              f"WR {m['win_rate_pct']:>5.1f}%  "
              f"Return {m['total_return_pct']:>+6.1f}%  "
              f"DD {m['max_drawdown_pct']:>6.1f}%  "
              f"Sharpe {m['sharpe_annualized']:>5.2f}")

    # Walk-forward des 3 meilleures
    print(f"\n{'='*70}")
    print(f"  WALK-FORWARD DES 3 MEILLEURES STRATEGIES")
    print(f"{'='*70}")
    for name, _ in ranked[:3]:
        cls = next(c for c in strategies if c.name == name)
        wf = rolling_backtest(df, cls, bt, window=300, step=100)
        print_walk_forward(name, wf)

    # Comparaison trailing stop sur la meilleure strategie
    if ranked:
        best_name, _ = ranked[0]
        best_cls = next(c for c in strategies if c.name == best_name)
        print(f"\n{'='*70}")
        print(f"  TEST TRAILING STOP sur {best_name}")
        print(f"{'='*70}")
        print(f"  {'Trailing':<18s}  {'Trades':>6s}  {'Win%':>5s}  {'PF':>5s}  {'Return%':>8s}  {'DD%':>6s}")
        for trail in [0.0, 1.0, 2.0, 3.0]:
            bt2 = Backtester(initial_capital=10_000, risk_pct=0.01, trailing_atr=trail)
            res = bt2.run(df, best_cls())
            m = res.metrics
            label = "off" if trail == 0 else f"{trail} x ATR"
            if m.get("trades", 0) > 0:
                pf = m["profit_factor"] if m["profit_factor"] != float("inf") else 99.0
                print(f"  {label:<18s}  {m['trades']:>6d}  {m['win_rate_pct']:>5.1f}  "
                      f"{pf:>5.2f}  {m['total_return_pct']:>+7.1f}  {m['max_drawdown_pct']:>6.1f}")

    print("\nRappel : performance passee != performance future.")
    print("Le walk-forward mesure la consistance, pas la garantie.\n")


if __name__ == "__main__":
    main()
