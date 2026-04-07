@echo off
setlocal
cd /d "%~dp0"

echo Starting Auto Website Checker GUI...
py -3 gui.py

endlocal
