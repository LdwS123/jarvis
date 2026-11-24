"""Le graphe de ce qu'il sait (Graphiti) : ce qui y entre, comment il répond, et qu'il ne casse rien s'il manque.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_graphe.py

Sans Claude ni Ollama : de faux clients jouent leurs réponses. La base embarquée (FalkorDB) est essayée pour de
vrai dans un dossier temporaire, si elle peut démarrer ici (il lui faut un petit serveur local).
"""

import asyncio
import os
import tempfile
import threading
import types
from datetime import datetime, timezone
from pathlib import Path

threading.Timer(90, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import claude_agent_sdk  # noqa: E402
from claude_agent_sdk import AssistantMessage, TextBlock  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from jervis import config  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="jervis-test-graphe-"))
config.FICHIER_GRAPHE = TMP / "graphe.db"   # jamais le vrai graphe

from jervis import graphe, outils, permissions  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def texte(resultat: dict) -> str:
    return resultat["content"][0]["text"]


# --- 1. Rien ne sort : télémétrie coupée avant l'import, empreintes à la taille du modèle local -----------------
verifier("télémétrie de Graphiti coupée", os.environ.get("GRAPHITI_TELEMETRY_ENABLED"), "false")
verifier("taille des empreintes = celle d'embeddinggemma", os.environ.get("EMBEDDING_DIM"), str(config.GRAPHE_DIM))
verifier("Ollama est cherché sur le Mac", config.OLLAMA_URL.startswith(("http://127.0.0.1", "http://localhost")), True)
if graphe.ACTIF:
    from graphiti_core.telemetry.telemetry import is_telemetry_enabled
    verifier("Graphiti se voit sans télémétrie", is_telemetry_enabled(), False)

# --- 2. Les petits morceaux -----------------------------------------------------------------------------------
verifier("JSON : entouré de ```json", graphe.lire_json('```json\n{"a": 1}\n```'), {"a": 1})
verifier("JSON : après une phrase", graphe.lire_json('Voilà : {"a": {"b": [1, 2]}} fini'), {"a": {"b": [1, 2]}})
try:
    graphe.lire_json("je ne sais pas")
    verifier("JSON : rien → erreur", "pas d'erreur", "erreur")
except ValueError:
    verifier("JSON : rien → erreur", True, True)
verifier("cosinus : même direction", round(graphe.cosinus([1, 0], [2, 0]), 3), 1.0)
verifier("cosinus : à angle droit", graphe.cosinus([1, 0], [0, 3]), 0.0)
verifier("cosinus : vecteur nul", graphe.cosinus([0, 0], [1, 1]), 0.0)

lien = types.SimpleNamespace
jour = datetime(2026, 3, 1, tzinfo=timezone.utc)
verifier("liens : le vrai d'abord, le passé ensuite et daté", graphe.decrire([
    lien(fact="Léa habite à Lyon.", valid_at=jour, invalid_at=datetime(2026, 9, 1), expired_at=None),
    lien(fact="Léa habite à Bordeaux.", valid_at=datetime(2026, 9, 1), invalid_at=None, expired_at=None),
    lien(fact="Il travaille sur Oqro.", valid_at=None, invalid_at=None, expired_at=None)]),
    ["- Léa habite à Bordeaux. (depuis le 01/09/2026)", "- Il travaille sur Oqro.",
     "- plus vrai depuis le 01/09/2026 : Léa habite à Lyon."])
verifier("liens : jamais plus que ce qu'on peut dire",
         len(graphe.decrire([lien(fact=f"f{i}", valid_at=None, invalid_at=None, expired_at=None) for i in range(40)])),
         graphe.VU_MAX)

# --- 3. Le graphe est interne : le chercher ne compte pas comme un texte venu d'ailleurs ----------------------
verifier("chercher_liens : une lecture", permissions.est_lecture("mcp__jervis__chercher_liens"), True)
verifier("chercher_liens : pas du dehors", permissions.lit_dehors("mcp__jervis__chercher_liens"), False)
noms = {getattr(o, "name", None) for o in outils.OUTILS}
verifier("chercher_liens n'existe que si le graphe est là", "chercher_liens" in noms, graphe.ACTIF)


async def tester_clients():
    if not graphe.ACTIF:
        return
    ClaudeLecteur, EmpreintesOllama, Classement = graphe._classes()
    from graphiti_core.prompts.models import Message

    # Claude lit « qui est qui » : une réponse mal formée, il réessaie une fois en sachant pourquoi.
    reponses = iter(["désolé, pas de JSON", 'Voilà {"nom": "Léa", "lien": "sœur"}'])
    vus = []

    async def faux_query(prompt, options):
        vus.append((prompt, options))
        yield AssistantMessage(content=[TextBlock(next(reponses))], model="haiku")
    claude_agent_sdk.query = faux_query
    lecteur = graphe._classes()[0]()   # _classes relit claude_agent_sdk.query

    class Lien(BaseModel):
        nom: str
        lien: str
    donnees = await lecteur._generate_response([Message(role="system", content="Tu extrais des liens."),
                                                Message(role="user", content="Sa sœur s'appelle Léa.")], Lien)
    verifier("lecteur : la réponse validée", donnees, {"nom": "Léa", "lien": "sœur"})
    verifier("lecteur : deux essais, le second sait pourquoi", (len(vus), "Essai précédent refusé" in vus[1][0]),
             (2, True))
    options = vus[0][1]
    verifier("lecteur : un petit Claude sans outils ni connecteurs",
             (options.model, options.tools, options.max_turns, options.env.get("ENABLE_CLAUDEAI_MCP_SERVERS")),
             (config.MODELE_GRAPHE, [], 1, "false"))
    verifier("lecteur : la consigne système passe en système",
             ("Tu extrais des liens." in options.system_prompt, "Tu extrais" in vus[0][0]), (True, False))

    reponses = iter(["non", "toujours pas"])
    try:
        await lecteur._generate_response([Message(role="user", content="x")], Lien)
        verifier("lecteur : deux échecs → erreur", "pas d'erreur", "erreur")
    except ValueError:
        verifier("lecteur : deux échecs → erreur", True, True)

    # Le classement final : par ressemblance d'empreintes, le plus proche d'abord.
    class FausseEmpreinte:
        async def create_batch(self, phrases):
            return [[1.0, 0.0] if "Lyon" in p else [0.0, 1.0] for p in phrases]
    classement = Classement(FausseEmpreinte())
    verifier("classement : le plus proche d'abord",
             [p for p, _ in await classement.rank("à Lyon ?", ["Il aime le ski", "Léa vit à Lyon"])],
             ["Léa vit à Lyon", "Il aime le ski"])
    verifier("classement : rien à classer", await classement.rank("x", []), [])

    # Ollama éteint : une phrase claire, le graphe se met en pause, l'outil renvoie vers la mémoire.
    import httpx

    async def ollama_eteint():
        raise httpx.ConnectError("All connection attempts failed")
    ouvrir, graphe._ouvrir = graphe._ouvrir, ollama_eteint
    reponse = texte(await graphe.chercher_liens.handler({"question": "où habite Léa ?"}))
    verifier("Ollama éteint : il le dit et répond avec sa mémoire",
             ("ollama serve" in reponse, "ta mémoire" in reponse, graphe.disponible()), (True, True, False))
    verifier("Ollama éteint : ajouter n'essaie même plus", await graphe.ajouter(["Il aime le ski"]), 0)
    verifier("Ollama éteint : oublier ne coince pas", await graphe.oublier("ski"), 0)
    graphe._panne, graphe._ouvrir = None, ouvrir

    # Une autre erreur ne vaut que pour cette fois : le graphe n'est pas mis en pause.
    async def autre_panne():
        raise RuntimeError("index cassé")
    graphe._ouvrir = autre_panne
    reponse = texte(await graphe.chercher_liens.handler({"question": "x"}))
    verifier("autre panne : dite, sans mettre le graphe en pause", ("index cassé" in reponse, graphe.disponible()),
             (True, True))
    graphe._ouvrir = ouvrir


async def tester_oublier_dans_outils():
    """L'outil oublier nettoie la mémoire ET le graphe."""
    outils.MEMOIRE = TMP / "memoire.md"
    outils.MEMOIRE.write_text("# Mémoire\n\n## Proches\n- Sa sœur Léa habite à Lyon.\n- Il aime le ski.\n",
                              encoding="utf-8")
    cibles = []

    async def faux_oublier(t):
        cibles.append(t)
        return 2
    vrai, graphe.oublier = graphe.oublier, faux_oublier
    reponse = texte(await outils.oublier.handler({"texte": "Léa"}))
    verifier("oublier : la mémoire et le graphe", (cibles, "1 ligne(s)" in reponse, "2 lien(s) du graphe" in reponse),
             (["Léa"], True, True))

    async def graphe_en_panne(_t):
        raise RuntimeError("base fermée")
    graphe.oublier = graphe_en_panne
    reponse = texte(await outils.oublier.handler({"texte": "ski"}))
    verifier("oublier : graphe en panne, la mémoire est quand même nettoyée",
             ("1 ligne(s)" in reponse, "pas pu être nettoyé" in reponse, "ski" in outils.MEMOIRE.read_text()),
             (True, True, False))
    graphe.oublier = vrai


async def tester_base():
    """La vraie base embarquée, dans un dossier temporaire : oublier, sauver sur le disque, vider."""
    if not graphe.ACTIF:
        return False
    try:
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from redislite.async_falkordb_client import AsyncFalkorDB
        base = AsyncFalkorDB(str(config.FICHIER_GRAPHE))
    except Exception as e:  # pas de petit serveur local possible ici (bac à sable)
        print(f"   (base embarquée non testée : {e!r:.80})")
        return False

    try:
        return await _dans_la_base(Graphiti, FalkorDriver(falkor_db=base, database="steve"))
    finally:
        graphe._graphe = None
        await base.client.close()   # arrête le petit serveur


async def _dans_la_base(Graphiti, pilote):
    ClaudeLecteur, EmpreintesOllama, Classement = graphe._classes()   # construits, jamais appelés ici
    graphe._graphe = Graphiti(graph_driver=pilote, llm_client=ClaudeLecteur(), embedder=EmpreintesOllama(),
                              cross_encoder=Classement(EmpreintesOllama()))
    await pilote.execute_query(
        "CREATE (:Entity {name:'Léa', uuid:'1'})-[:RELATES_TO {fact:'Léa habite à Lyon', uuid:'a'}]->"
        "(:Entity {name:'Lyon', uuid:'2'}), (:Entity {name:'Marc', uuid:'3'})-[:RELATES_TO "
        "{fact:'MARC bosse sur Oqro', uuid:'b'}]->(:Entity {name:'Oqro', uuid:'4'})")
    verifier("base : oublier ne touche que ce qui contient le texte (sans casse)", await graphe.oublier("marc"), 1)
    lignes, _, _ = await pilote.execute_query("MATCH ()-[e:RELATES_TO]->() RETURN e.fact AS f")
    verifier("base : le reste est là", [l["f"] for l in lignes], ["Léa habite à Lyon"])
    verifier("base : écrite sur le disque après le changement", config.FICHIER_GRAPHE.exists(), True)
    verifier("base : oublier ce qui n'existe pas", await graphe.oublier("Bordeaux"), 0)
    verifier("base : vider", await graphe.effacer(), True)
    lignes, _, _ = await pilote.execute_query("MATCH (n) RETURN count(n) AS n")
    verifier("base : vide", lignes[0]["n"], 0)
    return True


asyncio.run(tester_clients())
asyncio.run(tester_oublier_dans_outils())
base_testee = asyncio.run(tester_base())

# --- 4. Coupé : rien ne se passe, rien ne plante ----------------------------------------------------------------
graphe.ACTIF = False
verifier("coupé : pas disponible", graphe.disponible(), False)
verifier("coupé : ajouter ne fait rien", asyncio.run(graphe.ajouter(["Il aime le ski"])), 0)
verifier("coupé : chercher ne trouve rien", asyncio.run(graphe.chercher("ski")), [])
verifier("coupé : vider ne fait rien", asyncio.run(graphe.effacer()), False)
verifier("coupé : l'outil renvoie vers la mémoire",
         "ta mémoire" in texte(asyncio.run(graphe.chercher_liens.handler({"question": "x"}))), True)

print(f"\n{'✅' if not echecs else '❌'} graphe : {total - len(echecs)}/{total} vérifications OK", flush=True)
if not base_testee:
    print("   (base embarquée non testée ici)")
os._exit(0 if not echecs else 1)
