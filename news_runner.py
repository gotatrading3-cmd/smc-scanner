"""
news_runner.py - Actualites financieres en temps reel pour le canal Telegram "Info du monde" : en francais, sans aucun lien.

Ce que fait le bot, a chaque cycle (toutes les ~60 s dans le cloud, voir .github/workflows/news.yml) :
  1. lit les flux RSS/Atom de news_feeds.json (finance, bourse, economie, crypto, matieres premieres, banques centrales) ;
  2. ne garde que ce qui parle de finance / marches (le reste - sport, politique generale... - est ecarte) ;
  3. poste chaque NOUVEL article EN FRANCAIS : titre + court extrait fourni par le flux + l'image du flux envoyee telle quelle
     comme photo. JAMAIS de lien ni d'apercu de lien : tout se lit dans le canal, la source est nommee en texte simple.
     Les sources anglaises sont traduites automatiquement (news_translate.py, modele libre hors ligne) ;
     sans traduction fiable, l'article n'est pas publie (jamais d'anglais brut dans le canal) ;
  4. met en avant les chiffres publies ("X vs Y attendu") et les annonces de banques centrales ;
  5. ajoute parfois une note pedagogique "💡 À savoir" (news_edu.py) ;
  6. poste le calendrier economique (agenda, alerte 15 min avant, publication) via news_calendar.py.

Regles : jamais le texte complet d'un article ; la source est toujours nommee (en texte) ; les doublons entre sources sont
ecartes ; une limite horaire evite d'inonder le canal ; une source lue pour la premiere fois ne deverse pas son historique.
Ce script ENVOIE seulement : il ne lit jamais les messages entrants (getUpdates) pour ne pas perturber le bot de signaux.

MODE APERCU PAR DEFAUT : tant que NEWS_LIVE n'est pas "true" (ou news_live dans channel.json en local), tout part dans
le chat PRIVE du proprietaire, marque "APERCU", et rien n'est publie dans le canal.

Usage :
    python news_runner.py --once               # un cycle (celui qu'appelle le workflow)
    python news_runner.py --dry-run            # montre ce qui serait poste, n'envoie ni n'ecrit rien
    python news_runner.py --check-feeds        # teste chaque source (statut, nb d'articles, fraicheur, images)
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
import struct
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars(r"%APPDATA%\Python\Python312\site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)

import requests  # noqa: E402

import news_calendar as cal  # noqa: E402
import news_edu as edu  # noqa: E402

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
BOOTSTRAP = int(os.environ.get("NEWS_BOOTSTRAP") or 4)              # articles postes au tout premier lancement d'une source
DEFAULT_MAX_AGE_H = 4
SEND_PAUSE = 1.6                                                     # secondes entre 2 envois (limite Telegram ~20/min)
CAPTION_MAX = 1000                                                   # Telegram : 1024 caracteres au plus pour la legende d'une photo
MIN_IMG_BYTES, MAX_IMG_BYTES = 6_000, 8_000_000
MIN_IMG_W, MIN_IMG_H = 300, 160                                      # en dessous : vignette / logo, pas une vraie image

# Sujets financiers (francais + anglais) : sert de filtre pour les medias generalistes ("include": "__FINANCE__").
FINANCE_RE = re.compile(
    r"(?i)\b(?:"
    r"bours\w*|cac ?40|sbf ?120|dax|euro ?stoxx|stoxx|ftse|dow jones|nasdaq|s&p ?(?:500|global)?|nikkei|hang seng|wall street|russell|"
    r"march[eé]s? (?:financiers?|actions|boursiers?|obligataires?|des changes|des capitaux|europ[eé]ens?|asiatiques?|am[eé]ricains?|mondiaux)|"
    r"trading|traders?|investisseurs?|actionnaires?|cotation|introduction en bourse|ipo|opa|capitalisation|"
    r"actions (?:am[eé]ricaines?|europ[eé]ennes?|chinoises?|japonaises?|technologiques?|bancaires?|cot[eé]es?)|"
    r"valeurs? (?:bancaires?|technologiques?|cot[eé]es?)|"
    r"taux (?:directeurs?|d['’]int[eé]r[eê]ts?|de change|hypoth[eé]caires?|obligataires?|d['’]emprunt)|politique mon[eé]taire|"
    r"rendements? (?:obligataires?|des obligations|des bons)|bunds?|oat|bons du tr[eé]sor|spreads?|"
    r"banques? centrales?|bce|fed|r[eé]serve f[eé]d[eé]rale|boj|banque du japon|banque d['’]angleterre|banque de france|lagarde|powell|"
    r"inflation|d[eé]sinflation|d[eé]flation|stagflation|pib|r[eé]cession|croissance (?:[eé]conomique|mondiale|am[eé]ricaine|europ[eé]enne|chinoise|fran[cç]aise)|"
    r"ch[oô]mage|cr[eé]ations? d['’]emplois?|emplois non agricoles|nfp|payrolls?|pmi|ism|"
    r"indice des prix|prix [àa] la consommation|prix [àa] la production|ipc|production industrielle|ventes au d[eé]tail|"
    r"balance commerciale|d[eé]ficit (?:public|commercial|budg[eé]taire)|dette (?:publique|souveraine)|agences? de notation|moody['’]?s|fitch|"
    r"fmi|ocde|banque mondiale|eurogroupe|droits? de douane|tarifs? douaniers?|guerre commerciale|"
    r"devises?|forex|dollar|euro|yen|livre sterling|franc suisse|eur/usd|usd/jpy|gbp/usd|yuan|"
    r"l['’]or|once d['’]or|p[eé]trole|baril\w*|brent|wti|opep\w*|opec\w*|gaz naturel|mati[eè]res premi[eè]res|cuivre|lithium|uranium|"
    r"bitcoin|btc|ethereum|[eé]ther|crypto\w*|stablecoins?|altcoins?|blockchain|binance|coinbase|solana|ripple|xrp|halving|etf|"
    r"r[eé]sultats? (?:trimestriels?|annuels?|semestriels?|financiers?)|b[eé]n[eé]fices?|chiffre d['’]affaires|dividendes?|"
    r"rachats? d['’]actions|fusions?[- ]acquisitions?|banques?|assureurs?|hedge funds?|"
    r"stocks?|shares|equit(?:y|ies)|bonds?|yields?|treasur(?:y|ies)|rate (?:cuts?|hikes?|decisions?)|interest rates?|central banks?|"
    r"fomc|ecb|boe|cpi|ppi|pce|gdp|nonfarm|non-farm|unemployment|jobless|jobs report|tariffs?|trade war|earnings|profits?|"
    r"merger|acquisition|currenc(?:y|ies)|sterling|gold|silver|copper|oil|crude|natural gas|commodit(?:y|ies)|futures|volatility|"
    r"sell-?off|recession|retail sales|hedge fund|lenders?|credit|debt|deficit|imf|world bank"
    r")\b")
EXCLUDE_RE = re.compile(
    r"(?i)\b(horoscopes?|celebrit\w*|célébrit\w*|nba|nfl|nhl|mlb|premier league|ligue des champions|football|soccer|tennis|"
    r"rugby|formule 1|formula 1|olympi\w*|oscars?|grammys?|recipes?|recettes?|loto|lottery|astrolog\w*|royal family|"
    r"taylor swift|kardashian|m[eé]t[eé]o)\b")
NOISE_RE = re.compile(r"(?i)^(?:here['’]s the latest|live updates?|latest updates?|watch(?: live)?\b|listen\b|podcast|newsletter|sponsored|advertis\w*)")
PROMO_RE = re.compile(r"(?i)\b(investingpro|propicks|investing pro|juste valeur|black friday|code promo|offre exclusive|sponsoris[ée]e?s?|publi-?reportage)\b")
BREAKING_RE = re.compile(r"(?i)\b(breaking|urgent|flash|alerte|just in)\b")
DATA_RE = re.compile(r"(?i)(\bvs\.?(?=\s)|\bversus\b|\bexpected\b|\bforecasts?\b|\bconsensus\b|\bprev(ious)?\b|\bprior\b|"
                     r"\bprévu\w*|\battendu\w*|\bcontre\b)")
DATA_WORDS_RE = re.compile(
    r"(?i)\b(payrolls?|nfp|cpi|ppi|pce|gdp|pib|pmi|ism|inflation|unemployment|chômage|retail sales|jobs|jobless|claims|"
    r"confidence|sentiment|production|trade balance|housing|earnings|rate decision|emploi|ventes)\b")
_ENT = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([A-Za-z][A-Za-z0-9]*);")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Aucun lien, jamais : on retire les adresses ET on casse les noms de domaine ("Investing.com"), que Telegram transformerait
# sinon tout seul en lien cliquable.
_URL = re.compile(r"(?i)\s*(?:https?://|www\.)\S+")
_TLDS = "com|net|org|info|biz|io|co|ai|app|tv|me|ly|gg|xyz|fr|eu|uk|us|de|es|it|ch|be|ca|cn|jp|ru|in|br|au|nz|za|ng|ci|sn|gov|edu"
_DOMAIN = re.compile(rf"(?i)\b([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*)\.({_TLDS})\b")
ZW = "\u200b"


def no_links(text: str) -> str:
    return _DOMAIN.sub(lambda m: f"{m.group(1)}.{ZW}{m.group(2)}", _URL.sub("", text))


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
    s = re.sub(r"(?i)\s*(?:The post|L['’]article|Cet article|Le post) .{0,250}?(?:appeared first on|est apparu en premier sur|est paru en premier sur)\b.*$", "", s)
    s = re.sub(r"(?i)\s*(continue reading|read more|lire la suite|en savoir plus|\[…\]|\[\.\.\.\]).*$", "", s).strip()
    s = re.sub(r"(?i)^(?:investing\.com|reuters|afp|bloomberg|cnbc)\s*[-–—:]\s*", "", s)
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


_IMGEXT = re.compile(r"(?i)\.(?:jpe?g|png|webp)(?:[?#]|$)")
_IMG_BAD = re.compile(r"(?i)(logo|placeholder|default[-_]?(?:image|thumb)|favicon|sprite|spacer|pixel|1x1|blank|avatar|/ads?/|\.svg|\.gif|\.ico)")


def _pick_img(d: dict, url: str, width: int = 0) -> None:
    if url.startswith(("http://", "https://")) and width > d.get("_iw", -1):
        d["image"], d["_iw"] = url, width


def _media(d: dict, el: "ET.Element") -> None:
    """media:content / media:thumbnail / enclosure : retient l'image la plus large du flux pour cet article."""
    url = (el.attrib.get("url") or "").strip()
    if not url:
        return
    kind = (el.attrib.get("type") or "") + " " + (el.attrib.get("medium") or "")
    if _ln(el.tag).lower() == "thumbnail" or "image" in kind or _IMGEXT.search(url):
        w = (el.attrib.get("width") or "").strip()
        _pick_img(d, url, int(w) if w.isdigit() else 0)


def _loose_items(txt: str) -> List[dict]:
    """Dernier recours pour un flux XML mal forme : retrouve les articles par expressions regulieres."""
    items = []
    for blk in re.findall(r"(?is)<(?:item|entry)\b.*?</(?:item|entry)>", txt):
        def tag(name: str) -> str:
            m = re.search(rf"(?is)<{name}\b[^>]*>(.*?)</{name}>", blk)
            return re.sub(r"(?s)^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", m.group(1)).strip() if m else ""
        m = re.search(r"(?is)<link\b[^>]*\bhref=[\"']([^\"']+)", blk)
        d = {"title": tag("title"), "summary": tag("description") or tag("summary"), "link": m.group(1) if m else tag("link"),
             "date": tag("pubDate") or tag("published") or tag("updated") or tag("dc:date")}
        mi = re.search(r"(?is)<(?:media:content|media:thumbnail|enclosure)\b[^>]*\burl=[\"']([^\"']+)", blk)
        if mi:
            _pick_img(d, html.unescape(mi.group(1)).strip())
        if d["title"] and d["link"]:
            items.append(d)
    return items


def parse_feed(content: bytes) -> List[dict]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        txt = content.decode("utf-8", "replace")
        txt = _CTRL.sub("", re.sub(r"^\s*<\?xml[^>]*\?>", "", txt))
        txt = _ENT.sub(lambda m: f"&#{html.entities.name2codepoint[m.group(1)]};"
                       if m.group(1) in html.entities.name2codepoint else "", txt)
        try:
            root = ET.fromstring(txt)
        except ET.ParseError:
            return _loose_items(txt)
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
                    rel = ch.attrib.get("rel", "alternate")
                    if rel == "enclosure":
                        if (ch.attrib.get("type") or "").startswith("image"):
                            _pick_img(d, href.strip())
                    elif rel == "alternate" or "link" not in d:
                        d["link"] = href
                elif txt:
                    d["link"] = txt
            elif n == "title":
                d["title"] = txt
            elif n in ("description", "summary") and txt:
                d.setdefault("summary", txt)
            elif n in ("encoded", "content") and txt and not ch.attrib.get("url"):
                d.setdefault("summary_long", txt)
            elif n in ("enclosure", "content", "thumbnail"):
                _media(d, ch)
            elif n == "group":
                for g in ch:
                    _media(d, g)
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


def html_image(raw_html: Optional[str], base: str) -> str:
    """Premiere vraie image (balise <img>) du texte HTML d'un article, sinon ''."""
    for m in re.finditer(r"(?is)<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", raw_html or ""):
        u = urljoin(base, html.unescape(m.group(1)).strip())
        if u.startswith(("http://", "https://")) and not _IMG_BAD.search(u):
            return u
    return ""


def normalize_item(feed: dict, raw: dict, now: datetime) -> Optional[dict]:
    link = (raw.get("link") or "").strip()
    title = strip_html(raw.get("title"))
    if not title or not link.startswith("http"):
        return None
    ts = parse_date(raw.get("date"))
    if ts and ts > now + timedelta(minutes=10):
        ts = now
    summary = clean_summary(raw.get("summary") or raw.get("summary_long"), title, limit=220)
    title, summary = _URL.sub("", title).strip(), _URL.sub("", summary).strip()        # jamais d'adresse dans le texte
    image = raw.get("image") or html_image((raw.get("summary_long") or "") + " " + (raw.get("summary") or ""), link)
    if image and _IMG_BAD.search(image):
        image = ""
    breaking = bool(BREAKING_RE.search(title))
    data = bool(DATA_RE.search(title) and re.search(r"\d", title) and DATA_WORDS_RE.search(title))
    return {"key": link_key(link), "title": title, "summary": summary, "ts": ts, "source": feed["name"], "image": image,
            "emoji": feed.get("emoji", "📰"), "notify": bool(feed.get("notify")) or breaking or data,
            "breaking": breaking, "data": data, "feed": feed}


def accept(it: dict, now: datetime) -> Optional[str]:
    """None si l'article est publiable, sinon la raison du rejet."""
    f = it["feed"]
    if it["ts"] is not None and (now - it["ts"]) > timedelta(hours=float(f.get("max_age_h", DEFAULT_MAX_AGE_H))):
        return "old"
    text = f"{it['title']} {it['summary']}"
    inc = f.get("include")
    if inc and not (FINANCE_RE if inc == "__FINANCE__" else re.compile(inc, re.I)).search(text):
        return "filtered"
    exc = f.get("exclude")
    if exc and re.search(exc, it["title"], re.I):
        return "filtered"
    if (EXCLUDE_RE.search(it["title"]) or NOISE_RE.search(it["title"]) or PROMO_RE.search(it["title"])
            or len(it["title"].split()) < 3):
        return "filtered"
    return None


# ------------------------------------------------------------------------------------------------ traduction
_TRANSLATOR = {"obj": None, "tried": False}


def get_translator():
    """Traducteur anglais->francais (modele libre, hors ligne) ; None s'il est indisponible."""
    if not _TRANSLATOR["tried"]:
        _TRANSLATOR["tried"] = True
        try:
            import news_translate
            _TRANSLATOR["obj"] = news_translate.load()
        except Exception as e:
            log(f"[TRAD] indisponible : {type(e).__name__}: {str(e)[:140]}")
    return _TRANSLATOR["obj"]


def translate_item(it: dict) -> str:
    """Ajoute title_fr / summary_fr a un article anglais. 'ok' | 'unavailable' (reessaye plus tard) | 'error' (abandonne)."""
    tr = get_translator()
    if tr is None:
        return "unavailable"
    try:
        out = tr.en_to_fr([it["title"]] + ([it["summary"]] if it["summary"] else []))
    except Exception as e:
        log(f"[TRAD] echec : {type(e).__name__}: {str(e)[:140]}")
        return "error"
    if not out or not out[0]:
        return "error"                                        # titre non fiable : on ne publie pas
    it["title_fr"] = out[0]
    it["summary_fr"] = (out[1] or "") if len(out) > 1 else ""   # extrait non fiable : on publie le titre seul
    return "ok"


# ------------------------------------------------------------------------------------------------------- images
def sniff_image(b: bytes) -> str:
    if b[:2] == b"\xff\xd8":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return ""


def image_size(b: bytes) -> Optional[Tuple[int, int]]:
    """(largeur, hauteur) lues dans l'en-tete du fichier (JPEG, PNG, WebP), sans bibliotheque d'images."""
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", b[16:24])
        if b[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(b):
                if b[i] != 0xFF:
                    i += 1
                    continue
                m = b[i + 1]
                if m == 0xFF:
                    i += 1
                    continue
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", b[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            kind = b[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", b[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                bits = struct.unpack("<I", b[21:25])[0]
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1
    except Exception:
        return None
    return None


def fetch_image(url: str) -> Optional[bytes]:
    """Telecharge l'image fournie par le flux (taille et type verifies). None si inutilisable."""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "image/jpeg,image/png,image/webp;q=0.9"}, timeout=10, stream=True)
        if r.status_code != 200 or not r.headers.get("Content-Type", "").lower().startswith("image/"):
            return None
        if int(r.headers.get("Content-Length") or 0) > MAX_IMG_BYTES:
            return None
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > MAX_IMG_BYTES:
                return None
        data = bytes(buf)
        return data if len(data) >= MIN_IMG_BYTES and sniff_image(data) else None
    except Exception:
        return None


def prepare_image(it: dict, state: dict) -> Optional[Tuple[bytes, str]]:
    """(octets, extension) de l'image de l'article si elle est utilisable ; None sinon (l'article part alors en texte seul)."""
    url = it.get("image")
    if not url:
        return None
    data = fetch_image(url)
    if not data:
        return None
    size = image_size(data)
    if size:
        w, h = size
        if w < MIN_IMG_W or h < MIN_IMG_H or w > 3.6 * h or h > 3.6 * w or w + h > 9500:
            return None
    key = hashlib.sha1(data).hexdigest()[:16]
    if key in state.get("imgs", {}):                            # meme image deja utilisee (logo, visuel generique)
        return None
    it["img_key"] = key
    return data, sniff_image(data)


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
        self.last_id: Optional[int] = None

    @property
    def to_channel(self) -> bool:
        return bool(self.live and self.news and not self.force_preview)

    @property
    def target(self) -> str:
        return self.news if self.to_channel else self.owner

    def _call(self, method: str, payload: dict, field: str, files: Optional[dict] = None) -> Tuple[bool, str]:
        """(ok, erreur) ; erreur in {"", "photo" (image refusee), "fatal", "transient"}."""
        if not self.token or not self.target:
            return False, "fatal"
        payload["chat_id"] = self.target
        payload[field] = no_links(payload[field])                  # dernier filet : aucun lien ne part, quoi qu'il arrive
        if not self.to_channel:
            payload[field] = "🧪 <b>APERÇU</b>\n" + payload[field]
            payload["disable_notification"] = "true"
        for attempt in range(3):
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", data=payload, files=files,
                                  timeout=45 if files else 20)
                j = r.json()
            except Exception:
                time.sleep(2)
                continue
            if j.get("ok"):
                self.last_id = (j.get("result") or {}).get("message_id")
                return True, ""
            desc = str(j.get("description", ""))
            code = j.get("error_code")
            if code == 429:
                time.sleep(min(int((j.get("parameters") or {}).get("retry_after", 5)) + 1, 60))
                continue
            if code == 400 and "parse entities" in desc:
                payload["parse_mode"] = ""
                payload[field] = re.sub(r"<[^>]+>", "", html.unescape(payload[field]))
                continue
            log(f"[TG] refus ({code}) : {desc[:120]}")
            if code == 400 and method == "sendPhoto":
                return False, "photo"
            return False, "fatal" if code in (400, 401, 403, 404) else "transient"
        return False, "transient"

    def send(self, text: str, silent: bool = True) -> Tuple[bool, str]:
        payload = {"text": text, "parse_mode": "HTML", "disable_notification": "true" if silent else "false",
                   "link_preview_options": json.dumps({"is_disabled": True})}
        return self._call("sendMessage", payload, "text")

    def send_photo(self, photo: bytes, ext: str, caption: str, silent: bool = True) -> Tuple[bool, str]:
        payload = {"caption": caption, "parse_mode": "HTML", "disable_notification": "true" if silent else "false"}
        return self._call("sendPhoto", payload, "caption", files={"photo": (f"image.{ext}", photo)})

    def notify_owner(self, text: str) -> None:
        if self.token and self.owner:
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              data={"chat_id": self.owner, "text": text, "parse_mode": "HTML"}, timeout=15)
            except Exception:
                pass


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _visible(t: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", t)))


def build_message(it: dict, limit: int = 3900) -> str:
    """Titre en gras, court extrait, note pedagogique eventuelle, source en texte (jamais de lien). Tient dans `limit` caracteres."""
    translated = "title_fr" in it
    title = it["title_fr"] if translated else it["title"]
    summary = (it.get("summary_fr") or "") if translated else it["summary"]
    head = ("🚨 " if it["breaking"] else "") + ("📊 " if it["data"] else it["emoji"] + " ") + f"<b>{esc(title)}</b>"
    foot = f"Source : {esc(it['source'])}" + (" (trad. auto)" if translated else "")
    if it["ts"]:
        foot += f" · {it['ts'].strftime('%H:%M')} UTC"

    def assemble(summ: str, tip: str) -> str:
        parts = [head]
        if summ:
            parts.append(esc(summ))
        if tip:
            parts.append(f"💡 <b>À savoir</b> — {esc(tip)}")
        parts.append(foot)
        return "\n\n".join(parts)

    text = assemble(summary, it.get("tip") or "")
    if _visible(text) > limit:
        text = assemble(summary, "")                           # on sacrifie d'abord la note pedagogique
    if _visible(text) > limit and summary:
        room = limit - _visible(assemble("x", "")) - 3
        text = assemble(summary[:room].rsplit(" ", 1)[0].rstrip(",;:- ") + "…" if room > 40 else "", "")
    return text


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
    nowt = now.timestamp()
    feeds = load_feeds()
    state = load_json(STATE_FILE, {})
    cache = load_json(CACHE_FILE, {})
    first_run = not state.get("seeded")
    seen: Dict[str, float] = state.setdefault("seen", {})
    titles: List[list] = state.setdefault("titles", [])
    fseeded: Dict[str, float] = state.setdefault("fseeded", {})        # sources deja lues au moins une fois
    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"], state["count"] = today, 0
    hour = now.strftime("%Y-%m-%dT%H")
    if state.get("hour") != hour:
        state["hour"], state["hcount"] = hour, 0
    live_state = not (dry or preview_n)                                 # n'ecrit l'etat que pour un vrai cycle

    items, stats = collect(feeds, cache, now, force=preview_n > 0)
    items.sort(key=lambda i: i["ts"] or now)
    recent = [t[1] for t in titles if nowt - t[0] < 6 * 3600]
    todo: List[dict] = []
    counts = {"new": 0, "seen": 0, "old": 0, "filtered": 0, "dup": 0, "lang": 0}
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
                seen[it["key"]] = nowt
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
    else:                                                      # source lue pour la 1re fois : on ne deverse pas son historique
        newsrc = [i for i in todo if i["feed"]["url"] not in fseeded]
        if newsrc:
            keep, used = [], set()
            for it in sorted(newsrc, key=lambda i: i["ts"] or now, reverse=True):
                if it["ts"] and (now - it["ts"]) < timedelta(minutes=90) and it["source"] not in used:
                    keep.append(it)
                    used.add(it["source"])
                if len(keep) >= BOOTSTRAP:
                    break
            kept = {id(i) for i in keep}
            for it in newsrc:
                if id(it) not in kept:
                    seen[it["key"]] = nowt
            todo = [i for i in todo if i["feed"]["url"] in fseeded or id(i) in kept]
        if live_state:
            for u in {i["feed"]["url"] for i in items}:
                fseeded.setdefault(u, nowt)

    if not preview_n:                                          # trop d'articles : d'abord les prioritaires, puis les plus recents
        todo.sort(key=lambda i: (not i["notify"], -(i["ts"] or now).timestamp()))
        chosen, budget = [], max(0, MAX_PER_HOUR - state["hcount"])
        for it in todo:
            if len(chosen) >= MAX_PER_CYCLE + 4:               # petite marge : certains seront ecartes a l'envoi (traduction, doublon)
                break
            if it["notify"] or budget > 0:
                chosen.append(it)
                budget -= 0 if it["notify"] else 1
        todo = sorted(chosen, key=lambda i: i["ts"] or now)    # et on les publie dans l'ordre chronologique

    sent_now: List[str] = []
    posted, fatal = 0, False
    for it in todo:
        if posted >= (preview_n or MAX_PER_CYCLE):
            break
        if state["hcount"] >= MAX_PER_HOUR and not it["notify"] and not preview_n:
            continue                                           # reporte au cycle suivant (reste non vu), sans rien perdre
        if it["feed"].get("lang", "fr") != "fr":               # canal en francais : traduction obligatoire
            res = translate_item(it)
            if res != "ok":
                counts["lang"] += 1
                if res == "error" and live_state:
                    seen[it["key"]] = nowt
                continue
        ntf = norm_title(it.get("title_fr") or it["title"])
        if any(similar(ntf, o) for o in recent + sent_now):    # doublon (y compris entre une source anglaise traduite et une francaise)
            counts["dup"] += 1
            if live_state:
                seen[it["key"]] = nowt
            continue
        img = prepare_image(it, state)
        found = edu.tip_for(" ".join([it.get("title_fr", ""), it.get("summary_fr", ""), it["title"], it["summary"]]), state, nowt)
        it["tip"] = found[1] if found else ""
        room = CAPTION_MAX - (0 if tg.to_channel else 14)
        if dry:
            print(f"\n--- {it['source']} | {it['ts']} | notif={it['notify']} | {'PHOTO' if img else 'texte seul'} ---\n"
                  f"{build_message(it, room if img else 3900)}")
            posted += 1
            sent_now.append(ntf)
            continue
        sent = False
        if img:
            ok, err = tg.send_photo(img[0], img[1], build_message(it, room), silent=not it["notify"])
            if ok:
                sent = True
            elif err != "photo":
                fatal = err == "fatal"
                break
            else:
                img = None                                     # image refusee par Telegram : on publie le texte seul
        if not sent:
            ok, err = tg.send(build_message(it), silent=not it["notify"])
            if not ok:
                fatal = err == "fatal"
                break
        posted += 1
        sent_now.append(ntf)
        if live_state:
            seen[it["key"]] = nowt
            titles.append([nowt, it["nt"]])
            if ntf != it["nt"]:
                titles.append([nowt, ntf])
            state["count"] += 1
            state["hcount"] += 1
            if found and it["tip"]:
                edu.mark_tip(state, found[0], nowt)
            if img and it.get("img_key"):
                state.setdefault("imgs", {})[it["img_key"]] = nowt
            posts = state.setdefault("posts", [])              # numeros des messages envoyes (pour retirer un message si besoin)
            posts.append([int(nowt), tg.last_id, it["source"]])
            del posts[:-300]
            save_json(STATE_FILE, state)
        time.sleep(SEND_PAUSE)

    sent_cal = 0
    if not preview_n:
        def cal_send(text: str, sound: bool) -> bool:
            if dry:
                print("\n--- CALENDRIER ---\n" + text)
                return True
            ok, err = tg.send(text, silent=not sound)
            time.sleep(SEND_PAUSE)
            return ok
        sent_cal = cal.run_calendar(state, cache, now, cal_send, log, first_run=first_run)

    if fatal and not dry and not preview_n and nowt - state.get("notified", 0) > 6 * 3600:
        state["notified"] = nowt
        tg.notify_owner("⚠️ <b>Bot d'actualités</b> : impossible de publier dans le canal (droits de l'administrateur ? bot retiré ?). "
                        "Vérifie que le bot est administrateur avec le droit de publier.")

    # menage de l'etat
    cutoff = nowt - 4 * 86400
    for k in [k for k, v in seen.items() if v < cutoff]:
        del seen[k]
    state["titles"] = [t for t in titles if nowt - t[0] < 12 * 3600][-500:]
    if "imgs" in state:
        state["imgs"] = {k: v for k, v in state["imgs"].items() if nowt - v < 3 * 86400}
    if first_run and live_state:
        state["seeded"] = True
    if live_state:
        save_json(STATE_FILE, state)
        flag = os.environ.get("NEWS_POSTED_FLAG")
        if flag and (posted or sent_cal):
            Path(flag).write_text("1")
    if not dry:
        save_json(CACHE_FILE, cache)
    where = "CANAL" if tg.to_channel else "apercu prive"
    log(f"flux {stats['ok']} ok/{stats['304']}x304/{stats['err']} err | nouveaux {counts['new']} (deja vus {counts['seen']}, "
        f"anciens {counts['old']}, filtres {counts['filtered']}, doublons {counts['dup']}, sans traduction {counts['lang']}) | "
        f"postes {posted} + calendrier {sent_cal} -> {where} | total du jour {state['count']} (heure {state['hcount']}/{MAX_PER_HOUR})")


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
            nimg = sum(1 for i in items if i["image"])
            nsum = sum(1 for i in items if i["summary"])
            return (f" OK  {len(items):>3} art. | dernier il y a {newest:>8} | {n24:>3}/24h | img {nimg:>3} | extr. {nsum:>3} "
                    f"{f['lang'] if 'lang' in f else '--'} {f['name']:<20} {items[0]['title'][:44] if items else ''}")
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
        tg.send(m, silent=True)
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
