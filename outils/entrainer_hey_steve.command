#!/bin/zsh
# Entraîne un vrai mot-clé « Hey Steve » (livekit-wakeword, github.com/livekit/livekit-wakeword), en local sur ton Mac.
# Double-clique ce fichier, ou : zsh outils/entrainer_hey_steve.command
#
# Ce qu'il fait : génère des milliers d'exemples de « hey steve » en voix de synthèse (et des phrases qui
# y ressemblent, pour apprendre à NE PAS réagir), entraîne un petit modèle (~1 Mo), puis le range dans
# modeles/voix/hey_steve.onnx. Steve le prend tout seul au prochain démarrage (détection « hybride »).
#
# Il faut : ~4 Go de téléchargement, ~1 h sur une puce M (une fois pour toutes), et un compte LiveKit Cloud
# gratuit (cloud.livekit.io) : leur outil s'en sert pour la synthèse des voix d'entraînement.
set -euo pipefail
cd "$(dirname "$0")/.."
DOSSIER="$PWD"
TRAVAIL="$DOSSIER/modeles/entrainement"
MOT="hey steve"
mkdir -p "$TRAVAIL" "$DOSSIER/modeles/voix"

etape() { print -P "\n%F{cyan}▶ $1%f"; }

etape "1/6  Les outils (Homebrew : uv, livekit-cli, portaudio)"
command -v brew >/dev/null || { echo "❌ Installe d'abord Homebrew : https://brew.sh"; exit 1; }
command -v uv >/dev/null || brew install uv
command -v lk >/dev/null || brew install livekit-cli
brew list portaudio >/dev/null 2>&1 || brew install portaudio

etape "2/6  Connexion à LiveKit Cloud (une page s'ouvre dans ton navigateur)"
cd "$TRAVAIL"
if [[ ! -f .env.local ]]; then
  lk cloud auth
  lk app env -w
fi
set -a && source .env.local && set +a

etape "3/6  Installation de livekit-wakeword (outil d'entraînement)"
uv tool install --python 3.13 "livekit-wakeword[train]" 2>/dev/null || uv tool upgrade livekit-wakeword
export PATH="$HOME/.local/bin:$PATH"

etape "4/6  Préparation des données (le plus long à télécharger)"
NEGATIFS=()
if livekit-wakeword setup --help 2>&1 | grep -q -- "--hard-negative"; then
  # Ce qu'il ne doit PAS prendre pour son nom.
  for n in "steve" "hey eve" "hé stéphane" "hey stève jobs" "c'est vrai"; do NEGATIFS+=(--hard-negative "$n"); done
fi
livekit-wakeword setup --wakeword "$MOT" --language fr --eval "${NEGATIFS[@]}"

etape "5/6  Entraînement (environ une heure : laisse le Mac branché)"
livekit-wakeword train --wakeword "$MOT" --type standard
livekit-wakeword eval --wakeword "$MOT" || true   # affiche les faux réveils par heure, pour info

MODELE=$(find "$TRAVAIL" -name "hey_steve*.onnx" -newer .env.local -print 2>/dev/null | head -1)
[[ -n "$MODELE" ]] || { echo "❌ Pas trouvé le modèle entraîné dans $TRAVAIL."; exit 1; }
cp "$MODELE" "$DOSSIER/modeles/voix/hey_steve.onnx"

etape "6/6  Branchement dans Steve"
"$DOSSIER/.venv/bin/python" -m pip install --quiet livekit-wakeword

print -P "\n%F{green}✅ « Hey Steve » est prêt : modeles/voix/hey_steve.onnx%f"
echo "   Essaie-le d'abord, si tu veux :"
echo "     uvx --from 'livekit-wakeword[listener]' livekit-wakeword listen $DOSSIER/modeles/voix/hey_steve.onnx"
echo "   Puis relance Steve : double-clique installer.command."
echo "   (S'il se réveille trop souvent : JERVIS_SEUIL_MOTCLE=0.7 ; pas assez : 0.4.)"
