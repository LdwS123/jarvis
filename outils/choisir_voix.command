#!/bin/zsh
# Choisir la voix de Steve. Double-clique ce fichier, ou : zsh outils/choisir_voix.command
#
# Il te fait écouter la même phrase avec quatre voix fabriquées sur ton Mac (sans Internet) et avec
# l'ancienne voix Microsoft, note ton choix dans voix_choisie.txt, puis vérifie une par une les phrases
# courtes de la voix choisie (« Oui ? », « Une seconde. »… : une minute). Steve la prend au prochain démarrage.
set -euo pipefail
cd "$(dirname "$0")/.."

# (Fais-le quand Steve ne t'écoute pas : il entendrait les essais sortir des enceintes.)
.venv/bin/python -m jervis.choisir_voix 2>/dev/null
.venv/bin/python -m jervis.voix_sures 2>/dev/null || echo "⚠️  Les phrases courtes n'ont pas pu être vérifiées : il les dira sans vérification."

print -P "\n%F{cyan}Pour que Steve prenne cette voix, relance-le :%f"
echo '  pkill -f "Jervis.app/Contents"; pkill -f "python -m jervis"; sleep 2; open -a ~/Jervis/Jervis.app'
