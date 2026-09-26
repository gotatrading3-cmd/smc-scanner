"""
signal_publisher.py - Publication Telegram pour DEUX audiences :

  VIP    (groupe prive)  : TOUS les signaux, en detail (fiche complete + analyse), suivi TP/SL, bilans, guide
  PUBLIC (groupe normal) : QUELQUES signaux par jour (fiche complete, "deja dans le VIP"), leur suivi (gains ET pertes),
                           point du jour, conseils ; chaque image porte un lien cliquable pour nous ecrire

MODE APERCU PAR DEFAUT : tant qu'une audience n'est pas "live" (variable SIGNALS_LIVE pour le VIP, PUBLIC_LIVE pour le
public) ET qu'un groupe de destination est configure, ses messages arrivent uniquement dans TON chat prive (libelle APERCU).

Principes : messages courts et propres, sans avertissements ; resultats reels (pertes incluses) ; aucune promesse de gain.
Le bouton et le lien "nous ecrire" (groupe public) pointent vers un CONTACT - jamais vers le lien d'invitation du VIP
(il ouvrirait le groupe a tout le monde). Les messages "deja en profit" ne sont ecrits que lorsque c'est vrai (TP atteint).
"""
from __future__ import annotations
import html
import json
import os
import time
from pathlib import Path
from typing import List, Optional

import requests

from notifier import TelegramNotifier
from signal_engine import Signal, CHECK_LABELS, SCORED
from signal_data import symbol_meta, UNIVERSE
from signal_content import account_block, welcome

DIR = Path(__file__).parent
CHANNEL_CFG = DIR / "channel.json"
DEFAULT_CFG = {
    "live": False,                  # groupe VIP : publication reelle des signaux detailles ?
    "public_live": False,           # groupe public : publication reelle (teasers, suivi, marketing) ?
    "vip_chat_id": "",              # groupe / canal VIP (prive)
    "channel_id": "",               # groupe public
    "contact_link": "",             # https://t.me/<utilisateur> : bouton "nous ecrire" (groupe public uniquement)
    "account_link": "",             # lien partenaire d'ouverture de compte de trading (messages epingles + rappel hebdomadaire)
    "account_button_text": "💼 Ouvrir un compte de trading",
    "private_link": "",             # lien d'invitation du VIP : n'est JAMAIS publie
    "brand": "GOTA TRADING",
    "timeframe": "4h",              # timeframe d'entree (biais de tendance : D1 pour 4h, H4 pour 1h)
    "symbols": [],                  # vide = toutes les paires disponibles avec la source de prix
    "min_score": 4,
    "max_signals_per_day": 8,
    "max_active": 10,
    "public_signals_per_day": 2,    # signaux montres en entier dans le groupe public (le VIP les recoit TOUS)
    "public_progress": True,        # suivi public des signaux montres (objectif atteint / stop touche) : gains ET pertes
    "public_daily_max": 14,         # plafond de posts programmes / jour dans le groupe public
    "cta_every": 7,                 # 1 message public sur 7 porte l'invitation "nous ecrire" (les 6 autres n'en portent pas)
    "daily_recap": True,
    "weekly_recap": True,
    "button_text": "✉️ Nous écrire pour rejoindre le VIP",
    # Ce que contient REELLEMENT le groupe VIP (le marketing ne promet rien d'autre) :
    "vip_perks": [
        "Tous les signaux en direct : entrée, stop loss et 3 objectifs",
        "L'analyse détaillée de chaque setup",
        "Le suivi de chaque trade jusqu'au bout, gains comme pertes",
        "Mes analyses personnelles des marchés",
    ],
}

CHANNEL_ABOUT_PUBLIC = ("Analyse des marchés en continu : quelques signaux en direct, le suivi des trades, les conseils du jour et les résultats. "
                        "Tous les signaux, l'analyse détaillée et le suivi complet sont dans le groupe VIP : écris-nous pour le rejoindre.")


def pinned_text(brand: str = "GOTA TRADING", account_link: str = "") -> str:
    """Guide du groupe VIP a epingler une fois (sans avertissement)."""
    return (f"📌 <b>{html.escape(brand)} — groupe VIP : mode d'emploi</b>\n\n"
            "Pour chaque signal, tu reçois :\n"
            "• la <b>fiche</b> : entrée, stop loss, TP1 · TP2 · TP3 et ratio R:R\n"
            "• l'<b>analyse</b> : contexte, zone, confirmations, plan de gestion\n"
            "• le <b>suivi</b> : chaque objectif atteint ou stop touché, annoncé ici\n\n"
            "<b>Gestion suggérée</b>\n"
            "• 40 % à TP1 (puis stop à l'entrée) · 30 % à TP2 · 30 % à TP3\n"
            "• Risque conseillé : 1 % du capital maximum par trade\n"
            "• 1R = la perte prévue si le stop est touché\n"
            "• Prix indicatifs : applique les distances (pips / points) à ton propre prix d'entrée"
            + account_block(account_link))


def load_cfg() -> dict:
    """channel.json (local) puis variables d'environnement (GitHub : secrets / variables) qui ont priorite."""
    cfg = dict(DEFAULT_CFG)
    if CHANNEL_CFG.exists():
        try:
            cfg.update(json.loads(CHANNEL_CFG.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[PUB] channel.json illisible : {e}")
    env = os.environ

    def ev(name: str) -> Optional[str]:
        v = env.get(name)
        return v.strip() if v and v.strip() else None

    truthy = ("1", "true", "yes", "oui")
    for name, key in (("SIGNALS_LIVE", "live"), ("PUBLIC_LIVE", "public_live"), ("SIGNALS_PUBLIC_PROGRESS", "public_progress")):
        if ev(name) is not None:
            cfg[key] = ev(name).lower() in truthy
    for name, key in (("VIP_CHAT_ID", "vip_chat_id"), ("CHANNEL_ID", "channel_id"), ("CONTACT_LINK", "contact_link"),
                      ("ACCOUNT_LINK", "account_link"),
                      ("PRIVATE_LINK", "private_link"), ("SIGNALS_BRAND", "brand"), ("SIGNALS_TF", "timeframe")):
        if ev(name) is not None:
            cfg[key] = ev(name)
    for name, key in (("SIGNALS_MIN_SCORE", "min_score"), ("SIGNALS_MAX_PER_DAY", "max_signals_per_day"),
                      ("PUBLIC_SIGNALS_PER_DAY", "public_signals_per_day"), ("CTA_EVERY", "cta_every")):
        if ev(name) is not None:
            try:
                cfg[key] = int(ev(name))
            except ValueError:
                pass
    if ev("SIGNALS_SYMBOLS") is not None:
        cfg["symbols"] = [s.strip().upper() for s in ev("SIGNALS_SYMBOLS").split(",") if s.strip()]
    return cfg


def save_cfg(cfg: dict) -> None:
    CHANNEL_CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ helpers de mise en forme
def _pf(p: float, digits: int) -> str:
    return f"{p:,.{digits}f}".replace(",", " ")


def _tf(tf: str) -> str:
    return {"15m": "M15", "60m": "H1", "240m": "H4"}.get(tf, tf)


def _len_txt(s: Signal, d: float) -> str:
    """Longueur (pips pour le forex, $ pour la crypto, points sinon)."""
    m = symbol_meta(s.display) or {}
    cls = UNIVERSE.get(s.display, {}).get("cls", "")
    if cls == "fx" and m.get("point"):
        return f"{d / (m['point'] * 10):.1f} pips"
    if cls == "crypto":
        return f"{d:,.0f} $".replace(",", " ")
    return f"{d:,.2f} pts".replace(",", " ")


def _dist_txt(s: Signal, level: float) -> str:
    return _len_txt(s, abs(level - s.entry))


def _acc(t: str) -> str:
    """Les libelles du moteur sont en ASCII ; on remet les accents pour l'affichage."""
    for a, b in (("liquidite", "liquidité"), ("empilee", "empilée"), ("noeud", "nœud"),
                 ("alignee", "alignée"), ("confirmee", "confirmée")):
        t = t.replace(a, b)
    return t


def _check_text(k: str, s: Signal, long_: bool) -> str:
    if k == "momentum":
        return f"RSI {s.rsi:.0f} en retournement"
    if k == "discount":
        return "Zone Discount" if long_ else "Zone Premium"
    if k == "volume":
        return "Volume Profile : zone au POC / nœud de volume"
    return _acc(CHECK_LABELS[k])


class Publisher:
    def __init__(self, cfg: Optional[dict] = None, log=print, audience: str = "vip"):
        self.cfg = cfg or load_cfg()
        self.log = log
        self.audience = audience                        # "vip" | "public"
        n = TelegramNotifier(silent=True)
        self.token = n.token
        self.owner_chat = n.chat_id
        if audience == "vip":
            flag, dest = self.cfg.get("live"), str(self.cfg.get("vip_chat_id", "")).strip()
        else:
            flag, dest = self.cfg.get("public_live"), str(self.cfg.get("channel_id", "")).strip()
        self.dest = dest
        self.live = bool(flag) and bool(dest)
        self.target = dest if self.live else self.owner_chat
        self.enabled = bool(self.token and self.target)
        self.silent = str(os.environ.get("SIGNALS_SILENT", "")).strip().lower() in ("1", "true", "yes", "oui")
        self.state: Optional[dict] = None               # etat du runner (compteur d'invitations cta_seq), branche par le runner

    # ------------------------------------------------------------ contact / marquage
    @property
    def handle(self) -> str:
        """@utilisateur du contact (depuis contact_link) ou ''."""
        link = str(self.cfg.get("contact_link", "")).strip()
        tail = link.split("t.me/", 1)[1].strip("/").split("?")[0] if link.startswith("https://t.me/") else ""
        return "@" + tail if tail and not tail.startswith("+") else ""

    def _tag(self) -> str:
        if self.live:
            return ""
        who = "groupe VIP" if self.audience == "vip" else "groupe public"
        return f"🧪 <b>APERÇU · {who}</b> · visible uniquement par toi\n\n"

    def _markup(self, with_button: bool, account: bool = False) -> Optional[str]:
        """Boutons sous le message : 'nous ecrire' (groupe public) et/ou 'ouvrir un compte' (messages epingles, rappel)."""
        rows = []
        link = str(self.cfg.get("contact_link", "")).strip()
        if with_button and self.audience == "public" and link.startswith("http"):
            rows.append([{"text": self.cfg["button_text"], "url": link}])
        acc = str(self.cfg.get("account_link", "")).strip()
        if account and acc.startswith("http"):
            rows.insert(0, [{"text": self.cfg.get("account_button_text") or "💼 Ouvrir un compte de trading", "url": acc}])
        return json.dumps({"inline_keyboard": rows}) if rows else None

    # ------------------------------------------------------------ bas niveau
    def _api(self, method: str, data: dict, files: Optional[dict] = None, retry: bool = True):
        if not self.enabled:
            self.log("[PUB] Telegram non configure")
            return None
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            r = requests.post(url, data=data, files=files, timeout=40)
            j = r.json()
        except Exception as e:
            self.log(f"[PUB] erreur reseau {method} : {str(e).replace(self.token, '***')[:160]}")
            return None
        if j.get("ok"):
            return j["result"]
        wait = j.get("parameters", {}).get("retry_after")
        if wait and retry:
            time.sleep(min(int(wait) + 1, 60))
            return self._api(method, data, files, retry=False)
        self.log(f"[PUB] {method} refuse : {j.get('description', '?')}")
        return None

    def notify_owner(self, text: str) -> None:
        """Message technique au PROPRIETAIRE uniquement (jamais a un groupe), ex. panne de la source de prix."""
        if self.token and self.owner_chat:
            self._api("sendMessage", {"chat_id": self.owner_chat, "text": text, "parse_mode": "HTML"})

    def _quiet(self, notify: bool) -> bool:
        """Envoi silencieux (sans sonnerie) : apercus, tests de nuit (SIGNALS_SILENT) et posts courants du groupe public."""
        return (not notify) or (not self.live) or self.silent

    def send_text(self, text: str, reply_to: Optional[int] = None, button: bool = False, notify: bool = True,
                  account: bool = False) -> Optional[int]:
        data = {"chat_id": self.target, "text": self._tag() + text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if self._quiet(notify):
            data["disable_notification"] = "true"
        if reply_to:
            data["reply_to_message_id"] = reply_to
            data["allow_sending_without_reply"] = "true"
        mk = self._markup(button, account)
        if mk:
            data["reply_markup"] = mk
        res = self._api("sendMessage", data)
        return res["message_id"] if res else None

    def send_photo(self, path: str, caption: str, reply_to: Optional[int] = None, button: bool = False, notify: bool = True) -> Optional[int]:
        data = {"chat_id": self.target, "caption": (self._tag() + caption)[:1024], "parse_mode": "HTML"}
        if self._quiet(notify):
            data["disable_notification"] = "true"
        if reply_to:
            data["reply_to_message_id"] = reply_to
            data["allow_sending_without_reply"] = "true"
        mk = self._markup(button)
        if mk:
            data["reply_markup"] = mk
        with open(path, "rb") as f:
            res = self._api("sendPhoto", data, files={"photo": f})
        return res["message_id"] if res else None

    def cta_line(self) -> str:
        """Ligne cliquable sous chaque image du groupe public : ouvre le contact pour rejoindre le VIP."""
        link = str(self.cfg.get("contact_link", "")).strip()
        if not link.startswith("http"):
            return ""
        return f'👉 <a href="{html.escape(link, quote=True)}">Écris-nous pour rejoindre le groupe VIP</a>'

    # ------------------------------------------------------------ rythme des invitations : 1 message sur cta_every
    def cta_due(self) -> bool:
        """True si le PROCHAIN message public doit porter l'invitation 'nous ecrire'. Les autres messages n'en portent pas :
        par defaut 6 messages normaux, puis 1 avec l'invitation (cta_every = 7)."""
        if self.state is None:
            return True
        return int(self.state.get("cta_seq", 0)) + 1 >= int(self.cfg.get("cta_every", 7) or 7)

    def _tick(self, cta: bool) -> None:
        if self.state is not None:
            self.state["cta_seq"] = 0 if cta else int(self.state.get("cta_seq", 0)) + 1

    def post_public(self, text: str, img: Optional[str] = None, reply_to: Optional[int] = None, button: bool = True,
                    notify: bool = False, account: bool = False, cta: Optional[bool] = None) -> Optional[int]:
        """Post du groupe PUBLIC. `cta` : le message porte l'invitation 'nous ecrire' (bouton + lien cliquable sous l'image) ;
        par defaut 1 message sur 7 (cta_due), pour ne pas saturer le groupe. notify=False : sans sonnerie ; True : nouveau signal."""
        if cta is None:
            cta = self.cta_due()
        button = button and cta
        if img and os.path.exists(img):
            line = self.cta_line() if cta else ""
            cap = text if (not line or str(self.cfg.get("contact_link", "")).strip() in text) else f"{text}\n\n{line}"
            if len(self._tag() + cap) > 1024:                  # legende trop longue : image (+ lien), puis le texte en reponse
                mid = self.send_photo(img, line, reply_to=reply_to, button=button, notify=notify)
                if mid:
                    self.send_text(text, reply_to=mid, button=False, notify=False)
            else:
                mid = self.send_photo(img, cap, reply_to=reply_to, button=button, notify=notify)
        else:
            mid = self.send_text(text, reply_to=reply_to, button=button, notify=notify, account=account)
        if mid:
            self._tick(cta)
        return mid

    # ------------------------------------------------------------ verification (ne poste rien)
    def check_target(self) -> dict:
        out = {"ok": False, "detail": ""}
        who = "VIP" if self.audience == "vip" else "public"
        if not self.token:
            out["detail"] = "token Telegram absent"
            return out
        if not self.dest:
            out["detail"] = f"groupe {who} : identifiant non configure"
            return out
        try:
            me = requests.get(f"https://api.telegram.org/bot{self.token}/getMe", timeout=20).json()
            chat = requests.post(f"https://api.telegram.org/bot{self.token}/getChat", data={"chat_id": self.dest}, timeout=20).json()
            if not chat.get("ok"):
                out["detail"] = f"groupe {who} introuvable : {chat.get('description')}. Le bot y est-il administrateur ?"
                return out
            mem = requests.post(f"https://api.telegram.org/bot{self.token}/getChatMember",
                                data={"chat_id": self.dest, "user_id": me["result"]["id"]}, timeout=20).json()
            m = mem.get("result", {})
            title = chat["result"].get("title", self.dest)
            if m.get("status") == "administrator" and m.get("can_post_messages", True):
                out.update(ok=True, detail=f"OK - le bot @{me['result']['username']} peut publier dans « {title} » ({who})")
            else:
                out["detail"] = f"le bot est '{m.get('status')}' dans « {title} » ({who}) : il doit etre ADMINISTRATEUR"
        except Exception as e:
            out["detail"] = str(e).replace(self.token, "***")[:200]
        return out

    # ------------------------------------------------------------ VIP : signal detaille
    def signal_caption(self, s: Signal, digits: int) -> str:
        """Legende de la fiche VIP : niveaux copiables (un appui copie le prix), distances, risque / reward."""
        long_ = s.direction == "LONG"
        f = lambda v: _pf(v, digits)
        d = lambda v: _dist_txt(s, v)
        rr = abs(s.tps[-1] - s.entry) / max(s.risk, 1e-12)
        cap = [f"{'🟢' if long_ else '🔴'} <b>{'ACHAT' if long_ else 'VENTE'} {html.escape(s.display)}</b> · {_tf(s.tf)}",
               "",
               f"📍 Entrée   <code>{f(s.entry)}</code>",
               f"🛑 Stop loss   <code>{f(s.sl)}</code>   <i>−{d(s.sl)}</i>",
               f"🎯 TP1   <code>{f(s.tps[0])}</code>   <i>+{d(s.tps[0])}</i>",
               f"🎯 TP2   <code>{f(s.tps[1])}</code>   <i>+{d(s.tps[1])}</i>",
               f"🎯 TP3   <code>{f(s.tps[2])}</code>   <i>+{d(s.tps[2])}</i>",
               "",
               f"⚖️ Risque <b>{d(s.sl)}</b> · Reward <b>{d(s.tps[2])}</b> · R:R 1 : {rr:.0f}",
               f"Grade {s.grade}"]
        return "\n".join(cap)

    def analysis_text(self, s: Signal, digits: int) -> str:
        """L'analyse detaillee (reponse sous la fiche VIP) : pourquoi ce signal, ou est la zone, comment gerer."""
        long_ = s.direction == "LONG"
        f = lambda v: _pf(v, digits)
        htf = "journalière" if s.tf == "240m" else "H4"
        zone = ("Order Block " if s.zone_kind == "OB" else "Fair Value Gap ") + ("haussier" if long_ else "baissier")
        ok = [_check_text(k, s, long_) for k in SCORED if s.checks.get(k)]
        miss = [_acc(CHECK_LABELS[k].split(" (")[0]) for k in SCORED if not s.checks.get(k)]
        lines = [f"🧠 <b>Analyse · {html.escape(s.display)} · {_tf(s.tf)}</b>", "",
                 "<b>Contexte</b>",
                 f"Tendance {htf} {'haussière' if long_ else 'baissière'} : moyennes 50 / 200 alignées, prix "
                 f"{'au-dessus' if long_ else 'en dessous'} de la moyenne 200.", "",
                 "<b>Zone d'entrée</b>",
                 f"{zone} <code>{f(s.zone_lo)} – {f(s.zone_hi)}</code> ({_len_txt(s, abs(s.zone_hi - s.zone_lo))}) : "
                 f"retest frais, bougie de rejet confirmée à la clôture de {s.signal_time[11:16]} UTC.", "",
                 f"<b>Confirmations ({s.score}/6)</b>"]
        lines += [f"✓ {t}" for t in ok]
        if miss:
            lines.append(f"• Non remplies : {' · '.join(miss)}")
        lines += ["", "<b>Plan de trade</b>",
                  f"• Stop <code>{f(s.sl)}</code> : {'sous' if long_ else 'au-dessus de'} la zone, à environ {s.risk / s.atr:.1f} ATR de l'entrée",
                  "• 40 % à TP1 (puis stop à l'entrée) · 30 % à TP2 · 30 % à TP3",
                  "• Signal valable tant que le prix reste proche de l'entrée (moins de 0,35 R d'écart)",
                  "• Risque conseillé : 1 % du capital maximum par trade"]
        return "\n".join(lines)

    def post_signal(self, s: Signal, img_path: str, digits: int) -> Optional[int]:
        """VIP : fiche (image + niveaux copiables) puis l'analyse en reponse."""
        mid = self.send_photo(img_path, self.signal_caption(s, digits))
        if mid:
            self.send_text(self.analysis_text(s, digits), reply_to=mid)
        return mid

    def update_text(self, s: Signal, ev: dict, total_r: float) -> str:
        t, name = ev["type"], html.escape(s.display)
        if t == "TP1":
            return f"🎯 <b>TP1 atteint</b> · {name}\n+1R · stop à l'entrée"
        if t == "TP2":
            return f"🎯 <b>TP2 atteint</b> · {name}\n+2R"
        if t == "TP3":
            return f"🏆 <b>TP3 atteint</b> · {name}\nRésultat : {total_r:+.2f}R"
        if t == "SL":
            return f"🛑 <b>Stop touché</b> · {name}\nRésultat : {total_r:+.2f}R"
        if t == "BE":
            return f"⏹ <b>Clôturé à l'entrée</b> · {name}\nRésultat : {total_r:+.2f}R"
        return f"⌛ <b>Clôturé au temps</b> · {name}\nRésultat : {total_r:+.2f}R"

    def post_update(self, s: Signal, ev: dict, total_r: float, reply_to: Optional[int],
                    img_path: Optional[str] = None) -> Optional[int]:
        text = self.update_text(s, ev, total_r)
        if img_path and os.path.exists(img_path):
            return self.send_photo(img_path, text, reply_to=reply_to)
        return self.send_text(text, reply_to=reply_to)

    def post_recap_image(self, img_path: str, caption: str) -> Optional[int]:
        if self.audience == "public":
            return self.post_public(caption, img=img_path)
        return self.send_photo(img_path, caption)

    def _pin(self, mid: Optional[int]) -> None:
        if mid and self.live:
            self._api("pinChatMessage", {"chat_id": self.target, "message_id": mid, "disable_notification": "true"})

    def _pinned_by_us(self) -> Optional[int]:
        """Identifiant du message epingle du groupe s'il a ete publie par ce bot, sinon None."""
        chat, me = self._api("getChat", {"chat_id": self.target}), self._api("getMe", {})
        pm = (chat or {}).get("pinned_message") or {}
        if pm and me and (pm.get("from") or {}).get("id") == me.get("id"):
            return pm.get("message_id")
        return None

    def upsert_pinned(self, text: str, contact_button: bool) -> Optional[int]:
        """Message epingle (guide VIP / accueil public). En direct : MODIFIE le message epingle du bot s'il existe (ni doublon ni
        notification), sinon le publie et l'epingle. En apercu : envoye dans ton chat prive."""
        mid = self._pinned_by_us() if self.live else None
        if mid:
            data = {"chat_id": self.target, "message_id": mid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
            mk = self._markup(contact_button, account=True)
            if mk:
                data["reply_markup"] = mk
            return mid if self._api("editMessageText", data) else None
        mid = self.send_text(text, button=contact_button, notify=False, account=True)
        self._pin(mid)
        return mid

    def post_pinned(self) -> Optional[int]:
        """VIP : guide d'usage epingle, avec le bouton et le lien d'ouverture de compte."""
        return self.upsert_pinned(pinned_text(self.cfg["brand"], self.cfg.get("account_link", "")), contact_button=False)

    def post_welcome(self) -> Optional[int]:
        """PUBLIC : message d'accueil epingle, avec les boutons 'nous ecrire' et 'ouvrir un compte'."""
        return self.upsert_pinned(welcome(self.handle, self.cfg.get("account_link", "")), contact_button=True)
