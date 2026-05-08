"""
Notifications Telegram pour le scanner.

Configuration via variables d'environnement (recommande) :
    TELEGRAM_BOT_TOKEN  - jeton fourni par @BotFather
    TELEGRAM_CHAT_ID    - ton chat ID (voir scanner --setup-telegram)

Configuration alternative via telegram.json (meme dossier) :
    {"token": "...", "chat_id": "..."}

Stdlib uniquement (urllib) - pas de dependance externe.
"""
from __future__ import annotations
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional


CONFIG_FILE = Path(__file__).parent / "telegram.json"


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None,
                 silent: bool = False):
        # priorite : args > env vars > config file
        if not token:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not chat_id:
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if (not token or not chat_id) and CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
                token = token or cfg.get("token", "")
                chat_id = chat_id or cfg.get("chat_id", "")
            except Exception:
                pass

        self.token = str(token)
        self.chat_id = str(chat_id)
        self.silent = silent
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled:
            if not self.silent:
                print("  [Telegram] non configure (set TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID)")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }).encode()
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read())
                return bool(payload.get("ok"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"  [Telegram] HTTP {e.code} : {body[:200]}")
            return False
        except Exception as e:
            print(f"  [Telegram] erreur : {e}")
            return False

    def send_setup(self, alert) -> bool:
        """alert: SetupAlert du scanner."""
        ts = alert.timestamp.strftime("%Y-%m-%d %H:%M UTC")
        sl_pct = (alert.sl - alert.entry) / alert.entry * 100
        tp_pct = (alert.tp - alert.entry) / alert.entry * 100

        text = (
            f"*{alert.direction} setup* - `{alert.symbol}`\n"
            f"\n"
            f"Entry : `{alert.entry:,.4f}`\n"
            f"SL    : `{alert.sl:,.4f}`  ({sl_pct:+.2f}%)\n"
            f"TP    : `{alert.tp:,.4f}`  ({tp_pct:+.2f}%)\n"
            f"R:R   : `1:{alert.rr:.2f}`\n"
            f"\n"
            f"_{alert.reason}_\n"
            f"RSI : `{alert.rsi:.1f}`   EMA200 dist : `{alert.distance_to_ema200_pct:+.2f}%`\n"
            f"\n"
            f"`{ts}`"
        )
        return self.send(text)


def get_chat_id_helper(token: str) -> str:
    """
    Utilitaire : recupere le dernier chat_id en interrogeant l'API getUpdates.
    L'utilisateur doit avoir envoye au moins un message (/start) au bot avant.
    """
    if not token:
        return ""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        if not data.get("ok") or not data.get("result"):
            return ""
        # prend le dernier update qui a un message
        for upd in reversed(data["result"]):
            msg = upd.get("message") or upd.get("channel_post")
            if msg and msg.get("chat"):
                return str(msg["chat"]["id"])
    except Exception as e:
        print(f"  [Telegram] erreur getUpdates : {e}")
    return ""


def setup_wizard():
    """
    Petit assistant interactif pour configurer Telegram.
    A lancer une fois apres avoir cree le bot via @BotFather.
    """
    print("\n=== Configuration Telegram ===\n")
    print("Etape 1 : sur Telegram, parle a @BotFather, envoie /newbot, suis les instructions.")
    print("          Tu obtiens un TOKEN du genre 1234567890:ABC...\n")
    token = input("Colle le TOKEN ici : ").strip()
    if not token:
        print("  abandon")
        return

    print("\nEtape 2 : ouvre une discussion avec ton bot et envoie-lui /start.")
    input("          Appuie sur ENTREE quand c'est fait ... ")

    print("\nEtape 3 : recuperation automatique du chat_id ...")
    chat_id = get_chat_id_helper(token)
    if not chat_id:
        print("  Pas de chat_id trouve. Verifie que tu as bien envoye /start au bot.")
        chat_id = input("  Ou colle-le manuellement : ").strip()
        if not chat_id:
            return

    print(f"\n  Token   : {token[:10]}...")
    print(f"  Chat ID : {chat_id}")

    cfg = {"token": token, "chat_id": chat_id}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    print(f"\n  Sauvegarde dans : {CONFIG_FILE}")

    # test
    notifier = TelegramNotifier(token, chat_id)
    if notifier.send("*Scanner SMC* configure !\nTu recevras les setups confluents ici."):
        print("  Test message envoye - check ton Telegram.\n")
    else:
        print("  Echec du test. Verifie le token et le chat_id.\n")


def setup_from_token(token: str) -> bool:
    """
    Setup non-interactif : prend le TOKEN en argument,
    fetch le chat_id automatiquement, sauvegarde, envoie test.
    Prerequis : avoir envoye /start au bot avant.
    """
    token = token.strip()
    if not token or ":" not in token:
        print("  ERREUR : TOKEN invalide (format attendu : 1234567890:ABC...)")
        return False
    print(f"  Token recu : {token[:12]}...")
    print(f"  Recuperation du chat_id via getUpdates ...")
    chat_id = get_chat_id_helper(token)
    if not chat_id:
        print("\n  ECHEC : aucun chat_id trouve.")
        print("  -> As-tu envoye /start a ton bot dans Telegram ?")
        print("  Si oui, attends 30s et relance la commande.")
        return False
    print(f"  Chat ID : {chat_id}")
    cfg = {"token": token, "chat_id": chat_id}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    print(f"  Config sauvegardee : {CONFIG_FILE}")
    n = TelegramNotifier(token, chat_id)
    if n.send("*Scanner SMC* configure !\nTu recevras les setups confluents ici."):
        print("  Test message envoye - check ton Telegram.")
        return True
    print("  Echec envoi test. Token ou chat_id invalide ?")
    return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        setup_wizard()
    elif len(sys.argv) > 2 and sys.argv[1] == "--token":
        setup_from_token(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        n = TelegramNotifier()
        if n.send("Test depuis notifier.py - tout marche."):
            print("OK envoye")
        else:
            print("Echec - verifie la config")
    else:
        print("Usage :")
        print("  python notifier.py --token TOKEN  (setup en 1 commande)")
        print("  python notifier.py --setup        (assistant interactif)")
        print("  python notifier.py --test         (envoie un message test)")
