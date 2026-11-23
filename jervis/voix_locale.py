"""La voix locale de Steve : Pocket TTS (Kyutai), un petit modèle qui tourne sur le processeur du Mac.

Rien ne part sur Internet : une phrase est fabriquée en 0,1 à 0,5 s (la voix Microsoft : 0,6 à 2,6 s, avec
la connexion). Le modèle se charge une fois, au démarrage, sur un fil à part (4 s). S'il manque ou s'il
plante, `dire` renvoie None et tts.py reprend la voix Microsoft, puis celle du Mac.

Il ne lit que des mots : chaque phrase passe d'abord par oral.en_mots (« 14 h 30 » → « quatorze heures trente »).

Son point faible : les phrases de un à trois mots. Il en rate une sur six (« Une seconde. » sort en charabia),
parce qu'il a appris sur des phrases entières. Celles que Steve dit souvent sont donc fabriquées une fois,
réécoutées et rangées dans voix_sures/ (voir voix_sures.py) : `dire` les reprend telles quelles.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import re
import threading
import wave
from pathlib import Path

import numpy as np

from . import config, oral

os.environ.setdefault("HF_HOME", str(config.DOSSIER / "modeles"))  # les modèles restent dans le dossier de Jervis

ECHECS_MAXI = 3          # après trois phrases ratées de suite, il se met de côté jusqu'au prochain démarrage
_NIVEAU = 0.14           # volume visé (celui de la voix Microsoft, mesuré sur les passages parlés)
_SILENCE = np.zeros(2400, dtype=np.float32)  # une phrase sans un seul mot (« … », un émoji) : 50 ms de rien
_SURES = config.DOSSIER / "voix_sures"       # les phrases courtes déjà vérifiées, un dossier par voix

_modele = None
_etat = None             # la voix choisie, déjà écoutée par le modèle
_fil: threading.Thread | None = None
_fini = threading.Event()        # le chargement est terminé (réussi ou non)
_verrou = threading.Lock()       # une phrase à la fois : le modèle n'a qu'un fil de calcul
_echecs = 0
_en_memoire: dict[str, np.ndarray] = {}   # les phrases vérifiées déjà relues sur le disque
erreur: str | None = None        # pourquoi elle n'est pas là, pour le journal


def active() -> bool:
    """Est-ce qu'on veut la voix locale ? (JERVIS_VOIX_LOCALE=non la coupe.)"""
    return config.VOIX_LOCALE.lower() not in {"", "non", "off", "0"}


def prete() -> bool:
    return _etat is not None and _echecs < ECHECS_MAXI


def demarrer() -> None:
    """Charge le modèle sur un fil à part. Sans effet si c'est déjà fait ou en cours."""
    global _fil
    if _fil is None and active():
        _fil = threading.Thread(target=_charger, name="voix-locale", daemon=True)
        _fil.start()


def attendre(delai: float) -> bool:
    """Attend la fin du chargement (au plus `delai` secondes). Dit si la voix est prête."""
    if _fil is not None:
        _fini.wait(delai)
    return prete()


def _ouvrir(voix: str):
    from pocket_tts import TTSModel
    modele = TTSModel.load_model(language=config.VOIX_LOCALE_LANGUE)
    return modele, modele.get_state_for_audio_prompt(voix)


@contextlib.contextmanager
def _hors_ligne():
    """Le paquet demande d'abord à Hugging Face si les fichiers ont changé : une attente (et un échec sans
    Internet). Ils sont déjà sur le disque, épinglés à une version précise : ici, on les prend là."""
    import huggingface_hub
    from pocket_tts.utils import utils
    normal = utils.hf_hub_download
    utils.hf_hub_download = functools.partial(huggingface_hub.hf_hub_download, local_files_only=True)
    try:
        yield
    finally:
        utils.hf_hub_download = normal


def _charger() -> None:
    global _modele, _etat, erreur
    try:
        try:
            with _hors_ligne():
                modele, etat = _ouvrir(config.VOIX_LOCALE)
        except Exception:   # première fois sur ce Mac : il les télécharge (220 Mo)
            modele, etat = _ouvrir(config.VOIX_LOCALE)
        modele.generate_audio(etat, "Bonjour.")  # la première phrase est plus lente : on la fait ici
        _modele, _etat = modele, etat
    except Exception as e:  # paquet absent, voix inconnue, pas de place… : il parlera avec l'autre voix
        erreur = f"{type(e).__name__} : {e}"
    finally:
        _fini.set()


def _niveler(son: np.ndarray) -> np.ndarray:
    """Met chaque phrase au même volume. Le modèle parle deux fois moins fort que la voix Microsoft, et pas
    toujours pareil d'une phrase à l'autre (un « Oui. » sort parfois presque chuchoté)."""
    crete = float(np.abs(son).max())
    if crete < 1e-4:
        return son
    parle = son[np.abs(son) > 0.1 * crete]
    return son * min(_NIVEAU / float(np.sqrt(np.mean(parle ** 2))), 8.0, 0.95 / crete)


def _fichier_sur(texte: str) -> Path:
    """Où est rangée la version vérifiée de ce texte (déjà en mots), pour la voix choisie."""
    cle = hashlib.sha1(" ".join(texte.casefold().split()).encode("utf-8")).hexdigest()[:16]
    return _SURES / f"{config.VOIX_LOCALE_LANGUE}-{config.VOIX_LOCALE}" / f"{cle}.wav"


def sure(phrase: str) -> np.ndarray | None:
    """La version vérifiée de cette phrase (float32, mono, 48 kHz), ou None s'il n'y en a pas."""
    texte = oral.en_mots(phrase)
    if texte not in _en_memoire:
        try:
            with wave.open(str(_fichier_sur(texte)), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (48000, 1, 2):
                    return None
                son = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        except (OSError, EOFError, wave.Error):  # pas de fichier, ou fichier abîmé : il la refabriquera
            return None
        if not len(son):
            return None
        _en_memoire[texte] = son.astype(np.float32) / 32768.0
    return _en_memoire[texte]


def garder(phrase: str, son: np.ndarray) -> None:
    """Range une phrase qui vient d'être vérifiée (le son rendu par `fabriquer`)."""
    texte = oral.en_mots(phrase)
    fichier = _fichier_sur(texte)
    fichier.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(fichier), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(son, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    _en_memoire.pop(texte, None)


def fabriquer(phrase: str) -> np.ndarray | None:
    """Fait dire la phrase au modèle : ses échantillons (float32, mono, 48 kHz, au volume de référence)."""
    global _echecs
    if not prete():
        return None
    texte = oral.en_mots(phrase)
    if not re.search(r"\w", texte):
        return _SILENCE
    try:
        son = _generer(_etat, texte)
        _echecs = 0
        return son
    except Exception:
        _echecs += 1
        return None


def _generer(etat, texte: str) -> np.ndarray:
    from scipy.signal import resample_poly
    with _verrou:
        son = _modele.generate_audio(etat, texte)
    son = np.asarray(son.detach().cpu().numpy(), dtype=np.float32).reshape(-1)
    if not len(son) or not np.isfinite(son).all():
        raise ValueError("son vide")
    if _modele.sample_rate != 48000:
        son = resample_poly(son, 48000, _modele.sample_rate).astype(np.float32)
    return _niveler(son)


def essayer(voix: str, phrase: str) -> np.ndarray | None:
    """La phrase dite par une autre voix que celle choisie : pour les comparer (choisir_voix.py)."""
    if _modele is None:
        return None
    try:
        with _hors_ligne():
            etat = _modele.get_state_for_audio_prompt(voix)
        return _generer(etat, oral.en_mots(phrase))
    except Exception:
        return None


def dire(phrase: str) -> np.ndarray | None:
    """Les échantillons (float32, mono, 48 kHz) de la phrase, ou None si la voix locale ne peut pas la dire."""
    if not prete():
        return None
    son = sure(phrase)
    if son is None:
        son = fabriquer(phrase)
    return None if son is None else np.clip(son * config.VOIX_LOCALE_VOLUME, -1.0, 1.0)
