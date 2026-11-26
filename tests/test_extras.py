"""Les serveurs MCP gratuits en plus (extras.py) : lesquels se branchent, comment le Gardien les traite, et ton
mcp.json à toi.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_extras.py

Aucun serveur n'est lancé ni appelé : on vérifie ce que Steve donnerait à Claude Code. Le mcp.json de test est
écrit dans un dossier temporaire, jamais dans ~/Jervis.
"""

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, config, extras, permissions  # noqa: E402
from jervis.permissions import Decision  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-extras-"))
config.MCP_PERSO = TMP / "mcp.json"
config.MCP_GRATUITS = "tous"
config.RECHERCHE_OUTILS = ""
DISTANTS = {"deepwiki", "context7", "microsoft", "aws", "cloudflare", "huggingface"}
NPX = {"apple", "youtube", "nextjs"}
UVX = {"wikipedia", "arxiv"}

# --- 1. JERVIS_MCP_GRATUITS ---------------------------------------------------------------------------------------
for reglage, attendu in [("tous", set(extras.CATALOGUE)), ("aucun", set()), ("", set()), ("non", set()),
                         ("deepwiki, context7,inconnu", {"deepwiki", "context7"}), ("WIKIPEDIA", {"wikipedia"})]:
    config.MCP_GRATUITS = reglage
    verifier(f"JERVIS_MCP_GRATUITS={reglage!r}", extras.voulus(), attendu)
config.MCP_GRATUITS = "tous"
verifier("le catalogue : 11 serveurs", set(extras.CATALOGUE), DISTANTS | NPX | UVX)

# --- 2. Ce qui se branche : selon npx et uvx ------------------------------------------------------------------------
vrai_programme = extras._programme
for installes, attendus in [({"npx"}, DISTANTS | NPX), (set(), DISTANTS), ({"npx", "uvx"}, DISTANTS | NPX | UVX)]:
    extras._programme = lambda nom, i=installes: f"/opt/outils/bin/{nom}" if nom in i else None
    verifier(f"branchés avec {sorted(installes) or 'ni npx ni uvx'}", set(extras.serveurs()), attendus)
s = extras.serveurs()
verifier("un distant : HTTP, chargé à la demande", s["context7"],
         {"type": "http", "url": "https://mcp.context7.com/mcp", "alwaysLoad": False})
verifier("npx : version épinglée, -y", s["apple"]["args"], ["-y", "@kimsungwhee/apple-docs-mcp@1.0.26"])
verifier("npx : son dossier dans le PATH", s["apple"]["env"]["PATH"].split(":")[0], "/opt/outils/bin")
verifier("uvx : version épinglée, Wikipédia en français", (s["wikipedia"]["command"], s["wikipedia"]["args"]),
         ("/opt/outils/bin/uvx", ["wikipedia-mcp==2.0.1", "--language", "fr"]))
verifier("tous chargés à la demande", {n: v["alwaysLoad"] for n, v in s.items()}, {n: False for n in s})
verifier("chaque version est épinglée", all("@" in v.paquet[1][1:] or "==" in v.paquet[1]
                                            for v in extras.CATALOGUE.values() if v.paquet), True)
config.MCP_GRATUITS = "aucun"
verifier("aucun : rien", extras.serveurs(), {})
verifier("aucun : pas de consigne", extras.consigne(), "")
config.MCP_GRATUITS = "tous"

# --- 3. Ton mcp.json --------------------------------------------------------------------------------------------
verifier("pas de mcp.json", extras.perso(), {})
config.MCP_PERSO.write_text("{ pas du json", encoding="utf-8")
verifier("mcp.json illisible : ignoré", extras.perso(), {})
config.MCP_PERSO.write_text(json.dumps({"mcpServers": {
    "notes": {"type": "stdio", "command": "/usr/local/bin/notes-mcp"},
    "context7": {"type": "http", "url": "https://exemple.fr/mcp"},
    "outil": {"type": "http", "url": "https://x/mcp", "alwaysLoad": True},
    "mac": {"type": "stdio", "command": "/tmp/faux"}, "jervis": {"type": "stdio", "command": "/tmp/faux"},
    "navigateur": {"command": "x"}, "ecran": {"command": "x"}, "claude-in-chrome": {"command": "x"},
    "claude_ai_Gmail": {"command": "x"}, "Avec Espace": {"command": "x"}, "a__b": {"command": "x"},
    "pas_un_dict": "x"}}), encoding="utf-8")
perso = extras.perso()
verifier("mcp.json : les noms réservés, invalides ou mal formés sont ignorés", sorted(perso), ["context7", "notes", "outil"])
verifier("mcp.json : chargé à la demande par défaut", perso["notes"]["alwaysLoad"], False)
verifier("mcp.json : ton alwaysLoad est respecté", perso["outil"]["alwaysLoad"], True)
s = extras.serveurs()
verifier("mcp.json : le tien remplace celui du catalogue", s["context7"]["url"], "https://exemple.fr/mcp")
verifier("mcp.json : branché avec les autres", "notes" in s and "deepwiki" in s, True)
verifier("mcp.json : tes noms ne sont jamais coupés", {"notes", "outil", "deepwiki"} <= extras.noms(), True)
verifier("consigne : tes serveurs y sont", "- notes : un serveur à lui (mcp.json)" in extras.consigne(), True)
verifier("consigne : la doc d'abord", "préfère-les à une recherche web" in extras.consigne(), True)

# --- 4. Qui est un extra, et qui lit -------------------------------------------------------------------------------
for outil, attendu in [("mcp__deepwiki__ask_wiki_question", ("deepwiki", "ask_wiki_question")),
                       ("mcp__aws__aws___read_documentation", ("aws", "aws___read_documentation")),
                       ("mcp__context7__query-docs", ("context7", "query-docs")),
                       ("mcp__notes__search_notes", ("notes", "search_notes")),
                       ("mcp__mac__see", None), ("mcp__jervis__retenir", None), ("mcp__inconnu__get_x", None),
                       ("mcp__claude_ai_Gmail__send_message", None), ("Bash", None), ("mcp__deepwiki__", None)]:
    verifier(f"extra_de({outil})", extras.extra_de(outil), attendu)
for outil, attendu in [("mcp__deepwiki__ask_wiki_question", True), ("mcp__aws__aws___read_documentation", True),
                       ("mcp__nextjs__nextjs_docs", True), ("mcp__nextjs__upgrade_nextjs_16", False),
                       ("mcp__nextjs__browser_eval", False), ("mcp__nextjs__init", False),
                       ("mcp__huggingface__hub_repo_search", True), ("mcp__youtube__get_transcript", True),
                       ("mcp__arxiv__download_paper", True), ("mcp__arxiv__add_watch", False),
                       ("mcp__apple__search_apple_docs", True), ("mcp__deepwiki__delete_everything", False),
                       # le context7 de ton mcp.json n'est plus celui du catalogue : jugé sur le nom de l'action
                       ("mcp__context7__query-docs", True), ("mcp__context7__resolve-library-id", True),
                       ("mcp__context7__publish", False),
                       ("mcp__notes__search_notes", True), ("mcp__notes__get-page", True),
                       ("mcp__notes__delete_note", False), ("mcp__notes__create_note", False),
                       ("mcp__notes__searchandreplace", False)]:
    verifier(f"lecture : {outil}", extras.est_lecture(outil), attendu)
    verifier(f"lecture (Gardien) : {outil}", permissions.est_lecture(outil), attendu)

# --- 5. La politique : la même dans tous les modes -----------------------------------------------------------------
for mode in ("autonome", "confiance", "essentielles", "toutes"):
    for outil, attendu in [("mcp__context7__query-docs", Decision.OUI), ("mcp__wikipedia__search_wikipedia", Decision.OUI),
                           ("mcp__nextjs__upgrade_nextjs_16", Decision.DEMANDER),
                           ("mcp__nextjs__browser_eval", Decision.DEMANDER),
                           ("mcp__notes__create_note", Decision.DEMANDER), ("mcp__notes__search_notes", Decision.OUI)]:
        verifier(f"{mode} : {outil}", permissions.politique(outil, {}, mode=mode), attendu)
    # ses serveurs à lui ne changent pas
    verifier(f"{mode} : Gmail lire", permissions.politique("mcp__claude_ai_Gmail__search_threads", {}, mode=mode),
             Decision.OUI)
verifier("ce qu'ils renvoient vient d'ailleurs", permissions.lit_dehors("mcp__deepwiki__ask_wiki_question"), True)


# --- 6. Le Gardien : méfiance, et mcp.json jamais écrit par un outil ------------------------------------------------
async def gardien() -> None:
    questions: list[str] = []

    async def repondre(q: str) -> bool:
        questions.append(q)
        return False

    config.CONFIRMATIONS = "autonome"
    g = permissions.Gardien(repondre)
    g.nouvelle_demande(mefiance=True)
    r = await g("mcp__context7__query-docs", {"libraryId": "/vercel/next.js", "query": "cache"}, None)
    verifier("méfiance : lire la doc passe seul", (type(r).__name__, len(questions)), ("PermissionResultAllow", 0))
    r = await g("mcp__nextjs__upgrade_nextjs_16", {"project_path": "/x"}, None)
    verifier("autonome : une action qui n'est pas une lecture se demande",
             (type(r).__name__, len(questions)), ("PermissionResultDeny", 1))
    verifier("la question nomme l'action et le serveur", questions[-1],
             "Je dois utiliser nextjs, action upgrade nextjs 16. Je peux ?")
    g.nouvelle_demande()
    for chemin in (str(config.MCP_PERSO), str(config.MCP_PERSO).upper(), f"{TMP}/./mcp.json"):
        for outil in ("Write", "Edit"):
            r = await g(outil, {"file_path": chemin, "content": "{}", "old_string": "a", "new_string": "b"}, None)
            verifier(f"{outil} {chemin} : refusé, sans question", (type(r).__name__, "à la main" in r.message),
                     ("PermissionResultDeny", True))
    verifier("mcp.json : rien n'a été écrit", "notes" in config.MCP_PERSO.read_text(encoding="utf-8"), True)
    verifier("un autre fichier du dossier reste permis", permissions.vise_mcp_perso(str(TMP / "notes.json")), False)


asyncio.run(gardien())

# --- 7. Dans la cage : ~/Jervis/mcp.json fermé ---------------------------------------------------------------------
verifier("cage : mcp.json fermé en écriture", str(permissions._RACINE / "mcp.json") in permissions._cage_profil(), True)
verifier("mcp.json vit à la racine de Steve", extras.config.MCP_PERSO.name, "mcp.json")


# --- 8. Le cerveau : ses serveurs toujours chargés, les extras à la demande, rien de coupé ------------------------
siens = {"jervis": {"type": "sdk", "name": "jervis", "instance": object()},
         "mac": {"type": "stdio", "command": "/x/npx", "args": []}}
tout = brain.avec_extras(siens)
verifier("les siens : toujours chargés", (tout["jervis"]["alwaysLoad"], tout["mac"]["alwaysLoad"]), (True, True))
verifier("le serveur maison garde son instance", tout["jervis"]["instance"] is siens["jervis"]["instance"], True)
verifier("les siens ne sont pas modifiés en place", "alwaysLoad" in siens["mac"], False)
verifier("les extras : à la demande", (tout["deepwiki"]["alwaysLoad"], tout["notes"]["alwaysLoad"]), (False, False))
verifier("RECHERCHE_OUTILS vide : le réglage de Claude Code", brain.env_outils(), {})
config.RECHERCHE_OUTILS = "auto:5"
verifier("RECHERCHE_OUTILS : passé à Claude Code", brain.env_outils(), {"ENABLE_TOOL_SEARCH": "auto:5"})
config.RECHERCHE_OUTILS = ""
verifier("ses consignes parlent des extras", "deepwiki : la doc de n'importe quel dépôt" in brain._consignes(False), True)


class FauxClient:
    def __init__(self, noms: list[str]) -> None:
        self.noms, self.coupes = noms, []

    async def get_mcp_status(self):
        return {"mcpServers": [{"name": n, "status": "connected"} for n in self.noms]}

    async def toggle_mcp_server(self, nom: str, actif: bool) -> None:
        self.coupes.append(nom)


client = FauxClient(["jervis", "deepwiki", "notes", "arxiv", "claude.ai Canva", "intrus"])
asyncio.run(brain.couper_intrus(client))
verifier("couper_intrus : les extras restent, l'intrus part", sorted(client.coupes), ["claude.ai Canva", "intrus"])

extras._programme = vrai_programme
print(f"\n{'✅' if not echecs else '❌'} serveurs MCP en plus : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
