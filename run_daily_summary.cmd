@echo off
REM Lance par tache planifiee Windows tous les jours a 22:00 UTC.
REM Avec --weekly le dimanche.

set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"
set "PYTHON=C:\Users\GOTA TRADING\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHONPATH=C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=%DIR%\daily_summary.log"

cd /d "%DIR%"
echo === START %DATE% %TIME% === >> "%LOG%"
"%PYTHON%" -u "%DIR%\daily_summary.py" %1 >> "%LOG%" 2>&1
