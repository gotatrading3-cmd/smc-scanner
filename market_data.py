"""
GOTA TRADING - Donnees marche live.

Sources gratuites sans cle :
- News : Reddit r/cryptocurrency (hot posts)
- Fear & Greed : alternative.me
- Sessions forex calculees en local

Cache disque 5 min pour eviter trop d'appels API.
"""
from __future__ import annotations
import json
import urllib.request
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_CACHE_DIR = Path(__file__).parent / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)
NEWS_CACHE_TTL = 600   # 10 min
FNG_CACHE_TTL = 1800   # 30 min


def _cached_fetch(url: str, cache_name: str, ttl: int) -> dict | list | None:
    cache_file = _CACHE_DIR / cache_name
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < ttl:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GOTA-Trading/1.0 (by /u/gota_trader)"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            data = json.loads(raw)
            cache_file.write_bytes(raw)
            return data
    except Exception as e:
        print(f"  [market_data] erreur fetch : {e}")
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None


def get_crypto_news(limit: int = 10) -> list:
    """News crypto via Reddit r/cryptocurrency hot posts."""
    url = f"https://www.reddit.com/r/cryptocurrency/hot.json?limit={limit*2}"
    data = _cached_fetch(url, "news_reddit.json", NEWS_CACHE_TTL)
    if not data or "data" not in data:
        return []
    result = []
    for post in data["data"].get("children", []):
        p = post.get("data", {})
        if p.get("stickied"):  # skip pinned posts
            continue
        result.append({
            "title": p.get("title", "")[:140],
            "source": "r/cryptocurrency",
            "url": "https://reddit.com" + p.get("permalink", "#"),
            "ts": int(p.get("created_utc", 0)),
            "score": p.get("score", 0),
            "comments": p.get("num_comments", 0),
            "flair": p.get("link_flair_text", "") or "",
        })
        if len(result) >= limit:
            break
    return result


def get_finance_news(limit: int = 5) -> list:
    """Bonus : Reddit r/wallstreetbets ou r/investing pour news finance generale."""
    url = f"https://www.reddit.com/r/finance/hot.json?limit={limit*2}"
    data = _cached_fetch(url, "news_finance.json", NEWS_CACHE_TTL)
    if not data or "data" not in data:
        return []
    result = []
    for post in data["data"].get("children", []):
        p = post.get("data", {})
        if p.get("stickied"):
            continue
        result.append({
            "title": p.get("title", "")[:140],
            "source": "r/finance",
            "url": "https://reddit.com" + p.get("permalink", "#"),
            "ts": int(p.get("created_utc", 0)),
            "score": p.get("score", 0),
        })
        if len(result) >= limit:
            break
    return result


def get_fear_greed() -> dict | None:
    """Fear & Greed Index crypto (0-100). Alternative.me, gratuit."""
    data = _cached_fetch("https://api.alternative.me/fng/?limit=1", "fng.json", FNG_CACHE_TTL)
    if not data or "data" not in data:
        return None
    d = data["data"][0]
    val = int(d.get("value", 50))
    label = d.get("value_classification", "Neutral")
    fr = {
        "Extreme Fear": "Peur Extreme",
        "Fear": "Peur",
        "Neutral": "Neutre",
        "Greed": "Avidite",
        "Extreme Greed": "Avidite Extreme",
    }.get(label, label)
    color = "#f85149" if val < 30 else "#fbbf24" if val < 55 else "#fbbf24" if val < 75 else "#3fb950"
    if val < 25:
        color = "#dc2626"
    elif val > 75:
        color = "#22c55e"
    return {"value": val, "label": fr, "color": color}


# Sessions forex UTC (approx, non-stricte)
SESSIONS = [
    ("Sydney",   "🇦🇺", 22, 7),
    ("Tokyo",    "🇯🇵", 0,  9),
    ("Londres",  "🇬🇧", 8,  17),
    ("New York", "🇺🇸", 13, 22),
]


def get_sessions_status() -> list:
    """Retourne l'etat de chaque session (active/inactive) avec heures."""
    now = datetime.now(timezone.utc)
    h = now.hour
    wd = now.weekday()
    weekend = (wd == 5) or (wd == 6 and h < 22) or (wd == 4 and h >= 22)
    result = []
    for name, flag, start, end in SESSIONS:
        if weekend:
            active = False
        elif start < end:
            active = start <= h < end
        else:
            active = h >= start or h < end
        result.append({
            "name": name, "flag": flag,
            "start": start, "end": end,
            "active": active and not weekend,
        })
    return result


def get_market_overview() -> dict:
    """Synthese marche complete pour le dashboard."""
    return {
        "fng": get_fear_greed(),
        "sessions": get_sessions_status(),
        "now_utc": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "now_date": datetime.now(timezone.utc).strftime("%A %d %b %Y"),
    }


def format_age(ts: int) -> str:
    """Convertit un timestamp unix en age relatif ('il y a 2h')."""
    if not ts:
        return ""
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)
    s = delta.total_seconds()
    if s < 60:
        return f"il y a {int(s)}s"
    if s < 3600:
        return f"il y a {int(s/60)} min"
    if s < 86400:
        return f"il y a {int(s/3600)}h"
    return f"il y a {int(s/86400)}j"
