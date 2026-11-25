"""Le chat écrit : tu lui tapes une demande sur le tableau de bord, il te répond par écrit, sans un mot à voix haute.

La page (son propre fil, celui du serveur) dépose tes messages ici ; la boucle de Steve (asyncio) les prend un
par un. Les deux côtés se parlent à travers un verrou et `call_soon_threadsafe` : jamais d'asyncio touché
depuis le fil du serveur. Rien ne sort du Mac : la page et le serveur sont sur 127.0.0.1.

Chaque message porte un numéro `n` et la version `v` de sa dernière modification : la page demande
« ce qui a changé depuis la version V » et met à jour ses bulles (une réponse qui s'écrit mot à mot change
plusieurs fois de version).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Callable

from . import config

TEXTE_MAX = 4000          # au-delà, ce n'est plus une demande tapée (un fichier collé par erreur…)


class Messagerie:
    def __init__(self, garder: int = 200) -> None:
        self._verrou = threading.Lock()
        self._messages: deque[dict[str, Any]] = deque(maxlen=garder)
        self._n = 0
        self._v = 0
        self._boucle: asyncio.AbstractEventLoop | None = None
        self._arrivees: asyncio.Queue[tuple[int, str]] | None = None
        self._questions: dict[int, asyncio.Future] = {}
        # Appelé dans la boucle de Steve dès qu'un message arrive, avant la file : un « stop » tapé le fait taire
        # tout de suite, même s'il est en train de parler ou de finir autre chose. Faux = le message s'arrête là.
        self.urgence: Callable[[str], bool] | None = None

    # --- côté Steve (la boucle asyncio) ---------------------------------------------------------------

    def brancher(self, boucle: asyncio.AbstractEventLoop) -> None:
        """Steve est prêt à lire : à partir de là, la page peut écrire."""
        with self._verrou:
            self._boucle = boucle
            self._arrivees = asyncio.Queue()

    def debrancher(self) -> None:
        with self._verrou:
            self._boucle, self._arrivees = None, None
            questions, self._questions = self._questions, {}
        for futur in questions.values():  # une question en suspens vaut un non
            if not futur.done():
                futur.get_loop().call_soon_threadsafe(lambda f=futur: f.done() or f.set_result(False))

    async def prochain(self) -> str:
        """Le prochain message que tu as tapé (attend s'il n'y en a pas). La page le voit « pris en main »."""
        assert self._arrivees is not None, "appelle brancher() d'abord"
        n, texte = await self._arrivees.get()
        self.fermer(n)
        return texte

    def ajouter(self, qui: str, texte: str = "", fini: bool = True) -> int:
        """Une nouvelle bulle (« toi », « steve », « question », « info »). Renvoie son numéro."""
        with self._verrou:
            self._n += 1
            self._v += 1
            self._messages.append({"n": self._n, "v": self._v, "qui": qui, "texte": texte, "fini": fini,
                                   "quand": time.strftime("%H:%M"), "reponse": None})
            return self._n

    def _modifier(self, n: int, **champs: Any) -> None:
        with self._verrou:
            for m in reversed(self._messages):
                if m["n"] == n:
                    self._v += 1
                    m.update(champs, v=self._v)
                    return

    def completer(self, n: int, morceau: str) -> None:
        """La réponse qui s'écrit : un morceau de plus au bout de la bulle."""
        with self._verrou:
            for m in reversed(self._messages):
                if m["n"] == n:
                    self._v += 1
                    m["texte"] += morceau
                    m["v"] = self._v
                    return

    def fermer(self, n: int, texte: str | None = None) -> None:
        """La réponse est finie (et `texte` la remplace si on le donne : la version propre)."""
        self._modifier(n, fini=True, **({"texte": texte} if texte is not None else {}))

    async def demander(self, question: str, delai: float = config.QUESTION_FOND_S) -> bool:
        """Une question oui/non dans le chat (boutons Oui / Non). Pas de réponse à temps = non."""
        n = self.ajouter("question", question, fini=False)
        futur = asyncio.get_running_loop().create_future()
        with self._verrou:
            self._questions[n] = futur
        try:
            return await asyncio.wait_for(asyncio.shield(futur), delai)
        except asyncio.TimeoutError:
            return False
        finally:
            with self._verrou:
                self._questions.pop(n, None)
            reponse = futur.result() if futur.done() and not futur.cancelled() else None
            self._modifier(n, fini=True, reponse="oui" if reponse else "non")

    # --- côté page (le fil du serveur) ------------------------------------------------------------------

    def branchee(self) -> bool:
        with self._verrou:
            return self._boucle is not None

    def recevoir(self, texte: str) -> bool:
        """Un message tapé sur la page. Faux si Steve n'écoute pas (en train de démarrer, ou mode sans chat)."""
        texte = " ".join(texte.split())[:TEXTE_MAX]
        with self._verrou:
            boucle, arrivees = self._boucle, self._arrivees
        if not texte or boucle is None or arrivees is None:
            return False
        n = self.ajouter("toi", texte, fini=False)  # pas encore lu : il finit peut-être autre chose
        boucle.call_soon_threadsafe(self._arrivee, arrivees, n, texte)
        return True

    def _arrivee(self, arrivees: asyncio.Queue, n: int, texte: str) -> None:
        """Dans la boucle de Steve : l'urgence d'abord (un « stop »), puis la file."""
        try:
            suite = self.urgence is None or self.urgence(texte)
        except Exception:
            suite = True  # une urgence qui plante ne doit pas perdre ton message
        if not suite:
            self.fermer(n)
            self.ajouter("info", "Arrêté.")
            return
        arrivees.put_nowait((n, texte))

    def repondre(self, n: int, oui: bool) -> bool:
        """Un clic sur Oui ou Non. Faux si la question n'attend plus."""
        with self._verrou:
            futur = self._questions.get(n)
        if futur is None or futur.done():
            return False
        futur.get_loop().call_soon_threadsafe(lambda: futur.done() or futur.set_result(oui))
        return True

    def depuis(self, v: int) -> dict[str, Any]:
        """Ce qui a changé depuis la version `v` (tout, si v vaut 0), et la version actuelle."""
        with self._verrou:
            return {"v": self._v, "branche": self._boucle is not None,
                    "messages": [dict(m) for m in self._messages if m["v"] > v]}


MESSAGERIE = Messagerie()
