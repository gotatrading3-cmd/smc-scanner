"""
GOTA TRADING - Dashboard web local.

Interface complete sur http://localhost:8080
- Compte, equity, PnL, positions
- Stats 7j / 30j
- Trades recents
- Symboles surveilles
- Boutons Pause / Resume

Stdlib uniquement.

Lancement :
  python dashboard.py
  -> ouvre http://localhost:8080
"""
from __future__ import annotations
import sys
import os
import json
import html
from pathlib import Path
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

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
EQUITY_LOG = Path(__file__).parent / "mt5_equity.log"
PORT = 8080


def fetch_data():
    cfg = json.loads(CONFIG_FILE.read_text())
    if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
        return None
    try:
        info = mt5.account_info()
        ti = mt5.terminal_info()
        positions = mt5.positions_get() or []
        now = datetime.now()

        deals_today = mt5.history_deals_get(now - timedelta(hours=24), now) or []
        deals_7d = mt5.history_deals_get(now - timedelta(days=7), now) or []
        deals_30d = mt5.history_deals_get(now - timedelta(days=30), now) or []

        def stats(deals):
            closed = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not closed:
                return {"trades": 0, "wins": 0, "losses": 0, "wr": 0, "pnl": 0}
            pnls = [d.profit + d.swap + d.commission for d in closed]
            wins = sum(1 for p in pnls if p > 0)
            return {
                "trades": len(closed),
                "wins": wins,
                "losses": len(closed) - wins,
                "wr": wins / len(closed) * 100,
                "pnl": sum(pnls),
            }

        recent = sorted(
            [d for d in deals_7d if d.entry == mt5.DEAL_ENTRY_OUT],
            key=lambda d: d.time, reverse=True,
        )[:15]

        # Equity history (graph)
        eq_history = []
        if EQUITY_LOG.exists():
            try:
                for line in EQUITY_LOG.read_text(encoding="utf-8").splitlines()[-100:]:
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        eq_history.append({"ts": parts[0], "balance": float(parts[1]), "equity": float(parts[2])})
            except Exception:
                pass

        return {
            "account": {
                "login": info.login,
                "balance": info.balance,
                "equity": info.equity,
                "currency": info.currency,
                "leverage": info.leverage,
                "demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
                "autotrade": ti.trade_allowed,
                "server": info.server,
            },
            "positions": [
                {
                    "ticket": p.ticket, "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume, "open": p.price_open,
                    "sl": p.sl, "tp": p.tp, "profit": p.profit,
                    "comment": p.comment,
                    "time": datetime.fromtimestamp(p.time).strftime("%H:%M"),
                } for p in positions
            ],
            "stats_today": stats(deals_today),
            "stats_7d": stats(deals_7d),
            "stats_30d": stats(deals_30d),
            "recent_trades": [
                {
                    "symbol": d.symbol,
                    "type": "BUY" if d.type == 0 else "SELL",
                    "volume": d.volume,
                    "price": d.price,
                    "profit": d.profit + d.swap + d.commission,
                    "time": datetime.fromtimestamp(d.time).strftime("%d/%m %H:%M"),
                } for d in recent
            ],
            "symbols": cfg.get("symbol_map", {}),
            "paused": PAUSE_FILE.exists(),
            "equity_history": eq_history,
        }
    finally:
        mt5.shutdown()


def make_sparkline(points: list, width: int = 200, height: int = 50) -> str:
    """SVG sparkline a partir d'une liste de valeurs."""
    if len(points) < 2:
        return ""
    mn, mx = min(points), max(points)
    if mx == mn:
        mx = mn + 1
    coords = []
    for i, v in enumerate(points):
        x = i / (len(points) - 1) * width
        y = height - (v - mn) / (mx - mn) * height
        coords.append(f"{x:.1f},{y:.1f}")
    path = "M " + " L ".join(coords)
    last_y = height - (points[-1] - mn) / (mx - mn) * height
    color = "#4ade80" if points[-1] >= points[0] else "#f87171"
    return f'''
<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <path d="{path}" stroke="{color}" stroke-width="2" fill="none"/>
  <circle cx="{(len(points)-1)/(len(points)-1)*width:.1f}" cy="{last_y:.1f}" r="3" fill="{color}"/>
</svg>'''


def render_html(data: dict) -> str:
    if data is None:
        return "<html><body><h1>Connexion MT5 echec</h1></body></html>"

    a = data["account"]
    st = data["stats_today"]
    s7 = data["stats_7d"]
    s30 = data["stats_30d"]
    pos_rows = ""
    for p in data["positions"]:
        cls = "buy" if p["type"] == "BUY" else "sell"
        pnl_cls = "pos" if p["profit"] >= 0 else "neg"
        pos_rows += (
            f"<tr><td>#{p['ticket']}</td><td><b>{html.escape(p['symbol'])}</b></td>"
            f"<td><span class='tag {cls}'>{p['type']}</span></td><td>{p['volume']}</td>"
            f"<td>{p['open']}</td><td>{p['sl']}</td><td>{p['tp']}</td>"
            f"<td class='{pnl_cls}'><b>{p['profit']:+.2f}</b></td><td>{p['time']}</td></tr>"
        )
    if not pos_rows:
        pos_rows = "<tr><td colspan='9' class='empty'>Aucune position ouverte</td></tr>"

    recent_rows = ""
    for t in data["recent_trades"][:10]:
        cls = "buy" if t["type"] == "BUY" else "sell"
        pnl_cls = "pos" if t["profit"] >= 0 else "neg"
        recent_rows += (
            f"<tr><td>{t['time']}</td><td><b>{html.escape(t['symbol'])}</b></td>"
            f"<td><span class='tag {cls}'>{t['type']}</span></td><td>{t['volume']}</td>"
            f"<td>{t['price']}</td><td class='{pnl_cls}'><b>{t['profit']:+.2f}</b></td></tr>"
        )
    if not recent_rows:
        recent_rows = "<tr><td colspan='6' class='empty'>Aucun trade recent</td></tr>"

    sym_chips = ""
    for k, v in sorted(data["symbols"].items()):
        sym_chips += f"<span class='chip'><b>{html.escape(k)}</b> <code>{html.escape(v)}</code></span>"

    pause_banner = ""
    if data["paused"]:
        pause_banner = '<div class="banner pause">⏸ TRADING SUSPENDU - <a href="/resume" class="btn-inline">REPRENDRE</a></div>'

    autotrade_state = "🟢 ON" if a["autotrade"] else "🔴 OFF"
    pnl_today_cls = "pos" if st["pnl"] >= 0 else "neg"
    pnl_today_sign = "+" if st["pnl"] >= 0 else ""

    pnl7_cls = "pos" if s7["pnl"] >= 0 else "neg"
    pnl30_cls = "pos" if s30["pnl"] >= 0 else "neg"

    floating = a["equity"] - a["balance"]
    floating_cls = "pos" if floating >= 0 else "neg"

    eq_sparkline = ""
    if len(data["equity_history"]) >= 2:
        equities = [pt["equity"] for pt in data["equity_history"]]
        eq_sparkline = make_sparkline(equities, width=180, height=40)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>GOTA TRADING - Dashboard</title>
<meta http-equiv="refresh" content="20">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  background: linear-gradient(135deg, #0a0e27 0%, #1a1f3a 100%);
  color: #e8eaf6;
  padding: 20px;
  min-height: 100vh;
}}

.header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 25px;
  padding-bottom: 20px;
  border-bottom: 2px solid #2a3055;
}}
.brand {{
  display: flex;
  align-items: center;
  gap: 12px;
}}
.brand .logo {{
  width: 48px; height: 48px;
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  font-size: 24px;
  color: #0a0e27;
}}
.brand h1 {{ font-size: 24px; font-weight: 800; letter-spacing: 1px; }}
.brand .subtitle {{ font-size: 12px; color: #94a3b8; }}
.header-right {{ display: flex; gap: 10px; align-items: center; }}
.demo-tag {{ background: #4ade80; color: #062611; padding: 5px 12px; border-radius: 6px; font-weight: 700; font-size: 12px; }}
.real-tag {{ background: #ef4444; color: white; padding: 5px 12px; border-radius: 6px; font-weight: 700; font-size: 12px; }}

.banner {{ padding: 15px; border-radius: 8px; margin-bottom: 20px; text-align: center; font-weight: 700; }}
.banner.pause {{ background: linear-gradient(90deg, #f59e0b, #d97706); color: #1f1408; }}

.btn-inline {{
  background: #0a0e27;
  color: #fbbf24;
  padding: 6px 14px;
  border-radius: 6px;
  text-decoration: none;
  margin-left: 10px;
  font-weight: 700;
}}

.cards {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 15px;
  margin-bottom: 25px;
}}
.card {{
  background: rgba(42, 48, 85, 0.5);
  backdrop-filter: blur(10px);
  border: 1px solid #2a3055;
  border-radius: 12px;
  padding: 18px;
  transition: transform 0.2s;
}}
.card:hover {{ transform: translateY(-2px); border-color: #fbbf24; }}
.card .label {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }}
.card .value {{ font-size: 26px; font-weight: 800; margin-top: 8px; }}
.card .sub {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}

.pos {{ color: #4ade80; }}
.neg {{ color: #f87171; }}
.buy {{ background: rgba(74, 222, 128, 0.2); color: #4ade80; }}
.sell {{ background: rgba(248, 113, 113, 0.2); color: #f87171; }}

.tag {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}

h2 {{
  color: #fbbf24;
  font-size: 18px;
  margin: 25px 0 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
h2::before {{ content: ""; width: 4px; height: 18px; background: #fbbf24; border-radius: 2px; }}

table {{
  width: 100%;
  border-collapse: collapse;
  background: rgba(42, 48, 85, 0.3);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 20px;
}}
th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #2a3055; font-size: 14px; }}
th {{ background: #1a1f3a; color: #fbbf24; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
.empty {{ text-align: center; color: #64748b; font-style: italic; padding: 25px; }}

.chip {{
  display: inline-block;
  background: rgba(251, 191, 36, 0.1);
  border: 1px solid rgba(251, 191, 36, 0.3);
  padding: 6px 12px;
  border-radius: 20px;
  font-size: 12px;
  margin: 3px;
}}
.chip code {{ background: rgba(255,255,255,0.05); padding: 1px 6px; border-radius: 3px; color: #94a3b8; font-size: 11px; }}

.actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }}
.btn {{
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
  color: #0a0e27;
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  font-weight: 700;
  cursor: pointer;
  text-decoration: none;
  font-size: 13px;
  transition: opacity 0.2s;
}}
.btn:hover {{ opacity: 0.9; }}
.btn.red {{ background: linear-gradient(135deg, #ef4444, #dc2626); color: white; }}
.btn.green {{ background: linear-gradient(135deg, #4ade80, #22c55e); color: #062611; }}

.refresh {{ position: fixed; bottom: 20px; right: 20px; color: #64748b; font-size: 11px; padding: 6px 12px; background: rgba(42, 48, 85, 0.7); border-radius: 6px; }}

.sparkline-card {{ display: flex; justify-content: space-between; align-items: center; }}
</style>
</head>
<body>

<div class="header">
  <div class="brand">
    <div class="logo">G</div>
    <div>
      <h1>GOTA TRADING</h1>
      <div class="subtitle">SMC Scanner & Auto-Executor</div>
    </div>
  </div>
  <div class="header-right">
    <span class="{'demo-tag' if a['demo'] else 'real-tag'}">{'COMPTE DEMO' if a['demo'] else 'COMPTE REEL'}</span>
    <span style="color:#94a3b8;font-size:12px;">Auto-Trade: {autotrade_state}</span>
  </div>
</div>

{pause_banner}

<div class="actions">
  {'<a href="/resume" class="btn green">▶️ Reprendre</a>' if data['paused'] else '<a href="/pause" class="btn red">⏸ Pause</a>'}
  <a href="/" class="btn">🔄 Rafraichir</a>
</div>

<h2>💼 Compte</h2>
<div class="cards">
  <div class="card">
    <div class="label">Login</div>
    <div class="value" style="font-size:20px;">{a['login']}</div>
    <div class="sub">{html.escape(a['server'])}</div>
  </div>
  <div class="card">
    <div class="label">Balance</div>
    <div class="value">{a['balance']:.2f}</div>
    <div class="sub">{a['currency']}</div>
  </div>
  <div class="card">
    <div class="label">Equity</div>
    <div class="value">{a['equity']:.2f}</div>
    <div class="sub">{a['currency']}</div>
  </div>
  <div class="card">
    <div class="label">PnL flottant</div>
    <div class="value {floating_cls}">{floating:+.2f}</div>
    <div class="sub">{a['currency']}</div>
  </div>
  <div class="card">
    <div class="label">Levier</div>
    <div class="value">1:{a['leverage']}</div>
  </div>
  <div class="card sparkline-card">
    <div>
      <div class="label">Equity 100 derniers points</div>
      <div class="sub">Tendance recente</div>
    </div>
    {eq_sparkline}
  </div>
</div>

<h2>📊 Performance</h2>
<div class="cards">
  <div class="card">
    <div class="label">Aujourd'hui (24h)</div>
    <div class="value {pnl_today_cls}">{pnl_today_sign}{st['pnl']:.2f}</div>
    <div class="sub">{st['trades']} trades - WR {st['wr']:.0f}% ({st['wins']}W/{st['losses']}L)</div>
  </div>
  <div class="card">
    <div class="label">7 derniers jours</div>
    <div class="value {pnl7_cls}">{'+' if s7['pnl']>=0 else ''}{s7['pnl']:.2f}</div>
    <div class="sub">{s7['trades']} trades - WR {s7['wr']:.0f}%</div>
  </div>
  <div class="card">
    <div class="label">30 derniers jours</div>
    <div class="value {pnl30_cls}">{'+' if s30['pnl']>=0 else ''}{s30['pnl']:.2f}</div>
    <div class="sub">{s30['trades']} trades - WR {s30['wr']:.0f}%</div>
  </div>
</div>

<h2>📌 Positions ouvertes ({len(data['positions'])})</h2>
<table>
  <tr><th>Ticket</th><th>Symbole</th><th>Type</th><th>Vol</th><th>Open</th><th>SL</th><th>TP</th><th>PnL</th><th>Ouverte</th></tr>
  {pos_rows}
</table>

<h2>📋 Trades recents (15 derniers)</h2>
<table>
  <tr><th>Date/heure</th><th>Symbole</th><th>Type</th><th>Vol</th><th>Prix</th><th>PnL</th></tr>
  {recent_rows}
</table>

<h2>🌍 Marches surveilles ({len(data['symbols'])})</h2>
<div>{sym_chips}</div>

<div class="refresh">Auto-refresh 20s</div>

</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/pause":
            PAUSE_FILE.write_text(f"{datetime.now().isoformat()}\nDepuis dashboard\n")
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if path == "/resume":
            if PAUSE_FILE.exists():
                PAUSE_FILE.unlink()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if path != "/":
            self.send_response(404)
            self.end_headers()
            return
        try:
            data = fetch_data()
            body = render_html(data).encode("utf-8")
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
        pass


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"GOTA TRADING Dashboard : http://localhost:{PORT}")
    print("Ctrl+C pour arreter")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret")


if __name__ == "__main__":
    main()
