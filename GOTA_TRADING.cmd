@echo off
REM Lance l'application GOTA TRADING (fenetre native)
set "DIR=C:\Users\GOTA TRADING\.claude\trading-analysis"
set "PYTHON=C:\Users\GOTA TRADING\AppData\Local\Programs\Python\Python312\pythonw.exe"
set "PYTHONPATH=C:\Users\GOTA TRADING\AppData\Roaming\Python\Python312\site-packages"
set "PYTHONIOENCODING=utf-8"
cd /d "%DIR%"
start "" "%PYTHON%" "%DIR%\gota_trading_app.py"
