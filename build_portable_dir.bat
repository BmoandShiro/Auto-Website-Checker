@echo off
setlocal
cd /d "%~dp0"

echo Installing build dependencies...
py -3 -m pip install -r requirements.txt pyinstaller

echo Building onedir portable app...
py -3 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name "WebsiteAuditer" ^
  --icon "assets\app-icon.png" ^
  --collect-data "spellchecker" ^
  --add-data "settings.json;." ^
  --add-data "run-history;run-history" ^
  --add-data "assets\\app-icon.png;assets" ^
  "gui.py"

echo Installing bundled Chromium into dist folder...
set PLAYWRIGHT_BROWSERS_PATH=%cd%\dist\WebsiteAuditer\ms-playwright
py -3 -m playwright install chromium

if exist "dist\WebsiteAuditer\WebsiteAuditer.exe" (
  echo.
  echo Build complete:
  echo   %cd%\dist\WebsiteAuditer\
) else (
  echo Build failed.
  exit /b 1
)

endlocal
