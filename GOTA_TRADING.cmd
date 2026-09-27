@echo off
REM ============================================================
REM  GOTA TRADING - Lanceur application
REM  1. Ferme toute ancienne fenetre GOTA encore ouverte (jamais de vieux contenu affiche).
REM  2. S'assure que les deux tableaux de bord tournent (signaux + comptes MT5 locaux).
REM  3. Lance une fenetre NATIVE (pywebview/WebView2) sur les signaux - pas de navigateur visible.
REM  4. Fallback Edge --app si pywebview indisponible (gere aussi par gota_app.py lui-meme).
REM ============================================================
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw"

REM --- 0. Ferme toute instance encore ouverte (fenetre native ou Edge --app) : evite d'afficher du vieux contenu ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'gota_app\.py' -or $_.CommandLine -match 'GotaTradingApp' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >NUL 2>&1

REM --- 1. Verifie si le dashboard des signaux (cloud) repond ---
curl -s -o NUL --max-time 6 http://localhost:8090/
if errorlevel 1 (
    start "" /min cmd /c "%DIR%\run_signal_dashboard.cmd"
    ping -n 11 127.0.0.1 >NUL
)

REM --- 1bis. Verifie si le dashboard MT5 (comptes locaux) repond (accessible depuis un lien de l'autre) ---
curl -s -o NUL --max-time 6 http://localhost:8080/
if errorlevel 1 (
    start "" /min cmd /c "%DIR%\run_dashboard.cmd"
)

REM --- 2. Lance la fenetre (native si possible ; gota_app.py bascule lui-meme sur Edge sinon) ---
start "" "%PYTHONW%" "%DIR%\gota_app.py"
