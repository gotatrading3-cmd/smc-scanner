@echo off
REM Lance le Grid Bot DEMO. NE PAS lancer en meme temps que run_mt5_executor.cmd.
set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"
set "PYTHON=C:\Users\GOTA TRADING\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHONPATH=C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\grid.log"

cd /d "%DIR%"
echo === START %DATE% %TIME% === >> "%LOG%"
"%PYTHON%" -u "%DIR%\grid_executor.py" >> "%LOG%" 2>&1
echo === END %DATE% %TIME% === >> "%LOG%"
