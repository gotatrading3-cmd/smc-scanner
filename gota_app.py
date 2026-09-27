"""
GOTA TRADING - Lanceur d'application native.
Ouvre le dashboard local dans une fenetre native via pywebview (engine WebView2).
De l'exterieur c'est une vraie appli Windows : pas de barre d'adresse, pas d'onglets,
juste une fenetre titree "GOTA TRADING".
"""
from __future__ import annotations
import sys
import os
import time
import urllib.request
import urllib.error
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

URL = "http://localhost:8090"  # tableau de bord des signaux (robot cloud) : vue principale de l'appli


def wait_for_dashboard(timeout: int = 60) -> bool:
    """Attend que le dashboard reponde. 200 ou 401 = serveur OK."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=2)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):  # serveur up, demande auth (mais on est local)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    print("GOTA TRADING - attente du dashboard...")
    if not wait_for_dashboard(60):
        print("Le dashboard ne repond pas sur localhost:8090 apres 60s.")
        print("Le watchdog devrait le relancer dans 3 min. Reessaye apres.")
        sys.exit(1)

    try:
        import webview
    except ImportError:
        print("pywebview pas installe.")
        print('Installe avec : python -m pip install --user pywebview')
        sys.exit(2)

    # Stockage persistant pour cookies/cache de la fenetre
    storage = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "GotaTrading" / "webview_data"
    storage.mkdir(parents=True, exist_ok=True)

    webview.create_window(
        title="GOTA TRADING",
        url=URL,
        width=1400,
        height=900,
        min_size=(900, 600),
        resizable=True,
    )
    # webview.start() est bloquant : il rend la main quand l'utilisateur ferme la fenetre.
    webview.start(storage_path=str(storage), private_mode=False)


if __name__ == "__main__":
    main()
