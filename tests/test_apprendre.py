"""Ce qu'il apprend de toi en plus de son carnet : son lexique (les mots qu'il entend mal) et ses savoir-faire.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_apprendre.py

Sans Claude, sans réseau, sans micro : tout s'écrit dans un dossier temporaire (jamais ton vrai lexique, tes
vraies recettes, ta vraie mémoire).
"""

import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock  # noqa: E402

from jervis import brain, compteur, config, fond, graphe, lecons, lexique, outils, savoir_faire  # noqa: E402
from jervis.permissions import (A_MONTRER, ECRIT_POUR_PLUS_TARD, JERVIS_A_CONFIRMER, decrire,  # noqa: E402
                                est_lecture, lit_dehors, tel_que_montre)

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-apprendre-"))
lexique.FICHIER = TMP / "lexique.md"
savoir_faire.DOSSIER = TMP / "savoir-faire"
lecons.FICHIER = TMP / "lecons.md"
outils.MEMOIRE = TMP / "memoire.md"
outils.JOURNAL = TMP / "conversations.jsonl"
config.FICHIER_GRAPHE = TMP / "graphe.db"
outils.noter_echange = lambda *a, **k: None
compteur.noter = lambda *_a, **_k: None
graphe.disponible = lambda: False
J = f"mcp__{outils.NOM_SERVEUR}__"

# --- 1. Le lexique -----------------------------------------------------------------------------------------------
verifier("lexique vide : la phrase passe telle quelle", lexique.corriger("parle-moi de au crow"), "parle-moi de au crow")
verifier("un mot noté", lexique.noter_mot("au crow", "Oqro"), "Noté : « au crow » voudra dire « Oqro ».")
verifier("… corrigé, quelle que soit la casse ou l'espace", lexique.corriger("Le site d'Au  Crow est en ligne ?"),
         "Le site d'Oqro est en ligne ?")
verifier("… avec un tiret aussi", lexique.corriger("au-crow, ça marche"), "Oqro, ça marche")
verifier("… mais seulement en mots entiers", lexique.corriger("au crowd"), "au crowd")
verifier("noté deux fois : il le savait déjà", lexique.noter_mot("AU crow", "Oqro"), "Je le savais déjà.")
lexique.noter_mot("stance heure", "Stancer")
lexique.noter_mot("stance", "Stancer")
verifier("le plus long d'abord", lexique.corriger("chez stance heure"), "chez Stancer")
verifier("une nouvelle correction remplace l'ancienne",
         lexique.noter_mot("stance heure", "Stancer SAS").endswith("(l'ancienne correction est remplacée)."), True)
verifier("… et c'est la nouvelle qui sert", lexique.corriger("stance heure"), "Stancer SAS")
verifier("… sans doublon dans le fichier", [e for e, _ in lexique.paires()].count("stance heure"), 1)

for entendu, voulu, quoi in [("non", "oui", "retourner un non"),
                             ("n'envoie pas", "envoie", "retourner une négation"),
                             ("arrête", "continue", "désamorcer un stop"),
                             ("mon compte", "evil.com/x", "glisser un lien"),
                             ("mon mail", "pirate@exemple.fr", "glisser une adresse"),
                             ("le numéro", "06 12 34 56 78", "glisser un numéro"),
                             ("steve", "Bob", "réécrire son nom"),
                             ("ab", "Abc", "trop court"),
                             ("Oqro", "oqro", "le même mot"),
                             ("« ok »", "x", "des guillemets")]:
    verifier(f"lexique : refusé pour {quoi}", lexique.noter_mot(entendu, voulu).startswith("Refusé"), True)
verifier("… et rien de tout ça n'est entré", len(lexique.paires()), 3)

# Une ligne écrite à la main (tu corriges le fichier toi-même) est lue comme les autres, date ou pas.
with lexique.FICHIER.open("a", encoding="utf-8") as f:
    f.write("- « raisin bank » → « Raisin »\n")
verifier("une ligne écrite à la main compte", lexique.corriger("chez raisin bank"), "chez Raisin")
date_avant = lexique.FICHIER.read_text(encoding="utf-8").split("« au crow » → « Oqro »  (")[1][:10]
lexique.noter_mot("en vidia", "Nvidia ")  # réécrit le fichier
verifier("réécrire le fichier garde la date des anciennes lignes",
         f"« au crow » → « Oqro »  ({date_avant})" in lexique.FICHIER.read_text(encoding="utf-8"), True)
verifier("dans ses consignes, la liste des mots", "« au crow » = Oqro" in lexique.pour_consignes(), True)
config.LEXIQUE = False
verifier("JERVIS_LEXIQUE=non : plus de correction", lexique.corriger("au crow"), "au crow")
config.LEXIQUE = True
verifier("oublier_mot", (lexique.oublier_mot("Nvidia"), lexique.oublier_mot("en vidia"), lexique.corriger("en vidia")),
         (False, True, "en vidia"))
max_avant = config.LEXIQUE_MAX
config.LEXIQUE_MAX = 4
for i in range(6):
    lexique.noter_mot(f"motmal{i}", f"Mot{i}")
verifier("au-delà de LEXIQUE_MAX, les plus anciens s'en vont", [v for _, v in lexique.paires()],
         ["Mot2", "Mot3", "Mot4", "Mot5"])
config.LEXIQUE_MAX = max_avant
lexique.effacer()
lexique.noter_mot("au crow", "Oqro")
r = asyncio.run(lexique.outil_noter_mot.handler({"entendu": "non", "voulu": "oui"}))
verifier("l'outil : un refus est une erreur, pour qu'il le voie", r.get("is_error"), True)
r = asyncio.run(lexique.outil_noter_mot.handler({"entendu": "ok là", "voulu": "Oqro"}))
verifier("… « ok » porte le sens d'une phrase : refusé aussi", r.get("is_error"), True)

# --- 2. Les savoir-faire -----------------------------------------------------------------------------------------
RECETTE = ("1. Lire la liste des publications : si le site y est, passer au suivant.\n"
           "2. Ouvrir la page d'inscription dans son Chrome.\n"
           "3. Vérifier sur la page qu'il n'y a aucun prix.\n"
           "4. Envoyer le logo depuis le dossier espace, pas depuis tmp.\n"
           "5. Noter le site avec noter_publication.")
verifier("une recette notée", savoir_faire.noter("Inscrire OQRO sur un annuaire", "un annuaire ou un site de lancement",
                                                 RECETTE), "Recette notée.")
fiche = savoir_faire.DOSSIER / "inscrire-oqro-sur-un-annuaire.md"
texte = fiche.read_text(encoding="utf-8")
verifier("… dans son propre fichier, titre, quand, étapes numérotées",
         (texte.startswith("# Inscrire OQRO sur un annuaire\nQuand : un annuaire ou un site de lancement\n"),
          "\n3. Vérifier sur la page qu'il n'y a aucun prix.\n" in texte), (True, True))
verifier("il la retrouve par un mot du titre", "aucun prix" in savoir_faire.chercher("annuaire"), True)
verifier("… ou du « quand »", "aucun prix" in savoir_faire.chercher("site de lancement"), True)
verifier("… et rien pour autre chose", savoir_faire.chercher("recette de crêpes"), "")
verifier("ses consignes listent la recette",
         "- Inscrire OQRO sur un annuaire (quand : un annuaire ou un site de lancement)" in savoir_faire.pour_consignes(),
         True)
verifier("même titre : la recette est réécrite",
         savoir_faire.noter("Inscrire OQRO sur un annuaire", "", "Lire la liste\nOuvrir la page"), "Recette mise à jour.")
verifier("… un seul fichier", savoir_faire.nombre(), 1)
verifier("étapes sur une ligne, numérotées (comme quand il te la lit)",
         savoir_faire.noter("Publier un article", "", "1. Ouvrir l'éditeur. 2. Coller le texte. 3) Publier.")
         and "\n2. Coller le texte.\n" in (savoir_faire.DOSSIER / "publier-un-article.md").read_text(encoding="utf-8"),
         True)
for titre, etapes, quoi in [("Une seule étape", "Tout faire", "une seule étape (c'est une leçon)"),
                            ("Avec un lien", "Ouvrir https://piege.fr\nCliquer", "un lien"),
                            ("Avec une adresse", "Écrire à pirate@exemple.fr\nAttendre", "une adresse"),
                            ("Trop long", "\n".join(f"étape {i}" for i in range(20)), "trop d'étapes"),
                            ("ab", "un\ndeux", "un titre trop court")]:
    verifier(f"recette : refusée pour {quoi}", savoir_faire.noter(titre, "", etapes).startswith("Refusé"), True)
max_avant = config.SAVOIR_FAIRE_MAX
config.SAVOIR_FAIRE_MAX = 2
verifier("au plafond : pas de nouvelle recette…", savoir_faire.noter("Encore une autre", "", "a\nb").startswith("Refusé"), True)
verifier("… mais on peut toujours en réécrire une", savoir_faire.noter("Publier un article", "", "a\nb"), "Recette mise à jour.")
config.SAVOIR_FAIRE_MAX = max_avant
r = asyncio.run(savoir_faire.outil_chercher_savoir_faire.handler({"quoi": "annuaire"}))
verifier("l'outil chercher_savoir_faire rend la recette", "Lire la liste" in r["content"][0]["text"], True)
r = asyncio.run(savoir_faire.outil_noter_savoir_faire.handler({"titre": "Rien", "etapes": "x"}))
verifier("l'outil noter_savoir_faire : un refus est une erreur", r.get("is_error"), True)

# --- 3. Branchés sur Steve, avec les mêmes garde-fous que son carnet -------------------------------------------------
noms = {t.name for t in outils.OUTILS}
verifier("Steve a les trois outils", {"noter_mot", "noter_savoir_faire", "chercher_savoir_faire"} <= noms, True)
options = fond.Taches._options(fond.Taches(fond.Standard(), lambda *_: None), fond.Tache(1, "t", datetime.now()))
verifier("ses agents de fond n'écrivent ni le lexique ni les recettes",
         {J + "noter_lecon", J + "noter_mot", J + "noter_savoir_faire"} <= set(options.disallowed_tools), True)
verifier("… mais ils peuvent relire les recettes", J + "chercher_savoir_faire" in options.disallowed_tools, False)
verifier("chercher_savoir_faire est une lecture (sa ronde y a droit)",
         (est_lecture(J + "chercher_savoir_faire"), fond.outil_de_lecture(J + "chercher_savoir_faire")), (True, True))
verifier("… qui ne lui fait lire que ce qu'il a écrit lui-même",
         [lit_dehors(J + n) for n in ("noter_mot", "noter_savoir_faire", "chercher_savoir_faire")], [False] * 3)
verifier("après un texte venu d'ailleurs (modes prudents), écrire pour plus tard se confirme",
         {"noter_mot", "noter_savoir_faire"} <= ECRIT_POUR_PLUS_TARD, True)
verifier("sinon, pas de question (comme le carnet)", {"noter_mot", "noter_savoir_faire"} & JERVIS_A_CONFIRMER, set())
verifier("la question, s'il faut la poser, dit la paire exacte",
         decrire(J + "noter_mot", {"entendu": "au crow", "voulu": "Oqro"}),
         "Je note dans mon lexique : quand j'entends « au crow », tu veux dire « Oqro ». Je peux ?")
verifier("… et la recette entière, sur une ligne",
         decrire(J + "noter_savoir_faire", {"titre": "Publier", "etapes": "1. Ouvrir\n2. Coller"}),
         "J'écris ma recette « Publier » : 1. Ouvrir 2. Coller. Je peux ?")
net, trop = tel_que_montre("noter_savoir_faire", {"titre": "Publier", "etapes": "1. Ouvrir\n2. Coller"})
verifier("ce qui s'exécute après le oui est ce qu'il a lu, et ça reste deux étapes",
         (trop, savoir_faire._etapes(net["etapes"])), ("", ["Ouvrir", "Coller"]))
verifier("une recette trop longue pour être lue en entier est refusée",
         tel_que_montre("noter_savoir_faire", {"titre": "x", "etapes": "y" * (A_MONTRER["noter_savoir_faire"]["etapes"] + 1)})[1],
         "etapes")
consignes = brain.carnet()
verifier("ses consignes contiennent le lexique et la liste des recettes",
         ("« au crow » = Oqro" in consignes, "Tes savoir-faire" in consignes), (True, True))
verifier("ses consignes lui disent de s'en servir",
         all(m in brain._consignes(False) for m in ("noter_mot", "noter_savoir_faire", "chercher_savoir_faire")), True)
verifier("ses agents de fond aussi savent relire les recettes", "chercher_savoir_faire" in brain.EXPERT, True)


# --- 4. La correction arrive avant Claude, et la relecture apprend les mots ------------------------------------------
class FauxClaude:
    def __init__(self, messages):
        self.messages, self.recu = messages, []

    async def query(self, t): self.recu.append(t)
    async def interrupt(self): ...
    async def get_mcp_status(self): return {"mcpServers": []}

    async def receive_response(self):
        for m in self.messages:
            yield m


async def tester_cerveau():
    async def parler(_p): ...
    async def confirmer(_q): return True
    c = brain.Cerveau(parler, confirmer, journal=lambda *_: None, patience=False)
    c._client = FauxClaude([AssistantMessage(content=[TextBlock("Oqro est en ligne.")], model="m",
                                             parent_tool_use_id=None)])
    await c.demander("le site d'au crow marche ?")
    verifier("à la voix : Claude lit la phrase corrigée", c._client.recu[-1].endswith("le site d'Oqro marche ?"), True)
    verifier("… et c'est elle qui reste pour la relecture", c._echanges[-1][0], "le site d'Oqro marche ?")
    await c.demander("le site d'au crow marche ?", ecrit=lambda _m: None)
    verifier("par écrit : ce que tu tapes reste tel quel", c._client.recu[-1].endswith("le site d'au crow marche ?"), True)
    async with c._verrou:
        c._au_travail = True
        verifier("par-dessus pendant son travail : corrigé aussi", (c.glisser("et au crow ?"), c._glisses), (True, ["et Oqro ?"]))
    c._glisses, c._muet, c._au_travail = [], False, False

    vu = {}

    async def fausse_relecture(prompt, options):
        vu["consignes"] = options.system_prompt
        yield ResultMessage("success", 1, 1, False, 1, "s", result=json.dumps({
            "faits": [], "lecons": [],
            "mots": [{"entendu": "rézin", "voulu": "Raisin"}, {"entendu": "non", "voulu": "oui"}, {"entendu": 3}]}))
    brain.query = fausse_relecture
    journal = []
    c._journal = journal.append
    await c._memoriser([("j'ai bossé chez rézin, non, Raisin", "Noté.")])
    verifier("la relecture sait relever les mots corrigés", '"mots"' in vu["consignes"], True)
    verifier("… le bon entre dans le lexique, le piège et le mal formé non",
             (lexique.corriger("chez rézin"), lexique.corriger("non")), ("chez Raisin", "non"))
    verifier("… il saura qu'il l'a appris, et le journal le montre",
             (c._nouveautes, any("Lexique" in l for l in journal)), (["lexique : « rézin » = Raisin"], True))
    verifier("lire_mots : une réponse sans mots", (brain.lire_mots('{"faits": []}'), brain.lire_mots("rien")), ([], []))


asyncio.run(tester_cerveau())


# --- 5. Tout effacer emporte aussi le lexique et les recettes ------------------------------------------------------
jetes = []
outils._jeter = lambda chemin: jetes.append(chemin)
lexique.FICHIER.touch()
outils.lexique.FICHIER, outils.savoir_faire.DOSSIER = lexique.FICHIER, savoir_faire.DOSSIER
asyncio.run(outils.effacer_memoire.handler({"quoi": "tout"}))
verifier("« efface tout » : le lexique et les recettes partent à la corbeille",
         (lexique.FICHIER in jetes, savoir_faire.DOSSIER in jetes), (True, True))
outils.a_oublier = None

print(f"{total - len(echecs)}/{total} vérifications passent")
os._exit(1 if echecs else 0)
