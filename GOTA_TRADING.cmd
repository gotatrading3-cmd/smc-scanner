@echo off
REM ============================================================
REM  GOTA TRADING - Lanceur application
REM  Ne s'occupe QUE du tableau de bord des signaux (aucun lien avec MT5/les comptes locaux).
REM  Lance depuis le raccourci du bureau via hidden_run.vbs : AUCUNE fenetre noire ne doit jamais
REM  apparaitre. Si tu vois quand meme une invite de commandes, ce n'est pas ce script - dis-le.
REM  1. Ferme toute ancienne fenetre GOTA encore ouverte (jamais de vieux contenu affiche).
REM  2. S'assure que le tableau de bord des signaux tourne (lance en arriere-plan, sans fenetre).
REM  3. Lance une fenetre NATIVE (pywebview/WebView2) - pas de navigateur visible.
REM ============================================================
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
REM Renforcement (voir signal_dashboard.py) : au cas ou Python ne retrouve pas tout seul le dossier
REM utilisateur ou sont installes "requests"/"cryptography"/"webview".
set "PYTHONPATH=%APPDATA%\Python\Python312\site-packages"
set "PYTHONNOUSERSITE="

REM --- 0. Ferme toute VIEILLE instance encore ouverte (fenetre native ou Edge --app) : evite d'afficher
REM      du vieux contenu. Ne touche PAS a une instance lancee il y a moins de 20s : si l'utilisateur
REM      reclique parce que rien ne s'affiche encore, ca ne doit pas interrompre le lancement en cours
REM      (gota_app.py a de toute facon son propre verrou mono-instance depuis 2026-09-29). ---
powershell -NoProfile -WindowStyle Hidden -Command "$cutoff = (Get-Date).AddSeconds(-20); Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -match 'gota_app\.py' -or $_.CommandLine -match 'GotaTradingApp') -and $_.CreationDate -lt $cutoff } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >NUL 2>&1

REM --- 1. Le tableau de bord tourne en boucle (run_signal_dashboard.cmd, qui se relance seul si
REM        besoin) : on n'en lance un 2e QUE si aucune boucle n'existe deja. Sans cette verification,
REM        cliquer sur l'icone au mauvais moment (boucle en train de se relancer, 3s de battement)
REM        peut empiler des boucles fantomes en arriere-plan pour rien. ---
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_signal_dashboard\.cmd' }) { exit 0 } else { exit 1 }" >NUL 2>&1
if errorlevel 1 (
    wscript.exe "%DIR%\hidden_run.vbs" "%DIR%\run_signal_dashboard.cmd"
)

REM --- attend que le tableau de bord reponde (jusqu'a ~40s - large marge pour un PC qui vient de
REM      demarrer, reseau/disque plus lents que d'habitude) au lieu d'une pause fixe a l'aveugle ---
for /l %%i in (1,1,20) do (
    curl -s -o NUL --max-time 2 http://localhost:8090/ && goto :dashboard_ready
    ping -n 2 127.0.0.1 >NUL
)
:dashboard_ready

REM --- 2. Lance la fenetre (native si possible ; gota_app.py bascule lui-meme sur Edge/navigateur sinon) ---
start "" "%PYTHONW%" "%DIR%\gota_app.py"
