"""La ligne directe avec la voix Microsoft.

edge-tts ouvre une connexion neuve pour chaque phrase (0,45 s mesuré), puis la referme. Ici la connexion est
ouverte d'avance, pendant que Claude réfléchit, et elle ressert d'une phrase à l'autre. Et comme le service met
entre 1 et 4 s pour une même phrase selon le moment, le premier bout d'une réponse peut être demandé deux fois
en même temps : on garde la demande qui revient la première.

Ce module parle au service avec les briques d'edge-tts (jeton, format des messages). Si une mise à jour
d'edge-tts les déplace, ou si le service change, `disponible()` répond non et tts.py reprend le chemin d'edge-tts.
"""

from __future__ import annotations

import asyncio
import time
from xml.sax.saxutils import escape

from . import config

try:
    import ssl

    import aiohttp
    import certifi
    from edge_tts.communicate import (DRM, SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL, connect_id, date_to_string,
                                      mkssml, remove_incompatible_characters, ssml_headers_plus_data)
    from edge_tts.data_classes import TTSConfig
    _BRIQUES = True
except Exception:  # edge-tts absent ou réorganisé : on s'en passe
    _BRIQUES = False

LIGNES = 2          # lignes ouvertes d'avance : deux, pour la demande doublée du premier bout
AGE_MAXI = 22.0     # le service ferme une ligne inutilisée après 30 à 60 s (mesuré) : avant ça, on la remplace
VEILLE = 120.0      # après une demande, on garde des lignes fraîches pendant ce temps-là
DELAI = 8.0         # au-delà, une demande est abandonnée
PANNES_MAXI = 3     # échecs de suite avant de laisser la ligne directe de côté…
REPOS = 300.0       # … pendant cinq minutes

_REGLAGE = ("X-Timestamp:%s\r\nContent-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n"
            '{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false",'
            '"wordBoundaryEnabled":"false"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n')


class _LigneMorte(Exception):
    """Le service avait fermé cette ligne avant d'envoyer le moindre son : on peut redemander sur une neuve."""


class _Ligne:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.depuis = time.monotonic()  # ouverture, ou fin de la dernière phrase

    @property
    def fraiche(self) -> bool:
        return not self.ws.closed and time.monotonic() - self.depuis < AGE_MAXI


_boucle: asyncio.AbstractEventLoop | None = None
_session = None
_ssl = None
_libres: list[_Ligne] = []           # ouvertes, prêtes à servir
_ouvertures: set[asyncio.Task] = set()
_taches: set[asyncio.Task] = set()   # fermetures et demandes perdantes, gardées en vie jusqu'à leur fin
_veilleur: asyncio.Task | None = None
_jusqua = 0.0                        # fin de la veille
_occupees = 0                        # lignes en train de servir une demande (elles reviendront)
_pas_avant = 0.0                     # une ouverture vient d'échouer : on attend un peu avant de réessayer
_pannes = 0
_reprise = 0.0                       # pas avant cet instant


def disponible() -> bool:
    return _BRIQUES and config.VOIX_DIRECTE and time.monotonic() >= _reprise


def ouvertes() -> int:
    """Combien de lignes sont prêtes en ce moment (pour le tableau de bord)."""
    return sum(1 for ligne in _libres if ligne.fraiche)


def _ici() -> None:
    """Les connexions appartiennent à une boucle asyncio : si elle a changé (il a redémarré), on repart de zéro."""
    global _boucle, _session, _veilleur
    boucle = asyncio.get_running_loop()
    if boucle is not _boucle:
        global _occupees
        _boucle, _session, _veilleur, _occupees = boucle, None, None, 0
        _libres.clear()
        _ouvertures.clear()


def _en_fond(coroutine) -> asyncio.Task:
    tache = asyncio.create_task(coroutine)
    _taches.add(tache)
    tache.add_done_callback(_taches.discard)
    tache.add_done_callback(lambda t: t.cancelled() or t.exception())  # son échec éventuel ne regarde personne
    return tache


async def _ouvrir() -> _Ligne:
    global _session, _ssl
    if _ssl is None:
        _ssl = ssl.create_default_context(cafile=certifi.where())
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(sock_connect=5))
    for essai in (1, 2):
        try:
            ws = await _session.ws_connect(
                f"{WSS_URL}&ConnectionId={connect_id()}&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
                f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
                compress=15, headers=DRM.headers_with_muid(WSS_HEADERS), ssl=_ssl)
            break
        except aiohttp.ClientResponseError as e:
            if e.status != 403 or essai == 2:
                raise
            DRM.handle_client_response_error(e)  # l'horloge du Mac est décalée : on se cale sur celle du service
    await ws.send_str(_REGLAGE % date_to_string())
    return _Ligne(ws)


def _fermer(ligne: _Ligne) -> None:
    _en_fond(ligne.ws.close())


def _rendre(ligne: _Ligne) -> None:
    ligne.depuis = time.monotonic()
    if len(_libres) <= LIGNES:
        _libres.append(ligne)
    else:
        _fermer(ligne)


async def _prendre(neuve: bool = False) -> _Ligne:
    while not neuve:
        while _libres:
            ligne = _libres.pop()  # la plus récente
            if ligne.fraiche:
                return ligne
            _fermer(ligne)
        en_route = {o for o in _ouvertures if not o.done()}
        if not en_route:
            break
        await asyncio.wait(en_route, return_when=asyncio.FIRST_COMPLETED)  # une ligne arrive : on l'attend
    return await _ouvrir()


async def _tour(ligne: _Ligne, texte: str, voix: str, vitesse: str) -> bytes:
    """Demande une phrase sur cette ligne et renvoie son mp3."""
    ssml = mkssml(TTSConfig(voix, vitesse, "+0%", "+0Hz", "SentenceBoundary"),
                  escape(remove_incompatible_characters(texte)))
    try:
        await ligne.ws.send_str(ssml_headers_plus_data(connect_id(), date_to_string(), ssml))
    except (ConnectionError, aiohttp.ClientError) as e:
        raise _LigneMorte() from e
    son = bytearray()
    async for message in ligne.ws:
        if message.type == aiohttp.WSMsgType.BINARY:
            son += message.data[int.from_bytes(message.data[:2], "big") + 2:]  # 2 octets de taille, l'en-tête, le son
        elif message.type == aiohttp.WSMsgType.TEXT:
            if "Path:turn.end" in message.data:
                if not son:
                    raise RuntimeError("le service n'a renvoyé aucun son")
                return bytes(son)
        else:
            break
    if son:
        raise RuntimeError("ligne coupée en plein son")
    raise _LigneMorte()


async def _une_demande(texte: str, voix: str, vitesse: str) -> bytes:
    global _occupees
    _occupees += 1
    try:
        for neuve in (False, True):
            ligne = await _prendre(neuve)
            try:
                son = await _tour(ligne, texte, voix, vitesse)
            except _LigneMorte:
                _fermer(ligne)
                if neuve:
                    raise
                continue  # la ligne ouverte d'avance était déjà fermée côté service : on réessaie sur une neuve
            except BaseException:
                _fermer(ligne)  # coupée en plein tour (abandon compris) : elle ne peut plus resservir
                raise
            _rendre(ligne)
            return son
        raise _LigneMorte()
    finally:
        _occupees -= 1


async def dire(texte: str, voix: str, vitesse: str, doubler: bool = False) -> bytes:
    """Le mp3 de ce texte. `doubler` : demandé deux fois en même temps, on garde la première réponse."""
    global _pannes, _reprise
    _ici()
    courses = [asyncio.create_task(asyncio.wait_for(_une_demande(texte, voix, vitesse), DELAI))
               for _ in range(2 if doubler else 1)]
    erreur: Exception | None = None
    try:
        for arrivee in asyncio.as_completed(courses):
            try:
                son = await arrivee
            except Exception as e:
                erreur = e
                continue
            _pannes = 0
            return son
    except asyncio.CancelledError:
        for course in courses:
            course.cancel()
        raise
    finally:
        for course in courses:  # la demande perdante finit toute seule : sa ligne resservira
            _taches.add(course)
            course.add_done_callback(_taches.discard)
            course.add_done_callback(lambda t: t.cancelled() or t.exception())
    _pannes += 1
    if _pannes >= PANNES_MAXI:
        _pannes, _reprise = 0, time.monotonic() + REPOS
    raise erreur or RuntimeError("aucune réponse")


async def _veiller() -> None:
    """Tant qu'on vient de lui parler, garde des lignes fraîches ; ensuite il les referme."""
    while time.monotonic() < _jusqua and disponible():
        for ligne in [l for l in _libres if not l.fraiche]:
            _libres.remove(ligne)
            _fermer(ligne)
        manquantes = LIGNES - len(_libres) - len(_ouvertures) - _occupees
        for _ in range(manquantes if time.monotonic() >= _pas_avant else 0):
            async def ouvrir() -> None:
                global _pas_avant
                try:
                    _libres.append(await _ouvrir())
                except Exception:  # pas d'Internet : `dire` échouera aussi, et tts.py prendra la voix de secours
                    _pas_avant = time.monotonic() + 15
            ouverture = _en_fond(ouvrir())
            _ouvertures.add(ouverture)
            ouverture.add_done_callback(_ouvertures.discard)
        await asyncio.sleep(2)
    while _libres:
        _fermer(_libres.pop())


def prechauffer() -> None:
    """Ouvre des lignes d'avance et les garde fraîches un moment. À appeler dès qu'il va avoir à parler."""
    global _jusqua, _veilleur
    if not disponible():
        return
    try:
        _ici()
    except RuntimeError:
        return  # appelé hors de la boucle asyncio : rien à préparer
    _jusqua = time.monotonic() + VEILLE
    if _veilleur is None or _veilleur.done():
        _veilleur = _en_fond(_veiller())
