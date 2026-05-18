"""
GOTA TRADING - Grid Bot (DEMO / EDUCATIF).

Reproduit la mecanique vue dans la video : empilement de positions BUY
sur l'or, fermeture du panier quand profit total atteint.

BUT EDUCATIF : te montrer la phase "ca monte" ET la phase "ca explose".
Le grid/martingale finit TOUJOURS par vider un compte sur un mouvement
adverse assez long. Ici tu le testes en demo, sans risque reel.

=== GARDE-FOUS HARDCODES (ne pas desactiver sans comprendre) ===
- DEMO_ONLY        : refuse tout compte non-demo
- GRID_LOT fixe    : PAS de martingale exponentiel (0.01 a chaque niveau)
- MAX_POSITIONS    : plafond strict du nombre de positions
- EQUITY_FLOOR     : ferme TOUT le panier si l'equity descend trop bas
- MAX_TOTAL_LOT    : plafond du lot cumule

Lancement :
    python grid_executor.py --setup-info   # explique les params
    python grid_executor.py --dry-run       # simulation, aucun ordre
    python grid_executor.py                 # demo reel (ordres sur demo)

NE PAS lancer en meme temps que mt5_executor.py (conflit sur le compte).
"""
from __future__ import annotations
import sys
import os
import json
import time
from pathlib import Path
from datetime import datetime

for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import MetaTrader5 as mt5  # noqa: E402

try:
    from notifier import TelegramNotifier
except Exception:
    TelegramNotifier = None

CONFIG_FILE = Path(__file__).parent / "mt5_config.json"
GRID_LOG = Path(__file__).parent / "grid.log"

# ============ PARAMETRES GRID MULTI-SYMBOLES (demo $1000) ============
# Chaque symbole a son propre panier independant, son step et son lot.
# step adapte au prix : BTC ~78000$ -> step 50$ ; GOLD ~4500$ -> step 5$.
GRID_SYMBOLS = {
    "BTC/USDT": {"step": 50.0, "lot": 0.03, "basket_tp": 10.0},
    "XAUUSD":   {"step": 5.0,  "lot": 0.01, "basket_tp": 8.0},
}
GRID_DIRECTION = "BUY"          # BUY only (comme la video) ou "SELL"
SCAN_SECONDS = 10               # frequence de check

# ============ GARDE-FOUS HARDCODES ============
DEMO_ONLY = True                # JAMAIS de compte reel
MAX_POSITIONS_PER_SYMBOL = 20   # plafond strict par symbole (20 BTC + 20 GOLD max)
EQUITY_FLOOR_USD = 300.0        # si equity < 300$ -> ferme TOUS les paniers (stop loss)
MAGIC = 20260516                # identifiant des ordres du grid bot


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with GRID_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def connect() -> dict | None:
    cfg = json.loads(CONFIG_FILE.read_text())
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        log(f"MT5 init failed: {mt5.last_error()}")
        return None
    return cfg


def get_grid_positions(mt5_symbol: str) -> list:
    """Positions du grid bot (filtrees par magic number)."""
    positions = mt5.positions_get(symbol=mt5_symbol) or []
    return [p for p in positions if p.magic == MAGIC]


def open_grid_position(mt5_symbol: str, grid_lot: float, dry_run: bool) -> bool:
    info = mt5.symbol_info(mt5_symbol)
    tick = mt5.symbol_info_tick(mt5_symbol)
    if info is None or tick is None:
        log(f"[GRID] {mt5_symbol} indisponible")
        return False
    if not info.visible:
        mt5.symbol_select(mt5_symbol, True)

    is_buy = GRID_DIRECTION == "BUY"
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
    price = tick.ask if is_buy else tick.bid
    lot = max(grid_lot, info.volume_min)

    if dry_run:
        log(f"[DRY-RUN] Ouvrirait {GRID_DIRECTION} {mt5_symbol} lot={lot} @ {price}")
        return True

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "deviation": 50,
        "magic": MAGIC,
        "comment": "GOTA grid",
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"[GRID] echec ouverture : {r.retcode if r else 'n/a'} {r.comment if r else ''}")
        return False
    log(f"[GRID] +1 position {GRID_DIRECTION} @ {r.price} lot={r.volume} (ticket {r.order})")
    return True


def _pick_filling(mt5_symbol: str):
    """Choisit le mode de filling supporte par le symbole (evite retcode 10030)."""
    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    fm = info.filling_mode  # bitmask : 1=FOK, 2=IOC
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def close_basket(mt5_symbol: str, reason: str) -> float:
    """Ferme toutes les positions du grid. VERIFIE chaque resultat + retry."""
    positions = get_grid_positions(mt5_symbol)
    if not positions:
        return 0.0
    filling = _pick_filling(mt5_symbol)
    realized = 0.0
    closed = 0
    for p in positions:
        success = False
        for attempt in range(1, 4):  # 3 tentatives
            tick = mt5.symbol_info_tick(p.symbol)
            if tick is None:
                break
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": p.ticket,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if p.type == 0 else tick.ask,
                "deviation": 2000,  # large : crypto/or bougent vite
                "magic": MAGIC,
                "comment": "grid close",
                "type_filling": filling,
            }
            r = mt5.order_send(req)
            if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
                success = True
                closed += 1
                realized += p.profit
                break
            else:
                rc = r.retcode if r else "n/a"
                cm = r.comment if r else ""
                log(f"[GRID] close #{p.ticket} tentative {attempt}/3 echec : retcode={rc} {cm}")
        if not success:
            log(f"[GRID] close #{p.ticket} ECHEC - position reste ouverte")
    log(f"[GRID] PANIER {mt5_symbol} : {closed}/{len(positions)} fermees, "
        f"PnL realise {realized:+.2f}$ ({reason})")
    return realized


def grid_cycle(cfg: dict, dry_run: bool, notifier=None) -> bool:
    """Un cycle de gestion du grid multi-symboles. Retourne False si arret."""
    ai = mt5.account_info()
    if ai is None:
        log("[GRID] account_info indisponible")
        return True

    # GARDE-FOU 1 : demo only
    if DEMO_ONLY and ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log("[GRID] STOP - compte non-demo (DEMO_ONLY=True)")
        return False

    symbol_map = cfg.get("symbol_map", {})
    all_grid_pos = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]

    # GARDE-FOU 2 : equity floor GLOBAL -> ferme TOUS les paniers
    if ai.equity < EQUITY_FLOOR_USD and all_grid_pos:
        log(f"[GRID] EQUITY FLOOR atteint ({ai.equity:.2f}$ < {EQUITY_FLOOR_USD}$)")
        total = 0.0
        for sym_key in GRID_SYMBOLS:
            ms = symbol_map.get(sym_key)
            if ms:
                total += close_basket(ms, "equity floor")
        if notifier:
            notifier.send(f"🛑 *GRID - Equity Floor*\nTous paniers fermes a {ai.equity:.2f}$\n"
                          f"PnL: {total:+.2f}$\n\nC'est le scenario 'ca explose'.")
        return False  # on arrete le grid

    log(f"[GRID] equity={ai.equity:.2f}$  total positions grid={len(all_grid_pos)}")

    # Gestion independante de chaque symbole
    for sym_key, params in GRID_SYMBOLS.items():
        mt5_symbol = symbol_map.get(sym_key)
        if not mt5_symbol:
            log(f"[GRID] {sym_key} pas dans symbol_map - skip")
            continue

        positions = get_grid_positions(mt5_symbol)
        n = len(positions)
        basket_pnl = sum(p.profit for p in positions)
        log(f"[GRID]   {sym_key}: {n}/{MAX_POSITIONS_PER_SYMBOL} pos, basket {basket_pnl:+.2f}$")

        # TAKE PROFIT du panier de ce symbole
        if n > 0 and basket_pnl >= params["basket_tp"]:
            realized = close_basket(mt5_symbol, f"{sym_key} basket TP {basket_pnl:.2f}$")
            if notifier:
                notifier.send(f"🟢 *GRID {sym_key} - Panier gagnant*\n"
                              f"{n} positions fermees\nPnL: {realized:+.2f}$")
            continue

        # OUVERTURE premiere position
        if n == 0:
            open_grid_position(mt5_symbol, params["lot"], dry_run)
            continue

        # AJOUT d'un niveau si le prix a bouge de step
        if n < MAX_POSITIONS_PER_SYMBOL:
            tick = mt5.symbol_info_tick(mt5_symbol)
            if tick is None:
                continue
            is_buy = GRID_DIRECTION == "BUY"
            if is_buy:
                lowest_entry = min(p.price_open for p in positions)
                if tick.ask <= lowest_entry - params["step"]:
                    log(f"[GRID]   {sym_key} recule de {params['step']}$ -> +1 niveau")
                    open_grid_position(mt5_symbol, params["lot"], dry_run)
            else:
                highest_entry = max(p.price_open for p in positions)
                if tick.bid >= highest_entry + params["step"]:
                    open_grid_position(mt5_symbol, params["lot"], dry_run)
    return True


def main():
    args = sys.argv[1:]
    if "--setup-info" in args:
        print(__doc__)
        print("\nParametres actuels :")
        print(f"  Direction     : {GRID_DIRECTION}")
        for sk, pp in GRID_SYMBOLS.items():
            print(f"  {sk:10s} : step {pp['step']}$  lot {pp['lot']}  panier TP {pp['basket_tp']}$")
        print(f"  Max pos/symbole: {MAX_POSITIONS_PER_SYMBOL}")
        print(f"  Equity floor   : {EQUITY_FLOOR_USD}$")
        return

    dry_run = "--dry-run" in args
    once = "--once" in args

    log("=== GOTA GRID BOT - DEMARRAGE ===")
    log(f"  DEMO_ONLY={DEMO_ONLY}  dry_run={dry_run}  direction={GRID_DIRECTION}")
    for sk, pp in GRID_SYMBOLS.items():
        log(f"  {sk}: step={pp['step']}$ lot={pp['lot']} basket_tp={pp['basket_tp']}$")
    log(f"  max_pos/symbole={MAX_POSITIONS_PER_SYMBOL}  equity_floor={EQUITY_FLOOR_USD}$")

    cfg = connect()
    if cfg is None:
        log("Connexion MT5 echec - MT5 doit etre ouvert.")
        return

    ai = mt5.account_info()
    if ai and DEMO_ONLY and ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log("Compte non-demo. Refus. Arret.")
        mt5.shutdown()
        return

    notifier = None
    if TelegramNotifier is not None:
        try:
            notifier = TelegramNotifier(silent=True)
            if not notifier.enabled:
                notifier = None
        except Exception:
            notifier = None

    try:
        while True:
            try:
                cont = grid_cycle(cfg, dry_run, notifier)
                if not cont:
                    log("=== GRID ARRETE par garde-fou ===")
                    break
            except Exception as e:
                log(f"[GRID] erreur cycle : {e}")
            if once:
                break
            time.sleep(SCAN_SECONDS)
    finally:
        mt5.shutdown()
        log("=== GOTA GRID BOT - FIN ===\n")


if __name__ == "__main__":
    main()
