"""Ce qui se passe pendant que tu fais autre chose.

- Standard      : la file des « choses à te dire » (tâche finie, question d'une tâche, annonce programmée).
                  Elle sonne : la veille s'interrompt entre deux phrases pour te les dire.
- Taches        : les grosses tâches confiées à l'expert, chacune dans sa propre session Claude, en parallèle.
                  Steve te rend la main tout de suite et te prévient quand c'est fini.
- Programmateur : les annonces et actions à heure fixe (« à 18 h, dis-moi de partir »), et le briefing du matin.
- outils_fond   : les outils qui permettent à Steve de piloter tout ça (mcp__jervis__lancer_tache_de_fond…).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock,
                              ToolUseBlock, tool)

from . import compteur, config, outils
from .brain import EXPERT, _maintenant, preparer_connecteurs
from .permissions import Gardien


@dataclass
class Evenement:
    genre: str                    # "fini" | "question" | "annonce" | "faire"
    texte: str
    futur: asyncio.Future | None = field(default=None, repr=False)  # la réponse attendue (questions)


class Standard:
    """La file d'attente de ce que Steve doit te dire de lui-même."""

    def __init__(self) -> None:
        self._file: deque[Evenement] = deque()
        self.sonnerie = threading.Event()  # lue par le fil du micro : interrompt la veille entre deux phrases
        self._nouveau = asyncio.Event()

    def poster(self, ev: Evenement) -> None:
        self._file.append(ev)
        self.sonnerie.set()
        self._nouveau.set()

    def en_attente(self, genres: tuple[str, ...] | None = None) -> bool:
        return any(genres is None or e.genre in genres for e in self._file)

    def prendre(self, genres: tuple[str, ...] | None = None) -> Evenement | None:
        """Le plus ancien événement (d'un de ces genres), ou None."""
        for e in self._file:
            if genres is None or e.genre in genres:
                self._file.remove(e)
                break
        else:
            e = None
        if not self._file:
            self.sonnerie.clear()
            self._nouveau.clear()
        return e

    async def attendre(self) -> None:
        await self._nouveau.wait()

    async def demander(self, question: str, delai: float = 1800) -> bool:
        """Pour une tâche de fond : pose la question dès que tu es disponible. Sans réponse en 30 min, c'est non."""
        futur = asyncio.get_running_loop().create_future()
        self.poster(Evenement("question", question, futur))
        try:
            return await asyncio.wait_for(futur, delai)
        except asyncio.TimeoutError:
            return False


# --- Tâches de fond ------------------------------------------------------------------------

@dataclass
class Tache:
    numero: int
    titre: str
    debut: datetime
    travail: asyncio.Task | None = None
    action: str = "démarre"          # ce qu'il est en train de faire (dernier outil lancé)
    etapes: int = 0                  # nombre d'outils lancés jusqu'ici
    statut: str = "en cours"         # en cours | finie | échec | annulée
    fin: datetime | None = None


_OUTILS_COURANTS = {"Bash": "lance une commande", "Read": "lit un fichier", "Glob": "cherche des fichiers",
                    "Grep": "cherche dans des fichiers", "Write": "écrit un fichier", "Edit": "modifie un fichier",
                    "WebFetch": "lit une page web", "WebSearch": "cherche sur le web"}


def decrire_action(nom: str, entree: Any) -> str:
    """Un outil que l'agent vient de lancer → « cherche sur le web : … », « Gmail · mail à la corbeille »."""
    entree = entree if isinstance(entree, dict) else {}
    if nom in _OUTILS_COURANTS:
        detail = entree.get("query") or entree.get("url") or ""
        return f"{_OUTILS_COURANTS[nom]} : {str(detail)[:60]}" if detail else _OUTILS_COURANTS[nom]
    from .tableau import nommer_outil  # import tardif : tableau.py est lourd et n'a pas besoin de nous
    serveur, action = nommer_outil(nom)
    detail = str(entree.get("url") or entree.get("query") or entree.get("element") or "")[:60]
    return " · ".join(x for x in (serveur, action or nom.split("__")[-1].replace("_", " "), detail) if x)


class Taches:
    def __init__(self, standard: Standard, journal: Callable[[str], None] = print) -> None:
        self._standard = standard
        self._journal = journal
        self._taches: dict[int, Tache] = {}
        self._histoire: deque[Tache] = deque(maxlen=8)   # les dernières tâches finies, pour le tableau de bord
        self._suivant = 1
        self._boucle: asyncio.AbstractEventLoop | None = None  # celle de Steve : le tableau y envoie ses annulations

    def lancer(self, titre: str, brief: str) -> str:
        if len(self._taches) >= config.TACHES_MAX:
            return (f"Déjà {len(self._taches)} tâches en cours (le maximum) : attends qu'une finisse, "
                    "ou annules-en une.")
        tache = Tache(self._suivant, titre.strip() or "sans titre", datetime.now())
        self._suivant += 1
        self._boucle = asyncio.get_running_loop()
        tache.travail = self._boucle.create_task(self._travailler(tache, brief))
        self._taches[tache.numero] = tache
        self._journal(f"   🧵 Tâche {tache.numero} lancée : {tache.titre}")
        return f"Tâche {tache.numero} lancée en arrière-plan. Tu seras prévenu quand elle sera finie."

    def lister(self) -> str:
        """Pour la voix : ce qui tourne (avec ce que chacun fait là, tout de suite) et ce qui vient de finir."""
        if not self._taches and not self._histoire:
            return "Aucune tâche en cours."
        maintenant = datetime.now()
        lignes = [f"{t.numero}. « {t.titre} », depuis {int((maintenant - t.debut).total_seconds() // 60)} min, "
                  f"{t.etapes} étapes, en ce moment : {t.action}" for t in self._taches.values()]
        if not lignes:
            lignes = ["Aucune tâche en cours."]
        if self._histoire:
            lignes.append("Terminées récemment : " + " ; ".join(
                f"{t.numero}. « {t.titre} » ({t.statut})" for t in list(self._histoire)[:4]))
        return "\n".join(lignes)

    def etat(self) -> list[dict[str, Any]]:
        """Pour le tableau de bord : les agents en cours, puis les derniers finis (du plus récent au plus ancien)."""
        maintenant = datetime.now()
        vus = [*self._taches.values(), *self._histoire]
        return [{"numero": t.numero, "titre": t.titre, "statut": t.statut, "action": t.action if t.fin is None else "",
                 "etapes": t.etapes, "secondes": int(((t.fin or maintenant) - t.debut).total_seconds()),
                 "fini_il_y_a": int((maintenant - t.fin).total_seconds()) if t.fin else None} for t in vus]

    def _clore(self, tache: Tache, statut: str) -> None:
        """Une tâche sort de la liste des vivantes et entre dans l'histoire (une seule fois)."""
        if tache.fin is not None:
            return
        tache.statut, tache.fin = statut, datetime.now()
        self._taches.pop(tache.numero, None)
        self._histoire.appendleft(tache)

    def annuler(self, numero: int) -> str:
        tache = self._taches.get(numero)
        if tache is None:
            return f"Pas de tâche numéro {numero}."
        self._clore(tache, "annulée")
        if tache.travail:
            tache.travail.cancel()
        return f"Tâche « {tache.titre} » annulée."

    def annuler_depuis_un_fil(self, numero: int) -> bool:
        """Le bouton du tableau de bord (un autre fil que Steve) : l'annulation passe par la boucle de Steve."""
        if numero not in self._taches or self._boucle is None or self._boucle.is_closed():
            return False
        self._boucle.call_soon_threadsafe(self.annuler, numero)
        return True

    async def arreter(self) -> None:
        for n in list(self._taches):
            self.annuler(n)

    def _options(self, titre: str) -> ClaudeAgentOptions:
        serveurs: dict[str, Any] = {outils.NOM_SERVEUR: outils.serveur_jervis()}
        if navigateur := outils.serveur_navigateur(isole=True):
            serveurs[outils.NOM_NAVIGATEUR] = navigateur
        if mac := outils.serveur_mac():
            serveurs[outils.NOM_MAC] = mac
        if ecran := outils.serveur_screenpipe():
            serveurs[outils.NOM_ECRAN] = ecran

        async def confirmer(question: str) -> bool:
            return await self._standard.demander(f"Pour la tâche « {titre} » : {question}")

        consignes = (f"{EXPERT.replace('{prenom}', config.PRENOM)}\nNous sommes le {_maintenant()}.\n"
                     f"Tu travailles en arrière-plan : l'utilisateur fait autre chose pendant ce temps.\n"
                     f"Ce que {config.PRENOM} sait de lui :\n{outils.lire_memoire() or '(rien)'}\n")
        return ClaudeAgentOptions(
            system_prompt=consignes,
            model=config.MODELE_EXPERT,
            effort=config.EFFORT_EXPERT,
            thinking={"type": "adaptive"},
            tools=["Bash", "WebSearch", "WebFetch", "Read", "Glob", "Grep", "Write", "Edit"],
            setting_sources=[],
            cwd=str(config.ESPACE),
            mcp_servers=serveurs,
            max_buffer_size=config.TAMPON_MAX,
            disallowed_tools=[*outils.OUTILS_MAC_COUPES, *outils.OUTILS_ECRAN_COUPES],
            can_use_tool=Gardien(confirmer),
        )

    async def _travailler(self, tache: Tache, brief: str) -> None:
        client = ClaudeSDKClient(options=self._options(tache.titre))
        textes: list[str] = []
        resultat = None
        try:
            await client.connect()
            await preparer_connecteurs(client, 10, lambda _m: None)
            await client.query(brief)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for bloc in message.content:
                        if isinstance(bloc, TextBlock) and bloc.text.strip():
                            textes.append(bloc.text)
                        elif isinstance(bloc, ToolUseBlock):
                            tache.etapes += 1
                            tache.action = decrire_action(bloc.name, bloc.input)
                elif isinstance(message, ResultMessage):
                    resultat = message.result
                    cout = compteur.chiffres(message)
                    compteur.noter("taches", cout)
                    self._journal(f"   🧵 Tâche {tache.numero} : {compteur.dollars(cout['cout'])} (prix API)")
            self._clore(tache, "finie")
            compte_rendu = (resultat or (textes[-1] if textes else "")).strip() or "(pas de compte rendu)"
            self._journal(f"   🧵 Tâche {tache.numero} finie : {compte_rendu[:200]}")
            self._standard.poster(Evenement(
                "fini", f"(tâche de fond terminée) « {tache.titre} » : {compte_rendu}\n"
                        "Résume-le-lui en deux phrases."))
        except asyncio.CancelledError:
            self._clore(tache, "annulée")
            self._journal(f"   🧵 Tâche {tache.numero} annulée.")
            raise
        except Exception as e:
            self._clore(tache, "échec")
            self._journal(f"   🧵 Tâche {tache.numero} en échec : {e!r}")
            self._standard.poster(Evenement(
                "fini", f"(tâche de fond terminée) « {tache.titre} » a échoué ({type(e).__name__}). "
                        "Dis-le-lui en une phrase et propose de réessayer."))
        finally:
            self._clore(tache, "échec")  # sans effet si elle est déjà close ; filet si une exception rare a filé
            with contextlib.suppress(Exception):
                await client.disconnect()


# --- Annonces programmées et briefing du matin --------------------------------------------------

PROGRAMMES = config.ESPACE / "programmes.json"
ETAT_BRIEFING = config.ESPACE / "briefing_fait.txt"
BRIEFING_PAR_DEFAUT = """\
Fais-lui le point du matin, en quatre ou cinq phrases parlées, sans liste :
- la météo du jour là où il est (WebSearch) ;
- ses rendez-vous d'aujourd'hui (agenda), avec l'heure et avec qui ;
- les mails importants non lus depuis hier soir (ignore les newsletters et les pubs) ;
- ses rappels du jour.
Termine par une phrase d'encouragement courte, pas niaise.
"""


def lire_quand(quand: str, maintenant: datetime | None = None) -> datetime:
    """« 2026-11-23 18:30 » ou « 18:30 » / « 18h30 » (la prochaine fois que cette heure arrive)."""
    maintenant = maintenant or datetime.now()
    quand = quand.strip().replace("T", " ")
    with contextlib.suppress(ValueError):
        return datetime.strptime(quand[:16], "%Y-%m-%d %H:%M")
    m = re.fullmatch(r"(\d{1,2})\s*[:hH]\s*(\d{2})?", quand)
    if not m:
        raise ValueError(f"heure illisible : {quand!r} (attendu AAAA-MM-JJ HH:MM ou HH:MM)")
    h, mn = int(m.group(1)), int(m.group(2) or 0)
    moment = maintenant.replace(hour=h, minute=mn, second=0, microsecond=0)
    return moment if moment > maintenant else moment + timedelta(days=1)


def _dit_heure(d: datetime) -> str:
    jour = "aujourd'hui" if d.date() == datetime.now().date() else (
        "demain" if d.date() == datetime.now().date() + timedelta(days=1) else f"le {d:%d/%m}")
    return f"{jour} à {d:%H} h {d:%M}"


class Programmateur:
    def __init__(self, standard: Standard, journal: Callable[[str], None] = print) -> None:
        self._standard = standard
        self._journal = journal

    # Le fichier est relu à chaque fois : tu peux le modifier à la main pendant qu'il tourne.
    def _lire(self) -> list[dict[str, Any]]:
        try:
            return json.loads(PROGRAMMES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _ecrire(self, programmes: list[dict[str, Any]]) -> None:
        PROGRAMMES.parent.mkdir(parents=True, exist_ok=True)
        PROGRAMMES.write_text(json.dumps(programmes, ensure_ascii=False, indent=1), encoding="utf-8")

    def programmer(self, quand: str, dire: str = "", faire: str = "", chaque_jour: bool = False) -> str:
        if not (dire or faire):
            return "Il faut `dire` (une phrase à annoncer) ou `faire` (une consigne à exécuter)."
        moment = lire_quand(quand)
        programmes = self._lire()
        numero = max((p["id"] for p in programmes), default=0) + 1
        programmes.append({"id": numero, "prochain": moment.isoformat(timespec="minutes"),
                           "chaque_jour": bool(chaque_jour), **({"faire": faire} if faire else {"dire": dire})})
        self._ecrire(programmes)
        return f"Programmé (n° {numero}) {_dit_heure(moment)}" + (", puis chaque jour." if chaque_jour else ".")

    def lister(self) -> str:
        programmes = sorted(self._lire(), key=lambda p: p["prochain"])
        if not programmes:
            return "Rien de programmé."
        return "\n".join(f"{p['id']}. {_dit_heure(datetime.fromisoformat(p['prochain']))}"
                         f"{' (chaque jour)' if p.get('chaque_jour') else ''} : {p.get('dire') or p.get('faire')}"
                         for p in programmes)

    def annuler(self, numero: int) -> str:
        programmes = self._lire()
        restants = [p for p in programmes if p["id"] != numero]
        if len(restants) == len(programmes):
            return f"Rien de programmé sous le numéro {numero}."
        self._ecrire(restants)
        return "Annulé."

    async def tourner(self) -> None:
        """Toutes les 20 secondes : ce qui est arrivé à échéance part dans le standard."""
        while True:
            try:
                await self._echeances()
                await self._briefing()
            except Exception as e:  # un fichier abîmé ne doit pas tuer Steve
                self._journal(f"   ⏰ Programmateur : {e!r}")
            await asyncio.sleep(20)

    async def _echeances(self) -> None:
        maintenant = datetime.now()
        programmes, changes = self._lire(), False
        for p in list(programmes):
            moment = datetime.fromisoformat(p["prochain"])
            if moment > maintenant:
                continue
            changes = True
            if maintenant - moment < timedelta(hours=6):  # Mac éteint toute la nuit : on ne rattrape pas tout
                if faire := p.get("faire"):
                    self._standard.poster(Evenement("faire", f"(programmé) {faire}"))
                else:
                    self._standard.poster(Evenement("annonce", p.get("dire", "")))
                await notifier(config.PRENOM, p.get("dire") or p.get("faire", ""))
            if p.get("chaque_jour"):
                while moment <= maintenant:
                    moment += timedelta(days=1)
                p["prochain"] = moment.isoformat(timespec="minutes")
            else:
                programmes.remove(p)
        if changes:
            self._ecrire(programmes)

    async def _briefing(self) -> None:
        maintenant = datetime.now()
        if not config.BRIEFING or maintenant.hour >= 12:
            return
        if f"{maintenant:%H:%M}" < config.BRIEFING_HEURE:
            return
        aujourd_hui = f"{maintenant:%Y-%m-%d}"
        if ETAT_BRIEFING.exists() and ETAT_BRIEFING.read_text().strip() == aujourd_hui:
            return
        if not await present():  # il attend que tu sois devant le Mac
            return
        ETAT_BRIEFING.parent.mkdir(parents=True, exist_ok=True)
        ETAT_BRIEFING.write_text(aujourd_hui)
        self._standard.poster(Evenement("faire", f"(programmé) Briefing du matin.\n{lire_briefing()}"))


def lire_briefing() -> str:
    if not config.FICHIER_BRIEFING.exists():
        config.FICHIER_BRIEFING.write_text(BRIEFING_PAR_DEFAUT, encoding="utf-8")
    return config.FICHIER_BRIEFING.read_text(encoding="utf-8")


async def present() -> bool:
    """Tu as touché le clavier ou la souris depuis moins de PRESENT_S secondes (compteur HIDIdleTime de macOS)."""
    code, sortie = await outils._lancer("ioreg", "-c", "IOHIDSystem", "-d", "4", delai=5)
    m = re.search(r'"HIDIdleTime" = (\d+)', sortie)
    return code == 0 and m is not None and int(m.group(1)) < config.PRESENT_S * 1e9


async def notifier(titre: str, texte: str) -> None:
    """Une notification macOS, en plus de la voix (utile si tu as mis le son en sourdine)."""
    script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
    with contextlib.suppress(Exception):
        await outils._applescript(script, titre, texte[:200])


# --- Les outils que Steve utilise pour tout ça ------------------------------------------------

def outils_fond(taches: Taches, programmateur: Programmateur) -> list:
    _t = outils._texte

    @tool("lancer_tache_de_fond", "Confie un travail à un agent (ton expert) qui l'exécute en arrière-plan, en "
          "parallèle des autres : tu restes disponible et tu seras prévenu quand c'est fini. À utiliser dès que "
          "c'est long ou en plusieurs étapes : recherche sur le web ou sur LinkedIn, prospection, comparaison, "
          "réservation, rédaction, code. Plusieurs travaux indépendants = plusieurs appels dans le même tour, un "
          "agent chacun. `titre` = 3 à 6 mots ; `brief` = tout ce qu'il doit savoir : l'objectif, ce que tu sais "
          "déjà, les contraintes, et ce qu'il doit te rendre.", {"titre": str, "brief": str})
    async def lancer_tache_de_fond(args: dict[str, Any]) -> dict[str, Any]:
        return _t(taches.lancer(args["titre"], args["brief"]))

    @tool("taches_en_cours", "Liste les agents de fond : ce que chacun fait en ce moment, depuis combien de temps, "
          "et ceux qui viennent de finir.", {})
    async def taches_en_cours(_args: dict[str, Any]) -> dict[str, Any]:
        return _t(taches.lister())

    @tool("annuler_tache", "Annule une tâche de fond par son numéro (vu avec taches_en_cours).", {"numero": int})
    async def annuler_tache(args: dict[str, Any]) -> dict[str, Any]:
        return _t(taches.annuler(int(args["numero"])))

    @tool("programmer_annonce", "Programme quelque chose à heure fixe, pour que TOI tu le fasses à ce moment-là. "
          "`quand` = « AAAA-MM-JJ HH:MM » ou « HH:MM » ; `dire` = la phrase à lui annoncer (« Il est l'heure de "
          "partir à la gare. ») OU `faire` = une consigne que tu exécuteras alors (« regarde si son train a du "
          "retard et dis-le-lui ») ; `chaque_jour` = true pour répéter tous les jours.",
          {"type": "object", "properties": {"quand": {"type": "string"}, "dire": {"type": "string"},
                                            "faire": {"type": "string"}, "chaque_jour": {"type": "boolean"}},
           "required": ["quand"]})
    async def programmer_annonce(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _t(programmateur.programmer(args["quand"], args.get("dire", ""), args.get("faire", ""),
                                               bool(args.get("chaque_jour"))))
        except ValueError as e:
            return outils._erreur(str(e))

    @tool("lister_programmes", "Liste les annonces et actions programmées.", {})
    async def lister_programmes(_args: dict[str, Any]) -> dict[str, Any]:
        return _t(programmateur.lister())

    @tool("annuler_programme", "Annule une annonce programmée par son numéro (vu avec lister_programmes).",
          {"numero": int})
    async def annuler_programme(args: dict[str, Any]) -> dict[str, Any]:
        return _t(programmateur.annuler(int(args["numero"])))

    return [lancer_tache_de_fond, taches_en_cours, annuler_tache, programmer_annonce, lister_programmes,
            annuler_programme]
