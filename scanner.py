"""
scanner.py - Monitoring live de setups confluents OB+VP+BoS+RSI.

Toutes les N minutes, scan une liste de symboles et alerte si un setup
LONG ou SHORT confluent est detecte au dernier bar ferme.

Setup LONG (filtre identique au backtest qui sort PF 1.95) :
  - prix > EMA200
  - prix retest un Bull OB non-mitige
  - OB midpoint dans la value area du Volume Profile (VAL <= mid <= VAH) ou pres POC
  - RSI(14) entre 35 et 55
  - BoS bull recent dans les 20 dernieres bougies

Lancement :
    python scanner.py                       # BTC/ETH/SOL en 4H, scan toutes les 15 min
    python scanner.py BTC/USDT ETH/USDT     # symboles custom
    python scanner.py --once                # un seul scan puis quitte
    python scanner.py --tf 1h --interval 5  # 1H, scan toutes les 5 min

Ctrl+C pour arreter.
"""
from __future__ import annotations
import sys
import os

# Robustesse Windows : ajoute explicitement le user site-packages.
# Chemin absolu en 1er (bulletproof si %APPDATA% pas dispo au boot).
for _candidate in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _candidate and os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

import time
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List

from indicators import ema, rsi, atr
from smc import detect_order_blocks, detect_fvg, detect_structure_events
from volume_profile import compute_volume_profile
from notifier import TelegramNotifier


DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]
DEFAULT_TIMEFRAME = "4h"
DEFAULT_INTERVAL_MIN = 15
LIMIT = 500
VP_LOOKBACK = 250
VP_BINS = 30


@dataclass
class SetupAlert:
    symbol: str
    direction: str          # 'LONG' ou 'SHORT'
    timestamp: pd.Timestamp
    entry: float
    sl: float
    tp: float
    reason: str
    rsi: float
    distance_to_ema200_pct: float

    @property
    def rr(self) -> float:
        return abs(self.tp - self.entry) / max(abs(self.entry - self.sl), 1e-9)


def detect_setup(symbol: str, df: pd.DataFrame) -> Optional[SetupAlert]:
    df = df.copy()
    df["ema200"] = ema(df["close"], 200)
    df["rsi14"] = rsi(df["close"], 14)
    df["atr14"] = atr(df, 14)

    last = df.iloc[-1]
    if pd.isna(last["ema200"]) or pd.isna(last["atr14"]):
        return None

    obs = detect_order_blocks(df)
    fvgs = detect_fvg(df)
    events = detect_structure_events(df)
    try:
        vp = compute_volume_profile(df.tail(VP_LOOKBACK), bins=VP_BINS)
    except Exception:
        vp = None

    # BoS recent ?
    last_idx = len(df) - 1
    recent_bull_bos = False
    recent_bear_bos = False
    for ev in events:
        if ev.type in ("BoS", "CHoCH"):
            ev_idx = df.index.get_loc(ev.time)
            if (last_idx - ev_idx) <= 20:
                if ev.direction == "bull":
                    recent_bull_bos = True
                else:
                    recent_bear_bos = True

    price = last["close"]
    bar_low = last["low"]
    bar_high = last["high"]
    a = last["atr14"]

    def vp_passes(level: float) -> bool:
        if vp is None:
            return True
        in_va = vp.is_in_value_area(level)
        near_poc = abs(level - vp.poc) / vp.poc < 0.02
        return in_va or near_poc

    # LONG : trend up + retest OB ou FVG + RSI 25-60 (relaxe pour plus de signaux)
    # BoS recent supprime pour augmenter la frequence
    if price > last["ema200"] and 25 < last["rsi14"] < 60:
        # essai OB d'abord
        for ob in obs:
            if ob.direction == "bull" and not ob.mitigated:
                if bar_low <= ob.top and bar_high >= ob.bottom:
                    if vp_passes(ob.mid):
                        sl = ob.bottom - 0.3 * a
                        if price > sl:
                            tp = price + 1.0 * (price - sl)  # R:R 1:1 (scalp tres court)
                            return SetupAlert(
                                symbol=symbol, direction="LONG",
                                timestamp=last.name, entry=price, sl=sl, tp=tp,
                                reason=f"Order Block Haussier {ob.bottom:.2f}-{ob.top:.2f} dans Zone Valeur",
                                rsi=last["rsi14"],
                                distance_to_ema200_pct=(price - last["ema200"]) / last["ema200"] * 100,
                            )
        # puis FVG
        for fvg in fvgs:
            if fvg.direction == "bull" and not fvg.filled:
                if bar_low <= fvg.top and bar_high >= fvg.bottom:
                    mid = (fvg.top + fvg.bottom) / 2
                    if vp_passes(mid):
                        sl = fvg.bottom - 0.5 * a
                        if price > sl:
                            tp = price + 1.0 * (price - sl)  # R:R 1:1 (scalp tres court)
                            return SetupAlert(
                                symbol=symbol, direction="LONG",
                                timestamp=last.name, entry=price, sl=sl, tp=tp,
                                reason=f"FVG Haussier {fvg.bottom:.2f}-{fvg.top:.2f} dans Zone Valeur",
                                rsi=last["rsi14"],
                                distance_to_ema200_pct=(price - last["ema200"]) / last["ema200"] * 100,
                            )

    # SHORT : symetrique, relaxe pour plus de signaux
    if price < last["ema200"] and 40 < last["rsi14"] < 75:
        for ob in obs:
            if ob.direction == "bear" and not ob.mitigated:
                if bar_high >= ob.bottom and bar_low <= ob.top:
                    if vp_passes(ob.mid):
                        sl = ob.top + 0.3 * a
                        if sl > price:
                            tp = price - 1.0 * (sl - price)  # R:R 1:1 (scalp tres court)
                            return SetupAlert(
                                symbol=symbol, direction="SHORT",
                                timestamp=last.name, entry=price, sl=sl, tp=tp,
                                reason=f"Order Block Baissier {ob.bottom:.2f}-{ob.top:.2f} dans Zone Valeur",
                                rsi=last["rsi14"],
                                distance_to_ema200_pct=(price - last["ema200"]) / last["ema200"] * 100,
                            )
        for fvg in fvgs:
            if fvg.direction == "bear" and not fvg.filled:
                if bar_high >= fvg.bottom and bar_low <= fvg.top:
                    mid = (fvg.top + fvg.bottom) / 2
                    if vp_passes(mid):
                        sl = fvg.top + 0.5 * a
                        if sl > price:
                            tp = price - 1.0 * (sl - price)  # R:R 1:1 (scalp tres court)
                            return SetupAlert(
                                symbol=symbol, direction="SHORT",
                                timestamp=last.name, entry=price, sl=sl, tp=tp,
                                reason=f"FVG Baissier {fvg.bottom:.2f}-{fvg.top:.2f} dans Zone Valeur",
                                rsi=last["rsi14"],
                                distance_to_ema200_pct=(price - last["ema200"]) / last["ema200"] * 100,
                            )

    return None


def fetch(exchange, symbol: str, timeframe: str, limit: int = LIMIT) -> pd.DataFrame:
    bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return df


def scan_once(exchange, symbols: List[str], timeframe: str,
              notifier: Optional[TelegramNotifier] = None,
              already_notified: Optional[set] = None) -> List[SetupAlert]:
    print(f"\n{'='*70}")
    print(f"  Scan {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  TF {timeframe}")
    print(f"{'='*70}")
    alerts: List[SetupAlert] = []
    for symbol in symbols:
        try:
            df = fetch(exchange, symbol, timeframe)
            setup = detect_setup(symbol, df)
            last_price = df["close"].iloc[-1]
            if setup:
                alerts.append(setup)
                print(f"\n  >>> {symbol:<12s}  {setup.direction}  prix {last_price:,.2f}")
                print(f"      Reason : {setup.reason}")
                print(f"      Entry  : {setup.entry:,.4f}")
                print(f"      SL     : {setup.sl:,.4f}  ({(setup.sl - setup.entry) / setup.entry * 100:+.2f}%)")
                print(f"      TP     : {setup.tp:,.4f}  ({(setup.tp - setup.entry) / setup.entry * 100:+.2f}%)")
                print(f"      R:R    : 1:{setup.rr:.2f}")
                print(f"      RSI    : {setup.rsi:.1f}     EMA200 dist : {setup.distance_to_ema200_pct:+.2f}%")

                # Notification Telegram (dedupe par symbole + timestamp pour eviter les spams)
                if notifier and notifier.enabled:
                    key = f"{setup.symbol}|{setup.timestamp}|{setup.direction}"
                    if already_notified is None or key not in already_notified:
                        if notifier.send_setup(setup):
                            print(f"      [Telegram] notification envoyee")
                            if already_notified is not None:
                                already_notified.add(key)
            else:
                print(f"  {symbol:<12s}  prix {last_price:,.2f}  -  pas de setup")
        except Exception as e:
            print(f"  {symbol:<12s}  ERREUR : {e}")
    return alerts


def parse_args(argv: List[str]):
    once = False
    no_telegram = False
    timeframe = DEFAULT_TIMEFRAME
    interval_min = DEFAULT_INTERVAL_MIN
    symbols: List[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--once":
            once = True
        elif arg == "--no-telegram":
            no_telegram = True
        elif arg == "--tf" and i + 1 < len(argv):
            timeframe = argv[i + 1]
            i += 1
        elif arg == "--interval" and i + 1 < len(argv):
            interval_min = int(argv[i + 1])
            i += 1
        else:
            symbols.append(arg)
        i += 1
    if not symbols:
        symbols = DEFAULT_SYMBOLS
    return symbols, timeframe, once, interval_min, no_telegram


STATE_FILE = "scanner_state.json"


def load_state() -> set:
    """
    Charge les setups deja notifies (dedup persistant entre runs CI).
    Retourne un set de cles 'symbol|timestamp|direction'.
    """
    import json
    from pathlib import Path
    p = Path(__file__).parent / STATE_FILE
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text())
        # garde max 200 cles recentes pour pas faire grossir indefinitement
        return set(data.get("notified", [])[-200:])
    except Exception:
        return set()


def save_state(notified: set) -> None:
    import json
    from pathlib import Path
    p = Path(__file__).parent / STATE_FILE
    try:
        p.write_text(json.dumps({"notified": sorted(notified)[-200:]}, indent=2))
    except Exception as e:
        print(f"  [state] erreur ecriture : {e}")


def main():
    symbols, timeframe, once, interval_min, no_telegram = parse_args(sys.argv)
    exchange = ccxt.binance({"enableRateLimit": True})

    notifier = None if no_telegram else TelegramNotifier(silent=True)
    telegram_state = "ON" if (notifier and notifier.enabled) else "OFF"

    print(f"\nScanner SMC confluent")
    print(f"  Symboles  : {symbols}")
    print(f"  Timeframe : {timeframe}")
    print(f"  Mode      : {'one-shot' if once else f'loop tous les {interval_min} min'}")
    print(f"  Telegram  : {telegram_state}")
    if not once:
        print(f"  Ctrl+C pour arreter")

    # Persiste le dedup entre runs (utile en CI : GitHub Actions toutes les 15 min)
    already_notified: set = load_state()
    if already_notified:
        print(f"  Etat charge : {len(already_notified)} setups deja notifies")

    while True:
        try:
            scan_once(exchange, symbols, timeframe, notifier, already_notified)
            save_state(already_notified)
        except Exception as e:
            print(f"  Erreur globale : {e}")
        if once:
            break
        time.sleep(interval_min * 60)

    print("\nFin du scanner.\n")


if __name__ == "__main__":
    main()
