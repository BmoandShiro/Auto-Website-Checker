#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Install dependencies..."
python3 -m pip install -r requirements.txt pyinstaller

echo "Build macOS app bundle..."
python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "AutoWebsiteChecker" \
  --osx-bundle-identifier com.autowebsitechecker.app \
  --collect-data "spellchecker" \
  --add-data "settings.json:." \
  --add-data "run-history:run-history" \
  --add-data "assets/app-icon.png:assets" \
  --codesign-identity "" \
  "gui.py"

echo "Install bundled Chromium into app Resources..."
export PLAYWRIGHT_BROWSERS_PATH="$(pwd)/dist/AutoWebsiteChecker.app/Contents/Resources/ms-playwright"
python3 -m playwright install chromium

# Playwright adds files after PyInstaller sealed the bundle; resign so Gatekeeper does not report "damaged".
codesign --force --deep --sign - "dist/AutoWebsiteChecker.app"

echo "Create DMG (requires macOS + hdiutil)..."
mkdir -p dist/dmg
cp -R "dist/AutoWebsiteChecker.app" "dist/dmg/"
hdiutil create -volname "AutoWebsiteChecker" -srcfolder "dist/dmg" -ov -format UDZO "dist/AutoWebsiteChecker.dmg"

echo "Done: dist/AutoWebsiteChecker.dmg"
