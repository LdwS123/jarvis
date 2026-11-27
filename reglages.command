#!/bin/zsh
# Double-clique ce fichier : la page Réglages s'ouvre dans ton navigateur (son nom, tes langues, sa voix, ton compte
# Claude). Elle marche aussi avant son premier lancement. Ferme cette fenêtre (ou ctrl-C) quand tu as fini.
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || { echo "❌ Pas de .venv ici : installe d'abord Jervis (voir README)."; exit 1; }
exec .venv/bin/python -m jervis.reglages "$@"
