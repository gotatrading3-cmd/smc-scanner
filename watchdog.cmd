@echo off
REM Wrapper du watchdog - lance par la tache planifiee toutes les 3 min.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\GOTA TRADING\.claude\trading-analysis\watchdog.ps1"
