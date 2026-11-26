"""Parler dans les groupes : iMessage (le nom exact du groupe dans Messages), WhatsApp (la discussion qui porte
exactement ce nom, vérifiée par l'en-tête), en texte et en vocal ; et ce que le Gardien en dit.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_groupes.py

Rien ne part : WhatsApp est un faux (une liste de discussions, une recherche, un en-tête, une zone d'écriture),
Messages n'est pas appelé (le script est remplacé), aucune touche n'est envoyée.
"""

import asyncio
import os
import threading

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import ax, brain, config, outils, permissions, vocal  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


# --- Un faux WhatsApp, à l'accessibilité près ---------------------------------------------------------------------

class Bout:
    """Un élément d'interface : un rôle, une description, une valeur, des enfants, et ce qu'on lui a fait."""

    def __init__(self, ecran, role="AXButton", description="", valeur=None, enfants=(), quand_appuye=None,
                 prend_texte=True):
        self.ecran, self.role, self.description, self.valeur = ecran, role, description, valeur
        self.enfants, self.quand_appuye, self.prend_texte, self.focus = list(enfants), quand_appuye, prend_texte, False

    def texte(self, attribut):
        return {"AXRole": self.role, "AXDescription": self.description, "AXValue": self.valeur}.get(attribut)

    def booleen(self, attribut):
        return self.focus if attribut == "AXFocused" else None

    def liste(self, attribut):
        return self.enfants if attribut == "AXChildren" else []

    def mettre(self, attribut, oui):
        if attribut == "AXFocused":
            self.focus = oui
        return True

    def mettre_texte(self, attribut, texte):
        if not self.prend_texte:
            return False
        self.valeur = texte or None
        if self is self.ecran.recherche:
            self.ecran.filtrer(texte)
        return True

    def appuyer(self):
        if self.quand_appuye:
            self.quand_appuye()
            return True
        return False


class FauxEcran(vocal.WhatsApp):
    """La liste (seules les discussions récentes sont visibles), la recherche (elle trouve tout), l'en-tête."""

    def __init__(self, visibles, toutes=None, ouverte="Maman", entree_envoie=True, ecriture_prend=True):
        self.toutes, self.visibles, self.nom_ouvert = list(toutes or visibles), list(visibles), ouverte
        self.micro = object()
        self.recherche = Bout(self, "AXStaticText")
        self.ecriture = Bout(self, "AXTextArea", prend_texte=ecriture_prend)
        self.entree_envoie, self.envoyes, self.appuis, self.recherches = entree_envoie, [], [], []
        self.liste = Bout(self, "AXGroup")
        self.filtrer("")

    def filtrer(self, texte):
        if texte:
            self.recherches.append(texte)
        noms = [n for n in self.toutes if vocal._simple(texte) in vocal._simple(n)] if texte else self.visibles
        self.liste.enfants = [Bout(self, "AXStaticText" if n == self.nom_ouvert else "AXButton", n,
                                   quand_appuye=lambda n=n: self.ouvrir_vraiment(n)) for n in noms]

    def ouvrir_vraiment(self, nom):
        self.appuis.append(nom)
        self.nom_ouvert, self.micro = nom, object()  # une autre discussion : un bouton du micro neuf

    def _trouver(self, identifiant):
        return {vocal.MICRO: self.micro and Bout(self, description=str(id(self.micro))),
                vocal.LISTE: self.liste, vocal.RECHERCHE: self.recherche, vocal.ECRITURE: self.ecriture,
                vocal.ENTETE: Bout(self, description=self.nom_ouvert)}.get(identifiant)

    def devant(self):
        pass

    def pret(self):
        return None

    def valider(self):
        if self.entree_envoie and self.ecriture.valeur:
            self.envoyes.append((self.nom_ouvert, self.ecriture.valeur))
            self.ecriture.valeur = None
        return True


# Le bouton du micro « neuf » : deux lectures du même bouton sont égales (comme ax.Element), pas celles de deux.
Bout.__eq__ = lambda a, b: isinstance(b, Bout) and a.description == b.description and a.role == b.role


def ouvrir(ecran, nom):
    try:
        ecran.ouvrir_nom(nom, patience=0.5)
        return ecran.nom_ouvert
    except vocal.Echec as e:
        return f"Echec: {e}"


print("👥 Ouvrir un groupe WhatsApp")
e = FauxEcran(["Maman", "Famille 🏡", "Foot du jeudi"])
verifier("visible dans la liste : appuyé, ouvert", (ouvrir(e, "Foot du jeudi"), e.appuis, e.recherches),
         ("Foot du jeudi", ["Foot du jeudi"], []))
e = FauxEcran(["Maman"], toutes=["Maman", "Les cousins", "Les cousins (vieux)"])
verifier("pas visible : cherché, puis appuyé", (ouvrir(e, "les cousins"), e.recherches), ("Les cousins", ["les cousins"]))
verifier("la recherche est remise à zéro après", e.recherche.valeur, None)
e = FauxEcran(["Famille"], toutes=["Famille", "Famille 2"])
verifier("un nom qui n'est qu'un bout d'un autre : pas confondu", ouvrir(e, "Famille 2"), "Famille 2")
e = FauxEcran(["Maman", "Famille", "Famille"])
verifier("deux discussions du même nom : il refuse", ouvrir(e, "famille").startswith("Echec: 2 discussions"), True)
verifier("deux discussions du même nom : rien n'est appuyé", e.appuis, [])
e = FauxEcran(["Maman"], toutes=["Maman", "Famille"])
verifier("introuvable : il le dit, sans rien ouvrir", (ouvrir(e, "Fam"), e.appuis),
         ("Echec: aucune discussion WhatsApp ne s'appelle exactement « Fam » : redis-moi son nom tel qu'il est dans "
          "WhatsApp", []))
e = FauxEcran(["Maman", "Famille"], ouverte="Famille")
verifier("déjà ouverte : rien à appuyer", (ouvrir(e, "Famille"), e.appuis), ("Famille", []))
verifier("sans nom : il demande lequel", ouvrir(FauxEcran(["Maman"]), "  ").startswith("Echec: quel groupe"), True)


class EnteteMenteuse(FauxEcran):
    def ouvrir_vraiment(self, nom):
        self.appuis.append(nom)  # (l'app n'a pas changé de discussion)


e = EnteteMenteuse(["Maman", "Famille"])
verifier("l'en-tête ne dit pas le bon nom : rien n'est envoyé",
         ouvrir(e, "Famille").startswith("Echec: je ne suis pas sûr"), True)

# --- Un message écrit WhatsApp --------------------------------------------------------------------------------------
print("\n💬 Un message écrit WhatsApp")


def texte(ecran, **kw):
    return asyncio.run(vocal.envoyer_texte(kw.pop("contact", ""), kw.pop("texte", "J'arrive"), pilote=ecran, **kw))


e = FauxEcran(["Maman", "Foot"])
verifier("dans un groupe : parti, au bon endroit", (texte(e, groupe="Foot")[0], e.envoyes), ("ENVOYÉ", [("Foot", "J'arrive")]))
e = FauxEcran(["Maman", "Foot"], entree_envoie=False)
statut, detail = texte(e, groupe="Foot")
verifier("Entrée sans effet : ÉCHEC, et la zone d'écriture est vidée", (statut, e.ecriture.valeur), ("ÉCHEC", None))
e = FauxEcran(["Maman", "Foot"], ecriture_prend=False)
verifier("WhatsApp ne prend pas le texte : ÉCHEC, rien n'est parti", (texte(e, groupe="Foot")[0], e.envoyes), ("ÉCHEC", []))
e = FauxEcran(["Maman", "Foot"])
e.ecriture.valeur = "mon brouillon à moi"
verifier("un brouillon à toi dans la discussion : il n'y touche pas",
         (texte(e, groupe="Foot")[0], e.ecriture.valeur), ("ÉCHEC", "mon brouillon à moi"))
for nom, kw in [("ni contact ni groupe", {}), ("les deux à la fois", {"contact": "Mamad", "groupe": "Foot"})]:
    e = FauxEcran(["Foot"])
    verifier(f"à qui ? {nom} : ÉCHEC sans rien ouvrir", (texte(e, **kw)[0], e.appuis, e.envoyes), ("ÉCHEC", [], []))
verifier("un message vide : rien", texte(FauxEcran(["Foot"]), groupe="Foot", texte="  ")[0], "ÉCHEC")
statut, detail = asyncio.run(vocal.envoyer("Mamad", "Salut", pilote=FauxEcran(["Foot"]), groupe="Foot"))
verifier("un vocal : un contact ET un groupe, refusé avant tout", (statut, "pas les deux" in detail), ("ÉCHEC", True))


class Ouvreur:
    def __init__(self):
        self.fait = []

    def ouvrir(self, numero): self.fait.append(("numéro", numero))
    def ouvrir_nom(self, nom): self.fait.append(("nom", nom))


o = Ouvreur()
asyncio.run(vocal._ouvrir(o, "", " Famille ", None))
asyncio.run(vocal._ouvrir(o, "Mamad", "", "+336"))
verifier("le vocal ouvre le groupe par son nom, une personne par son numéro", o.fait,
         [("nom", "Famille"), ("numéro", "+336")])

# --- iMessage dans un groupe -----------------------------------------------------------------------------------------
print("\n📨 iMessage dans un groupe")
scripts = []
vrai_script = outils._applescript


def faux_script(reponse):
    async def script(source, *argv):
        scripts.append((source is outils._IMESSAGE_GROUPE, argv))
        return 0, reponse
    return script


try:
    for nom, args, reponse, attendu in [
        ("un groupe connu : envoyé", {"groupe": "Famille", "texte": "Coucou"}, "envoyé\n", "Message envoyé au groupe Famille."),
        ("aucun groupe de ce nom : rien ne part", {"groupe": "Fam", "texte": "Coucou"}, "aucun", "aucune discussion"),
        ("plusieurs groupes de ce nom : rien ne part", {"groupe": "Fam", "texte": "Coucou"}, "plusieurs", "plusieurs"),
        ("ni personne ni groupe", {"texte": "Coucou"}, "envoyé", "soit `destinataire`"),
        ("une personne et un groupe", {"destinataire": "+33 6", "groupe": "Fam", "texte": "x"}, "envoyé", "pas les deux"),
    ]:
        outils._applescript = faux_script(reponse)
        r = asyncio.run(outils.envoyer_imessage.handler(args))
        verifier(f"iMessage : {nom}", attendu in r["content"][0]["text"], True)
    verifier("iMessage : le script du groupe reçoit le nom, puis le texte", scripts[0], (True, ("Famille", "Coucou")))
    verifier("iMessage : sans destinataire clair, aucun script n'est lancé", len(scripts), 3)
finally:
    outils._applescript = vrai_script

# --- Le Gardien et les consignes ------------------------------------------------------------------------------------
print("\n🛡  Le Gardien")
J = "mcp__jervis__"
for outil, entree, attendu in [
    ("envoyer_whatsapp", {"groupe": "Foot", "texte": "J'arrive"}, "J'envoie sur WhatsApp au groupe « Foot » : « J'arrive ». Je peux ?"),
    ("envoyer_whatsapp", {"contact": "Mamad", "texte": "Ok", "nom": "Mamad"}, "J'envoie sur WhatsApp à Mamad : « Ok ». Je peux ?"),
    ("envoyer_vocal_whatsapp", {"groupe": "Famille", "texte": "Coucou"},
     "J'envoie un vocal WhatsApp au groupe « Famille » : « Coucou ». Je peux ?"),
    ("envoyer_imessage", {"groupe": "Famille", "texte": "Coucou"}, "J'envoie au groupe « Famille » : « Coucou ». Je peux ?"),
    ("envoyer_imessage", {"groupe": "Famille", "destinataire": "+33 6 1", "texte": "x"},
     "J'envoie au groupe « Famille » et à +33 6 1 : « x ». Je peux ?"),
]:
    verifier(f"la question : {outil} {sorted(entree)}", permissions.decrire(J + outil, entree), attendu)
verifier("« autorise ça désormais » vise le groupe", permissions.cible_de(J + "envoyer_whatsapp", {"groupe": "Foot", "texte": "x"}),
         "le groupe Foot")
verifier("un groupe et une personne : pas de règle", permissions.cible_de(J + "envoyer_whatsapp",
         {"groupe": "Foot", "contact": "Mamad", "texte": "x"}), None)
verifier("un message WhatsApp se confirme (hors autonome)", "envoyer_whatsapp" in permissions.JERVIS_A_CONFIRMER, True)
verifier("son résultat est écrit par le code (pas une lecture du dehors)", permissions.lit_dehors(J + "envoyer_whatsapp"), False)
verifier("consignes : les groupes, iMessage et WhatsApp", all(m in brain.CONSIGNES for m in (
    "envoyer_whatsapp", "groupe iMessage", "le nom exact du groupe")), True)
verifier("les outils WhatsApp sont déclarés", {o.name for o in vocal.OUTILS_VOCAL} <= {o.name for o in outils.OUTILS}, True)

# --- JERVIS_MEFIANCE : les questions d'après un texte venu d'ailleurs ------------------------------------------------
print("\n🔓 Méfiance")
questions: list[str] = []


async def repondre(q):
    questions.append(q)
    return False


async def apres_dehors(outil, entree):
    g = permissions.Gardien(repondre)
    g.nouvelle_demande()
    g.a_lu_dehors()
    return await g(outil, entree, None)

vraie_mefiance, vraies_confirmations = config.MEFIANCE, config.CONFIRMATIONS
try:
    config.CONFIRMATIONS = "autonome"
    for mefiance, attendu in [(True, "PermissionResultDeny"), (False, "PermissionResultAllow")]:
        config.MEFIANCE = mefiance
        avant = len(questions)
        for outil, entree in [("Bash", {"command": "osascript -e 'tell application \"Safari\" to activate'"}),
                              (J + "liberer_port", {"port": 3000, "forcer": True})]:
            r = asyncio.run(apres_dehors(outil, entree))
            verifier(f"méfiance {'oui' if mefiance else 'non'} : {outil.split('__')[-1]} après une page",
                     type(r).__name__, attendu)
        verifier(f"méfiance {'oui' if mefiance else 'non'} : {'deux questions' if mefiance else 'aucune question'}",
                 len(questions) - avant, 2 if mefiance else 0)
        r = asyncio.run(apres_dehors("mcp__mac__drag", {"app": "Finder", "from": "B1", "to": "B2"}))
        verifier(f"méfiance {'oui' if mefiance else 'non'} : glisser dans le Finder passe seul", type(r).__name__,
                 "PermissionResultAllow")
    r = asyncio.run(apres_dehors(J + "lancer_tests", {"projet": "oko"}))
    verifier("méfiance non : ses tests hors cage, même après une page", (type(r).__name__, r.updated_input.get("cage")),
             ("PermissionResultAllow", False))
    r = asyncio.run(apres_dehors("Bash", {"command": "npm install"}))
    verifier("méfiance non : une commande hors cage, telle quelle", r.updated_input.get("command"), "npm install")
    r = asyncio.run(apres_dehors("mcp__mac__drag", {"app": "Messages", "from": "B1", "to": "B2"}))
    verifier("méfiance non : glisser dans Messages reste refusé", "Pas de Messages" in getattr(r, "message", ""), True)
    r = asyncio.run(apres_dehors(J + "appeler", {"numero": "112"}))
    verifier("méfiance non : un numéro d'urgence se demande toujours", type(r).__name__, "PermissionResultDeny")
finally:
    config.MEFIANCE, config.CONFIRMATIONS = vraie_mefiance, vraies_confirmations
verifier("par défaut, la méfiance est là", vraie_mefiance, True)
verifier("les gestes sont rouverts (glisser, sélectionner, espaces, Dock)", any(
    o.endswith(("__drag", "__select_text", "__space", "__dock")) for o in outils.OUTILS_MAC_COUPES), False)
verifier("glisser compte comme un geste", "drag" in permissions.GESTES_MAC, True)
D = permissions.Decision
for politique in (permissions.politique_confiance, permissions.politique_essentielle):
    verifier(f"{politique.__name__} : ouvrir une app du Dock, seul",
             politique("mcp__mac__dock", {"action": "launch", "app": "Safari"}), D.OUI)
    verifier(f"{politique.__name__} : « Vider la corbeille » dans le Dock, il demande",
             politique("mcp__mac__dock", {"action": "right-click", "app": "Corbeille", "select": "Vider la corbeille"}),
             D.DEMANDER)

# Les fonctions de macOS qu'il appelle pour taper existent (aucune touche n'est envoyée ici)
cg = ax._graphiques()
verifier("macOS : les touches remises à une seule app", all(hasattr(cg, f) for f in (
    "CGEventCreateKeyboardEvent", "CGEventKeyboardSetUnicodeString", "CGEventPostToPid")), True)

print(f"\n{'✅' if not echecs else '❌'} groupes : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
