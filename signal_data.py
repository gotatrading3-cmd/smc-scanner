"""
signal_data.py - Donnees de marche du systeme de signaux. DEUX backends interchangeables :

  mt5   : MetaTrader 5 (Windows, PC local)  -> tous les instruments, spread reel, tick volume
  yahoo : Yahoo Finance via yfinance (GitHub Actions / n'importe ou, gratuit, sans cle)
          -> paires forex + crypto (prix spot). Metaux / indices / petrole : contrats a terme
             chez Yahoo (decalage de prix vs CFD) => exclus du cloud, on ne publie pas de faux niveaux.

Choix du backend : variable d'environnement SIGNAL_BACKEND (mt5|yahoo). Defaut : mt5 sous Windows,
yahoo ailleurs. LECTURE SEULE : ce module ne passe JAMAIS d'ordre.
"""
from __future__ import annotations
import sys
import os
import json
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

DIR = Path(__file__).parent
CACHE_DIR = DIR / "signal_cache"
MT5_CONFIG = DIR / "mt5_config.json"
META_FILE = DIR / "signal_meta.json"
MT5_EXE_CANDIDATES = [
    r"C:\Program Files\XM Global MT5\terminal64.exe",
    r"C:\Program Files\XMTrading MT5\terminal64.exe",
    r"C:\Program Files\XM MT5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
]

BACKEND = (os.environ.get("SIGNAL_BACKEND") or ("mt5" if sys.platform == "win32" else "yahoo")).lower()

# id public -> {mt5: nom MT5, yahoo: ticker Yahoo (None = indisponible en spot), cls: classe d'actif}
UNIVERSE: Dict[str, dict] = {
    "EURUSD": dict(mt5="EURUSD", yahoo="EURUSD=X", cls="fx"),
    "GBPUSD": dict(mt5="GBPUSD", yahoo="GBPUSD=X", cls="fx"),
    "USDJPY": dict(mt5="USDJPY", yahoo="USDJPY=X", cls="fx"),
    "AUDUSD": dict(mt5="AUDUSD", yahoo="AUDUSD=X", cls="fx"),
    "USDCAD": dict(mt5="USDCAD", yahoo="USDCAD=X", cls="fx"),
    "USDCHF": dict(mt5="USDCHF", yahoo="USDCHF=X", cls="fx"),
    "NZDUSD": dict(mt5="NZDUSD", yahoo="NZDUSD=X", cls="fx"),
    "EURJPY": dict(mt5="EURJPY", yahoo="EURJPY=X", cls="fx"),
    "GBPJPY": dict(mt5="GBPJPY", yahoo="GBPJPY=X", cls="fx"),
    "XAUUSD": dict(mt5="GOLD", yahoo=None, cls="metal"),
    "XAGUSD": dict(mt5="SILVER", yahoo=None, cls="metal"),
    "US100": dict(mt5="US100Cash", yahoo=None, cls="index"),
    "US30": dict(mt5="US30Cash", yahoo=None, cls="index"),
    "US500": dict(mt5="US500Cash", yahoo=None, cls="index"),
    "GER40": dict(mt5="GER40Cash", yahoo=None, cls="index"),
    "BTCUSD": dict(mt5="BTCUSD", yahoo="BTC-USD", cls="crypto"),
    "ETHUSD": dict(mt5="ETHUSD", yahoo="ETH-USD", cls="crypto"),
    "USOIL": dict(mt5="OILCash", yahoo=None, cls="oil"),
}
# compat backtest : nom MT5 -> (id public, classe)
WATCHLIST: Dict[str, Tuple[str, str]] = {v["mt5"]: (k, v["cls"]) for k, v in UNIVERSE.items()}

TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
TF_LABEL = {1: "M1", 5: "M5", 15: "M15", 60: "H1", 240: "H4", 1440: "D1"}


def available_ids() -> list:
    """Instruments disponibles avec le backend courant."""
    if BACKEND == "yahoo":
        return [k for k, v in UNIVERSE.items() if v["yahoo"]]
    return list(UNIVERSE)


def utc_now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))


def _meta_all() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _mt5_name(sym: str) -> str:
    return UNIVERSE[sym]["mt5"] if sym in UNIVERSE else sym


def _sid(sym: str) -> str:
    """Nom MT5 ou id -> id public."""
    if sym in UNIVERSE:
        return sym
    return WATCHLIST[sym][0] if sym in WATCHLIST else sym


# =====================================================================================
#                                   BACKEND  MT5
# =====================================================================================
_mt5 = None


def _mt5mod():
    global _mt5
    if _mt5 is None:
        import MetaTrader5 as m  # noqa: WPS433
        _mt5 = m
    return _mt5


def _tf_const(tf_key: str):
    m = _mt5mod()
    return {"1m": m.TIMEFRAME_M1, "5m": m.TIMEFRAME_M5, "15m": m.TIMEFRAME_M15,
            "1h": m.TIMEFRAME_H1, "4h": m.TIMEFRAME_H4, "1d": m.TIMEFRAME_D1}[tf_key]


def _find_exe() -> Optional[str]:
    for p in MT5_EXE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def connect(retries: int = 5, log=print) -> bool:
    """mt5 : se connecte (lance le terminal si besoin). yahoo : rien a faire."""
    if BACKEND != "mt5":
        return True
    m = _mt5mod()
    if not MT5_CONFIG.exists():
        log("[DATA] mt5_config.json absent - lance setup.cmd")
        return False
    cfg = json.loads(MT5_CONFIG.read_text())
    exe = _find_exe()
    if exe:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"], capture_output=True, text=True).stdout
        if "terminal64.exe" not in out:
            log("[DATA] terminal MT5 absent -> lancement")
            subprocess.Popen([exe], creationflags=0x00000008)
            time.sleep(20)
    for k in range(1, retries + 1):
        kw = dict(login=int(cfg["login"]), password=cfg["password"], server=cfg["server"], timeout=25000)
        if exe:
            kw["path"] = exe
        if m.initialize(**kw):
            return True
        log(f"[DATA] init MT5 tentative {k}/{retries} : {m.last_error()}")
        time.sleep(5)
    return False


def shutdown() -> None:
    if BACKEND == "mt5" and _mt5 is not None:
        try:
            _mt5.shutdown()
        except Exception:
            pass


def server_to_utc(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Serveur XM = GMT+2 (hiver) / GMT+3 (ete, regle US DST : la cloture du jour = 17h New York)."""
    ny = idx.tz_localize("UTC").tz_convert("America/New_York").tz_localize(None)
    off_ny = (ny.to_numpy() - idx.to_numpy()) / np.timedelta64(1, "h")   # -4 (DST) ou -5
    server_off = np.where(off_ny > -4.5, 3, 2)
    return idx - pd.to_timedelta(server_off, unit="h")


def clean_spread(df: pd.DataFrame) -> pd.DataFrame:
    """Le spread historique MT5 est parfois 0 : on evite un backtest irrealiste.
    Plancher = 70% de la mediane des spreads non nuls (les elargissements restent visibles)."""
    if "spread" not in df.columns:
        return df
    s = df["spread"].astype(float)
    nz = s[s > 0]
    if len(nz):
        df = df.copy()
        df["spread"] = s.clip(lower=float(nz.median()) * 0.7)
    return df


def _mt5_rates(sym: str, tf_key: str, count: int, closed_only: bool) -> Optional[pd.DataFrame]:
    m = _mt5mod()
    name = _mt5_name(sym)
    m.symbol_select(name, True)
    r = m.copy_rates_from_pos(name, _tf_const(tf_key), 0, count)
    if r is None or len(r) == 0:
        return None
    df = pd.DataFrame(r)
    idx = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s")).as_unit("ns")
    df.index = server_to_utc(idx)
    df.index.name = "ts"
    df = df[["open", "high", "low", "close", "tick_volume", "spread"]].astype(float)
    if closed_only:
        df = df[df.index + pd.Timedelta(minutes=TF_MIN[tf_key]) <= utc_now()]
    return clean_spread(df)


# =====================================================================================
#                                  BACKEND  YAHOO
# =====================================================================================
_YF_CACHE: Dict[tuple, Tuple[float, Optional[pd.DataFrame]]] = {}


def _yf_fetch(ticker: str, interval: str, period: str, retries: int = 3) -> Optional[pd.DataFrame]:
    key = (ticker, interval, period)
    hit = _YF_CACHE.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    import yfinance as yf
    out = None
    for k in range(retries):
        try:
            d = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False, actions=False)
            if d is not None and len(d) > 0:
                idx = d.index.tz_convert("UTC").tz_localize(None)
                out = pd.DataFrame({"open": d["Open"].to_numpy(float), "high": d["High"].to_numpy(float),
                                    "low": d["Low"].to_numpy(float), "close": d["Close"].to_numpy(float),
                                    "tick_volume": d["Volume"].to_numpy(float)}, index=idx)
                out.index.name = "ts"
                out = out[~out.index.duplicated(keep="last")].sort_index().dropna(subset=["open", "high", "low", "close"])
                break
        except Exception:
            pass
        time.sleep(2 * (k + 1))
    _YF_CACHE[key] = (time.time(), out)
    return out


def _resample(h1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """H1 -> H4 ; bins alignes sur 01:00/05:00/.../21:00 UTC (= H4 du serveur XM en heure d'ete)."""
    agg = h1.resample(f"{minutes}min", offset="1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum"})
    return agg.dropna(subset=["open", "close"])


def _yahoo_rates(sym: str, tf_key: str, count: int, closed_only: bool) -> Optional[pd.DataFrame]:
    sid = _sid(sym)
    tk = UNIVERSE.get(sid, {}).get("yahoo")
    if not tk:
        return None
    if tf_key == "5m":
        df = _yf_fetch(tk, "5m", "30d")
    elif tf_key == "15m":
        df = _yf_fetch(tk, "15m", "60d")
    elif tf_key in ("1h", "4h"):
        df = _yf_fetch(tk, "1h", "730d")
        if df is not None and tf_key == "4h":
            df = _resample(df, 240)
    elif tf_key == "1d":
        df = _yf_fetch(tk, "1d", "10y")
    else:
        return None
    if df is None or len(df) == 0:
        return None
    df = df.tail(count).copy()
    if closed_only:
        df = df[df.index + pd.Timedelta(minutes=TF_MIN[tf_key]) <= utc_now()]
    m = _meta_all().get(sid, {})
    df["spread"] = float(m.get("spread_pts", 0.0))          # spread typique (points) mesure sur MT5
    return df


# =====================================================================================
#                                     API COMMUNE
# =====================================================================================
def get_rates(sym: str, tf_key: str, count: int, closed_only: bool = True) -> Optional[pd.DataFrame]:
    """Bougies (index = ouverture, UTC naif) : open high low close tick_volume spread(points)."""
    return _yahoo_rates(sym, tf_key, count, closed_only) if BACKEND == "yahoo" else _mt5_rates(sym, tf_key, count, closed_only)


def get_history(sym: str, tf_key: str, count: int, max_wait: int = 150, use_cache: bool = True, log=print) -> Optional[pd.DataFrame]:
    """Historique long pour le backtest (mt5 : attend que le terminal ait telecharge les donnees)."""
    if BACKEND == "yahoo":
        return get_rates(sym, tf_key, count)
    CACHE_DIR.mkdir(exist_ok=True)
    f = CACHE_DIR / f"{_mt5_name(sym)}_{tf_key}_{count}.pkl"
    if use_cache and f.exists() and (time.time() - f.stat().st_mtime) < 6 * 3600:
        return pd.read_pickle(f)
    t0, prev, df = time.time(), -1, None
    while True:
        df = get_rates(sym, tf_key, count)
        n = 0 if df is None else len(df)
        waited = time.time() - t0
        if n >= count * 0.98 or (n > 0 and n == prev and waited > 8) or waited > max_wait:
            break
        prev = n
        time.sleep(3)
    if df is not None and len(df) > 0:
        df.to_pickle(f)
    return df


def get_tick(sym: str) -> Optional[Tuple[float, float]]:
    """(bid, ask) actuels ou None. yahoo : derniere cloture 5m (+ spread typique pour le ask)."""
    if BACKEND == "yahoo":
        d = _yahoo_rates(sym, "5m", 3, closed_only=False)
        if d is None or len(d) == 0:
            return None
        meta = symbol_meta(sym) or {}
        px = float(d["close"].iloc[-1])
        return px, px + float(d["spread"].iloc[-1]) * float(meta.get("point", 0.0))
    t = _mt5mod().symbol_info_tick(_mt5_name(sym))
    if t is None or t.bid <= 0:
        return None
    return float(t.bid), float(t.ask)


def symbol_meta(sym: str) -> Optional[dict]:
    """{'point','digits','spread_pts',...}. mt5 : direct ; yahoo : signal_meta.json (mesure sur MT5)."""
    sid = _sid(sym)
    if BACKEND == "yahoo" or not _mt5_ready():
        m = _meta_all().get(sid)
        return dict(m) if m else None
    si = _mt5mod().symbol_info(_mt5_name(sym))
    if si is None:
        return None
    return {"point": si.point, "digits": si.digits, "contract": si.trade_contract_size, "spread_pts": si.spread}


def _mt5_ready() -> bool:
    return BACKEND == "mt5" and _mt5 is not None


def fmt_price(p: float, digits: int) -> str:
    return f"{p:,.{digits}f}".replace(",", " ")
