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
  --add-data "settings.json:." \
  --add-data "run-history:run-history" \
  "gui.py"

echo "Create DMG (requires macOS + hdiutil)..."
mkdir -p dist/dmg
cp -R "dist/AutoWebsiteChecker.app" "dist/dmg/"
hdiutil create -volname "AutoWebsiteChecker" -srcfolder "dist/dmg" -ov -format UDZO "dist/AutoWebsiteChecker.dmg"

echo "Done: dist/AutoWebsiteChecker.dmg"
