"""
news_translate.py - traduction anglais -> francais pour le canal "Info du monde" : gratuite, hors ligne, sans cle.

Moteur : OPUS-MT "tc-big en-fr" (Helsinki-NLP, licence CC-BY 4.0 : usage commercial autorise, avec attribution) execute par
CTranslate2 sur le processeur du robot. Aucun texte n'est envoye a un service tiers.
  Attribution : Tiedemann & Thottingal, "OPUS-MT - Building open translation services for the World" (EAMT 2020) ;
  conversion CTranslate2 int8 : depot Hugging Face craftwise/ct2-opus-mt-tc-big-en-fr-int8 (version figee ci-dessous).
(Un modele plus connu, NLLB-200, a ete ecarte : sa licence interdit l'usage commercial.)

Ce que fait ce module, autour du modele :
  - remet les titres "En Majuscules De Titre" en phrase normale (le modele les copie sinon sans les traduire) ;
  - decoupe les textes en phrases (le modele en oublie quand on lui en donne plusieurs d'un coup) ;
  - protege le jargon financier que le modele deforme (sigles, idiomes de chiffres publies) et met les nombres a la francaise ;
  - verifie le resultat (vide, anglais restant, longueur absurde, boucles, confiance du modele) : en cas de doute il
    renvoie None et l'article n'est PAS publie - on prefere se taire que publier une traduction fausse ou de l'anglais brut.

Usage :
    python news_translate.py --ensure                    # telecharge le modele (~235 Mo) s'il manque
    python news_translate.py "Fed holds rates steady"    # traduit un texte pour essai (affiche aussi la confiance)
"""
from __future__ import annotations
import hashlib
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import List, Optional

DIR = Path(__file__).parent
MODELS_DIR = Path(os.environ.get("NEWS_MODELS_DIR") or (DIR / "news_models"))
MODEL_DIR = MODELS_DIR / "opus_tc_big_en_fr"

REPO = "craftwise/ct2-opus-mt-tc-big-en-fr-int8"
REVISION = "5a4bdbbc160d8e18c10e69109e990817f1177e33"          # version figee (pas de surprise si le depot change)
MODEL_SHA256 = "e8f65078dc16af2996ff74cbc4c9b8885c4c4bd0f92d727d9f7bdf4ac32871a8"
FILES = {"config.json": 223, "model.bin": 233813642, "shared_vocabulary.json": 971990,
         "source.spm": 802408, "target.spm": 819955}

MIN_CONFIDENCE = float(os.environ.get("NEWS_MT_MIN_CONF") or -1.0)   # log-proba moyenne par mot ; voir le diagnostic en bas


def ready() -> bool:
    return all((MODEL_DIR / n).exists() and (MODEL_DIR / n).stat().st_size == s for n, s in FILES.items())


def ensure(log=print) -> Path:
    """Telecharge le modele s'il manque (verifie taille et empreinte). Leve une exception en cas d'echec."""
    import requests
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in FILES.items():
        dst = MODEL_DIR / name
        if dst.exists() and dst.stat().st_size == size:
            continue
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        tmp = dst.with_name(dst.name + ".part")
        last = None
        for attempt in range(4):
            try:
                t0 = time.time()
                h = hashlib.sha256()
                with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "GOTA-NewsBot/1.0"}) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            h.update(chunk)
                if tmp.stat().st_size != size:
                    raise IOError(f"taille inattendue ({tmp.stat().st_size} au lieu de {size})")
                if name == "model.bin" and h.hexdigest() != MODEL_SHA256:
                    raise IOError("empreinte du modele differente de celle attendue")
                tmp.replace(dst)
                log(f"{name} : {size / 1e6:.1f} Mo en {time.time() - t0:.0f}s")
                break
            except Exception as e:
                last = e
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"telechargement de {name} impossible : {last}")
    return MODEL_DIR


# ------------------------------------------------------------------------------------------ avant / apres le modele
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " ", " ": " ", " ": " "})
_FRAC = {"¼": ".25", "½": ".5", "¾": ".75", "⅓": ".33", "⅔": ".67", "⅛": ".125"}
_FRAC_CLASS = "[" + "".join(_FRAC) + "]"
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_ABBREV = {"inc", "corp", "ltd", "co", "st", "mr", "ms", "mrs", "dr", "vs", "no", "jr", "sr", "mt", "u.s", "u.k", "e.g", "i.e", "etc"}
_BRAND = re.compile(r"(?i)^(?:investingLive|FXStreet|Bloomberg|CNBC|Seeking Alpha)\b[\s:,\-–—]*")
_MONTHS = {"jan": "janvier", "feb": "février", "mar": "mars", "apr": "avril", "may": "mai", "jun": "juin", "jul": "juillet",
           "aug": "août", "sep": "septembre", "sept": "septembre", "oct": "octobre", "nov": "novembre", "dec": "décembre"}
_MONTH_RE = re.compile(r"(?i)\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?")
_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "if", "in", "of", "on", "or", "the", "to", "vs", "via", "with", "from",
          "into", "over", "amid", "after", "than", "not", "no", "is", "are", "up", "off", "out", "down", "per"}
# noms propres qui sont aussi des mots courants (ex. "Fed" / "fed") : on ne les met jamais en minuscules
_KEEP = {"Fed", "Treasury", "Treasuries", "Congress", "Senate", "House", "White", "Wall", "Street", "Reserve", "Federal", "Supreme",
         "Court", "Union", "Commission", "Dow", "Jones", "Hang", "Seng", "Bank", "Bund", "Brent"}


def normalize(t: str) -> str:
    t = t.translate(_QUOTES)
    t = re.sub(r"(\d)\s*(" + _FRAC_CLASS + ")", lambda m: m.group(1) + _FRAC[m.group(2)], t)      # 2¼ % -> 2.25 %
    t = re.sub("(" + _FRAC_CLASS + ")", lambda m: "0" + _FRAC[m.group(1)], t)
    t = _MONTH_RE.sub(lambda m: f"{m.group(1)} {_MONTHS[m.group(2).lower()]}", t)
    return re.sub(r"\s+", " ", t).strip()




def split_sentences(t: str) -> List[str]:
    out, start = [], 0
    for m in re.finditer(r"([.!?])\s+(?=[A-Z\"'(\[])", t):
        before = t[start:m.start()].split(" ")[-1].lower().rstrip(".")
        if m.group(1) == "." and (before in _ABBREV or len(before) == 1):
            continue
        out.append(t[start:m.end()].strip())
        start = m.end()
    out.append(t[start:].strip())
    return [s for s in out if s]


# ---- jargon : anglais -> anglais plus clair pour le modele (avant) ; francais -> francais usuel (apres) ---------------------
_COMMO = {"gold": "du cours de l'or", "silver": "du cours de l'argent", "oil": "du cours du pétrole", "crude oil": "du cours du pétrole",
          "bitcoin": "du bitcoin", "ethereum": "d'ethereum", "copper": "du cours du cuivre", "natural gas": "du cours du gaz naturel",
          "platinum": "du cours du platine", "palladium": "du cours du palladium"}
_FIG =r"(NFP|payrolls?|jobs|CPI|PPI|PCE|GDP|PMI|ISM|retail sales)"
PRE = [
    (rf"(?i)\b{_FIG}\s+miss(?:es|ed)?\b", r"disappointing \1 figures"),
    (rf"(?i)\b{_FIG}\s+beat(?:s)?\b", r"stronger-than-expected \1 figures"),
    (r"(?i)\bsoft(?= (?:US |U\.S\. )?(?:jobs?|labou?r|data|payrolls?|retail|cpi|gdp|pmi|reading|economic|hiring|employment))", "weak"),
    (r"(?i)\bFX news wrap\b", "forex market summary"),
    (r"(?i)\bpric(?:e|es|ed|ing)\s+in\b", lambda m: {"price": "anticipate", "prices": "anticipates", "priced": "anticipated",
                                                       "pricing": "anticipating"}[m.group(0).split()[0].lower()]),
    (r"(?i)\bslip(s|ped|ping)?\s+ahead of\b", lambda m: {"": "fall before", "s": "falls before", "ped": "fell before",
                                                          "ping": "falling before"}[(m.group(1) or "").lower()]),
    (r"(?i)\b(gold|silver|oil|crude oil|bitcoin|ethereum|copper|natural gas|platinum|palladium)\s+(?:price\s+)?(?:forecast|outlook|analysis)\s*:",
     lambda m: f"Prévisions {_COMMO[m.group(1).lower()]} :"),
    (r"(?i)\b([A-Z]{3}/[A-Z]{3})\s+(?:price\s+)?(?:forecast|outlook|analysis)\s*:", r"Prévisions \1 :"),
    (r"(?i)(?<!hourly )(?<!weekly )(?<!average )(?<!real )(?<!wage )\bearnings\b(?! per share)", "corporate results"),
    (r"(?i)\bhawkish\b", "restrictive"),
    (r"(?i)\bdovish\b", "accommodative"),
    (r"\bNFP\b", "Non-Farm Payrolls"),
    (r"\bBTC\b", "Bitcoin"),
    (r"(?i)\bcrypto\b(?![.\w-])", "cryptocurrency"),
    (r"(?i)\bbears\b", "sellers"),
    (r"(?i)\bbulls\b", "buyers"),
    (r"(?i)\bcarry trades?\b", "carry-trade"),
]
POST = [
    (r"(?i)\b(?:non[- ]farm payrolls?|(?:paies?|salaires?|emplois?) non agricoles?)(?:\s*\(NFP\))?", "emplois non agricoles (NFP)"),
    (r"(?i)\bcryptographiques?\b", "crypto"),
    (r"\bcarry-trade\b", "carry trade"),
    (r"(?i)\s*\bpour cent\b", " %"),
    (r"(?<![\w-])(\d+)\.(\d+)", r"\1,\2"),                 # 0.1 -> 0,1 (decimale francaise ; "GPT-5.1" reste intact)
    (r"\s*⁇\s*", " "),                                # symbole "caractere inconnu" du modele : jamais affiche
]
_PRE = [(re.compile(a), b) for a, b in PRE]
_POST = [(re.compile(a), b) for a, b in POST]


def _like(src: str, new: str) -> str:
    """Remplacement qui garde la majuscule initiale du mot remplace (debut de phrase)."""
    return new[:1].upper() + new[1:] if src[:1].isupper() and not src.isupper() else new

# mots anglais qui ne peuvent pas apparaitre dans un francais correct (on a retire "on" et "but" qui existent en francais)
_EN_STOP = {"the", "of", "and", "to", "in", "for", "with", "is", "are", "as", "at", "by", "from", "that", "this", "its",
            "has", "have", "will", "after", "over", "amid", "says", "said"}


_EN_LEAK = {"forecast", "outlook", "price", "prices", "sellers", "buyers", "market", "markets", "weekly", "amid", "says", "said"}


def good(src: str, out: str) -> bool:
    """Garde-fou : refuse les traductions vides, restees en anglais, de longueur absurde ou bouclees."""
    ws, wo = src.split(), out.split()
    if not wo or not out.strip():
        return False
    if not (0.45 <= len(wo) / max(1, len(ws)) <= 2.6):
        return False
    low = [re.sub(r"[^a-z]", "", w.lower()) for w in wo]
    if sum(1 for w in low if w in _EN_STOP) >= max(3, int(0.15 * len(wo))):
        return False
    if any(w in _EN_LEAK for w in low):                    # un mot anglais evident est reste : traduction manquee
        return False
    if any(low[i] and low[i] == low[i + 1] == low[i + 2] == low[i + 3] for i in range(len(low) - 3)):
        return False
    if out.strip().lower() == src.strip().lower() and len(ws) > 3:
        return False
    return True


class Translator:
    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        import ctranslate2
        import sentencepiece as spm
        self.ct = ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8", inter_threads=1,
                                         intra_threads=int(os.environ.get("NEWS_MT_THREADS") or 4))
        self.sp_s = spm.SentencePieceProcessor()
        self.sp_s.load(str(model_dir / "source.spm"))
        self.sp_t = spm.SentencePieceProcessor()
        self.sp_t.load(str(model_dir / "target.spm"))

    # -- mots courants : "Slow" -> "slow", mais "Brazil" reste "Brazil" : on garde la graphie que le modele juge la plus probable
    def _cost(self, w: str) -> float:
        pieces = self.sp_s.encode(w, out_type=str)
        return sum(self.sp_s.get_score(self.sp_s.piece_to_id(p)) for p in pieces)

    def _common_lower(self, w: str) -> bool:
        return self._cost(w.lower()) > self._cost(w) + 1.0        # marge : un nom propre ambigu (Novartis, Clayton) reste intact

    def is_title_case(self, t: str) -> bool:
        """Vrai pour "Jobs Slow but Inflation Keeps Fed on Alert" ; faux pour une simple liste de noms ("Pfizer, Sanofi et Novartis")."""
        words = [w for w in _WORD.findall(t) if len(w) > 1 and w.lower() not in _SMALL]
        if len(words) < 3 or sum(1 for w in words if w[0].isupper()) / len(words) < 0.8:
            return False
        return sum(1 for w in words if w[0].isupper() and self._common_lower(w)) >= max(2, len(words) // 3)

    def untitle(self, t: str) -> str:
        def fix(m: "re.Match[str]") -> str:
            w = m.group(0)
            if len(w) < 2 or w.isupper() or not w[0].isupper() or any(c.isupper() for c in w[1:]) or w in _KEEP:
                return w
            return w.lower() if self._common_lower(w) else w
        out = _WORD.sub(fix, t)
        out = re.sub(r"^([a-z])", lambda m: m.group(1).upper(), out)
        return re.sub(r"([:;!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)

    def safe_chars(self, t: str) -> str:
        """Remplace les lettres inconnues du modele (c avec caron...) par leur base ASCII, sinon il ecrit un symbole bizarre."""
        unk = self.sp_s.unk_id()
        if unk not in self.sp_s.encode(t):
            return t
        out = []
        for ch in t:
            if ord(ch) < 128 or unk not in self.sp_s.encode(ch):
                out.append(ch)
            else:
                out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode())
        return "".join(out)

    def prep(self, t: str) -> str:
        t = _BRAND.sub("", normalize(t))
        parts = re.split(r"(?<=:)\s+", t)               # "Silver Price Forecast: Bears crowd..." : chaque proposition a son cas
        t = " ".join(self.untitle(p) if self.is_title_case(p) else p for p in parts)
        for rx, rep in _PRE:
            t = rx.sub(lambda m, rep=rep: _like(m.group(0), rep(m) if callable(rep) else m.expand(rep)), t)
        return self.safe_chars(t)

    def post(self, t: str) -> str:
        for rx, rep in _POST:
            t = rx.sub(rep, t)
        t = t.replace("’", "'")
        t = re.sub(r"\s+([,.;!?])", r"\1", t)
        return re.sub(r"\s+", " ", t).strip()

    def _run(self, sentences: List[str]) -> List[tuple]:
        """[(texte traduit, confiance moyenne par mot en log-proba : plus c'est proche de 0, plus le modele est sur)]"""
        src = [self.sp_s.encode(s, out_type=str) + ["</s>"] for s in sentences]
        res = self.ct.translate_batch(src, beam_size=4, max_decoding_length=256, repetition_penalty=1.1, return_scores=True)
        return [(self.sp_t.decode(r.hypotheses[0]), float(r.scores[0])) for r in res]      # scores deja divises par la longueur

    def en_to_fr(self, texts: List[str], scores: Optional[list] = None) -> List[Optional[str]]:
        """Traduit chaque texte ; None si le resultat n'est pas fiable (l'appelant n'envoie alors rien).
        `scores` (liste vide fournie par l'appelant) recoit la confiance minimale de chaque texte, pour reglage/diagnostic."""
        plans, jobs, names = [], [], []
        for t in texts:
            p = self.prep(t) if t.strip() else ""
            sents = split_sentences(p) if p else []
            plans.append(len(sents))
            jobs.extend(sents)
            names.append({self.safe_chars(w): w for w in re.findall(r"\w+", normalize(t)) if self.safe_chars(w) != w})
        outs = self._run(jobs) if jobs else []
        res, k = [], 0
        for t, n, nm in zip(texts, plans, names):
            parts = outs[k:k + n]
            k += n
            fr = self.post(" ".join(p for p, _ in parts)) if parts else ""
            for ascii_form, original in nm.items():       # noms propres : on rend les accents d'origine
                fr = fr.replace(ascii_form, original)
            conf = min((sc for _, sc in parts), default=-99.0)
            if scores is not None:
                scores.append(conf)
            res.append(fr if good(normalize(t), fr) and conf >= MIN_CONFIDENCE else None)
        return res


def load() -> Translator:
    """Charge le traducteur ; leve une exception s'il est indisponible (modele absent, bibliotheque manquante)."""
    if not ready():
        raise FileNotFoundError("modele de traduction absent (python news_translate.py --ensure)")
    return Translator()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--ensure" in sys.argv:
        ensure()
        print("modele pret :", MODEL_DIR)
    else:
        tr = load()
        sc: list = []
        for s, f in zip(sys.argv[1:], tr.en_to_fr(sys.argv[1:], sc)):
            print(f"{s}\n  -> {f}   (confiance {sc[len(sc) - len(sys.argv[1:]) + sys.argv[1:].index(s)]:.2f})")
