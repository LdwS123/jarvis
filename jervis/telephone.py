"""Steve t'appelle pour de vrai : le bilan du soir au téléphone, en FaceTime audio, et il te parle dans l'appel.

Le chemin du son, tout sur le Mac (rien d'autre que l'appel FaceTime lui-même ne quitte le Mac) :
- sa voix → le câble audio APPEL_CABLE (BlackHole 2ch, un appareil son virtuel gratuit) ;
- le câble devient, le temps de l'appel, le micro de macOS : FaceTime l'écoute, ta ligne entend donc sa voix, et
  rien d'autre (ni la pièce, ni ta propre voix qui reviendrait) ;
- ta voix sort des haut-parleurs du Mac, et son micro à lui (épinglé sur celui d'avant l'appel) l'entend.

Il compose (facetime-audio://), tu décroches et dis « allô » : il se présente, te fait le bilan, et tu lui réponds
sans dire son nom. Un blanc, ou « bonne nuit » : il raccroche. Le micro de macOS est remis comme avant à la fin de
l'appel ; s'il s'arrête en plein appel, au redémarrage suivant (le nom de l'ancien micro est noté dans espace/).

Pendant l'appel, la surveillance des appels (reunion.Surveillance) ne prend pas FaceTime pour une réunion, et ta
voix, passée par le téléphone et les haut-parleurs, n'est ni vérifiée ni apprise (ton empreinte reste intacte) : un
oui sensible demandé dans l'appel est donc refusé, comme une voix qu'il ne reconnaît pas.
"""

from __future__ import annotations

import re
import time

from . import appareils, appels, config

ETAT_MICRO = config.ESPACE / "micro_avant_appel.txt"   # le micro de macOS d'avant l'appel (pour le remettre)
APRES_S = 20.0          # après l'appel, FaceTime lâche le micro en quelques secondes : toujours pas une réunion
_FACETIME = ("com.apple.facetime", "com.apple.avconferenced")
_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_en_ligne = False
_jusqua = 0.0


def adresse(brut: str | None = None) -> str | None:
    """Qui appeler (BILAN_SOIR_APPEL) : un numéro (jamais surtaxé) ou une adresse Apple. None : rien d'appelable."""
    brut = (config.BILAN_SOIR_APPEL if brut is None else brut).strip()
    if _MAIL.fullmatch(brut):
        return brut
    numero = appels.numero_appelable(brut)
    return numero if numero and not appels.numero_surtaxe(numero) and not appels.numero_urgence(numero) else None


def cable() -> appareils.Appareil | None:
    """Le câble audio (BlackHole) s'il est installé et vu par macOS (il faut redémarrer après l'avoir installé)."""
    nom = config.APPEL_CABLE.strip().casefold()
    return next((a for a in appareils.tous() if nom and a.entree and nom in a.nom.casefold()), None)


def pas_pret() -> str | None:
    """Pourquoi il ne peut pas t'appeler (une phrase pour le journal), ou None : il peut."""
    if not config.BILAN_SOIR_APPEL:
        return "pas d'adresse à appeler (JERVIS_BILAN_SOIR_APPEL)"
    if adresse() is None:
        return "JERVIS_BILAN_SOIR_APPEL n'est ni un numéro ni une adresse Apple"
    if not config.APPELS:
        return "les appels sont coupés (JERVIS_APPELS=non)"
    if cable() is None:
        return f"pas de câble audio « {config.APPEL_CABLE} » (installe BlackHole 2ch, puis redémarre le Mac)"
    return None


def preparer() -> dict | None:
    """Ce qu'il faut pour l'appel : {"cable": nom du câble, "micro": le micro par lequel il t'entendra}. Rien n'est
    encore changé. None : pas de câble, ou macOS ne dit pas quel est son micro."""
    c = cable()
    defaut, _ = appareils.par_defaut()
    if c is None or defaut is None:
        return None
    tous = appareils.tous()
    if defaut.id == c.id:  # (le câble est déjà le micro de macOS : il reste celui du Mac)
        integre = next((a for a in tous if a.entree and a.transport == appareils.INTEGRE), None)
        if integre is None:
            return None
        defaut = integre
    micro = appareils.choisir_micro(config.MICRO, defaut, tous) or defaut.nom
    return {"cable": c.nom, "cable_id": c.id, "avant": defaut.nom, "micro": micro}


def brancher(prepare: dict) -> bool:
    """Le câble devient le micro de macOS (FaceTime l'écoute). L'ancien est noté d'abord, pour revenir."""
    global _en_ligne
    _en_ligne = True
    ETAT_MICRO.parent.mkdir(parents=True, exist_ok=True)
    ETAT_MICRO.write_text(prepare["avant"], encoding="utf-8")
    if appareils.regler_micro(prepare["cable_id"]):
        return True
    debrancher()
    return False


def debrancher() -> None:
    """Fin de l'appel : le micro d'avant revient. FaceTime reste ignoré encore APRES_S secondes."""
    global _en_ligne, _jusqua
    remettre_le_micro()
    _en_ligne, _jusqua = False, time.monotonic() + APRES_S


def remettre_le_micro() -> bool:
    """Remet le micro de macOS d'avant l'appel, s'il en reste un de noté (au démarrage aussi : Steve a pu
    s'arrêter en plein appel, et ton Mac garderait le câble comme micro)."""
    try:
        nom = ETAT_MICRO.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    micro = next((a for a in appareils.tous() if a.entree and a.nom == nom), None)
    ok = micro is not None and appareils.regler_micro(micro.id)
    ETAT_MICRO.unlink(missing_ok=True)  # (débranché depuis : macOS en choisira un lui-même ; on ne réessaie pas)
    return ok


def en_ligne() -> bool:
    return _en_ligne


def ignorer_facetime() -> bool:
    """La surveillance des appels doit-elle ignorer FaceTime au micro ? (c'est Steve qui t'appelle)"""
    return _en_ligne or time.monotonic() < _jusqua


def est_facetime(app: str) -> bool:
    return app.casefold().startswith(_FACETIME)


async def composer(qui: str) -> bool:
    """Lance l'appel FaceTime audio, et clique « Appeler » si macOS le demande. Faux si FaceTime ne s'ouvre pas."""
    code, _ = await appels._lancer("open", f"facetime-audio://{qui}")
    if code:
        return False
    if config.APPEL_AUTO:
        await appels.cliquer(appels.BOUTONS_APPELER, 8)  # (pas de bulle : l'appel est déjà parti)
    return True


async def raccrocher() -> None:
    """Raccroche : le bouton de FaceTime, sinon FaceTime fermé (ce qui coupe l'appel)."""
    if not await appels.cliquer(appels.BOUTONS_RACCROCHER, 3):
        await appels._lancer("osascript", "-e", 'tell application "FaceTime" to quit', delai=10)
