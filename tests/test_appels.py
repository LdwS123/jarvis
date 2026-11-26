"""Appeler avec ton iPhone depuis le Mac : « Steve, appelle maman ».

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_appels.py

Aucun appel ne part : `open tel://…` et le clic sur « Appeler » sont remplacés par des faux.
"""

import asyncio
import os
import threading

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import appels, brain, config, outils, permissions  # noqa: E402
from jervis.permissions import Gardien, decrire, lit_dehors  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def texte(r: dict) -> str:
    return r["content"][0]["text"]


# --- Les numéros -------------------------------------------------------------------------------------------
verifier("numéro : écrit avec des espaces", appels.numero_appelable("06 12 34 56 78"), "0612345678")
verifier("numéro : international, avec (0)", appels.numero_appelable("+33 (0)6 12 34 56 78"), "+33612345678")
verifier("numéro : 0033 → +33", appels.numero_appelable("0033 6 12 34 56 78"), "+33612345678")
verifier("numéro : +33 0899… (le 0 gardé) → +33899…", appels.numero_appelable("+33 0899 12 34 56"), "+33899123456")
verifier("numéro : 0033 0899… aussi", appels.numero_appelable("0033 0 899 12 34 56"), "+33899123456")
verifier("surtaxé : même écrit +33 0899…", appels.numero_surtaxe(appels.numero_appelable("+33 0899 12 34 56")), True)
verifier("numéro : tel:", appels.numero_appelable("tel:+33612345678"), "+33612345678")
verifier("numéro : un nom n'en est pas un", appels.numero_appelable("maman"), None)
verifier("numéro : une adresse mail non plus", appels.numero_appelable("moi@icloud.com"), None)
verifier("numéro : vide", appels.numero_appelable(None), None)
verifier("national : +33 → 0", appels.national("+33612345678"), "0612345678")
verifier("urgence : le 17", appels.numero_urgence("17"), "la police")
verifier("urgence : le 112", appels.numero_urgence("112"), "les urgences")
verifier("urgence : un portable n'en est pas une", appels.numero_urgence("0612345678"), None)
for n in ("0892123456", "0899123456", "118712", "3615", "1023", "+881612345678", "+449012345678"):
    verifier(f"surtaxé : {n}", appels.numero_surtaxe(n), True)
for n in ("0612345678", "+33612345678", "0800123456", "0142123456", "15", "112", "3114", "+14155550100"):
    verifier(f"pas surtaxé : {n}", appels.numero_surtaxe(n), False)

# --- L'outil appeler ----------------------------------------------------------------------------------------
lances: list[tuple] = []
clics: list[list[str]] = []
reponse_clic = {"ok": True}


async def faux_lancer(*cmd, entree=None, delai=15):
    lances.append(cmd)
    return 0, ""


async def faux_cliquer(noms, secondes=6):
    clics.append(noms)
    return reponse_clic["ok"]


appels._lancer = faux_lancer
appels.cliquer = faux_cliquer


def appeler(**args) -> dict:
    return asyncio.run(appels.appeler.handler(args))


r = appeler(numero="06 12 34 56 78", nom="maman")
verifier("appeler : tel:// vers le bon numéro", lances[-1], ("open", "tel://0612345678"))
verifier("appeler : il clique sur « Appeler »", clics[-1], appels.BOUTONS_APPELER)
verifier("appeler : il sait qu'il doit se taire", ("Ça sonne chez maman" in texte(r), "tais-toi" in texte(r)), (True, True))
appeler(numero="+33612345678", nom="Léa", facetime=True)
verifier("appeler : FaceTime audio s'il le demande", lances[-1], ("open", "facetime-audio://+33612345678"))
reponse_clic["ok"] = False
r = appeler(numero="0612345678", nom="maman")
verifier("appeler : bulle introuvable → il te dit de cliquer", "cliquer sur « Appeler »" in texte(r), True)
reponse_clic["ok"] = True
n = len(lances)
r = appeler(numero="08 99 12 34 56", nom="voyance")
verifier("appeler : un numéro surtaxé → refusé, rien lancé", (r.get("is_error"), "surtaxé" in texte(r), len(lances)),
         (True, True, n))
r = appeler(numero="+33 0899 12 34 56", nom="voyance")
verifier("appeler : surtaxé déguisé en +33 0899… → refusé aussi", (r.get("is_error"), len(lances)), (True, n))
r = appeler(numero="maman")
verifier("appeler : un nom à la place du numéro → il cherche d'abord", ("chercher_contact" in texte(r), len(lances)),
         (True, n))
config.APPELS = False
r = appeler(numero="0612345678")
verifier("appeler : JERVIS_APPELS=non → rien", (r.get("is_error"), len(lances)), (True, n))
config.APPELS = True
config.APPEL_AUTO = False
n_clics = len(clics)
appeler(numero="0612345678", nom="maman")
verifier("appeler : JERVIS_APPEL_AUTO=non → il compose sans cliquer", len(clics), n_clics)
config.APPEL_AUTO = True
r = asyncio.run(appels.raccrocher.handler({}))
verifier("raccrocher : il clique sur « Raccrocher »", (clics[-1], texte(r)), (appels.BOUTONS_RACCROCHER, "Raccroché."))


async def faux_contacts(script, *args):
    return 0, "Maman | tél 06 12 34 56 78 (_$!<Mobile>!$_) | tél 01 42 12 34 56 (_$!<Home>!$_)\n"

outils._applescript, vrai_applescript = faux_contacts, outils._applescript
verifier("contacts : l'étiquette de chaque numéro, en clair", texte(asyncio.run(outils.chercher_contact.handler(
    {"nom": "maman"}))), "Maman | tél 06 12 34 56 78 (Mobile) | tél 01 42 12 34 56 (Home)\n")
outils._applescript = vrai_applescript
verifier("contacts : il cherche aussi les surnoms (« maman »)", "nickname contains q" in outils._CONTACT, True)
verifier("outils : appeler et raccrocher sont à lui", {"appeler", "raccrocher"} <= {t.name for t in outils.OUTILS}, True)


# --- Les permissions -------------------------------------------------------------------------------------------
class FauxContexte:
    pass


async def gardien() -> dict:
    questions: list[str] = []

    async def repondre(question: str) -> bool:
        questions.append(question)
        return True

    g = Gardien(repondre)
    vu: dict = {}

    async def essai(entree: dict) -> str:
        questions.clear()
        g.nouvelle_demande()
        return type(await g("mcp__jervis__appeler", entree, FauxContexte())).__name__

    config.CONFIRMATIONS = "autonome"
    vu["autonome : appelle maman"] = (await essai({"numero": "0612345678", "nom": "maman"}), list(questions))
    vu["autonome : le 17"] = (await essai({"numero": "17"}), list(questions))
    questions.clear()
    g.nouvelle_demande()
    await g("mcp__navigateur__browser_snapshot", {}, FauxContexte())  # il a lu une page…
    vu["après une page lue"] = (type(await g("mcp__jervis__appeler", {"numero": "0142123456", "nom": "le resto"},
                                             FauxContexte())).__name__, list(questions))
    questions.clear()
    g.nouvelle_demande(mefiance=True)
    vu["sur le rapport d'un agent"] = (type(await g("mcp__jervis__appeler", {"numero": "0612345678"},
                                                    FauxContexte())).__name__, len(questions))
    questions.clear()
    g.nouvelle_demande()
    g.a_distance = True
    vu["depuis l'iPhone"] = (type(await g("mcp__jervis__appeler", {"numero": "0612345678"}, FauxContexte())).__name__,
                             len(questions))
    g.a_distance = False
    for nom, outil, entree in (
            ("osascript", "Bash", {"command": """osascript -e 'tell application "Messages" to send "go" to buddy "x"'"""}),
            ("JXA", "Bash", {"command": """osascript -l JavaScript -e 'Application("Messages").send("go", {to: b})'"""}),
            ("script écrit", "Write", {"file_path": "/tmp/a.scpt",
                                       "content": 'tell application id "com.apple.iChat"\nsend "go" to b\nend tell'})):
        g.nouvelle_demande()
        vu[f"messages par {nom}"] = type(await g(outil, entree, FauxContexte())).__name__
    g.nouvelle_demande()
    vu["messages sans envoi"] = type(await g("Bash", {"command": """osascript -e 'tell application "Messages" to activate'"""},
                                             FauxContexte())).__name__
    permissions._lu_dehors_a = float("-inf")
    vu["jamais lu dehors"] = permissions.lu_dehors_depuis() > 1e9
    await g("mcp__navigateur__browser_snapshot", {}, FauxContexte())
    vu["vient de lire dehors"] = permissions.lu_dehors_depuis() < 5
    config.CONFIRMATIONS = "essentielles"
    vu["essentielles : appelle maman"] = (await essai({"numero": "0612345678", "nom": "maman"}), len(questions))
    config.CONFIRMATIONS = "autonome"
    return vu


vu = asyncio.run(gardien())
verifier("gardien : en autonome, « appelle maman » part sans question", vu["autonome : appelle maman"],
         ("PermissionResultAllow", []))
verifier("gardien : un numéro d'urgence est toujours demandé", vu["autonome : le 17"],
         ("PermissionResultAllow", ["J'appelle le 17, la police. Je peux ?"]))
verifier("gardien : un numéro lu sur une page → il demande", vu["après une page lue"],
         ("PermissionResultAllow", ["J'appelle le resto au 0142123456. Je peux ?"]))
verifier("gardien : sur le rapport d'un agent → il demande", vu["sur le rapport d'un agent"], ("PermissionResultAllow", 1))
verifier("gardien : depuis l'iPhone → refusé (ça sonnerait sur le Mac)", vu["depuis l'iPhone"],
         ("PermissionResultDeny", 0))
for nom in ("osascript", "JXA", "script écrit"):
    verifier(f"gardien : un iMessage par {nom} → refusé (il a envoyer_imessage)", vu[f"messages par {nom}"],
             "PermissionResultDeny")
verifier("gardien : Messages sans envoi → permis", vu["messages sans envoi"], "PermissionResultAllow")
verifier("gardien : il note quand il a lu un texte du dehors", (vu["jamais lu dehors"], vu["vient de lire dehors"]),
         (True, True))
verifier("gardien : en mode essentielles, toujours demandé", vu["essentielles : appelle maman"],
         ("PermissionResultAllow", 1))
verifier("décrire : sans nom", decrire("mcp__jervis__appeler", {"numero": "0612345678"}), "J'appelle le 0612345678. Je peux ?")
verifier("appeler n'est pas une lecture du dehors", (lit_dehors("mcp__jervis__appeler"),
                                                     lit_dehors("mcp__jervis__raccrocher")), (False, False))
verifier("appeler fait partie des actions confirmées", "appeler" in permissions.JERVIS_A_CONFIRMER, True)

c = brain._consignes(ecran=False)
verifier("consignes : il sait appeler", ("Appels :" in c, "raccrocher" in c), (True, True))

print(f"\n{'✅' if not echecs else '❌'} appels : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
