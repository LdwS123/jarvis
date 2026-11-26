"""Son lexique : les mots que la reconnaissance vocale écorche chez toi, et ce que tu voulais dire.

Parakeet ne connaît ni « Oqro » ni « Stancer » : il écrit « au crow », « stance heure ». Chaque fois que tu le
corriges (« non, j'ai dit Oqro »), il note la paire ici (outil noter_mot, ou la relecture de la conversation), et
la phrase suivante arrive déjà corrigée. Comme un modèle qui s'adapte à ta voix, mais en clair : tu peux lire,
corriger ou rayer une ligne quand tu veux.

La correction ne touche que le texte que Claude lit : le mot d'éveil et le « stop » sont entendus avant, sur ta
phrase brute. Et elle ne peut pas retourner le sens d'une phrase : jamais de « non », « pas », « stop »,
« envoie »… ni dans ce qui est entendu ni dans ce qui le remplace.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from . import config
from .lecons import _SUSPECT, _cle

FICHIER: Path = config.FICHIER_LEXIQUE
ENTETE = (f"# Le lexique de {config.PRENOM}\n"
          "Les mots qu'il entend mal chez toi, et ce que tu voulais dire. Corrige ou efface une ligne quand tu veux.\n\n")
_LIGNE = re.compile(r"^- « (?P<entendu>[^«»]{2,40}) » → « (?P<voulu>[^«»]{1,40}) »(?:  \((?P<date>\d\d/\d\d/\d{4})\))?$")
# Les mots qui portent le sens d'une phrase (dire non, arrêter, envoyer, payer) : une correction ne les touche
# jamais, sinon un « non, n'envoie pas » mal noté deviendrait un « oui ».
SENSIBLES = frozenset(mot for m in (
    "non oui pas ne n jamais rien tout plus stop arrête annule annuler attends attendre envoie envoyer "
    "supprime supprimer efface effacer paye payer achète acheter valide valider confirme confirmer reset "
    "continue vas-y vasy ok okay d'accord").split() for mot in _cle(m).split())
NOM = re.compile(config.NOM, re.IGNORECASE)


def _lire() -> list[tuple[str, str]]:
    try:
        lignes = FICHIER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [(m["entendu"].strip(), m["voulu"].strip()) for l in lignes if (m := _LIGNE.match(l.rstrip()))]


def _ecrire(paires: list[tuple[str, str]], dates: dict[str, str] | None = None) -> None:
    dates = dates or {}
    FICHIER.parent.mkdir(parents=True, exist_ok=True)
    FICHIER.write_text(ENTETE + "".join(
        f"- « {e} » → « {v} »  ({dates.get(_cle(e), f'{datetime.now():%d/%m/%Y}')})\n" for e, v in paires),
        encoding="utf-8")


def _dates() -> dict[str, str]:
    try:
        lignes = FICHIER.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return {_cle(m["entendu"]): m["date"] for l in lignes if (m := _LIGNE.match(l.rstrip())) and m["date"]}


def paires() -> list[tuple[str, str]]:
    return _lire()


def _motif(entendu: str) -> re.Pattern:
    """« au crow » trouve « Au Crow », « au  crow », « au-crow », mais pas « au crowd » (mots entiers)."""
    morceaux = [re.escape(m) for m in re.findall(r"\w+", entendu)]
    return re.compile(r"(?<!\w)" + r"[\s\-’']*".join(morceaux) + r"(?!\w)", re.IGNORECASE)


def corriger(texte: str) -> str:
    """Ta phrase, avec les mots de ton lexique remis d'aplomb. Les plus longs d'abord (« au crow prod » avant
    « au crow »). Rien ne change si le lexique est coupé (JERVIS_LEXIQUE=non) ou vide."""
    if not (config.LEXIQUE and texte):
        return texte
    for entendu, voulu in sorted(_lire(), key=lambda p: -len(p[0])):
        texte = _motif(entendu).sub(voulu, texte)
    return texte


def _refus(entendu: str, voulu: str) -> str:
    """Pourquoi cette paire ne peut pas entrer dans le lexique ("" si elle peut)."""
    if not 3 <= len(entendu) <= 40 or not 1 <= len(voulu) <= 40:
        return "Refusé : « entendu » fait de 3 à 40 caractères, « voulu » de 1 à 40."
    if not re.search(r"\w", entendu) or "«" in entendu + voulu or "»" in entendu + voulu:
        return "Refusé : écris les mots tels quels, sans guillemets."
    if _cle(entendu) == _cle(voulu):
        return "Refusé : c'est déjà le même mot."
    if _SUSPECT.search(entendu) or _SUSPECT.search(voulu):
        return "Refusé : un mot du lexique ne contient ni lien, ni adresse mail, ni numéro."
    if (set(_cle(entendu).split()) | set(_cle(voulu).split())) & SENSIBLES:
        return ("Refusé : le lexique ne touche jamais aux mots qui portent le sens d'une phrase (non, pas, stop, "
                "envoie, payer…). Note seulement le nom propre ou le mot technique mal entendu.")
    if NOM.search(entendu) or NOM.search(voulu):
        return f"Refusé : on ne réécrit pas le nom de {config.PRENOM}, son réglage est à part (JERVIS_NOM)."
    return ""


def noter_mot(entendu: str, voulu: str) -> str:
    """Ajoute (ou remplace) une paire « entendu » → « voulu ». Renvoie ce qui a été fait."""
    entendu, voulu = " ".join(str(entendu).split()), " ".join(str(voulu).split())
    if motif := _refus(entendu, voulu):
        return motif
    actuelles = _lire()
    cle = _cle(entendu)
    if (entendu, voulu) in actuelles or any(_cle(e) == cle and v == voulu for e, v in actuelles):
        return "Je le savais déjà."
    dates = _dates()
    gardees = [(e, v) for e, v in actuelles if _cle(e) != cle]
    remplacee = len(gardees) != len(actuelles)
    dates.pop(cle, None)
    _ecrire([*gardees, (entendu, voulu)][-config.LEXIQUE_MAX:], dates)
    return f"Noté : « {entendu} » voudra dire « {voulu} »" + (" (l'ancienne correction est remplacée)." if remplacee else ".")


def oublier_mot(entendu: str) -> bool:
    actuelles = _lire()
    gardees = [(e, v) for e, v in actuelles if _cle(e) != _cle(entendu)]
    if len(gardees) == len(actuelles):
        return False
    _ecrire(gardees, _dates())
    return True


def pour_consignes() -> str:
    """Ce qu'il sait de ta façon de parler, à la fin de ses consignes."""
    liste = _lire()
    if not liste:
        return ""
    return ("\nTon lexique (les mots que la reconnaissance vocale écorche chez lui ; ses phrases te parviennent déjà "
            "corrigées, ceci t'aide à reconnaître les variantes) : "
            + " ; ".join(f"« {e} » = {v}" for e, v in liste) + "\n")


def preparer() -> Path:
    if not FICHIER.exists():
        _ecrire([])
    return FICHIER


def effacer() -> bool:
    if not FICHIER.exists():
        return False
    _ecrire([])
    return True


@tool("noter_mot", "Note dans ton lexique un mot que la reconnaissance vocale entend mal chez lui, quand il te "
      "corrige (« non, j'ai dit Oqro », « Stancer, avec un S ») ou quand le même mot écorché revient et que tu as "
      "compris lequel c'était. `entendu` = ce que tu as reçu (« au crow »), `voulu` = le bon mot (« Oqro »). Ses "
      "phrases suivantes arriveront déjà corrigées. Seulement des noms propres et des mots techniques, jamais "
      "« non », « pas », « stop » ni un verbe d'action. Ne le dis pas à voix haute, sauf s'il le demande.",
      {"type": "object",
       "properties": {"entendu": {"type": "string"}, "voulu": {"type": "string"}},
       "required": ["entendu", "voulu"]})
async def outil_noter_mot(args: dict[str, Any]) -> dict[str, Any]:
    resultat = noter_mot(args.get("entendu", ""), args.get("voulu", ""))
    return {"content": [{"type": "text", "text": resultat}], **({"is_error": True} if resultat.startswith("Refusé") else {})}
