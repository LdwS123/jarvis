"""Le tableau de bord : une page locale (http://127.0.0.1:8765) façon écran holographique.

Rien n'y est inventé : son état, vos derniers échanges, ce qu'il vient de faire et ses temps de réponse
(lus à la fin de jervis.log), son usage de Claude, sa voix, et l'état du Mac expliqué en clair.
On peut aussi lui écrire (le chat, chat.py) : il répond par écrit, sans un mot à voix haute.
La page Réglages (/reglages, reglages.py) y est servie aussi : son nom, ses langues, sa voix, ton compte Claude.

Le serveur n'écoute que sur cette machine, refuse les pages d'autres sites, et ne calcule rien tant que
la page n'est pas ouverte : la boucle vocale ne le sent pas.
"""

from __future__ import annotations

import ast
import ctypes
import ctypes.util
import datetime as dt
import hmac
import html
import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import chat, compteur, config, ligne, reglages, voix_locale

PAGE = Path(__file__).with_name("tableau.html")
JOURNAL = config.DOSSIER / "jervis.log"
FIN_DU_JOURNAL = 96 * 1024   # on ne relit que la fin du journal : assez pour une longue conversation
CORPS_MAX = 20_000           # un message tapé (chat.TEXTE_MAX caractères, accents compris) tient largement dedans

# --- Ce que raconte jervis.log ----------------------------------------------------------------------

_COULEURS = re.compile(r"\x1b\[[0-9;]*m")
_TOI = re.compile(r"^🧑 Toi(?: \(([^)]*)\))? : (.*)$")
_OUTIL = re.compile(r"^\s*⚙️?\s+(\S+)(.*)$")
_CHRONO = re.compile(r"^\s*⏱ ([\d,]+) s\s+\((.*)\)\s*$")
_ETAPE = re.compile(r"^(.+?) ([\d,]+|\?)$")
_SERVEURS = {"jervis": config.PRENOM, "claude-in-chrome": "Chrome", "navigateur": "Navigateur", "mac": "Mac",
             "ecran": "Écran", "Google_Calendar": "Agenda", "Google_Drive": "Drive"}
# Ce que font les outils les plus fréquents, dit en français (les autres gardent leur nom).
_ACTIONS = {"trash thread": "mail à la corbeille", "search threads": "recherche de mails", "get thread": "lecture d'un mail",
            "send message": "envoi d'un mail", "create draft": "brouillon de mail", "reply": "réponse à un mail",
            "list events": "lecture de l'agenda", "create event": "nouveau rendez-vous", "update event": "rendez-vous modifié",
            "see": "regarde l'écran", "click": "clic", "type": "saisie au clavier", "window": "fenêtre", "dialog": "boîte de dialogue",
            "navigate": "ouvre une page", "browser navigate": "ouvre une page", "computer": "pilote la page",
            "read page": "lit la page", "get page text": "lit la page", "find": "cherche dans la page",
            "ecrire dans cursor": "prompt dans Cursor", "mettre a la corbeille": "à la corbeille",
            "effacer memoire": "efface sa mémoire", "memoire du mac": "mémoire vive du Mac",
            "verifier autorisations": "vérifie ses autorisations"}


# Une commande du terminal, telle qu'il l'a lancée : on n'en garde que le début, et jamais un mot de passe.
_SECRET = re.compile(
    r"(?i)(\b(?:[\w-]*(?:token|secret|passw(?:or)?d|passwd|pwd|api[_-]?key|apikey|auth\w*|bearer)[\w-]*)\b\s*[=:]\s*|"
    r"\b(?:bearer|basic)\s+|(?<=\s)-u\s+[^\s:]+:|--?(?:password|passwd|token|secret|api-key|key)[= ]\s*)(?:'[^']*'|\"[^\"]*\"|\S+)")
_PORTEUR = re.compile(r"(?i)\b(bearer|basic)\s+\S+")
_CLE_BRUTE = re.compile(r"\b(?:sk-[\w-]{8,}|gh[pousr]_\w{12,}|xox[abprs]-[\w-]{8,}|AKIA[0-9A-Z]{12,}|eyJ[\w-]{15,}\.[\w.-]+)")
COMMANDE_MAX = 140


def masquer_secrets(commande: str) -> str:
    """« curl -H 'Authorization: Bearer abc' … » → « curl -H 'Authorization: ••• … » : la page ne montre jamais un secret."""
    commande = _PORTEUR.sub(lambda m: m.group(1) + " •••", commande)
    commande = _SECRET.sub(lambda m: m.group(1) + "•••", commande)
    return _CLE_BRUTE.sub("•••", commande)


def lire_commande(reste: str) -> dict[str, str] | None:
    """Ce qui suit « ⚙️  Bash » dans le journal (un dict Python imprimé) → la commande et ce qu'elle voulait faire."""
    try:
        entree = ast.literal_eval(reste.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None  # une ligne coupée ou étrange : on l'ignore plutôt que de la deviner
    if not isinstance(entree, dict) or not isinstance(entree.get("command"), str):
        return None
    commande = " ".join(masquer_secrets(entree["command"]).split())
    if not commande:
        return None
    if len(commande) > COMMANDE_MAX:
        commande = commande[:COMMANDE_MAX - 1].rstrip() + "…"
    but = entree.get("description")
    return {"commande": commande, "but": " ".join(but.split())[:90] if isinstance(but, str) else ""}


def _nombre(texte: str) -> float | None:
    try:
        return float(texte.replace(",", "."))
    except ValueError:
        return None


def nommer_outil(brut: str) -> tuple[str, str]:
    """« mcp__claude_ai_Gmail__trash_thread » → (« Gmail », « mail à la corbeille »)."""
    if not brut.startswith("mcp__"):
        return brut, ""
    _, serveur, *reste = brut.split("__")
    serveur = serveur.removeprefix("claude_ai_")
    nom = _SERVEURS.get(serveur) or re.sub(r"[_-]+", " ", serveur).strip().capitalize()
    action = re.sub(r"[_-]+", " ", "__".join(reste)).strip()
    return nom, _ACTIONS.get(action, action)


def lire_chrono(total: str, detail: str) -> dict[str, Any] | None:
    """« 5,3 s (fin de phrase 0,4 · texte 0,2 · Claude 1,8 · voix 2,9) » → le total et ses étapes, en secondes."""
    secondes = _nombre(total)
    if secondes is None:
        return None
    etapes, note = [], ""
    for morceau in detail.split(" · "):
        m = _ETAPE.match(morceau.strip())
        if not m:
            continue
        if m[1].startswith("vraie réponse"):  # un instant, pas une durée : il ne s'additionne pas aux étapes
            note = morceau.strip()
        elif (duree := _nombre(m[2])) is not None:
            etapes.append([m[1].removesuffix(" à"), duree])
    return {"total": secondes, "etapes": etapes, "note": note}


def lire_journal(texte: str) -> dict[str, list]:
    """Les derniers tours de parole, ses dernières actions, ses dernières commandes et ses derniers temps de réponse."""
    echanges: list[dict[str, str]] = []
    commandes: list[dict[str, str]] = []
    outils: list[dict[str, Any]] = []
    chronos: list[dict[str, Any]] = []
    lui = f"🤖 {config.PRENOM} : "
    lui_ecrit = (f"🤖 {config.PRENOM} (écrit) : ", f"🤖 {config.PRENOM} (iPhone) : ")  # le chat, ou iMessage

    def tour(qui: str, phrase: str, ecrit: bool = False) -> None:
        # Ses phrases d'une même réponse : un seul tour. Une réponse écrite reste à part (et en entier sur la page).
        if echanges and echanges[-1]["qui"] == qui and echanges[-1].get("ecrit", False) == ecrit and not ecrit:
            echanges[-1]["texte"] += " " + phrase
        else:
            echanges.append({"qui": qui, "texte": phrase, **({"ecrit": True} if ecrit else {})})

    for ligne in _COULEURS.sub("", texte).splitlines():
        if m := _TOI.match(ligne):
            tour("toi", m[2], ecrit=m[1] in ("écrit", "iPhone"))
        elif ligne.startswith(lui_ecrit):
            tour("steve", ligne.split(" : ", 1)[1].replace(" ⏎ ", "\n"), ecrit=True)
        elif ligne.startswith(lui):
            # Son annonce au démarrage n'est pas une réponse (l'ancienne tournure traîne encore dans le journal).
            if ligne[len(lui):] not in (config.ANNONCE, f"{config.PRENOM} en ligne."):
                tour("steve", ligne[len(lui):])
        elif m := _OUTIL.match(ligne):
            nom, action = nommer_outil(m[1])
            if m[1] == "Bash" and (commande := lire_commande(m[2])):
                commandes.append(commande)
            if outils and (outils[-1]["nom"], outils[-1]["action"]) == (nom, action):
                outils[-1]["fois"] += 1
            else:
                outils.append({"nom": nom, "action": action, "fois": 1})
        elif (m := _CHRONO.match(ligne)) and (chrono := lire_chrono(m[1], m[2])):
            chronos.append(chrono)
    return {"echanges": echanges[-8:], "outils": outils[-6:], "commandes": commandes[-6:], "chronos": chronos[-24:]}


def fin_du_journal(chemin: Path = JOURNAL) -> str:
    try:
        with chemin.open("rb") as f:
            taille = f.seek(0, os.SEEK_END)
            f.seek(max(0, taille - FIN_DU_JOURNAL))
            brut = f.read()
    except OSError:
        return ""
    texte = brut.decode("utf-8", errors="replace")
    return texte.split("\n", 1)[-1] if taille > FIN_DU_JOURNAL else texte  # la 1re ligne est sans doute coupée


# --- Son usage de Claude ----------------------------------------------------------------------------

def usage() -> dict[str, Any]:
    """Son usage de Claude : la part utilisée de l'abonnement, et les 7 derniers jours en jetons (pas en dollars)."""
    jours = [{"jour": j["jour"], "jetons": j["jetons"], "appels": j["appels"]} for j in compteur.par_jour(7)]
    limites = []
    try:
        fenetres = json.loads(compteur.LIMITES.read_text(encoding="utf-8")).get("fenetres", {})
    except (OSError, ValueError):
        fenetres = {}
    for genre in ("five_hour", "seven_day"):
        if f := fenetres.get(genre):
            retour = f.get("resets_at") or 0
            libre = retour and retour < time.time()  # la fenêtre a été remise à zéro depuis la dernière mesure
            limites.append({"nom": compteur.NOMS_LIMITES.get(genre, genre),
                            "part": 0.0 if libre else float(f.get("utilization") or 0),
                            "retour": "" if libre else compteur._heure(retour)})
    return {"jours": jours, "limites": limites}


# --- Le Mac (sans psutil) ---------------------------------------------------------------------------

class _Processeur:
    """Part du processeur occupée depuis la mesure d'avant (compteurs du noyau, comme le Moniteur d'activité)."""

    def __init__(self) -> None:
        self._avant: tuple[int, ...] | None = None
        try:
            self._libc = ctypes.CDLL(ctypes.util.find_library("c"))
            self._libc.mach_host_self.restype = ctypes.c_uint
            self._hote = self._libc.mach_host_self()
        except Exception:
            self._libc = None

    def _compteurs(self) -> tuple[int, ...] | None:
        if self._libc is None:
            return None
        valeurs, n = (ctypes.c_uint * 4)(), ctypes.c_uint(4)
        if self._libc.host_statistics(self._hote, 3, ctypes.byref(valeurs), ctypes.byref(n)) != 0:  # 3 = charge CPU
            return None
        return tuple(valeurs)  # utilisateur, système, inactif, basse priorité

    def part(self) -> float:
        maintenant, avant = self._compteurs(), self._avant
        self._avant = maintenant
        if maintenant and avant:
            ecarts = [(a - b) % 2**32 for a, b in zip(maintenant, avant)]
            if total := sum(ecarts):
                return 1 - ecarts[2] / total
        return min(1.0, os.getloadavg()[0] / (os.cpu_count() or 1))  # 1re mesure : la charge moyenne


def _sortie(*commande: str) -> str:
    try:
        return subprocess.run(commande, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def memoire_occupee() -> float | None:
    """Mémoire active + réservée + compressée, sur la mémoire totale (le « utilisée » du Moniteur d'activité)."""
    texte = _sortie("vm_stat")
    page = re.search(r"page size of (\d+) bytes", texte)
    pages = dict(re.findall(r"^(.+?):\s+(\d+)\.", texte, re.MULTILINE))
    try:
        occupees = sum(int(pages[k]) for k in ("Pages active", "Pages wired down", "Pages occupied by compressor"))
        return min(1.0, occupees * int(page[1]) / (os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")))
    except (KeyError, TypeError, ValueError, OSError):
        return None


def batterie() -> dict[str, Any] | None:
    """{"part": 0.83, "secteur": True, "reste": 200, "charge": False}, ou None sur un Mac sans batterie.

    `reste` : les minutes avant qu'elle soit vide (ou pleine, si elle charge), quand macOS a une estimation."""
    texte = _sortie("pmset", "-g", "batt")
    if m := re.search(r"(\d+)%", texte):
        reste = re.search(r"(\d+):(\d+) remaining", texte)
        return {"part": int(m[1]) / 100, "secteur": "AC Power" in texte,
                "reste": _minutes(reste) if reste and "no estimate" not in texte else None,
                "charge": bool(re.search(r";\s*charging", texte))}
    return None


def _minutes(m: re.Match) -> int | None:
    minutes = int(m[1]) * 60 + int(m[2])
    return minutes if minutes <= 15 * 60 else None  # « 20:00 » juste après le débranchement : pas encore une estimation


_PRESSIONS = {1: "normale", 2: "tendue", 4: "critique"}


def pression_memoire() -> str | None:
    """Le signal du noyau : « normale », « tendue » (il compresse et écrit sur le disque) ou « critique »."""
    try:
        return _PRESSIONS.get(int(_sortie("/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip()))
    except ValueError:
        return None


def swap_go() -> float | None:
    """La mémoire que macOS a dû poser sur le disque, faute de place (en Go)."""
    m = re.search(r"used = ([\d.]+)M", _sortie("/usr/sbin/sysctl", "-n", "vm.swapusage"))
    return round(float(m[1]) / 1024, 1) if m else None


def allume_depuis() -> int | None:
    """Les secondes depuis que le Mac a démarré."""
    m = re.search(r"sec = (\d+)", _sortie("/usr/sbin/sysctl", "-n", "kern.boottime"))
    return max(0, int(time.time()) - int(m[1])) if m else None


def _nom_app(pid: int, chemin: str) -> str:
    if pid == os.getpid():
        return config.PRENOM  # le tableau tourne dans son processus : c'est lui
    if m := re.search(r"/([^/]+)\.app/", chemin):  # « Cursor Helper (Renderer) » compte pour Cursor
        return m[1]
    return os.path.basename(chemin)


def apps_actives(combien: int = 3) -> dict[str, list]:
    """Les apps qui prennent le plus de processeur (part du Mac entier) et de mémoire (en Go), leurs aides comprises."""
    coeurs = os.cpu_count() or 1
    processeur: dict[str, float] = {}
    memoire: dict[str, float] = {}
    for ligne in _sortie("/bin/ps", "-Ao", "pid=,pcpu=,rss=,comm=").splitlines():
        morceaux = ligne.split(None, 3)
        if len(morceaux) < 4:
            continue
        try:
            pid, cpu, rss = int(morceaux[0]), float(morceaux[1].replace(",", ".")), int(morceaux[2])
        except ValueError:
            continue
        nom = _nom_app(pid, morceaux[3])
        processeur[nom] = processeur.get(nom, 0.0) + cpu / 100 / coeurs
        memoire[nom] = memoire.get(nom, 0.0) + rss / 1024 / 1024
    def tete(d: dict[str, float], n: int) -> list:
        return [{"nom": k, "part": round(v, n)} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:combien]]
    return {"processeur": tete(processeur, 3), "memoire": tete(memoire, 1)}


class _Reseau:
    """Ce qui passe par le Wi-Fi ou le câble, en octets par seconde, depuis la mesure d'avant."""

    def __init__(self) -> None:
        self._avant: tuple[float, int, int] | None = None

    @staticmethod
    def _compteurs() -> tuple[int, int] | None:
        recu = envoye = 0
        vus = False
        for ligne in _sortie("/usr/sbin/netstat", "-ibn").splitlines():
            morceaux = ligne.split()
            if len(morceaux) < 8 or not morceaux[0].startswith("en") or "<Link#" not in ligne:
                continue
            try:  # depuis la fin : l'adresse matérielle manque parfois au milieu
                recu, envoye, vus = recu + int(morceaux[-5]), envoye + int(morceaux[-2]), True
            except ValueError:
                continue
        return (recu, envoye) if vus else None

    def debit(self) -> dict[str, float] | None:
        maintenant, compteurs = time.monotonic(), self._compteurs()
        avant = self._avant
        self._avant = (maintenant, *compteurs) if compteurs else None
        if not (compteurs and avant) or maintenant - avant[0] < 0.5:
            return None
        duree = maintenant - avant[0]
        return {"recu": max(0, round((compteurs[0] - avant[1]) / duree)),
                "envoye": max(0, round((compteurs[1] - avant[2]) / duree))}


# --- Le tableau -------------------------------------------------------------------------------------

class Tableau:
    """Ce que la page affiche, et le petit serveur qui le lui donne."""

    def __init__(self, etat, actions: dict[str, Callable[[], None]] | None = None, journal: Path = JOURNAL) -> None:
        self.etat = etat                 # l'Etat du mode vocal (son nom d'état, et ses oreilles)
        self.actions = actions or {}     # les boutons de la page : {"pause": …, "memoire": …}
        self.journal = journal
        self.jeton = secrets.token_urlsafe(24)  # la page le reçoit ; un autre site ne peut pas le lire
        self.port: int | None = None
        self._processeur = _Processeur()
        self._reseau = _Reseau()
        self._garde: dict[str, tuple[float, Any]] = {}
        self._verrou = threading.RLock()  # ré-entrant : « systeme » demande « batterie »

    def _gardé(self, cle: str, duree: float, fabrique: Callable[[], Any]) -> Any:
        """Une valeur recalculée au plus toutes les `duree` secondes, et seulement si la page la demande."""
        with self._verrou:
            quand, valeur = self._garde.get(cle, (0.0, None))
            if time.monotonic() - quand > duree:
                valeur = fabrique()
                self._garde[cle] = (time.monotonic(), valeur)
            return valeur

    def pouls(self) -> dict[str, Any]:
        """Ce qui bouge vite : son état, et le volume de sa voix et du micro (pour l'anneau)."""
        oreilles = self.etat.oreilles
        ecoute = oreilles is not None and not oreilles.en_pause
        enceinte = getattr(oreilles, "haut_parleur", None) if ecoute else None
        return {"etat": self.etat.nom,
                "voix": round(float(getattr(enceinte, "niveau", 0.0)), 4),
                "micro": round(float(getattr(oreilles, "niveau", 0.0)), 4) if ecoute else 0.0}

    def _du_journal(self) -> dict[str, list]:
        try:
            marque = self.journal.stat()
            cle = (marque.st_mtime_ns, marque.st_size)
        except OSError:
            cle = None
        with self._verrou:
            quand, valeur = self._garde.get("journal", (None, None))
            if quand != cle or valeur is None:
                valeur = lire_journal(fin_du_journal(self.journal))
                self._garde["journal"] = (cle, valeur)
            return valeur

    def _systeme(self) -> dict[str, Any]:
        disque = shutil.disk_usage(Path.home())
        memoire = memoire_occupee()
        try:
            total_memoire = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3
        except (OSError, ValueError):
            total_memoire = None
        return {"processeur": round(self._processeur.part(), 3), "memoire": memoire,
                "memoire_go": [round(memoire * total_memoire, 1), round(total_memoire)]
                              if memoire is not None and total_memoire else None,
                "pression": self._gardé("pression", 5, pression_memoire),
                "swap_go": self._gardé("swap", 5, swap_go),
                "disque": round(disque.used / disque.total, 3),
                "disque_go": [round(disque.free / 1e9), round(disque.total / 1e9)],  # en Go « du Finder » (10⁹)
                "batterie": self._gardé("batterie", 30, batterie),
                "apps": self._gardé("apps", 5, apps_actives),
                "reseau": self._reseau.debit(),
                "allume": self._gardé("allume", 60, allume_depuis)}

    @staticmethod
    def _voix() -> dict[str, Any]:
        """Quelle voix il a en ce moment : sa voix locale, la voix neuronale (ligne directe ou chemin classique),
        ou celle du Mac."""
        return {"neuronale": bool(config.VOIX_NEURONALE), "directe": bool(config.VOIX_DIRECTE),
                "en_repos": bool(config.VOIX_DIRECTE) and not ligne.disponible(), "lignes": ligne.ouvertes(),
                "locale": config.VOIX_LOCALE if voix_locale.prete() else None}

    def action(self, nom: str) -> Callable[[], None] | None:
        """Ce que fait un bouton : ceux de la barre (pause, mémoire…), ou « annuler-agent-3 » (le × d'un agent)."""
        if nom in self.actions:
            return self.actions[nom]
        if nom == "reglages":
            return lambda: self.ouvrir("reglages")
        if m := re.fullmatch(r"annuler-agent-(\d{1,6})", nom):
            taches = getattr(self.etat, "taches", None)
            if taches is not None:
                return lambda: taches.annuler_depuis_un_fil(int(m.group(1)))
        return None

    def _agents(self) -> list[dict[str, Any]]:
        """Les agents de fond que Steve a lancés : ceux qui tournent, puis les derniers finis."""
        taches = getattr(self.etat, "taches", None)
        try:
            return taches.etat() if taches is not None else []
        except Exception:  # un agent qui change pendant qu'on le lit : la page le redemandera dans 1,5 s
            return []

    @staticmethod
    def _connecteurs() -> list[dict[str, str]]:
        """Chrome et les connecteurs qu'il utilise, avec ce que Claude Code en a dit la dernière fois (« connected »,
        « needs-auth », « failed »…). Un connecteur dont il n'a pas encore parlé : « inconnu »."""
        from . import brain   # ici et pas en haut : le tableau ne charge Claude que quand la page le demande
        noms = {"claude-in-chrome": "Chrome"}
        facultatifs = [c for c in config.CONNECTEURS_FACULTATIFS if c in brain.STATUT_SERVEURS]  # s'il en a vu un
        for brut in [*config.CONNECTEURS, *facultatifs]:
            court = brut.removeprefix("claude.ai ").strip()
            noms[brut] = _SERVEURS.get(court.replace(" ", "_"), court)
        return [{"nom": nom, "statut": brain.STATUT_SERVEURS.get(brut, ("inconnu", 0))[0]} for brut, nom in noms.items()]

    def _bilan_agents(self) -> dict[str, int]:
        """Combien d'agents lancés, en cours, finis, en échec, annulés depuis son démarrage."""
        taches = getattr(self.etat, "taches", None)
        try:
            return taches.bilan() if taches is not None else {}
        except Exception:
            return {}

    def instantane(self) -> dict[str, Any]:
        """Tout ce que la page affiche (elle le redemande toutes les secondes et demie)."""
        lu = self._du_journal()
        totaux = [c["total"] for c in lu["chronos"]]
        oreilles = self.etat.oreilles
        return {"prenom": config.PRENOM, "etat": self.etat.nom,
                "micro_en_pause": bool(oreilles is not None and oreilles.en_pause),
                **lu, "mediane": round(statistics.median(totaux), 2) if totaux else None,
                "usage": self._gardé("usage", 5, usage),
                "systeme": self._gardé("systeme", 2, self._systeme),
                "voix": self._voix(),
                "souvenirs": self._gardé("souvenirs", 10, _souvenirs),
                "agents": self._agents(), "bilan_agents": self._bilan_agents(),
                "connecteurs": self._gardé("connecteurs", 5, self._connecteurs)}

    def page(self) -> tuple[str, str]:
        """La page et son `nonce` (seul le script qui le porte a le droit de tourner)."""
        nonce = secrets.token_urlsafe(16)
        return (PAGE.read_text(encoding="utf-8").replace("__NONCE__", nonce).replace("__JETON__", self.jeton)
                .replace("__PRENOM__", html.escape(config.PRENOM, quote=True))), nonce

    @property
    def adresse(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def demarrer(self, port: int = config.TABLEAU_PORT) -> bool:
        global _actif
        tableau = self

        class Requete(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # la page garde la connexion ouverte : pas de poignée de main à chaque pouls

            def log_message(self, *_args) -> None:  # sinon chaque pouls écrirait une ligne dans jervis.log
                pass

            def _chez_nous(self) -> bool:
                """Refuse un autre site qui ferait pointer son nom de domaine vers 127.0.0.1 (DNS rebinding)."""
                return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

            def _repondre(self, code: int, corps: bytes = b"", genre: str = "text/plain", en_plus: dict = {}) -> None:
                self.send_response(code)
                for nom, valeur in {"Content-Type": f"{genre}; charset=utf-8", "Content-Length": str(len(corps)),
                                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **en_plus}.items():
                    self.send_header(nom, valeur)
                self.end_headers()
                self.wfile.write(corps)

            def _json(self, donnees: Any) -> None:
                self._repondre(200, json.dumps(donnees, ensure_ascii=False).encode(), "application/json")

            def _jeton_ok(self) -> bool:
                return self._chez_nous() and hmac.compare_digest(self.headers.get("X-Jeton", ""), tableau.jeton)

            def do_GET(self) -> None:
                chemin, _, requete = self.path.partition("?")
                if not self._chez_nous():
                    self._repondre(403)
                elif chemin == "/chat":  # vos messages écrits : seulement pour la page (qui a le jeton)
                    if not self._jeton_ok():
                        self._repondre(403)
                        return
                    v = re.fullmatch(r"depuis=(\d{1,12})", requete)
                    self._json(chat.MESSAGERIE.depuis(int(v[1]) if v else 0))
                elif chemin == "/pouls":
                    self._json(tableau.pouls())
                elif chemin == "/etat":
                    self._json(tableau.instantane())
                elif chemin == "/reglages":
                    page, nonce = reglages.page(tableau.jeton)
                    self._repondre(200, page.encode(), "text/html",
                                   {"Content-Security-Policy": reglages.regles(nonce)})
                elif chemin.startswith("/reglages/"):
                    if not self._jeton_ok():
                        self._repondre(403)
                        return
                    self._repondre(*reglages.traiter("GET", chemin))
                elif chemin == "/":
                    page, nonce = tableau.page()
                    regles = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                              "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                    self._repondre(200, page.encode(), "text/html", {"Content-Security-Policy": regles})
                else:
                    self._repondre(404)

            def do_POST(self) -> None:
                try:
                    taille = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    taille = -1
                if not 0 <= taille <= CORPS_MAX:  # trop gros : on ne le lit pas, et on ferme la connexion
                    self.close_connection = True
                    self._repondre(413, en_plus={"Connection": "close"})
                    return
                corps = self.rfile.read(taille)
                if not self._jeton_ok():
                    self._repondre(403)
                elif self.path == "/ecrire":
                    try:
                        texte = json.loads(corps.decode("utf-8")).get("texte", "")
                    except (ValueError, AttributeError):
                        texte = None
                    if not isinstance(texte, str) or not texte.strip():
                        self._repondre(400)
                    elif not chat.MESSAGERIE.recevoir(texte):
                        self._repondre(503, "Il n'écoute pas encore (il démarre, ou il est en pause).".encode())
                    else:
                        self._repondre(204)
                elif self.path in {"/reglages", "/reglages/claude"}:
                    self._repondre(*reglages.traiter("POST", self.path, corps))
                elif m := re.fullmatch(r"/repondre/(\d{1,12})/(oui|non)", self.path):
                    self._repondre(204 if chat.MESSAGERIE.repondre(int(m[1]), m[2] == "oui") else 410)
                elif (action := tableau.action(self.path.removeprefix("/action/"))) is None:
                    self._repondre(404)
                else:
                    action()
                    self._repondre(204)

        try:
            serveur = ThreadingHTTPServer(("127.0.0.1", port), Requete)
        except OSError as e:
            print(f"   ⚠️  Tableau de bord indisponible (port {port} : {e}). Change JERVIS_TABLEAU_PORT.", flush=True)
            return False
        serveur.daemon_threads = True
        self.port = port
        threading.Thread(target=serveur.serve_forever, daemon=True, name="jervis-tableau").start()
        _actif = self
        return True

    def ouvrir(self, page: str = "") -> bool:
        """Dans une fenêtre sans barre d'adresse si Chrome est là, sinon dans le navigateur par défaut.
        `page` : « reglages » pour la page Réglages."""
        if self.port is None:
            return False
        adresse = self.adresse + page
        if Path("/Applications/Google Chrome.app").exists():
            subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--app={adresse}"])
        else:
            subprocess.Popen(["open", adresse])
        return True


def _souvenirs() -> dict[str, int]:
    """Combien de choses il sait sur toi, et combien de fois vous vous êtes parlé aujourd'hui."""
    from . import outils

    def lignes(chemin: Path) -> list[str]:
        try:
            return chemin.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    jour = f'"quand": "{dt.date.today():%Y-%m-%d}'
    return {"faits": sum(l.startswith("- ") for l in lignes(outils.MEMOIRE)),
            "echanges_du_jour": sum(jour in l for l in lignes(outils.JOURNAL))}


_actif: Tableau | None = None  # le tableau en service (mode barre des menus), pour l'outil vocal


def ouvrir() -> bool:
    return _actif is not None and _actif.ouvrir()
