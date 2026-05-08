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

from scanner import detect_setup, fetch, SetupAlert, DEFAULT_TIMEFRAME, LIMIT  # noqa: E402
from notifier import TelegramNotifier  # noqa: E402


CONFIG_FILE = Path(__file__).parent / "mt5_config.json"
TRADE_LOG = Path(__file__).parent / "mt5_trades.log"

# ===== SAFETY HARDCODE - NE PAS DESACTIVER SANS COMPRENDRE =====
DEMO_ONLY = True
MAX_LOT = 0.01
SYMBOL_WHITELIST = {"BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"}
COOLDOWN_HOURS = 4
SCAN_INTERVAL_MIN = 15
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]


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
    print("Exemple : pour BTC/USDT du scanner, le broker XM peut l'appeler 'BTCUSD' ou 'BTCUSD.cash'.")
    print("Si un symbole n'est pas dispo chez ton broker, mets une chaine vide (il sera ignore).\n")
    symbol_map = {}
    for s in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]:
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
        # essaye d'activer
        if not mt5.symbol_select(mt5_symbol, True):
            return False, f"impossible d'activer {mt5_symbol} dans Market Watch"

    if setup.sl is None or setup.sl == setup.entry:
        return False, "SL invalide (obligatoire)"

    # check cooldown
    last_ts = read_last_trade_time(setup.symbol)
    if last_ts and (datetime.now() - last_ts) < timedelta(hours=COOLDOWN_HOURS):
        return False, f"cooldown actif (dernier trade {setup.symbol} il y a < {COOLDOWN_HOURS}h)"

    # check positions ouvertes
    positions = mt5.positions_get(symbol=mt5_symbol)
    if positions and len(positions) > 0:
        return False, f"position deja ouverte sur {mt5_symbol}"

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

    # Lot fixe (safety)
    lot = MAX_LOT

    # Round SL/TP au tick size
    digits = info.digits

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": round(setup.sl, digits),
        "tp": round(setup.tp, digits),
        "deviation": 50,
        "magic": 20260508,
        "comment": "SMC scanner auto",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    if dry_run:
        log(f"[DRY-RUN] Aurait place : {mt5_symbol} {setup.direction} lot={lot} "
            f"price={price:.{digits}f} SL={setup.sl:.{digits}f} TP={setup.tp:.{digits}f}")
        return True

    log(f"[ORDER] Envoi : {mt5_symbol} {setup.direction} lot={lot} "
        f"price={price:.{digits}f} SL={setup.sl:.{digits}f} TP={setup.tp:.{digits}f}")
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

def scan_and_execute(cfg: dict, exchange, notifier: Optional[TelegramNotifier],
                     dry_run: bool = False) -> None:
    if not safety_pre_check(cfg):
        return

    log(f"=== Scan {datetime.now().strftime('%H:%M:%S')} (dry_run={dry_run}) ===")
    for symbol in DEFAULT_SYMBOLS:
        try:
            df = fetch(exchange, symbol, DEFAULT_TIMEFRAME, LIMIT)
            setup = detect_setup(symbol, df)
            if not setup:
                continue

            ok, reason = safety_check_setup(setup, cfg)
            if not ok:
                log(f"[SKIP] {symbol} {setup.direction} : {reason}")
                continue

            log(f"[SETUP] {symbol} {setup.direction} entry={setup.entry:.2f} "
                f"SL={setup.sl:.2f} TP={setup.tp:.2f}")

            if place_order(setup, cfg, dry_run=dry_run):
                if notifier and notifier.enabled and not dry_run:
                    notifier.send_setup(setup)
        except Exception as e:
            log(f"[ERROR] {symbol} : {e}")


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

    exchange = ccxt.binance({"enableRateLimit": True})
    notifier = TelegramNotifier(silent=True)

    try:
        while True:
            try:
                scan_and_execute(cfg, exchange, notifier, dry_run=dry_run)
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
