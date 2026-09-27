"""
signal_dashboard.py - Tableau de bord LOCAL (lecture seule) du systeme de signaux GOTA.

Montre en un coup d'oeil ce que fait le robot qui tourne dans le cloud (GitHub Actions), meme quand ce PC est eteint :
etat de la chaine, marche (ouvert/ferme), signaux actifs (VIP, dechiffres localement), historique reel et bilan.

Ne pilote rien : aucune ecriture sur le depot, aucun ordre. Toutes les donnees viennent du depot PUBLIC
(workflow runs + branche "data") ; les niveaux des signaux EN COURS sont chiffres sur le depot et dechiffres
uniquement ici, sur ce PC, avec la cle locale state_key.txt (jamais envoyee nulle part).

Lancer :  python signal_dashboard.py   (ou double-clic sur run_signal_dashboard.cmd)
Ouvrir :  http://localhost:8090
"""
from __future__ import annotations
import base64
import html
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

DIR = Path(__file__).parent
PORT = 8090
REPO = "gotatrading3-cmd/smc-scanner"
RAW = f"https://raw.githubusercontent.com/{REPO}/data"
API = f"https://api.github.com/repos/{REPO}"
AUTH_FILE = DIR / "dashboard_auth.json"
KEY_FILE = DIR / "state_key.txt"

BG, PANEL, PANEL2, BORDER = "#06080c", "#0b1017", "#0d1420", "#1c2531"
TXT, MUTED, DIM = "#e8ebf0", "#8a93a1", "#4b5462"
GOLD, GOLD_L = "#d4af37", "#f1d67c"
GREEN, RED, BLUE = "#2ecc71", "#ef4444", "#38bdf8"

# ------------------------------------------------------------------ auth (reprend dashboard_auth.json, comme dashboard.py)
def _load_auth() -> tuple[str, str]:
    try:
        d = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return d.get("user", "gota"), d.get("password", "gota")
    except Exception:
        return "gota", "gota"


def _check_basic_auth(header_value: str) -> bool:
    user, pwd = _load_auth()
    if not header_value.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header_value[6:]).decode("utf-8")
        u, _, p = decoded.partition(":")
        return u == user and p == pwd
    except Exception:
        return False


# ------------------------------------------------------------------ donnees (depot public + dechiffrement local)
_RUNS_CACHE = {"at": 0.0, "value": None, "rate_limited": False}
_RUNS_TTL = 180  # secondes : l'API GitHub (api.github.com) est limitee a 60 requetes/heure PAR IP sans jeton ;
# ce cache evite qu'un simple refresh de la page en consomme une a chaque fois. raw.githubusercontent.com
# (etat + historique) n'est PAS soumis a cette meme limite : seul l'appel "workflow runs" est mis en cache ici.


def _get(url: str, timeout: float = 5.0):
    try:
        r = requests.get(url, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            return r
        if r.status_code == 403 and "rate limit" in r.text.lower():
            return "rate_limited"
    except Exception:
        pass
    return None


def _get_runs_cached():
    """Version mise en cache (3 min) de l'appel 'workflow runs', pour ne pas epuiser la limite GitHub (60/h/IP)."""
    now = time.time()
    if now - _RUNS_CACHE["at"] < _RUNS_TTL and (_RUNS_CACHE["value"] is not None or _RUNS_CACHE["rate_limited"]):
        return _RUNS_CACHE["value"], _RUNS_CACHE["rate_limited"]
    r = _get(f"{API}/actions/workflows/continu.yml/runs?per_page=5")
    rate_limited = r == "rate_limited"
    r = None if rate_limited else r
    _RUNS_CACHE.update(at=now, value=r, rate_limited=rate_limited)
    return r, rate_limited


def _fernet():
    from cryptography.fernet import Fernet
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    return Fernet(key.encode())


def fetch_snapshot() -> dict:
    """Tout ce qu'affiche le tableau de bord, recalcule a chaque chargement de page (donnees reelles, jamais inventees).
    Les 3 appels reseau partent EN PARALLELE (chacun limite a 5 s) : la page repond vite meme si GitHub est lent,
    au lieu d'attendre chaque appel l'un apres l'autre."""
    out = {
        "fetched": datetime.now(timezone.utc), "chain_ok": False, "chain_detail": "inconnu",
        "chain_runs": [], "state": {}, "active": {}, "history": [], "key_ok": False, "error": None,
    }

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_runs = ex.submit(_get_runs_cached)
        f_state = ex.submit(_get, f"{RAW}/signals_state.json")
        f_hist = ex.submit(_get, f"{RAW}/signals_history.jsonl")
        (runs, rate_limited), st, hist = f_runs.result(), f_state.result(), f_hist.result()

    if rate_limited:
        out["chain_detail"] = "limite de requêtes GitHub atteinte pour ce PC (60/h sans compte) — ça revient tout seul d'ici quelques minutes"
    if runs is not None:
        wr = runs.json().get("workflow_runs", [])
        out["chain_runs"] = wr
        for r in wr:
            if r.get("status") != "completed":  # in_progress, queued, pending, waiting... tout sauf termine = la chaine tourne
                out["chain_ok"] = True
                out["chain_detail"] = f"en cours depuis {r['created_at']} ({r.get('status')})"
                break
        if not out["chain_ok"] and wr:
            last = wr[0]
            age_min = (out["fetched"] - datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))).total_seconds() / 60
            out["chain_ok"] = last.get("conclusion") == "success" and age_min < 20
            out["chain_detail"] = f"dernier passage il y a {age_min:.0f} min ({last.get('conclusion') or last.get('status')})"
    elif not rate_limited:
        out["chain_detail"] = "GitHub injoignable depuis ce PC (ne veut pas dire que le robot est arrete)"

    if st is not None:
        raw = st.json()
        enc = raw.pop("private_enc", None)
        out["state"] = raw
        if enc:
            if KEY_FILE.exists():
                try:
                    priv = json.loads(_fernet().decrypt(enc.encode()).decode())
                    out["active"] = priv.get("active", {})
                    out["key_ok"] = True
                except Exception:
                    out["error"] = "cle locale presente mais le dechiffrement a echoue (cle differente de celle du cloud ?)"
            else:
                out["error"] = "cle locale (state_key.txt) introuvable : signaux en cours non lisibles ici"

    if hist is not None:
        for ln in hist.text.splitlines():
            try:
                out["history"].append(json.loads(ln))
            except Exception:
                pass
    return out


def market_open(now: datetime) -> bool:
    """Forex : ferme du vendredi ~22h UTC au dimanche ~22h UTC (approximatif, hors jours feries)."""
    wd, h = now.weekday(), now.hour
    if wd == 5:
        return False
    if wd == 4 and h >= 22:
        return False
    if wd == 6 and h < 22:
        return False
    return True


def label_outcome(o: str) -> str:
    return {"SL": "Stop touché", "TP1": "TP1 puis stop entrée", "TP2": "TP2 atteint", "TP3": "TP3 atteint",
            "EXPIRED": "Clôturé au temps"}.get(o, o)


def age_str(iso: str, now: datetime) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        mins = int((now - t).total_seconds() / 60)
    except Exception:
        return "?"
    if mins < 60:
        return f"{mins} min"
    if mins < 1440:
        return f"{mins // 60} h {mins % 60:02d}"
    return f"{mins // 1440} j"


# ------------------------------------------------------------------ petits graphiques (SVG, palette GOTA)
def equity_curve(history: list) -> str:
    """Courbe du R cumule (une seule serie, or) ; un point = un trade clos, dans l'ordre. Infobulle native au survol."""
    live = sorted([h for h in history if h.get("mode") == "live"], key=lambda h: h["close_time"])
    if len(live) < 1:
        return '<div class="empty">Aucun trade clôturé pour l\'instant — la courbe apparaîtra dès le premier trade fermé.</div>'
    W, H, PAD = 720, 200, 28
    cum, pts = 0.0, [0.0]
    for h in live:
        cum += float(h["r"])
        pts.append(cum)
    lo, hi = min(pts), max(pts)
    span = max(hi - lo, 1.0)
    n = len(pts)
    xs = [PAD + i * (W - 2 * PAD) / max(n - 1, 1) for i in range(n)]
    ys = [H - PAD - (v - lo) / span * (H - 2 * PAD) for v in pts]
    y0 = H - PAD - (0 - lo) / span * (H - 2 * PAD)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{GOLD if pts[i] >= 0 else RED}" stroke="{BG}" stroke-width="1.5">'
        f'<title>{"Départ" if i == 0 else f"{live[i-1]["display"]} · {label_outcome(live[i-1]["outcome"])} · {live[i-1]["r"]:+.2f}R"}'
        f" — cumul {pts[i]:+.2f}R</title></circle>"
        for i, (x, y) in enumerate(zip(xs, ys)))
    final_cls = GREEN if cum >= 0 else RED
    return f'''<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Résultat cumulé en R">
      <line x1="{PAD}" y1="{y0:.1f}" x2="{W - PAD}" y2="{y0:.1f}" stroke="{BORDER}" stroke-width="1" stroke-dasharray="3,4"/>
      <path d="{path}" fill="none" stroke="{GOLD}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
      {dots}
      <text x="{PAD}" y="16" fill="{MUTED}" font-size="11" font-family="inherit">Résultat cumulé (R) · {n - 1} trade{'s' if n - 1 > 1 else ''}</text>
      <text x="{W - PAD}" y="16" fill="{final_cls}" font-size="13" font-weight="700" text-anchor="end" font-family="inherit">{cum:+.2f} R</text>
    </svg>'''


def strength_bars(snap: dict) -> str:
    """Force des devises sur 5 jours : barres divergentes autour de zero, valeur toujours affichee (jamais la couleur seule)."""
    ranked = (snap or {}).get("ranked") or []
    if len(ranked) < 2:
        return '<div class="empty">Instantané du marché pas encore disponible.</div>'
    mx = max(abs(p) for _, p in ranked) or 1.0
    rows = []
    for cur, p in ranked:
        w = abs(p) / mx * 46
        side = "right" if p >= 0 else "left"
        color = GREEN if p >= 0 else RED
        rows.append(f'''<div class="bar-row">
          <span class="bar-cur">{html.escape(cur)}</span>
          <div class="bar-track">
            <div class="bar-fill {side}" style="width:{w:.1f}%;background:{color}"></div>
          </div>
          <span class="bar-val" style="color:{color}">{p:+.2f}%</span>
        </div>''')
    return f'<div class="bars">{"".join(rows)}</div>'


# ------------------------------------------------------------------ page
def render_content(data: dict) -> str:
    """Fragment HTML des donnees (servi par /data, injecte par le squelette dans #app). Peut echouer si le reseau
    est mauvais : l'appelant (do_GET) rattrape toute exception et renvoie un message clair au lieu de planter."""
    now = data["fetched"]
    st, active, hist = data["state"], data["active"], data["history"]
    live_hist = [h for h in hist if h.get("mode") == "live"]
    n = len(live_hist)
    tp1 = sum(1 for h in live_hist if h["outcome"] in ("TP1", "TP2", "TP3")) / n * 100 if n else 0.0
    sl = sum(1 for h in live_hist if h["outcome"] == "SL") / n * 100 if n else 0.0
    total_r = sum(float(h["r"]) for h in live_hist)
    today = str(now.date())
    fx_open = market_open(now)

    chain_pill = f'<span class="pill {"ok" if data["chain_ok"] else "bad"}"><span class="dot"></span>{"En ligne" if data["chain_ok"] else "À vérifier"}</span>'
    market_pill = f'<span class="pill {"ok" if fx_open else "muted"}">{"Marché FX ouvert" if fx_open else "Marché FX fermé (week-end)"}</span>'

    def _cu(target: int) -> str:
        return f'<span class="cu" data-target="{target}">0</span>'

    kpis = [
        ("Signaux clôturés", _cu(n), TXT),
        ("Taux TP1", f"{tp1:.0f}%", GREEN if tp1 >= 50 else GOLD),
        ("Stops", f"{sl:.0f}%", RED if sl else MUTED),
        ("Résultat cumulé", f"{total_r:+.1f} R", GREEN if total_r >= 0 else RED),
        ("Signaux aujourd'hui", _cu(st.get("per_day", {}).get(today, 0)), TXT),
        ("Actifs en ce moment", _cu(len(active)), GOLD if active else MUTED),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="kpi-label">{html.escape(l)}</div><div class="kpi-val" style="color:{c}">{v}</div></div>' for l, v, c in kpis)

    active_html = ""
    for sid, rec in active.items():
        sg = rec.get("signal", {})
        if not sg:
            continue
        long_ = sg.get("direction") == "LONG"
        dcol = GREEN if long_ else RED
        tps = sg.get("tps", [None, None, None])
        active_html += f'''<div class="sig-card">
          <div class="sig-head">
            <span class="sig-pair">{html.escape(sg.get("display", "?"))}</span>
            <span class="sig-dir" style="color:{dcol};border-color:{dcol}">{"ACHAT" if long_ else "VENTE"}</span>
            <span class="sig-age">depuis {age_str(rec.get("published", ""), now)}</span>
            {'<span class="sig-tag">public</span>' if rec.get("public_msg_id") else ""}
          </div>
          <div class="sig-levels">
            <div><span class="lbl">Entrée</span><b>{sg.get("entry", "?")}</b></div>
            <div><span class="lbl" style="color:{RED}">Stop</span><b style="color:{RED}">{sg.get("sl", "?")}</b></div>
            <div><span class="lbl" style="color:{GREEN}">TP1</span><b style="color:{GREEN}">{tps[0]}</b></div>
            <div><span class="lbl" style="color:{GREEN}">TP2</span><b style="color:{GREEN}">{tps[1]}</b></div>
            <div><span class="lbl" style="color:{GREEN}">TP3</span><b style="color:{GREEN}">{tps[2]}</b></div>
          </div>
        </div>'''
    if not active_html:
        if not data["key_ok"] and active == {} and data.get("error"):
            active_html = f'<div class="empty">⚠ {html.escape(data["error"])}</div>'
        else:
            active_html = '<div class="empty">Aucun signal actif en ce moment.</div>'

    trade_rows = ""
    for h in sorted(hist, key=lambda x: x["close_time"], reverse=True)[:12]:
        pos = float(h["r"]) >= 0
        badge = GREEN if pos else RED
        mode_tag = "" if h.get("mode") == "live" else '<span class="chip">aperçu</span>'
        trade_rows += f'''<tr>
          <td>{html.escape(str(h["close_time"])[:16]).replace("T", " ")}</td>
          <td><b>{html.escape(h["display"])}</b></td>
          <td>{"Achat" if h["direction"] == "LONG" else "Vente"}</td>
          <td>{label_outcome(h["outcome"])} {mode_tag}</td>
          <td style="color:{badge};font-weight:700">{float(h["r"]):+.2f} R</td>
        </tr>'''
    if not trade_rows:
        trade_rows = '<tr><td colspan="5" class="empty">Aucun trade clôturé pour l\'instant.</td></tr>'

    watch = [v["display"] for v in (st.get("watch") or {}).values() if v]
    watch_html = ", ".join(html.escape(w) for w in watch[:10]) if watch else "aucune paire particulière en ce moment"

    runs_html = ""
    for r in data["chain_runs"][:5]:
        cls = {"success": GREEN, "failure": RED}.get(r.get("conclusion"), GOLD if r.get("status") == "in_progress" else MUTED)
        runs_html += f'<div class="run-line"><span class="run-dot" style="background:{cls}"></span>{r["created_at"][:16].replace("T"," ")} UTC — {r.get("conclusion") or r.get("status")}</div>'

    return f'''
  <div class="top">
    <h1>🟡 GOTA SIGNAUX</h1>
    <div class="sub">Tableau de bord local · lecture seule · actualisé toutes les 90 s</div>
    {chain_pill}{market_pill}
  </div>

  <div class="sessions-row" id="sessions"></div>

  <div class="grid">{kpi_html}</div>

  <div class="panels">
    <div class="panel">
      <h2>Résultat cumulé</h2>
      {equity_curve(hist)}
    </div>
    <div class="panel">
      <h2>Force des devises (5 jours)</h2>
      {strength_bars(st.get("snap", {}).get("data"))}
      <div style="margin-top:14px;font-size:12px;color:{MUTED}">👀 Zones surveillées : {watch_html}</div>
    </div>
  </div>

  <div class="panel" style="margin-bottom:16px;">
    <h2>Signaux actifs (VIP — déchiffrés localement, jamais publiés en clair)</h2>
    {active_html}
  </div>

  <div class="panel" style="margin-bottom:16px;">
    <h2>Historique des trades clôturés</h2>
    <table><thead><tr><th>Clôturé</th><th>Paire</th><th>Sens</th><th>Résultat</th><th>R</th></tr></thead>
    <tbody>{trade_rows}</tbody></table>
  </div>

  <div class="panel">
    <h2>Santé de la chaîne cloud (GOTA Continu)</h2>
    <div class="sub" style="margin-bottom:8px;">{html.escape(data["chain_detail"])}</div>
    <div class="runs">{runs_html or '<div class="empty">Historique des exécutions indisponible.</div>'}</div>
  </div>

  <div class="foot">Généré le {now.strftime('%d/%m/%Y à %H:%M:%S UTC')} · données publiques du dépôt {REPO} · aucun ordre n'est passé depuis cette page
    &nbsp;·&nbsp;<a href="http://localhost:8080" style="color:{MUTED}">comptes MT5 locaux →</a></div>
'''


def render_shell() -> str:
    """Page servie INSTANTANEMENT sur / (aucun appel reseau) : ecran de demarrage GOTA, puis le contenu reel
    (route /data) vient s'y glisser des qu'il est pret. Ne peut pas echouer : garantit qu'un clic sur l'icone
    affiche toujours quelque chose tout de suite, meme si GitHub est lent ou injoignable."""
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GOTA Signaux — Tableau de bord</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:{BG}; color:{TXT}; font-family: "Sora", -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .mono {{ font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 60px; }}
  /* ---------- ecran de demarrage ---------- */
  #splash {{
    position:fixed; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:18px;
    background:{BG}; z-index:50; transition:opacity .5s ease; padding:20px; text-align:center;
  }}
  #splash.hide {{ opacity:0; pointer-events:none; }}
  .splash-mark {{ position:relative; width:88px; height:88px; }}
  .splash-mark svg {{ width:100%; height:100%; }}
  .splash-ring {{ position:absolute; inset:-14px; border-radius:50%; border:2px solid transparent; border-top-color:{GOLD}; border-right-color:{GOLD}55; animation:spin 1.1s linear infinite; }}
  .splash-word {{ font-size:22px; font-weight:700; letter-spacing:.14em; }}
  .splash-word b {{ color:{GOLD}; }}
  .splash-sub {{ font-family:"IBM Plex Mono",monospace; font-size:12px; color:{MUTED}; letter-spacing:.03em; }}
  .splash-dots span {{ animation:blink 1.4s ease-in-out infinite; }}
  .splash-dots span:nth-child(2) {{ animation-delay:.2s; }}
  .splash-dots span:nth-child(3) {{ animation-delay:.4s; }}
  .splash-err {{ color:{RED}; font-size:12px; margin-top:6px; display:none; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
  @media (prefers-reduced-motion: reduce) {{ .splash-ring, .splash-dots span {{ animation:none !important; }} }}
  .top {{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:22px; }}
  .top h1 {{ font-size:22px; margin:0; letter-spacing:.5px; font-weight:700; }}
  .top .sub {{ color:{MUTED}; font-size:12.5px; font-family:"IBM Plex Mono",monospace; }}
  .sessions-row {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:18px; }}
  .session-card {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:11px; padding:12px 13px; transition:border-color .3s,background .3s; }}
  .session-card.active {{ border-color:{GREEN}66; background:linear-gradient(180deg,{GREEN}14,{PANEL} 65%); }}
  .session-top {{ display:flex; align-items:center; justify-content:space-between; font-weight:600; font-size:12.5px; }}
  .session-dot {{ width:6px; height:6px; border-radius:50%; background:{DIM}; }}
  .session-card.active .session-dot {{ background:{GREEN}; box-shadow:0 0 6px {GREEN}; animation:blink 2.2s ease-in-out infinite; }}
  .session-hours {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; color:{MUTED}; display:block; margin-top:5px; }}
  .session-state {{ font-size:9.5px; text-transform:uppercase; letter-spacing:.05em; color:{DIM}; }}
  .session-card.active .session-state {{ color:{GREEN}; }}
  @keyframes blink {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.35; }} }}
  .pill {{ display:inline-flex; align-items:center; gap:6px; padding:6px 12px; border-radius:20px; font-size:12.5px; font-weight:600; border:1px solid {BORDER}; }}
  .pill.ok {{ color:{GREEN}; border-color:{GREEN}33; background:#0e1a14; }}
  .pill.bad {{ color:{RED}; border-color:{RED}33; background:#1a0e0e; }}
  .pill.muted {{ color:{MUTED}; }}
  .pill .dot {{ width:7px; height:7px; border-radius:50%; background:currentColor; }}
  .grid {{ display:grid; grid-template-columns: repeat(6, 1fr); gap:12px; margin-bottom:22px; }}
  .kpi {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:12px; padding:14px 16px; }}
  .kpi-label {{ font-size:10.5px; color:{MUTED}; text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px; }}
  .kpi-val {{ font-size:22px; font-weight:700; font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; }}
  .panels {{ display:grid; grid-template-columns: 1.3fr 1fr; gap:16px; margin-bottom:16px; }}
  .panel {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:14px; padding:18px 20px; }}
  .panel h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:{MUTED}; margin:0 0 14px; }}
  .chart {{ width:100%; height:auto; font-family: -apple-system, sans-serif; }}
  .bars {{ display:flex; flex-direction:column; gap:8px; }}
  .bar-row {{ display:grid; grid-template-columns: 42px 1fr 60px; align-items:center; gap:8px; font-size:12.5px; }}
  .bar-cur {{ font-weight:700; color:{TXT}; }}
  .bar-track {{ position:relative; height:8px; background:{PANEL2}; border-radius:4px; overflow:hidden; }}
  .bar-fill {{ position:absolute; top:0; bottom:0; border-radius:4px; }}
  .bar-fill.right {{ left:50%; }}
  .bar-fill.left {{ right:50%; }}
  .bar-val {{ text-align:right; font-weight:700; }}
  .sig-card {{ background:{PANEL2}; border:1px solid {BORDER}; border-radius:10px; padding:12px 14px; margin-bottom:10px; }}
  .sig-head {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap; }}
  .sig-pair {{ font-weight:700; font-size:14px; }}
  .sig-dir {{ font-size:11px; font-weight:700; border:1px solid; border-radius:12px; padding:2px 8px; }}
  .sig-age {{ color:{MUTED}; font-size:11.5px; margin-left:auto; }}
  .sig-tag {{ background:{GOLD}22; color:{GOLD}; font-size:10px; padding:2px 7px; border-radius:8px; }}
  .sig-levels {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12.5px; }}
  .sig-levels .lbl {{ display:block; color:{MUTED}; font-size:10px; text-transform:uppercase; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; color:{MUTED}; font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; padding:6px 8px; border-bottom:1px solid {BORDER}; }}
  td {{ padding:8px; border-bottom:1px solid {BORDER}; }}
  .chip {{ background:{PANEL2}; color:{MUTED}; font-size:9.5px; padding:1px 6px; border-radius:6px; margin-left:6px; }}
  .empty {{ color:{MUTED}; font-size:13px; padding:16px 0; }}
  .runs {{ margin-top:10px; }}
  .run-line {{ font-size:11.5px; color:{MUTED}; display:flex; align-items:center; gap:8px; padding:3px 0; }}
  .run-dot {{ width:7px; height:7px; border-radius:50%; flex:none; }}
  .foot {{ color:{DIM}; font-size:11.5px; margin-top:24px; text-align:center; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns: repeat(2,1fr); }} .panels {{ grid-template-columns: 1fr; }} }}
</style></head>
<body>

<div id="splash">
  <div class="splash-mark">
    <div class="splash-ring"></div>
    <svg viewBox="0 0 32 32" fill="none"><path d="M4 24 L11 10 L16 18 L21 6 L28 24" stroke="{GOLD}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </div>
  <div class="splash-word"><b>GOTA</b> SIGNAUX</div>
  <div class="splash-sub" id="splashMsg">Connexion au robot<span class="splash-dots"><span>.</span><span>.</span><span>.</span></span></div>
  <div class="splash-err" id="splashErr">Le réseau met du temps à répondre — nouvel essai dans 5 s.</div>
</div>

<div class="wrap"><div id="app"></div></div>

<script>
  var SESSIONS = [{{n:"Sydney",o:22,c:7}},{{n:"Tokyo",o:0,c:9}},{{n:"Londres",o:8,c:17}},{{n:"New York",o:13,c:22}}];
  function inSess(h,s) {{ return s.o<s.c ? (h>=s.o&&h<s.c) : (h>=s.o||h<s.c); }}
  function drawSessions() {{
    var h = new Date().getUTCHours(), box = document.getElementById("sessions");
    if (!box) return;
    box.innerHTML = SESSIONS.map(function(s){{
      var a = inSess(h,s);
      return '<div class="session-card'+(a?' active':'')+'"><div class="session-top"><span>'+s.n+'</span><span class="session-dot"></span></div>'+
             '<span class="session-hours">'+String(s.o).padStart(2,'0')+'h–'+String(s.c).padStart(2,'0')+'h UTC</span>'+
             '<span class="session-state">'+(a?'Ouverte':'Fermée')+'</span></div>';
    }}).join('');
  }}

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function countUp(el) {{
    var target = parseInt(el.getAttribute('data-target'),10)||0;
    if (reduceMotion) {{ el.textContent = target; return; }}
    var t0 = performance.now(), dur = 800;
    function step(t) {{
      var p = Math.min(1,(t-t0)/dur);
      el.textContent = Math.round(target*(1-Math.pow(1-p,3)));
      if (p<1) requestAnimationFrame(step); else el.textContent = target;
    }}
    requestAnimationFrame(step);
  }}

  function initDashboard() {{
    drawSessions();
    setInterval(drawSessions, 30000);
    document.querySelectorAll('.cu').forEach(countUp);
    if (!reduceMotion && 'IntersectionObserver' in window) {{
      var targets = document.querySelectorAll('.kpi, .panel, .session-card, .sig-card');
      targets.forEach(function(el){{ el.style.opacity=0; el.style.transform='translateY(10px)'; el.style.transition='opacity .45s ease,transform .45s ease'; }});
      var io = new IntersectionObserver(function(es){{ es.forEach(function(e){{ if(e.isIntersecting){{ e.target.style.opacity=1; e.target.style.transform='none'; io.unobserve(e.target); }} }}); }}, {{threshold:.1}});
      targets.forEach(function(el){{ io.observe(el); }});
    }}
  }}

  // ---- ecran de demarrage : le vrai contenu (/data) vient se glisser dedans des qu'il est pret ----
  // Ne peut jamais rester bloque : nouvel essai automatique toutes les 5 s tant que /data echoue.
  function loadData() {{
    var errEl = document.getElementById('splashErr'), msgEl = document.getElementById('splashMsg');
    fetch('/data', {{ cache: 'no-store' }}).then(function(r) {{
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.text();
    }}).then(function(htmlText) {{
      document.getElementById('app').innerHTML = htmlText;
      initDashboard();
      var splash = document.getElementById('splash');
      splash.classList.add('hide');
      setTimeout(function(){{ splash.remove(); }}, 550);
    }}).catch(function() {{
      errEl.style.display = 'block'; msgEl.style.display = 'none';
      setTimeout(function(){{ errEl.style.display='none'; msgEl.style.display=''; loadData(); }}, 5000);
    }});
  }}
  function refreshData() {{
    fetch('/data', {{ cache: 'no-store' }}).then(function(r) {{ return r.text(); }})
      .then(function(t) {{ document.getElementById('app').innerHTML = t; initDashboard(); }}).catch(function() {{}});
  }}
  loadData();
  setInterval(refreshData, 90000);
</script>
</body></html>'''


# ------------------------------------------------------------------ serveur
_PUBLIC_PATHS = {"/favicon.ico"}


class Handler(BaseHTTPRequestHandler):
    def _trusted_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        path = urlparse(self.path).path
        if path not in _PUBLIC_PATHS and not self._trusted_local() and not _check_basic_auth(self.headers.get("Authorization", "")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="GOTA Signaux"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/favicon.ico":
            try:
                b = (DIR / "logo.ico").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/x-icon")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            except Exception:
                self.send_response(404)
                self.end_headers()
            return
        if path == "/":
            # coquille instantanee (aucun appel reseau) : garantit un affichage immediat, meme si GitHub est lent
            body = render_shell().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/data":
            self.send_response(404)
            self.end_headers()
            return
        try:
            data = fetch_snapshot()
            body = render_content(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = f"<div class='panel'><h2>Erreur</h2><pre style='white-space:pre-wrap;color:{MUTED}'>{html.escape(str(e))}</pre></div>"
            body = err.encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    print(f"GOTA Signaux - Tableau de bord sur http://localhost:{PORT}  (Ctrl+C pour arreter)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
