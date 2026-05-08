"""
Indicateurs techniques partages.
Pas de dependance autre que pandas/numpy.
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False).mean()


def sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length).mean()


def rsi(s: pd.Series, length: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = up / dn
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3):
    lo = df["low"].rolling(k).min()
    hi = df["high"].rolling(k).max()
    pk = 100 * (df["close"] - lo) / (hi - lo)
    return pk, pk.rolling(d).mean()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def bbands(s: pd.Series, length: int = 20, k: float = 2.0):
    mid = sma(s, length)
    sd = s.rolling(length).std()
    return mid - k * sd, mid, mid + k * sd


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up_move = h.diff()
    dn_move = -l.diff()
    plus_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / length, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / length, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


def swing_points(df: pd.DataFrame, lookback: int = 5):
    win = 2 * lookback + 1
    sh = df["high"] == df["high"].rolling(win, center=True).max()
    sl = df["low"] == df["low"].rolling(win, center=True).min()
    return sh.fillna(False), sl.fillna(False)
