@echo off
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
set "PYTHONPATH=%APPDATA%\Python\Python312\site-packages"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\signal_dashboard.log"

cd /d "%DIR%"
echo === START %DATE% %TIME% === >> "%LOG%"
"%PYTHON%" -u "%DIR%\signal_dashboard.py" >> "%LOG%" 2>&1
