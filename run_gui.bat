@echo off
setlocal
cd /d "%~dp0"

echo Starting Website Auditer GUI...
py -3 gui.py

endlocal
