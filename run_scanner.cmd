@echo off
set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"
set "PYTHON=C:\Users\GOTA TRADING\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHONPATH=C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\scanner.log"

cd /d "%DIR%"
echo === START %DATE% %TIME% === >> "%LOG%"
"%PYTHON%" -u "%DIR%\scanner.py" >> "%LOG%" 2>&1
echo === END %DATE% %TIME% (exit %ERRORLEVEL%) === >> "%LOG%"
