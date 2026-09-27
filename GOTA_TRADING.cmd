@echo off
REM ============================================================
REM  GOTA TRADING - Lanceur application
REM  Ne s'occupe QUE du tableau de bord des signaux (aucun lien avec MT5/les comptes locaux,
REM  qui sont un systeme totalement separe et inutilise - source de confusion, retire).
REM  1. Ferme toute ancienne fenetre GOTA encore ouverte (jamais de vieux contenu affiche).
REM  2. S'assure que le tableau de bord des signaux tourne.
REM  3. Lance une fenetre NATIVE (pywebview/WebView2) - pas de navigateur visible.
REM ============================================================
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"

REM --- 0. Ferme toute instance encore ouverte (fenetre native ou Edge --app) : evite d'afficher du vieux contenu ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'gota_app\.py' -or $_.CommandLine -match 'GotaTradingApp' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >NUL 2>&1

REM --- 1. Verifie si le tableau de bord des signaux repond ; le lance sinon (il se relance tout seul s'il plante) ---
curl -s -o NUL --max-time 6 http://localhost:8090/
if errorlevel 1 (
    start "" /min cmd /c "%DIR%\run_signal_dashboard.cmd"
    ping -n 11 127.0.0.1 >NUL
)

REM --- 2. Lance la fenetre (native si possible ; gota_app.py bascule lui-meme sur Edge sinon) ---
start "" "%PYTHONW%" "%DIR%\gota_app.py"
