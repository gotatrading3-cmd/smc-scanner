"""
Dashboard web local - serveur HTTP minimal sur localhost:8080.
Affiche : positions ouvertes, equity, stats 7j/30j, log trades.

Aucune dependance externe (stdlib uniquement).

Lancement :
  python dashboard.py
  -> ouvre http://localhost:8080 dans ton navigateur
"""
from __future__ import annotations
import sys
import os
import json
import html
from pathlib import Path
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

for _c in [
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import MetaTrader5 as mt5  # noqa: E402

CONFIG_FILE = Path(__file__).parent / "mt5_config.json"
TRADE_LOG = Path(__file__).parent / "mt5_trades.log"
PAUSE_FILE = Path(__file__).parent / ".pause"
PORT = 8080


def fetch_data():
    cfg = json.loads(CONFIG_FILE.read_text())
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        return None
    try:
        info = mt5.account_info()
        positions = mt5.positions_get() or []
        now = datetime.now()
        deals_7d = mt5.history_deals_get(now - timedelta(days=7), now) or []
        deals_30d = mt5.history_deals_get(now - timedelta(days=30), now) or []

        def stats(deals):
            closed = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not closed:
                return {"trades": 0, "wr": 0, "pnl": 0}
            pnls = [d.profit + d.swap + d.commission for d in closed]
            wins = sum(1 for p in pnls if p > 0)
            return {"trades": len(closed), "wr": wins / len(closed) * 100, "pnl": sum(pnls)}

        return {
            "account": {
                "login": info.login,
                "balance": info.balance,
                "equity": info.equity,
                "currency": info.currency,
                "leverage": info.leverage,
                "demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
            },
            "positions": [
                {
                    "ticket": p.ticket, "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume, "open": p.price_open,
                    "sl": p.sl, "tp": p.tp, "profit": p.profit,
                    "comment": p.comment,
                } for p in positions
            ],
            "stats_7d": stats(deals_7d),
            "stats_30d": stats(deals_30d),
            "symbols": cfg.get("symbol_map", {}),
            "paused": PAUSE_FILE.exists(),
        }
    finally:
        mt5.shutdown()


def render_html(data: dict) -> str:
    if data is None:
        return "<html><body><h1>Connexion MT5 echec</h1></body></html>"

    a = data["account"]
    s7 = data["stats_7d"]
    s30 = data["stats_30d"]
    pos_rows = ""
    for p in data["positions"]:
        cls = "buy" if p["type"] == "BUY" else "sell"
        pnl_cls = "pos" if p["profit"] >= 0 else "neg"
        pos_rows += (
            f"<tr><td>{p['ticket']}</td><td>{html.escape(p['symbol'])}</td>"
            f"<td class='{cls}'>{p['type']}</td><td>{p['volume']}</td>"
            f"<td>{p['open']}</td><td>{p['sl']}</td><td>{p['tp']}</td>"
            f"<td class='{pnl_cls}'>{p['profit']:+.2f}</td></tr>"
        )
    if not pos_rows:
        pos_rows = "<tr><td colspan='8' style='text-align:center;color:#666'>Aucune position ouverte</td></tr>"

    sym_rows = ""
    for k, v in sorted(data["symbols"].items()):
        sym_rows += f"<tr><td><code>{html.escape(k)}</code></td><td><code>{html.escape(v)}</code></td></tr>"

    pause_banner = ""
    if data["paused"]:
        pause_banner = '<div class="banner pause">⏸ TRADING AUTO SUSPENDU</div>'

    pnl7_cls = "pos" if s7["pnl"] >= 0 else "neg"
    pnl30_cls = "pos" if s30["pnl"] >= 0 else "neg"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>SMC Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #1a1a1a; color: #eee; }}
h1, h2 {{ color: #4a9eff; }}
.cards {{ display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; }}
.card {{ background: #2a2a2a; border-radius: 8px; padding: 15px; flex: 1; min-width: 180px; }}
.card .label {{ color: #999; font-size: 12px; text-transform: uppercase; }}
.card .value {{ font-size: 24px; font-weight: bold; margin-top: 5px; }}
.pos {{ color: #4ade80; }}
.neg {{ color: #f87171; }}
.buy {{ color: #4ade80; font-weight: bold; }}
.sell {{ color: #f87171; font-weight: bold; }}
table {{ width: 100%; border-collapse: collapse; background: #2a2a2a; border-radius: 8px; overflow: hidden; }}
th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #3a3a3a; }}
th {{ background: #333; color: #4a9eff; }}
.banner {{ padding: 15px; border-radius: 8px; margin-bottom: 20px; text-align: center; font-weight: bold; }}
.banner.pause {{ background: #f59e0b; color: #000; }}
.demo-tag {{ background: #4ade80; color: #000; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
.real-tag {{ background: #f87171; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
.refresh {{ position: fixed; bottom: 20px; right: 20px; color: #666; font-size: 12px; }}
</style>
</head>
<body>

<h1>SMC Trading Dashboard <span class="{'demo-tag' if a['demo'] else 'real-tag'}">{'DEMO' if a['demo'] else 'REEL'}</span></h1>

{pause_banner}

<div class="cards">
  <div class="card">
    <div class="label">Compte</div>
    <div class="value">{a['login']}</div>
  </div>
  <div class="card">
    <div class="label">Balance</div>
    <div class="value">{a['balance']:.2f} {a['currency']}</div>
  </div>
  <div class="card">
    <div class="label">Equity</div>
    <div class="value">{a['equity']:.2f} {a['currency']}</div>
  </div>
  <div class="card">
    <div class="label">PnL flottant</div>
    <div class="value {'pos' if (a['equity']-a['balance'])>=0 else 'neg'}">
      {(a['equity']-a['balance']):+.2f}
    </div>
  </div>
  <div class="card">
    <div class="label">Levier</div>
    <div class="value">1:{a['leverage']}</div>
  </div>
</div>

<h2>Statistiques</h2>
<div class="cards">
  <div class="card">
    <div class="label">7 derniers jours</div>
    <div class="value">{s7['trades']} trades</div>
    <div>WR: {s7['wr']:.0f}% &nbsp; PnL: <span class="{pnl7_cls}">{s7['pnl']:+.2f}</span></div>
  </div>
  <div class="card">
    <div class="label">30 derniers jours</div>
    <div class="value">{s30['trades']} trades</div>
    <div>WR: {s30['wr']:.0f}% &nbsp; PnL: <span class="{pnl30_cls}">{s30['pnl']:+.2f}</span></div>
  </div>
</div>

<h2>Positions ouvertes</h2>
<table>
  <tr><th>Ticket</th><th>Symbole</th><th>Type</th><th>Volume</th><th>Open</th><th>SL</th><th>TP</th><th>PnL</th></tr>
  {pos_rows}
</table>

<h2>Symboles surveilles ({len(data['symbols'])})</h2>
<table>
  <tr><th>Scanner</th><th>MT5</th></tr>
  {sym_rows}
</table>

<div class="refresh">Auto-refresh 30s</div>

</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        try:
            data = fetch_data()
            html_text = render_html(data)
            body = html_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = f"<html><body><h1>Erreur</h1><pre>{html.escape(str(e))}</pre></body></html>"
            body = err.encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard : http://localhost:{PORT}")
    print("Ctrl+C pour arreter")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret")


if __name__ == "__main__":
    main()
