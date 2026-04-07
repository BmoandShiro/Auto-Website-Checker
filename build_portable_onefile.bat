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
  --name "AutoWebsiteChecker" ^
  --add-data "settings.json;." ^
  --add-data "run-history;run-history" ^
  "gui.py"

if exist "dist\AutoWebsiteChecker.exe" (
  echo.
  echo Build complete:
  echo   %cd%\dist\AutoWebsiteChecker.exe
) else (
  echo Build failed.
  exit /b 1
)

endlocal
