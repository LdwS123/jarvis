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
Un message qui arrive pendant qu'il lit une page ou un mail (ou juste après) a pu être écrit par lui, sous la
dictée de ce texte, comme un message qui reprend un texte passé par ses outils : il te demande d'abord si c'est bien
toi (`suspect`, Cerveau.lu_dehors). Et pendant qu'une question ❓ attend ta réponse, rien de ce qui pourrait écrire
dans Messages ne tourne (permissions.QuestionEnCours) : ton « oui » ne peut venir que de toi.

Un message vocal (le micro dans Messages) : transcrit sur le Mac, comme ce que tu lui dis (Whisper, dans ta langue),
puis fait comme un message écrit, marqué « (message vocal) ». Le son ne quitte pas le Mac.

Il faut l'Accès complet au disque pour « Jervis » (comme pour les codes reçus par SMS).
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from . import comptes, config, permissions

MARQUE = "🤖 "              # le début de chacune de ses réponses
QUESTION = "❓ "            # ses questions oui/non
TEXTE_MAX = 4000            # un message plus long : coupé (Messages en accepte plus, ton écran de montre non)
ADRESSES_S = 300            # ses adresses à toi, relues dans la base toutes les 5 minutes
DOUBLE_S = 5                # le même texte deux fois en 5 s : les deux copies d'un message à soi-même
ENVOYES_S = 900             # ce qu'il a envoyé lui-même pendant 15 min : jamais relu comme un ordre
VOCAL = "(message vocal) "   # le début d'une demande dictée en message vocal
ATTENTE_SON_S = 15          # le fichier d'un vocal pas encore téléchargé : attendu jusqu'à 15 s
SONS = (".caf", ".m4a", ".amr", ".aac", ".mp3", ".wav", ".opus")

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
    """Une adresse sous une seule forme, pour la comparer en entier : un mail en minuscules, un numéro au format
    international (« +33 6 12 34 56 78 », « 0612345678 », « 0033612345678 » et « tel:+33612345678 » donnent
    « +33612345678 »). Jamais une fin de numéro : un +44 6 12 34 56 78 n'est pas toi."""
    adresse = (adresse or "").strip().removeprefix("tel:").removeprefix("mailto:")
    if re.match(r"^[EePp]:", adresse):  # « E:moi@icloud.com » (le compte, dans la base)
        adresse = adresse[2:]
    if "@" in adresse:
        return adresse.casefold()
    chiffres = re.sub(r"\D", "", adresse)
    if adresse.startswith("+"):
        numero = "+" + chiffres
    elif chiffres.startswith("00"):
        numero = "+" + chiffres[2:]
    elif len(chiffres) == 10 and chiffres.startswith("0"):  # un numéro français écrit sans indicatif
        numero = "+33" + chiffres[1:]
    else:
        numero = chiffres
    return numero if len(chiffres) >= 6 else ""


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


def est_un_son(nom: str, mime: str | None = None, uti: str | None = None) -> bool:
    """Une pièce jointe qui est un son (le message vocal de Messages est un .caf)."""
    return ((mime or "").startswith("audio/") or "audio" in (uti or "").casefold()
            or nom.casefold().endswith(SONS))


def transcrire_vocal(chemin: Path) -> str:
    """Le message vocal en texte, sur le Mac : afconvert (livré avec macOS) le met en WAV 16 kHz mono, puis
    Whisper le transcrit dans la langue où tu as parlé. Au-delà de VOCAL_MAX_S secondes : coupé."""
    import wave

    import numpy as np

    from . import stt
    with tempfile.TemporaryDirectory() as dossier:
        wav = Path(dossier) / "vocal.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{config.SAMPLE_RATE}", "-c", "1", str(chemin),
                        str(wav)], check=True, capture_output=True, timeout=60)
        with wave.open(str(wav), "rb") as f:
            audio = np.frombuffer(f.readframes(int(config.VOCAL_MAX_S * config.SAMPLE_RATE)), dtype=np.int16)
    morceaux = []
    pas = 30 * config.SAMPLE_RATE  # Whisper lit 30 s à la fois
    for debut in range(0, len(audio), pas):
        texte, _ = stt.transcrire_morceau(audio[debut:debut + pas])
        morceaux.append(texte)
    return " ".join(t for t in morceaux if t).strip()


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
                 journal: Callable[[str], None] = print, suspect: Callable[[], bool] | None = None,
                 transcrire: Callable[[Path], str] = transcrire_vocal) -> None:
        self._envoyer = envoyer
        self._transcrire = transcrire
        self._suspect = suspect or (lambda: False)
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
        # (ton texte, arrivé pendant qu'il lisait une page ou un mail ?) : alors il te demande si c'est bien toi
        self.arrivees: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
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
            sons = self._sons(lien, [l[6] for l in lignes if not comptes.texte_message(l[0], l[1]).strip(" ￼")])
            self._dernier, self.erreur = dernier, ""
        except sqlite3.Error as e:
            self.erreur = str(e)
            return []
        finally:
            lien.close()
        textes = []
        for texte, corps, date, de_moi, qui, fil, numero in lignes:
            if not de_moi and cle(qui) not in self._a_moi:
                continue  # quelqu'un d'autre dans le fil : jamais un ordre
            quand = date / 1e9 if date > 1e11 else float(date)  # en nanosecondes depuis macOS 10.13
            texte = " ".join(comptes.texte_message(texte, corps).replace("￼", " ").split())
            if not texte and numero in sons:
                texte = self._ecouter(*sons[numero], quand)
            if not texte or vient_de_lui(texte):
                continue
            if any(t == texte and abs(quand - q) < DOUBLE_S for t, q in self._vus):
                continue  # la copie « reçue » du message que ton iPhone vient d'envoyer
            self._vus.append((texte, quand))
            self.adresse = fil
            textes.append(texte)
        return textes

    def _nouveaux(self, lien: sqlite3.Connection, apres: int, jusqua: int) -> list[tuple]:
        fils = {r: a for r, a in self._fils}
        marques = ",".join("?" * len(fils))
        base = ("SELECT m.text, m.attributedBody, m.date, m.is_from_me, COALESCE(h.id, ''), j.chat_id, m.ROWID "
                "FROM message m "
                "JOIN chat_message_join j ON j.message_id = m.ROWID LEFT JOIN handle h ON m.handle_id = h.ROWID "
                f"WHERE j.chat_id IN ({marques}) AND m.ROWID > ? AND m.ROWID <= ?")
        try:  # ni les réactions (« A aimé… »), ni les changements de nom ou de photo du fil
            lignes = lien.execute(base + " AND COALESCE(m.associated_message_type, 0) = 0 "
                                         "AND COALESCE(m.item_type, 0) = 0 ORDER BY m.ROWID",
                                  (*fils, apres, jusqua)).fetchall()
        except sqlite3.Error:
            lignes = lien.execute(base + " ORDER BY m.ROWID", (*fils, apres, jusqua)).fetchall()
        return [(*l[:5], fils[l[5]], l[6]) for l in lignes]

    def _sons(self, lien: sqlite3.Connection, messages: list[int]) -> dict[int, tuple[Path, int]]:
        """{message : (le fichier son, sa taille)} pour les messages sans texte qui portent un son."""
        if not messages:
            return {}
        try:
            lignes = lien.execute(
                "SELECT j.message_id, a.filename, a.mime_type, a.uti, COALESCE(a.total_bytes, 0) FROM attachment a "
                "JOIN message_attachment_join j ON j.attachment_id = a.ROWID "
                f"WHERE j.message_id IN ({','.join('?' * len(messages))})", messages).fetchall()
        except sqlite3.Error:
            return {}  # une base sans pièces jointes (ou d'une autre forme) : pas de vocal
        return {m: (Path(os.path.expanduser(f)), taille) for m, f, mime, uti, taille in lignes
                if f and est_un_son(f, mime, uti)}

    def _ecouter(self, chemin: Path, taille: int, quand: float) -> str:
        """Le message vocal transcrit, une seule fois (la copie reçue du même son, quelques secondes après, est
        sautée avant d'être transcrite)."""
        cle_son = f"🎤{taille}"
        if taille and any(t == cle_son and abs(quand - q) < DOUBLE_S for t, q in self._vus):
            return ""
        self._vus.append((cle_son, quand))
        for _ in range(int(ATTENTE_SON_S * 2)):  # le son arrive parfois un peu après le message
            if chemin.exists():
                break
            time.sleep(0.5)
        try:
            texte = self._transcrire(chemin)
        except Exception as e:  # un son illisible (pas encore téléchargé, format inconnu) : il le dit, sans tomber
            self._journal(f"   📱 Message vocal pas compris ({type(e).__name__}: {e})")
            return ""
        if not texte:
            self._journal("   📱 Message vocal : je n'ai rien entendu.")
            return ""
        return VOCAL + texte

    async def relever(self) -> None:
        """Toutes les IMESSAGE_RELEVE_S secondes : tes nouveaux messages vont dans la file (ou répondent à sa
        question, s'il en attend une). Sans accès à la base, il le dit une fois et réessaie chaque minute."""
        dit = False
        while True:
            for texte in await asyncio.to_thread(self.lire):
                # Ce texte vient de passer par l'un de ses outils (un script, une frappe) : il vient de lui.
                de_lui = permissions.vu_dans_ses_outils(texte)
                if self._question is not None and not self._question.done():
                    # Sa réponse (pendant ce temps, rien n'écrit dans Messages), et si elle arrive pendant une lecture.
                    self._question.set_result((texte, de_lui, self._suspect()))
                else:  # il lisait une page ou un mail, ou c'est l'un de ses textes : il te demande si c'est bien toi
                    self.arrivees.put_nowait((texte, de_lui or self._suspect()))
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
        # D'ici ta réponse, aucun de ses agents ne lance ce qui pourrait écrire dans Messages : ton « oui » est le tien.
        with permissions.QuestionEnCours():
            try:
                if not await self.envoyer(f"{question} (réponds oui ou non)", QUESTION):
                    return False
                texte, de_lui, suspect = await asyncio.wait_for(asyncio.shield(futur),
                                                                delai or config.QUESTION_IMESSAGE_S)
            except asyncio.TimeoutError:
                await self.envoyer("Pas de réponse : je n'ai rien fait.")
                return False
            finally:
                self._question = None
        if de_lui:  # la réponse reprend un texte passé par ses outils : elle vient de lui, ce n'est pas ton accord
            self._journal(f"   📱 Réponse écartée (elle reprend un texte de ses outils) : {texte[:120]}")
            return False
        accord = self._accord(texte)
        if accord is None:  # lu ensuite comme une demande : avec le même doute que s'il était arrivé seul
            self.arrivees.put_nowait((texte, suspect))
        return bool(accord)


async def servir(boite: Boite, demander: Callable[..., Awaitable[str]], relire: Callable[[], None] | None = None,
                 journal: Callable[[str], None] = print) -> None:
    """Ta demande arrive, il la fait (`demander` : Cerveau.demander, venu d'iMessage), et te répond dans le fil.
    Si elle prend du temps, un « je m'en occupe » part d'abord. Quand tu ne lui écris plus (ECRIT_RELECTURE_S),
    `relire` : il relit l'échange pour sa mémoire, comme après le chat du tableau de bord.
    Un message arrivé pendant qu'il lisait une page ou un mail : il te demande d'abord si c'est bien toi."""
    a_relire = False
    while True:
        try:
            texte, suspect = await (asyncio.wait_for(boite.arrivees.get(), config.ECRIT_RELECTURE_S)
                                    if a_relire and relire else boite.arrivees.get())
        except asyncio.TimeoutError:
            a_relire = False
            relire()
            continue
        a_relire = True
        if suspect:  # il lisait une page ou un mail, ou le texte vient de ses outils : il a pu partir de lui
            journal(f"   📱 Message peut-être pas de toi, je te demande : {texte[:120]}")
            court = texte if len(texte) <= 200 else texte[:199] + "…"
            if not await boite.demander(f"Je ne suis pas sûr que ce message vienne de toi (il est arrivé pendant que "
                                        f"je lisais une page ou un mail, ou il reprend l'un de mes textes) : c'est "
                                        f"bien toi qui l'as écrit ? « {court} »"):
                await boite.envoyer("Je l'ignore.")
                continue
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
