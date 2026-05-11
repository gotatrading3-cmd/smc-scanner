"""
Resume quotidien et hebdo des trades MT5 envoye sur Telegram.

Lance toutes les 24h (par exemple via tache planifiee Windows a 22:00 UTC).
    python daily_summary.py            # resume du jour
    python daily_summary.py --weekly   # resume des 7 derniers jours
    python daily_summary.py --month    # resume des 30 derniers jours
"""
from __future__ import annotations
import sys
import os
import json
import io
from pathlib import Path
from datetime import datetime, timedelta

# Force stdout en UTF-8 pour eviter UnicodeEncodeError sur Windows cp1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# user site-packages
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


def fetch_history(start: datetime, end: datetime):
    """Recupere l'historique des deals fermes entre deux dates."""
    deals = mt5.history_deals_get(start, end)
    if deals is None:
        return []
    return list(deals)


def compute_stats(deals) -> dict:
    """
    Aggrege les deals en stats. On prend les deals de type 'out' (cloture)
    qui ont un profit non-nul.
    """
    closed = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    if not closed:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "pnl": 0.0, "best": 0.0, "worst": 0.0,
            "by_symbol": {},
        }

    pnls = [d.profit + d.swap + d.commission for d in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    by_symbol: dict[str, dict] = {}
    for d in closed:
        s = d.symbol
        if s not in by_symbol:
            by_symbol[s] = {"trades": 0, "pnl": 0.0, "wins": 0}
        by_symbol[s]["trades"] += 1
        pnl = d.profit + d.swap + d.commission
        by_symbol[s]["pnl"] += pnl
        if pnl > 0:
            by_symbol[s]["wins"] += 1

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closed) * 100) if closed else 0.0,
        "pnl": sum(pnls),
        "best": max(pnls) if pnls else 0.0,
        "worst": min(pnls) if pnls else 0.0,
        "by_symbol": by_symbol,
    }


def format_summary(period_label: str, stats: dict, account_info) -> str:
    if stats["trades"] == 0:
        return (
            f"📊 *Bilan {period_label}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Aucun trade cloture sur cette periode.\n\n"
            f"💼 Compte : `{account_info.equity:.2f} {account_info.currency}`"
        )

    pnl_emoji = "🟢" if stats["pnl"] >= 0 else "🔴"
    pnl_sign = "+" if stats["pnl"] >= 0 else ""

    lines = [
        f"📊 *Bilan {period_label}*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📈 Trades   : `{stats['trades']}`",
        f"✅ Wins     : `{stats['wins']}`",
        f"❌ Losses   : `{stats['losses']}`",
        f"🎯 Win rate : `{stats['win_rate']:.1f}%`",
        f"",
        f"{pnl_emoji} PnL total : `{pnl_sign}{stats['pnl']:.2f} {account_info.currency}`",
        f"⭐ Meilleur  : `+{stats['best']:.2f}`",
        f"💢 Pire      : `{stats['worst']:.2f}`",
        f"",
        f"💼 Compte    : `{account_info.equity:.2f} {account_info.currency}`",
    ]

    if stats["by_symbol"]:
        lines.append("")
        lines.append("*Par symbole :*")
        ranked = sorted(stats["by_symbol"].items(), key=lambda x: -x[1]["pnl"])
        for sym, st in ranked[:8]:
            sign = "+" if st["pnl"] >= 0 else ""
            lines.append(f"  `{sym:<10s}` {st['trades']}T  {st['wins']}W  `{sign}{st['pnl']:.2f}`")

    return "\n".join(lines)


def run(period: str = "day"):
    cfg = json.loads(CONFIG_FILE.read_text())
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        print("MT5 init failed")
        return

    info = mt5.account_info()

    end = datetime.now()
    if period == "day":
        start = end - timedelta(hours=24)
        label = "du jour (24h)"
    elif period == "week":
        start = end - timedelta(days=7)
        label = "de la semaine (7j)"
    elif period == "month":
        start = end - timedelta(days=30)
        label = "du mois (30j)"
    else:
        start = end - timedelta(hours=24)
        label = "du jour"

    deals = fetch_history(start, end)
    stats = compute_stats(deals)
    msg = format_summary(label, stats, info)

    print(msg.replace("*", "").replace("`", ""))

    n = TelegramNotifier(silent=True)
    if n.enabled:
        n.send(msg)
        print("\n[Telegram] envoye")

    mt5.shutdown()


def main():
    period = "day"
    for a in sys.argv[1:]:
        if a == "--weekly":
            period = "week"
        elif a == "--month":
            period = "month"
    run(period)


if __name__ == "__main__":
    main()
