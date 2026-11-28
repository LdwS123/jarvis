"""Son coffre (tes données sensibles, jamais vues par Claude) et son propre code (le mot de passe du Mac).

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_coffre.py

Rien ne sort du Mac, et rien ne touche à ton vrai Trousseau, à ton presse-papiers ni à la fenêtre du mot de passe :
ils sont remplacés par des faux, et le registre du coffre vit dans un dossier temporaire. Les numéros sont des
exemples publics (l'IBAN d'exemple de la Banque de France, la carte de test 4111…).
"""

import asyncio
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, coffre, comptes, config, outils, permissions, proprio  # noqa: E402
from jervis.permissions import Gardien  # noqa: E402

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


TMP = Path(tempfile.mkdtemp())
config.ESPACE = TMP / "espace"
coffre.REGISTRE = config.ESPACE / "coffre.json"
config.COFFRE, config.MEFIANCE, config.CODE_MOT_DE_PASSE = True, True, True

IBAN = "FR7630006000011234567890189"
IBAN_ESPACES = "FR76 3000 6000 0112 3456 7890 189"
IBAN_2 = "DE89370400440532013000"
CORPS = "1840575123456"
NIR = CORPS + str(97 - int(CORPS) % 97).zfill(2)
CARTE = "4111 1111 1111 1111"

# --- Ce qui se reconnaît tout seul ---------------------------------------------------------------------------
verifier("IBAN : valide, d'un bloc ou par groupes", [coffre.iban_valide(s) for s in (IBAN, IBAN_ESPACES, IBAN_2)],
         [True] * 3)
verifier("IBAN : une clé fausse → non", coffre.iban_valide("FR7630006000011234567890188"), False)
verifier("numéro de sécu : clé juste, clé fausse, Corse",
         (coffre.nir_valide(NIR), coffre.nir_valide(CORPS + "00"),
          coffre.nir_valide("2840" + "52A" + "123456" + str(97 - int("2840519123456") % 97).zfill(2))),
         (True, False, True))
verifier("carte : Luhn", (coffre.carte_valide(CARTE), coffre.carte_valide("4111 1111 1111 1112")), (True, False))
phrase = (f"Mon IBAN : {IBAN_ESPACES}, la carte {CARTE}, sécu {NIR[0]} {NIR[1:3]} {NIR[3:5]} {NIR[5:7]} "
          f"{NIR[7:10]} {NIR[10:13]} {NIR[13:]}. Le 2024-01-15, tél 06 12 34 56 78, commande 1234567890123, 3,5 %.")
verifier("masquer : IBAN, carte, sécu cachés ; date, téléphone, commande, nombre intacts", coffre.masquer(phrase),
         "Mon IBAN : [🔒 un IBAN], la carte [🔒 une carte bancaire], sécu [🔒 un numéro de sécu]. Le 2024-01-15, "
         "tél 06 12 34 56 78, commande 1234567890123, 3,5 %.")
verifier("masquer : un IBAN collé à un mot, en minuscules, en fin de texte",
         coffre.masquer(f"iban:{IBAN.lower()}"), "iban:[🔒 un IBAN]")
verifier("masquer : un texte sans rien → le même", coffre.masquer("Rendez-vous à 14 h chez Léa."),
         "Rendez-vous à 14 h chez Léa.")
config.COFFRE = False
verifier("JERVIS_COFFRE=non : plus de masquage", coffre.masquer(IBAN), IBAN)
config.COFFRE = True
verifier("hexadécimal du Trousseau : décodé seulement si ce n'est pas de l'ASCII simple",
         (coffre._lire_sortie("c3a974c3a9e282ac"), coffre._lire_sortie("abcdef12"), coffre._lire_sortie("Truc!2024")),
         ("été€", "abcdef12", "Truc!2024"))

# --- Trousseau, presse-papiers : des faux --------------------------------------------------------------------
trousseau: dict[str, str] = {}
presse_papiers: list[str] = [""]
copies: list[str] = []


async def faux_ranger(nom, valeur):
    trousseau[nom] = valeur


async def faux_retrouver(nom):
    return trousseau.get(nom)


async def faux_oublier(nom):
    trousseau.pop(nom, None)


async def faux_presse_papiers():
    return presse_papiers[0]


async def faux_vider(valeur):
    if presse_papiers[0] == valeur:
        presse_papiers[0] = ""


async def faux_copier(secret):
    copies.append(secret)


coffre._trousseau_ranger, coffre._trousseau_retrouver, coffre._trousseau_oublier = (faux_ranger, faux_retrouver,
                                                                                      faux_oublier)
coffre._presse_papiers, coffre._vider_presse_papiers, comptes.copier = faux_presse_papiers, faux_vider, faux_copier

# --- Ce que tu lui dis : rangé, puis masqué -------------------------------------------------------------------
dit = asyncio.run(coffre.proteger(f"mon iban c'est {IBAN_ESPACES} et mon mot de passe impots.gouv.fr c'est "
                                  "Truc!2024 merci"))
verifier("proteger : ta phrase, sans rien de secret", dit,
         "mon iban c'est [🔒 iban] et mon mot de passe impots.gouv.fr c'est [🔒 mot de passe impots.gouv.fr] merci")
verifier("proteger : rangés dans le Trousseau (l'IBAN sans espaces)", trousseau,
         {"iban": IBAN, "mot de passe impots.gouv.fr": "Truc!2024"})
fiches = coffre.fiches()
verifier("registre : ni valeur, un indice, le site du mot de passe",
         (IBAN in str(fiches), "Truc!2024" in str(fiches), fiches["iban"]["indice"],
          fiches["mot de passe impots.gouv.fr"]["sites"]), (False, False, "FR76 … 0189", ["impots.gouv.fr"]))
verifier("proteger : le même IBAN redit → le même nom, rien de nouveau",
         (asyncio.run(coffre.proteger(f"vire sur {IBAN}")), len(trousseau)), ("vire sur [🔒 iban]", 2))
verifier("proteger : un deuxième IBAN → « iban 2 »", asyncio.run(coffre.proteger(f"et celui-là {IBAN_2}")),
         "et celui-là [🔒 iban 2]")
verifier("proteger : « le mot de passe est trop court » n'est pas un mot de passe",
         asyncio.run(coffre.proteger("le mot de passe est trop court")), "le mot de passe est trop court")
verifier("masquer : une valeur rangée, retrouvée mot pour mot n'importe où",
         coffre.masquer("Votre mot de passe Truc!2024 a été changé"), "Votre mot de passe [🔒 mot de passe impots.gouv.fr] a été changé")
verifier("masquer : une phrase « mot de passe : … » lue ailleurs n'est jamais rangée",
         (coffre.masquer("mot de passe : Zz9!abcd"), len(trousseau)), ("mot de passe : Zz9!abcd", 3))

# --- Ce que ses outils lui rendent ---------------------------------------------------------------------------
image = {"type": "image", "source": {"type": "base64", "data": "A" * 5000}}
sortie = [{"type": "text", "text": f"Page URL: https://www.impots.gouv.fr/accueil\nIBAN {IBAN}"}, image]
r = asyncio.run(coffre.crochet({"tool_name": "mcp__navigateur__browser_snapshot", "tool_response": sortie}, None, None))
verifier("crochet : la sortie masquée, sa forme et son image intactes",
         r, {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": [
             {"type": "text", "text": "Page URL: https://www.impots.gouv.fr/accueil\nIBAN [🔒 iban]"}, image]}})
verifier("crochet : la page ouverte retenue (son domaine)", coffre.page_ouverte(), "impots.gouv.fr")
bash = {"stdout": f"facture.txt: {CARTE}", "stderr": "", "interrupted": False}
verifier("crochet : la sortie de Bash, mêmes clés", asyncio.run(coffre.crochet(
    {"tool_name": "Bash", "tool_response": bash}, None, None))["hookSpecificOutput"]["updatedToolOutput"],
         {"stdout": "facture.txt: [🔒 une carte bancaire]", "stderr": "", "interrupted": False})
verifier("crochet : rien à masquer → on ne touche à rien", asyncio.run(coffre.crochet(
    {"tool_name": "Read", "tool_response": {"content": "bonjour"}}, None, None)), {})


class FauxCerveau:
    _glisses: list[str] = []
    _muet = False

    def _journal(self, _t):
        pass


faux = FauxCerveau()
r = asyncio.run(brain.Cerveau._apres_outil(faux, {"tool_name": "Bash", "tool_response": {"stdout": IBAN}}, None, None))
verifier("Steve : son crochet masque la sortie", r["hookSpecificOutput"]["updatedToolOutput"],
         {"stdout": "[🔒 iban]"})
faux._glisses = [f"et mets {IBAN}"]
r = asyncio.run(brain.Cerveau._apres_outil(faux, {"tool_name": "Bash", "tool_response": {"stdout": "ok"}}, None, None))
verifier("Steve : ce que tu dis pendant son travail, masqué aussi, sans sortie réécrite",
         r, {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                    "additionalContext": f"{brain.MARQUE_GLISSE} « et mets [🔒 iban] »"}})

# --- Ses outils ----------------------------------------------------------------------------------------------
presse_papiers[0] = NIR
r = asyncio.run(coffre.ranger_secret.handler({"nom": "Numéro de sécu", "sites": ["https://www.ameli.fr/assure"]}))
verifier("ranger_secret : rangé, genre reconnu, presse-papiers vidé, rien de la valeur dans la réponse",
         (trousseau.get("numéro de sécu"), coffre.fiche("numéro de sécu")["genre"], presse_papiers[0],
          NIR in texte(r), coffre.fiche("numéro de sécu")["sites"]), (NIR, "nir", "", False, ["ameli.fr"]))
presse_papiers[0] = "autre"
verifier("ranger_secret : un nom déjà pris → refusé sans remplacer=true",
         (asyncio.run(coffre.ranger_secret.handler({"nom": "iban"})).get("is_error"), trousseau["iban"]), (True, IBAN))
presse_papiers[0] = ""
verifier("ranger_secret : presse-papiers vide → refusé",
         asyncio.run(coffre.ranger_secret.handler({"nom": "neuf"})).get("is_error"), True)
liste = texte(asyncio.run(coffre.lister_secrets.handler({})))
verifier("lister_secrets : les noms et indices, jamais les valeurs",
         ("« iban »" in liste, "FR76 … 0189" in liste, IBAN in liste, "Truc!2024" in liste, NIR in liste),
         (True, True, False, False, False))
r = asyncio.run(coffre.copier_secret.handler({"nom": "iban", "site": "impots.gouv.fr"}))
verifier("copier_secret : la valeur au presse-papiers, pas dans la réponse", (copies[-1], IBAN in texte(r)),
         (IBAN, False))
verifier("copier_secret : un nom inconnu → erreur",
         asyncio.run(coffre.copier_secret.handler({"nom": "rien", "site": "a.fr"})).get("is_error"), True)
asyncio.run(coffre.oublier_secret.handler({"nom": "iban 2"}))
verifier("oublier_secret : hors du Trousseau, du registre et du masquage",
         ("iban 2" in trousseau, coffre.fiche("iban 2"), coffre.masquer(IBAN_2)), (False, None, "[🔒 un IBAN]"))
coffre._valeurs.clear()
verifier("charger : au démarrage, les valeurs reviennent du Trousseau",
         (asyncio.run(coffre.charger()), coffre.masquer(IBAN)), (3, "[🔒 iban]"))
verifier("outils : les quatre sont à lui", {"ranger_secret", "lister_secrets", "copier_secret", "oublier_secret"}
         <= {t.name for t in outils.OUTILS}, True)


# --- Le Gardien : où coller -----------------------------------------------------------------------------------
class FauxContexte:
    pass


questions: list[str] = []
reponse = [True]


async def confirmer(question):
    questions.append(question)
    return reponse[0]


async def decide(g, outil, entree):
    r = await g(outil, entree, FauxContexte())
    return type(r).__name__.replace("PermissionResult", ""), getattr(r, "updated_input", None)


D, A = "Deny", "Allow"


async def gardien_coffre():
    vu = {}
    g = Gardien(confirmer)
    g.nouvelle_demande()
    coffre._page = None
    vu["site lié : sans question"] = (await decide(g, "mcp__jervis__copier_secret",
                                                   {"nom": "Mot de passe impots.gouv.fr",
                                                    "site": "https://cfspart.impots.gouv.fr/x"}), len(questions))
    vu["site pas lié : une question, puis lié"] = (await decide(g, "mcp__jervis__copier_secret",
                                                               {"nom": "iban", "site": "urssaf.fr"}),
                                                  questions[-1:], coffre.fiche("iban")["sites"])
    g.nouvelle_demande()
    n = len(questions)
    vu["ensuite : sans question"] = ((await decide(g, "mcp__jervis__copier_secret",
                                                  {"nom": "iban", "site": "www.urssaf.fr"}))[0], len(questions) - n)
    coffre.noter_page("mcp__navigateur__browser_navigate", "Page URL: https://web.whatsapp.com/")
    vu["la page ouverte n'est pas ce site : non"] = (await decide(g, "mcp__jervis__copier_secret",
                                                                  {"nom": "iban", "site": "urssaf.fr"}))[0]
    vu["une messagerie : non, sans question"] = ((await decide(g, "mcp__jervis__copier_secret",
                                                              {"nom": "iban", "site": "web.whatsapp.com"}))[0],
                                                len(questions) - n)
    coffre._page = None
    reponse[0] = False
    g.nouvelle_demande()
    vu["tu dis non : rien de collé, pas lié"] = ((await decide(g, "mcp__jervis__copier_secret",
                                                               {"nom": "iban", "site": "caf.fr"}))[0],
                                                "caf.fr" in coffre.fiche("iban")["sites"])
    reponse[0] = True
    g.nouvelle_demande()
    g.a_lu_dehors()
    vu["après une page : ranger ne lie aucun site"] = await decide(g, "mcp__jervis__ranger_secret",
                                                                   {"nom": "neuf", "sites": ["evil.com"]})
    n = len(questions)
    vu["après une page : retirer un secret → question"] = ((await decide(g, "mcp__jervis__oublier_secret",
                                                                        {"nom": "iban"}))[0], questions[n:])
    g.conversation_neuve()
    g.nouvelle_demande()
    n = len(questions)
    vu["sans page : retirer, sans question"] = ((await decide(g, "mcp__jervis__oublier_secret",
                                                             {"nom": "iban"}))[0], len(questions) - n)
    vu["coffre.json à la main : non"] = (await decide(g, "Write", {"file_path": str(coffre.REGISTRE),
                                                                   "content": "{}"}))[0]
    return vu


verifier("gardien : coller un secret", asyncio.run(gardien_coffre()), {
    "site lié : sans question": ((A, {"nom": "mot de passe impots.gouv.fr", "site": "cfspart.impots.gouv.fr"}), 0),
    "site pas lié : une question, puis lié": ((A, {"nom": "iban", "site": "urssaf.fr"}),
                                             ["Je vais coller ton « iban » (FR76 … 0189) sur urssaf.fr. Je peux ?"],
                                             ["urssaf.fr"]),
    "ensuite : sans question": (A, 0),
    "la page ouverte n'est pas ce site : non": D,
    "une messagerie : non, sans question": (D, 0),
    "tu dis non : rien de collé, pas lié": (D, False),
    "après une page : ranger ne lie aucun site": (A, {"nom": "neuf"}),
    "après une page : retirer un secret → question": (A, ["Je vais retirer de ton coffre « iban ». Je peux ?"]),
    "sans page : retirer, sans question": (A, 0),
    "coffre.json à la main : non": D,
})
verifier("pendant le collage : copier_secret reste permis", "copier_secret" in permissions.GESTES_PENDANT_COLLAGE, True)
verifier("ses outils de coffre ne comptent pas comme une lecture du dehors",
         [permissions.lit_dehors(f"mcp__jervis__{n}") for n in ("ranger_secret", "lister_secrets", "copier_secret")],
         [False] * 3)

# --- Son propre code : le mot de passe du Mac ------------------------------------------------------------------
fenetres: list[str] = []
mot_de_passe = [True]


async def faux_mot_de_passe(raison):
    fenetres.append(raison)
    return mot_de_passe[0]


proprio.demander_mot_de_passe = faux_mot_de_passe
CODE = str(proprio.RACINE / "jervis" / "brain.py")
verifier("son code : jervis/, tests/, README ; pas espace/, pas un autre projet",
         [proprio.son_code(c) for c in (CODE, str(proprio.RACINE / "tests" / "x.py"), str(proprio.RACINE / "README.md"),
                                        str(proprio.RACINE / "espace" / "note.md"), "/tmp/autre/jervis/x.py",
                                        str(proprio.RACINE / "JERVIS" / "brain.py"))],
         [True, True, True, False, False, True])
verifier("une commande qui écrit dans son code ; une qui lit, non",
         [proprio.commande_touche_son_code(c) for c in (
             "sed -i '' 's/a/b/' ~/Jervis/jervis/brain.py", "cd ~/Jervis && git checkout jervis/brain.py",
             "echo x > ../jervis/brain.py", "grep -n x ~/Jervis/jervis/*.py 2>/dev/null",
             "cd ~/Jervis && git status", "cat ~/Jervis/README.md")],
         [True, True, True, False, False, False])


async def gardien_code():
    vu = {}
    proprio.fermer()
    g = Gardien(confirmer)
    g.nouvelle_demande()
    ecrire = {"file_path": CODE, "content": "x"}
    vu["première modification : le mot de passe"] = ((await decide(g, "Write", ecrire))[0], len(fenetres))
    vu["ensuite : l'accord reste ouvert"] = ((await decide(g, "Edit", {"file_path": CODE, "old_string": "a",
                                                                       "new_string": "b"}))[0], len(fenetres))
    vu["un autre projet : rien à voir"] = ((await decide(g, "Write", {"file_path": str(TMP / "projet" / "a.py"),
                                                                      "content": "x"}))[0], len(fenetres))
    g.a_lu_dehors()
    vu["après une page : redemandé à chaque fois"] = ((await decide(g, "Write", ecrire))[0],
                                                     (await decide(g, "Write", ecrire))[0], len(fenetres),
                                                     "vérifie que c'est bien toi" in fenetres[-1])
    vu["après une page : pas par une commande"] = (await decide(g, "Bash", {
        "command": "sed -i '' 's/a/b/' ~/Jervis/jervis/brain.py"}))[0]
    proprio.fermer()
    g.conversation_neuve()
    g.nouvelle_demande()
    mot_de_passe[0] = False
    vu["mot de passe annulé : non"] = (await decide(g, "Write", ecrire))[0]
    mot_de_passe[0] = True
    g.a_distance = True
    n = len(fenetres)
    vu["depuis l'iPhone, accord fermé : non, sans fenêtre"] = ((await decide(g, "Write", ecrire))[0],
                                                              len(fenetres) - n)
    g.a_distance = False
    vu["une commande qui écrit dans son code : le mot de passe"] = ((await decide(g, "Bash", {
        "command": "cd ~/Jervis && git checkout jervis/brain.py"}))[0], len(fenetres) - n)
    vu["ta voix : jamais"] = ((await decide(g, "Write", {"file_path": str(proprio.RACINE / "voix.npy"),
                                                         "content": "x"}))[0],
                              (await decide(g, "Bash", {"command": "cp x.npy ~/Jervis/voix_depart.npy"}))[0])
    proprio.fermer()
    config.CODE_MOT_DE_PASSE = False
    n = len(fenetres)
    vu["JERVIS_CODE_MOT_DE_PASSE=non : comme avant"] = ((await decide(g, "Write", ecrire))[0], len(fenetres) - n)
    config.CODE_MOT_DE_PASSE = True
    return vu


verifier("gardien : son propre code", asyncio.run(gardien_code()), {
    "première modification : le mot de passe": (A, 1),
    "ensuite : l'accord reste ouvert": (A, 1),
    "un autre projet : rien à voir": (A, 1),
    "après une page : redemandé à chaque fois": (A, A, 3, True),
    "après une page : pas par une commande": D,
    "mot de passe annulé : non": D,
    "depuis l'iPhone, accord fermé : non, sans fenêtre": (D, 0),
    "une commande qui écrit dans son code : le mot de passe": (A, 1),
    "ta voix : jamais": (D, D),
    "JERVIS_CODE_MOT_DE_PASSE=non : comme avant": (A, 0),
})
verifier("la fenêtre : un texte AppleScript sans guillemet qui casse",
         proprio._chaine_applescript('a "b" \\c'), '"a \\"b\\" \\\\c"')

# --- Ce que Claude lit au départ ---------------------------------------------------------------------------------
vraie_memoire = outils.lire_memoire
outils.lire_memoire = lambda: f"- Son IBAN : {IBAN}"
c = brain._consignes(ecran=False)
outils.lire_memoire = vraie_memoire
verifier("consignes : sa mémoire passe par le coffre", (IBAN in c, "[🔒 iban]" in c), (False, True))
verifier("consignes : il sait se servir du coffre, et ne fait jamais dire un secret",
         ("copier_secret" in c, "ranger_secret" in c, "ne la lui fais jamais dire" in c), (True, True, True))

print(f"\n{'✅' if not echecs else '❌'} coffre : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
