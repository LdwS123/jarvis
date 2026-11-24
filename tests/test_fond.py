"""Les agents de fond : plusieurs en parallèle, ce que chacun fait, leur histoire, et ce que voit le tableau de bord.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_fond.py

Sans Claude ni réseau : un faux client joue les messages (un outil lancé, puis le résultat).
"""

import asyncio
import os
import threading
import types

threading.Timer(30, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock  # noqa: E402

from jervis import compteur, config, fond, tableau  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


# --- 1. Ce qu'un agent est en train de faire, dit en français --------------------------------------------------
for nom, entree, attendu in [
    ("WebSearch", {"query": "fondateurs fintech Paris"}, "cherche sur le web : fondateurs fintech Paris"),
    ("WebFetch", {"url": "https://exemple.fr"}, "lit une page web : https://exemple.fr"),
    ("Bash", {"command": "ls"}, "lance une commande"),
    ("Write", {"file_path": "/x"}, "écrit un fichier"),
    ("mcp__claude_ai_Gmail__trash_thread", {}, "Gmail · mail à la corbeille"),
    ("mcp__navigateur__browser_navigate", {"url": "https://linkedin.com"}, "Navigateur · ouvre une page · https://linkedin.com"),
]:
    verifier(f"action : {nom}", fond.decrire_action(nom, entree), attendu)
verifier("action : une entrée qui n'est pas un dict", fond.decrire_action("Bash", None), "lance une commande")
verifier("action : une très longue requête est coupée",
         len(fond.decrire_action("WebSearch", {"query": "x" * 500})), len("cherche sur le web : ") + 60)

# --- 2. Un faux client ---------------------------------------------------------------------------------------------
barrieres: dict[str, asyncio.Event] = {}


class FauxClient:
    def __init__(self, options=None) -> None:
        self.brief = ""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def query(self, brief: str) -> None:
        self.brief = brief

    async def receive_response(self):
        yield AssistantMessage([ToolUseBlock("1", "WebSearch", {"query": self.brief})], "m")
        yield AssistantMessage([ToolUseBlock("2", "mcp__navigateur__browser_navigate", {"url": "https://a.fr"})], "m")
        if self.brief in barrieres:
            await barrieres[self.brief].wait()
        if self.brief == "casse":
            raise RuntimeError("boum")
        yield AssistantMessage([TextBlock("brouillon")], "m")
        usage = {"m": {"inputTokens": 1200, "cacheCreationInputTokens": 300, "cacheReadInputTokens": 90000, "outputTokens": 500}}
        yield ResultMessage("success", 1, 1, False, 1, "s", result=f"résultat de {self.brief}",
                            model_usage=usage if self.brief == "jetons" else None)


async def sans_connecteurs(*_a, **_k) -> None: ...

fond.ClaudeSDKClient = FauxClient
fond.preparer_connecteurs = sans_connecteurs
compteur.noter = lambda *_a, **_k: None  # ne pas écrire dans les vraies dépenses
fond.Taches._options = lambda self, titre: None


async def main() -> None:
    standard = fond.Standard()
    journal: list[str] = []
    taches = fond.Taches(standard, journal.append)
    verifier("plafond par défaut : cinq agents à la fois", config.TACHES_MAX, 5)
    verifier("rien au départ (voix)", taches.lister(), "Aucune tâche en cours.")
    verifier("rien au départ (tableau)", taches.etat(), [])
    verifier("bilan au départ", taches.bilan(), {"lancees": 0, "finies": 0, "echecs": 0, "annulees": 0, "en_cours": 0})

    config.TACHES_MAX = 3
    for brief in ("alpha", "bravo"):
        barrieres[brief] = asyncio.Event()
        taches.lancer(f"Tâche {brief}", brief)
    await asyncio.sleep(0.05)

    etat = taches.etat()
    verifier("deux agents en parallèle", [(t["numero"], t["statut"]) for t in etat], [(1, "en cours"), (2, "en cours")])
    verifier("chacun a son action en cours", {t["action"] for t in etat}, {"Navigateur · ouvre une page · https://a.fr"})
    verifier("et son nombre d'étapes", [t["etapes"] for t in etat], [2, 2])
    verifier("la voix décrit chacun", [("en ce moment : Navigateur" in ligne) for ligne in taches.lister().splitlines()],
             [True, True])

    barrieres["casse"] = asyncio.Event()
    taches.lancer("Tâche cassée", "casse")
    verifier("le plafond est respecté", taches.lancer("Quatrième", "delta").startswith("Déjà 3 tâches"), True)
    verifier("un refus ne crée pas d'agent", len(taches.etat()), 3)

    barrieres["alpha"].set()  # alpha finit bien
    barrieres["casse"].set()  # celle-là plante
    await asyncio.sleep(0.1)
    par_titre = {t["titre"]: t for t in taches.etat()}
    verifier("alpha est finie", par_titre["Tâche alpha"]["statut"], "finie")
    verifier("le bilan compte les agents (3 lancés, 1 fini, 1 en échec, 1 en cours)", taches.bilan(),
             {"lancees": 3, "finies": 1, "echecs": 1, "annulees": 0, "en_cours": 1})
    verifier("chaque agent a son nombre de jetons (0 : le faux client n'en annonce aucun)",
             [t["jetons"] for t in taches.etat()], [0, 0, 0])
    verifier("elle ne fait plus rien", par_titre["Tâche alpha"]["action"], "")
    verifier("elle garde sa durée et son heure de fin", (par_titre["Tâche alpha"]["secondes"] >= 0,
                                                         par_titre["Tâche alpha"]["fini_il_y_a"] is not None), (True, True))
    verifier("la cassée est en échec", par_titre["Tâche cassée"]["statut"], "échec")
    verifier("bravo tourne toujours", par_titre["Tâche bravo"]["statut"], "en cours")
    verifier("les vivants d'abord, puis les finis", [t["statut"] for t in taches.etat()][0], "en cours")

    textes = [standard.prendre().texte, standard.prendre().texte]
    verifier("Steve est prévenu de la réussite", any("résultat de alpha" in t for t in textes), True)
    verifier("Steve est prévenu de l'échec", any("a échoué" in t for t in textes), True)

    verifier("la voix cite aussi les terminées", "Terminées récemment" in taches.lister(), True)
    verifier("annuler bravo", taches.annuler(2), "Tâche « Tâche bravo » annulée.")
    verifier("annuler une tâche déjà partie", taches.annuler(2).startswith("Pas de tâche"), True)
    await asyncio.sleep(0.05)
    statuts = sorted(t["statut"] for t in taches.etat())
    verifier("bravo est annulée, une seule fois dans l'histoire", statuts, ["annulée", "finie", "échec"])
    verifier("plus aucun vivant", "en cours" in statuts, False)
    verifier("annulée = pas d'annonce", standard.en_attente(), False)

    verifier("le bilan final : l'annulation compte, une seule fois", taches.bilan(),
             {"lancees": 3, "finies": 1, "echecs": 1, "annulees": 1, "en_cours": 0})
    ecran_bilan = tableau.Tableau(types.SimpleNamespace(nom="veille", oreilles=None, taches=taches))
    verifier("le tableau montre le bilan", ecran_bilan._bilan_agents()["lancees"], 3)
    verifier("sans agents branchés, pas de bilan",
             tableau.Tableau(types.SimpleNamespace(nom="v", oreilles=None))._bilan_agents(), {})
    verifier("un bilan qui plante ne casse pas la page",
             tableau.Tableau(types.SimpleNamespace(nom="v", oreilles=None, taches=types.SimpleNamespace(bilan=lambda: 1 / 0)))
             ._bilan_agents(), {})

    taches.lancer("Tâche jetons", "jetons")
    await asyncio.sleep(0.1)
    jetons = next(t["jetons"] for t in taches.etat() if t["titre"] == "Tâche jetons")
    verifier("les jetons d'un agent : ce qu'il a lu et écrit (le cache relu n'est pas compté)", jetons, 1200 + 300 + 500)
    standard.prendre()

    taches._histoire.clear()
    for i in range(12):
        taches._histoire.appendleft(fond.Tache(i, f"t{i}", fond.datetime.now(), statut="finie", fin=fond.datetime.now()))
    verifier("l'histoire est bornée", len(taches.etat()), 8)

    # --- 3. Ce que le tableau de bord en voit ---------------------------------------------------------------
    ecran = tableau.Tableau(types.SimpleNamespace(nom="veille", oreilles=None, taches=taches))
    verifier("le tableau montre les agents", len(ecran._agents()), 8)
    verifier("sans agents branchés, une liste vide", tableau.Tableau(types.SimpleNamespace(nom="veille", oreilles=None))._agents(), [])
    verifier("un agent qui plante en se lisant ne casse pas la page",
             tableau.Tableau(types.SimpleNamespace(nom="v", oreilles=None, taches=types.SimpleNamespace(etat=lambda: 1 / 0)))._agents(), [])

    # --- 4. Le × d'un agent sur le tableau de bord (un autre fil que Steve) -------------------------------------
    barrieres["charlie"] = asyncio.Event()
    taches.lancer("Tâche charlie", "charlie")
    await asyncio.sleep(0.05)
    numero = next(t["numero"] for t in taches.etat() if t["titre"] == "Tâche charlie")
    verifier("le × : l'action est reconnue", callable(ecran.action(f"annuler-agent-{numero}")), True)
    verifier("une action inventée n'existe pas", (ecran.action("annuler-agent-x"), ecran.action("annuler-agent-1;rm"),
                                                  ecran.action("rm -rf")), (None, None, None))
    verifier("sans agents branchés, pas de ×", tableau.Tableau(types.SimpleNamespace(nom="v", oreilles=None)).action("annuler-agent-1"), None)
    await asyncio.to_thread(ecran.action(f"annuler-agent-{numero}"))
    await asyncio.sleep(0.1)
    verifier("charlie est annulée par le fil du serveur", next(t["statut"] for t in taches.etat() if t["titre"] == "Tâche charlie"),
             "annulée")
    verifier("un numéro qui n'existe pas", taches.annuler_depuis_un_fil(999), False)
    ecran.actions["pause"] = lambda: None
    verifier("les boutons de la barre marchent toujours", callable(ecran.action("pause")), True)


asyncio.run(main())
print(f"\n{'✅' if not echecs else '❌'} fond : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
