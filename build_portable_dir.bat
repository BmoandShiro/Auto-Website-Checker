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
  --name "AutoWebsiteChecker" ^
  --add-data "settings.json;." ^
  --add-data "run-history;run-history" ^
  --add-data "assets\\app-icon.png;assets" ^
  "gui.py"

echo Installing bundled Chromium into dist folder...
set PLAYWRIGHT_BROWSERS_PATH=%cd%\dist\AutoWebsiteChecker\ms-playwright
py -3 -m playwright install chromium

if exist "dist\AutoWebsiteChecker\AutoWebsiteChecker.exe" (
  echo.
  echo Build complete:
  echo   %cd%\dist\AutoWebsiteChecker\
) else (
  echo Build failed.
  exit /b 1
)

endlocal
