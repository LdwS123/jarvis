"""Le vocal WhatsApp : un vrai message vocal (la bulle de voix, pas un fichier audio ni du texte), dit avec sa voix
locale (Pocket TTS, voix_locale.py). Tout se fabrique sur le Mac ; seul le vocal part, par WhatsApp, comme le tien.

Comment : il ouvre la discussion du contact dans l'app WhatsApp du Mac (whatsapp://send?phone=…), appuie sur le
bouton du micro (ChatBar_VoiceMessageButton) par l'accessibilité (ax.py), joue sa phrase, puis appuie sur Envoyer
(ChatBar_PTTDraft_SendButton). Si quelque chose rate en route, la corbeille du brouillon
(ChatBar_PTTDraft_TrashButton) : rien ne part à moitié.

Pendant l'enregistrement seulement (au plus VOCAL_WHATSAPP_MAX_S) :
- la surveillance des appels ne compte pas WhatsApp qui prend le micro (sinon il se croit en appel, se tait et
  répond par notifications) ; un vrai appel Zoom, Meet… reste vu ;
- il ne t'écoute plus et ne se laisse pas couper (sinon il entend sa propre voix dans les enceintes, croit que tu
  lui coupes la parole et abandonne l'outil en cours) ;
- il garde la parole pour lui : aucune réponse, annonce ni « tink » ne se glisse dans le vocal.
Tout est rétabli dans un `finally`, et le drapeau expire seul au bout du maximum, même si rien ne le remet.

Jamais de repli : si le vocal ne peut pas partir, il le dit (ÉCHEC + la raison). Pas de texte, pas de fichier.

Un groupe (ou quelqu'un sans numéro dans Contacts) : il ouvre la discussion qui porte exactement ce nom dans la
liste de WhatsApp (en la cherchant si elle n'y est pas), vérifie que l'en-tête de la discussion ouverte dit bien ce
nom, et refuse s'il y en a plusieurs. Le même chemin sert au texte (envoyer_whatsapp) : le texte est posé dans la
zone d'écriture, Entrée, et il vérifie qu'il a quitté la zone.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import unicodedata
import weakref
from collections import Counter
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import numpy as np
from claude_agent_sdk import tool

from . import ax, config, voix_locale

MICRO = "ChatBar_VoiceMessageButton"
ENVOYER = "ChatBar_PTTDraft_SendButton"
CORBEILLE = "ChatBar_PTTDraft_TrashButton"
RECHERCHE = "TokenizedSearchBar_TextView"
LISTE = "ChatListView_TableView"
ENTETE = "NavigationBar_HeaderViewButton"
ECRITURE = "ChatBar_ComposerTextView"

# --- Le drapeau « vocal en cours » ------------------------------------------------------------------------------

_jusqua = 0.0            # jusqu'à quand l'écoute et le barge-in sont suspendus (horloge monotone)
_whatsapp_jusqua = 0.0   # jusqu'à quand la surveillance des appels ne compte pas WhatsApp
# WhatsApp peut garder le micro un instant après l'envoi : la surveillance l'ignore encore jusqu'à ce qu'il le
# lâche (au plus APRES_S). Dès qu'il l'a lâché, un vrai appel WhatsApp est de nouveau vu au coup d'œil suivant.
APRES_S = 20.0


def en_cours() -> bool:
    """Un vocal s'enregistre : il ne t'écoute pas et ne se laisse pas couper."""
    return time.monotonic() < _jusqua


def whatsapp_ignore() -> bool:
    """La surveillance des appels doit-elle ignorer WhatsApp qui tient le micro ?"""
    return time.monotonic() < _whatsapp_jusqua


def whatsapp_a_lache() -> None:
    """La surveillance a vu WhatsApp lâcher le micro : fini de l'ignorer (sauf pendant l'enregistrement)."""
    global _whatsapp_jusqua
    if not en_cours():
        _whatsapp_jusqua = 0.0


def est_whatsapp(app: str) -> bool:
    return app.casefold().startswith("net.whatsapp")


@contextmanager
def suspendre(maxi: float | None = None) -> Iterator[None]:
    """Le temps du bloc (au plus `maxi` secondes, même si le bloc ne se termine jamais) : écoute, barge-in et
    détection d'appel par WhatsApp suspendus. Rétabli à la sortie, même sur une erreur."""
    global _jusqua, _whatsapp_jusqua
    maxi = config.VOCAL_WHATSAPP_MAX_S if maxi is None else maxi
    _jusqua = _whatsapp_jusqua = time.monotonic() + maxi
    print("   🎙  Vocal WhatsApp : je ne t'écoute plus le temps de l'enregistrer.", flush=True)
    try:
        yield
    finally:
        _jusqua = 0.0
        _whatsapp_jusqua = time.monotonic() + APRES_S
        print("   🎙  Vocal WhatsApp : je t'écoute de nouveau.", flush=True)


# --- Le son ------------------------------------------------------------------------------------------------------

class Echec(Exception):
    """Le vocal ne part pas ; le message dit pourquoi, en clair."""


_BLANC = np.zeros(int(0.25 * 48000), dtype=np.float32)


def fabriquer(texte: str) -> np.ndarray:
    """Tout le vocal, phrase par phrase, avec sa voix locale (48 kHz mono). Echec si elle ne peut pas."""
    from . import tts
    phrases = tts._phrases(tts._nettoyer(texte))
    if not phrases:
        raise Echec("il n'y a rien à dire dans ce vocal")
    if not voix_locale.active():
        raise Echec("sa voix locale est coupée (JERVIS_VOIX_LOCALE) : sans elle, pas de vocal")
    voix_locale.demarrer()
    if not voix_locale.attendre(45):
        raise Echec("sa voix locale n'est pas prête (Pocket TTS ne s'est pas chargé)")
    morceaux = [_BLANC]
    for phrase in phrases:
        son = voix_locale.dire(phrase)
        if son is None or not len(son):
            raise Echec("sa voix locale n'a pas pu dire une des phrases")
        morceaux += [tts._rogner(son), _BLANC]
    return np.concatenate(morceaux).astype(np.float32)


# --- À qui -------------------------------------------------------------------------------------------------------

def _simple(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.casefold()) if unicodedata.category(c) != "Mn").strip()


def choisir_numero(fiches: str, nom: str) -> str:
    """Le numéro à utiliser parmi les fiches de Contacts (« Nom | tél +33… (Mobile) | mail … », une par ligne).
    Le portable d'abord. Echec si personne, ou plusieurs personnes possibles."""
    gens = []
    for ligne in fiches.splitlines():
        champs = [c.strip() for c in ligne.split(" | ")]
        tels = [c[4:] for c in champs[1:] if c.startswith("tél ")]
        if champs and champs[0] and tels:
            gens.append((champs[0], tels))
    if not gens:
        raise Echec(f"pas de numéro de téléphone pour « {nom} » dans Contacts")
    # Contacts trouve « Ma » dans « Mamad Diallo » : chaque mot dit doit être un mot entier de la fiche, sinon la
    # question (« un vocal à Ma ») et la règle 📌 viseraient quelqu'un d'autre que la personne envoyée.
    mots = _simple(nom).split()
    entiers = [g for g in gens if mots and all(m in re.findall(r"\w+", _simple(g[0])) for m in mots)]
    if not entiers:
        raise Echec(f"« {nom} » ne correspond exactement à aucun contact ({', '.join(g[0] for g in gens[:3])} ?) : "
                    "redis-moi son nom tel qu'il est dans Contacts")
    gens = entiers
    if len(gens) > 1:  # « Mamad » et « Mamadou » : le nom exact (ou le prénom exact) départage
        exacts = [g for g in gens if _simple(g[0]) == _simple(nom) or _simple(g[0]).split()[0] == _simple(nom)]
        if len(exacts) != 1:
            raise Echec(f"{len(gens)} contacts correspondent à « {nom} » : cherche avec chercher_contact, puis "
                        "redonne-moi son numéro")
        gens = exacts
    tels = gens[0][1]
    portable = next((t for t in tels if re.search(r"mobile|iphone|portable|cell", t, re.I)), tels[0])
    return re.sub(r"\s*\(.*\)$", "", portable).strip()


async def numero_de(contact: str) -> str:
    """Le numéro international (+33…) de `contact` : un numéro tel quel, ou un nom cherché dans Contacts."""
    from . import distance
    contact = (contact or "").strip()
    if "@" in contact:
        raise Echec("WhatsApp a besoin d'un numéro de téléphone, pas d'une adresse mail")
    if len(re.sub(r"\D", "", contact)) < 6:
        from . import outils  # (outils importe ce module : on le prend au moment de s'en servir)
        code, fiches = await outils._applescript(outils._CONTACT, contact)
        if code:
            raise Echec(f"Contacts ne répond pas ({fiches[:120]})")
        contact = choisir_numero(re.sub(r"_\$!<(.+?)>!\$_", r"\1", fiches), contact)
    numero = distance.cle(contact)
    if not numero.startswith("+"):
        raise Echec(f"le numéro « {contact} » n'a pas d'indicatif (+33…) : WhatsApp ne saurait pas à qui l'envoyer")
    return numero


# --- WhatsApp, piloté par l'accessibilité -------------------------------------------------------------------------

# Une bulle de vocal dans la discussion (pas les boutons de la barre du bas) : son identifiant ou sa description.
_VOCAL = re.compile(r"vocal|voice|ptt|audio", re.I)


class WhatsApp:
    """L'app WhatsApp du Mac. Les tests en passent un faux avec les mêmes méthodes."""

    PROCESSUS = "WhatsApp"

    def _app(self) -> ax.Element | None:
        pid = ax.pid_de(self.PROCESSUS)
        return ax.application(pid) if pid else None

    def _fenetres(self) -> list[ax.Element]:
        app = self._app()
        return app.liste("AXWindows") if app else []

    def _trouver(self, identifiant: str) -> ax.Element | None:
        for fenetre in self._fenetres():
            if (element := ax.trouver(fenetre, identifiant)) is not None:
                return element
        return None

    def _menu(self, mot: str) -> bool:
        """Appuie sur l'article de menu dont l'identifiant contient `mot` (« fermer_la_discussion »)."""
        app = self._app()
        article = app and ax.trouver(app, condition=lambda e: e.texte("AXRole") == "AXMenuItem"
                                     and mot in (e.texte("AXIdentifier") or "") and e.booleen("AXEnabled") is not False)
        return bool(article) and article.appuyer()

    def pret(self) -> str | None:
        """None si on peut le piloter, sinon pourquoi pas."""
        if not ax.autorise():
            return ("macOS ne laisse pas Jervis piloter les apps : il manque l'autorisation Accessibilité "
                    "(Réglages → Confidentialité et sécurité → Accessibilité)")
        return None

    def existe(self, identifiant: str) -> bool:
        return self._trouver(identifiant) is not None

    def appuyer(self, identifiant: str) -> bool:
        element = self._trouver(identifiant)
        return element is not None and element.appuyer()

    def devant(self) -> None:
        """Sort ses fenêtres du Dock et la met au premier plan : une fenêtre réduite annule le brouillon."""
        app = self._app()
        if app is None:
            return
        for fenetre in app.liste("AXWindows"):
            if fenetre.booleen("AXMinimized"):
                fenetre.mettre("AXMinimized", False)
        app.mettre("AXFrontmost", True)

    def ouvrir(self, numero: str, patience: float = 12.0) -> None:
        """Ouvre la discussion de ce numéro. Pour être sûr que le bouton du micro est celui de CETTE discussion
        (pas celui de la discussion d'avant, si le numéro n'a pas WhatsApp), il ferme d'abord celle qui est
        ouverte, puis attend un bouton du micro neuf."""
        ancien = self._trouver(MICRO)
        if ancien is not None and self._menu("fermer_la_discussion"):
            fin = time.monotonic() + 2
            while time.monotonic() < fin and self.existe(MICRO):
                time.sleep(0.15)
            ancien = self._trouver(MICRO)
        try:
            subprocess.run(["open", f"whatsapp://send?phone={numero.lstrip('+')}"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise Echec(f"WhatsApp ne s'ouvre pas ({e})") from None
        fin, fenetre_demandee = time.monotonic() + patience, False
        while time.monotonic() < fin:
            if not self._fenetres() and not fenetre_demandee and self._app() is not None:
                fenetre_demandee = self._menu("ouvrir_la_fenêtre_principale")
            bouton = self._trouver(MICRO)
            if bouton is not None and (ancien is None or bouton != ancien):
                time.sleep(0.5)  # la discussion finit de s'afficher
                return
            time.sleep(0.3)
        if ancien is not None:
            raise Echec("je ne suis pas sûr que la bonne discussion WhatsApp soit ouverte : je n'envoie rien")
        raise Echec("la discussion WhatsApp ne s'est pas ouverte (ce numéro a-t-il WhatsApp ? WhatsApp est-il "
                    "connecté sur le Mac ?)")

    def ouverte(self) -> str:
        """Le nom de la discussion ouverte (l'en-tête : un groupe, ou quelqu'un)."""
        entete = self._trouver(ENTETE)
        return (entete.texte("AXDescription") or "") if entete is not None else ""

    def discussions(self, nom: str) -> list[ax.Element]:
        """Les discussions de la liste qui portent exactement ce nom (aux majuscules et aux accents près)."""
        liste = self._trouver(LISTE)
        if liste is None:
            return []
        return [c for c in liste.liste("AXChildren") if _simple(c.texte("AXDescription") or "") == _simple(nom)]

    def chercher(self, texte: str) -> bool:
        """Tape `texte` dans la recherche de WhatsApp (vide : la remet à zéro). Vrai si elle l'a pris."""
        champ = self._trouver(RECHERCHE)
        if champ is None:
            return False
        champ.mettre("AXFocused", True)
        if champ.mettre_texte("AXValue", texte) and (champ.texte("AXValue") or "") == texte:
            return True
        pid = ax.pid_de(self.PROCESSUS)
        return bool(texte) and pid is not None and champ.booleen("AXFocused") is True and ax.taper(pid, texte)

    def ouvrir_nom(self, nom: str, patience: float = 10.0) -> None:
        """Ouvre la discussion qui s'appelle exactement `nom` (un groupe surtout). Refuse s'il y en a plusieurs, et
        n'en sort que quand l'en-tête dit ce nom avec un bouton du micro neuf : jamais la discussion d'à côté."""
        if not _simple(nom):
            raise Echec("quel groupe ? Redis-moi son nom tel qu'il est dans WhatsApp")
        self.devant()
        ancien, cherche = self._trouver(MICRO), False
        trouvees = self.discussions(nom)
        if not trouvees:
            cherche = self.chercher(nom)
            fin = time.monotonic() + 4
            while cherche and time.monotonic() < fin and not (trouvees := self.discussions(nom)):
                time.sleep(0.3)
        try:
            if len(trouvees) > 1:
                raise Echec(f"{len(trouvees)} discussions s'appellent « {nom} » dans WhatsApp : je n'envoie rien, "
                            "redis-moi lequel (son nom exact)")
            if not trouvees:
                raise Echec(f"aucune discussion WhatsApp ne s'appelle exactement « {nom} » : redis-moi son nom tel "
                            "qu'il est dans WhatsApp")
            # La discussion déjà ouverte est un texte dans la liste (sélectionnée), les autres des boutons.
            deja = trouvees[0].texte("AXRole") != "AXButton" and _simple(self.ouverte()) == _simple(nom)
            if not deja and not trouvees[0].appuyer():
                raise Echec("WhatsApp n'a pas ouvert la discussion")
            fin = time.monotonic() + patience
            while time.monotonic() < fin:
                bouton = self._trouver(MICRO)
                if (_simple(self.ouverte()) == _simple(nom) and bouton is not None
                        and (deja or ancien is None or bouton != ancien)):
                    time.sleep(0.5)  # la discussion finit de s'afficher
                    return
                time.sleep(0.3)
            raise Echec(f"je ne suis pas sûr que la discussion « {nom} » soit ouverte : je n'envoie rien")
        finally:
            if cherche:
                self.chercher("")

    def ecrire(self, texte: str) -> None:
        """Pose `texte` dans la zone d'écriture de la discussion ouverte (sans l'envoyer)."""
        champ = self._trouver(ECRITURE)
        if champ is None:
            raise Echec("je ne trouve pas la zone d'écriture de la discussion")
        if (champ.texte("AXValue") or "").strip():
            raise Echec("il y a déjà un brouillon dans cette discussion : je n'y touche pas")
        champ.mettre("AXFocused", True)
        if not champ.mettre_texte("AXValue", texte) or (champ.texte("AXValue") or "") != texte:
            self.effacer()
            raise Echec("WhatsApp n'a pas pris le texte dans la zone d'écriture")

    def brouillon(self) -> str:
        champ = self._trouver(ECRITURE)
        return (champ.texte("AXValue") or "") if champ is not None else ""

    def effacer(self) -> None:
        champ = self._trouver(ECRITURE)
        if champ is not None:
            champ.mettre_texte("AXValue", "")

    def valider(self) -> bool:
        """Entrée dans la zone d'écriture (ce que fait ton doigt pour envoyer), remise à WhatsApp seul."""
        champ, pid = self._trouver(ECRITURE), ax.pid_de(self.PROCESSUS)
        return champ is not None and pid is not None and champ.booleen("AXFocused") is not False \
            and ax.taper(pid, entree=True)

    def vocaux(self) -> Counter:
        """Les bulles de vocal visibles : (identifiant, rôle, description) → combien."""
        vus: Counter = Counter()
        for fenetre in self._fenetres():
            for e in ax.parcourir(fenetre):
                ident = e.texte("AXIdentifier") or ""
                if ident.startswith("ChatBar_"):
                    continue
                description = e.texte("AXDescription") or ""
                if _VOCAL.search(ident) or _VOCAL.search(description):
                    vus[(ident, e.texte("AXRole") or "", description)] += 1
        return vus


# --- L'envoi -------------------------------------------------------------------------------------------------------

async def _attendre(condition: Callable[[], bool], delai: float, pas: float = 0.15) -> bool:
    fin = time.monotonic() + delai
    while True:
        if await asyncio.to_thread(condition):
            return True
        if time.monotonic() >= fin:
            return False
        await asyncio.sleep(pas)


def _appel_en_cours() -> list[str]:
    """Les vraies apps d'appel au micro (pas WhatsApp s'il finit à peine un vocal)."""
    from . import reunion
    apps = reunion.apps_en_appel(reunion.processus_audio())
    return [a for a in apps if not (whatsapp_ignore() and est_whatsapp(a))]


async def _enregistrer(pilote, son: np.ndarray, etat: dict) -> None:
    """Micro, sa phrase, Envoyer. `etat` dit où on en est, pour le `finally` de qui l'appelle."""
    from . import tts
    await asyncio.to_thread(pilote.devant)
    if not await asyncio.to_thread(pilote.appuyer, MICRO):
        raise Echec("je ne trouve pas le bouton du vocal dans la discussion")
    etat["enregistre"] = True
    if not await _attendre(lambda: pilote.existe(ENVOYER), 3.0):
        raise Echec("WhatsApp n'a pas commencé l'enregistrement (a-t-il le droit d'utiliser le micro ?)")
    await asyncio.sleep(0.4)  # l'enregistrement démarre vraiment
    if not await tts.jouer_tel_quel(son, config.VOCAL_WHATSAPP_SORTIE):
        if config.VOCAL_WHATSAPP_SORTIE:
            raise Echec(f"sa voix a été coupée, ou la sortie « {config.VOCAL_WHATSAPP_SORTIE} » n'existe pas")
        raise Echec("sa voix a été coupée pendant l'enregistrement (un appel qui commence ?)")
    await asyncio.sleep(0.5)  # la fin de la dernière syllabe
    if not await asyncio.to_thread(pilote.existe, ENVOYER):
        raise Echec("le brouillon du vocal a disparu avant l'envoi (fenêtre WhatsApp réduite ou fermée ?)")
    if not await asyncio.to_thread(pilote.appuyer, ENVOYER):
        raise Echec("le bouton Envoyer du vocal n'a pas répondu")
    etat["envoye"] = True


def _qui(contact: str, groupe: str) -> None:
    if bool(contact.strip()) == bool(groupe.strip()):
        raise Echec("donne soit `contact` (une personne), soit `groupe` (le nom exact du groupe), pas les deux"
                    if contact.strip() else "à qui ? Donne `contact` (une personne) ou `groupe` (le nom du groupe)")


async def _ouvrir(pilote, contact: str, groupe: str, numero: str | None) -> None:
    if groupe.strip():
        await asyncio.to_thread(pilote.ouvrir_nom, groupe.strip())
    else:
        await asyncio.to_thread(pilote.ouvrir, numero)


# Un envoi WhatsApp à la fois : ouvrir une discussion puis y écrire. Sans quoi deux envois en même temps (le
# répondeur, sa réponse dans « Message à moi-même ») pourraient écrire dans la discussion ouverte par l'autre.
_tours: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _tour() -> asyncio.Lock:
    return _tours.setdefault(asyncio.get_running_loop(), asyncio.Lock())


async def envoyer(contact: str, texte: str, pilote=None, groupe: str = "") -> tuple[str, str]:
    """(« ENVOYÉ » | « INCERTAIN » | « ÉCHEC », le détail en clair). `groupe` : le nom exact d'un groupe, à la place
    de `contact`."""
    async with _tour():
        return await _envoyer(contact, texte, pilote, groupe)


async def _envoyer(contact: str, texte: str, pilote=None, groupe: str = "") -> tuple[str, str]:
    from . import tts
    try:
        _qui(contact, groupe)
        if appels := await asyncio.to_thread(_appel_en_cours):
            from . import reunion
            raise Echec(f"tu es en appel ({reunion.noms(appels)}) : je ne joue pas un vocal dans ton appel")
        numero = None if groupe.strip() else await numero_de(contact)
        son = await asyncio.to_thread(fabriquer, texte)
        duree = len(son) / 48000
        if duree > config.VOCAL_WHATSAPP_MAX_S - 5:
            raise Echec(f"trop long ({duree:.0f} s ; au plus {config.VOCAL_WHATSAPP_MAX_S - 5:.0f} s) : raccourcis")
        pilote = pilote or WhatsApp()
        if raison := pilote.pret():
            raise Echec(raison)
        await _ouvrir(pilote, contact, groupe, numero)
        avant = await asyncio.to_thread(pilote.vocaux)
        etat = {"enregistre": False, "envoye": False}
        try:
            async with tts.micro_reserve():
                with suspendre():
                    await asyncio.wait_for(_enregistrer(pilote, son, etat), config.VOCAL_WHATSAPP_MAX_S)
        except asyncio.TimeoutError:
            raise Echec(f"l'enregistrement a dépassé {config.VOCAL_WHATSAPP_MAX_S:.0f} s") from None
        finally:
            if etat["enregistre"] and not etat["envoye"]:
                pilote.appuyer(CORBEILLE)  # rien ne part à moitié (sans attendre : ça marche même annulé)
    except Echec as e:
        print(f"   🎙  Vocal WhatsApp raté : {e}", flush=True)
        return "ÉCHEC", str(e)
    except Exception as e:  # une panne imprévue : jamais de repli, on le dit
        print(f"   🎙  Vocal WhatsApp raté : {type(e).__name__}: {e}", flush=True)
        return "ÉCHEC", f"panne imprévue ({type(e).__name__})"
    # Il a appuyé sur Envoyer : la bulle doit apparaître et le brouillon disparaître.
    nouveaux: Counter = Counter()

    def parti() -> bool:
        nonlocal nouveaux
        nouveaux = pilote.vocaux() - avant
        return not pilote.existe(ENVOYER) and bool(nouveaux)
    if await _attendre(parti, 8.0, pas=0.5):
        # (jamais la description dans le journal : elle peut contenir le nom du contact ou l'heure)
        print(f"   🎙  Vocal WhatsApp envoyé ({duree:.0f} s) : {[(i, r) for i, r, _ in nouveaux]}", flush=True)
        return "ENVOYÉ", f"vocal de {duree:.0f} s envoyé, la bulle est dans la discussion"
    print("   🎙  Vocal WhatsApp : Envoyer appuyé, mais pas de bulle vue.", flush=True)
    return "INCERTAIN", ("j'ai appuyé sur Envoyer mais je ne vois pas la bulle du vocal dans la discussion. Ne le "
                         "renvoie pas : demande-lui de regarder WhatsApp")


async def envoyer_texte(contact: str, texte: str, pilote=None, groupe: str = "") -> tuple[str, str]:
    """Un message écrit WhatsApp, à quelqu'un ou dans un groupe : (« ENVOYÉ » | « INCERTAIN » | « ÉCHEC », détail)."""
    async with _tour():
        return await _envoyer_texte(contact, texte, pilote, groupe)


async def _envoyer_texte(contact: str, texte: str, pilote=None, groupe: str = "") -> tuple[str, str]:
    ecrit = False
    try:
        _qui(contact, groupe)
        if not texte.strip():
            raise Echec("le message est vide")
        numero = None if groupe.strip() else await numero_de(contact)
        pilote = pilote or WhatsApp()
        if raison := pilote.pret():
            raise Echec(raison)
        await _ouvrir(pilote, contact, groupe, numero)
        await asyncio.to_thread(pilote.ecrire, texte)
        ecrit = True
        if not await asyncio.to_thread(pilote.valider):
            raise Echec("WhatsApp n'a pas pris la touche Entrée")
    except Echec as e:
        if ecrit:
            pilote.effacer()  # rien ne reste à moitié dans la zone d'écriture
        print(f"   💬 Message WhatsApp raté : {e}", flush=True)
        return "ÉCHEC", str(e)
    except Exception as e:
        if ecrit:
            pilote.effacer()
        print(f"   💬 Message WhatsApp raté : {type(e).__name__}: {e}", flush=True)
        return "ÉCHEC", f"panne imprévue ({type(e).__name__})"
    if await _attendre(lambda: texte.strip() not in pilote.brouillon(), 3.0, pas=0.3):
        print("   💬 Message WhatsApp envoyé.", flush=True)
        return "ENVOYÉ", "le message est parti (il a quitté la zone d'écriture)"
    pilote.effacer()
    print("   💬 Message WhatsApp : Entrée sans effet, brouillon effacé.", flush=True)
    return "ÉCHEC", "WhatsApp a gardé le texte dans la zone d'écriture (Entrée n'a rien envoyé) ; je l'ai effacé"


@tool("envoyer_whatsapp", "Envoie un message ÉCRIT WhatsApp, à quelqu'un ou dans un groupe : « écris sur le groupe "
      "Famille que j'arrive », « dis à Mamad sur WhatsApp… ». Soit `contact` = son nom dans Contacts ou son numéro "
      "(+33…), soit `groupe` = le nom exact du groupe (ou de la discussion) tel qu'il est dans WhatsApp ; `texte` = le "
      "message ; `nom` = le prénom (ou le groupe) à prononcer quand tu demandes confirmation.",
      {"type": "object",
       "properties": {"contact": {"type": "string"}, "groupe": {"type": "string"}, "texte": {"type": "string"},
                      "nom": {"type": "string"}},
       "required": ["texte"]})
async def envoyer_whatsapp(args: dict[str, Any]) -> dict[str, Any]:
    statut, detail = await envoyer_texte(str(args.get("contact") or ""), str(args.get("texte") or ""),
                                         groupe=str(args.get("groupe") or ""))
    texte = f"{statut} : {detail}."
    if statut == "ÉCHEC":
        return {"content": [{"type": "text", "text": texte + " Rien n'est parti."}], "is_error": True}
    return {"content": [{"type": "text", "text": texte}]}


@tool("envoyer_vocal_whatsapp", "Envoie un VRAI message vocal WhatsApp (une bulle de voix, dite avec ta voix) : "
      "« envoie-lui un vocal », « dis-le à Mamad en vocal », « un vocal sur le groupe Famille ». Soit `contact` = son "
      "nom dans Contacts ou son numéro (+33…), soit `groupe` = le nom exact du groupe tel qu'il est dans WhatsApp ; "
      "`texte` = ce que tu dis dans le vocal, à la première personne, comme tu le dirais ; `nom` = le "
      "prénom à prononcer quand tu demandes confirmation. Jamais de texte ni de fichier à la place, même si ça rate. "
      "« INCERTAIN » : ne le renvoie pas, dis-lui de regarder WhatsApp.",
      {"type": "object",
       "properties": {"contact": {"type": "string"}, "groupe": {"type": "string"}, "texte": {"type": "string"},
                      "nom": {"type": "string"}},
       "required": ["texte"]})
async def envoyer_vocal_whatsapp(args: dict[str, Any]) -> dict[str, Any]:
    statut, detail = await envoyer(str(args.get("contact") or ""), str(args.get("texte") or ""),
                                   groupe=str(args.get("groupe") or ""))
    texte = f"{statut} : {detail}."
    if statut == "ÉCHEC":
        return {"content": [{"type": "text", "text": texte + " Rien n'est parti. N'envoie ni texte ni fichier à la "
                             "place : dis-lui pourquoi en une phrase."}], "is_error": True}
    return {"content": [{"type": "text", "text": texte}]}


OUTILS_VOCAL = [envoyer_vocal_whatsapp, envoyer_whatsapp]
