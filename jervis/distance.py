"""Steve partout : tu lui écris depuis ton iPhone (ta montre, ou Siri qui dicte), il te répond dans le même fil.

Tu t'envoies un iMessage à toi-même (la conversation avec ton propre numéro ou ton adresse iCloud). Le Mac le
reçoit comme tes autres messages : Steve relit la base de Messages (chat.db, en lecture seule) toutes les deux
secondes, prend ce qui est nouveau dans CETTE conversation-là, fait la demande comme si tu l'avais tapée dans le
chat, et te répond dans le même fil. Rien ne passe par un serveur à nous : c'est iMessage, chiffré de bout en bout,
entre tes appareils.

Qui peut lui donner un ordre ? Seulement toi : la conversation avec toi-même n'a qu'un participant (ton identifiant
Apple), et chaque message reçu doit venir d'une de tes adresses. Un message d'un autre, un groupe, une réaction
(« A aimé… ») ne comptent jamais. Ses propres réponses commencent par 🤖 et ce qu'il vient d'envoyer est noté : il
ne se relit jamais lui-même (pas de boucle, et un texte venu d'une page ne revient pas comme un ordre de toi).

Il faut l'Accès complet au disque pour « Jervis » (comme pour les codes reçus par SMS).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from . import comptes, config

MARQUE = "🤖 "              # le début de chacune de ses réponses
QUESTION = "❓ "            # ses questions oui/non
TEXTE_MAX = 4000            # un message plus long : coupé (Messages en accepte plus, ton écran de montre non)
ADRESSES_S = 300            # ses adresses à toi, relues dans la base toutes les 5 minutes
DOUBLE_S = 5                # le même texte deux fois en 5 s : les deux copies d'un message à soi-même
ENVOYES_S = 900             # ce qu'il a envoyé lui-même pendant 15 min : jamais relu comme un ordre

# Ce qu'il a envoyé par iMessage (ses réponses, et tout envoyer_imessage) : (texte réduit, quand).
_envoyes: deque[tuple[str, float]] = deque(maxlen=200)


def _reduit(texte: str) -> str:
    return " ".join(texte.removeprefix(MARQUE).removeprefix(QUESTION).split()).casefold()


def noter_envoi(texte: str) -> None:
    """Un message qui part du Mac : s'il revient dans ta conversation, ce n'est pas toi qui parles."""
    _envoyes.append((_reduit(texte), time.monotonic()))


def vient_de_lui(texte: str) -> bool:
    if texte.lstrip().startswith((MARQUE.strip(), QUESTION.strip())):
        return True
    t, maintenant = _reduit(texte), time.monotonic()
    return any(t == e and maintenant - quand < ENVOYES_S for e, quand in _envoyes)


def cle(adresse: str | None) -> str:
    """Une adresse réduite pour comparer : un mail en minuscules, un numéro par ses 9 derniers chiffres
    (« +33 6 12 34 56 78 », « 0612345678 » et « tel:+33612345678 » sont le même numéro)."""
    adresse = (adresse or "").strip().removeprefix("tel:").removeprefix("mailto:")
    if re.match(r"^[EePp]:", adresse):  # « E:moi@icloud.com » (le compte, dans la base)
        adresse = adresse[2:]
    if "@" in adresse:
        return adresse.casefold()
    chiffres = re.sub(r"\D", "", adresse)
    return chiffres[-9:] if len(chiffres) >= 6 else ""


def en_clair(texte: str) -> str:
    """Sa réponse écrite, lisible dans une bulle (et par Siri) : sans les ** ni les ` du Markdown."""
    texte = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m[1] or m[2], texte)
    texte = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", texte)
    texte = re.sub(r"^#{1,6}\s*", "", texte, flags=re.MULTILINE)
    texte = re.sub(r"^\s*[-*]\s+", "• ", texte, flags=re.MULTILINE)
    texte = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 : \2", texte)
    texte = texte.strip()
    return texte if len(texte) <= TEXTE_MAX else texte[:TEXTE_MAX - 1] + "…"


_OUI = re.compile(r"^\W*(oui|ouais|ouep|yes|yep|ok|okay|vas[- ]y|go|d'accord|dac|confirme|bien sûr|carrément|"
                  r"fais[- ]le|valide|c'est bon|👍)(?!\w)", re.IGNORECASE)
_NON = re.compile(r"^\W*(non|nan|no|nope|stop|annule|laisse|surtout pas|pas maintenant|attends|👎)(?!\w)", re.IGNORECASE)


def accord_simple(texte: str) -> bool | None:
    """Oui → True, non → False, autre chose → None (__main__ passe sa lecture plus fine, lire_accord)."""
    if _NON.search(texte):
        return False
    return True if _OUI.search(texte) else None


class Boite:
    """Ta conversation avec toi-même dans Messages, vue depuis le Mac. `envoyer(adresse, texte)` fait partir
    un iMessage (AppleScript dans Steve, un faux dans les tests)."""

    def __init__(self, envoyer: Callable[[str, str], Awaitable[tuple[int, str]]], base: Path | None = None,
                 adresses: str | None = None, accord: Callable[[str], bool | None] = accord_simple,
                 journal: Callable[[str], None] = print) -> None:
        self._envoyer = envoyer
        self._base = base or comptes.CHAT_DB
        reglage = (config.IMESSAGE if adresses is None else adresses).strip()
        # « auto » : ses adresses sont lues dans la base ; sinon, la liste que tu as donnée (« +336…, moi@icloud.com »).
        self._donnees = set() if reglage.lower() in {"auto", "oui", "on", "1", ""} else {
            c for a in reglage.split(",") if (c := cle(a))}
        self._accord = accord
        self._journal = journal
        self._dernier: int | None = None     # le dernier message déjà vu (ROWID) : rien d'avant le démarrage
        self._a_moi: set[str] = set(self._donnees)
        self._fils: list[tuple[int, str]] = []   # (ROWID du fil, son adresse) : tes conversations avec toi-même
        self._fils_lus = 0.0
        self.adresse = ""                     # où répondre : le fil de ton dernier message
        self._vus: deque[tuple[str, float]] = deque(maxlen=20)
        self._question: asyncio.Future | None = None
        self.arrivees: asyncio.Queue[str] = asyncio.Queue()
        self.erreur = ""                      # pourquoi il ne lit pas (l'accès au disque…) ; "" s'il lit

    # --- lire la base --------------------------------------------------------------------------------------

    def _ouvrir(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self._base}?mode=ro", uri=True, timeout=3)

    def _trouver_fils(self, lien: sqlite3.Connection) -> None:
        """Tes adresses (celles où tu reçois tes messages) et tes conversations avec toi-même."""
        a_moi = set(self._donnees)
        if not self._donnees:
            for requete in ("SELECT DISTINCT destination_caller_id FROM message WHERE is_from_me = 1",
                            "SELECT DISTINCT last_addressed_handle FROM chat"):
                try:
                    a_moi |= {c for (a,) in lien.execute(requete) if (c := cle(a))}
                except sqlite3.Error:
                    pass  # une colonne absente de cette version de macOS : l'autre suffit
        try:
            fils = lien.execute("SELECT ROWID, chat_identifier, COALESCE(style, 45) FROM chat").fetchall()
        except sqlite3.Error:
            fils = [(r, a, 45) for r, a in lien.execute("SELECT ROWID, chat_identifier FROM chat")]
        self._a_moi = a_moi
        self._fils = [(r, a) for r, a, style in fils if style != 43 and cle(a) in a_moi]  # 43 = un groupe
        self._fils_lus = time.monotonic()

    def lire(self) -> list[str]:
        """Tes nouveaux messages à Steve (vide s'il n'y a rien, ou si la base est fermée : voir self.erreur)."""
        try:
            lien = self._ouvrir()
        except sqlite3.Error as e:
            self.erreur = str(e)
            return []
        try:
            dernier = lien.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0]
            if self._dernier is None:  # au démarrage : ce qui est déjà là n'est pas une demande
                self._dernier = dernier
            if not self._fils or time.monotonic() - self._fils_lus > ADRESSES_S:
                self._trouver_fils(lien)
            if dernier <= self._dernier or not self._fils:
                self._dernier, self.erreur = max(self._dernier, dernier), ""
                return []
            lignes = self._nouveaux(lien, self._dernier, dernier)
            self._dernier, self.erreur = dernier, ""
        except sqlite3.Error as e:
            self.erreur = str(e)
            return []
        finally:
            lien.close()
        textes = []
        for texte, corps, date, de_moi, qui, fil in lignes:
            if not de_moi and cle(qui) not in self._a_moi:
                continue  # quelqu'un d'autre dans le fil : jamais un ordre
            texte = " ".join(comptes.texte_message(texte, corps).replace("￼", " ").split())
            if not texte or vient_de_lui(texte):
                continue
            quand = date / 1e9 if date > 1e11 else float(date)  # en nanosecondes depuis macOS 10.13
            if any(t == texte and abs(quand - q) < DOUBLE_S for t, q in self._vus):
                continue  # la copie « reçue » du message que ton iPhone vient d'envoyer
            self._vus.append((texte, quand))
            self.adresse = fil
            textes.append(texte)
        return textes

    def _nouveaux(self, lien: sqlite3.Connection, apres: int, jusqua: int) -> list[tuple]:
        fils = {r: a for r, a in self._fils}
        marques = ",".join("?" * len(fils))
        base = ("SELECT m.text, m.attributedBody, m.date, m.is_from_me, COALESCE(h.id, ''), j.chat_id FROM message m "
                "JOIN chat_message_join j ON j.message_id = m.ROWID LEFT JOIN handle h ON m.handle_id = h.ROWID "
                f"WHERE j.chat_id IN ({marques}) AND m.ROWID > ? AND m.ROWID <= ?")
        try:  # ni les réactions (« A aimé… »), ni les changements de nom ou de photo du fil
            lignes = lien.execute(base + " AND COALESCE(m.associated_message_type, 0) = 0 "
                                         "AND COALESCE(m.item_type, 0) = 0 ORDER BY m.ROWID",
                                  (*fils, apres, jusqua)).fetchall()
        except sqlite3.Error:
            lignes = lien.execute(base + " ORDER BY m.ROWID", (*fils, apres, jusqua)).fetchall()
        return [(*l[:5], fils[l[5]]) for l in lignes]

    async def relever(self) -> None:
        """Toutes les IMESSAGE_RELEVE_S secondes : tes nouveaux messages vont dans la file (ou répondent à sa
        question, s'il en attend une). Sans accès à la base, il le dit une fois et réessaie chaque minute."""
        dit = False
        while True:
            for texte in await asyncio.to_thread(self.lire):
                if self._question is not None and not self._question.done():
                    self._question.set_result(texte)
                else:
                    self.arrivees.put_nowait(texte)
            if self.erreur and not dit:
                self._journal(f"   📱 Je ne lis pas tes iMessages : il me faut l'Accès complet au disque (Réglages → "
                              f"Confidentialité et sécurité). ({self.erreur})")
            dit = bool(self.erreur)
            await asyncio.sleep(60 if self.erreur else config.IMESSAGE_RELEVE_S)

    # --- répondre ----------------------------------------------------------------------------------------

    async def envoyer(self, texte: str, debut: str = MARQUE) -> bool:
        if not self.adresse:
            return False
        message = debut + en_clair(texte)
        noter_envoi(message)
        code, sortie = await self._envoyer(self.adresse, message)
        if code:
            self._journal(f"   📱 Réponse iMessage pas partie : {sortie[:150]}")
        return not code

    async def demander(self, question: str, delai: float | None = None) -> bool:
        """Une question oui/non par iMessage : ton prochain message y répond. Pas de réponse à temps = non.
        Autre chose qu'un oui ou un non : c'est non, et ton message est lu ensuite comme une nouvelle demande."""
        futur = asyncio.get_running_loop().create_future()
        self._question = futur
        try:
            if not await self.envoyer(f"{question} (réponds oui ou non)", QUESTION):
                return False
            texte = await asyncio.wait_for(asyncio.shield(futur), delai or config.QUESTION_IMESSAGE_S)
        except asyncio.TimeoutError:
            await self.envoyer("Pas de réponse : je n'ai rien fait.")
            return False
        finally:
            self._question = None
        accord = self._accord(texte)
        if accord is None:
            self.arrivees.put_nowait(texte)
        return bool(accord)


async def servir(boite: Boite, demander: Callable[..., Awaitable[str]], relire: Callable[[], None] | None = None,
                 journal: Callable[[str], None] = print) -> None:
    """Ta demande arrive, il la fait (`demander` : Cerveau.demander, venu d'iMessage), et te répond dans le fil.
    Si elle prend du temps, un « je m'en occupe » part d'abord. Quand tu ne lui écris plus (ECRIT_RELECTURE_S),
    `relire` : il relit l'échange pour sa mémoire, comme après le chat du tableau de bord."""
    a_relire = False
    while True:
        try:
            texte = await (asyncio.wait_for(boite.arrivees.get(), config.ECRIT_RELECTURE_S) if a_relire and relire
                           else boite.arrivees.get())
        except asyncio.TimeoutError:
            a_relire = False
            relire()
            continue
        a_relire = True
        journal(f"🧑 Toi (iPhone) : {texte}")
        travail = asyncio.ensure_future(demander(texte, ecrit=lambda _morceau: None,
                                                 confirmer_ecrit=boite.demander, origine="iMessage"))
        try:
            if config.IMESSAGE_PATIENCE_S > 0:
                fini, _ = await asyncio.wait({travail}, timeout=config.IMESSAGE_PATIENCE_S)
                if not fini:
                    await boite.envoyer("Je m'en occupe…")
            reponse = await travail
        except asyncio.CancelledError:
            travail.cancel()
            raise
        except Exception as e:
            reponse = f"⚠️ Je n'ai pas pu le faire ({e!r})."
        reponse = reponse or "OK."
        journal(f"🤖 {config.PRENOM} (iPhone) : " + " ⏎ ".join(l.strip() for l in reponse.splitlines() if l.strip()))
        await boite.envoyer(reponse)
