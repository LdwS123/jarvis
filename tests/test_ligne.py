"""La ligne directe avec la voix (jervis/ligne.py), sans Internet : le service est remplacé par un faux.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_ligne.py
"""

import asyncio
import os
import threading

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import aiohttp  # noqa: E402

from jervis import config, ligne, tts  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


class Message:
    def __init__(self, type_, data):
        self.type, self.data = type_, data


def binaire(son: bytes) -> Message:
    en_tete = b"Path:audio\r\n"
    return Message(aiohttp.WSMsgType.BINARY, len(en_tete).to_bytes(2, "big") + en_tete + son)


FIN = Message(aiohttp.WSMsgType.TEXT, "X-RequestId:1\r\nPath:turn.end\r\n\r\n{}")


class FauxService:
    """Une connexion au service : `delais` = temps de fabrication de chaque phrase demandée dessus."""

    def __init__(self, nom: str, delais: list[float], morte: bool = False):
        self.nom, self.delais, self.morte = nom, list(delais), morte
        self.closed = False
        self.envoyes: list[str] = []
        self._file: asyncio.Queue = asyncio.Queue()

    async def send_str(self, texte: str) -> None:
        if self.morte or self.closed:
            raise aiohttp.ClientConnectionResetError("Cannot write to closing transport")
        self.envoyes.append(texte)
        if "Path:ssml" in texte:
            asyncio.get_running_loop().create_task(self._fabriquer())

    async def _fabriquer(self) -> None:
        await asyncio.sleep(self.delais.pop(0) if self.delais else 0.01)
        for morceau in (b"ID3-", self.nom.encode(), b"-fin"):
            await self._file.put(binaire(morceau))
        await self._file.put(FIN)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self._file.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def close(self) -> None:
        self.closed = True
        await self._file.put(None)


ouvertes: list[FauxService] = []
a_ouvrir: list[tuple[str, list[float], bool]] = []


async def faux_ouvrir():
    nom, delais, morte = a_ouvrir.pop(0) if a_ouvrir else (f"n{len(ouvertes)}", [0.01], False)
    await asyncio.sleep(0.05)  # l'ouverture d'une connexion
    ws = FauxService(nom, delais, morte)
    ouvertes.append(ws)
    await ws.send_str("Path:speech.config")
    return ligne._Ligne(ws)

ligne._ouvrir = faux_ouvrir
config.VOIX_DIRECTE = True


async def scenarios():
    voix, vitesse = "fr-FR-RemyMultilingualNeural", "+8%"

    # 1. Une ligne ouverte d'avance sert plusieurs phrases de suite, sans nouvelle connexion.
    a_ouvrir[:] = [("a", [0.01, 0.01, 0.01], False), ("b", [0.01], False)]
    ligne.prechauffer()
    await asyncio.sleep(0.2)
    verifier("deux lignes ouvertes d'avance", ligne.ouvertes(), 2)
    sons = [await ligne.dire(t, voix, vitesse) for t in ("Un.", "Deux.", "Trois.")]
    verifier("le son reçu est le son sans les en-têtes", sons[0] in (b"ID3-a-fin", b"ID3-b-fin"), True)
    verifier("trois phrases, toujours la même ligne", len(set(sons)), 1)
    verifier("aucune connexion de plus", len(ouvertes), 2)
    await ligne.dire("<b>", voix, vitesse)
    verifier("le texte est échappé pour le SSML", any("&lt;b&gt;" in e for ws in ouvertes for e in ws.envoyes), True)

    # 2. Doublée : deux demandes en même temps, la plus rapide gagne ; la perdante finit et sa ligne revient.
    for ws in ouvertes:
        ws.delais = [1.0] if ws.nom == "a" else [0.1]
    son = await ligne.dire("Le début.", voix, vitesse, doubler=True)
    verifier("doublée : la réponse la plus rapide gagne", son, b"ID3-b-fin")
    await asyncio.sleep(1.1)
    verifier("doublée : la ligne perdante revient servir", ligne.ouvertes(), 2)

    # 3. La ligne prête était fermée côté service : il redemande sur une neuve, sans erreur.
    for l in ligne._libres:
        l.ws.morte = True
    avant = len(ouvertes)
    son = await ligne.dire("Encore.", voix, vitesse)
    verifier("ligne morte : redemandé sur une ligne neuve", (son.startswith(b"ID3-"), len(ouvertes) > avant), (True, True))

    # 4. Une ligne trop vieille (le service l'aurait fermée) n'est pas utilisée.
    for l in ligne._libres:
        l.depuis -= ligne.AGE_MAXI + 1
    verifier("lignes trop vieilles écartées", ligne.ouvertes(), 0)

    # 5. Trois échecs de suite : la ligne directe se met de côté, et tts.py prend le chemin d'edge-tts.
    async def en_panne():
        raise aiohttp.ClientConnectionError("pas d'Internet")
    ligne._ouvrir = en_panne
    ligne._libres.clear()
    for _ in range(ligne.PANNES_MAXI):
        try:
            await ligne.dire("Rien.", voix, vitesse)
        except Exception:
            pass
    verifier("après trois pannes, ligne directe de côté", ligne.disponible(), False)
    ligne._reprise = 0.0
    ligne._ouvrir = faux_ouvrir

    # 6. tts : la ligne en panne vite → chemin d'edge-tts ; lente → voix macOS sans attendre deux fois.
    import edge_tts
    vrai_communicate = edge_tts.Communicate
    appels = []

    class FauxCommunicate:
        def __init__(self, texte, *a, **k):
            appels.append(texte)

        async def save(self, chemin):
            open(chemin, "wb").write(b"mp3-classique")
    edge_tts.Communicate = FauxCommunicate
    vrai_dire = ligne.dire

    async def dire_casse(*a, **k):
        raise RuntimeError("service changé")
    ligne.dire = dire_casse
    fichier = await tts._synthetiser("Bonjour.")
    verifier("ligne cassée → chemin d'edge-tts", (fichier.read_bytes(), appels), (b"mp3-classique", ["Bonjour."]))

    async def dire_lent(*a, **k):
        await asyncio.sleep(10)
    ligne.dire, tts._DELAI_VOIX = dire_lent, 2.2
    fichier = await tts._synthetiser("Bonsoir.")
    verifier("service trop lent → voix macOS, sans redemander", (fichier, appels), (None, ["Bonjour."]))
    tts._DELAI_VOIX = 6.0

    ligne.dire = vrai_dire
    fichier = await tts._synthetiser("Salut.", en_tete=True)
    verifier("ligne directe → mp3 écrit tel quel", fichier.read_bytes().startswith(b"ID3-"), True)
    config.VOIX_DIRECTE = False
    fichier = await tts._synthetiser("Coucou.")
    verifier("JERVIS_VOIX_DIRECTE=non → chemin d'edge-tts", appels[-1], "Coucou.")
    config.VOIX_DIRECTE = True
    edge_tts.Communicate = vrai_communicate


asyncio.new_event_loop().run_until_complete(scenarios())

# 7. Une nouvelle boucle (il a redémarré après une erreur) : les vieilles lignes sont oubliées.
ligne._libres.append(ligne._Ligne(FauxService("vieille", [])))


async def nouvelle_boucle():
    ligne._ici()
    return len(ligne._libres)
verifier("nouvelle boucle : on repart de zéro", asyncio.new_event_loop().run_until_complete(nouvelle_boucle()), 0)

print(f"\n{'✅' if not echecs else '❌'} ligne directe : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
