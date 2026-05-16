@echo off
REM ============================================================
REM  GOTA TRADING - Lanceur application
REM  Ouvre le tableau de bord en VRAIE fenetre app (Edge dedie).
REM ============================================================
set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"
set "APPDATA_EDGE=%LOCALAPPDATA%\GotaTradingApp"

REM --- 1. Verifie si le dashboard repond sur le port 8080 ---
curl -s -o NUL --max-time 6 http://localhost:8080/
if errorlevel 1 (
    REM Dashboard non actif -> on le lance
    start "" /min cmd /c "%DIR%\run_dashboard.cmd"
    REM Attente ~10s sans 'timeout' (ping marche partout)
    ping -n 11 127.0.0.1 >NUL
)

REM --- 2. Ouvre le tableau de bord en fenetre application ---
REM --user-data-dir : profil Edge dedie => TOUJOURS une fenetre app propre,
REM jamais un onglet melange avec ta navigation Edge habituelle.
start "" msedge --app=http://localhost:8080 --user-data-dir="%APPDATA_EDGE%" --window-size=1340,880 --window-position=120,60

REM Fallback si Edge absent
if errorlevel 1 start http://localhost:8080
