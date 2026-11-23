#!/bin/zsh
# Double-clique ce fichier dans le Finder pour lancer Jervis dans le Terminal.
cd "$(dirname "$0")" && exec .venv/bin/python -m jervis "$@"
