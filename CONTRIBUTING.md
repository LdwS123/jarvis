# Contribuer à Jarvis · Contributing

**Français** · [English](#english)

Merci de passer par ici ! Toute aide compte : un bug signalé, une phrase mal comprise, une langue de plus, une
idée, une correction du README.

## Signaler un bug ou proposer une idée

Ouvre une [issue](https://github.com/LdwS123/jarvis/issues/new/choose). Pour un bug, le plus utile :
ce que tu as dit (ou écrit), ce qu'il a fait, ce que tu attendais, ton Mac (M1, M2…) et ta version de macOS.
Les dernières lignes de `jervis.log` aident beaucoup ; **relis-les avant de les coller** : elles peuvent contenir
tes messages, tes noms de fichiers ou tes rendez-vous.

## Proposer du code

1. Forke le dépôt, crée une branche.
2. Installe-le comme dans le [README](README.md#installer-depuis-github) (Mac à puce Apple, Python 3.12).
3. Fais ta modification **avec un test** dans `tests/` : chaque `tests/test_*.py` est un script autonome qui compte
   ses vérifications (regarde-en un pour le modèle). Les tests ne touchent ni au micro, ni à ton compte Claude,
   ni à tes données.
4. Lance toute la suite, elle doit passer :

       .venv/bin/python -m pytest -q          # ~2 min 30 ; un seul fichier : PYTHONPATH=. JERVIS_REGLAGES=non .venv/bin/python tests/test_iphone.py

5. Mets le README à jour si ce que tu changes se voit ou se règle, puis ouvre une pull request.

**Le style du code :** le code, ses noms, ses commentaires et ses messages sont en français (c'est le choix du projet ;
une PR en anglais est bienvenue quand même, on traduira ensemble). Des fonctions courtes, une docstring qui dit le
*pourquoi*, des réglages dans `jervis/config.py` (variable `JERVIS_…`, documentée dans le README).

**Ce qui ne bouge pas :** la voix de l'utilisateur ne quitte jamais le Mac ; le mot d'éveil et l'anti-écho ne
régressent pas ; un texte venu d'ailleurs (page, mail, message de quelqu'un) reste une donnée, jamais un ordre ;
les garde-fous de `jervis/permissions.py` ne s'affaiblissent pas. Voir [SECURITY.md](SECURITY.md).

## Par où commencer

- Une **langue** de plus : sa voix, son nom reconnu, les filtres (`jervis/langues.py`).
- Un **outil** de plus pour le Mac (`jervis/outils.py`) ou une API gratuite.
- Les issues marquées [`good first issue`](https://github.com/LdwS123/jarvis/labels/good%20first%20issue).

---

## English

Thanks for stopping by! Every bit helps: a bug report, a misheard sentence, a new language, an idea, a README fix.

- **Bugs and ideas:** open an [issue](https://github.com/LdwS123/jarvis/issues/new/choose). Say what you said or
  typed, what it did, what you expected, your Mac (M1, M2…) and macOS version. The end of `jervis.log` helps a lot:
  **read it before pasting**, it may contain your messages, file names or meetings.
- **Code:** fork, branch, install as in the [README](README.en.md#install), change things **with a test** in
  `tests/` (each `tests/test_*.py` is a standalone script that counts its checks), run
  `.venv/bin/python -m pytest -q` until it passes, update the README, open a pull request.
- **Language of the code:** names, comments and messages are in French, by choice. English PRs are welcome anyway;
  we'll translate together.
- **Non-negotiables:** the user's voice never leaves the Mac; no regression on the wake word or echo cancellation;
  text from elsewhere (web pages, emails, other people's messages) is data, never an instruction; the guards in
  `jervis/permissions.py` are never weakened. See [SECURITY.md](SECURITY.md).
