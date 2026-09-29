"""
MT5 Bridge - miroir fidele sur un compte MT5 de demonstration des signaux DEJA publies par le robot
cloud (GOTA Confluence / signal_runner.py) : memes niveaux (entree/SL/TP1-2-3), meme gestion
(40% / 30% / 30%, stop remonte a breakeven des que TP1 est touche).

Ce script ne genere AUCUN signal lui-meme et n'invente rien : il lit l'etat public + chiffre deja
publie sur la branche "data" du depot (le MEME etat que lit deja signal_dashboard.py), et reproduit
sur MT5 exactement ce qui est deja montre dans le tableau de bord et deja envoye sur Telegram. Le
compte MT5 devient donc un miroir honnete de ce qui est deja annonce - jamais une decision separee,
jamais un chiffre invente.

SECURITE - garde-fous codes en dur (ne pas retirer sans en comprendre les consequences) :
- DEMO_ONLY = True : verifie a CHAQUE cycle que le compte connecte est un compte de demonstration ;
  refuse d'envoyer le moindre ordre sinon. Le tableau de bord affiche "Compte demo", jamais "Live",
  tant que ce compte est un compte demo - c'est un choix delibere, pas une limitation technique.
- Un stop-loss broker est TOUJOURS pose des l'ouverture (filet de securite meme si ce script
  plante ou si le PC s'eteint) ; le TP broker = TP3 (dernier palier, filet si le pont est hors ligne
  pendant qu'un palier est touche).
- Necessite le PC allume + le terminal MT5 ouvert + ce script lance : contrairement aux signaux
  Telegram (qui tournent 24/7 dans le cloud), l'execution MT5 ne peut PAS tourner "PC eteint" -
  MetaTrader5 ne peut piloter qu'un terminal installe localement, GitHub Actions ne peut pas le faire.

Lancement :
    python mt5_bridge.py            # tourne en boucle (PC + MT5 ouverts obligatoire)
    python mt5_bridge.py --once     # un seul cycle (verification / debug)
    python mt5_bridge.py --dry-run  # logge ce qu'il ferait, ne place et ne modifie AUCUN ordre reel
"""
from __future__ import annotations
import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

for _candidate in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars(r"%APPDATA%\Python\Python312\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _candidate and os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
try:
    import site
    site.addsitedir(r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages")
except Exception:
    pass

import requests  # noqa: E402
import MetaTrader5 as mt5  # noqa: E402

from signal_data import UNIVERSE  # noqa: E402  (mapping id public -> nom MT5, deja utilise ailleurs)

DIR = Path(__file__).parent
REPO = "gotatrading3-cmd/smc-scanner"
RAW = f"https://raw.githubusercontent.com/{REPO}/data"
KEY_FILE = DIR / "state_key.txt"
CONFIG_FILE = DIR / "mt5_config.json"
LEDGER_FILE = DIR / "mt5_bridge_ledger.json"
STATUS_FILE = DIR / "mt5_bridge_status.json"
LOG_FILE = DIR / "mt5_bridge.log"

# ===== SECURITE - NE PAS DESACTIVER SANS COMPRENDRE (voir docstring) =====
DEMO_ONLY = True
RISK_PCT = 0.01           # 1% du solde actuel du compte, risque par signal miroir
MAX_LOT_PER_TRADE = 5.0   # plafond de securite absolu (evite un lot absurde en cas de bug de calcul)
TP_SPLIT = (0.4, 0.3, 0.3)  # doit rester identique a signal_engine.PARAMS["tp_split"]
MAGIC = 20260929
POLL_SECONDS = 60


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ config / connexion
def load_config() -> dict | None:
    if not CONFIG_FILE.exists():
        log(f"[CONFIG] {CONFIG_FILE} introuvable - lance d'abord mt5_executor.py --setup")
        return None
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def connect_mt5(cfg: dict) -> bool:
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        log(f"[MT5] connexion echouee : {mt5.last_error()}")
        return False
    return True


def account_status() -> dict:
    """Etat honnete du compte connecte. is_demo=False + DEMO_ONLY=True => le pont refuse d'agir."""
    info = mt5.account_info()
    if info is None:
        return {"connected": False}
    return {
        "connected": True,
        "is_demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "profit": info.profit,
        "currency": info.currency,
        "margin_free": info.margin_free,
    }


# ------------------------------------------------------------------ etat cloud (lecture seule, meme source que le tableau de bord)
def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(KEY_FILE.read_text(encoding="utf-8").strip().encode())


def fetch_cloud_active() -> tuple[dict, str | None]:
    """Renvoie (dict des signaux actifs {id: {signal, sim}}, message d'erreur ou None)."""
    try:
        r = requests.get(f"{RAW}/signals_state.json", timeout=8)
        if r.status_code != 200:
            return {}, f"etat cloud injoignable (HTTP {r.status_code})"
        raw = r.json()
        enc = raw.get("private_enc")
        if not enc:
            return {}, None  # aucun signal actif publie
        if not KEY_FILE.exists():
            return {}, "cle locale (state_key.txt) introuvable"
        priv = json.loads(_fernet().decrypt(enc.encode()).decode())
        return priv.get("active", {}), None
    except Exception as e:
        return {}, f"erreur lecture etat cloud : {e}"


# ------------------------------------------------------------------ ledger local (quels signaux sont deja miroir sur MT5)
def load_ledger() -> dict:
    if LEDGER_FILE.exists():
        try:
            return json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_ledger(ledger: dict) -> None:
    LEDGER_FILE.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ ordres MT5
def _round_step(volume: float, step: float) -> float:
    if step <= 0:
        step = 0.01
    return round((volume // step) * step, 8)


def compute_lot(balance: float, entry: float, sl: float, info) -> float | None:
    risk_usd = balance * RISK_PCT
    sl_distance = abs(entry - sl)
    risk_per_lot = sl_distance * info.trade_contract_size
    if risk_per_lot <= 0:
        return None
    lot = _round_step(risk_usd / risk_per_lot, info.volume_step)
    if lot < info.volume_min:
        # si meme le lot minimum du broker coute plus de 3x le risque cible, on refuse (trop cher)
        if info.volume_min * risk_per_lot > risk_usd * 3:
            return None
        lot = info.volume_min
    return min(lot, MAX_LOT_PER_TRADE)


def open_mirror(sig_id: str, sig: dict, balance: float, dry_run: bool) -> dict | None:
    mt5_symbol = UNIVERSE.get(sig["display"], {}).get("mt5")
    if not mt5_symbol:
        log(f"[SKIP] {sig['display']} : pas de correspondance MT5 connue pour ce symbole")
        return None
    info = mt5.symbol_info(mt5_symbol)
    if info is None or not info.visible:
        mt5.symbol_select(mt5_symbol, True)
        info = mt5.symbol_info(mt5_symbol)
    if info is None:
        log(f"[SKIP] {mt5_symbol} introuvable chez ce broker")
        return None
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        log(f"[SKIP] {mt5_symbol} : tick indisponible")
        return None

    is_long = sig["direction"] == "LONG"
    lot = compute_lot(balance, sig["entry"], sig["sl"], info)
    if lot is None:
        log(f"[SKIP] {mt5_symbol} : lot minimum broker trop cher pour le risque cible ({RISK_PCT:.0%} du solde)")
        return None
    price = tick.ask if is_long else tick.bid
    digits = info.digits

    if dry_run:
        log(f"[DRY-RUN] ouvrirait {mt5_symbol} {sig['direction']} lot={lot} "
            f"SL={sig['sl']:.{digits}f} TP(final)={sig['tps'][2]:.{digits}f}")
        return {"ticket": None, "mt5_symbol": mt5_symbol, "direction": sig["direction"],
                "volume_total": lot, "legs_done": [False, False, False], "sl_moved_be": False,
                "opened_at": datetime.now(timezone.utc).isoformat(), "dry_run": True}

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(sig["sl"], digits),
        "tp": round(sig["tps"][2], digits),
        "deviation": 50,
        "magic": MAGIC,
        "comment": f"GOTA:{sig_id}"[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"[ORDER] echec miroir {mt5_symbol} {sig['direction']} : "
            f"{result.retcode if result else mt5.last_error()}")
        return None
    log(f"[ORDER] miroir place : {mt5_symbol} {sig['direction']} lot={lot} ticket={result.order} "
        f"(signal {sig_id})")
    return {"ticket": result.order, "mt5_symbol": mt5_symbol, "direction": sig["direction"],
            "volume_total": lot, "legs_done": [False, False, False], "sl_moved_be": False,
            "opened_at": datetime.now(timezone.utc).isoformat()}


def _get_position(ticket: int):
    if ticket is None:
        return None
    positions = mt5.positions_get(ticket=ticket)
    return positions[0] if positions else None


def partial_close(pos, volume: float, comment: str, dry_run: bool) -> bool:
    info = mt5.symbol_info(pos.symbol)
    tick = mt5.symbol_info_tick(pos.symbol)
    if info is None or tick is None:
        return False
    volume = min(_round_step(volume, info.volume_step) or info.volume_step, pos.volume)
    if volume <= 0:
        return False
    if dry_run:
        log(f"[DRY-RUN] fermerait {volume} lot sur {pos.symbol} #{pos.ticket} ({comment})")
        return True
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": pos.ticket,
        "symbol": pos.symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
        "deviation": 50,
        "magic": MAGIC,
        "comment": comment[:31],
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
    log(f"[{'CLOSE' if ok else 'CLOSE-ECHEC'}] {pos.symbol} #{pos.ticket} {volume} lot ({comment})")
    return ok


def move_sl_to_breakeven(pos, new_sl: float, dry_run: bool) -> bool:
    info = mt5.symbol_info(pos.symbol)
    digits = info.digits if info else 5
    if dry_run:
        log(f"[DRY-RUN] deplacerait SL de {pos.symbol} #{pos.ticket} -> {new_sl:.{digits}f} (breakeven)")
        return True
    request = {"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket,
               "sl": round(new_sl, digits), "tp": pos.tp, "symbol": pos.symbol}
    result = mt5.order_send(request)
    ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
    log(f"[{'BREAKEVEN' if ok else 'BREAKEVEN-ECHEC'}] {pos.symbol} #{pos.ticket} SL -> {new_sl:.{digits}f}")
    return ok


# ------------------------------------------------------------------ cycle principal
def run_cycle(dry_run: bool = False) -> dict:
    acct = account_status()
    if not acct.get("connected"):
        log("[CYCLE] compte MT5 injoignable - reconnexion au prochain cycle")
        return acct
    if DEMO_ONLY and not acct["is_demo"]:
        log("[SECURITE] Ce compte n'est PAS un compte demo - le pont refuse d'agir (DEMO_ONLY=True)")
        acct["refused_non_demo"] = True
        return acct

    active, err = fetch_cloud_active()
    if err:
        log(f"[CYCLE] {err}")

    ledger = load_ledger()

    for sig_id, rec in active.items():
        sig, sim = rec["signal"], rec["sim"]
        if sig_id not in ledger:
            entry = open_mirror(sig_id, sig, acct["balance"], dry_run)
            if entry:
                ledger[sig_id] = entry
            continue

        entry = ledger[sig_id]
        pos = _get_position(entry.get("ticket"))
        if pos is None and not entry.get("dry_run"):
            continue  # deja ferme cote MT5 (SL/TP broker atteint) - rien a faire de plus

        for k in range(3):
            if sim["hit"][k] and not entry["legs_done"][k]:
                is_last = k == 2 or sim["remaining"] <= 1e-6
                if pos is not None:
                    vol = pos.volume if is_last else entry["volume_total"] * TP_SPLIT[k]
                    partial_close(pos, vol, f"GOTA TP{k + 1}", dry_run)
                entry["legs_done"][k] = True
        if sim["sl_cur"] != sig["sl"] and not entry["sl_moved_be"]:
            if pos is not None:
                move_sl_to_breakeven(pos, sim["sl_cur"], dry_run)
            entry["sl_moved_be"] = True
        ledger[sig_id] = entry

    for sig_id in list(ledger):
        if sig_id in active:
            continue
        entry = ledger[sig_id]
        pos = _get_position(entry.get("ticket"))
        if pos is not None:
            partial_close(pos, pos.volume, "GOTA cloture", dry_run)
            log(f"[SYNC] signal {sig_id} cloture cote cloud -> position MT5 fermee")
        del ledger[sig_id]

    if not dry_run:
        save_ledger(ledger)  # en dry-run, rien n'est persiste : le ledger reste exactement comme avant ce test

    open_positions = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]
    status = {**acct, "bridge_ok": True, "dry_run": dry_run,
              "mirrored_active": len(active), "open_positions": [
                  {"symbol": p.symbol, "direction": "LONG" if p.type == mt5.ORDER_TYPE_BUY else "SHORT",
                   "volume": p.volume, "price_open": p.price_open, "sl": p.sl, "tp": p.tp, "profit": p.profit}
                  for p in open_positions],
              "updated_at": datetime.now(timezone.utc).isoformat()}
    STATUS_FILE.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_config()
    if not cfg:
        return
    log(f"=== demarrage mt5_bridge (dry_run={args.dry_run}) ===")
    if not connect_mt5(cfg):
        json.dumps({"connected": False, "updated_at": datetime.now(timezone.utc).isoformat()})
        STATUS_FILE.write_text(json.dumps({"connected": False,
                                            "updated_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        return
    try:
        while True:
            try:
                run_cycle(dry_run=args.dry_run)
            except Exception as e:
                log(f"[CYCLE] erreur inattendue (on continue) : {e}")
            if args.once:
                break
            time.sleep(POLL_SECONDS)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
