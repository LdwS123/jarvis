#!/bin/zsh
# Fabrique Jervis.app dans le dossier du projet (appelé par installer.command).
set -euo pipefail
cd "$(dirname "$0")/.."

APP="Jervis.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

/usr/bin/clang -O2 -o "$APP/Contents/MacOS/Jervis" outils/lanceur.c

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Jervis</string>
  <key>CFBundleDisplayName</key><string>Jervis</string>
  <key>CFBundleIdentifier</key><string>local.jervis.assistant</string>
  <key>CFBundleExecutable</key><string>Jervis</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Jervis écoute quand tu dis son nom.</string>
  <key>NSAppleEventsUsageDescription</key><string>Jervis pilote tes apps (Messages, Notes, Rappels, Musique…) quand tu le lui demandes.</string>
  <key>NSRemindersUsageDescription</key><string>Jervis crée et lit tes rappels.</string>
  <key>NSContactsUsageDescription</key><string>Jervis cherche le numéro d'un contact pour lui envoyer un message.</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "   (signature ad hoc impossible, pas grave)"
echo "✅ $APP fabriquée."
