"""
news_runner.py - Actualites economiques en temps reel pour le canal Telegram "Info du monde".

Ce que fait le bot, a chaque cycle (toutes les ~60 s dans le cloud, voir .github/workflows/news.yml) :
  1. lit les flux RSS/Atom de news_feeds.json (agences, journaux, banques centrales, videos) ;
  2. poste chaque NOUVEL article : titre + court extrait fourni par le flux + lien vers la source
     (Telegram affiche l'image / la video de la page via l'apercu du lien - on ne copie ni n'heberge rien) ;
  3. met en avant les chiffres publies par les agences ("X vs Y attendu") et les annonces de banques centrales ;
  4. poste le calendrier economique (agenda, alerte 15 min avant, publication) via news_calendar.py.

Regles : jamais le texte complet d'un article ; la source est toujours nommee et liee ; les doublons entre sources
sont ecartes ; une limite quotidienne evite d'inonder le canal ; au premier lancement on ne vide pas tout l'historique.
Ce script ENVOIE seulement : il ne lit jamais les messages entrants (getUpdates) pour ne pas perturber le bot de signaux.

MODE APERCU PAR DEFAUT : tant que NEWS_LIVE n'est pas "true" (ou news_live dans channel.json en local), tout part dans
le chat PRIVE du proprietaire, marque "APERCU", et rien n'est publie dans le canal.

Usage :
    python news_runner.py --once               # un cycle (celui qu'appelle le workflow)
    python news_runner.py --dry-run            # montre ce qui serait poste, n'envoie ni n'ecrit rien
    python news_runner.py --check-feeds        # teste chaque source (statut, nb d'articles, fraicheur)
    python news_runner.py --preview 5          # envoie 5 articles + exemples de calendrier dans TON chat prive
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import html
import html.entities
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars(r"%APPDATA%\Python\Python312\site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)

import requests  # noqa: E402

import news_calendar as cal  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DIR = Path(__file__).parent
FEEDS_FILE = Path(os.environ.get("NEWS_FEEDS_FILE") or (DIR / "news_feeds.json"))
DATA_DIR = Path(os.environ.get("NEWS_DATA_DIR") or (DIR / "news_state_local"))
STATE_FILE = DATA_DIR / "news_state.json"
CACHE_FILE = Path(os.environ.get("NEWS_CACHE_FILE") or (DIR / "news_cache_local.json"))
UA = "GOTA-NewsBot/1.0 (+https://github.com/gotatrading3-cmd/smc-scanner)"

MAX_PER_CYCLE = int(os.environ.get("NEWS_MAX_PER_CYCLE") or 10)     # envois max par cycle (le reste passe au cycle suivant)
MAX_PER_HOUR = int(os.environ.get("NEWS_MAX_PER_HOUR") or 20)       # au-dela : seuls breaking / chiffres / banques centrales passent ; le reste attend l'heure suivante
BOOTSTRAP = int(os.environ.get("NEWS_BOOTSTRAP") or 4)              # articles postes au tout premier lancement
DEFAULT_MAX_AGE_H = 4
SEND_PAUSE = 1.6                                                     # secondes entre 2 envois (limite Telegram ~20/min)

WORLD_RE = re.compile(
    r"(?i)\b(econom\w*|économ\w*|inflation|central banks?|banques? centrales?|interest rates?|taux directeurs?|fed|ecb|bce|"
    r"tariffs?|droits? de douane|sanctions?|oil|pétrole|petrole|opec|opep|natural gas|gaz|war|guerre|ceasefire|cessez-le-feu|"
    r"elections?|élections?|trade (war|deal|talks)|stocks?|shares|bourse|currency|currencies|devises?|dollar|euro|yen|gold|"
    r"bitcoin|crypto\w*|recession|récession|gdp|pib|jobs|emploi|chômage|unemployment|debt|dette|default|budget|imf|fmi|"
    r"world bank|banque mondiale|china|chine|russia|russie|ukraine|iran|israel|israël|taiwan|gaza|brexit|shutdown|embargo|"
    r"nuclear|nucléaire|strikes?|grève|energy|énergie|energie|supply chain)\b")
EXCLUDE_RE = re.compile(
    r"(?i)\b(horoscopes?|celebrit\w*|célébrit\w*|nba|nfl|nhl|mlb|premier league|ligue des champions|football|soccer|tennis|"
    r"rugby|formule 1|formula 1|olympi\w*|oscars?|grammys?|recipes?|recettes?|loto|lottery|astrolog\w*|royal family|"
    r"taylor swift|kardashian)\b")
BREAKING_RE = re.compile(r"(?i)\b(breaking|urgent|flash|alerte|just in)\b")
DATA_RE = re.compile(r"(?i)(\bvs\.?(?=\s)|\bversus\b|\bexpected\b|\bforecasts?\b|\bconsensus\b|\bprev(ious)?\b|\bprior\b|"
                     r"\bprévu\w*|\battendu\w*|\bcontre\b)")
DATA_WORDS_RE = re.compile(
    r"(?i)\b(payrolls?|nfp|cpi|ppi|pce|gdp|pib|pmi|ism|inflation|unemployment|chômage|retail sales|jobs|jobless|claims|"
    r"confidence|sentiment|production|trade balance|housing|earnings|rate decision|emploi|ventes)\b")
_ENT = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([A-Za-z][A-Za-z0-9]*);")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------------------------------- lecture des flux
def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def strip_html(s: Optional[str]) -> str:
    s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def clean_summary(raw: Optional[str], title: str, limit: int = 240) -> str:
    s = strip_html(raw)
    s = re.sub(r"(?i)\s*The post .{0,200}? appeared first on .*$", "", s)
    s = re.sub(r"(?i)\s*(continue reading|read more|lire la suite|en savoir plus|\[…\]|\[\.\.\.\]).*$", "", s).strip()
    if title and s.lower().startswith(title.lower()[:60]):
        s = s[len(title):].lstrip(" -–—:.")
    if len(s) < 25 or s.lower() == title.lower():
        return ""
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"
    return s


def parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        d = parsedate_to_datetime(raw)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def parse_feed(content: bytes) -> List[dict]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        txt = content.decode("utf-8", "replace")
        txt = _CTRL.sub("", re.sub(r"^\s*<\?xml[^>]*\?>", "", txt))
        txt = _ENT.sub(lambda m: f"&#{html.entities.name2codepoint[m.group(1)]};"
                       if m.group(1) in html.entities.name2codepoint else "", txt)
        root = ET.fromstring(txt)
    items = []
    for node in root.iter():
        if _ln(node.tag) not in ("item", "entry"):
            continue
        d: Dict[str, str] = {}
        for ch in node:
            n = _ln(ch.tag).lower()
            txt = ("".join(ch.itertext()) if len(ch) else (ch.text or "")).strip()
            if n == "link":
                href = ch.attrib.get("href")
                if href:
                    if ch.attrib.get("rel", "alternate") == "alternate" or "link" not in d:
                        d["link"] = href
                elif txt:
                    d["link"] = txt
            elif n == "title":
                d["title"] = txt
            elif n in ("description", "summary") and txt:
                d.setdefault("summary", txt)
            elif n in ("encoded", "content") and txt and not ch.attrib.get("url"):
                d.setdefault("summary_long", txt)
            elif n in ("guid", "id"):
                d["id"] = txt
            elif n in ("pubdate", "published") and txt:
                d["date"] = txt
            elif n in ("updated", "date") and txt:
                d.setdefault("date", txt)
        items.append(d)
    return items


def fetch_feed(feed: dict, cache: dict) -> Tuple[str, List[dict]]:
    c = cache.get(feed["url"], {})
    headers = {"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if c.get("etag"):
        headers["If-None-Match"] = c["etag"]
    if c.get("lm"):
        headers["If-Modified-Since"] = c["lm"]
    r = requests.get(feed["url"], headers=headers, timeout=12, allow_redirects=True)
    if r.status_code == 304:
        return "304", []
    r.raise_for_status()
    items = parse_feed(r.content)
    cache.setdefault(feed["url"], {}).update({"etag": r.headers.get("ETag", ""), "lm": r.headers.get("Last-Modified", "")})
    return "ok", items


_TRACK = re.compile(r"(?i)^(utm_\w+|feed_item_type|cmpid|ocid|guccounter|taid|mod|ref|source)$")


def clean_link(link: str) -> str:
    """Retire les parametres de tracage (utm_...) : lien plus propre, memes pages."""
    try:
        u = urlsplit(link.strip())
        q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not _TRACK.match(k)]
        return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))
    except Exception:
        return link.strip()


def link_key(link: str) -> str:
    base = re.sub(r"[?#].*$", "", link.strip().lower().rstrip("/"))
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", t.lower()).split())


def similar(a: str, b: str) -> bool:
    ta, tb = set(w for w in a.split() if len(w) > 2), set(w for w in b.split() if len(w) > 2)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter >= 4 and inter / len(ta | tb) >= 0.6:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.88


def normalize_item(feed: dict, raw: dict, now: datetime) -> Optional[dict]:
    title = strip_html(raw.get("title"))
    link = clean_link(raw.get("link") or "")
    if not title or not link.startswith("http"):
        return None
    ts = parse_date(raw.get("date"))
    if ts and ts > now + timedelta(minutes=10):
        ts = now
    summary = clean_summary(raw.get("summary") or raw.get("summary_long"), title)
    breaking = bool(BREAKING_RE.search(title))
    data = bool(DATA_RE.search(title) and re.search(r"\d", title) and DATA_WORDS_RE.search(title))
    return {"key": link_key(link), "title": title, "link": link, "summary": summary, "ts": ts, "source": feed["name"],
            "emoji": feed.get("emoji", "📰"), "notify": bool(feed.get("notify")) or breaking or data,
            "breaking": breaking, "data": data, "feed": feed}


def accept(it: dict, now: datetime) -> Optional[str]:
    """None si l'article est publiable, sinon la raison du rejet."""
    f = it["feed"]
    if it["ts"] is not None and (now - it["ts"]) > timedelta(hours=float(f.get("max_age_h", DEFAULT_MAX_AGE_H))):
        return "old"
    text = f"{it['title']} {it['summary']}"
    inc = f.get("include")
    if inc and not (WORLD_RE if inc == "__WORLD__" else re.compile(inc, re.I)).search(text):
        return "filtered"
    exc = f.get("exclude")
    if exc and re.search(exc, it["title"]):
        return "filtered"
    if EXCLUDE_RE.search(it["title"]):
        return "filtered"
    return None


# ------------------------------------------------------------------------------------------------------ Telegram
class Telegram:
    def __init__(self) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.owner = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.news = os.environ.get("NEWS_CHAT_ID", "")
        env_live = os.environ.get("NEWS_LIVE")
        self.live = (env_live or "").lower() == "true"
        if not self.token:                                       # repli local (fichiers non versionnes)
            try:
                d = json.loads((DIR / "telegram.json").read_text(encoding="utf-8"))
                self.token, self.owner = d.get("token", ""), self.owner or str(d.get("chat_id", ""))
            except Exception:
                pass
        if not self.news and env_live is None:
            try:
                d = json.loads((DIR / "channel.json").read_text(encoding="utf-8"))
                self.news = str(d.get("news_chat_id", ""))
                self.live = bool(d.get("news_live", False))
            except Exception:
                pass
        self.force_preview = False

    @property
    def to_channel(self) -> bool:
        return bool(self.live and self.news and not self.force_preview)

    @property
    def target(self) -> str:
        return self.news if self.to_channel else self.owner

    def send(self, text: str, silent: bool = True, preview_url: Optional[str] = None, no_preview: bool = False) -> Tuple[bool, str]:
        """(ok, erreur) ; erreur in {"", "fatal", "transient"}."""
        if not self.token or not self.target:
            return False, "fatal"
        if not self.to_channel:
            text = "🧪 <b>APERÇU</b>\n" + text
            silent = True
        lp = {"is_disabled": True} if no_preview else ({"url": preview_url, "prefer_large_media": True} if preview_url else {})
        payload = {"chat_id": self.target, "text": text, "parse_mode": "HTML", "disable_notification": "true" if silent else "false"}
        if lp:
            payload["link_preview_options"] = json.dumps(lp)
        for attempt in range(3):
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage", data=payload, timeout=20)
                j = r.json()
            except Exception:
                time.sleep(2)
                continue
            if j.get("ok"):
                return True, ""
            desc = str(j.get("description", ""))
            code = j.get("error_code")
            if code == 429:
                time.sleep(min(int((j.get("parameters") or {}).get("retry_after", 5)) + 1, 60))
                continue
            if code == 400 and "parse entities" in desc:
                payload["parse_mode"] = ""
                payload["text"] = re.sub(r"<[^>]+>", "", html.unescape(text))
                continue
            log(f"[TG] refus ({code}) : {desc[:120]}")
            return False, "fatal" if code in (400, 401, 403, 404) else "transient"
        return False, "transient"

    def notify_owner(self, text: str) -> None:
        if self.token and self.owner:
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              data={"chat_id": self.owner, "text": text, "parse_mode": "HTML"}, timeout=15)
            except Exception:
                pass


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def build_message(it: dict) -> str:
    head = ("🚨 " if it["breaking"] else "") + ("📊 " if it["data"] else it["emoji"] + " ") + f"<b>{esc(it['title'])}</b>"
    parts = [head]
    if it["summary"]:
        parts.append(esc(it["summary"]))
    foot = f'<a href="{html.escape(it["link"], quote=True)}">Lire sur {esc(it["source"])}</a>'
    if it["ts"]:
        foot += f" · {it['ts'].strftime('%H:%M')} UTC"
    parts.append(foot)
    return "\n\n".join(parts)


# -------------------------------------------------------------------------------------------------------- etat
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_feeds() -> List[dict]:
    return json.loads(FEEDS_FILE.read_text(encoding="utf-8"))["feeds"]


# ------------------------------------------------------------------------------------------------------ cycle
def collect(feeds: List[dict], cache: dict, now: datetime, force: bool = False) -> Tuple[List[dict], dict]:
    due = [f for f in feeds if force or cache.get(f["url"], {}).get("next", 0) <= now.timestamp()]
    stats = {"due": len(due), "ok": 0, "304": 0, "err": 0}
    errs: List[str] = []

    def work(f: dict):
        try:
            return f, fetch_feed(f, cache), None
        except Exception as e:
            return f, ("err", []), f"{f['name']} ({type(e).__name__})"

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(work, due))
    items: List[dict] = []
    for f, (status, raw_items), err in results:
        c = cache.setdefault(f["url"], {})
        c["next"] = now.timestamp() + int(f.get("every", 60)) - 3
        if err:
            c["fails"] = c.get("fails", 0) + 1
            stats["err"] += 1
            errs.append(err)
            continue
        c["fails"] = 0
        stats[status] += 1
        for raw in raw_items:
            it = normalize_item(f, raw, now)
            if it:
                items.append(it)
    if errs:
        log("flux en erreur : " + ", ".join(errs[:8]))
    return items, stats


def run_cycle(tg: Telegram, dry: bool = False, preview_n: int = 0) -> None:
    now = datetime.now(timezone.utc)
    feeds = load_feeds()
    state = load_json(STATE_FILE, {})
    cache = load_json(CACHE_FILE, {})
    first_run = not state.get("seeded")
    seen: Dict[str, float] = state.setdefault("seen", {})
    titles: List[list] = state.setdefault("titles", [])
    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"], state["count"] = today, 0
    hour = now.strftime("%Y-%m-%dT%H")
    if state.get("hour") != hour:
        state["hour"], state["hcount"] = hour, 0

    items, stats = collect(feeds, cache, now, force=preview_n > 0)
    items.sort(key=lambda i: i["ts"] or now)
    recent = [t[1] for t in titles if now.timestamp() - t[0] < 6 * 3600]
    todo: List[dict] = []
    counts = {"new": 0, "seen": 0, "old": 0, "filtered": 0, "dup": 0}
    batch_titles: List[str] = []
    for it in items:
        if it["key"] in seen and not preview_n:
            counts["seen"] += 1
            continue
        counts["new"] += 1
        why = accept(it, now)
        nt = norm_title(it["title"])
        if why is None and any(similar(nt, o) for o in recent + batch_titles):
            why = "dup"
        if why:
            counts[why] += 1
            if not preview_n:
                seen[it["key"]] = now.timestamp()
            continue
        batch_titles.append(nt)
        it["nt"] = nt
        todo.append(it)

    if preview_n:                                              # apercu : N articles de sources differentes
        pick, used = [], set()
        for it in reversed(todo):
            if it["source"] not in used:
                pick.append(it)
                used.add(it["source"])
            if len(pick) >= preview_n:
                break
        todo = list(reversed(pick))
    elif first_run:                                            # 1er lancement : on n'inonde pas, on garde les plus recents
        fresh = [i for i in todo if i["ts"] and (now - i["ts"]) < timedelta(hours=2)]
        keep, used = [], set()
        for it in reversed(fresh):
            if it["source"] not in used:
                keep.append(it)
                used.add(it["source"])
            if len(keep) >= BOOTSTRAP:
                break
        for it in todo:
            seen[it["key"]] = now.timestamp()
        todo = list(reversed(keep))

    posted, fatal = 0, False
    for it in todo:
        if posted >= (preview_n or MAX_PER_CYCLE):
            break
        if state["hcount"] >= MAX_PER_HOUR and not it["notify"] and not preview_n:
            continue                                           # reporte au cycle suivant (reste non vu), sans rien perdre
        if dry:
            print(f"\n--- {it['source']} | {it['ts']} | notif={it['notify']} ---\n{build_message(it)}")
            posted += 1
            continue
        ok, err = tg.send(build_message(it), silent=not it["notify"], preview_url=it["link"])
        if ok:
            posted += 1
            if not preview_n:
                seen[it["key"]] = now.timestamp()
                titles.append([now.timestamp(), it["nt"]])
                state["count"] += 1
                state["hcount"] += 1
                save_json(STATE_FILE, state)
            time.sleep(SEND_PAUSE)
        elif err == "fatal":
            fatal = True
            break
        else:
            break

    sent_cal = 0
    if not preview_n:
        def cal_send(text: str, sound: bool) -> bool:
            if dry:
                print("\n--- CALENDRIER ---\n" + text)
                return True
            ok, err = tg.send(text, silent=not sound, no_preview=True)
            time.sleep(SEND_PAUSE)
            return ok
        sent_cal = cal.run_calendar(state, cache, now, cal_send, log, first_run=first_run)

    if fatal and not dry and not preview_n and now.timestamp() - state.get("notified", 0) > 6 * 3600:
        state["notified"] = now.timestamp()
        tg.notify_owner("⚠️ <b>Bot d'actualités</b> : impossible de publier dans le canal (droits de l'administrateur ? bot retiré ?). "
                        "Vérifie que le bot est administrateur avec le droit de publier.")

    # menage de l'etat
    cutoff = now.timestamp() - 4 * 86400
    for k in [k for k, v in seen.items() if v < cutoff]:
        del seen[k]
    state["titles"] = [t for t in titles if now.timestamp() - t[0] < 12 * 3600][-500:]
    if first_run and not preview_n:
        state["seeded"] = True
    if not dry and not preview_n:
        save_json(STATE_FILE, state)
        flag = os.environ.get("NEWS_POSTED_FLAG")
        if flag and (posted or sent_cal):
            Path(flag).write_text("1")
    if not dry:
        save_json(CACHE_FILE, cache)
    where = "CANAL" if tg.to_channel else "apercu prive"
    log(f"flux {stats['ok']} ok/{stats['304']}x304/{stats['err']} err | nouveaux {counts['new']} (deja vus {counts['seen']}, "
        f"anciens {counts['old']}, filtres {counts['filtered']}, doublons {counts['dup']}) | postes {posted} + calendrier {sent_cal} "
        f"-> {where} | total du jour {state['count']} (heure {state['hcount']}/{MAX_PER_HOUR})")


# ------------------------------------------------------------------------------------------------ outils / CLI
def check_feeds() -> None:
    feeds = load_feeds()
    now = datetime.now(timezone.utc)

    def work(f: dict) -> str:
        try:
            r = requests.get(f["url"], headers={"User-Agent": UA}, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                return f"  X  HTTP {r.status_code}  {f['name']:<22} {f['url'][:70]}"
            items = [normalize_item(f, x, now) for x in parse_feed(r.content)]
            items = [i for i in items if i]
            dated = [i["ts"] for i in items if i["ts"]]
            newest = f"{(now - max(dated)).total_seconds() / 60:.0f} min" if dated else "?"
            n24 = sum(1 for d in dated if now - d < timedelta(hours=24))
            return f" OK  {len(items):>3} art. | dernier il y a {newest:>8} | {n24:>3}/24h  {f['name']:<22} {items[0]['title'][:48] if items else ''}"
        except Exception as e:
            return f"  X  {type(e).__name__}: {str(e)[:60]}  {f['name']:<22} {f['url'][:60]}"

    with ThreadPoolExecutor(max_workers=10) as ex:
        for line in ex.map(work, feeds):
            print(line)


def calendar_preview(tg: Telegram) -> None:
    """Envoie dans TON chat prive des exemples reels : agenda du prochain jour utile, alerte et publication du prochain evenement fort."""
    tg.force_preview = True
    evs = cal.fetch_calendar()
    if not evs:
        print("calendrier injoignable")
        return
    now = datetime.now(timezone.utc)
    upcoming = sorted((e for e in evs if e["time_utc"] > now), key=lambda e: e["time_utc"])
    if not upcoming:
        print("aucun evenement a venir cette semaine")
        return
    day = next((e["time_utc"] for e in upcoming if e["impact"] in ("High", "Medium")), upcoming[0]["time_utc"])
    msgs = [cal.agenda_message(evs, day), cal.week_message(evs, now)]
    highs = [e for e in upcoming if e["impact"] == "High"]
    explained = [e for e in highs if cal.explain(e["title"])] or highs
    if explained:
        t0 = explained[0]["time_utc"]
        grp = [e for e in highs if e["time_utc"] == t0 and e["ccy"] == explained[0]["ccy"]]
        msgs += [cal.alert_message(grp, 15), cal.release_message(grp)]
    for m in [m for m in msgs if m]:
        print("\n" + m)
        tg.send(m, silent=True, no_preview=True)
        time.sleep(SEND_PAUSE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calendar-preview", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-feeds", action="store_true")
    ap.add_argument("--preview", type=int, default=0, metavar="N")
    a = ap.parse_args()
    if a.check_feeds:
        return check_feeds()
    tg = Telegram()
    if a.calendar_preview:
        return calendar_preview(tg)
    if a.preview:
        tg.force_preview = True
    run_cycle(tg, dry=a.dry_run, preview_n=a.preview)


if __name__ == "__main__":
    main()
