"""Les phrases courtes de Steve, vérifiées une par une.

Sa voix locale (voix_locale.py) dit bien les phrases entières, mais elle rate une phrase courte sur six :
« Une seconde. » sort parfois en charabia. Or ce sont les phrases qu'il dit le plus souvent. Ici, chacune est
fabriquée, puis réécoutée par Whisper : on garde la première version qu'il reconnaît mot pour mot, et
voix_locale.dire la reprendra telle quelle, toujours la même.

    .venv/bin/python -m jervis.voix_sures            # fabrique celles qui manquent pour la voix choisie
    .venv/bin/python -m jervis.voix_sures --tout     # les refait toutes

À relancer après avoir changé de voix (outils/choisir_voix.command le fait tout seul) ou de phrases d'attente.
Une phrase qui n'est pas rangée ici reste dite par le modèle, sans vérification.
"""

from __future__ import annotations

import re
import sys
import unicodedata

import numpy as np

from . import config, oral, tts, voix_locale

ESSAIS = 12   # au-delà, la voix n'y arrive pas : la phrase reste fabriquée au fil de l'eau

# Ce que le programme lui fait dire lui-même, en plus des phrases d'attente et des relances de config.py.
DU_PROGRAMME = ["Pardon, c'est oui ou non ?", "Oui ou non ?", "À plus tard.", "Je t'écoute.", "Oui ?"]
# Les réponses courtes qui reviennent le plus dans une conversation.
COURANTES = [
    "Oui.", "Non.", "D'accord.", "C'est fait.", "C'est noté.", "C'est bon.", "C'est envoyé.", "C'est parti.",
    "Très bien.", "Bien sûr.", "Entendu.", "Ça marche.", "Pas de souci.", "Avec plaisir.", "De rien.", "Merci.",
    "Voilà.", "Tout de suite.", "Je m'en occupe.", "Je ne sais pas.", "Aucune idée.", "Désolé.", "Pardon ?",
    "Bonjour.", "Bonsoir.", "Salut.", "Bonne nuit.", "Bonne journée.", "À demain.", "À tout à l'heure.",
]


def phrases() -> list[str]:
    """Tout ce qui mérite d'être vérifié, découpé comme tts.parler le découpe, sans doublon."""
    toutes = [config.ANNONCE, *config.PHRASES_PATIENCE, *DU_PROGRAMME, *COURANTES]
    bouts = [bout for p in toutes for bout in tts._phrases(tts._nettoyer(p))]
    return list(dict.fromkeys(bouts))


def mots(texte: str) -> list[str]:
    """Les mots du texte, sans majuscules, accents ni ponctuation : ce qu'on compare. Les lettres muettes de
    la fin ne comptent pas : « souci » et « soucis », « désolé » et « désolée » se disent pareil, et Whisper
    écrit l'un ou l'autre (« désolée » pour une voix de femme)."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte.casefold())
                           if unicodedata.category(c) != "Mn")
    return [re.sub(r"(?<=e)e$", "", re.sub(r"(?<=..)[sx]$", "", mot)) for mot in re.findall(r"[a-z0-9]+", sans_accents)]


def reconnue(phrase: str, entendu: str) -> bool:
    return bool(mots(entendu)) and mots(entendu) in (mots(phrase), mots(oral.en_mots(phrase)))


def reecouter(son: np.ndarray) -> str:
    """Ce que Whisper (en français) comprend de ce son, tel qu'il sortira des enceintes."""
    from scipy.signal import resample_poly

    from . import stt
    son = resample_poly(tts._rogner(son), 1, 3).astype(np.float32)   # 48 kHz → 16 kHz
    son = son / max(1e-6, float(np.abs(son).max())) * 0.5
    return stt._sur_le_fil_mlx(stt._transcrire_whisper, (son * 32767).astype(np.int16), False)


def verifier(phrase: str, essais: int = ESSAIS, ecouter=reecouter) -> int:
    """Fabrique la phrase jusqu'à ce qu'elle soit reconnue, et la range. Renvoie le nombre d'essais (0 = échec)."""
    for essai in range(1, essais + 1):
        son = voix_locale.fabriquer(phrase)
        if son is None:
            return 0
        if reconnue(phrase, ecouter(son)):
            voix_locale.garder(phrase, son)
            return essai
    return 0


def construire(tout: bool = False) -> bool:
    if not voix_locale.active():
        print("La voix locale est coupée (il parle avec la voix Microsoft) : rien à vérifier.", flush=True)
        return True
    voix_locale.demarrer()
    if not voix_locale.attendre(120):
        print(f"❌ La voix locale ne se charge pas ({voix_locale.erreur or 'elle est coupée'}).", flush=True)
        return False
    a_faire = [p for p in phrases() if tout or voix_locale.sure(p) is None]
    print(f"🗣  Voix « {config.VOIX_LOCALE} » : {len(a_faire)} phrase(s) courte(s) à vérifier "
          f"({len(phrases()) - len(a_faire)} déjà rangée(s)).", flush=True)
    du_premier_coup, ratees = 0, []
    for phrase in a_faire:
        essais = verifier(phrase)
        du_premier_coup += essais == 1
        if not essais:
            ratees.append(phrase)
        print(f"   {'✓' if essais else '✗'} {phrase}" + (f"  ({essais} essais)" if essais > 1 else ""), flush=True)
    if a_faire:
        print(f"   Bonnes du premier coup : {du_premier_coup}/{len(a_faire)}.", flush=True)
    if ratees:
        print(f"⚠️  {len(ratees)} phrase(s) jamais reconnue(s) en {ESSAIS} essais : {' / '.join(ratees)}\n"
              "   Elles restent dites par le modèle, sans vérification.", flush=True)
    return True


if __name__ == "__main__":
    sys.exit(0 if construire("--tout" in sys.argv) else 1)
