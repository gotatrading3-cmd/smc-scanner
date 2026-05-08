"""
Bot Telegram interactif - long-polling sur getUpdates.

Commandes :
  /status   - positions ouvertes + equity
  /stats    - performance 7j / 30j
  /pause    - suspend le trading (bot continue de scanner mais ne place rien)
  /resume   - relance le trading
  /log      - 5 derniers trades fermes
  /symbols  - liste des symboles configures
  /help     - liste des commandes

Lancement :
  python telegram_bot.py
  (ou ajouter au Startup Windows)
"""
from __future__ import annotations
import sys
import os
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta

for _c in [
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import MetaTrader5 as mt5  # noqa: E402

from notifier import TelegramNotifier  # noqa: E402

CONFIG_FILE = Path(__file__).parent / "mt5_config.json"
PAUSE_FILE = Path(__file__).parent / ".pause"
TRADE_LOG = Path(__file__).parent / "mt5_trades.log"

POLL_TIMEOUT = 30  # long-polling getUpdates


def get_updates(token: str, offset: int) -> list:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = urllib.parse.urlencode({"offset": offset, "timeout": POLL_TIMEOUT})
    try:
        with urllib.request.urlopen(f"{url}?{params}", timeout=POLL_TIMEOUT + 5) as r:
            data = json.loads(r.read())
            return data.get("result", []) if data.get("ok") else []
    except Exception as e:
        print(f"  [poll] erreur : {e}")
        return []


def cmd_help() -> str:
    return (
        "*Commandes disponibles :*\n"
        "/status  - positions ouvertes + equity\n"
        "/stats   - performance 7j et 30j\n"
        "/pause   - suspendre le trading auto\n"
        "/resume  - relancer le trading auto\n"
        "/log     - 5 derniers trades fermes\n"
        "/symbols - liste des symboles surveilles\n"
        "/help    - cette aide"
    )


def cmd_status(cfg: dict) -> str:
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        return "❌ Connexion MT5 echec"
    try:
        info = mt5.account_info()
        positions = mt5.positions_get()

        lines = [f"💼 *Compte* `{info.login}`",
                 f"Balance : `{info.balance:.2f} {info.currency}`",
                 f"Equity  : `{info.equity:.2f} {info.currency}`",
                 f"PnL flot.: `{(info.equity - info.balance):+.2f}`",
                 ""]

        if positions:
            lines.append(f"📌 *Positions ouvertes* ({len(positions)}) :")
            for p in positions:
                t = "🟢 BUY " if p.type == 0 else "🔴 SELL"
                lines.append(f"  {t} `{p.symbol:<10s}` {p.volume} @ `{p.price_open}` "
                             f"PnL `{p.profit:+.2f}`")
        else:
            lines.append("📌 Aucune position ouverte")

        if PAUSE_FILE.exists():
            lines.append("\n⏸ *Trading auto SUSPENDU* (`/resume` pour relancer)")

        return "\n".join(lines)
    finally:
        mt5.shutdown()


def cmd_stats(cfg: dict) -> str:
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        return "❌ Connexion MT5 echec"
    try:
        now = datetime.now()
        out = []
        for label, days in [("7 jours", 7), ("30 jours", 30)]:
            deals = mt5.history_deals_get(now - timedelta(days=days), now)
            if deals is None:
                deals = []
            closed = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not closed:
                out.append(f"*{label}* : aucun trade")
                continue
            pnls = [d.profit + d.swap + d.commission for d in closed]
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(closed) * 100
            tot = sum(pnls)
            sign = "+" if tot >= 0 else ""
            out.append(f"*{label}* : `{len(closed)}` trades  WR `{wr:.0f}%`  "
                       f"PnL `{sign}{tot:.2f}`")
        return "\n".join(out) if out else "Aucune donnee"
    finally:
        mt5.shutdown()


def cmd_pause() -> str:
    PAUSE_FILE.write_text(f"{datetime.now().isoformat()}\nManuelle via /pause Telegram\n")
    return "⏸ *Trading auto suspendu*\nLes scans continuent mais aucun ordre place.\nUtilise `/resume` pour relancer."


def cmd_resume() -> str:
    if PAUSE_FILE.exists():
        PAUSE_FILE.unlink()
        return "▶️ *Trading auto relance*"
    return "Le trading n'etait pas suspendu."


def cmd_log() -> str:
    if not TRADE_LOG.exists():
        return "Aucun trade dans le log."
    lines = TRADE_LOG.read_text(encoding="utf-8").splitlines()
    placed = [l for l in lines if "PLACED" in l][-5:]
    if not placed:
        return "Aucun trade place dans le log."
    out = ["*5 derniers trades places :*"]
    for l in placed:
        out.append(f"`{l[:80]}`")
    return "\n".join(out)


def cmd_symbols(cfg: dict) -> str:
    sm = cfg.get("symbol_map", {})
    if not sm:
        return "Aucun symbole configure."
    out = ["*Symboles surveilles :*"]
    for k, v in sorted(sm.items()):
        out.append(f"  `{k:<10s}` -> `{v}`")
    return "\n".join(out)


def handle_command(text: str, cfg: dict) -> str:
    text = text.strip().lower()
    if text.startswith("/help") or text == "/start":
        return cmd_help()
    if text.startswith("/status"):
        return cmd_status(cfg)
    if text.startswith("/stats"):
        return cmd_stats(cfg)
    if text.startswith("/pause"):
        return cmd_pause()
    if text.startswith("/resume"):
        return cmd_resume()
    if text.startswith("/log"):
        return cmd_log()
    if text.startswith("/symbols"):
        return cmd_symbols(cfg)
    return ""  # ignore les messages non-commande


def main():
    if not CONFIG_FILE.exists():
        print("mt5_config.json absent")
        return
    cfg = json.loads(CONFIG_FILE.read_text())

    notifier = TelegramNotifier(silent=True)
    if not notifier.enabled:
        print("Telegram non configure")
        return

    token = notifier.token
    chat_id = notifier.chat_id

    print(f"Bot Telegram demarre - chat {chat_id}")
    print("Commandes : /help /status /stats /pause /resume /log /symbols")
    print("Ctrl+C pour arreter\n")

    offset = 0
    while True:
        try:
            updates = get_updates(token, offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue
                if str(msg.get("chat", {}).get("id")) != str(chat_id):
                    continue  # ignore messages d'autres utilisateurs
                text = msg.get("text", "")
                if not text.startswith("/"):
                    continue
                print(f"[{datetime.now().strftime('%H:%M:%S')}] commande : {text}")
                response = handle_command(text, cfg)
                if response:
                    notifier.send(response)
        except KeyboardInterrupt:
            print("\nArret demande")
            break
        except Exception as e:
            print(f"  erreur main : {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
