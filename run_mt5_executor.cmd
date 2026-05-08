@echo off
REM Wrapper de l'executor MT5 - lance au demarrage Windows.
REM Lance MT5 desktop si pas deja ouvert, puis l'executor avec retry.

set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"
set "PYTHON=C:\Users\GOTA TRADING\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHONPATH=C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\mt5_executor.log"
set "MT5_EXE=C:\Program Files\XM Global MT5\terminal64.exe"

cd /d "%DIR%"

echo === START %DATE% %TIME% === >> "%LOG%"

REM Lance MT5 desktop si pas deja en cours
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I "terminal64.exe" >NUL
if errorlevel 1 (
    echo Lancement de MT5 desktop... >> "%LOG%"
    start "" "%MT5_EXE%"
    REM Attente que MT5 charge avant de connecter
    timeout /t 30 /nobreak >NUL
)

"%PYTHON%" -u "%DIR%\mt5_executor.py" >> "%LOG%" 2>&1

echo === END %DATE% %TIME% (exit %ERRORLEVEL%) === >> "%LOG%"
