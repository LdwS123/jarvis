"""Seul toi : le mot de passe du Mac, pour qu'il modifie son propre code.

Quand tu lui dis « améliore-toi », « t'as le droit, change ton code », il le modifie lui-même (Read, Edit, Write) :
jervis/, tests/, Jervis.app, ses outils, ses scripts de lancement, sa doc. Un « oui » ne suffit pas, ni dit ni écrit
(une voix s'imite, un message se glisse) : la première fois, macOS lui-même ouvre sa fenêtre « Steve veut modifier
son propre code » et te demande le mot de passe de ta session, devant le Mac. Ce mot de passe n'arrive jamais ni
dans son code ni chez Claude : c'est macOS qui le vérifie (osascript « with administrator privileges »), Steve ne
reçoit que « oui » ou « non ». Ensuite, l'accord reste ouvert CODE_ACCES_MIN minutes, pour toute la modification.

Toujours redemandé, même accord ouvert : juste après une page, un mail ou le compte rendu d'un agent (ce texte
pourrait lui avoir dicté le changement), et pendant un appel. Loin du Mac (il t'écrit depuis ton iPhone) : personne
pour taper le mot de passe, donc non, sauf accord encore ouvert. Ta voix (voix.npy, voix_depart.npy,
voix_centres.npy) : jamais par un outil. Une commande (sed -i, >, git checkout…) qui toucherait son code passe par
le même mot de passe ; après une page lue, son code ne se modifie que par Edit ou Write.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from . import config

RACINE = Path(__file__).resolve().parent.parent  # ~/Jervis
# Ce qui est son code (et sa doc), à la racine de ~/Jervis
SON_CODE = {"jervis", "tests", "jervis.app", "outils", ".github", "installer.command", "lancer.command",
            "reglages.command", "requirements.txt", "pytest.ini", "readme.md", "readme.en.md", "readme.ka.md",
            "readme.zh.md", "contributing.md", "security.md"}
# Ta voix : jamais par un outil (ni toi par erreur en lui demandant, ni une page)
TA_VOIX = {"voix.npy", "voix_depart.npy", "voix_centres.npy"}
DELAI_S = 120   # le temps de taper le mot de passe

_ouvert_jusqu_a = 0.0


def _relatif(chemin: str) -> tuple[str, ...] | None:
    """Les parties du chemin sous ~/Jervis (sans casse, comme APFS), ou None s'il est ailleurs."""
    try:
        vrai = Path(chemin).expanduser()
        vrai = (vrai if vrai.is_absolute() else config.ESPACE / vrai).resolve()
        racine = RACINE.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if str(vrai).casefold() == str(racine).casefold():
        return ()
    if not str(vrai).casefold().startswith(str(racine).casefold() + "/"):
        return None
    return tuple(p.casefold() for p in vrai.parts[len(racine.parts):])


def son_code(chemin: str) -> bool:
    parties = _relatif(chemin)
    return bool(parties) and parties[0] in SON_CODE


def ta_voix(chemin: str) -> bool:
    parties = _relatif(chemin)
    return bool(parties) and len(parties) == 1 and parties[0] in TA_VOIX


# Une commande qui nomme son code (~/Jervis/jervis, ../tests, cd ~/Jervis…) et qui écrit (sed -i, >, tee, cp, mv,
# git checkout…) : une règle simple, pas un analyseur de shell. Son vrai chemin pour changer son code reste Edit.
_NOMME_SON_CODE = re.compile(
    r"(?:(?:~|\$HOME|\$\{HOME\}|/Users/[^/\s]+)/Jervis|\.\.)/(?:jervis|tests|Jervis\.app|outils|\.github|[\w.]+\.command"
    r"|requirements\.txt|pytest\.ini|README[\w.]*\.md|voix[\w]*\.npy)\b"
    r"|\bcd\s+[\"']?(?:~|\$HOME|\$\{HOME\}|/Users/[^/\s]+)/Jervis[\"']?\s*(?:&&|;|$)", re.IGNORECASE)
_ECRIT = re.compile(
    r"\bsed\b[^|;&]*\s-[a-zA-Z]*i|\bperl\b[^|;&]*\s-[a-zA-Z]*i|(?<![0-9&>])>{1,2}(?!\s*/dev/null)(?!&)"
    r"|\b(?:tee|cp|mv|ln|install|rsync|patch|truncate|touch|dd|unzip|tar)\b"
    r"|\bgit\s+(?:checkout|restore|reset|apply|am|stash|pull|merge|rebase|cherry-pick|revert|switch|clean)\b"
    r"|\bopen\([^)]*[\"'][wax]|\bwrite_(?:text|bytes)\b|\.write\(|\bshutil\.", re.IGNORECASE)


def commande_touche_son_code(commande: str) -> bool:
    return bool(_NOMME_SON_CODE.search(commande) and _ECRIT.search(commande))


def ouvert() -> bool:
    return time.monotonic() < _ouvert_jusqu_a


def ouvrir() -> None:
    global _ouvert_jusqu_a
    _ouvert_jusqu_a = time.monotonic() + config.CODE_ACCES_MIN * 60


def fermer() -> None:
    global _ouvert_jusqu_a
    _ouvert_jusqu_a = 0.0


def _chaine_applescript(texte: str) -> str:
    return '"' + texte.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def demander_mot_de_passe(raison: str) -> bool:
    """La fenêtre de macOS (mot de passe de ta session, ou Touch ID selon ton Mac). Vrai seulement si macOS l'a
    accepté ; annulé, faux, ou pas de réponse en DELAI_S secondes : faux. Steve ne voit jamais ce que tu tapes."""
    script = (f'do shell script "/usr/bin/true" with prompt {_chaine_applescript(raison)} '
              f"with administrator privileges")
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return False
    try:
        return await asyncio.wait_for(proc.wait(), DELAI_S) == 0
    except asyncio.TimeoutError:
        proc.kill()
        return False
