"""Te fait écouter les voix possibles de Steve, et note celle que tu choisis dans voix_choisie.txt.

    zsh outils/choisir_voix.command      (ou double-clic dessus)

Les voix locales sont fabriquées sur ce Mac, sans Internet. La dernière de la liste est l'ancienne voix
Microsoft : plus lente (une seconde et demie par phrase) et elle a besoin d'Internet, mais c'est la plus sûre
sur les phrases très courtes. Steve prend ton choix à son prochain démarrage.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, tts, voix_locale

# La phrase d'essai ne contient pas son nom : si Steve tourne à côté, il ne doit pas se croire appelé.
ESSAI = "Bonjour ! Tu as trois nouveaux mails, et il est 14 h 30. Je te les lis maintenant, ou plus tard ?"
LOCALES = [("giovanni", "voix d'homme, celle d'origine"),
           ("estelle", "voix de femme, la seule enregistrée par une Française"),
           ("javert", "voix d'homme"),
           ("juergen", "voix d'homme")]
MICROSOFT = "non"   # ce qu'on écrit dans voix_choisie.txt pour couper la voix locale
FICHIER = config.DOSSIER / "voix_choisie.txt"


def _jouer(chemin: Path) -> None:
    subprocess.run(["afplay", str(chemin)], check=False)


def _son_local(voix: str, dossier: Path) -> Path | None:
    son = voix_locale.essayer(voix, ESSAI)
    if son is None:
        return None
    chemin = dossier / f"{voix}.wav"
    tts._ecrire_wav(chemin, son)
    return chemin


def _son_microsoft() -> Path | None:
    try:
        return asyncio.run(tts._synthetiser(ESSAI))
    except Exception:
        return None


def choisir() -> int:
    print("Je prépare les voix (quelques secondes)…", flush=True)
    actuelle = config.VOIX_LOCALE if voix_locale.active() else MICROSOFT
    if not voix_locale.active():
        config.VOIX_LOCALE = LOCALES[0][0]   # coupée pour l'instant : on la charge quand même, pour comparer
    voix_locale.demarrer()
    voix_locale.attendre(120)
    dossier = Path(tempfile.mkdtemp(prefix="jervis-essai-"))
    choix: list[tuple[str, str, Path]] = []   # (ce qu'on écrira, ce qu'on affiche, le son)
    for voix, portrait in LOCALES:
        if chemin := _son_local(voix, dossier):
            choix.append((voix, f"{voix} : {portrait}", chemin))
    if chemin := _son_microsoft():
        choix.append((MICROSOFT, "Rémy : l'ancienne voix Microsoft (avec Internet)", chemin))
    if not choix:
        print(f"❌ Aucune voix n'a pu être préparée ({voix_locale.erreur or 'pas de voix locale, pas d’Internet'}).")
        return 1

    for numero, (_, portrait, chemin) in enumerate(choix, 1):
        print(f"\n  {numero}. {portrait}", flush=True)
        _jouer(chemin)
    while True:
        reponse = input(f"\nTon choix (1 à {len(choix)}), un numéro suivi de « ? » pour réécouter (ex. 2?), "
                        "ou Entrée pour ne rien changer : ").strip()
        if not reponse:
            print(f"Rien de changé : il garde « {actuelle} ».")
            return 0
        numero = reponse.rstrip("?").strip()
        if not numero.isdigit() or not 1 <= int(numero) <= len(choix):
            continue
        nom, portrait, chemin = choix[int(numero) - 1]
        if reponse.endswith("?"):
            _jouer(chemin)
            continue
        FICHIER.write_text(nom + "\n", encoding="utf-8")
        print(f"✓ Noté : {portrait}.")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(choisir())
    except (KeyboardInterrupt, EOFError):
        print("\nRien de changé.")
        sys.exit(0)
