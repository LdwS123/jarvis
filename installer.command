#!/bin/zsh
# Double-clique ce fichier : Jervis s'installe dans la barre des menus et démarre tout seul à chaque session.
#   ./installer.command            → installe (ou réinstalle après une mise à jour)
#   ./installer.command --retirer  → ne le lance plus au démarrage
set -euo pipefail
cd "$(dirname "$0")"
DOSSIER="$PWD"
LABEL="local.jervis.assistant"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAINE="gui/$(id -u)"

launchctl bootout "$DOMAINE/$LABEL" 2>/dev/null || true
pkill -f "jervis --barre" 2>/dev/null || true

if [[ "${1:-}" == "--retirer" ]]; then
  rm -f "$AGENT"
  echo "Jervis ne démarrera plus tout seul. (Jervis.app reste dans $DOSSIER.)"
  exit 0
fi

[[ -x .venv/bin/python ]] || { echo "❌ Pas de .venv ici : installe d'abord Jervis (voir README)."; exit 1; }
# Il pense avec TON abonnement Claude (Pro ou Max), par Claude Code : sans lui, il entend mais ne répond pas.
# Premier lancement (pas encore de reglages.json) ou compte pas connecté : la page d'installation s'ouvre dans ton
# navigateur (se connecter à Claude, son nom, tes langues, sa voix), et l'installation attend « Terminer et lancer ».
#   ./installer.command --sans-page  → saute la page (tout est réglé par variables JERVIS_…)
CONNECTE="$(.venv/bin/python -c 'from jervis import reglages; s = reglages.statut_claude(); print("oui" if s.get("connecte") or s.get("illisible") else "non")' 2>/dev/null || echo oui)"
if [[ "${1:-}" != "--sans-page" && ( "$CONNECTE" != "oui" || ! -f reglages.json ) ]]; then
  echo "🔑 La page d'installation s'ouvre dans ton navigateur : connecte ton compte Claude, choisis ses réglages,"
  echo "   puis clique « Terminer et lancer ». (ctrl-C pour abandonner.)"
  if ! .venv/bin/python -m jervis.reglages --installation; then
    echo "❌ Installation pas terminée : relance ./installer.command quand tu veux."
    exit 1
  fi
fi

zsh outils/construire_app.sh

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/open</string><string>-a</string><string>$DOSSIER/Jervis.app</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
PLIST

launchctl bootstrap "$DOMAINE" "$AGENT"
echo ""
echo "✅ Jervis est installé : regarde l'icône ⏳ puis 🎙 en haut à droite de l'écran."
echo "   macOS va te demander l'accès au micro : clique « Autoriser »."
echo "   Il redémarrera tout seul à chaque ouverture de session."
echo "   ⚠️  Ne lance pas lancer.command en même temps (deux Jervis se disputeraient le micro)."
echo "   Pour l'enlever du démarrage : ./installer.command --retirer"
echo "   Son nom, tes langues (中文, English, ქართული…), sa voix, ton compte Claude : menu de l'icône → Réglages…"
echo "   (ou double-clique reglages.command)."
