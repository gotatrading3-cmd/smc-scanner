"""
signal_data.py - Donnees de marche du systeme de signaux. DEUX backends interchangeables :

  mt5   : MetaTrader 5 (Windows, PC local)  -> tous les instruments, spread reel, tick volume
  yahoo : Yahoo Finance via yfinance (GitHub Actions / n'importe ou, gratuit, sans cle)
          -> paires forex + crypto (prix spot).
          -> or et indices US : Yahoo ne donne que les CONTRATS A TERME, decales du prix CFD du courtier (environ 35 $ sur l'or,
             250-300 points sur le US100/US30). On garde leurs bougies (la forme du marche) mais on les RECALE sur le vrai prix
             CFD de Dukascopy (gratuit, sans cle) : voir _anchor(). Sans ecart recent connu, l'instrument est ignore - jamais
             de niveaux publies avec un decalage inconnu. Petrole / argent / DAX restent exclus (pas encore recales).

Choix du backend : variable d'environnement SIGNAL_BACKEND (mt5|yahoo). Defaut : mt5 sous Windows,
yahoo ailleurs. LECTURE SEULE : ce module ne passe JAMAIS d'ordre.
"""
from __future__ import annotations
import sys
import os
import json
import lzma
import struct
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

# id public -> {mt5: nom MT5, yahoo: ticker Yahoo (None = indisponible), cls: classe d'actif,
#               anchor: instrument Dukascopy qui donne le prix CFD pour recaler un contrat a terme (voir _anchor)}
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
    "XAUUSD": dict(mt5="GOLD", yahoo="GC=F", cls="metal", anchor="XAUUSD"),
    "XAGUSD": dict(mt5="SILVER", yahoo=None, cls="metal"),
    "US100": dict(mt5="US100Cash", yahoo="NQ=F", cls="index", anchor="USATECHIDXUSD"),
    "US30": dict(mt5="US30Cash", yahoo="YM=F", cls="index", anchor="USA30IDXUSD"),
    "US500": dict(mt5="US500Cash", yahoo="ES=F", cls="index", anchor="USA500IDXUSD"),
    "GER40": dict(mt5="GER40Cash", yahoo=None, cls="index"),
    "BTCUSD": dict(mt5="BTCUSD", yahoo="BTC-USD", cls="crypto"),
    "ETHUSD": dict(mt5="ETHUSD", yahoo="ETH-USD", cls="crypto"),
    "USOIL": dict(mt5="OILCash", yahoo=None, cls="oil"),
}
# Toutes les paires de devises majeures + croisees (28) : les exotiques sont exclues (spreads trop larges)
_FX_EXTRA = ["EURGBP", "EURAUD", "EURCAD", "EURCHF", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
             "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD", "CADJPY", "CADCHF", "CHFJPY", "NZDJPY", "NZDCAD", "NZDCHF"]
for _p in _FX_EXTRA:
    UNIVERSE.setdefault(_p, dict(mt5=_p, yahoo=f"{_p}=X", cls="fx"))
# compat backtest : nom MT5 -> (id public, classe)
WATCHLIST: Dict[str, Tuple[str, str]] = {v["mt5"]: (k, v["cls"]) for k, v in UNIVERSE.items()}

TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
TF_LABEL = {1: "M1", 5: "M5", 15: "M15", 60: "H1", 240: "H4", 1440: "D1"}


FUTURES_OFF = os.environ.get("SIGNALS_FUTURES", "").strip().lower() in ("off", "0", "false", "no")   # interrupteur : or + indices


def available_ids() -> list:
    """Instruments disponibles avec le backend courant."""
    if BACKEND == "yahoo":
        return [k for k, v in UNIVERSE.items() if v["yahoo"] and not (v.get("anchor") and FUTURES_OFF)]
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


# ---- recalage des contrats a terme (or, indices US) sur le prix CFD -------------------------------------------------------------
# Ecart = (dernier cours acheteur CFD de l'heure, Dukascopy) - (cloture de la barre horaire du contrat a terme). Mesure une fois par
# heure (+ un point par jour ouvre a 15 h UTC pour les 30 derniers jours) et gardee dans basis_cache.json (branche data en ligne).
# Chaque echantillon, une fois ecrit, ne change plus : l'historique recale est stable d'un cycle a l'autre.
BASIS_FILE = Path(os.environ.get("SIGNALS_DATA_DIR") or DIR) / "basis_cache.json"
DUKA_URL = "https://datafeed.dukascopy.com/datafeed/{pair}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"   # mois numerotes a partir de 0
BASIS_BACKFILL_DAYS = 30
BASIS_REQUESTS_PER_CALL = 8          # requetes reseau max par instrument et par appel (le rattrapage se fait sur quelques cycles)
BASIS_MAX_STALE_H = 72               # sans ecart recent (source HS), l'instrument est ignore plutot que publie avec un decalage inconnu
BASIS_TIME_BUDGET_S = 25.0           # temps reseau max par passage et par instrument : une panne de la source ne doit jamais bloquer les scans
_BASIS: Optional[dict] = None
_BASIS_DONE: set = set()             # un seul rattrapage par instrument et par passage (un passage = un cycle de 5 min)
_DUKA_FAILS = 0                      # pannes consecutives de la source dans ce passage : au bout de 2 on n'insiste plus


def _basis_all() -> dict:
    global _BASIS
    if _BASIS is None:
        try:
            _BASIS = json.loads(BASIS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _BASIS = {}
    return _BASIS


def _basis_save() -> None:
    try:
        BASIS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BASIS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_basis_all(), separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, BASIS_FILE)
    except Exception:
        pass


def _duka_last_bid(pair: str, hour: pd.Timestamp) -> Optional[float]:
    """Dernier cours acheteur (bid) CFD de l'heure UTC commencant a `hour` ; None si pas de ticks (marche ferme) ou source indisponible."""
    global _DUKA_FAILS
    import requests
    if _DUKA_FAILS >= 2:
        return None
    url = DUKA_URL.format(pair=pair, y=hour.year, m=hour.month - 1, d=hour.day, h=hour.hour)
    for k in range(2):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (GOTA signals)"}, timeout=15)
        except Exception:
            time.sleep(1)
            continue
        if r.status_code == 200:
            _DUKA_FAILS = 0
            try:
                raw = lzma.decompress(r.content) if r.content else b""
            except Exception:
                return None
            n = len(raw) // 20
            if n == 0:
                return None
            _ms, _ask, bid, _av, _bv = struct.unpack(">IIIff", raw[(n - 1) * 20:n * 20])
            return bid / 1000.0
        if r.status_code in (403, 404):
            _DUKA_FAILS = 0
            return None                                           # pas de ticks cette heure-la (marche ferme)
        time.sleep(1)                                             # 503 / 429 : un seul nouvel essai
    _DUKA_FAILS += 1
    return None


def _update_basis(sid: str) -> None:
    """Complete le cache d'ecarts de `sid` : derniere heure complete, puis rattrapage progressif des jours passes."""
    if sid in _BASIS_DONE:
        return
    _BASIS_DONE.add(sid)
    cfg = UNIVERSE[sid]
    fut = _yf_fetch(cfg["yahoo"], "1h", "730d")
    if fut is None or len(fut) == 0:
        return
    t_start = time.time()
    store = _basis_all().setdefault(sid, {})
    now = utc_now()
    wanted = [(now - pd.Timedelta(minutes=75)).floor("h")]        # derniere heure complete (Dukascopy publie avec un peu de retard)
    for back in range(1, BASIS_BACKFILL_DAYS + 1):
        day = (now - pd.Timedelta(days=back)).normalize()
        if day.weekday() < 5:
            wanted.append(day + pd.Timedelta(hours=15))
    tried, dirty = 0, False
    for h in wanted:
        key = h.strftime("%Y-%m-%dT%H")
        if key in store or h not in fut.index:
            continue
        if tried >= BASIS_REQUESTS_PER_CALL or time.time() - t_start > BASIS_TIME_BUDGET_S or _DUKA_FAILS >= 2:
            break
        tried += 1
        bid = _duka_last_bid(cfg["anchor"], h)
        if bid is None:
            continue
        store[key] = round(bid - float(fut.loc[h, "close"]), 3)
        dirty = True
    old = (now - pd.Timedelta(days=45)).strftime("%Y-%m-%dT%H")
    for k in [k for k in store if k < old]:
        del store[k]
        dirty = True
    if dirty:
        _basis_save()


def _anchor(sid: str, df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Recale des bougies de contrat a terme sur le prix CFD du courtier. None si aucun ecart recent n'est connu."""
    _update_basis(sid)
    store = _basis_all().get(sid) or {}
    if not store:
        return None
    keys = sorted(store)
    times = pd.to_datetime([k + ":00" for k in keys], format="%Y-%m-%dT%H:%M")
    if utc_now() - times[-1] > pd.Timedelta(hours=BASIS_MAX_STALE_H):
        return None
    vals = np.array([store[k] for k in keys], dtype=float)
    pos = np.clip(np.searchsorted(times.values, df.index.values, side="right") - 1, 0, len(vals) - 1)
    out = df.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].to_numpy(float) + vals[pos]
    return out


def _yahoo_rates(sym: str, tf_key: str, count: int, closed_only: bool) -> Optional[pd.DataFrame]:
    sid = _sid(sym)
    cfg = UNIVERSE.get(sid, {})
    tk = cfg.get("yahoo")
    if not tk:
        return None
    if tf_key == "5m":
        df = _yf_fetch(tk, "5m", "30d")
    elif tf_key == "15m":
        df = _yf_fetch(tk, "15m", "60d")
    elif tf_key in ("1h", "4h"):
        df = _yf_fetch(tk, "1h", "730d")
    elif tf_key == "1d":
        df = _yf_fetch(tk, "1d", "10y")
    else:
        return None
    if df is None or len(df) == 0:
        return None
    if cfg.get("anchor"):                                       # or / indices US : contrat a terme -> prix CFD du courtier
        df = _anchor(sid, df)
        if df is None:
            return None
    if tf_key == "4h":
        df = _resample(df, 240)
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
