"""
GOTA TRADING - Application desktop / Dashboard web.
Design pro SaaS, sidebar nav, charts SVG, actions cliquables.
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

DIR = Path(__file__).parent
CONFIG_FILE = DIR / "mt5_config.json"
TRADE_LOG = DIR / "mt5_trades.log"
PAUSE_FILE = DIR / ".pause"
EQUITY_LOG = DIR / "mt5_equity.log"
PORT = 8080
APP_VERSION = "1.0"


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
                return {"trades": 0, "wins": 0, "losses": 0, "wr": 0, "pnl": 0,
                        "best": 0, "worst": 0}
            pnls = [d.profit + d.swap + d.commission for d in closed]
            wins = sum(1 for p in pnls if p > 0)
            return {
                "trades": len(closed),
                "wins": wins,
                "losses": len(closed) - wins,
                "wr": wins / len(closed) * 100,
                "pnl": sum(pnls),
                "best": max(pnls),
                "worst": min(pnls),
            }

        recent = sorted(
            [d for d in deals_30d if d.entry == mt5.DEAL_ENTRY_OUT],
            key=lambda d: d.time, reverse=True,
        )[:30]

        # PnL par symbole
        by_sym = {}
        for d in [x for x in deals_30d if x.entry == mt5.DEAL_ENTRY_OUT]:
            pnl = d.profit + d.swap + d.commission
            by_sym.setdefault(d.symbol, {"trades": 0, "wins": 0, "pnl": 0.0})
            by_sym[d.symbol]["trades"] += 1
            by_sym[d.symbol]["pnl"] += pnl
            if pnl > 0:
                by_sym[d.symbol]["wins"] += 1

        eq_history = []
        if EQUITY_LOG.exists():
            try:
                for line in EQUITY_LOG.read_text(encoding="utf-8").splitlines()[-200:]:
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        eq_history.append({"ts": parts[0], "equity": float(parts[2])})
            except Exception:
                pass

        return {
            "account": {
                "login": info.login, "balance": info.balance, "equity": info.equity,
                "currency": info.currency, "leverage": info.leverage,
                "demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
                "autotrade": ti.trade_allowed, "server": info.server,
            },
            "positions": [
                {
                    "ticket": p.ticket, "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume, "open": p.price_open,
                    "sl": p.sl, "tp": p.tp, "profit": p.profit,
                    "time": datetime.fromtimestamp(p.time).strftime("%H:%M"),
                } for p in positions
            ],
            "stats_today": stats(deals_today),
            "stats_7d": stats(deals_7d),
            "stats_30d": stats(deals_30d),
            "by_symbol": by_sym,
            "recent_trades": [
                {
                    "symbol": d.symbol,
                    "type": "BUY" if d.type == 0 else "SELL",
                    "volume": d.volume, "price": d.price,
                    "profit": d.profit + d.swap + d.commission,
                    "time": datetime.fromtimestamp(d.time).strftime("%d/%m %H:%M"),
                } for d in recent
            ],
            "symbols": cfg.get("symbol_map", {}),
            "paused": PAUSE_FILE.exists(),
            "equity_history": eq_history,
            "settings": load_settings(),
        }
    finally:
        mt5.shutdown()


def load_settings() -> dict:
    """Lit les settings depuis mt5_executor (constants)."""
    try:
        import mt5_executor as ex
        return {
            "scan_interval_min": ex.SCAN_INTERVAL_MIN,
            "scan_timeframe": ex.SCAN_TIMEFRAME,
            "risk_usd_target": ex.RISK_USD_TARGET,
            "risk_usd_max": ex.RISK_USD_MAX,
            "profit_usd_target": ex.PROFIT_USD_TARGET,
            "early_close_usd": ex.EARLY_CLOSE_USD,
            "profit_usd_hard_max": ex.PROFIT_USD_HARD_MAX,
            "cooldown_hours": ex.COOLDOWN_HOURS,
            "max_position_hours": ex.MAX_POSITION_HOURS,
            "daily_loss_usd_max": ex.DAILY_LOSS_USD_MAX,
            "min_equity_usd": ex.MIN_EQUITY_USD,
            "demo_only": ex.DEMO_ONLY,
        }
    except Exception as e:
        return {"error": str(e)}


def read_logs(n: int = 30) -> list:
    if not TRADE_LOG.exists():
        return []
    try:
        lines = TRADE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:][::-1]
    except Exception:
        return []


def make_equity_chart(points: list, width: int = 720, height: int = 200) -> str:
    """Courbe d'equity SVG avec axes, grid, area fill."""
    if len(points) < 2:
        return '<div class="empty-chart">Pas encore assez de donnees</div>'

    vals = [p["equity"] for p in points]
    mn, mx = min(vals), max(vals)
    if mx == mn:
        mx = mn + 1
    pad = (mx - mn) * 0.1
    mn -= pad
    mx += pad

    pad_left = 50
    pad_bottom = 20
    chart_w = width - pad_left - 10
    chart_h = height - pad_bottom - 10

    coords = []
    for i, v in enumerate(vals):
        x = pad_left + i / (len(vals) - 1) * chart_w
        y = 10 + chart_h - (v - mn) / (mx - mn) * chart_h
        coords.append((x, y))

    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area_path = f"{path} L {coords[-1][0]:.1f},{10+chart_h} L {coords[0][0]:.1f},{10+chart_h} Z"

    last_v = vals[-1]
    first_v = vals[0]
    color = "#3fb950" if last_v >= first_v else "#f85149"
    fill_color = "rgba(63,185,80,0.15)" if last_v >= first_v else "rgba(248,81,73,0.15)"

    # Y-axis labels (5 ticks)
    y_labels = ""
    for i in range(5):
        v = mn + (mx - mn) * i / 4
        y = 10 + chart_h - i / 4 * chart_h
        y_labels += f'<text x="{pad_left - 5}" y="{y + 4}" fill="#8b949e" font-size="10" text-anchor="end">{v:.2f}</text>'
        y_labels += f'<line x1="{pad_left}" y1="{y}" x2="{width - 10}" y2="{y}" stroke="#30363d" stroke-width="0.5" stroke-dasharray="2,2"/>'

    return f'''
<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none" style="display:block">
  {y_labels}
  <path d="{area_path}" fill="{fill_color}"/>
  <path d="{path}" stroke="{color}" stroke-width="2" fill="none"/>
  <circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="4" fill="{color}"/>
  <text x="{coords[-1][0] - 5:.1f}" y="{coords[-1][1] - 8:.1f}" fill="{color}" font-size="11" font-weight="bold" text-anchor="end">{last_v:.2f}</text>
</svg>'''


def make_pnl_bars(by_sym: dict, width: int = 720) -> str:
    if not by_sym:
        return '<div class="empty-chart">Aucun trade ferme</div>'
    items = sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True)
    max_abs = max(abs(v["pnl"]) for _, v in items) or 1
    rows = ""
    for sym, s in items:
        pct = abs(s["pnl"]) / max_abs * 100
        color = "#3fb950" if s["pnl"] >= 0 else "#f85149"
        rows += f'''
        <div class="bar-row">
          <div class="bar-label">{html.escape(sym)}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width:{pct:.1f}%;background:{color};"></div>
          </div>
          <div class="bar-value" style="color:{color};">{s["pnl"]:+.2f}</div>
          <div class="bar-meta">{s["trades"]}T - WR {s["wins"]/s["trades"]*100:.0f}%</div>
        </div>'''
    return f'<div class="bars">{rows}</div>'


LOGO_SVG = '''
<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" class="logo">
  <defs>
    <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#fcd34d"/>
      <stop offset="50%" stop-color="#f59e0b"/>
      <stop offset="100%" stop-color="#b45309"/>
    </linearGradient>
    <filter id="glow"><feGaussianBlur stdDeviation="1.5"/></filter>
  </defs>
  <circle cx="32" cy="32" r="30" fill="url(#gold)" stroke="#fbbf24" stroke-width="1"/>
  <rect x="14" y="32" width="4" height="14" fill="#0d1117" rx="1"/>
  <rect x="22" y="22" width="4" height="24" fill="#3fb950" rx="1"/>
  <rect x="30" y="28" width="4" height="18" fill="#f85149" rx="1"/>
  <rect x="38" y="20" width="4" height="26" fill="#3fb950" rx="1"/>
  <rect x="46" y="26" width="4" height="20" fill="#0d1117" rx="1"/>
  <polyline points="16,38 24,28 32,32 40,22 48,30" stroke="#0d1117" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="32" y="58" text-anchor="middle" fill="#0d1117" font-size="6" font-weight="900" font-family="system-ui">GOTA</text>
</svg>
'''


def render_html(data: dict) -> str:
    if data is None:
        return "<html><body><h1>Connexion MT5 echec</h1></body></html>"

    a = data["account"]
    st = data["stats_today"]
    s7 = data["stats_7d"]
    s30 = data["stats_30d"]
    settings = data["settings"]
    logs = read_logs(40)

    pos_rows = ""
    for p in data["positions"]:
        cls = "buy" if p["type"] == "BUY" else "sell"
        pnl_cls = "pos" if p["profit"] >= 0 else "neg"
        pos_rows += f'''
        <tr>
          <td>#{p['ticket']}</td>
          <td><b>{html.escape(p['symbol'])}</b></td>
          <td><span class="tag {cls}">{p['type']}</span></td>
          <td>{p['volume']}</td>
          <td>{p['open']}</td>
          <td>{p['sl']}</td>
          <td>{p['tp']}</td>
          <td class="{pnl_cls}"><b>{p['profit']:+.2f}</b></td>
          <td>{p['time']}</td>
          <td>
            <form method="POST" action="/close" style="margin:0;display:inline">
              <input type="hidden" name="ticket" value="{p['ticket']}"/>
              <button type="submit" class="btn-row" onclick="return confirm('Fermer la position #{p['ticket']} ?');">✕</button>
            </form>
          </td>
        </tr>'''
    if not pos_rows:
        pos_rows = '<tr><td colspan="10" class="empty">Aucune position ouverte - le bot attend un setup</td></tr>'

    recent_rows = ""
    for t in data["recent_trades"]:
        cls = "buy" if t["type"] == "BUY" else "sell"
        pnl_cls = "pos" if t["profit"] >= 0 else "neg"
        recent_rows += f'''
        <tr>
          <td>{t['time']}</td>
          <td><b>{html.escape(t['symbol'])}</b></td>
          <td><span class="tag {cls}">{t['type']}</span></td>
          <td>{t['volume']}</td>
          <td>{t['price']}</td>
          <td class="{pnl_cls}"><b>{t['profit']:+.2f}</b></td>
        </tr>'''
    if not recent_rows:
        recent_rows = '<tr><td colspan="6" class="empty">Aucun trade ferme dans 30j</td></tr>'

    sym_chips = ""
    for k, v in sorted(data["symbols"].items()):
        sym_chips += f'<span class="chip"><b>{html.escape(k)}</b><code>{html.escape(v)}</code></span>'

    pause_banner = ""
    if data["paused"]:
        pause_banner = '<div class="banner pause"><div>⏸ <b>TRADING AUTO SUSPENDU</b></div><form method="POST" action="/resume" style="margin:0"><button class="btn green" type="submit">▶️ Reprendre</button></form></div>'

    auto = "🟢 ON" if a["autotrade"] else "🔴 OFF"
    pnl_today_cls = "pos" if st["pnl"] >= 0 else "neg"
    floating = a["equity"] - a["balance"]
    floating_cls = "pos" if floating >= 0 else "neg"

    eq_chart = make_equity_chart(data["equity_history"])
    pnl_bars = make_pnl_bars(data["by_symbol"])

    log_rows = ""
    for l in logs:
        cls = ""
        if "PLACED" in l or "TP" in l:
            cls = "log-success"
        elif "SL" in l or "ERROR" in l or "echec" in l.lower():
            cls = "log-error"
        elif "CLOSE" in l:
            cls = "log-info"
        log_rows += f'<div class="log-line {cls}">{html.escape(l)}</div>'

    settings_rows = ""
    settings_descriptions = {
        "scan_interval_min": "Intervalle entre scans",
        "scan_timeframe": "Bougies analysees",
        "risk_usd_target": "Risque cible par trade",
        "risk_usd_max": "Risque maximum par trade",
        "profit_usd_target": "Take profit MT5",
        "early_close_usd": "Fermeture auto early",
        "profit_usd_hard_max": "Plafond profit force close",
        "cooldown_hours": "Cooldown par symbole",
        "max_position_hours": "Duree max position",
        "daily_loss_usd_max": "Perte max 24h USD",
        "min_equity_usd": "Plancher equity",
        "demo_only": "Mode demo only",
    }
    for k, v in settings.items():
        if k == "error":
            continue
        unit = ""
        if "usd" in k:
            unit = " $"
        elif "hours" in k:
            unit = " h"
        elif "min" in k:
            unit = " min"
        desc = settings_descriptions.get(k, "")
        settings_rows += f'<tr><td><code>{k}</code></td><td>{desc}</td><td class="settings-val">{v}{unit}</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>GOTA TRADING</title>
<meta http-equiv="refresh" content="30">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; }}
body {{
  font-family: -apple-system, "Inter", "Segoe UI", system-ui, sans-serif;
  background: #0d1117;
  color: #c9d1d9;
  font-size: 14px;
  display: flex;
  min-height: 100vh;
}}

/* ===== SIDEBAR ===== */
.sidebar {{
  width: 240px;
  background: linear-gradient(180deg, #0d1117 0%, #161b22 100%);
  border-right: 1px solid #30363d;
  padding: 20px 0;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}}
.sidebar-brand {{
  padding: 0 20px 20px;
  border-bottom: 1px solid #30363d;
  display: flex;
  align-items: center;
  gap: 12px;
}}
.sidebar-brand .logo {{ width: 44px; height: 44px; flex-shrink: 0; }}
.sidebar-brand h1 {{ font-size: 16px; font-weight: 800; letter-spacing: 1px; color: #fbbf24; }}
.sidebar-brand .subtitle {{ font-size: 10px; color: #8b949e; margin-top: 2px; letter-spacing: 0.5px; }}

.nav {{ padding: 15px 10px; }}
.nav a {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  color: #c9d1d9;
  text-decoration: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s;
}}
.nav a:hover {{ background: rgba(251, 191, 36, 0.08); color: #fbbf24; }}
.nav a .icon {{ font-size: 16px; width: 20px; text-align: center; }}

.sidebar-status {{
  position: absolute;
  bottom: 20px;
  left: 20px;
  right: 20px;
  padding: 12px;
  border-radius: 8px;
  background: rgba(63, 185, 80, 0.1);
  border: 1px solid rgba(63, 185, 80, 0.3);
  font-size: 11px;
  text-align: center;
}}
.sidebar-status.paused {{ background: rgba(248, 81, 73, 0.1); border-color: rgba(248, 81, 73, 0.3); }}
.sidebar-status .dot {{
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #3fb950;
  margin-right: 6px;
  animation: pulse 2s infinite;
}}
.sidebar-status.paused .dot {{ background: #f85149; animation: none; }}
@keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}

/* ===== MAIN ===== */
.main {{
  flex: 1;
  padding: 30px 35px;
  max-width: calc(100% - 240px);
}}
.topbar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 25px;
}}
.topbar h2 {{ font-size: 24px; font-weight: 800; }}
.tag-account {{
  background: #3fb950;
  color: #0d1117;
  padding: 6px 14px;
  border-radius: 6px;
  font-weight: 700;
  font-size: 12px;
  letter-spacing: 0.5px;
}}
.tag-account.real {{ background: #f85149; color: white; }}

.banner {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-radius: 10px;
  margin-bottom: 25px;
  font-weight: 600;
}}
.banner.pause {{ background: linear-gradient(90deg, #f59e0b, #d97706); color: #1f1408; }}

/* ===== CARDS ===== */
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 30px; }}
.card {{
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 10px;
  padding: 16px;
  transition: all 0.2s;
}}
.card:hover {{ border-color: #fbbf24; transform: translateY(-2px); }}
.card .label {{
  color: #8b949e;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  font-weight: 600;
  margin-bottom: 8px;
}}
.card .value {{ font-size: 22px; font-weight: 800; }}
.card .sub {{ font-size: 11px; color: #8b949e; margin-top: 4px; }}

.pos {{ color: #3fb950 !important; }}
.neg {{ color: #f85149 !important; }}

.section {{ margin-bottom: 35px; }}
.section h3 {{
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #fbbf24;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #30363d;
  display: flex;
  align-items: center;
  gap: 8px;
}}

/* ===== TABLES ===== */
table {{ width: 100%; border-collapse: separate; border-spacing: 0; background: #161b22; border: 1px solid #30363d; border-radius: 10px; overflow: hidden; }}
th, td {{ padding: 12px; text-align: left; font-size: 13px; }}
th {{
  background: #1c2128;
  color: #fbbf24;
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid #30363d;
}}
tr:not(:last-child) td {{ border-bottom: 1px solid #21262d; }}
tr:hover td {{ background: rgba(251, 191, 36, 0.03); }}
.empty {{ text-align: center; color: #8b949e; font-style: italic; padding: 30px !important; }}

.tag {{ padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: 0.5px; }}
.tag.buy {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
.tag.sell {{ background: rgba(248, 81, 73, 0.15); color: #f85149; }}

/* ===== CHIPS ===== */
.chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.chip {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(251, 191, 36, 0.08);
  border: 1px solid rgba(251, 191, 36, 0.2);
  padding: 5px 10px;
  border-radius: 16px;
  font-size: 11px;
}}
.chip code {{
  background: rgba(255,255,255,0.03);
  padding: 1px 6px;
  border-radius: 3px;
  color: #8b949e;
  font-size: 10px;
}}

/* ===== BUTTONS ===== */
.btn {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: linear-gradient(135deg, #fbbf24, #d97706);
  color: #0d1117;
  padding: 9px 18px;
  border: none;
  border-radius: 8px;
  font-weight: 700;
  cursor: pointer;
  text-decoration: none;
  font-size: 12px;
  letter-spacing: 0.5px;
  transition: opacity 0.15s, transform 0.1s;
}}
.btn:hover {{ opacity: 0.9; }}
.btn:active {{ transform: scale(0.97); }}
.btn.red {{ background: linear-gradient(135deg, #f85149, #c93026); color: white; }}
.btn.green {{ background: linear-gradient(135deg, #3fb950, #238636); color: white; }}
.btn.ghost {{ background: transparent; border: 1px solid #30363d; color: #c9d1d9; }}
.btn-row {{ background: rgba(248, 81, 73, 0.1); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.3); padding: 4px 10px; border-radius: 5px; cursor: pointer; font-size: 12px; }}
.btn-row:hover {{ background: rgba(248, 81, 73, 0.2); }}

.actions {{ display: flex; gap: 10px; margin-bottom: 25px; }}

/* ===== BARS ===== */
.bars {{ display: flex; flex-direction: column; gap: 8px; }}
.bar-row {{ display: grid; grid-template-columns: 100px 1fr 80px 100px; gap: 12px; align-items: center; font-size: 12px; }}
.bar-label {{ font-weight: 600; }}
.bar-track {{ height: 18px; background: rgba(255,255,255,0.03); border-radius: 4px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s; }}
.bar-value {{ font-weight: 700; text-align: right; }}
.bar-meta {{ color: #8b949e; font-size: 11px; }}

/* ===== LOGS ===== */
.logs {{ background: #0a0e15; border: 1px solid #30363d; border-radius: 10px; padding: 12px; max-height: 320px; overflow-y: auto; font-family: "JetBrains Mono", monospace; font-size: 11px; }}
.log-line {{ padding: 3px 6px; border-radius: 3px; color: #c9d1d9; }}
.log-line.log-success {{ color: #3fb950; }}
.log-line.log-error {{ color: #f85149; }}
.log-line.log-info {{ color: #58a6ff; }}

/* ===== SETTINGS ===== */
.settings-val {{ font-family: "JetBrains Mono", monospace; color: #fbbf24; font-weight: 700; }}

/* ===== CHARTS ===== */
.chart-container {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 15px; }}
.empty-chart {{ color: #8b949e; text-align: center; padding: 40px; }}

.footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #21262d; color: #8b949e; font-size: 11px; text-align: center; }}
.footer b {{ color: #fbbf24; }}
</style>
</head>
<body>

<aside class="sidebar">
  <div class="sidebar-brand">
    {LOGO_SVG}
    <div>
      <h1>GOTA TRADING</h1>
      <div class="subtitle">SMC AUTO EXECUTOR</div>
    </div>
  </div>
  <nav class="nav">
    <a href="#overview"><span class="icon">📊</span> Vue d'ensemble</a>
    <a href="#performance"><span class="icon">📈</span> Performance</a>
    <a href="#positions"><span class="icon">📌</span> Positions</a>
    <a href="#history"><span class="icon">📋</span> Historique</a>
    <a href="#markets"><span class="icon">🌍</span> Marches</a>
    <a href="#settings"><span class="icon">⚙️</span> Parametres</a>
    <a href="#logs"><span class="icon">📃</span> Logs</a>
  </nav>
  <div class="sidebar-status {'paused' if data['paused'] else ''}">
    <span class="dot"></span>
    {'TRADING SUSPENDU' if data['paused'] else 'TRADING ACTIF'}
  </div>
</aside>

<main class="main">

  <div class="topbar">
    <h2>Tableau de bord</h2>
    <div style="display:flex;gap:10px;align-items:center;">
      <span class="tag-account {'real' if not a['demo'] else ''}">{'DEMO' if a['demo'] else 'REEL'}</span>
      <span style="color:#8b949e;font-size:12px;">Auto-Trade {auto}</span>
    </div>
  </div>

  {pause_banner}

  <div class="actions">
    {'<form method="POST" action="/resume" style="margin:0"><button type="submit" class="btn green">▶️ Reprendre</button></form>' if data['paused'] else '<form method="POST" action="/pause" style="margin:0"><button type="submit" class="btn red">⏸ Pause</button></form>'}
    <a href="/" class="btn ghost">🔄 Rafraichir</a>
  </div>

  <section id="overview" class="section">
    <h3>💼 Compte</h3>
    <div class="cards">
      <div class="card">
        <div class="label">Login</div>
        <div class="value" style="font-size:18px;">{a['login']}</div>
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
        <div class="sub">Positions ouvertes</div>
      </div>
      <div class="card">
        <div class="label">Levier</div>
        <div class="value">1:{a['leverage']}</div>
      </div>
    </div>
  </section>

  <section class="section">
    <h3>📈 Courbe d'equity</h3>
    <div class="chart-container">{eq_chart}</div>
  </section>

  <section id="performance" class="section">
    <h3>🎯 Performance</h3>
    <div class="cards">
      <div class="card">
        <div class="label">Aujourd'hui (24h)</div>
        <div class="value {pnl_today_cls}">{'+' if st['pnl']>=0 else ''}{st['pnl']:.2f}</div>
        <div class="sub">{st['trades']} trades - WR {st['wr']:.0f}% ({st['wins']}W/{st['losses']}L)</div>
      </div>
      <div class="card">
        <div class="label">7 derniers jours</div>
        <div class="value {'pos' if s7['pnl']>=0 else 'neg'}">{'+' if s7['pnl']>=0 else ''}{s7['pnl']:.2f}</div>
        <div class="sub">{s7['trades']} trades - WR {s7['wr']:.0f}%</div>
      </div>
      <div class="card">
        <div class="label">30 derniers jours</div>
        <div class="value {'pos' if s30['pnl']>=0 else 'neg'}">{'+' if s30['pnl']>=0 else ''}{s30['pnl']:.2f}</div>
        <div class="sub">{s30['trades']} trades - WR {s30['wr']:.0f}%</div>
      </div>
      <div class="card">
        <div class="label">Meilleur trade 30j</div>
        <div class="value pos">+{s30['best']:.2f}</div>
      </div>
      <div class="card">
        <div class="label">Pire trade 30j</div>
        <div class="value neg">{s30['worst']:.2f}</div>
      </div>
    </div>
    <div style="margin-top:15px" class="chart-container">
      <div style="color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:10px;">PnL par symbole (30j)</div>
      {pnl_bars}
    </div>
  </section>

  <section id="positions" class="section">
    <h3>📌 Positions ouvertes ({len(data['positions'])})</h3>
    <table>
      <tr><th>Ticket</th><th>Symbole</th><th>Type</th><th>Vol</th><th>Open</th><th>SL</th><th>TP</th><th>PnL</th><th>Heure</th><th></th></tr>
      {pos_rows}
    </table>
  </section>

  <section id="history" class="section">
    <h3>📋 Historique recent (30 derniers)</h3>
    <table>
      <tr><th>Date/heure</th><th>Symbole</th><th>Type</th><th>Vol</th><th>Prix</th><th>PnL</th></tr>
      {recent_rows}
    </table>
  </section>

  <section id="markets" class="section">
    <h3>🌍 Marches surveilles ({len(data['symbols'])})</h3>
    <div class="chips">{sym_chips}</div>
  </section>

  <section id="settings" class="section">
    <h3>⚙️ Parametres du bot</h3>
    <table>
      <tr><th>Cle</th><th>Description</th><th>Valeur</th></tr>
      {settings_rows}
    </table>
    <div style="margin-top:10px;color:#8b949e;font-size:11px;">
      Ces parametres sont configures dans mt5_executor.py. Pour les modifier, demande a l'assistant.
    </div>
  </section>

  <section id="logs" class="section">
    <h3>📃 Logs de trading (40 derniers)</h3>
    <div class="logs">{log_rows or '<div class="log-line">Aucun log encore.</div>'}</div>
  </section>

  <div class="footer">
    <b>GOTA TRADING</b> v{APP_VERSION} - Auto-refresh 30s - Bot powered by SMC strategy
  </div>

</main>

</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
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

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        params = parse_qs(body)

        if path == "/pause":
            PAUSE_FILE.write_text(f"{datetime.now().isoformat()}\nDepuis dashboard\n")
        elif path == "/resume":
            if PAUSE_FILE.exists():
                PAUSE_FILE.unlink()
        elif path == "/close":
            ticket = params.get("ticket", [None])[0]
            if ticket:
                try:
                    self._close_position(int(ticket))
                except Exception as e:
                    print(f"Close error: {e}")

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _close_position(self, ticket: int):
        cfg = json.loads(CONFIG_FILE.read_text())
        if not mt5.initialize(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
            return
        try:
            positions = mt5.positions_get(ticket=ticket) or []
            for pos in positions:
                tick = mt5.symbol_info_tick(pos.symbol)
                if not tick:
                    continue
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": pos.ticket,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
                    "price": tick.bid if pos.type == 0 else tick.ask,
                    "deviation": 50,
                    "magic": 20260508,
                    "comment": "manual close from dashboard",
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                mt5.order_send(req)
        finally:
            mt5.shutdown()

    def log_message(self, fmt, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"GOTA TRADING Dashboard : http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret")


if __name__ == "__main__":
    main()
