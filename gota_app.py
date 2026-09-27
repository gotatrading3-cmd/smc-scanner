"""
GOTA TRADING - Lanceur d'application native.
Ouvre le tableau de bord des signaux dans une fenetre native via pywebview (WebView2).
De l'exterieur c'est une vraie appli Windows : pas de barre d'adresse, pas d'onglets,
juste une fenetre titree "GOTA TRADING".

Robuste par construction : si la fenetre native echoue pour une raison quelconque
(WebView2 absent, pywebview non installe, etc.), on ouvre quand meme le tableau de
bord dans le navigateur - jamais un simple echec silencieux (pythonw n'a pas de
console : une exception non rattrapee ne s'affiche nulle part et ressemble, vu de
l'exterieur, a "l'icone ne fait rien").
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

# Path hardening - user site-packages
for _c in [
    r"C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages",
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

DIR = Path(__file__).parent
LOG = DIR / "gota_app.log"
BASE_URL = "http://localhost:8090"  # tableau de bord des signaux (robot cloud) : vue principale de l'appli


def log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def wait_for_dashboard(timeout: int = 60) -> bool:
    """Attend que le dashboard reponde. 200 ou 401 = serveur OK."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE_URL, timeout=2)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):  # serveur up, demande auth (mais on est local)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def open_fallback(url: str) -> None:
    """Ouvre le tableau de bord d'une facon qui marche presque toujours : Edge en mode appli,
    sinon le navigateur par defaut. Utilise en dernier recours si la fenetre native echoue."""
    try:
        appdata_edge = Path(os.environ.get("LOCALAPPDATA", "")) / "GotaTradingApp"
        subprocess.Popen(["msedge", f"--app={url}", f"--user-data-dir={appdata_edge}",
                          "--window-size=1340,880", "--window-position=120,60"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        log("fallback : Edge --app lance")
    except Exception as e:
        log(f"fallback Edge --app echoue ({e}) - navigateur par defaut")
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except Exception as e2:
            log(f"fallback navigateur par defaut echoue aussi : {e2}")


def main() -> None:
    log("=== lancement ===")
    url = f"{BASE_URL}/?v={int(time.time())}"  # ecarte tout cache eventuel : chargement toujours frais

    if not wait_for_dashboard(60):
        log("le dashboard ne repond pas sur :8090 apres 60s - ouverture quand meme (il finira par repondre)")

    try:
        import webview
    except ImportError:
        log("pywebview non installe - ouverture dans le navigateur")
        open_fallback(url)
        return

    try:
        storage = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "GotaTrading" / "webview_data"
        storage.mkdir(parents=True, exist_ok=True)
        webview.create_window(
            title="GOTA TRADING",
            url=url,
            width=1400,
            height=900,
            min_size=(900, 600),
            resizable=True,
        )
        webview.start(storage_path=str(storage), private_mode=False)
        log("fenetre native fermee normalement")
    except Exception:
        log("fenetre native en echec :\n" + traceback.format_exc())
        open_fallback(url)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("erreur fatale imprevue :\n" + traceback.format_exc())
        try:
            open_fallback(f"{BASE_URL}/?v={int(time.time())}")
        except Exception:
            pass
