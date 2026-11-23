"""La voix de Jervis : d'abord sa voix locale (Pocket TTS, voix_locale.py), qui fabrique une phrase en
0,1 à 0,5 s sans Internet ; sinon la voix neuronale Microsoft (edge-tts) ; et la voix macOS `say` en dernier secours.

Quand le micro tourne en duplex (voir audio.py), le son est décodé et joué par ce flux-là :
l'anti-écho connaît alors exactement ce qui sort des enceintes et le retire du micro.
Sinon (micro en pause, flux duplex impossible), on joue avec `afplay` comme avant.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from . import config, ligne, oral, voix_locale

_DOSSIER_AUDIO = Path(tempfile.mkdtemp(prefix="jervis-voix-"))
_TINK = Path("/System/Library/Sounds/Tink.aiff")
_POP = Path("/System/Library/Sounds/Pop.aiff")
_compteur = 0
_lecture: asyncio.subprocess.Process | None = None  # le son afplay en cours, pour pouvoir le couper
_tais_toi = False
_haut_parleur = None   # audio.HautParleur, branché par le mode vocal


def brancher(haut_parleur) -> None:
    """Fait passer sa voix par le flux duplex du micro (pour l'anti-écho)."""
    global _haut_parleur
    _haut_parleur = haut_parleur


def _duplex() -> bool:
    return _haut_parleur is not None and _haut_parleur.actif


def _nettoyer(texte: str) -> str:
    """Retire le markdown et les URL, qui sonnent mal à l'oral."""
    texte = re.sub(r"https?://\S+", "", texte)
    texte = re.sub(r"[*_`#>|]", "", texte)
    texte = re.sub(r"^\s*[-•]\s+", "", texte, flags=re.MULTILINE)
    texte = oral.abreviations(texte)  # « M. Dupont » : ce point-là n'est pas une fin de phrase
    return re.sub(r"\s+", " ", texte).strip()


# La voix Microsoft fabrique toute la phrase, puis envoie tout le son d'un coup (en 0,2 à 0,9 s). Mesuré sur des
# phrases jamais dites : 1,1 à 2 s d'attente pour 30 caractères, 1,9 à 3,5 s pour 60, 2,2 à 4,5 s pour 140, dont
# 0,45 s rien que pour ouvrir la connexion. (Une phrase déjà dite revient en 0,6 s : le service s'en souvient.)
# Une longue phrase est donc dite par bouts (à ses virgules) : le premier part vite, et les suivants se
# fabriquent pendant qu'il le dit. Jouer le son au fil de son arrivée ne ferait gagner que 0,1 à 0,3 s : ce qui
# compte, c'est la connexion déjà ouverte et la demande doublée du premier bout (voir ligne.py).
_PHRASE_LONGUE = 60
_BOUT_MINI = 15  # « Oui, » ou « Bon, » seuls : collés au bout suivant


def _bouts(phrase: str) -> list[str]:
    if len(phrase) <= _PHRASE_LONGUE:
        return [phrase]
    bouts: list[str] = []
    for bout in re.split(r"(?<=[,;:])\s+", phrase):  # « 5,5 » n'est pas coupé : pas d'espace après la virgule
        if bouts and len(bouts[-1]) < _BOUT_MINI:
            bouts[-1] += " " + bout
        else:
            bouts.append(bout)
    return bouts


def _phrases(texte: str) -> list[str]:
    return [b for p in re.split(r"(?<=[.!?…])\s+", texte) if p.strip() for b in _bouts(p)]


def _nouveau_fichier(extension: str) -> Path:
    global _compteur
    _compteur += 1
    return _DOSSIER_AUDIO / f"{_compteur}{extension}"


_DELAI_VOIX = 6.0  # au-delà, on ne l'attend plus : la voix macOS prend le relais
_DELAI_VOIX_LOCALE = 45.0  # au démarrage, le temps qu'on laisse à sa voix locale pour se charger (4 s d'habitude)


async def _synthetiser(phrase: str, en_tete: bool = False) -> Path | None:
    """Fabrique le mp3 d'une phrase. Renvoie None si la voix neuronale est indisponible.
    `en_tete` : c'est le début d'une réponse, celui qu'on attend en silence, donc il est demandé deux fois."""
    if config.VOIX_NEURONALE.lower() in {"", "non", "off"}:
        return None
    fichier = _nouveau_fichier(".mp3")
    depart = time.monotonic()
    if ligne.disponible():
        try:
            son = await asyncio.wait_for(ligne.dire(phrase, config.VOIX_NEURONALE, config.VITESSE, doubler=en_tete),
                                         timeout=_DELAI_VOIX)
            fichier.write_bytes(son)
            return fichier
        except Exception:
            if time.monotonic() - depart > 2:
                return None  # le service est lent ou muet : inutile de l'attendre une deuxième fois
    try:  # le chemin d'edge-tts : une connexion neuve pour cette phrase
        import edge_tts
        com = edge_tts.Communicate(phrase, config.VOIX_NEURONALE, rate=config.VITESSE)
        await asyncio.wait_for(com.save(str(fichier)), timeout=_DELAI_VOIX - (time.monotonic() - depart))
        return fichier if fichier.stat().st_size > 0 else None
    except Exception:
        return None  # pas d'Internet, service lent… on passe à la voix macOS


def prechauffer() -> None:
    """Il va avoir à parler : ouvre la ligne avec le service de voix pendant que Claude réfléchit.
    Avec sa voix locale, il n'y a rien à ouvrir."""
    voix_locale.demarrer()
    if voix_locale.prete():
        return
    if config.VOIX_NEURONALE.lower() not in {"", "non", "off"}:
        ligne.prechauffer()


async def _say_vers_fichier(phrase: str) -> Path | None:
    fichier = _nouveau_fichier(".aiff")
    proc = await asyncio.create_subprocess_exec("say", "-v", config.VOIX, "-r", config.DEBIT, "-o", str(fichier), phrase)
    await proc.wait()
    return fichier if proc.returncode == 0 and fichier.exists() else None


def _lire_wav(chemin: Path) -> np.ndarray:
    with wave.open(str(chemin)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


async def _decoder(source: Path) -> np.ndarray | None:
    """mp3/aiff → échantillons float32 mono 48 kHz, avec afconvert (fourni avec macOS)."""
    wav = source.with_suffix(".wav")
    proc = await asyncio.create_subprocess_exec("afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "1",
                                                str(source), str(wav))
    await proc.wait()
    try:
        return _lire_wav(wav) if proc.returncode == 0 else None
    except Exception:
        return None
    finally:
        wav.unlink(missing_ok=True)


def _rogner(echantillons: np.ndarray | None, debut_s: float = 0.03, fin_s: float = 0.15) -> np.ndarray | None:
    """La voix Microsoft ajoute ~0,4 s de silence autour de chaque bout : dit par bouts, ça hacherait la phrase.
    On garde juste une petite respiration à la fin (la pause d'une virgule)."""
    if echantillons is None or not len(echantillons):
        return echantillons
    fort = np.flatnonzero(np.abs(echantillons) > 0.01)
    if not len(fort):
        return echantillons
    return echantillons[max(0, fort[0] - int(debut_s * 48000)):fort[-1] + int(fin_s * 48000)]


def _ecrire_wav(chemin: Path, echantillons: np.ndarray) -> None:
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(echantillons, -1.0, 1.0) * 32767).astype(np.int16).tobytes())


async def _en_local(phrase: str) -> tuple[Path, np.ndarray] | None:
    """La phrase dite par sa voix locale : le fichier (pour afplay, hors duplex) et les échantillons.
    None si elle n'est pas là ou si elle a raté cette phrase : la voix Microsoft prend alors le relais."""
    if not voix_locale.prete():
        return None
    try:
        echantillons = await asyncio.wait_for(asyncio.to_thread(voix_locale.dire, phrase), timeout=_DELAI_VOIX)
    except Exception:
        return None
    if echantillons is None:
        return None
    echantillons = _rogner(echantillons)
    fichier = _nouveau_fichier(".wav")
    _ecrire_wav(fichier, echantillons)
    return fichier, echantillons


async def _preparer(phrase: str, en_tete: bool = False) -> tuple[Path | None, np.ndarray | None]:
    """Le fichier son de la phrase et, si on joue en duplex, ses échantillons déjà décodés."""
    if local := await _en_local(phrase):
        return local
    fichier = await _synthetiser(phrase, en_tete)
    if not _duplex():
        return fichier, None
    fichier = fichier or await _say_vers_fichier(phrase)
    return fichier, (_rogner(await _decoder(fichier)) if fichier else None)


# Le premier bout en fabrication. Plusieurs fabrications en même temps se ralentissent (0,8 s seul, 1,7 s à
# cinq) : les autres attendent qu'il soit prêt, puis partent ensemble pendant qu'il est dit.
_en_tete: asyncio.Task | None = None


def _fabriquer(phrase: str) -> asyncio.Task:
    global _en_tete
    if _en_tete is None or _en_tete.done():
        # S'il ne parle pas en ce moment, ce bout-là est attendu en silence : il est demandé deux fois.
        _en_tete = asyncio.create_task(_preparer(phrase, en_tete=not _verrou.locked()))
        return _en_tete
    tete = _en_tete

    async def apres_la_tete():
        await asyncio.wait({tete})
        return await _preparer(phrase)
    return asyncio.create_task(apres_la_tete())


async def _jouer(commande: list[str]) -> None:
    global _lecture
    _lecture = await asyncio.create_subprocess_exec(*commande)
    await _lecture.wait()
    _lecture = None


# Les phrases déjà en fabrication avant qu'il les dise (la phrase 2 se fabrique pendant qu'il dit la 1).
_anticipees: dict[str, asyncio.Task] = {}
# Les petites phrases préparées une fois pour toutes au démarrage (« Voyons… ») : elles partent sans délai.
_pretes: dict[str, tuple[Path | None, np.ndarray | None]] = {}


def anticiper(texte: str) -> None:
    """Commence à fabriquer le son de ce texte tout de suite ; `parler` le reprendra là où il en est."""
    for phrase in _phrases(_nettoyer(texte)):
        if phrase not in _anticipees and phrase not in _pretes:
            _anticipees[phrase] = _fabriquer(phrase)


async def precharger(phrases: list[str]) -> None:
    """Fabrique et garde en mémoire des phrases courtes qu'il dira souvent."""
    if voix_locale.active():  # on attend sa voix locale : ces phrases et ses réponses auront la même voix
        voix_locale.demarrer()
        await asyncio.to_thread(voix_locale.attendre, _DELAI_VOIX_LOCALE)

    async def une(phrase: str) -> None:
        if local := await _en_local(phrase):
            _pretes[phrase] = local
            return
        fichier = await _synthetiser(phrase) or await _say_vers_fichier(phrase)
        if fichier:
            _pretes[phrase] = (fichier, _rogner(await _decoder(fichier)))
    await asyncio.gather(*(une(_nettoyer(p)) for p in phrases))


def _ranger(fabrication: asyncio.Task) -> None:
    """Efface le fichier d'une phrase anticipée qu'il ne dira finalement pas."""
    def effacer(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is None and (fichier := t.result()[0]):
            fichier.unlink(missing_ok=True)
    fabrication.add_done_callback(effacer)


def taire() -> None:
    """Coupe la phrase en cours et les suivantes (tu lui as coupé la parole)."""
    global _tais_toi
    _tais_toi = True
    for fabrication in _anticipees.values():
        _ranger(fabrication)
    _anticipees.clear()
    if _haut_parleur is not None:
        _haut_parleur.arreter()
    if _lecture and _lecture.returncode is None:
        _lecture.terminate()


_verrou = asyncio.Lock()  # une seule voix à la fois, jamais deux phrases qui se chevauchent


premier_son: float | None = None  # quand le premier son est parti (chrono) ; remis à None par qui mesure


async def parler(texte: str) -> None:
    """Prononce le texte. Toutes les phrases sont préparées en parallèle :
    la première commence à jouer pendant que les suivantes se fabriquent."""
    texte = _nettoyer(texte)
    if not texte:
        return
    phrases = _phrases(texte)

    async def deja_pret(phrase: str):
        return _pretes[phrase]

    # Déjà prête ou en fabrication (anticipée) : on la reprend ; sinon on la lance maintenant.
    fabrications = [asyncio.ensure_future(deja_pret(p)) if p in _pretes
                    else _anticipees.pop(p, None) or _fabriquer(p) for p in phrases]
    global _tais_toi, premier_son
    async with _verrou:
        _tais_toi = False
        for phrase, fabrication in zip(phrases, fabrications):
            fichier, echantillons = await fabrication
            if _tais_toi:
                break
            if premier_son is None:
                premier_son = time.monotonic()
            if echantillons is not None and _duplex():
                await _haut_parleur.jouer(echantillons)
            elif fichier:
                await _jouer(["afplay", str(fichier)])
            else:
                await _jouer(["say", "-v", config.VOIX, "-r", config.DEBIT, phrase])
            if fichier and phrase not in _pretes:
                fichier.unlink(missing_ok=True)
        for phrase, fabrication in zip(phrases, fabrications):  # phrases coupées : on range quand même leurs fichiers
            fichier, _ = await fabrication
            if fichier and phrase not in _pretes:
                fichier.unlink(missing_ok=True)


_sons: dict[Path, np.ndarray] = {}


def _jouer_son(chemin: Path, volume: float = 1.0) -> None:
    """Joue un son système sans attendre (par le haut-parleur de l'anti-écho s'il existe)."""
    if _duplex():
        if chemin not in _sons:
            wav = _nouveau_fichier(".wav")
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "1", str(chemin), str(wav)],
                           check=False)
            try:
                _sons[chemin] = _lire_wav(wav) * volume
            except Exception:
                _sons[chemin] = np.zeros(0, dtype=np.float32)
            wav.unlink(missing_ok=True)
        if len(_sons[chemin]):
            _haut_parleur.ajouter(_sons[chemin])
            return
    subprocess.Popen(["afplay", "-v", str(volume), str(chemin)])


def bip() -> None:
    """Petit son non bloquant qui signale « je t'écoute »."""
    _jouer_son(_TINK)


def bip_fin() -> None:
    """Petit son plus doux : « je n'écoute plus, je repasse en veille »."""
    _jouer_son(_POP, 0.5)
