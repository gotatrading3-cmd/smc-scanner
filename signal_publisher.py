"""
signal_publisher.py - Publication Telegram (canal communaute) : signaux, mises a jour, bilans, contenu.

MODE APERCU PAR DEFAUT : tant que channel.json n'a pas "live": true ET un channel_id valide,
TOUT est envoye uniquement dans TON chat prive (libelle "APERCU"). Rien n'est publie a ta communaute.

Principes de contenu (ecrits dans le code volontairement) :
- posts COURTS et propres : uniquement les niveaux (entree, stop, TP1/TP2/TP3, R:R), sans encombrement
- l'avertissement complet vit UNE fois, dans le message epingle + la description du canal (voir PINNED / --pinned)
- les resultats viennent de l'historique reel (signals_history.jsonl) : pertes incluses, jamais masquees
- aucune promesse de gain, aucune fausse rarete : l'appel vers le canal prive decrit ce qu'il contient vraiment
"""
from __future__ import annotations
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import requests

from notifier import TelegramNotifier
from signal_engine import Signal, CHECK_LABELS, SCORED
from signal_data import symbol_meta, UNIVERSE

DIR = Path(__file__).parent
CHANNEL_CFG = DIR / "channel.json"
DEFAULT_CFG = {
    "live": False,
    "channel_id": "",
    "private_link": "",
    "brand": "GOTA TRADING",
    "timeframe": "4h",              # timeframe d'entree (biais de tendance : D1 pour 4h, H4 pour 1h)
    "symbols": [],                  # vide = tous les instruments disponibles avec la source de prix
    "min_score": 4,
    "max_signals_per_day": 3,
    "max_active": 6,
    "promo_posts_per_day": 1,
    "education_posts_per_day": 1,
    "daily_recap": True,
    "weekly_recap": True,
    "button_text": "🔒 Analyse complète · canal privé",
    "private_perks": [
        "Analyse détaillée de chaque setup (multi-timeframe)",
        "Gestion du trade expliquée en direct",
        "Échanges avec la communauté",
    ],
}
# Avertissement COMPLET : affiche une seule fois (message epingle), jamais sur chaque fiche.
DISCLAIMER = ("⚠️ <b>Avertissement</b> : le trading comporte un risque élevé de perte en capital. Contenu informatif et "
              "éducatif : il ne constitue ni un conseil en investissement, ni une recommandation personnalisée. "
              "Les performances passées ne préjugent pas des performances futures.")
CHANNEL_ABOUT = ("Signaux forex & crypto : entrée, stop loss et objectifs. Chaque résultat est publié, pertes incluses. "
                 "Le trading comporte un risque de perte en capital ; contenu informatif, pas un conseil en investissement.")


def pinned_text(brand: str = "GOTA TRADING") -> str:
    return (f"📌 <b>{html.escape(brand)} — mode d'emploi</b>\n\n"
            "Chaque signal indique l'<b>entrée</b>, le <b>stop loss</b>, trois objectifs (<b>TP1 · TP2 · TP3</b>) et le ratio <b>R:R</b>.\n\n"
            "• <b>1R</b> = la perte prévue si le stop est touché\n"
            "• Gestion suggérée : 40 % à TP1 (puis stop à l'entrée) · 30 % à TP2 · 30 % à TP3\n"
            "• Risque conseillé : 1 % du capital maximum par trade\n"
            "• Prix indicatifs : applique les distances (pips / points) à ton propre prix d'entrée\n"
            "• Chaque signal est suivi jusqu'au bout — TP, stop ou expiration — gains et pertes publiés\n\n"
            + DISCLAIMER)


EDU_TIPS = [
    "💡 <b>L'Order Block (OB)</b>\nC'est la dernière bougie opposée avant un mouvement impulsif. Des ordres importants y sont souvent restés en attente : quand le prix revient, il réagit parfois. « Parfois » : on attend toujours une bougie de confirmation avant d'agir.",
    "💡 <b>Le Fair Value Gap (FVG)</b>\nUn vide sur 3 bougies : le prix est allé trop vite. Le marché revient souvent le combler, au moins en partie. C'est une zone d'intérêt, jamais une garantie.",
    "💡 <b>Le balayage de liquidité</b>\nLe prix perce un ancien plus bas (ou plus haut) pour déclencher les stops, puis repart dans l'autre sens. Une mèche qui reprend le niveau demande de l'attention — pas un achat automatique.",
    "💡 <b>Discount / Premium</b>\nOn coupe un mouvement en deux : sous le milieu = Discount (on cherche des achats), au-dessus = Premium (on cherche des ventes). Acheter cher dans une hausse dégrade le ratio gain / risque.",
    "💡 <b>Le risque avant tout</b>\nRisquer 1 % par trade, c'est pouvoir enchaîner 10 pertes de suite sans dépasser -10 %. Risquer 10 %, c'est être hors-jeu après quelques erreurs. Les gains viennent après la survie.",
    "💡 <b>Le « R »</b>\n1R = la perte prévue si le stop est touché. Un trade à +2R gagne deux fois ce qu'on risquait. Avec 40 % de trades gagnants à 2R on serait rentable… mais seule une centaine de trades mesurés le confirme, pas une impression.",
    "💡 <b>Pourquoi on saute des trades</b>\nSpread trop large, tendance contraire, stop trop serré : chaque filtre retire des setups. Moins de trades, mais des conditions réunies. Ne pas trader est une position.",
    "💡 <b>Le stop n'est pas négociable</b>\nLe reculer quand le prix s'en approche transforme une petite perte en grosse perte. On le place avant d'entrer, on ne l'éloigne jamais.",
    "💡 <b>Confluence ≠ certitude</b>\nPlusieurs confirmations augmentent la probabilité, jamais à 100 %. Un setup Grade A+ peut perdre. C'est pour ça que le risque par trade reste petit.",
    "💡 <b>Calculer sa taille de position</b>\nTaille = risque en $ ÷ (stop en pips × valeur du pip). Exemple : 1 000 $ de capital, risque 1 % = 10 $ ; stop à 20 pips, pip à 0,10 $ par micro-lot → 5 micro-lots (0,05 lot).",
    "💡 <b>Le stop à l'entrée (breakeven)</b>\nAprès TP1 on remonte le stop au prix d'entrée : le reste du trade ne peut plus perdre (hors frais). Contrepartie : on peut être sorti juste avant que le prix reparte. Chaque choix a un coût.",
    "💡 <b>Les sessions de marché</b>\nLondres et New York concentrent la liquidité. En dehors, les mouvements sont plus lents et les spreads plus larges : c'est pourquoi on note la session dans nos confirmations.",
    "💡 <b>Tenir un journal</b>\nNote chaque trade : raison, résultat, émotion. Sur 50 trades, ton journal t'apprend plus que n'importe quel « gourou » sur ce qui marche… ou pas pour toi.",
    "💡 <b>Démo d'abord</b>\nTester une stratégie en compte démo pendant plusieurs semaines coûte 0 $. Si tu ne sais pas la suivre en démo, tu ne la suivras pas en réel.",
]


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

    if ev("SIGNALS_LIVE") is not None:
        cfg["live"] = ev("SIGNALS_LIVE").lower() in ("1", "true", "yes", "oui")
    for name, key in (("CHANNEL_ID", "channel_id"), ("PRIVATE_LINK", "private_link"),
                      ("SIGNALS_BRAND", "brand"), ("SIGNALS_TF", "timeframe")):
        if ev(name) is not None:
            cfg[key] = ev(name)
    for name, key in (("SIGNALS_MIN_SCORE", "min_score"), ("SIGNALS_MAX_PER_DAY", "max_signals_per_day")):
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


def _pf(p: float, digits: int) -> str:
    return f"{p:,.{digits}f}".replace(",", " ")


class Publisher:
    def __init__(self, cfg: Optional[dict] = None, log=print):
        self.cfg = cfg or load_cfg()
        self.log = log
        n = TelegramNotifier(silent=True)
        self.token = n.token
        self.owner_chat = n.chat_id
        ch = str(self.cfg.get("channel_id", "")).strip()
        self.live = bool(self.cfg.get("live")) and bool(ch)
        self.target = ch if self.live else self.owner_chat
        self.enabled = bool(self.token and self.target)

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

    def _markup(self, with_button: bool) -> Optional[str]:
        link = str(self.cfg.get("private_link", "")).strip()
        if with_button and link.startswith("http"):
            return json.dumps({"inline_keyboard": [[{"text": self.cfg["button_text"], "url": link}]]})
        return None

    def _tag(self) -> str:
        return "" if self.live else "🧪 <b>APERÇU</b> · visible uniquement par toi (mode test)\n\n"

    def send_text(self, text: str, reply_to: Optional[int] = None, button: bool = False) -> Optional[int]:
        data = {"chat_id": self.target, "text": self._tag() + text, "parse_mode": "HTML",
                "disable_web_page_preview": "true"}
        if reply_to:
            data["reply_to_message_id"] = reply_to
            data["allow_sending_without_reply"] = "true"
        mk = self._markup(button)
        if mk:
            data["reply_markup"] = mk
        res = self._api("sendMessage", data)
        return res["message_id"] if res else None

    def notify_owner(self, text: str) -> None:
        """Message technique au PROPRIETAIRE uniquement (jamais au canal), ex. panne de la source de prix."""
        if self.token and self.owner_chat:
            self._api("sendMessage", {"chat_id": self.owner_chat, "text": text, "parse_mode": "HTML"})

    def send_photo(self, path: str, caption: str, reply_to: Optional[int] = None, button: bool = False) -> Optional[int]:
        data = {"chat_id": self.target, "caption": (self._tag() + caption)[:1024], "parse_mode": "HTML"}
        if reply_to:
            data["reply_to_message_id"] = reply_to
            data["allow_sending_without_reply"] = "true"
        mk = self._markup(button)
        if mk:
            data["reply_markup"] = mk
        with open(path, "rb") as f:
            res = self._api("sendPhoto", data, files={"photo": f})
        return res["message_id"] if res else None

    # ------------------------------------------------------------ verification du canal
    def check_channel(self) -> dict:
        """Verifie que le bot peut publier dans channel_id, SANS rien poster."""
        out = {"ok": False, "detail": ""}
        ch = str(self.cfg.get("channel_id", "")).strip()
        if not self.token:
            out["detail"] = "token Telegram absent (telegram.json)"
            return out
        if not ch:
            out["detail"] = "channel_id vide dans channel.json"
            return out
        try:
            me = requests.get(f"https://api.telegram.org/bot{self.token}/getMe", timeout=20).json()
            chat = requests.post(f"https://api.telegram.org/bot{self.token}/getChat", data={"chat_id": ch}, timeout=20).json()
            if not chat.get("ok"):
                out["detail"] = f"canal introuvable : {chat.get('description')}. Le bot est-il ajoute au canal ?"
                return out
            mem = requests.post(f"https://api.telegram.org/bot{self.token}/getChatMember",
                                data={"chat_id": ch, "user_id": me["result"]["id"]}, timeout=20).json()
            m = mem.get("result", {})
            title = chat["result"].get("title", ch)
            if m.get("status") == "administrator" and m.get("can_post_messages", True):
                out.update(ok=True, detail=f"OK - le bot @{me['result']['username']} peut publier dans « {title} »")
            else:
                out["detail"] = (f"le bot est '{m.get('status')}' dans « {title} » : "
                                 f"ajoute-le comme ADMINISTRATEUR avec le droit « Publier des messages »")
        except Exception as e:
            out["detail"] = str(e).replace(self.token, "***")[:200]
        return out

    # ------------------------------------------------------------ signaux
    def signal_parts(self, s: Signal, digits: int) -> tuple:
        """(legende courte, detail textuel des confirmations). La legende ne contient que l'essentiel :
        sens, instrument, entree, stop loss, TP1/TP2/TP3 et ratio. Le detail des confirmations est deja dans l'image."""
        long_ = s.direction == "LONG"
        f = lambda v: _pf(v, digits)
        rr = abs(s.tps[-1] - s.entry) / max(s.risk, 1e-12)
        cap = [f"{'🟢' if long_ else '🔴'} <b>{'ACHAT' if long_ else 'VENTE'} {html.escape(s.display)}</b> · {_tf(s.tf)}",
               "",
               f"📍 Entrée   <code>{f(s.entry)}</code>",
               f"🛑 Stop loss   <code>{f(s.sl)}</code>",
               f"🎯 TP1   <code>{f(s.tps[0])}</code>",
               f"🎯 TP2   <code>{f(s.tps[1])}</code>",
               f"🎯 TP3   <code>{f(s.tps[2])}</code>",
               "",
               f"R:R 1 : {rr:.0f}  ·  Grade {s.grade}"]
        htf = "journalière" if s.tf == "240m" else "H4"
        ok_lines = [f"Tendance {htf} {'haussière' if long_ else 'baissière'}",
                    f"Retest {'Order Block' if s.zone_kind == 'OB' else 'Fair Value Gap'} frais + rejet"]
        miss = []
        for k in SCORED:
            if s.checks.get(k):
                ok_lines.append(_check_text(k, s, long_))
            else:
                miss.append(_acc(CHECK_LABELS[k].split(" (")[0]))
        body = [f"{s.score}/6 confirmations + 4 conditions obligatoires"]
        body += [f"• {t}" for t in ok_lines]
        if miss:
            body.append(f"Non remplies : {' · '.join(miss)}")
        return "\n".join(cap), "\n".join(body)

    def signal_caption(self, s: Signal, digits: int) -> str:
        cap, body = self.signal_parts(s, digits)
        return cap + "\n\n" + body

    def post_signal(self, s: Signal, img_path: str, digits: int) -> Optional[int]:
        cap, _ = self.signal_parts(s, digits)
        return self.send_photo(img_path, cap, button=True)

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
        return f"⌛ <b>Expiré</b> · {name}\nRésultat : {total_r:+.2f}R"

    def post_update(self, s: Signal, ev: dict, total_r: float, reply_to: Optional[int],
                    img_path: Optional[str] = None) -> Optional[int]:
        text = self.update_text(s, ev, total_r)
        if img_path and os.path.exists(img_path):
            return self.send_photo(img_path, text, reply_to=reply_to)
        return self.send_text(text, reply_to=reply_to)

    # ------------------------------------------------------------ bilans / contenu
    def post_recap_image(self, img_path: str, caption: str) -> Optional[int]:
        return self.send_photo(img_path, caption, button=True)

    def post_promo(self) -> Optional[int]:
        link = str(self.cfg.get("private_link", "")).strip()
        perks = "\n".join(f"• {html.escape(str(p))}" for p in self.cfg.get("private_perks", []))
        return self.send_text(f"🔒 <b>Canal privé {html.escape(self.cfg['brand'])}</b>\n{perks}", button=bool(link))

    def post_education(self, day_index: int) -> Optional[int]:
        return self.send_text(EDU_TIPS[day_index % len(EDU_TIPS)], button=True)

    def post_pinned(self) -> Optional[int]:
        """Message d'usage + avertissement complet (a epingler UNE fois). En direct : tente de l'epingler."""
        mid = self.send_text(pinned_text(self.cfg["brand"]))
        if mid and self.live:
            self._api("pinChatMessage", {"chat_id": self.target, "message_id": mid, "disable_notification": "true"})
        return mid


def _tf(tf: str) -> str:
    return {"15m": "M15", "60m": "H1", "240m": "H4"}.get(tf, tf)


def _dist_line(s: Signal) -> str:
    """Distances depuis l'entree (pips pour le forex, unites de prix sinon) : valables sur n'importe quel courtier."""
    m = symbol_meta(s.display) or {}
    cls = UNIVERSE.get(s.display, {}).get("cls", "")
    if cls == "fx" and m.get("point"):
        pip = m["point"] * 10
        f = lambda x: f"{abs(x - s.entry) / pip:.1f} pips"
    else:
        f = lambda x: f"{abs(x - s.entry):,.2f}".replace(",", " ")
    return (f"↔️ Distances : stop {f(s.sl)} · TP1 {f(s.tps[0])} · TP2 {f(s.tps[1])} · TP3 {f(s.tps[2])}")


def _check_text(k: str, s: Signal, long_: bool) -> str:
    if k == "momentum":
        return f"RSI {s.rsi:.0f} en retournement"
    if k == "discount":
        return "Zone Discount" if long_ else "Zone Premium"
    if k == "volume":
        return "Volume Profile : zone au POC / nœud de volume"
    return _acc(CHECK_LABELS[k])


def _acc(t: str) -> str:
    """Les libelles du moteur sont en ASCII ; on remet les accents pour l'affichage public."""
    for a, b in (("liquidite", "liquidité"), ("empilee", "empilée"), ("noeud", "nœud"),
                 ("alignee", "alignée"), ("confirmee", "confirmée"), ("saines", "saines")):
        t = t.replace(a, b)
    return t
