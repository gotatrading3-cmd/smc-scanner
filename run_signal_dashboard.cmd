@echo off
REM Lance le tableau de bord des signaux et le RELANCE tout seul s'il s'arrete (panne reseau, etc.).
REM N'utilise QUE le Python installe pour cet utilisateur (jamais l'alias "python" du Windows Store).
REM Renforce contre l'erreur deja vue "module requests introuvable" (PYTHONPATH explicite en plus du
REM renforcement fait directement dans signal_dashboard.py).
REM Protection anti-boucle : si ca plante 5 fois de suite tout de suite, attend 5 minutes avant de
REM reessayer au lieu de marteler toutes les 3 secondes pour rien.
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set "PYTHONPATH=%APPDATA%\Python\Python312\site-packages"
set "PYTHONNOUSERSITE="
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\signal_dashboard.log"
set /a FAILS=0

cd /d "%DIR%"
if not exist "%PYTHON%" (
    echo === %DATE% %TIME% : Python introuvable a "%PYTHON%" === >> "%LOG%"
    exit /b 1
)

:loop
echo === START %DATE% %TIME% === >> "%LOG%"
"%PYTHON%" -u "%DIR%\signal_dashboard.py" >> "%LOG%" 2>&1
if errorlevel 1 (set /a FAILS+=1) else (set /a FAILS=0)
if %FAILS% GEQ 5 (
    echo === ARRET %DATE% %TIME% : 5 echecs de suite - pause 5 min === >> "%LOG%"
    set /a FAILS=0
    timeout /t 300 /nobreak >NUL
    goto loop
)
echo === ARRET %DATE% %TIME% - relance dans 3 s === >> "%LOG%"
timeout /t 3 /nobreak >NUL
goto loop
