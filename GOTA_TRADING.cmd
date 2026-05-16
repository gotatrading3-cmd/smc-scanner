@echo off
REM ============================================================
REM  GOTA TRADING - Lanceur de l'application
REM  Ouvre le tableau de bord en fenetre "app" via Edge.
REM  Fiable : si le dashboard ne tourne pas, il le demarre.
REM ============================================================
set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"

REM --- 1. Le dashboard repond-il deja sur le port 8080 ? ---
curl -s -o NUL --max-time 8 http://localhost:8080/
if errorlevel 1 (
    REM Dashboard non actif -> on le lance
    start "" /min cmd /c "%DIR%\run_dashboard.cmd"
    REM Laisse le temps au serveur de demarrer
    timeout /t 9 /nobreak >NUL
)

REM --- 2. Ouvre le tableau de bord en fenetre application ---
REM Mode --app : fenetre propre, sans onglets ni barre d'adresse.
start msedge --app=http://localhost:8080 --window-size=1340,880

REM Si Edge absent, fallback navigateur par defaut
if errorlevel 1 start http://localhost:8080
