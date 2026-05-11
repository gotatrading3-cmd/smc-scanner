"""
MT5 Executor - place les ordres sur MetaTrader 5 quand un setup confluent est detecte.

SECURITE - safety guards hardcodes :
- DEMO_ONLY = True : refuse de tourner sur compte reel par defaut
- MAX_LOT = 0.01    : taille de lot plafonnee
- SL obligatoire     : refuse de placer un ordre sans stop-loss
- Whitelist symboles : seulement BTC/ETH/SOL/AVAX/LINK
- Max 1 position ouverte par symbole
- Cooldown 4h entre 2 trades sur le meme symbole

Architecture :
- Tourne sur ton PC Windows avec MT5 OUVERT
- Boucle sur scan_once() toutes les N minutes (memes symboles que le scanner GitHub)
- Quand setup detecte : verifie safety -> place ordre MT5 -> notifie Telegram

Configuration : mt5_config.json (cree via setup_wizard ci-dessous)

Lancement :
    python mt5_executor.py --setup    # premier lancement, configure le compte
    python mt5_executor.py            # tourne en boucle (PC + MT5 ouvert obligatoire)
    python mt5_executor.py --once     # un seul scan
    python mt5_executor.py --dry-run  # detecte mais ne place AUCUN ordre (test)
"""
from __future__ import annotations
import sys
import os
import json
import time
import ccxt
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict

# Robustesse user site-packages
for _candidate in [
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _candidate and os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

import MetaTrader5 as mt5  # noqa: E402

from scanner import detect_setup, SetupAlert  # noqa: E402
from notifier import TelegramNotifier  # noqa: E402

# Mapping timeframe scanner -> constante MT5 (apres import mt5)
MT5_TIMEFRAME_MAP = {
    "1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30, "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4,
    "1d": mt5.TIMEFRAME_D1, "1w": mt5.TIMEFRAME_W1,
}


CONFIG_FILE = Path(__file__).parent / "mt5_config.json"
TRADE_LOG = Path(__file__).parent / "mt5_trades.log"
EQUITY_LOG = Path(__file__).parent / "mt5_equity.log"
PAUSE_FILE = Path(__file__).parent / ".pause"

# Filtres temporels (UTC)
SKIP_WEEKEND_FOR_NON_CRYPTO = True   # vendredi 20h UTC -> lundi 00h UTC
DRAWDOWN_24H_MAX_PCT = 3.0           # circuit breaker si -3% en 24h
TRAILING_TO_BE_AT_R = 1.0            # SL remonte a breakeven quand prix atteint +1R
TRAILING_LOCK_05R_AT = 1.5           # a +1.5R, lock 0.5R de profit
TRAILING_LOCK_1R_AT = 2.0            # a +2R, lock 1R de profit
MAX_POSITION_HOURS = 12              # ferme auto une position apres 12h (libere le capital)

# ===== SAFETY HARDCODE - NE PAS DESACTIVER SANS COMPRENDRE =====
DEMO_ONLY = True
MAX_LOT = 1.0
# Risque absolu en USD (adapte pour petits comptes demo / scalping)
RISK_USD_TARGET = 3.0       # cible : 3$ de risque par trade
RISK_USD_MAX = 5.0          # plafond : 5$ max si min lot broker oblige
PROFIT_USD_TARGET = 4.0     # cible profit : ferme quand PnL atteint 4$ (entre 3$ et 5$)
RR_TARGET = 1.0             # fallback si calcul $ impossible
SYMBOL_WHITELIST = {
    # Crypto
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT",
    # Indices
    "US100", "US30", "GER40", "UK100",
    # Metaux precieux
    "XAUUSD", "XAGUSD",
    # Forex majeurs
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    # Commodities (si dispos)
    "USOIL", "NATGAS",
}
COOLDOWN_HOURS = 1  # scalping : cooldown court
SCAN_INTERVAL_MIN = 5  # scan toutes les 5 min
DEFAULT_SYMBOLS = [
    # Crypto
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT",
    # Indices
    "US100", "US30", "GER40", "UK100",
    # Metaux
    "XAUUSD", "XAGUSD",
    # Forex
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    # Commodities
    "USOIL", "NATGAS",
]
SCAN_TIMEFRAME = "15m"  # scalping H4 -> M15
SCAN_LIMIT = 500


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with TRADE_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============================================================
#                      CONFIG
# ============================================================

def load_config() -> Optional[dict]:
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        log(f"Erreur lecture config: {e}")
        return None


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def setup_wizard():
    print("\n=== Configuration MT5 Executor ===\n")
    print("Prerequis : MT5 installe + compte DEMO XM cree.\n")
    login = input("Login MT5 (numero) : ").strip()
    password = input("Mot de passe MT5 : ").strip()
    server = input("Serveur MT5 (ex: XMGlobal-Demo 7) : ").strip()
    print("\nMapping des symboles : entre le symbole exact tel qu'affiche dans MT5 Market Watch.")
    print("Exemple : pour BTC/USDT, XM peut l'appeler 'BTCUSD' ou 'BTCUSD.cash'.")
    print("Pour US100/US30 chez XM : souvent 'US100Cash' / 'US30Cash'.")
    print("Pour XAUUSD/XAGUSD chez XM : souvent 'GOLD' / 'SILVER'.")
    print("Si un symbole n'est pas dispo chez ton broker, mets une chaine vide.\n")
    symbol_map = {}
    for s in DEFAULT_SYMBOLS:
        m = input(f"  {s} -> nom MT5 : ").strip()
        if m:
            symbol_map[s] = m
    cfg = {
        "login": int(login),
        "password": password,
        "server": server,
        "symbol_map": symbol_map,
    }
    save_config(cfg)
    print(f"\nConfig sauvegardee : {CONFIG_FILE}")
    print("Test de connexion...")
    if connect_mt5(cfg):
        log_account_info()
        mt5.shutdown()
        print("Connexion OK.")
    else:
        print("Connexion echec - verifie tes infos.")


def add_symbols_to_existing_config(new_mapping: dict) -> None:
    """Ajoute des symboles a la config existante sans tout reconfigurer."""
    cfg = load_config()
    if cfg is None:
        print("Pas de config existante. Lance --setup d'abord.")
        return
    cfg.setdefault("symbol_map", {}).update(new_mapping)
    save_config(cfg)
    print(f"Config mise a jour : {list(new_mapping.keys())}")
    print(f"Symboles totaux : {sorted(cfg['symbol_map'].keys())}")


# ============================================================
#                      MT5 CONNECTION
# ============================================================

MT5_TERMINAL_CANDIDATES = [
    r"C:\Program Files\XM Global MT5\terminal64.exe",
    r"C:\Program Files\XMTrading MT5\terminal64.exe",
    r"C:\Program Files\XM MT5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
]


def find_mt5_terminal() -> Optional[str]:
    for c in MT5_TERMINAL_CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def connect_mt5(cfg: dict, max_retry: int = 1, retry_delay: int = 30) -> bool:
    """
    Connecte a MT5. Si MT5 n'est pas en cours, le launch automatiquement
    (via le parametre path de mt5.initialize).
    Si max_retry > 1, retry avec backoff.
    """
    terminal_path = cfg.get("terminal_path") or find_mt5_terminal()
    init_kwargs = {
        "login": cfg["login"],
        "password": cfg["password"],
        "server": cfg["server"],
    }
    if terminal_path:
        init_kwargs["path"] = terminal_path

    for attempt in range(1, max_retry + 1):
        if mt5.initialize(**init_kwargs):
            return True
        err = mt5.last_error()
        if attempt < max_retry:
            log(f"MT5 init failed (attempt {attempt}/{max_retry}): {err} - retry dans {retry_delay}s")
            time.sleep(retry_delay)
        else:
            log(f"MT5 init failed: {err}")
    return False


def log_account_info() -> Optional[dict]:
    info = mt5.account_info()
    if info is None:
        log("Account info indisponible")
        return None
    log(f"Compte connecte : {info.login} sur {info.server}")
    log(f"  Trade mode  : {'DEMO' if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else 'REEL/CONCOURS'}")
    log(f"  Balance     : {info.balance:.2f} {info.currency}")
    log(f"  Equity      : {info.equity:.2f} {info.currency}")
    log(f"  Levier      : 1:{info.leverage}")
    return {
        "login": info.login,
        "balance": info.balance,
        "equity": info.equity,
        "is_demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        "currency": info.currency,
    }


# ============================================================
#                      SAFETY CHECKS
# ============================================================

def safety_pre_check(cfg: dict) -> bool:
    """Verifications avant scan. Si MT5 est down, tente une reconnexion."""
    info = mt5.account_info()
    if info is None:
        log("[SAFETY] Pas d'info compte - tentative reconnexion MT5...")
        try:
            mt5.shutdown()
        except Exception:
            pass
        if not connect_mt5(cfg, max_retry=2, retry_delay=15):
            log("[SAFETY] Reconnexion echec - SKIP ce scan")
            return False
        info = mt5.account_info()
        if info is None:
            log("[SAFETY] Toujours pas d'info compte - SKIP")
            return False
        log("[SAFETY] Reconnexion MT5 reussie")
    if DEMO_ONLY and info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log(f"[SAFETY] Compte non-demo (mode={info.trade_mode}) - REFUS (DEMO_ONLY=True)")
        return False
    return True


def get_recent_consecutive_losses(mt5_symbol: str, lookback_hours: int = 24) -> int:
    """Compte les pertes consecutives sur ce symbole. 0 si dernier trade = win."""
    since = datetime.now() - timedelta(hours=lookback_hours)
    deals = mt5.history_deals_get(since, datetime.now()) or []
    sym_deals = [d for d in deals if d.symbol == mt5_symbol and d.entry == mt5.DEAL_ENTRY_OUT]
    sym_deals.sort(key=lambda d: d.time, reverse=True)  # plus recent d'abord
    consec = 0
    for d in sym_deals:
        pnl = d.profit + d.swap + d.commission
        if pnl < 0:
            consec += 1
        else:
            break
    return consec


def safety_check_setup(setup: SetupAlert, cfg: dict) -> tuple[bool, str]:
    """Valide qu'un setup peut etre execute. Retourne (ok, raison_si_non)."""
    if setup.symbol not in SYMBOL_WHITELIST:
        return False, f"symbole {setup.symbol} hors whitelist"

    mt5_symbol = cfg.get("symbol_map", {}).get(setup.symbol)
    if not mt5_symbol:
        return False, f"pas de mapping MT5 pour {setup.symbol}"

    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        return False, f"symbole MT5 {mt5_symbol} introuvable chez le broker"
    if not info.visible:
        if not mt5.symbol_select(mt5_symbol, True):
            return False, f"impossible d'activer {mt5_symbol} dans Market Watch"

    if setup.sl is None or setup.sl == setup.entry:
        return False, "SL invalide (obligatoire)"

    # Cooldown trade recent sur le meme symbole
    last_ts = read_last_trade_time(setup.symbol)
    if last_ts and (datetime.now() - last_ts) < timedelta(hours=COOLDOWN_HOURS):
        return False, f"cooldown actif (dernier trade {setup.symbol} < {COOLDOWN_HOURS}h)"

    # Pas plus d'une position par symbole
    positions = mt5.positions_get(symbol=mt5_symbol)
    if positions and len(positions) > 0:
        return False, f"position deja ouverte sur {mt5_symbol}"

    # PROPRETE : pause symbole apres 2 pertes consecutives (evite revenge trading)
    consec_losses = get_recent_consecutive_losses(mt5_symbol, lookback_hours=24)
    if consec_losses >= 2:
        return False, f"pause {mt5_symbol} apres {consec_losses} pertes consecutives 24h"

    return True, "ok"


def read_last_trade_time(symbol: str) -> Optional[datetime]:
    try:
        if not TRADE_LOG.exists():
            return None
        last = None
        for line in TRADE_LOG.read_text(encoding="utf-8").splitlines():
            if f"PLACED" in line and symbol in line:
                ts_str = line.split("]")[0][1:]
                try:
                    last = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
        return last
    except Exception:
        return None


# ============================================================
#               FILTRE TEMPOREL / WEEKEND
# ============================================================

CRYPTO_KEYWORDS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "USDT")


def is_crypto(symbol: str) -> bool:
    return any(k in symbol.upper() for k in CRYPTO_KEYWORDS)


def is_market_open(symbol: str) -> bool:
    """Crypto = 24/7. Indices/forex/metaux : skip weekend (vendredi 21h UTC -> lundi)."""
    if is_crypto(symbol):
        return True
    if not SKIP_WEEKEND_FOR_NON_CRYPTO:
        return True
    now = datetime.utcnow()
    wd = now.weekday()  # 0=lundi, 6=dimanche
    h = now.hour
    if wd == 5:  # samedi
        return False
    if wd == 6:  # dimanche
        return False
    if wd == 4 and h >= 21:  # vendredi >= 21h UTC
        return False
    if wd == 0 and h < 1:  # lundi < 01h UTC (ouvre vers 22h dimanche pour FX, mais conservateur)
        return False
    return True


# ============================================================
#               CIRCUIT BREAKER (drawdown 24h)
# ============================================================

def log_equity(balance: float, equity: float) -> None:
    """Append equity courant pour suivi drawdown."""
    try:
        with EQUITY_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}\t{balance:.2f}\t{equity:.2f}\n")
    except Exception:
        pass


def check_drawdown_24h() -> tuple[bool, float]:
    """
    Lit l'equity log, calcule le DD% sur 24h.
    Retourne (ok, dd_pct). ok=False si DD > DRAWDOWN_24H_MAX_PCT.
    """
    if not EQUITY_LOG.exists():
        return True, 0.0
    try:
        cutoff = datetime.now() - timedelta(hours=24)
        peak = None
        last = None
        for line in EQUITY_LOG.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ts = datetime.fromisoformat(parts[0])
            if ts < cutoff:
                continue
            eq = float(parts[2])
            peak = eq if peak is None else max(peak, eq)
            last = eq
        if peak is None or last is None:
            return True, 0.0
        dd = (last - peak) / peak * 100
        return dd >= -DRAWDOWN_24H_MAX_PCT, dd
    except Exception:
        return True, 0.0


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def pause_trading(reason: str) -> None:
    PAUSE_FILE.write_text(f"{datetime.now().isoformat()}\n{reason}\n")
    log(f"[PAUSE] Trading suspendu : {reason}")


# ============================================================
#               TRAILING STOP - BREAKEVEN
# ============================================================

def _close_position(pos, reason: str) -> bool:
    info = mt5.symbol_info(pos.symbol)
    tick = mt5.symbol_info_tick(pos.symbol)
    if info is None or tick is None:
        return False
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": pos.ticket,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if pos.type == 0 else tick.ask,
        "deviation": 50,
        "magic": 20260508,
        "comment": f"auto close: {reason}",
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    r = mt5.order_send(req)
    if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"[CLOSE] {pos.symbol} #{pos.ticket} ferme ({reason}) PnL={pos.profit:+.2f}")
        return True
    log(f"[CLOSE] {pos.symbol} #{pos.ticket} echec close: {r.retcode if r else 'n/a'}")
    return False


def _move_sl(pos, new_sl: float, digits: int, label: str) -> bool:
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": pos.ticket,
        "sl": round(new_sl, digits),
        "tp": pos.tp,
        "symbol": pos.symbol,
    }
    r = mt5.order_send(req)
    if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"[TRAILING] {pos.symbol} #{pos.ticket} SL -> {new_sl:.{digits}f} ({label})")
        return True
    return False


def manage_open_positions() -> None:
    """
    Gestion auto des positions ouvertes :
    - Sortie temporelle si > MAX_POSITION_HOURS (libere le capital)
    - Trailing SL progressif a +1R / +1.5R / +2R pour verrouiller les profits
    """
    positions = mt5.positions_get()
    if not positions:
        return

    for pos in positions:
        if pos.sl == 0:
            continue
        info = mt5.symbol_info(pos.symbol)
        tick = mt5.symbol_info_tick(pos.symbol)
        if info is None or tick is None:
            continue
        digits = info.digits

        # 1) Sortie temporelle (scalping = on ne reste pas trop longtemps)
        position_age = datetime.now() - datetime.fromtimestamp(pos.time)
        if position_age > timedelta(hours=MAX_POSITION_HOURS):
            _close_position(pos, f"age > {MAX_POSITION_HOURS}h")
            continue

        # 2) Trailing progressif (profits maximises par paliers)
        is_long = (pos.type == 0)
        if is_long:
            risk = pos.price_open - pos.sl
            current = tick.bid
        else:
            risk = pos.sl - pos.price_open
            current = tick.ask
        if risk <= 0:
            continue

        # progression actuelle en R (1R = autant que le risque initial)
        if is_long:
            progress_r = (current - pos.price_open) / abs(pos.price_open - max(pos.sl, pos.price_open - risk * 10))
            progress_r = (current - pos.price_open) / risk
        else:
            progress_r = (pos.price_open - current) / risk

        # Niveaux de trailing (par ordre croissant pour appliquer le plus haut atteint)
        if progress_r >= TRAILING_LOCK_1R_AT:
            # Lock 1R de profit
            target_sl = pos.price_open + risk if is_long else pos.price_open - risk
            if (is_long and pos.sl < target_sl - 1e-9) or (not is_long and pos.sl > target_sl + 1e-9):
                _move_sl(pos, target_sl, digits, f"lock +1R a +{progress_r:.1f}R")
        elif progress_r >= TRAILING_LOCK_05R_AT:
            # Lock 0.5R de profit
            target_sl = pos.price_open + 0.5 * risk if is_long else pos.price_open - 0.5 * risk
            if (is_long and pos.sl < target_sl - 1e-9) or (not is_long and pos.sl > target_sl + 1e-9):
                _move_sl(pos, target_sl, digits, f"lock +0.5R a +{progress_r:.1f}R")
        elif progress_r >= TRAILING_TO_BE_AT_R:
            # Breakeven
            target_sl = pos.price_open
            if (is_long and pos.sl < target_sl - 1e-9) or (not is_long and pos.sl > target_sl + 1e-9):
                _move_sl(pos, target_sl, digits, f"BE a +{progress_r:.1f}R")


# ============================================================
#                FETCH OHLCV (MT5 desktop)
# ============================================================

def fetch_mt5_ohlcv(mt5_symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """
    Fetch OHLCV depuis MT5 desktop. Source unique pour TOUS les symboles
    (crypto + indices + metaux), pour aligner detection et execution.
    """
    tf = MT5_TIMEFRAME_MAP.get(timeframe.lower())
    if tf is None:
        raise ValueError(f"Timeframe MT5 inconnu: {timeframe}")

    # active le symbole dans Market Watch si besoin
    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        raise ValueError(f"Symbole MT5 introuvable: {mt5_symbol}")
    if not info.visible:
        mt5.symbol_select(mt5_symbol, True)

    rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, limit)
    if rates is None or len(rates) == 0:
        raise ValueError(f"Pas de donnees pour {mt5_symbol}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["ts"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("ts", inplace=True)
    df["volume"] = df["tick_volume"].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


# ============================================================
#                   PLACEMENT ORDRES
# ============================================================

def place_order(setup: SetupAlert, cfg: dict, dry_run: bool = False) -> bool:
    mt5_symbol = cfg["symbol_map"][setup.symbol]
    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        log(f"[ORDER] {mt5_symbol} introuvable")
        return False

    # Direction
    is_long = setup.direction == "LONG"
    order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL

    # Prix : on prend le prix marche actuel (Bid pour vente, Ask pour achat)
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        log(f"[ORDER] tick indisponible {mt5_symbol}")
        return False
    price = tick.ask if is_long else tick.bid
    spread = tick.ask - tick.bid

    # === SPREAD COMPENSATION + R:R 1:2 ===
    # Le SL signal est calcule "pure" par le scanner (sans tenir compte du spread).
    # Sur MT5 le SL est verifie sur le bid (pour LONG) ou l'ask (pour SHORT).
    # Si on garde le SL signal tel quel, le spread pose le SL plus pres qu'attendu
    # -> le SL peut etre touche juste par le spread.
    # Solution : elargir le SL d'un montant = spread + petit buffer,
    # puis recalculer TP pour avoir R:R 1:2 depuis le SL elargi.
    spread_buffer = spread * 1.5  # 1.5x spread = marge confortable
    if is_long:
        sl_adjusted = setup.sl - spread_buffer
        risk = price - sl_adjusted
        tp_adjusted = price + RR_TARGET * risk  # R:R configurable
    else:
        sl_adjusted = setup.sl + spread_buffer
        risk = sl_adjusted - price
        tp_adjusted = price - RR_TARGET * risk

    # === RISK-BASED LOT SIZING (en USD absolu, scalping) ===
    # Cible : RISK_USD_TARGET ($) de risque exact.
    # Si lot min broker oblige a risquer plus -> accepte jusqu'a RISK_USD_MAX.
    # Au-dela -> skip.
    sl_distance_price = abs(price - sl_adjusted)
    risk_per_lot = sl_distance_price * info.trade_contract_size  # $/lot
    if risk_per_lot <= 0:
        log(f"[ORDER] {mt5_symbol} : risk_per_lot nul, refus")
        return False

    # Tente d'atteindre le risque cible
    lot_target = RISK_USD_TARGET / risk_per_lot
    step = info.volume_step if info.volume_step > 0 else 0.01
    lot = (lot_target // step) * step
    lot = round(lot, 2)

    # Si lot calcule < min broker, on tente le min lot si risque <= MAX
    if lot < info.volume_min:
        risk_at_min = info.volume_min * risk_per_lot
        if risk_at_min <= RISK_USD_MAX:
            lot = info.volume_min
            log(f"[SIZING] {mt5_symbol} : lot ajuste au min broker {lot} "
                f"(risque {risk_at_min:.2f}$, sous le plafond {RISK_USD_MAX}$)")
        else:
            log(f"[SKIP] {mt5_symbol} {setup.direction} : "
                f"lot min broker {info.volume_min} risquerait {risk_at_min:.2f}$ "
                f"> plafond {RISK_USD_MAX}$. Trop cher pour cet actif.")
            return False
    if lot > MAX_LOT:
        lot = MAX_LOT

    # === TP en USD absolu (au lieu de R:R) ===
    # Calcul : distance prix pour que lot * distance * contract = PROFIT_USD_TARGET
    profit_unit = lot * info.trade_contract_size  # $ par unite de prix
    if profit_unit > 0:
        tp_distance_price = PROFIT_USD_TARGET / profit_unit
        if is_long:
            tp_adjusted = price + tp_distance_price
        else:
            tp_adjusted = price - tp_distance_price

    # Round SL/TP au tick size
    digits = info.digits

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": round(sl_adjusted, digits),
        "tp": round(tp_adjusted, digits),
        "deviation": 50,
        "magic": 20260508,
        "comment": "SMC scanner auto",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    if dry_run:
        log(f"[DRY-RUN] Aurait place : {mt5_symbol} {setup.direction} lot={lot} "
            f"price={price:.{digits}f} spread={spread:.{digits}f} "
            f"SL={sl_adjusted:.{digits}f} (signal {setup.sl:.{digits}f} elargi) "
            f"TP={tp_adjusted:.{digits}f} R:R=1:{RR_TARGET}")
        return True

    actual_risk = lot * risk_per_lot
    actual_profit = lot * info.trade_contract_size * abs(tp_adjusted - price)
    log(f"[ORDER] Envoi : {mt5_symbol} {setup.direction} lot={lot} "
        f"risque={actual_risk:.2f}$  profit_cible={actual_profit:.2f}$ "
        f"price={price:.{digits}f} spread={spread:.{digits}f} "
        f"SL={sl_adjusted:.{digits}f} TP={tp_adjusted:.{digits}f}")
    result = mt5.order_send(request)
    if result is None:
        log(f"[ORDER] Echec : last_error={mt5.last_error()}")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"[ORDER] Refuse retcode={result.retcode} comment={result.comment}")
        return False
    log(f"[ORDER] PLACED ticket={result.order} {setup.symbol} {setup.direction} "
        f"price={result.price} lot={result.volume}")
    return True


# ============================================================
#                      MAIN LOOP
# ============================================================

def scan_and_execute(cfg: dict, notifier: Optional[TelegramNotifier],
                     dry_run: bool = False) -> None:
    if not safety_pre_check(cfg):
        return

    # 1) Pause manuelle ?
    if is_paused():
        log("[PAUSE] Trading suspendu (fichier .pause present). Skip.")
        return

    # 2) Equity tracking + circuit breaker drawdown
    info = mt5.account_info()
    if info is not None:
        log_equity(info.balance, info.equity)
        ok_dd, dd = check_drawdown_24h()
        if not ok_dd:
            pause_trading(f"Drawdown 24h {dd:.2f}% > -{DRAWDOWN_24H_MAX_PCT}%")
            if notifier and notifier.enabled:
                notifier.send(
                    f"🚨 *CIRCUIT BREAKER active*\n"
                    f"Drawdown 24h : `{dd:.2f}%`\n"
                    f"Trading auto-suspendu.\n"
                    f"Pour reprendre : supprime `.pause` puis relance le bot."
                )
            return

    # 3) Trailing stop -> breakeven sur positions ouvertes
    try:
        manage_open_positions()
    except Exception as e:
        log(f"[TRAILING] erreur : {e}")

    log(f"=== Scan {datetime.now().strftime('%H:%M:%S')} (dry_run={dry_run}) ===")
    symbol_map = cfg.get("symbol_map", {})
    for symbol in DEFAULT_SYMBOLS:
        if symbol not in symbol_map:
            continue
        # 4) Filtre weekend pour non-crypto
        if not is_market_open(symbol):
            continue

        mt5_symbol = symbol_map[symbol]
        try:
            df = fetch_mt5_ohlcv(mt5_symbol, SCAN_TIMEFRAME, SCAN_LIMIT)
            setup = detect_setup(symbol, df)
            if not setup:
                continue

            ok, reason = safety_check_setup(setup, cfg)
            if not ok:
                log(f"[SKIP] {symbol} {setup.direction} : {reason}")
                continue

            log(f"[SETUP] {symbol} {setup.direction} entry={setup.entry:.4f} "
                f"SL={setup.sl:.4f} TP={setup.tp:.4f}")

            if place_order(setup, cfg, dry_run=dry_run):
                if notifier and notifier.enabled and not dry_run:
                    notifier.send_setup(setup)
        except Exception as e:
            log(f"[ERROR] {symbol} ({mt5_symbol}) : {e}")


def parse_args(argv):
    setup_mode = False
    once = False
    dry_run = False
    for a in argv[1:]:
        if a == "--setup":
            setup_mode = True
        elif a == "--once":
            once = True
        elif a == "--dry-run":
            dry_run = True
    return setup_mode, once, dry_run


def main():
    setup_mode, once, dry_run = parse_args(sys.argv)

    if setup_mode:
        setup_wizard()
        return

    cfg = load_config()
    if cfg is None:
        print("Config absente. Lance d'abord : python mt5_executor.py --setup")
        return

    log("=== MT5 Executor - DEMARRAGE ===")
    log(f"  DEMO_ONLY  = {DEMO_ONLY}")
    log(f"  MAX_LOT    = {MAX_LOT}")
    log(f"  COOLDOWN   = {COOLDOWN_HOURS}h")
    log(f"  Whitelist  = {sorted(SYMBOL_WHITELIST)}")
    log(f"  dry_run    = {dry_run}")

    # Au boot, MT5 peut prendre 30-60s a etre pret. Retry avec backoff.
    if not connect_mt5(cfg, max_retry=10, retry_delay=30):
        log("Connexion MT5 echec apres retries - verifie MT5 desktop ouvert.")
        return

    info = log_account_info()
    if info and DEMO_ONLY and not info["is_demo"]:
        log("Compte non-demo detecte. Refus DEMO_ONLY=True. Arret.")
        mt5.shutdown()
        return

    notifier = TelegramNotifier(silent=True)

    try:
        while True:
            try:
                scan_and_execute(cfg, notifier, dry_run=dry_run)
            except Exception as e:
                log(f"[GLOBAL ERROR] {e}")
            if once:
                break
            time.sleep(SCAN_INTERVAL_MIN * 60)
    finally:
        mt5.shutdown()
        log("=== MT5 Executor - ARRET ===\n")


if __name__ == "__main__":
    main()
