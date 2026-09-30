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
import subprocess
import sys
import time
from pathlib import Path

# Renforcement du chemin des paquets installes (pip --user) : sur cette machine, selon COMMENT ce script est lance
# (icone bureau, .cmd, etc.), Python ne trouve pas toujours tout seul le dossier utilisateur ou vivent "requests" et
# "cryptography" - deja vu en pratique (ModuleNotFoundError alors que le meme Python les trouve dans un terminal normal).
# Chemin ecrit en dur (n'a besoin d'aucune variable d'environnement) + ajout via le module site (methode officielle).
for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars(r"%APPDATA%\Python\Python312\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
try:
    import site
    site.addsitedir(r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages")
except Exception:
    pass

# Verrou mono-instance : si l'icone est cliquee plusieurs fois rapidement (ca arrive - rien de mal a
# ca), plusieurs "python signal_dashboard.py" peuvent demarrer en meme temps et se battre sur le meme
# fichier de log / port, ce qui les fait planter en cascade (vu en pratique : PermissionError sur le
# .log, puis ModuleNotFoundError par ricochet). Avec ce verrou, les instances en trop s'arretent tout
# de suite et proprement (pas une erreur) ; la seule instance legitime continue normalement.
_LOCK_FILE = Path(__file__).parent / "signal_dashboard.lock"


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                              capture_output=True, text=True, timeout=5)
        return str(pid) in out.stdout
    except Exception:
        return True  # en cas de doute, ne pas voler le verrou


def _acquire_single_instance_lock() -> bool:
    for _ in range(2):
        try:
            with open(_LOCK_FILE, "x", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            import atexit
            atexit.register(lambda: _LOCK_FILE.unlink(missing_ok=True))
            return True
        except FileExistsError:
            try:
                existing_pid = int(_LOCK_FILE.read_text(encoding="utf-8").strip())
            except Exception:
                existing_pid = None
            if existing_pid and _pid_alive(existing_pid):
                return False  # une instance legitime tourne deja
            try:
                _LOCK_FILE.unlink()  # verrou abandonne par un plantage precedent - on le recupere
            except Exception:
                pass
    return False


if not _acquire_single_instance_lock():
    sys.exit(0)

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

try:
    import requests
except ModuleNotFoundError as _e:
    # Ne doit plus jamais arriver avec le renforcement ci-dessus (et le verrou mono-instance empeche
    # maintenant la cause la plus frequente : plusieurs process concurrents). Message clair dans le
    # journal pour diagnostiquer plus vite si ca arrive quand meme - mais un echec d'ECRITURE du log
    # ne doit jamais ajouter une 2e erreur par-dessus la premiere.
    try:
        with open(Path(__file__).parent / "signal_dashboard.log", "a", encoding="utf-8") as _f:
            _f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERREUR CRITIQUE : {_e} — sys.path={sys.path}\n")
    except Exception:
        pass
    raise

DIR = Path(__file__).parent
PORT = 8090
REPO = "gotatrading3-cmd/smc-scanner"
RAW = f"https://raw.githubusercontent.com/{REPO}/data"
API = f"https://api.github.com/repos/{REPO}"
AUTH_FILE = DIR / "dashboard_auth.json"
KEY_FILE = DIR / "state_key.txt"
CHANNEL_FILE = DIR / "channel.json"

BG, PANEL, PANEL2, BORDER = "#06080c", "#0b1017", "#0d1420", "#1c2531"
TXT, MUTED, DIM = "#e8ebf0", "#8a93a1", "#4b5462"
GOLD, GOLD_L = "#d4af37", "#f1d67c"
GREEN, RED, BLUE = "#2ecc71", "#ef4444", "#38bdf8"

# Symboles pour l'onglet "Marches en direct" (widgets TradingView, aucune cle requise) : le meme univers que celui
# que le robot scanne. Format TradingView : prefixe d'echange ou flux agrege ":" + symbole.
FX_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
            "EURAUD", "EURCAD", "EURCHF", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD", "AUDJPY", "AUDCAD",
            "AUDCHF", "AUDNZD", "CADJPY", "CADCHF", "CHFJPY", "NZDJPY", "NZDCAD", "NZDCHF"]
CRYPTO_PAIRS = ["BTCUSD", "ETHUSD"]
METAL_PAIRS = ["XAUUSD", "XAGUSD"]


def _tv_symbol(sym: str) -> str:
    if sym in CRYPTO_PAIRS:
        return f"COINBASE:{sym}"
    if sym in METAL_PAIRS:
        return f"OANDA:{sym}"
    return f"FX:{sym}"

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


def load_links() -> dict:
    """Liens locaux (channel.json) : jamais envoyes nulle part, juste des raccourcis pour toi sur cette page LOCALE."""
    try:
        return json.loads(CHANNEL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def weekly_recap(live_hist: list, now: datetime) -> list:
    """Resultat par semaine ISO (4 dernieres semaines qui ont un trade), le plus recent en premier. Donnees reelles uniquement."""
    weeks: dict = {}
    for h in live_hist:
        try:
            t = datetime.fromisoformat(str(h["close_time"]).replace("Z", "+00:00"))
        except Exception:
            continue
        iso = t.isocalendar()
        key = (iso[0], iso[1])
        w = weeks.setdefault(key, {"n": 0, "r": 0.0, "tp1": 0})
        w["n"] += 1
        w["r"] += float(h["r"])
        if h["outcome"] in ("TP1", "TP2", "TP3"):
            w["tp1"] += 1
    out = [{"label": f"Semaine {k[1]:02d} · {k[0]}", "n": v["n"], "r": v["r"], "tp1_pct": v["tp1"] / v["n"] * 100}
           for k, v in sorted(weeks.items(), reverse=True)]
    return out[:4]


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
            <div class="bar-fill {side}" data-w="{w:.1f}" style="background:{color}"></div>
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

    # "cancelled" = le filet de securite (cron) qui s'efface parce que la chaine principale tourne deja -
    # normal et frequent, mais illisible pour quelqu'un qui ne connait pas GitHub Actions (on dirait une
    # panne). On ne montre que ce qui a un sens a lire : en cours / reussi / echoue.
    STATUS_FR = {"success": "terminé avec succès", "failure": "échoué", "in_progress": "en cours"}
    runs_html = ""
    shown = [r for r in data["chain_runs"] if r.get("status") == "in_progress" or r.get("conclusion") in ("success", "failure")][:5]
    for r in shown:
        key = r.get("conclusion") or r.get("status")
        cls = {"success": GREEN, "failure": RED}.get(key, GOLD)
        runs_html += f'<div class="run-line"><span class="run-dot" style="background:{cls}"></span>{r["created_at"][:16].replace("T"," ")} UTC — {STATUS_FR.get(key, key)}</div>'

    links = load_links()

    def _link(url: Optional[str], icon: str, title: str, desc: str) -> str:
        if url:
            return f'<a class="link-card" href="{html.escape(str(url), quote=True)}" target="_blank"><span class="ic">{icon}</span><span><span class="t">{html.escape(title)}</span><span class="d" style="display:block">{html.escape(desc)}</span></span></a>'
        return f'<div class="link-card disabled"><span class="ic">{icon}</span><span><span class="t">{html.escape(title)}</span><span class="d" style="display:block">non configuré</span></span></div>'

    links_html = (
        _link(links.get("private_link"), "🔐", "Groupe VIP", "Ouvrir dans Telegram")
        + _link(links.get("contact_link"), "✉️", "Contact public", "@" + str(links.get("contact_link", "")).rsplit("/", 1)[-1] if links.get("contact_link") else "")
        + _link(links.get("account_link"), "💼", "Ouvrir un compte", "Lien partenaire")
    )

    weeks = weekly_recap(live_hist, now)
    if weeks:
        mx = max(abs(w["r"]) for w in weeks) or 1.0
        week_rows = "".join(
            f'''<div class="week-row"><span>{html.escape(w["label"])}</span>
              <div class="week-track"><div class="week-fill" data-w="{abs(w['r']) / mx * 46:.1f}" style="background:{GREEN if w['r'] >= 0 else RED};{'left:50%' if w['r'] >= 0 else 'right:50%'}"></div></div>
              <span style="text-align:right;font-weight:700;color:{GREEN if w['r'] >= 0 else RED}">{w['r']:+.1f} R</span></div>'''
            for w in weeks)
    else:
        week_rows = '<div class="empty">Pas encore assez de trades clôturés pour un bilan par semaine.</div>'

    return f'''
  <div class="hero">
    <svg class="hero-net" viewBox="0 0 800 280" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g>
        <line x1="60" y1="70" x2="150" y2="150"/><line x1="150" y1="150" x2="230" y2="90"/>
        <line x1="150" y1="150" x2="120" y2="230"/><line x1="230" y1="90" x2="300" y2="190"/>
        <line x1="230" y1="90" x2="380" y2="55"/><line x1="300" y1="190" x2="210" y2="250"/>
        <line x1="380" y1="55" x2="430" y2="160"/><line x1="430" y1="160" x2="500" y2="220"/>
        <line x1="430" y1="160" x2="560" y2="95"/><line x1="500" y1="220" x2="450" y2="260"/>
        <line x1="560" y1="95" x2="620" y2="175"/><line x1="560" y1="95" x2="680" y2="65"/>
        <line x1="620" y1="175" x2="640" y2="250"/><line x1="680" y1="65" x2="730" y2="150"/>
        <line x1="730" y1="150" x2="770" y2="95"/><line x1="620" y1="175" x2="500" y2="220"/>
      </g>
      <g fill="{GOLD}">
        <circle cx="60" cy="70" style="animation-delay:0s"/><circle cx="150" cy="150" style="animation-delay:.4s"/>
        <circle cx="230" cy="90" style="animation-delay:.8s"/><circle cx="120" cy="230" style="animation-delay:1.2s"/>
        <circle cx="300" cy="190" style="animation-delay:1.6s"/><circle cx="380" cy="55" style="animation-delay:2s"/>
        <circle cx="430" cy="160" style="animation-delay:2.4s"/><circle cx="560" cy="95" style="animation-delay:.6s"/>
        <circle cx="620" cy="175" style="animation-delay:1s"/><circle cx="680" cy="65" style="animation-delay:1.4s"/>
        <circle cx="730" cy="150" style="animation-delay:1.8s"/><circle cx="770" cy="95" style="animation-delay:2.2s"/>
      </g>
      <g fill="{BLUE}">
        <circle cx="210" cy="250" style="animation-delay:3s"/><circle cx="500" cy="220" style="animation-delay:.2s"/>
        <circle cx="450" cy="260" style="animation-delay:2.6s"/><circle cx="640" cy="250" style="animation-delay:1.7s"/>
      </g>
    </svg>
    <div class="hero-scan"></div>
    <div class="eyebrow">— Confluence engine · marchés FX &amp; crypto</div>
    <div class="orb-holder" id="orbHolder" aria-hidden="true">
      <svg viewBox="0 0 260 260">
        <defs>
          <radialGradient id="coreGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="{GOLD_L}"/><stop offset="55%" stop-color="{GOLD}"/><stop offset="100%" stop-color="{GOLD}" stop-opacity="0"/>
          </radialGradient>
          <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="{GOLD}"/><stop offset="100%" stop-color="{BLUE}"/>
          </linearGradient>
          <linearGradient id="sweepGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="{GOLD}" stop-opacity="0"/><stop offset="100%" stop-color="{GOLD_L}" stop-opacity=".45"/>
          </linearGradient>
        </defs>
        <g class="radar-sweep"><path d="M130,130 L250,130 L242,52 Z" fill="url(#sweepGrad)"/></g>
        <circle class="core-glow" cx="130" cy="130" r="34" fill="url(#coreGrad)"/>
        <circle cx="130" cy="130" r="14" fill="{GOLD_L}"/>
        <circle cx="130" cy="130" r="14" fill="none" stroke="{BG}" stroke-width="2"/>
        <g class="ring-mid">
          <circle cx="130" cy="130" r="78" fill="none" stroke="{BORDER}" stroke-width="1.5"/>
          <circle cx="130" cy="130" r="78" fill="none" stroke="url(#ringGrad)" stroke-width="1.5" stroke-dasharray="20 218" stroke-linecap="round"/>
          <circle cx="130" cy="52" r="3.5" fill="{BLUE}"/>
        </g>
        <g class="ring-out"><circle cx="130" cy="130" r="118" fill="none" stroke="{BORDER}" stroke-width="1" stroke-dasharray="2 7"/></g>
        <g class="particles">
          <circle cx="130" cy="12" r="2.6" fill="{GOLD}"/><circle cx="228" cy="180" r="2" fill="{GOLD_L}"/><circle cx="34" cy="180" r="2" fill="{BLUE}"/>
        </g>
      </svg>
    </div>
    <h2>L'IA qui lit le marché avant d'<span class="accent">agir</span>.</h2>
    <div class="hero-tags">
      <span class="htag"><b>28</b> paires FX</span><span class="htag">+ <b>BTC / ETH</b></span>
      <span class="htag">Analyse <b>H4</b></span><span class="htag">Cloud <b>24/7</b></span>
    </div>
  </div>

  <div class="top">
    <h1><span class="orb-mini"><svg viewBox="0 0 34 34"><circle cx="17" cy="17" r="6" fill="{GOLD}"/><g class="ring"><circle cx="17" cy="17" r="15" fill="none" stroke="{BORDER}" stroke-width="1.6"/><circle cx="17" cy="17" r="15" fill="none" stroke="{GOLD}" stroke-width="1.6" stroke-dasharray="10 84" stroke-linecap="round"/></g></svg></span>GOTA SIGNAUX</h1>
    <div class="sub">Tableau de bord local · lecture seule · actualisé toutes les 90 s</div>
    {chain_pill}{market_pill}
  </div>

  <div class="sessions-row" id="sessions"></div>

  <div class="marquee-wrap"><div class="marquee" id="marquee" data-watch="{html.escape(json.dumps(watch), quote=True)}"></div></div>

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

  <div class="panel" style="margin-bottom:16px;">
    <h2>Bilan par semaine</h2>
    {week_rows}
  </div>

  <div class="panel" style="margin-bottom:16px;">
    <h2>Accès rapides</h2>
    <div class="links-row">{links_html}</div>
  </div>

  <div class="panel" style="margin-bottom:16px;">
    <h2>Comment le robot décide</h2>
    <div class="method">
      <svg viewBox="-48 -8 416 336" style="width:100%;max-width:300px;margin:0 auto;display:block">
        <polygon points="160,26 274,93 274,227 160,294 46,227 46,93" fill="none" stroke="{BORDER}" stroke-width="1.4"/>
        <polygon points="160,70 236,113 236,207 160,250 84,207 84,113" fill="{GOLD}0d" stroke="{GOLD}59" stroke-width="1"/>
        <g font-family="IBM Plex Mono, monospace" font-size="10.5" fill="{MUTED}" text-anchor="middle">
          <circle cx="160" cy="26" r="4.5" fill="{GOLD}"/><text x="160" y="12">LIQUIDITÉ</text>
          <circle cx="274" cy="93" r="4.5" fill="{GOLD}"/><text x="288" y="88" text-anchor="start">OB+FVG</text>
          <circle cx="274" cy="227" r="4.5" fill="{BLUE}"/><text x="288" y="232" text-anchor="start">VOLUME</text>
          <circle cx="160" cy="294" r="4.5" fill="{BLUE}"/><text x="160" y="311">RSI</text>
          <circle cx="46" cy="227" r="4.5" fill="{GOLD}"/><text x="32" y="232" text-anchor="end">DISCOUNT</text>
          <circle cx="46" cy="93" r="4.5" fill="{GOLD}"/><text x="32" y="88" text-anchor="end">SESSION</text>
        </g>
        <text x="160" y="164" text-anchor="middle" font-family="Sora" font-size="15" fill="{TXT}" font-weight="700">SCORE</text>
        <text x="160" y="182" text-anchor="middle" font-family="IBM Plex Mono" font-size="12" fill="{GOLD}">4 / 7 minimum</text>
      </svg>
      <div class="method-checks">
        <div class="mrow"><span class="n">01</span>Balayage de liquidité<span class="vtag aide" title="Backtest : résultat moyen -0,088R avec, -0,106R sans">Aide</span></div>
        <div class="mrow"><span class="n">02</span>Zone empilée OB + FVG<span class="vtag neutre" title="Backtest : résultat moyen -0,098R avec, -0,085R sans">Neutre</span></div>
        <div class="mrow"><span class="n">03</span>Volume Profile (POC)<span class="vtag freine" title="Backtest : résultat moyen -0,185R avec, -0,085R sans — mais peu de vraies données de volume sur le forex">Peu fiable</span></div>
        <div class="mrow"><span class="n">04</span>RSI en retournement<span class="vtag aide" title="Backtest : résultat moyen -0,085R avec, -0,138R sans">Aide</span></div>
        <div class="mrow"><span class="n">05</span>Zone Discount / Premium<span class="vtag neutre" title="Backtest : résultat moyen -0,091R avec, -0,089R sans">Neutre</span></div>
        <div class="mrow"><span class="n">06</span>Session Londres / New York<span class="vtag freine" title="Backtest : résultat moyen -0,104R avec, -0,047R sans">Freine</span></div>
        <div class="mrow"><span class="n">07</span>Kill zone (ouverture Londres/NY)<span class="vtag aide" title="Backtest : résultat moyen -0,087R avec, -0,127R sans">Aide</span></div>
      </div>
      <div class="method-note">Étiquette = ce que dit le dernier backtest complet (résultat moyen par trade avec vs sans chaque
      confirmation) — indicatif, pas une certitude définitive, surtout sur les échantillons les plus petits. Le Volume
      Profile manque de vraies données de volume sur la plupart des paires FX (source Yahoo) : peu fiable pour l'instant.</div>
    </div>
  </div>

  <div class="foot">Généré le {now.strftime('%d/%m/%Y à %H:%M:%S UTC')} · données publiques du dépôt {REPO} · aucun ordre n'est passé depuis cette page</div>
'''


def render_shell() -> str:
    """Page servie INSTANTANEMENT sur / (aucun appel reseau) : ecran de demarrage GOTA, puis le contenu reel
    (route /data) vient s'y glisser des qu'il est pret. Ne peut pas echouer : garantit qu'un clic sur l'icone
    affiche toujours quelque chose tout de suite, meme si GitHub est lent ou injoignable.
    L'onglet "Marchés en direct" (TradingView) vit ICI, dans la coquille statique, et pas dans le fragment
    /data qui est remplacé toutes les 90 s : sinon le graphique en direct serait détruit et rechargé sans arrêt."""
    symbol_options = "".join(f'<option value="{_tv_symbol(s)}">{s}</option>' for s in METAL_PAIRS + FX_PAIRS + CRYPTO_PAIRS)
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GOTA Signaux — Tableau de bord</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin:0; color:{TXT}; font-family: "Sora", -apple-system, "Segoe UI", Roboto, sans-serif;
    background:
      radial-gradient(ellipse 900px 460px at 50% 0, {GOLD}17, transparent 60%) fixed,
      radial-gradient(ellipse 700px 460px at 100% 6%, {BLUE}0d, transparent 60%) fixed,
      linear-gradient({PANEL2} 1px, transparent 1px) 0 0/46px 46px,
      linear-gradient(90deg, {PANEL2} 1px, transparent 1px) 0 0/46px 46px,
      {BG};
  }}
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
  @keyframes spin-rev {{ to {{ transform:rotate(-360deg); }} }}
  @keyframes pulse {{ 0%,100% {{ opacity:.55; transform:scale(1); }} 50% {{ opacity:1; transform:scale(1.08); }} }}
  @media (prefers-reduced-motion: reduce) {{ .splash-ring, .splash-dots span {{ animation:none !important; }} }}
  /* ---------- entete (meme mark que la page de presentation) ---------- */
  .hero {{ position:relative; text-align:center; padding:26px 0 34px; overflow:hidden; }}
  .hero > * {{ position:relative; z-index:2; }}
  .hero .eyebrow {{ font-family:"IBM Plex Mono",monospace; text-transform:uppercase; letter-spacing:.16em; font-size:10.5px; color:{BLUE}; margin-bottom:14px; }}
  .orb-holder {{ width:130px; height:130px; position:relative; margin:0 auto; transition:transform .3s ease-out; }}
  .orb-holder svg {{ width:100%; height:100%; overflow:visible; }}
  .ring-out {{ transform-origin:130px 130px; animation:spin 22s linear infinite; }}
  .ring-mid {{ transform-origin:130px 130px; animation:spin-rev 16s linear infinite; }}
  .particles {{ transform-origin:130px 130px; animation:spin 10s linear infinite; }}
  .core-glow {{ animation:pulse 3.2s ease-in-out infinite; }}
  .radar-sweep {{ transform-origin:130px 130px; animation:spin 3.6s linear infinite; }}
  @media (prefers-reduced-motion: reduce) {{ .ring-out, .ring-mid, .particles, .core-glow, .radar-sweep {{ animation:none !important; }} }}
  .hero h2 {{ font-size:clamp(24px,4vw,34px); font-weight:800; letter-spacing:.005em; margin:16px 0 8px;
    animation:hero-reveal 1s cubic-bezier(.16,.84,.44,1) both .1s; }}
  .hero h2 .accent {{ color:{GOLD}; }}
  @keyframes hero-reveal {{ from {{ clip-path:inset(0 100% 0 0); opacity:0; }} to {{ clip-path:inset(0 -10% 0 0); opacity:1; }} }}
  /* ---------- reseau neuronal anime en fond du hero ---------- */
  .hero-net {{ position:absolute; inset:-20px -4% auto -4%; height:280px; z-index:0; opacity:.6; pointer-events:none; }}
  .hero-net circle {{ animation:net-pulse 4.5s ease-in-out infinite; }}
  .hero-net line {{ animation:net-line 4.5s ease-in-out infinite; stroke:{BORDER}; stroke-width:1; }}
  @keyframes net-pulse {{ 0%,100% {{ opacity:.2; r:2; }} 50% {{ opacity:.9; r:3; }} }}
  @keyframes net-line {{ 0%,100% {{ opacity:.05; }} 50% {{ opacity:.35; }} }}
  .hero-scan {{ position:absolute; inset:0; z-index:1; pointer-events:none; opacity:.4; mix-blend-mode:screen;
    background:repeating-linear-gradient(180deg, {GOLD}08 0px, {GOLD}08 1px, transparent 1px, transparent 4px);
    animation:scan-move 5s linear infinite; }}
  @keyframes scan-move {{ from {{ background-position-y:0; }} to {{ background-position-y:80px; }} }}
  @media (prefers-reduced-motion: reduce) {{
    .hero h2 {{ animation:none !important; clip-path:none !important; opacity:1 !important; }}
    .hero-net circle, .hero-net line, .hero-scan {{ animation:none !important; }}
  }}
  .hero-tags {{ display:flex; gap:9px; flex-wrap:wrap; justify-content:center; margin-top:4px; }}
  .htag {{ font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.04em; color:{MUTED}; border:1px solid {BORDER}; border-radius:14px; padding:5px 11px; background:{PANEL}; }}
  .htag b {{ color:{TXT}; font-weight:500; }}
  /* ---------- onglets ---------- */
  .tabs {{ display:flex; gap:8px; margin-bottom:26px; border-bottom:1px solid {BORDER}; padding-bottom:0; }}
  .tab-btn {{
    font-family:"Sora",sans-serif; font-weight:600; font-size:13px; color:{MUTED}; background:none; border:none;
    padding:12px 4px; margin-right:22px; cursor:pointer; position:relative; display:flex; align-items:center; gap:7px;
  }}
  .tab-btn .ic {{ font-size:14px; }}
  .tab-btn.active {{ color:{TXT}; }}
  .tab-btn.active::after {{ content:""; position:absolute; left:0; right:0; bottom:-1px; height:2px; background:{GOLD}; border-radius:2px; }}
  .tab-btn:hover {{ color:{TXT}; }}
  .tabpanel[hidden] {{ display:none; }}
  /* ---------- marches en direct (TradingView) ---------- */
  .mk-toolbar {{ display:flex; align-items:center; gap:12px; margin-bottom:14px; flex-wrap:wrap; }}
  .mk-select {{
    background:{PANEL}; color:{TXT}; border:1px solid {BORDER}; border-radius:10px; padding:9px 14px;
    font-family:"IBM Plex Mono",monospace; font-size:12.5px; font-weight:500;
  }}
  .mk-select:focus {{ outline:1px solid {GOLD}; }}
  .mk-note {{ color:{DIM}; font-size:11px; }}
  .tv-ticker {{ margin-bottom:16px; border:1px solid {BORDER}; border-radius:12px; overflow:hidden; background:{PANEL}; }}
  .tv-chart {{ border:1px solid {BORDER}; border-radius:14px; overflow:hidden; background:{PANEL}; height:520px; position:relative; }}
  .tv-chart > div {{ position:absolute; inset:0; }}
  .tv-chart iframe {{ border:none !important; width:100% !important; height:100% !important; }}
  .tv-chart.mk-maxed {{
    position:fixed; inset:18px; z-index:1000; height:auto; box-shadow:0 20px 60px #000a;
  }}
  .mk-expand {{
    position:absolute; top:10px; right:10px; z-index:5; width:30px; height:30px; border-radius:8px;
    border:1px solid {BORDER}; background:{BG}cc; color:{TXT}; cursor:pointer; font-size:14px;
    display:flex; align-items:center; justify-content:center; backdrop-filter:blur(4px); transition:background .15s ease;
  }}
  .mk-expand:hover {{ background:{PANEL2}; border-color:{GOLD}88; }}
  .mk-layout {{ display:grid; grid-template-columns:1fr 290px; gap:16px; align-items:start; margin-bottom:16px; }}
  @media (max-width: 900px) {{ .mk-layout {{ grid-template-columns:1fr; }} }}
  /* ---------- cotations (liste des paires, style TradingView) ---------- */
  .quotes-group {{ margin-bottom:14px; }}
  .quotes-label {{ font-family:"IBM Plex Mono",monospace; font-size:11px; letter-spacing:.06em; color:{DIM}; text-transform:uppercase; margin-bottom:6px; }}
  .tv-quotes {{ border:1px solid {BORDER}; border-radius:12px; overflow:hidden; background:{PANEL}; }}
  /* ---------- panneau ordre (illustratif - voir mk-note) ---------- */
  .order-panel {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:14px; padding:18px; display:flex; flex-direction:column; gap:14px; }}
  .op-head {{ display:flex; align-items:center; gap:8px; }}
  .op-ai-dot {{ width:7px; height:7px; border-radius:50%; background:{GOLD}; box-shadow:0 0 7px {GOLD}; animation:blink 1.8s ease-in-out infinite; flex:none; }}
  .op-ai-label {{ font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.06em; color:{MUTED}; text-transform:uppercase; }}
  .op-symbol {{ margin-left:auto; font-family:"Sora",sans-serif; font-weight:700; font-size:13.5px; color:{TXT}; }}
  .op-side-toggle {{ display:grid; grid-template-columns:1fr 1fr; background:{PANEL2}; border-radius:10px; padding:3px; gap:3px; }}
  .op-side {{ font-family:"Sora",sans-serif; font-weight:700; font-size:12px; letter-spacing:.03em; padding:9px 0; border:none; border-radius:8px; background:none; color:{MUTED}; cursor:pointer; transition:all .2s ease; }}
  .op-side.active[data-side="buy"] {{ background:{GREEN}22; color:{GREEN}; box-shadow:inset 0 0 0 1px {GREEN}55; }}
  .op-side.active[data-side="sell"] {{ background:{RED}22; color:{RED}; box-shadow:inset 0 0 0 1px {RED}55; }}
  .op-field label {{ display:block; font-size:10.5px; color:{DIM}; margin-bottom:6px; text-transform:uppercase; letter-spacing:.04em; }}
  .op-stepper {{ display:flex; align-items:center; justify-content:space-between; background:{PANEL2}; border:1px solid {BORDER}; border-radius:10px; padding:6px; }}
  .op-step {{ width:28px; height:28px; border-radius:7px; border:1px solid {BORDER}; background:{PANEL}; color:{TXT}; font-size:15px; cursor:pointer; line-height:1; transition:background .15s ease; }}
  .op-step:hover {{ background:{BORDER}; }}
  .op-vol {{ font-family:"IBM Plex Mono",monospace; font-size:14px; font-weight:600; color:{TXT}; }}
  .op-quick-row {{ display:flex; gap:8px; }}
  .op-quick {{ flex:1; font-family:"IBM Plex Mono",monospace; font-size:10.5px; padding:7px 0; border-radius:8px; border:1px solid {BORDER}; background:{PANEL2}; color:{MUTED}; cursor:pointer; transition:all .15s ease; text-align:center; }}
  .op-quick.active {{ border-color:{GOLD}88; color:{GOLD}; background:{GOLD}14; }}
  .op-submit {{ position:relative; overflow:hidden; border:none; border-radius:11px; padding:14px 0; font-family:"Sora",sans-serif; font-weight:700; font-size:13.5px; letter-spacing:.02em; color:#06110b; cursor:pointer; transition:transform .12s ease, box-shadow .25s ease; }}
  .op-submit[data-side="buy"] {{ background:linear-gradient(135deg,{GREEN},#1fae63); box-shadow:0 6px 20px {GREEN}40; }}
  .op-submit[data-side="sell"] {{ background:linear-gradient(135deg,{RED},#c23434); box-shadow:0 6px 20px {RED}40; color:#1a0505; }}
  .op-submit:active {{ transform:scale(.97); }}
  .op-submit.op-done {{ background:linear-gradient(135deg,{BLUE},#1f7fae) !important; box-shadow:0 6px 20px {BLUE}40; color:#04222f; }}
  .op-note {{ color:{DIM}; font-size:10.5px; line-height:1.5; text-align:center; }}
  .top {{ display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:22px; }}
  .top h1 {{ font-size:22px; margin:0; letter-spacing:.5px; font-weight:700; display:flex; align-items:center; gap:10px; }}
  .top .sub {{ color:{MUTED}; font-size:12.5px; font-family:"IBM Plex Mono",monospace; }}
  .orb-mini {{ position:relative; width:34px; height:34px; flex:none; }}
  .orb-mini svg {{ width:100%; height:100%; overflow:visible; }}
  .orb-mini .ring {{ transform-origin:17px 17px; animation:spin 9s linear infinite; }}
  @media (prefers-reduced-motion: reduce) {{ .orb-mini .ring {{ animation:none !important; }} }}
  .marquee-wrap {{ overflow:hidden; border-top:1px solid {BORDER}; border-bottom:1px solid {BORDER}; padding:11px 0; margin-bottom:18px; }}
  .marquee {{ display:flex; gap:28px; width:max-content; animation:scroll-left 30s linear infinite; }}
  .marquee span {{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:{MUTED}; white-space:nowrap; }}
  .marquee span::before {{ content:"◆"; color:{BLUE}; margin-right:7px; font-size:7px; vertical-align:middle; }}
  @keyframes scroll-left {{ from {{ transform:translateX(0); }} to {{ transform:translateX(-50%); }} }}
  @media (prefers-reduced-motion: reduce) {{ .marquee {{ animation:none !important; }} }}
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
  .bar-fill {{ position:absolute; top:0; bottom:0; border-radius:4px; width:0; transition:width 1s cubic-bezier(.16,.84,.44,1); }}
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
  /* ---------- liens rapides ---------- */
  .links-row {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
  .link-card {{
    display:flex; align-items:center; gap:12px; background:{PANEL2}; border:1px solid {BORDER}; border-radius:12px;
    padding:14px 16px; text-decoration:none; color:{TXT}; transition:border-color .2s,transform .2s;
  }}
  .link-card:hover {{ border-color:{GOLD}66; transform:translateY(-2px); }}
  .link-card .ic {{ width:36px; height:36px; border-radius:9px; display:flex; align-items:center; justify-content:center; font-size:17px; flex:none; background:{PANEL}; border:1px solid {BORDER}; }}
  .link-card .t {{ font-weight:600; font-size:13px; }}
  .link-card .d {{ color:{MUTED}; font-size:11px; margin-top:1px; }}
  .link-card.disabled {{ opacity:.45; pointer-events:none; }}
  /* ---------- methode (hexagone des confirmations) ---------- */
  .method {{ display:grid; grid-template-columns:1fr 1.2fr; gap:26px; align-items:center; }}
  .method-checks {{ display:flex; flex-direction:column; }}
  .mrow {{ display:flex; align-items:center; gap:12px; padding:9px 2px; border-bottom:1px solid {BORDER}; font-size:12.5px; }}
  .vtag {{ font-family:"IBM Plex Mono",monospace; font-size:9.5px; font-weight:700; letter-spacing:.03em; padding:3px 8px; border-radius:10px; margin-left:auto; white-space:nowrap; }}
  .vtag.aide {{ color:{GREEN}; background:{GREEN}1a; }}
  .vtag.neutre {{ color:{MUTED}; background:{PANEL2}; }}
  .vtag.freine {{ color:{RED}; background:{RED}1a; }}
  .method-note {{ color:{DIM}; font-size:10px; line-height:1.5; margin-top:12px; }}
  .mrow:last-child {{ border-bottom:none; }}
  .mrow .n {{ font-family:"IBM Plex Mono",monospace; color:{GOLD}; font-size:11px; width:16px; flex:none; }}
  /* ---------- bilan hebdo ---------- */
  .week-row {{ display:grid; grid-template-columns:1fr 2fr 70px; align-items:center; gap:12px; font-size:12.5px; padding:8px 0; }}
  .week-track {{ position:relative; height:7px; background:{PANEL2}; border-radius:4px; overflow:hidden; }}
  .week-fill {{ position:absolute; top:0; bottom:0; left:50%; border-radius:4px; width:0; transition:width 1s cubic-bezier(.16,.84,.44,1); }}
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

<div class="wrap">
  <div class="tabs">
    <button class="tab-btn active" data-tab="overview"><span class="ic">📊</span>Vue d'ensemble</button>
    <button class="tab-btn" data-tab="markets"><span class="ic">📈</span>Marchés en direct</button>
  </div>

  <div id="tab-overview" class="tabpanel"><div id="app"></div></div>

  <div id="tab-markets" class="tabpanel" hidden>
    <div class="mk-toolbar">
      <select class="mk-select" id="mkSymbol">{symbol_options}</select>
      <span class="mk-note">Graphique en direct, fourni par TradingView — pour situer le marché, pas pour trader depuis cette page.</span>
    </div>
    <div class="tv-ticker" id="mkTicker"></div>
    <div class="mk-layout">
      <div class="tv-chart" id="mkChartWrap">
        <div id="mkChart"></div>
        <button class="mk-expand" id="mkExpand" type="button" title="Agrandir le graphique">⛶</button>
      </div>
      <div class="order-panel">
        <div class="op-head">
          <span class="op-ai-dot"></span>
          <span class="op-ai-label">IA · Prête</span>
          <span class="op-symbol" id="opSymbol">—</span>
        </div>
        <div class="op-side-toggle">
          <button class="op-side active" data-side="buy" id="opBuy" type="button">ACHAT</button>
          <button class="op-side" data-side="sell" id="opSell" type="button">VENTE</button>
        </div>
        <div class="op-field">
          <label>Volume (lots)</label>
          <div class="op-stepper">
            <button class="op-step" id="opDec" type="button">−</button>
            <span class="op-vol" id="opVol">0.10</span>
            <button class="op-step" id="opInc" type="button">+</button>
          </div>
        </div>
        <div class="op-quick-row">
          <button class="op-quick active" id="opSl" type="button">SL −0.5%</button>
          <button class="op-quick active" id="opTp" type="button">TP +1%</button>
        </div>
        <button class="op-submit" id="opSubmit" data-side="buy" type="button">
          <span id="opSubmitLabel">Passer l'ordre (ACHAT)</span>
        </button>
        <div class="op-note">Panneau illustratif — non connecté à un compte réel, aucun ordre n'est envoyé depuis cette page.</div>
      </div>
    </div>
    <div class="quotes-group">
      <div class="quotes-label">Forex</div>
      <div class="tv-quotes" id="mkQuotesForex"></div>
    </div>
    <div class="quotes-group">
      <div class="quotes-label">Métaux</div>
      <div class="tv-quotes" id="mkQuotesMetaux"></div>
    </div>
    <div class="quotes-group">
      <div class="quotes-label">Crypto</div>
      <div class="tv-quotes" id="mkQuotesCrypto"></div>
    </div>
  </div>
</div>

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

  function renderMarquee() {{
    var box = document.getElementById('marquee');
    if (!box) return;
    var watch = [];
    try {{ watch = JSON.parse(box.getAttribute('data-watch') || '[]'); }} catch (e) {{}}
    var items = watch.length ? watch.concat(watch) : ['Aucune zone particulière surveillée en ce moment'];
    box.innerHTML = items.map(function(w){{ return '<span>' + w + '</span>'; }}).join('');
  }}

  function animateChart() {{
    var path = document.querySelector('.chart path');
    if (!path) return;
    if (reduceMotion) return;
    try {{
      var len = path.getTotalLength();
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;
      path.getBoundingClientRect();
      path.style.transition = 'stroke-dashoffset 1.3s cubic-bezier(.16,.84,.44,1)';
      requestAnimationFrame(function(){{ path.style.strokeDashoffset = '0'; }});
      document.querySelectorAll('.chart circle[cx]').forEach(function(c, i) {{
        c.style.opacity = 0; c.style.transition = 'opacity .3s ease ' + (0.3 + i * 0.05) + 's';
        requestAnimationFrame(function(){{ c.style.opacity = 1; }});
      }});
    }} catch (e) {{}}
  }}

  function animateBars() {{
    var bars = document.querySelectorAll('.bar-fill[data-w], .week-fill[data-w]');
    bars.forEach(function(el, i) {{
      var w = el.getAttribute('data-w');
      setTimeout(function(){{ el.style.width = w + '%'; }}, reduceMotion ? 0 : i * 90);
    }});
  }}

  // ---- onglets + graphiques marche en direct (TradingView) : vivent HORS de #app, donc jamais
  // detruits par refreshData() toutes les 90s - le graphique reste stable pendant qu'on le regarde ----
  var marketsLoaded = false;
  function mountTicker(elId, symbols, displayMode) {{
    var box = document.getElementById(elId);
    if (!box) return;
    box.innerHTML = '<div class="tradingview-widget-container__widget"></div>';
    var s = document.createElement('script');
    s.type = 'text/javascript';
    s.src = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js';
    s.async = true;
    s.text = JSON.stringify({{ symbols: symbols, showSymbolLogo: true, isTransparent: false,
      displayMode: displayMode || 'adaptive', colorTheme: 'dark', locale: 'fr', backgroundColor: '{PANEL}' }});
    box.appendChild(s);
  }}
  function buildTicker() {{
    var opts = document.querySelectorAll('#mkSymbol option');
    var symbols = Array.prototype.map.call(opts, function(o) {{ return {{ proName: o.value, title: o.textContent }}; }});
    mountTicker('mkTicker', symbols, 'adaptive');
  }}
  function loadChart(symbol) {{
    var box = document.getElementById('mkChart');
    if (!box || !symbol) return;
    box.innerHTML = '<div class="tradingview-widget-container__widget"></div>';
    var s = document.createElement('script');
    s.type = 'text/javascript';
    s.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    s.async = true;
    s.text = JSON.stringify({{ autosize: true, symbol: symbol, interval: '240', timezone: 'Etc/UTC',
      theme: 'dark', style: '1', locale: 'fr', hide_top_toolbar: false, hide_legend: false,
      hide_side_toolbar: false, allow_symbol_change: false, withdateranges: true,
      backgroundColor: '{BG}', gridColor: '{BORDER}' }});
    box.appendChild(s);
  }}
  function buildQuotes() {{
    // Meme widget "bandeau" que celui du haut (deja fiable), juste regroupe par categorie et
    // empile a la verticale au lieu d'un seul long defilement - evite le widget "tableau" separe
    // qui pouvait s'afficher sans theme (fond blanc, pas de noms) selon le navigateur.
    var opts = document.querySelectorAll('#mkSymbol option');
    var groups = {{ Forex: [], 'Métaux': [], Crypto: [] }};
    Array.prototype.forEach.call(opts, function(o) {{
      var g = o.value.indexOf('OANDA:') === 0 ? 'Métaux' : (o.value.indexOf('COINBASE:') === 0 ? 'Crypto' : 'Forex');
      groups[g].push({{ proName: o.value, title: o.textContent }});
    }});
    mountTicker('mkQuotesForex', groups.Forex, 'regular');
    mountTicker('mkQuotesMetaux', groups['Métaux'], 'regular');
    mountTicker('mkQuotesCrypto', groups.Crypto, 'regular');
  }}
  function ensureMarketsLoaded() {{
    if (marketsLoaded) return;
    marketsLoaded = true;
    buildTicker();
    buildQuotes();
    loadChart(document.getElementById('mkSymbol').value);
  }}
  function initChartExpand() {{
    var btn = document.getElementById('mkExpand'), wrap = document.getElementById('mkChartWrap');
    if (!btn || !wrap) return;
    function setMaxed(on) {{
      wrap.classList.toggle('mk-maxed', on);
      btn.textContent = on ? '✕' : '⛶';
      btn.title = on ? 'Réduire le graphique' : 'Agrandir le graphique';
    }}
    btn.addEventListener('click', function() {{ setMaxed(!wrap.classList.contains('mk-maxed')); }});
    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape' && wrap.classList.contains('mk-maxed')) setMaxed(false);
    }});
  }}
  function switchTab(name) {{
    document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.toggle('active', b.dataset.tab === name); }});
    document.getElementById('tab-overview').hidden = name !== 'overview';
    document.getElementById('tab-markets').hidden = name !== 'markets';
    if (name === 'markets') ensureMarketsLoaded();
  }}
  function syncOpSymbol() {{
    var sel = document.getElementById('mkSymbol'), lbl = document.getElementById('opSymbol');
    if (sel && lbl) lbl.textContent = sel.options[sel.selectedIndex].textContent;
  }}
  function initTabs() {{
    document.querySelectorAll('.tab-btn').forEach(function(b) {{
      b.addEventListener('click', function() {{ switchTab(b.dataset.tab); }});
    }});
    var sel = document.getElementById('mkSymbol');
    if (sel) sel.addEventListener('change', function() {{ if (marketsLoaded) loadChart(sel.value); syncOpSymbol(); }});
    syncOpSymbol();
  }}

  // ---- panneau "ordre" de l'onglet Marches en direct : purement illustratif (voir .op-note),
  // aucune connexion reelle, aucun ordre envoye - juste pour l'ambiance "terminal de trading" ----
  function initOrderPanel() {{
    var vol = 0.10, side = 'buy';
    var volEl = document.getElementById('opVol'), submit = document.getElementById('opSubmit'),
        label = document.getElementById('opSubmitLabel'), buyBtn = document.getElementById('opBuy'),
        sellBtn = document.getElementById('opSell');
    if (!submit) return;
    function renderSide() {{
      buyBtn.classList.toggle('active', side === 'buy');
      sellBtn.classList.toggle('active', side === 'sell');
      submit.dataset.side = side;
      label.textContent = "Passer l'ordre (" + (side === 'buy' ? 'ACHAT' : 'VENTE') + ')';
    }}
    buyBtn.addEventListener('click', function() {{ side = 'buy'; renderSide(); }});
    sellBtn.addEventListener('click', function() {{ side = 'sell'; renderSide(); }});
    document.getElementById('opInc').addEventListener('click', function() {{
      vol = Math.min(5, +(vol + 0.01).toFixed(2)); volEl.textContent = vol.toFixed(2);
    }});
    document.getElementById('opDec').addEventListener('click', function() {{
      vol = Math.max(0.01, +(vol - 0.01).toFixed(2)); volEl.textContent = vol.toFixed(2);
    }});
    ['opSl', 'opTp'].forEach(function(id) {{
      document.getElementById(id).addEventListener('click', function() {{ this.classList.toggle('active'); }});
    }});
    submit.addEventListener('click', function() {{
      if (submit.classList.contains('op-done')) return;
      submit.classList.add('op-done');
      var prev = label.textContent;
      label.textContent = 'Ordre simulé ✓';
      setTimeout(function() {{ submit.classList.remove('op-done'); label.textContent = prev; }}, 1800);
    }});
    renderSide();
  }}

  function initHeroParallax() {{
    var hero = document.querySelector('.hero'), orb = document.getElementById('orbHolder');
    if (!hero || !orb || reduceMotion) return;
    hero.addEventListener('mousemove', function(e) {{
      var r = hero.getBoundingClientRect();
      var dx = (e.clientX - (r.left + r.width / 2)) / r.width;
      var dy = (e.clientY - (r.top + r.height / 2)) / r.height;
      orb.style.transform = 'translate(' + (dx * 14).toFixed(1) + 'px,' + (dy * 14).toFixed(1) + 'px)';
    }});
    hero.addEventListener('mouseleave', function() {{ orb.style.transform = 'translate(0,0)'; }});
  }}
  function initDashboard() {{
    initHeroParallax();
    drawSessions();
    setInterval(drawSessions, 30000);
    renderMarquee();
    document.querySelectorAll('.cu').forEach(countUp);
    animateChart();
    animateBars();
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
  initTabs();
  initOrderPanel();
  initChartExpand();
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
