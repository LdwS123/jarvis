"""Passer un appel avec ton iPhone, depuis le Mac : « Steve, appelle maman ».

macOS sait appeler par l'iPhone (Continuité : FaceTime → Réglages → « Appels depuis l'iPhone ») : Steve ouvre
tel://numéro (ou facetime-audio://), macOS affiche « Appeler … avec l'iPhone ? », et il clique sur « Appeler » pour
toi (l'autorisation Accessibilité). Tu parles ensuite dans le micro du Mac, ou tu prends l'appel sur l'iPhone.

Il compose, toi tu parles : il ne dit jamais un mot dans l'appel. Jamais de numéro surtaxé (ça paie). Un numéro
d'urgence, ou un appel qui suit la lecture d'une page ou d'un mail, est toujours demandé (permissions.py).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from claude_agent_sdk import tool

from . import config

# Les numéros d'urgence (France et Europe) : appelés seulement après ton oui.
URGENCES = {"15": "le SAMU", "17": "la police", "18": "les pompiers", "112": "les urgences", "114": "les urgences (SMS)",
            "115": "le Samu social", "119": "l'enfance en danger", "191": "les urgences aéronautiques",
            "196": "les urgences en mer", "197": "l'alerte attentat", "116000": "les enfants disparus",
            "3114": "la prévention du suicide"}


def _texte(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def _erreur(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "is_error": True}


def numero_appelable(brut: Any) -> str | None:
    """« 06 12 34 56 78 », « +33 (0)6 12… », « tel:+33612345678 » → « 0612345678 » / « +33612345678 ».
    Autre chose qu'un numéro (un nom, une adresse mail, des lettres) → None."""
    texte = str(brut or "").strip().removeprefix("tel:").replace("(0)", "")
    if not texte or not re.fullmatch(r"\+?[\d\s.\-()/]+", texte):
        return None
    chiffres = re.sub(r"\D", "", texte)
    if texte.startswith("00"):
        texte, chiffres = "+" + chiffres[2:], chiffres[2:]
    if not 2 <= len(chiffres) <= 15:
        return None
    numero = ("+" if texte.startswith("+") else "") + chiffres
    # « +33 0899… » (le 0 national gardé après l'indicatif) : le même numéro que « 0899… », il doit être vu surtaxé.
    while numero.startswith("+330"):
        numero = "+33" + numero[4:]
    return numero


def national(numero: str) -> str:
    """Le numéro tel qu'on le compose en France (« +33612345678 » → « 0612345678 »)."""
    return "0" + numero[3:] if numero.startswith("+33") else numero


def numero_urgence(numero: str | None) -> str | None:
    """Qui on appelle si c'est un numéro d'urgence (« la police »), sinon None."""
    return URGENCES.get(national(numero or ""))


def numero_surtaxe(numero: str) -> bool:
    """Un numéro qui coûte plus qu'un appel normal : 081 à 089 (sauf 080, gratuit), les 118 (renseignements),
    les numéros courts (3xxx, 10xx : souvent payants), les satellites (+881…) et les « kiosques » étrangers (+44 9…)."""
    n = national(numero)
    if n in URGENCES:
        return False
    return bool(re.fullmatch(r"08[1-9]\d{7}|118\d{3}|1\d{3}|3\d{3}", n)
                or re.match(r"\+(?:88[1-3]|8[78]\d|979|44(?:9|87)|1900)", n))


# Le bouton « Appeler » (ou « Raccrocher ») de la bulle de macOS, dans le centre de notifications ou FaceTime.
# Il le cherche pendant `item 2 of argv` secondes : la bulle met un instant à s'afficher.
_CLIQUER = """
on run argv
  set noms to paragraphs of (item 1 of argv)
  set fin to (current date) + ((item 2 of argv) as integer)
  tell application "System Events"
    repeat while (current date) < fin
      repeat with p in {"NotificationCenter", "FaceTime"}
        if exists process p then
          tell process p
            repeat with w in windows
              try
                repeat with e in (entire contents of w)
                  try
                    if role of e is "AXButton" then
                      set n to ""
                      try
                        set n to (name of e) as text
                      end try
                      if n is "" or n is "missing value" then set n to (description of e) as text
                      if noms contains n then
                        click e
                        return "ok"
                      end if
                    end if
                  end try
                end repeat
              end try
            end repeat
          end tell
        end if
      end repeat
      delay 0.3
    end repeat
  end tell
  return "introuvable"
end run
"""
BOUTONS_APPELER = ["Appeler", "Call", "Passer l'appel", "Appel"]
BOUTONS_RACCROCHER = ["Raccrocher", "End", "Terminer", "Fin", "Hang Up"]


async def _lancer(*cmd: str, entree: str | None = None, delai: float = 15) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE if entree is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        sortie, _ = await asyncio.wait_for(proc.communicate(entree.encode() if entree else None), delai)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "trop long"
    return proc.returncode or 0, sortie.decode(errors="replace").strip()


async def cliquer(noms: list[str], secondes: int = 6) -> bool:
    code, sortie = await _lancer("osascript", "-", "\n".join(noms), str(secondes), entree=_CLIQUER,
                                 delai=secondes + 10)
    return not code and sortie == "ok"


@tool("appeler", "Appelle quelqu'un par son iPhone, depuis le Mac (« appelle maman », « appelle le resto »). "
      "`numero` = son numéro (trouvé avec chercher_contact, ou sur la fiche du lieu) ; `nom` = à qui (« maman », "
      "« le Petit Bouillon ») ; `facetime` = true seulement s'il dit FaceTime (gratuit, vers un appareil Apple). "
      "Tu composes, il parle : tu ne dis plus rien pendant l'appel.",
      {"type": "object",
       "properties": {"numero": {"type": "string"}, "nom": {"type": "string"}, "facetime": {"type": "boolean"}},
       "required": ["numero"]})
async def appeler(args: dict[str, Any]) -> dict[str, Any]:
    numero = numero_appelable(args.get("numero"))
    qui = str(args.get("nom") or "").strip()[:60] or (numero or "ce numéro")
    if not config.APPELS:
        return _erreur("Les appels sont coupés (JERVIS_APPELS=non) : dis-lui de l'appeler depuis son iPhone.")
    if numero is None:
        return _erreur("Ce n'est pas un numéro de téléphone. Cherche-le avec chercher_contact (ou sur la fiche du "
                       "lieu), puis rappelle appeler avec le numéro.")
    if numero_surtaxe(numero):
        return _erreur(f"{numero} est un numéro surtaxé : l'appeler coûte de l'argent, et tu ne paies jamais. "
                       "Ne l'appelle pas ; dis-lui en une phrase qu'il peut l'appeler lui-même s'il y tient.")
    schema = "facetime-audio" if args.get("facetime") else "tel"
    code, sortie = await _lancer("open", f"{schema}://{numero}")
    if code:
        return _erreur(f"FaceTime n'a pas pu lancer l'appel ({sortie[:150]}). Dis-lui de vérifier sur le Mac : "
                       "FaceTime → Réglages → « Appels depuis l'iPhone », et l'iPhone sur le même compte iCloud.")
    if not config.APPEL_AUTO or await cliquer(BOUTONS_APPELER):
        return _texte(f"Ça sonne chez {qui}. Dis-le-lui en trois mots (« J'appelle {qui}. »), puis tais-toi : "
                      "c'est lui qui parle.")
    return _texte(f"L'appel vers {qui} est prêt, mais je n'ai pas pu cliquer sur « Appeler » (il me faut "
                  "l'autorisation Accessibilité, ou macOS a changé sa bulle) : dis-lui de cliquer sur « Appeler » "
                  "dans la bulle en haut à droite.")


@tool("raccrocher", "Raccroche l'appel en cours sur le Mac (« raccroche »).", {"type": "object", "properties": {}})
async def raccrocher(_args: dict[str, Any]) -> dict[str, Any]:
    if await cliquer(BOUTONS_RACCROCHER, secondes=2):
        return _texte("Raccroché.")
    return _erreur("Je n'ai pas trouvé d'appel en cours sur le Mac (ou pas l'autorisation Accessibilité). Dis-lui "
                   "de raccrocher sur l'iPhone ou dans la bulle FaceTime.")


OUTILS_APPELS = [appeler, raccrocher]
