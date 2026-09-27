@echo off
REM Lance le tableau de bord des signaux et le RELANCE tout seul s'il s'arrete (panne reseau, etc.) - jamais
REM besoin d'intervenir a la main. N'utilise QUE le Python installe pour cet utilisateur (jamais l'alias
REM "python" du Windows Store, qui a deja cause un "module requests introuvable" une fois).
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\signal_dashboard.log"

cd /d "%DIR%"
if not exist "%PYTHON%" (
    echo === %DATE% %TIME% : Python introuvable a "%PYTHON%" === >> "%LOG%"
    exit /b 1
)

:loop
echo === START %DATE% %TIME% === >> "%LOG%"
"%PYTHON%" -u "%DIR%\signal_dashboard.py" >> "%LOG%" 2>&1
echo === ARRET %DATE% %TIME% - relance dans 3 s === >> "%LOG%"
timeout /t 3 /nobreak >NUL
goto loop
