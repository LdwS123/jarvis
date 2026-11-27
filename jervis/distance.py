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
puis fait comme un message écrit, marqué « (message vocal) ». Le son ne quitte pas le Mac. Tu lui as parlé : il te
répond par un vocal, dit avec sa voix (IMESSAGE_VOCAL). Ses vocaux portent son nom de fichier (SON_PREFIXE) et leur
taille est notée : ils ne sont jamais transcrits comme une demande de toi.

Il répond dans le fil exact où tu as écrit (son identifiant dans la base, `chat id` pour Messages) : ton numéro et
ton adresse iCloud ne se mélangent pas en deux conversations.

Il faut l'Accès complet au disque pour « Jervis » (comme pour les codes reçus par SMS).

Par WhatsApp (BoiteWhatsApp) : la même chose dans ta discussion « Message à moi-même ». Une seule bulle par message
(dans Messages, le Mac envoie et reçoit le même iMessage : chaque bulle s'affiche deux fois). Steve lit la base de
WhatsApp (ChatStorage.sqlite, en lecture seule) et répond par WhatsApp lui-même, comme envoyer_whatsapp : écrit, ou
en vrai vocal si tu lui as parlé.
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
SON_PREFIXE = "jervis-vocal-"  # le nom de ses vocaux : jamais relus comme un des tiens
VOCAUX_GARDES_S = 86400       # ses vocaux envoyés : effacés du Mac au bout d'un jour

# Ses vocaux envoyés : (taille du fichier, quand). La copie qui revient dans ton fil a la même taille.
_sons_envoyes: deque[tuple[int, float]] = deque(maxlen=50)

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


def noter_son(chemin: Path) -> None:
    taille = chemin.stat().st_size if chemin.exists() else 0
    _sons_envoyes.append((taille, time.monotonic()))


def son_de_lui(chemin: Path, taille: int) -> bool:
    """Un vocal qu'il t'a envoyé lui-même (son nom, ou la taille d'un de ses vocaux récents)."""
    if chemin.name.startswith(SON_PREFIXE):
        return True
    maintenant = time.monotonic()
    return bool(taille) and any(taille == t and maintenant - quand < ENVOYES_S for t, quand in _sons_envoyes)


def dossier_vocaux() -> Path:
    """Où il range ses vocaux avant de les envoyer : là où Messages peut les prendre (ses pièces jointes), sinon
    dans Images. Ceux de plus d'un jour sont effacés."""
    reglage = config.IMESSAGE_VOCAUX_DOSSIER.strip()
    for dossier in ([Path(os.path.expanduser(reglage))] if reglage else
                    [Path.home() / "Library/Messages/Attachments/Jervis", Path.home() / "Pictures/Jervis"]):
        try:
            dossier.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        for vieux in dossier.glob(SON_PREFIXE + "*"):
            try:
                if time.time() - vieux.stat().st_mtime > VOCAUX_GARDES_S:
                    vieux.unlink()
            except OSError:
                pass
        return dossier
    return Path(tempfile.gettempdir())


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
    texte = re.sub(r"\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~", lambda m: m[1] or m[2] or m[3], texte)
    texte = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", texte)  # *en italique*
    texte = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", texte)
    texte = re.sub(r"^#{1,6}\s*", "", texte, flags=re.MULTILINE)
    texte = re.sub(r"^\s*[-*]\s+", "• ", texte, flags=re.MULTILINE)
    texte = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 : \2", texte)
    texte = texte.strip()
    return texte if len(texte) <= TEXTE_MAX else texte[:TEXTE_MAX - 1] + "…"


_OUI = re.compile(r"^\W*(oui|ouais|ouep|yes|yep|ok|okay|vas[- ]y|go|d'accord|dac|confirme|bien sûr|carrément|"
                  r"fais[- ]le|valide|c'est bon|c['’]est fait|c['’]est réglé|done|👍)(?!\w)", re.IGNORECASE)
_NON = re.compile(r"^\W*(non|nan|no|nope|stop|annule|laisse|surtout pas|pas maintenant|pas encore|attends|👎)(?!\w)", re.IGNORECASE)


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


def _donnees(reglage: str) -> set[str]:
    """« auto » : rien de donné (ses adresses sont lues dans la base) ; sinon la liste « +336…, moi@icloud.com »."""
    reglage = reglage.strip()
    return set() if reglage.lower() in {"auto", "oui", "on", "1", ""} else {
        c for a in reglage.split(",") if (c := cle(a))}


def adresses_a_moi(lien: sqlite3.Connection, donnees: set[str] | None = None) -> set[str]:
    """Tes adresses (celles où tu reçois tes messages) : données dans config.IMESSAGE, ou lues dans la base."""
    donnees = _donnees(config.IMESSAGE) if donnees is None else donnees
    a_moi = set(donnees)
    if not donnees:
        for requete in ("SELECT DISTINCT destination_caller_id FROM message WHERE is_from_me = 1",
                        "SELECT DISTINCT last_addressed_handle FROM chat"):
            try:
                a_moi |= {c for (a,) in lien.execute(requete) if (c := cle(a))}
            except sqlite3.Error:
                pass  # une colonne absente de cette version de macOS : l'autre suffit
    return a_moi


class Boite:
    """Ta conversation avec toi-même dans Messages, vue depuis le Mac. `envoyer(adresse, texte)` fait partir
    un iMessage (AppleScript dans Steve, un faux dans les tests)."""

    origine = "iMessage"   # d'où vient la demande, pour Cerveau.demander

    def __init__(self, envoyer: Callable[[str, str], Awaitable[tuple[int, str]]], base: Path | None = None,
                 adresses: str | None = None, accord: Callable[[str], bool | None] = accord_simple,
                 journal: Callable[[str], None] = print, suspect: Callable[[], bool] | None = None,
                 transcrire: Callable[[Path], str] = transcrire_vocal,
                 envoyer_son: Callable[[str, Path], Awaitable[tuple[int, str]]] | None = None,
                 dire: Callable[[str, Path], Awaitable[bool]] | None = None) -> None:
        self._envoyer = envoyer
        self._envoyer_son = envoyer_son  # (adresse, fichier) : son vocal part (AppleScript dans Steve)
        self._dire = dire                # (texte, fichier) : sa réponse dite dans un fichier (tts.vocal_en_fichier)
        self._transcrire = transcrire
        self._suspect = suspect or (lambda: False)
        self._base = base or comptes.CHAT_DB
        # « auto » : ses adresses sont lues dans la base ; sinon, la liste que tu as donnée (« +336…, moi@icloud.com »).
        self._donnees = _donnees(config.IMESSAGE if adresses is None else adresses)
        self._accord = accord
        self._journal = journal
        self._dernier: int | None = None     # le dernier message déjà vu (ROWID) : rien d'avant le démarrage
        self._a_moi: set[str] = set(self._donnees)
        self._fils: list[tuple[int, str]] = []   # (ROWID du fil, son adresse) : tes conversations avec toi-même
        self._fils_lus = 0.0
        self._connus: set[int] = set()        # tous les fils de la base à la dernière lecture (les tiens et les autres)
        self.adresse = ""                     # où répondre : le fil de ton dernier message
        self.fil = ""                         # ce fil-là pour Messages (« iMessage;-;+336… ») ; "" s'il ne le dit pas
        self._guids: dict[int, str] = {}      # ROWID du fil → son identifiant pour Messages
        self._vus: deque[tuple[str, float]] = deque(maxlen=20)
        self._question: asyncio.Future | None = None
        self._une_question = asyncio.Lock()   # une question à la fois : deux à la suite, jamais mêlées
        # (ton texte, arrivé pendant qu'il lisait une page ou un mail ?) : alors il te demande si c'est bien toi
        self.arrivees: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self.erreur = ""                      # pourquoi il ne lit pas (l'accès au disque…) ; "" s'il lit
        self.vu_a = 0.0                       # quand tu lui as écrit pour la dernière fois (time.monotonic)
        self._avis: list[str] = []            # ce qu'il doit te dire sans que tu aies rien demandé (un vocal perdu…)

    # --- lire la base --------------------------------------------------------------------------------------

    def _ouvrir(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self._base}?mode=ro", uri=True, timeout=3)

    def _trouver_fils(self, lien: sqlite3.Connection) -> None:
        """Tes adresses (celles où tu reçois tes messages) et tes conversations avec toi-même."""
        a_moi = adresses_a_moi(lien, self._donnees)
        try:
            fils = lien.execute("SELECT ROWID, chat_identifier, COALESCE(style, 45), COALESCE(guid, '') "
                                "FROM chat").fetchall()
        except sqlite3.Error:
            try:
                fils = [(r, a, s, "") for r, a, s in lien.execute(
                    "SELECT ROWID, chat_identifier, COALESCE(style, 45) FROM chat")]
            except sqlite3.Error:
                fils = [(r, a, 45, "") for r, a in lien.execute("SELECT ROWID, chat_identifier FROM chat")]
        self._a_moi = a_moi
        self._fils = [(r, a) for r, a, style, _ in fils if style != 43 and cle(a) in a_moi]  # 43 = un groupe
        self._guids = {r: g for r, _, _, g in fils if g}
        self._connus = {r for r, _, _, _ in fils}
        self._fils_lus = time.monotonic()

    def _fil_nouveau(self, lien: sqlite3.Connection, apres: int, jusqua: int) -> bool:
        """Un message est arrivé dans un fil qu'il n'a jamais vu (ta conversation avec toi-même effacée puis
        recommencée, ou écrite à une autre de tes adresses) : sans relire les fils tout de suite, ce message-là
        serait perdu."""
        try:
            fils = {r for (r,) in lien.execute("SELECT DISTINCT chat_id FROM chat_message_join "
                                               "WHERE message_id > ? AND message_id <= ?", (apres, jusqua))}
        except sqlite3.Error:
            return False
        return bool(fils - self._connus)

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
            if (not self._fils or time.monotonic() - self._fils_lus > ADRESSES_S
                    or dernier > self._dernier and self._fil_nouveau(lien, self._dernier, dernier)):
                deja = {r for r, _ in self._fils}
                self._trouver_fils(lien)
                if deja and (neufs := [a for r, a in self._fils if r not in deja]):
                    self._journal(f"   📱 Nouvelle conversation avec toi-même vue ({len(neufs)}) : je la lis aussi.")
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
        for texte, corps, date, de_moi, qui, fil, numero, rang in lignes:
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
            self.adresse, self.fil = fil, self._guids.get(rang, "")
            self.vu_a = time.monotonic()
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
        return [(*l[:5], fils[l[5]], l[6], l[5]) for l in lignes]

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
                if f and est_un_son(f, mime, uti) and not son_de_lui(Path(f), taille)}  # ses vocaux : jamais

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
        dit = annonce = False
        while True:
            for texte in await asyncio.to_thread(self.lire):
                # Ce texte vient de passer par l'un de ses outils (un script, une frappe) : il vient de lui.
                de_lui = permissions.vu_dans_ses_outils(texte)
                if self._question is not None and not self._question.done():
                    # Sa réponse (pendant ce temps, rien n'écrit dans Messages), et si elle arrive pendant une lecture.
                    self._question.set_result((texte, de_lui, self._suspect()))
                else:  # il lisait une page ou un mail, ou c'est l'un de ses textes : il te demande si c'est bien toi
                    self.arrivees.put_nowait((texte, de_lui or self._suspect()))
            while self._avis:
                await self.envoyer(self._avis.pop(0))
            if self.erreur and not dit:
                self._journal(f"   📱 Je ne lis pas tes {self.origine} : il me faut l'Accès complet au disque "
                              f"(Réglages → Confidentialité et sécurité). ({self.erreur})")
            elif not self.erreur and not annonce:  # une fois : ce qu'il lit, pour savoir où lui écrire
                annonce = True
                self._journal(self._annonce())
            dit = bool(self.erreur)
            await asyncio.sleep(60 if self.erreur else config.IMESSAGE_RELEVE_S)

    def _annonce(self) -> str:
        return (f"   📱 iMessage : je lis ta conversation avec toi-même ({len(self._fils)} fil(s))." if self._fils else
                "   📱 iMessage : je ne trouve pas encore ta conversation avec toi-même. Envoie-toi un message (à ton "
                "numéro ou à ton adresse iCloud) : je le lirai.")

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

    def _un_fil(self) -> None:
        """Pas encore de message de toi depuis son démarrage : il répond dans ta conversation avec toi-même."""
        if not self.adresse and self._fils:
            self.adresse, self.fil = self._fils[0][1], self._guids.get(self._fils[0][0], "")

    async def raconter(self, texte: str) -> bool:
        """Te raconter quelque chose sur l'iPhone (ce que le répondeur a fait), même si tu ne lui as pas encore
        écrit depuis son démarrage : dans ta conversation avec toi-même."""
        self._un_fil()
        return await self.envoyer(texte)

    async def raconter_vocal(self, texte: str) -> bool:
        """Comme raconter, mais de vive voix (le bilan du soir) : un vocal dit avec sa voix, le texte s'il ne part pas."""
        self._un_fil()
        return await self.envoyer_vocal(texte)

    async def envoyer_vocal(self, texte: str) -> bool:
        """Sa réponse en vocal (tu lui as parlé) : dite avec sa voix dans un fichier, qui part dans ton fil.
        Un lien dedans : le texte suit (un lien ne se dit pas). Sans voix, ou si le vocal ne part pas : le texte."""
        if not (self.adresse and self._envoyer_son and self._dire):
            return await self.envoyer(texte)
        son = dossier_vocaux() / f"{SON_PREFIXE}{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}.m4a"
        try:
            pret = await self._dire(en_clair(texte), son)
        except Exception as e:
            self._journal(f"   📱 Vocal pas fabriqué ({type(e).__name__}: {e}) : je réponds par écrit.")
            pret = False
        if pret:
            noter_son(son)
            code, sortie = await self._envoyer_son(self.adresse, son)
            if not code:
                if re.search(r"https?://", texte):
                    await self.envoyer(texte)
                return True
            self._journal(f"   📱 Vocal pas parti ({sortie[:150]}) : je réponds par écrit.")
        return await self.envoyer(texte)

    async def demander(self, question: str, delai: float | None = None) -> bool:
        """Une question oui/non par iMessage : ton prochain message y répond. Pas de réponse à temps = non.
        Autre chose qu'un oui ou un non : c'est non, et ton message est lu ensuite comme une nouvelle demande.
        Deux questions en même temps (Steve et une tâche de fond) : la seconde attend que la première ait sa réponse.
        Tu ne lui as encore rien écrit depuis son démarrage : elle part dans ta conversation avec toi-même."""
        async with self._une_question:
            if not self.adresse and getattr(self, "_fils", None):
                self.adresse, self.fil = self._fils[0][1], self._guids.get(self._fils[0][0], "")
            futur = asyncio.get_running_loop().create_future()
            self._question = futur
            # D'ici ta réponse, aucun de ses agents ne lance ce qui pourrait écrire dans Messages : ton « oui » est
            # le tien.
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


# --- Par WhatsApp : ta discussion « Message à moi-même » ---------------------------------------------------------

WHATSAPP_GROUPE = Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
WHATSAPP_TEXTES = (0, 7)     # un message écrit, un message avec un lien
WHATSAPP_SON = 3             # un message vocal (ou un son)
ATTENTE_SON_WHATSAPP_S = 45  # le son d'un vocal que WhatsApp n'a pas encore téléchargé sur le Mac : attendu 45 s
SES_VOCAUX_S = 30            # sa bulle de vocal apparaît dans la base jusqu'à 30 s après l'envoi


_MOTS = re.compile(r"\w{2,}")


def whatsapp_a_moi(reglage: str | None = None, groupe: Path | None = None) -> set[str]:
    """Tes identifiants WhatsApp (« 336…@s.whatsapp.net », et « …@lid » s'il y en a un) : ton numéro donné dans
    config.WHATSAPP_MOI, sinon lus dans les réglages de WhatsApp (plutil : plistlib ne lit pas ce fichier-là)."""
    reglage = (config.WHATSAPP_MOI if reglage is None else reglage).strip()
    if reglage.lower() not in {"auto", "oui", "on", "1", ""}:
        return {f"{numero[1:]}@s.whatsapp.net"} if (numero := cle(reglage)).startswith("+") else set()
    plist = (groupe or WHATSAPP_GROUPE) / "Library" / "Preferences" / "group.net.whatsapp.WhatsApp.shared.plist"
    a_moi = set()
    for nom in ("OwnJabberID", "lid"):
        try:
            sortie = subprocess.run(["plutil", "-extract", nom, "raw", "-o", "-", str(plist)],
                                    capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if sortie.returncode == 0 and re.fullmatch(r"\d{6,}@(s\.whatsapp\.net|lid)", jid := sortie.stdout.strip()):
            a_moi.add(jid)
    return a_moi


async def _whatsapp_texte(numero: str, texte: str) -> tuple[str, str]:
    from . import vocal
    return await vocal.envoyer_texte(numero, texte)


async def _whatsapp_vocal(numero: str, texte: str) -> tuple[str, str]:
    from . import vocal
    return await vocal.envoyer(numero, texte)


class BoiteWhatsApp(Boite):
    """Ta discussion « Message à moi-même » de WhatsApp, vue depuis le Mac : comme Boite (iMessage), sans les
    bulles doublées. Tout ce qui y est écrit vient de toi (ZISFROMME = 1 : ton iPhone ou ce Mac) ; ses réponses
    commencent par 🤖 et ses vocaux sont notés : il ne se relit jamais. `envoyer_texte(numéro, texte)` et
    `envoyer_vocal(numéro, texte)` répondent (« ENVOYÉ » | « INCERTAIN » | « ÉCHEC », détail) : WhatsApp piloté
    par l'accessibilité dans Steve (vocal.py), des faux dans les tests."""

    origine = "WhatsApp"

    def __init__(self, envoyer_texte: Callable[[str, str], Awaitable[tuple[str, str]]] = _whatsapp_texte,
                 envoyer_vocal: Callable[[str, str], Awaitable[tuple[str, str]]] = _whatsapp_vocal,
                 base: Path | None = None, moi: str | None = None,
                 accord: Callable[[str], bool | None] = accord_simple, journal: Callable[[str], None] = print,
                 suspect: Callable[[], bool] | None = None, transcrire: Callable[[Path], str] = transcrire_vocal) -> None:
        super().__init__(envoyer=self._pas_par_messages, base=base or WHATSAPP_GROUPE / "ChatStorage.sqlite",
                         adresses="auto", accord=accord, journal=journal, suspect=suspect, transcrire=transcrire)
        self._envoyer_texte, self._envoyer_vocal = envoyer_texte, envoyer_vocal
        self._medias = self._base.parent / "Message"   # ZMEDIALOCALPATH (« Media/… ») part de là
        self._reglage = config.WHATSAPP_MOI if moi is None else moi
        self._ids: set[str] = set()                    # tes identifiants WhatsApp
        self._sessions: list[int] = []                 # ta discussion avec toi-même (Z_PK), parfois deux
        self._en_attente: dict[int, tuple[float, float]] = {}  # vocal sans son encore : Z_PK → (vu à, sa date)
        self._ses_vocaux: list[list[float]] = []       # [début, fin (0 tant qu'il l'envoie), bulles encore à sauter]
        self._dits: deque[tuple[set[str], float]] = deque(maxlen=20)  # les mots de ses vocaux, et quand

    @staticmethod
    async def _pas_par_messages(_adresse: str, _texte: str) -> tuple[int, str]:
        return 1, "pas par Messages"

    # --- lire la base --------------------------------------------------------------------------------------

    def _trouver_fils(self, lien: sqlite3.Connection) -> None:
        self._ids = whatsapp_a_moi(self._reglage, self._base.parent)
        self._fils_lus = time.monotonic()
        numeros = sorted(j for j in self._ids if j.endswith("@s.whatsapp.net"))
        self.adresse = "+" + numeros[0].split("@")[0] if numeros else ""
        if not self._ids:
            self._sessions = []
            return
        ids = sorted(self._ids)
        self._sessions = [pk for (pk,) in lien.execute(
            f"SELECT Z_PK FROM ZWACHATSESSION WHERE ZCONTACTJID IN ({','.join('?' * len(ids))})", ids)]

    def lire(self) -> list[str]:
        """Tes nouveaux messages à Steve dans « Message à moi-même » (vide s'il n'y a rien : voir self.erreur)."""
        try:
            lien = self._ouvrir()
        except sqlite3.Error as e:
            self.erreur = str(e)
            return []
        try:
            dernier = lien.execute("SELECT COALESCE(MAX(Z_PK), 0) FROM ZWAMESSAGE").fetchone()[0]
            if self._dernier is None:  # au démarrage : ce qui est déjà là n'est pas une demande
                self._dernier = dernier
            # Sans ta discussion avec toi-même : cherchée toutes les 30 s (tu viens peut-être de la commencer).
            if time.monotonic() - self._fils_lus > (ADRESSES_S if self._sessions else 30) or not self._fils_lus:
                self._trouver_fils(lien)
            if not self._sessions:  # rien n'est perdu : ce que tu y écris d'ici là sera lu une fois trouvée
                self.erreur = ""
                return []
            lignes = []
            if dernier > self._dernier or self._en_attente:
                fils = ",".join("?" * len(self._sessions))
                attente = list(self._en_attente)
                lignes = lien.execute(
                    "SELECT m.Z_PK, COALESCE(m.ZTEXT, ''), COALESCE(m.ZMESSAGETYPE, 0), COALESCE(m.ZMESSAGEDATE, 0), "
                    "COALESCE(i.ZMEDIALOCALPATH, '') FROM ZWAMESSAGE m LEFT JOIN ZWAMEDIAITEM i ON i.Z_PK = m.ZMEDIAITEM "
                    f"WHERE m.ZCHATSESSION IN ({fils}) AND m.ZISFROMME = 1 "
                    f"AND (m.Z_PK > ? AND m.Z_PK <= ? OR m.Z_PK IN ({','.join('?' * len(attente))})) ORDER BY m.Z_PK",
                    (*self._sessions, self._dernier, dernier, *attente)).fetchall()
            self._dernier, self.erreur = max(self._dernier, dernier), ""
        except sqlite3.Error as e:
            self.erreur = str(e)
            return []
        finally:
            lien.close()
        textes = []
        for pk, texte, genre, date, chemin in lignes:
            quand = float(date) + comptes._EPOQUE_APPLE
            if genre == WHATSAPP_SON:
                texte = self._vocal(pk, quand, chemin)
            elif genre in WHATSAPP_TEXTES:
                texte = " ".join(texte.split())
            else:
                continue  # une photo, un fichier, un sticker, un message système : jamais un ordre
            if not texte or vient_de_lui(texte):
                continue
            self.vu_a = time.monotonic()
            textes.append(texte)
        return textes

    def _vocal(self, pk: int, quand: float, chemin: str) -> str:
        """Ton vocal transcrit (une fois) ; le sien, jamais. Son son pas encore sur le Mac : attendu, puis dit."""
        if pk not in self._en_attente and self._de_lui(quand):
            return ""
        fichier = Path(chemin) if Path(chemin).is_absolute() else self._medias / chemin
        if not chemin or not fichier.is_file() or not fichier.stat().st_size:
            vu = self._en_attente.setdefault(pk, (time.monotonic(), quand))[0]
            if time.monotonic() - vu > ATTENTE_SON_WHATSAPP_S:
                del self._en_attente[pk]
                self._journal("   📱 Vocal WhatsApp : son pas arrivé sur le Mac.")
                self._avis.append("Ton vocal n'est pas arrivé sur le Mac (WhatsApp ne l'a pas téléchargé) : "
                                  "renvoie-le, ou écris-le-moi.")
            return ""
        self._en_attente.pop(pk, None)
        try:
            texte = self._transcrire(fichier)
        except Exception as e:  # un son illisible : il le dit, sans tomber
            self._journal(f"   📱 Message vocal pas compris ({type(e).__name__}: {e})")
            return ""
        if not texte:
            self._journal("   📱 Message vocal : je n'ai rien entendu.")
            return ""
        if self._redit(texte):
            self._journal("   📱 Vocal WhatsApp : c'est l'un des siens (mêmes mots), pas une demande de toi.")
            return ""
        return VOCAL + texte

    def _redit(self, texte: str) -> bool:
        """Deuxième garde, par le contenu : une bulle qui redit l'un de ses vocaux (presque tous ses mots), arrivée
        en retard ou datée hors de sa fenêtre, reste la sienne. Jamais relue comme un ordre de toi."""
        mots = set(_MOTS.findall(texte.casefold()))
        maintenant = time.time()
        return len(mots) >= 3 and any(maintenant - quand < ENVOYES_S and len(mots & dits) >= 0.7 * len(mots)
                                      for dits, quand in self._dits)

    def _de_lui(self, quand: float) -> bool:
        """Une bulle de vocal datée de pendant qu'il en envoyait un (ou juste après) : la sienne, une par envoi."""
        maintenant = time.time()
        self._ses_vocaux[:] = [v for v in self._ses_vocaux if v[2] and (not v[1] or maintenant - v[1] < ENVOYES_S)]
        for envoi in self._ses_vocaux:
            debut, fin, _ = envoi
            if debut - 2 <= quand <= (fin or maintenant) + SES_VOCAUX_S:
                envoi[2] -= 1
                return True
        return False

    def _annonce(self) -> str:
        if not self._ids:
            return ("   📱 WhatsApp : je ne trouve pas ton numéro dans WhatsApp. Donne-le-moi "
                    "(JERVIS_WHATSAPP_MOI=+33…) pour lire ta discussion « Message à moi-même ».")
        return ("   📱 WhatsApp : je lis ta discussion « Message à moi-même »." if self._sessions else
                "   📱 WhatsApp : je ne trouve pas encore ta discussion « Message à moi-même ». Écris-toi un message "
                "dans WhatsApp (ton propre nom, « (vous) ») : je le lirai.")

    # --- répondre ----------------------------------------------------------------------------------------

    async def envoyer(self, texte: str, debut: str = MARQUE) -> bool:
        if not self.adresse:
            return False
        message = debut + en_clair(texte)
        noter_envoi(message)
        statut, detail = await self._envoyer_texte(self.adresse, message)  # (un envoi WhatsApp à la fois : vocal._tour)
        if statut == "ÉCHEC":
            self._journal(f"   📱 Réponse WhatsApp pas partie : {detail[:150]}")
        return statut != "ÉCHEC"   # « INCERTAIN » : peut-être partie, jamais renvoyée en double

    async def raconter(self, texte: str) -> bool:
        return await self.envoyer(texte)

    async def envoyer_vocal(self, texte: str) -> bool:
        """Sa réponse en vrai vocal WhatsApp (tu lui as parlé), dite avec sa voix. Un lien dedans : le texte suit.
        Si le vocal ne part pas (trop long, un appel en cours…) : le texte."""
        if not self.adresse:
            return False
        envoi = [time.time(), 0.0, 1]
        self._ses_vocaux.append(envoi)  # avant l'envoi : sa bulle peut être lue pendant qu'il l'envoie
        self._dits.append((set(_MOTS.findall(en_clair(texte).casefold())), time.time()))
        try:
            statut, detail = await self._envoyer_vocal(self.adresse, en_clair(texte))
        except Exception as e:
            statut, detail = "ÉCHEC", f"{type(e).__name__}: {e}"
        finally:
            envoi[1] = time.time()
        if statut == "ÉCHEC":
            envoi[2] = 0
            self._journal(f"   📱 Vocal WhatsApp pas parti ({detail[:150]}) : je réponds par écrit.")
            return await self.envoyer(texte)
        if re.search(r"https?://", texte):
            await self.envoyer(texte)
        return True


def la_derniere(boites: list[Boite]) -> Boite:
    """Là où tu lui as écrit en dernier ; si tu ne lui as encore rien écrit, WhatsApp (une bulle par message)."""
    return max(boites, key=lambda b: (b.vu_a, isinstance(b, BoiteWhatsApp)))


async def servir(boite: Boite, demander: Callable[..., Awaitable[str]], relire: Callable[[], None] | None = None,
                 journal: Callable[[str], None] = print) -> None:
    """Ta demande arrive, il la fait (`demander` : Cerveau.demander, venu d'iMessage ou de WhatsApp), et te répond
    dans le fil.
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
                                                 confirmer_ecrit=boite.demander, origine=boite.origine))
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
        if texte.startswith(VOCAL) and config.IMESSAGE_VOCAL.lower() not in {"non", "off", "0"}:
            await boite.envoyer_vocal(reponse)  # tu lui as parlé : il te répond de vive voix
        else:
            await boite.envoyer(reponse)
