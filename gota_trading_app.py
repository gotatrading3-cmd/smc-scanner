"""
GOTA TRADING - Application desktop native.

Wrapper qui :
1. Verifie que les 3 services (executor + bot + dashboard) tournent
2. Les lance si besoin (via les run_*.cmd)
3. Ouvre une fenetre native pywebview sur le dashboard
4. Quand tu fermes la fenetre, les services tournent toujours en arriere-plan

Lance via le raccourci "GOTA TRADING" sur le bureau.
"""
from __future__ import annotations
import sys
import os
import time
import socket
import subprocess
from pathlib import Path

# user site-packages
for _c in [
    os.path.expandvars("%APPDATA%\\Python\\Python312\\site-packages"),
    os.path.expanduser("~/AppData/Roaming/Python/Python312/site-packages"),
]:
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
        break

import webview  # noqa: E402

DIR = Path(__file__).parent
DASHBOARD_URL = "http://localhost:8080"
WRAPPERS = {
    "mt5_executor.py": DIR / "run_mt5_executor.cmd",
    "telegram_bot.py": DIR / "run_telegram_bot.cmd",
    "dashboard.py": DIR / "run_dashboard.cmd",
}


def port_open(host: str = "127.0.0.1", port: int = 8080, timeout: float = 1.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def is_running(script_name: str) -> bool:
    """Verifie si un script python tourne deja."""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
            text=True, errors="replace", stderr=subprocess.DEVNULL,
        )
        return script_name in out
    except Exception:
        return False


def ensure_services_running():
    """Lance les services manquants en arriere-plan."""
    started = []
    for script, wrapper in WRAPPERS.items():
        if not is_running(script):
            print(f"  Lance {script} ...")
            subprocess.Popen(
                ["cmd.exe", "/c", str(wrapper)],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            started.append(script)
    if started:
        time.sleep(5)  # attente que dashboard ecoute
    return started


def main():
    print("GOTA TRADING - demarrage de l'application")
    started = ensure_services_running()
    if started:
        print(f"Services lances : {started}")

    # attend que le dashboard reponde (max 30s)
    for i in range(30):
        if port_open():
            break
        print(f"  Attente dashboard ({i+1}/30)...")
        time.sleep(1)
    else:
        print("ERREUR : dashboard n'a pas demarre. Verifie les logs.")
        return

    # Lance la fenetre native
    print(f"Ouverture fenetre GOTA TRADING -> {DASHBOARD_URL}")
    webview.create_window(
        "GOTA TRADING",
        DASHBOARD_URL,
        width=1280,
        height=820,
        resizable=True,
        background_color="#0a0e27",
    )
    webview.start()
    print("Fenetre fermee. Les services continuent en arriere-plan.")


if __name__ == "__main__":
    main()
