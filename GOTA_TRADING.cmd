@echo off
REM ============================================================
REM  GOTA TRADING - Lanceur application
REM  1. S'assure que le dashboard tourne.
REM  2. Lance une fenetre NATIVE (pywebview/WebView2) - pas de navigateur visible.
REM  3. Fallback Edge --app si pywebview indisponible.
REM ============================================================
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw"

REM --- 1. Verifie si le dashboard MT5 (comptes locaux) repond ---
curl -s -o NUL --max-time 6 http://localhost:8080/
if errorlevel 1 (
    start "" /min cmd /c "%DIR%\run_dashboard.cmd"
    ping -n 11 127.0.0.1 >NUL
)

REM --- 1bis. Verifie si le tableau de bord des signaux (cloud) repond ---
curl -s -o NUL --max-time 6 http://localhost:8090/
if errorlevel 1 (
    start "" /min cmd /c "%DIR%\run_signal_dashboard.cmd"
)

REM --- 2. Si pywebview installe, lance la fenetre native ---
"%PYTHONW%" -c "import webview" >NUL 2>&1
if not errorlevel 1 (
    start "" "%PYTHONW%" "%DIR%\gota_app.py"
    exit /b 0
)

REM --- 3. Fallback : Edge --app (pas de navigateur chrome non plus, mais visiblement Edge) ---
set "APPDATA_EDGE=%LOCALAPPDATA%\GotaTradingApp"
start "" msedge --app=http://localhost:8090 --user-data-dir="%APPDATA_EDGE%" --window-size=1340,880 --window-position=120,60
if errorlevel 1 start http://localhost:8090
