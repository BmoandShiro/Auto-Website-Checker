@echo off
setlocal
cd /d "%~dp0"

echo Installing build dependencies...
py -3 -m pip install -r requirements.txt pyinstaller
set PLAYWRIGHT_BROWSERS_PATH=0
echo Installing Playwright Chromium into local package...
py -3 -m playwright install chromium

echo Building one-file portable EXE...
py -3 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "WebsiteAuditer" ^
  --icon "assets\app-icon.png" ^
  --collect-data "spellchecker" ^
  --add-data "settings.json;." ^
  --add-data "run-history;run-history" ^
  --add-data "assets\\app-icon.png;assets" ^
  "gui.py"

if exist "dist\WebsiteAuditer.exe" (
  echo.
  echo Build complete:
  echo   %cd%\dist\WebsiteAuditer.exe
) else (
  echo Build failed.
  exit /b 1
)

endlocal
