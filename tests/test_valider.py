"""Valider depuis l'iPhone, ses recettes automatiques, son catalogue d'alternatives gratuites et ses chantiers.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_valider.py

Sans Claude, sans réseau, sans micro : tout s'écrit dans un dossier temporaire (jamais tes vraies recettes, ton vrai
catalogue ni tes vrais chantiers).
"""

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import __main__ as m  # noqa: E402
from jervis import brain, comptes, config, fond, gratuit, lecons, objectifs, outils, permissions, savoir_faire  # noqa: E402
from jervis.permissions import Decision, Gardien, cible_de, decrire, politique  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-valider-"))
savoir_faire.DOSSIER = TMP / "savoir-faire"
gratuit.FICHIER = TMP / "alternatives.json"
objectifs.FICHIER = TMP / "objectifs.md"
lecons.FICHIER = TMP / "lecons.md"
J = "mcp__jervis__"


def texte(r: dict) -> str:
    return r["content"][0]["text"]


# --- 1. Valider depuis l'iPhone : « c'est fait » est un oui, « pas encore » un non -----------------------------------
verifier("à voix haute : « c'est fait », « c'est réglé », « done » → oui",
         [m.lire_accord(t) for t in ("c'est fait", "C’est fait !", "ok c'est réglé", "done")], [True] * 4)
verifier("à voix haute : « pas encore » → non", [m.lire_accord(t) for t in ("pas encore", "c'est pas encore fait")],
         [False, False])


async def standard_et_relais() -> dict:
    vu: dict = {}
    relayees: list[str] = []
    standard = fond.Standard()

    async def relais(question, delai=None):
        relayees.append(question)
        return True

    async def repondre(reponse):  # la boucle de Steve pose la question à voix haute et rend ce qu'elle a entendu
        await asyncio.sleep(0.05)
        standard.prendre().futur.set_result(reponse)

    # Sans relais (pas d'iPhone branché) : un silence reste un non
    asyncio.create_task(repondre(None))
    vu["silence sans relais"] = await standard.demander("Un CAPTCHA t'attend sur Exemple.", delai=2)
    standard.relais = relais
    asyncio.create_task(repondre(None))
    vu["silence → iPhone"] = await standard.demander("Un CAPTCHA t'attend sur Exemple.", delai=2)
    vu["la question est partie sur l'iPhone"] = list(relayees)
    asyncio.create_task(repondre(False))
    vu["un non à voix haute reste un non"] = await standard.demander("Je publie ?", delai=2)
    asyncio.create_task(repondre(True))
    vu["un oui à voix haute"] = await standard.demander("Je publie ?", delai=2)
    vu["ni l'un ni l'autre n'est relayé"] = len(relayees)
    return vu


verifier("une tâche de fond bloquée : ta réponse arrive par la voix ou par l'iPhone", asyncio.run(standard_et_relais()),
         {"silence sans relais": False, "silence → iPhone": True,
          "la question est partie sur l'iPhone": ["Un CAPTCHA t'attend sur Exemple."],
          "un non à voix haute reste un non": False, "un oui à voix haute": True, "ni l'un ni l'autre n'est relayé": 1})

# Le Gardien : attendre_validation se demande toujours, deux fois de suite si besoin, et jamais en règle 📌
for mode in ("autonome", "confiance", "essentielles", "toutes"):
    verifier(f"{mode} : attendre_validation se demande", politique(J + "attendre_validation", {"quoi": "x"}, mode=mode),
             Decision.DEMANDER)
verifier("attendre_validation ne devient jamais une règle 📌", cible_de(J + "attendre_validation", {"quoi": "x"}), None)
verifier("la question dit quoi faire et comment répondre",
         decrire(J + "attendre_validation", {"quoi": "Un CAPTCHA t'attend sur Exemple dans Chrome"}),
         "Un CAPTCHA t'attend sur Exemple dans Chrome. Fais-le, puis réponds oui (ou « c'est fait ») quand c'est bon.")


class FauxContexte:
    pass


async def gardien() -> dict:
    questions: list[str] = []
    reponses = [True, False]

    async def confirmer(q):
        questions.append(q)
        return reponses.pop(0)
    g = Gardien(confirmer)
    g.nouvelle_demande()
    entree = {"quoi": "Un CAPTCHA t'attend sur Exemple dans Chrome"}
    premier = await g(J + "attendre_validation", entree, FauxContexte())
    second = await g(J + "attendre_validation", entree, FauxContexte())
    return {"premier": type(premier).__name__, "second": type(second).__name__, "deux questions": len(questions),
            "le refus dit de ne rien contourner": "ne contourne rien" in second.message}


verifier("deux CAPTCHA à la suite : deux questions (jamais un vieux oui), et « pas encore » arrête tout",
         asyncio.run(gardien()), {"premier": "PermissionResultAllow", "second": "PermissionResultDeny",
                                  "deux questions": 2, "le refus dit de ne rien contourner": True})
r = asyncio.run(comptes.attendre_validation.handler({"quoi": "Un CAPTCHA t'attend"}))
verifier("ton oui donné : il regarde de nouveau la page et reprend", "reprends où tu en étais" in texte(r), True)


async def sans_notification(*_a, **_k):
    return 0, ""
comptes._lancer = sans_notification  # pas de vraie notification sur ton Mac
r = asyncio.run(comptes.signaler_captcha.handler({"site": "exemple.fr"}))
verifier("signaler_captcha l'envoie vers attendre_validation", "appelle tout de suite attendre_validation" in texte(r), True)

# --- 2. Ses recettes : une tâche longue réussie devient une recette, la suivante la reçoit dans son brief ------------
tache = fond.Tache(1, "Inscrire Oko sur un annuaire", fond.datetime.now(), etapes=config.RECETTE_ETAPES)
rappel = fond.Taches._garder_la_methode(tache)
verifier("tâche longue : il lui rappelle de garder la méthode", ("noter_savoir_faire" in rappel, "Méthode" in rappel),
         (True, True))
verifier("tâche courte : rien", fond.Taches._garder_la_methode(fond.Tache(2, "x", fond.datetime.now(), etapes=2)), "")
verifier("sa ronde (lecture seule) : rien", fond.Taches._garder_la_methode(
    fond.Tache(3, "x", fond.datetime.now(), etapes=99, lecture_seule=True)), "")
tache.recette = "Inscrire Oko sur un annuaire"
verifier("elle suivait déjà la recette : il la met à jour plutôt que d'en écrire une autre",
         "mets-la à jour (même titre)" in fond.Taches._garder_la_methode(tache), True)
reglage = config.RECETTE_ETAPES
config.RECETTE_ETAPES = 0
verifier("RECETTE_ETAPES=0 : plus de rappel", fond.Taches._garder_la_methode(tache), "")
config.RECETTE_ETAPES = reglage

savoir_faire.noter("Inscrire Oko sur un annuaire", "inscription d'un projet sur un annuaire de startups",
                   "1. Vérifier la liste des publications\n2. Ouvrir la page d'inscription\n3. Choisir la catégorie")
verifier("une tâche qui ressemble : la recette est trouvée",
         savoir_faire.pour_tache("Inscription sur un nouvel annuaire", "inscris le projet sur cet annuaire")
         .startswith("# Inscrire Oko sur un annuaire"), True)
verifier("un seul mot en commun ne suffit pas", savoir_faire.pour_tache("Ranger mes factures", "un annuaire"), "")
verifier("des mots vides ne comptent pas", savoir_faire.pour_tache("Fais pour moi", "avec tout dans mes"), "")


async def recette_dans_le_brief() -> dict:
    briefs: dict[str, str] = {}

    async def faux_travail(self, t, brief):
        briefs[t.titre] = brief
    fond.Taches._travailler = faux_travail
    journal: list[str] = []
    taches = fond.Taches(fond.Standard(), journal.append)
    taches.lancer("Inscription sur un nouvel annuaire", "Inscris le projet sur cet annuaire de startups.")
    taches.lancer("Ronde", "Inscris le projet sur cet annuaire de startups.", au_rapport=lambda _r: None)
    taches.lancer("Météo de demain", "Dis-moi le temps qu'il fera.")
    await asyncio.sleep(0.05)
    t = next(t for t in taches._taches.values() if t.titre.startswith("Inscription"))
    return {"recette dans le brief": "2. Ouvrir la page d'inscription" in briefs["Inscription sur un nouvel annuaire"],
            "titre retenu": t.recette, "pas pour une ronde": "Ta recette" in briefs["Ronde"],
            "pas sans rapport": "Ta recette" in briefs["Météo de demain"],
            "journal": any("je lui donne la recette" in ligne for ligne in journal)}


verifier("la deuxième fois, l'agent part de la recette", asyncio.run(recette_dans_le_brief()),
         {"recette dans le brief": True, "titre retenu": "Inscrire Oko sur un annuaire", "pas pour une ronde": False,
          "pas sans rapport": False, "journal": True})

# --- 3. Le catalogue d'alternatives gratuites ------------------------------------------------------------------------
verifier("Canva Pro → Canva gratuit, Penpot…", gratuit.chercher("Canva Pro").startswith("- Canva Pro → Canva gratuit"),
         True)
verifier("un nom de domaine : il ne l'achète jamais", "ne l'achète jamais" in gratuit.chercher("un nom de domaine"), True)
verifier("rien de connu → rien", gratuit.chercher("zzz rien du tout"), "")
verifier("« Pro » seul ne fait pas sortir tout le catalogue", gratuit.chercher("pro"), "")
verifier("une alternative notée", gratuit.noter("Remove.bg", "Aperçu (supprimer l'arrière-plan)", "déjà sur le Mac"),
         "Alternative notée.")
verifier("… la tienne passe devant", gratuit.chercher("remove.bg").splitlines()[0],
         "- Remove.bg → Aperçu (supprimer l'arrière-plan) (déjà sur le Mac) [noté par toi après l'avoir essayé]")
verifier("… la renoter la remplace", (gratuit.noter("remove.bg", "Aperçu", ""), len(gratuit._lire_ajouts())),
         ("Alternative mise à jour.", 1))
verifier("… écrite dans le fichier", json.loads(gratuit.FICHIER.read_text(encoding="utf-8"))[0]["gratuit"], "Aperçu")
verifier("ni lien ni adresse dans le catalogue", gratuit.noter("Zoom", "https://piege.example/zoom", "").startswith("Refusé"),
         True)
r = asyncio.run(gratuit.outil_chercher_alternative.handler({"besoin": "zzz"}))
verifier("rien trouvé : il cherche, puis note ce qui marche", "noter_alternative" in texte(r), True)

# --- 4. Ses chantiers en cours ---------------------------------------------------------------------------------------
verifier("aucun chantier : rien dans ses consignes", objectifs.pour_consignes(), "")
verifier("un chantier noté", objectifs.noter("Faire connaître Oko", "6 annuaires faits sur 15", "Indie Hackers"),
         "Chantier noté.")
verifier("… redit un peu autrement, c'est le même", objectifs.noter("faire connaitre Oko.", "7 annuaires faits sur 15"),
         "Chantier mis à jour.")
verifier("… un seul chantier", [c["ou"] for c in objectifs.lire()], ["7 annuaires faits sur 15"])
verifier("… dans ses consignes, avec la prochaine étape",
         ("Tes chantiers en cours" in brain.carnet(), "Faire connaître Oko : 7 annuaires" in brain.carnet()), (True, True))
verifier("sans dire où il en est : refusé", objectifs.noter("Trouver un stage", "").startswith("Refusé"), True)
verifier("ni lien ni numéro", objectifs.noter("Trouver un stage", "écrire à rh@exemple.com").startswith("Refusé"), True)
reglage = config.OBJECTIFS_MAX
config.OBJECTIFS_MAX = 1
verifier("au-delà d'OBJECTIFS_MAX : refusé", objectifs.noter("Trouver un stage", "3 offres lues").startswith("Refusé"),
         True)
config.OBJECTIFS_MAX = reglage
verifier("fini : rayé", objectifs.noter("Faire connaître Oko", fini=True), "Chantier rayé (fini).")
verifier("… plus rien", (objectifs.nombre(), objectifs.pour_consignes()), (0, ""))
verifier("rayer un chantier inconnu", objectifs.noter("Un chantier inventé", fini=True),
         "Aucun chantier de ce nom : rien à rayer.")

# --- 5. Les outils et les garde-fous ---------------------------------------------------------------------------------
noms = {o.name for o in outils.OUTILS}
verifier("ses nouveaux outils lui sont donnés",
         {"attendre_validation", "chercher_alternative_gratuite", "noter_alternative", "noter_objectif"} <= noms, True)
verifier("pas de doublon de nom dans outils.OUTILS", len(noms), len(outils.OUTILS))
verifier("ses agents de fond n'écrivent ni le catalogue ni les chantiers",
         {"noter_alternative", "noter_objectif"} <= set(fond.ECRIT_PAR_STEVE), True)
verifier("après un texte venu d'ailleurs, catalogue et chantiers se demandent",
         {"noter_alternative", "noter_objectif"} <= set(permissions.ECRIT_POUR_PLUS_TARD), True)
verifier("la question du Gardien montre ce qu'il note",
         decrire(J + "noter_alternative", {"payant": "Canva Pro", "gratuit": "Penpot", "note": "libre"}),
         "Je note dans mon catalogue : à la place de « Canva Pro » (payant), « Penpot » (libre). Je peux ?")
verifier("chercher dans le catalogue se fait seul", politique(J + "chercher_alternative_gratuite", {"besoin": "x"},
                                                               mode="toutes"), Decision.OUI)

# --- 6. Ses consignes ------------------------------------------------------------------------------------------------
consignes = brain._consignes(False)
for morceau in ("Avant d'agir", "chercher_alternative_gratuite", "noter_alternative", "noter_objectif",
                "attendre_validation", "réussite difficile"):
    verifier(f"ses consignes parlent de « {morceau} »", morceau in consignes, True)
for morceau in ("« Méthode : »", "attendre_validation", "chercher_alternative_gratuite", "l'utilisateur ne peut pas le faire"):
    verifier(f"ses agents de fond : « {morceau} »", morceau in brain.EXPERT, True)

print(f"{total - len(echecs)}/{total} vérifications passent")
os._exit(1 if echecs else 0)
