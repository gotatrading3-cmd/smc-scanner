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

# ============ PARAMETRES GRID ============
# BTC/USDT car ouvert 24/7 (GOLD ferme le weekend). Step adapte au prix BTC.
GRID_SYMBOL_KEY = "BTC/USDT"    # cle scanner -> mappe via symbol_map (BTCUSD chez XM)
GRID_DIRECTION = "BUY"          # BUY only (comme la video) ou "SELL"
GRID_STEP_USD = 50.0            # ecart de prix (en $) entre 2 niveaux - empilement rapide facon video
GRID_LOT = 0.01                 # lot FIXE par niveau (pas d'exponentiel = moins suicidaire)
BASKET_TP_USD = 3.0             # ferme tout le panier quand profit cumule >= 3$
SCAN_SECONDS = 10               # frequence de check

# ============ GARDE-FOUS HARDCODES ============
DEMO_ONLY = True                # JAMAIS de compte reel
MAX_POSITIONS = 15              # plafond strict de positions simultanees
MAX_TOTAL_LOT = 0.20            # lot cumule max (15 x 0.01 = 0.15, marge OK)
EQUITY_FLOOR_USD = 5.0          # si equity < 5$ -> ferme TOUT le panier (stop loss)
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


def open_grid_position(mt5_symbol: str, dry_run: bool) -> bool:
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
    lot = max(GRID_LOT, info.volume_min)

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


def close_basket(mt5_symbol: str, reason: str) -> float:
    """Ferme toutes les positions du grid. Retourne le PnL total realise."""
    positions = get_grid_positions(mt5_symbol)
    total = 0.0
    for p in positions:
        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None:
            continue
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": p.ticket,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if p.type == 0 else tick.ask,
            "deviation": 50,
            "magic": MAGIC,
            "comment": f"grid close: {reason}",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        total += p.profit
        mt5.order_send(req)
    if positions:
        log(f"[GRID] PANIER FERME ({reason}) - {len(positions)} positions - PnL {total:+.2f}$")
    return total


def grid_cycle(cfg: dict, dry_run: bool, notifier=None) -> bool:
    """Un cycle de gestion du grid. Retourne False si on doit arreter."""
    ai = mt5.account_info()
    if ai is None:
        log("[GRID] account_info indisponible")
        return True

    # GARDE-FOU 1 : demo only
    if DEMO_ONLY and ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log("[GRID] STOP - compte non-demo (DEMO_ONLY=True)")
        return False

    mt5_symbol = cfg.get("symbol_map", {}).get(GRID_SYMBOL_KEY)
    if not mt5_symbol:
        log(f"[GRID] STOP - {GRID_SYMBOL_KEY} pas dans symbol_map")
        return False

    positions = get_grid_positions(mt5_symbol)
    n = len(positions)
    basket_pnl = sum(p.profit for p in positions)

    # GARDE-FOU 2 : equity floor
    if ai.equity < EQUITY_FLOOR_USD and n > 0:
        log(f"[GRID] EQUITY FLOOR atteint ({ai.equity:.2f}$ < {EQUITY_FLOOR_USD}$)")
        realized = close_basket(mt5_symbol, "equity floor")
        if notifier:
            notifier.send(f"🛑 *GRID - Equity Floor*\nPanier ferme a {ai.equity:.2f}$\nPnL: {realized:+.2f}$\n\nC'est exactement le scenario 'ca explose'.")
        return False  # on arrete le grid

    log(f"[GRID] equity={ai.equity:.2f}$ positions={n}/{MAX_POSITIONS} basket_pnl={basket_pnl:+.2f}$")

    # TAKE PROFIT du panier
    if n > 0 and basket_pnl >= BASKET_TP_USD:
        realized = close_basket(mt5_symbol, f"basket TP {basket_pnl:.2f}$")
        if notifier:
            notifier.send(f"🟢 *GRID - Panier gagnant*\n{n} positions fermees\nPnL: {realized:+.2f}$")
        return True

    # OUVERTURE premiere position
    if n == 0:
        open_grid_position(mt5_symbol, dry_run)
        return True

    # AJOUT d'un niveau si le prix a recule de GRID_STEP
    if n < MAX_POSITIONS:
        total_lot = sum(p.volume for p in positions)
        if total_lot + GRID_LOT > MAX_TOTAL_LOT:
            log(f"[GRID] MAX_TOTAL_LOT atteint ({total_lot})")
            return True
        tick = mt5.symbol_info_tick(mt5_symbol)
        is_buy = GRID_DIRECTION == "BUY"
        if is_buy:
            lowest_entry = min(p.price_open for p in positions)
            if tick.ask <= lowest_entry - GRID_STEP_USD:
                log(f"[GRID] prix recule de {GRID_STEP_USD}$ -> +1 niveau")
                open_grid_position(mt5_symbol, dry_run)
        else:
            highest_entry = max(p.price_open for p in positions)
            if tick.bid >= highest_entry + GRID_STEP_USD:
                open_grid_position(mt5_symbol, dry_run)
    return True


def main():
    args = sys.argv[1:]
    if "--setup-info" in args:
        print(__doc__)
        print("\nParametres actuels :")
        print(f"  Symbole       : {GRID_SYMBOL_KEY} ({GRID_DIRECTION})")
        print(f"  Step grille   : {GRID_STEP_USD}$")
        print(f"  Lot par niveau: {GRID_LOT} (FIXE)")
        print(f"  Panier TP     : {BASKET_TP_USD}$")
        print(f"  Max positions : {MAX_POSITIONS}")
        print(f"  Equity floor  : {EQUITY_FLOOR_USD}$")
        return

    dry_run = "--dry-run" in args
    once = "--once" in args

    log("=== GOTA GRID BOT - DEMARRAGE ===")
    log(f"  DEMO_ONLY={DEMO_ONLY}  dry_run={dry_run}")
    log(f"  {GRID_SYMBOL_KEY} {GRID_DIRECTION} step={GRID_STEP_USD}$ lot={GRID_LOT}")
    log(f"  Panier TP={BASKET_TP_USD}$  max_pos={MAX_POSITIONS}  equity_floor={EQUITY_FLOOR_USD}$")

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
