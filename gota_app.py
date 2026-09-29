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

# Renforcement du chemin des paquets (pip --user) : voir signal_dashboard.py pour le detail du probleme observe.
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

DIR = Path(__file__).parent
LOG = DIR / "gota_app.log"
BASE_URL = "http://localhost:8090"  # tableau de bord des signaux (robot cloud) : vue principale de l'appli


def log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# Verrou mono-instance : cliquer plusieurs fois sur l'icone avant que la fenetre apparaisse (reaction
# humaine normale si rien ne s'affiche tout de suite) lancait plusieurs gota_app.py en parallele, qui se
# coupaient la route les uns les autres (vu en pratique : "pywebview non installe" par intermittence).
# Avec ce verrou, un clic en trop pendant qu'une instance demarre deja ne fait rien - la premiere finit
# normalement, sans concurrence.
_LOCK_FILE = DIR / "gota_app.lock"


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                              capture_output=True, text=True, timeout=5)
        return str(pid) in out.stdout
    except Exception:
        return True


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
                return False
            try:
                _LOCK_FILE.unlink()
            except Exception:
                pass
    return False


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


EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def open_fallback(url: str) -> None:
    """Ouvre le tableau de bord d'une facon qui marche presque toujours. D'abord Edge en mode appli (sans barre
    d'adresse) SI on trouve son chemin reel sur le disque (un simple "msedge" ne marche pas toujours ici : Windows
    ne cherche pas les .exe enregistres dans le PATH de la meme facon que dans une invite de commandes). Sinon,
    le navigateur par defaut (os.startfile), qui marche presque toujours."""
    edge = next((p for p in EDGE_PATHS if os.path.isfile(p)), None)
    if edge:
        try:
            appdata_edge = Path(os.environ.get("LOCALAPPDATA", "")) / "GotaTradingApp"
            subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={appdata_edge}",
                              "--window-size=1340,880", "--window-position=120,60"],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            log("fallback : Edge --app lance (" + edge + ")")
            return
        except Exception as e:
            log(f"fallback Edge --app echoue ({e}) - navigateur par defaut")
    else:
        log("Edge introuvable aux emplacements habituels - navigateur par defaut")
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        log("fallback : navigateur par defaut lance")
    except Exception as e2:
        log(f"fallback navigateur par defaut echoue aussi : {e2}")


def main() -> None:
    log("=== lancement ===")
    if not _acquire_single_instance_lock():
        log("une instance demarre deja - on ne fait rien (evite la concurrence)")
        return
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
