"""Le vocal WhatsApp : le drapeau « vocal en cours » suspend la détection d'appel et le barge-in, et tout est rétabli,
même sur une erreur ou une annulation. L'envoi lui-même se joue avec un faux WhatsApp.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_vocal.py

Rien ne part : pas de vraie app WhatsApp, pas de vraie voix, pas de vrai son. Le faux WhatsApp a les mêmes boutons
(micro, Envoyer, corbeille) et dit ce qu'on lui a fait.
"""

import asyncio
import os
import threading
import time
from collections import Counter

threading.Timer(90, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402

from jervis import brain, config, permissions, reunion, tts, vocal, voix_locale  # noqa: E402
from jervis import outils  # noqa: E402,F401  (comme Steve : outils déclare tout)

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def rien_de_suspendu() -> tuple:
    return vocal.en_cours(), tts._reserve, tts._verrou.locked()


WHATSAPP, ZOOM = (1, "net.whatsapp.WhatsApp", True), (2, "us.zoom.xos", True)

# --- Le drapeau ---------------------------------------------------------------------------------------------
print("🎙  Le drapeau « vocal en cours »")
vocal._jusqua = vocal._whatsapp_jusqua = 0.0
verifier("au repos : rien de suspendu", (vocal.en_cours(), vocal.whatsapp_ignore()), (False, False))
with vocal.suspendre():
    verifier("pendant : écoute et barge-in suspendus, WhatsApp ignoré", (vocal.en_cours(), vocal.whatsapp_ignore()),
             (True, True))
verifier("après : il t'écoute de nouveau tout de suite", vocal.en_cours(), False)
verifier("après : WhatsApp encore ignoré tant qu'il n'a pas lâché le micro", vocal.whatsapp_ignore(), True)
vocal.whatsapp_a_lache()
verifier("WhatsApp a lâché le micro : il compte de nouveau", vocal.whatsapp_ignore(), False)

try:
    with vocal.suspendre():
        raise ValueError("panne au milieu")
except ValueError:
    pass
verifier("une erreur dans le bloc : l'écoute est quand même rétablie", vocal.en_cours(), False)
vocal.whatsapp_a_lache()

with vocal.suspendre(maxi=0.05):
    time.sleep(0.1)
    verifier("le maximum passé, le drapeau tombe seul (même si le bloc ne finit pas)",
             (vocal.en_cours(), vocal.whatsapp_ignore()), (False, False))
vocal.whatsapp_a_lache()

with vocal.suspendre():
    vocal.whatsapp_a_lache()
    verifier("pendant l'enregistrement, « a lâché » ne remet pas WhatsApp en compte", vocal.whatsapp_ignore(), True)
vocal.whatsapp_a_lache()
verifier("est_whatsapp : WhatsApp et ses aides, pas Zoom",
         [vocal.est_whatsapp(a) for a in ("net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp.helper", "us.zoom.xos")],
         [True, True, False])

# --- La surveillance des appels ---------------------------------------------------------------------------------
print("\n📞 La surveillance des appels pendant un vocal")
micro = {"liste": []}
temps = {"t": 0.0}
vu: list = []


def surveillance() -> reunion.Surveillance:
    vu.clear()
    return reunion.Surveillance(lambda apps: vu.append(("début", apps)), lambda: vu.append("fin"),
                                lire=lambda: micro["liste"], horloge=lambda: temps["t"])


s = surveillance()
with vocal.suspendre():
    micro["liste"] = [WHATSAPP]
    s.regarder()
    verifier("WhatsApp au micro pendant le vocal : pas un appel (il ne se tait pas)", (vu, s.en_appel), ([], False))
    micro["liste"] = [WHATSAPP, ZOOM]
    s.regarder()
    verifier("un vrai appel Zoom pendant le vocal reste vu", vu, [("début", ["us.zoom.xos"])])

s = surveillance()
with vocal.suspendre():
    micro["liste"] = [WHATSAPP]
    s.regarder()
temps["t"] += 2
s.regarder()
verifier("juste après l'envoi, WhatsApp garde le micro un instant : toujours pas un appel", vu, [])
micro["liste"] = []
temps["t"] += 2
s.regarder()
verifier("WhatsApp a lâché le micro : il ne l'ignore plus", vocal.whatsapp_ignore(), False)
micro["liste"] = [WHATSAPP]
temps["t"] += 2
s.regarder()
verifier("ensuite, un vrai appel WhatsApp est de nouveau vu", vu, [("début", ["net.whatsapp.WhatsApp"])])

s = surveillance()
micro["liste"] = [ZOOM]
s.regarder()
with vocal.suspendre():
    micro["liste"] = [WHATSAPP]
    temps["t"] += config.APPEL_FIN_S + 1
    s.regarder()
    verifier("un appel déjà en cours n'est pas déclaré fini pendant le vocal", (vu[-1:], s.en_appel),
             ([("début", ["us.zoom.xos"])], True))
vocal.whatsapp_a_lache()

# La vérification « es-tu en appel ? » avant d'envoyer : WhatsApp qui finit un vocal n'en est pas un.
_appel_en_cours = vocal._appel_en_cours
lu = {"liste": [WHATSAPP, ZOOM]}
reunion.processus_audio = lambda: lu["liste"]
verifier("avant l'envoi : Zoom et WhatsApp au micro → en appel", _appel_en_cours(), ["net.whatsapp.WhatsApp", "us.zoom.xos"])
vocal._whatsapp_jusqua = time.monotonic() + 5
verifier("avant l'envoi : WhatsApp qui finit un vocal ne compte pas", _appel_en_cours(), ["us.zoom.xos"])
vocal.whatsapp_a_lache()


# --- L'envoi, avec un faux WhatsApp --------------------------------------------------------------------------------
class FauxWhatsApp:
    def __init__(self, micro=True, enregistre=True, bulle=True, pret=None):
        self.micro, self.enregistre, self.bulle, self._pret = micro, enregistre, bulle, pret
        self.brouillon = self.envoye = False
        self.appuis: list[str] = []
        self.ouvert = None

    def pret(self): return self._pret
    def ouvrir(self, numero): self.ouvert = numero
    def devant(self): self.appuis.append("devant")
    def existe(self, ident): return ident == vocal.ENVOYER and self.brouillon

    def appuyer(self, ident):
        self.appuis.append(ident)
        if ident == vocal.MICRO:
            if not self.micro:
                return False
            self.brouillon = self.enregistre
        elif ident == vocal.ENVOYER:
            if not self.brouillon:
                return False
            self.brouillon, self.envoye = False, self.bulle
        elif ident == vocal.CORBEILLE:
            self.brouillon = False
        return True

    def vocaux(self):
        return Counter({("", "AXGroup", "Message vocal"): 1 + self.envoye})


APPELS: list[str] = []
vocal._appel_en_cours = lambda: list(APPELS)
NUMEROS: list[str] = []


async def faux_numero(contact):
    NUMEROS.append(contact)
    return "+33612345678"


NUMERO_DE, FABRIQUER = vocal.numero_de, vocal.fabriquer
vocal.numero_de = faux_numero
SON = {"s": np.zeros(48000, dtype=np.float32)}
vocal.fabriquer = lambda texte: SON["s"]
JOUE: list = []           # pendant le son : (vocal en cours, parole réservée, verrou de la voix pris)
MODE = {"m": "ok"}
PILOTE: dict = {}


async def faux_jouer(son, appareil=""):
    JOUE.append(rien_de_suspendu())
    if MODE["m"] == "panne":
        raise RuntimeError("haut-parleur parti")
    if MODE["m"] == "long":
        await asyncio.sleep(30)
    if MODE["m"] == "reduite":
        PILOTE["p"].brouillon = False  # la fenêtre réduite : WhatsApp annule le brouillon
    return MODE["m"] != "coupee"


tts.jouer_tel_quel = faux_jouer


async def essai(mode="ok", **kw):
    MODE["m"] = mode
    JOUE.clear()
    PILOTE["p"] = pilote = FauxWhatsApp(**kw)
    resultat = await vocal.envoyer("Mamad", "Bonjour Mamad, c'est Steve.", pilote=pilote)
    return resultat, pilote


async def scenarios():
    print("\n📤 L'envoi")
    (statut, detail), p = await essai()
    verifier("envoyé : statut", statut, "ENVOYÉ")
    verifier("envoyé : la durée dans le détail", "vocal de 1 s envoyé" in detail, True)
    verifier("envoyé : micro, puis Envoyer, pas de corbeille", p.appuis, ["devant", vocal.MICRO, vocal.ENVOYER])
    verifier("envoyé : la discussion du bon numéro", p.ouvert, "+33612345678")
    verifier("pendant le son : écoute suspendue, parole réservée, voix verrouillée", JOUE, [(True, True, True)])
    verifier("envoyé : tout est rétabli", rien_de_suspendu(), (False, False, False))

    (statut, detail), p = await essai(bulle=False)
    verifier("pas de bulle après Envoyer : INCERTAIN (et pas de renvoi)", (statut, "Ne le renvoie pas" in detail),
             ("INCERTAIN", True))

    (statut, detail), p = await essai("reduite")
    verifier("brouillon disparu (fenêtre réduite) : ÉCHEC avec la raison", (statut, "réduite" in detail), ("ÉCHEC", True))
    verifier("brouillon disparu : pas d'Envoyer, la corbeille", p.appuis[-1:], [vocal.CORBEILLE])

    (statut, detail), p = await essai("panne")
    verifier("son en panne : ÉCHEC « panne imprévue »", (statut, detail), ("ÉCHEC", "panne imprévue (RuntimeError)"))
    verifier("son en panne : le brouillon part à la corbeille", (vocal.CORBEILLE in p.appuis, p.envoye), (True, False))
    verifier("son en panne : tout est rétabli", rien_de_suspendu(), (False, False, False))

    (statut, detail), p = await essai("coupee")
    verifier("voix coupée pendant l'enregistrement : ÉCHEC, corbeille", (statut, p.appuis[-1]), ("ÉCHEC", vocal.CORBEILLE))

    (statut, detail), p = await essai(micro=False)
    verifier("pas de bouton du vocal : ÉCHEC, et rien à jeter", (statut, vocal.CORBEILLE in p.appuis), ("ÉCHEC", False))

    (statut, detail), p = await essai(enregistre=False)
    verifier("l'enregistrement ne démarre pas : ÉCHEC (le micro ?) et corbeille",
             (statut, "micro" in detail, p.appuis[-1]), ("ÉCHEC", True, vocal.CORBEILLE))
    verifier("l'enregistrement ne démarre pas : le son n'a pas joué", JOUE, [])

    (statut, detail), p = await essai(pret="il manque l'autorisation Accessibilité")
    verifier("sans l'Accessibilité : ÉCHEC avec la raison, WhatsApp pas touché",
             (statut, detail, p.ouvert, p.appuis), ("ÉCHEC", "il manque l'autorisation Accessibilité", None, []))

    APPELS[:] = ["us.zoom.xos"]
    (statut, detail), p = await essai()
    APPELS.clear()
    verifier("en appel : il refuse de jouer un vocal dans ton appel", (statut, "en appel (Zoom)" in detail), ("ÉCHEC", True))
    verifier("en appel : WhatsApp pas touché, aucun son", (p.ouvert, p.appuis, JOUE), (None, [], []))

    SON["s"] = np.zeros(int(48000 * config.VOCAL_WHATSAPP_MAX_S), dtype=np.float32)
    (statut, detail), p = await essai()
    SON["s"] = np.zeros(48000, dtype=np.float32)
    verifier("trop long : ÉCHEC « raccourcis », rien d'ouvert", (statut, "raccourcis" in detail, p.ouvert),
             ("ÉCHEC", True, None))

    maxi = config.VOCAL_WHATSAPP_MAX_S
    config.VOCAL_WHATSAPP_MAX_S = 6.0
    SON["s"] = np.zeros(4800, dtype=np.float32)
    (statut, detail), p = await essai("long")
    config.VOCAL_WHATSAPP_MAX_S = maxi
    SON["s"] = np.zeros(48000, dtype=np.float32)
    verifier("enregistrement bloqué : ÉCHEC au bout du maximum", (statut, "dépassé 6 s" in detail), ("ÉCHEC", True))
    verifier("enregistrement bloqué : corbeille, et tout est rétabli", (p.appuis[-1], rien_de_suspendu()),
             (vocal.CORBEILLE, (False, False, False)))

    # Tu lui dis « stop » ailleurs (le chat), le cerveau coupe l'outil : la tâche est annulée en plein son.
    MODE["m"] = "long"
    JOUE.clear()
    PILOTE["p"] = p = FauxWhatsApp()
    tache = asyncio.ensure_future(vocal.envoyer("Mamad", "Bonjour", pilote=p))
    for _ in range(100):
        if JOUE:
            break
        await asyncio.sleep(0.05)
    tache.cancel()
    try:
        await tache
    except asyncio.CancelledError:
        pass
    verifier("annulé en plein son : rien ne part, la corbeille", (p.envoye, p.appuis[-1]), (False, vocal.CORBEILLE))
    verifier("annulé en plein son : tout est rétabli", rien_de_suspendu(), (False, False, False))

    # Une réponse qui arrive pendant le vocal attend la fin (le verrou de la voix), elle ne s'y glisse pas.
    MODE["m"] = "ok"
    PILOTE["p"] = p = FauxWhatsApp()
    apres_envoi: list[bool] = []

    async def reponse():
        await asyncio.sleep(0.6)  # pendant l'enregistrement
        async with tts._verrou:
            apres_envoi.append(p.envoye)

    _, (statut, _) = await asyncio.gather(reponse(), vocal.envoyer("Mamad", "Bonjour", pilote=p))
    verifier("une réponse pendant le vocal attend que le vocal soit envoyé", (statut, apres_envoi), ("ENVOYÉ", [True]))


asyncio.run(scenarios())

# --- La voix du vocal, le numéro ------------------------------------------------------------------------------------
print("\n🗣  La voix du vocal et le numéro")
voix_locale.demarrer = lambda: None
for nom, active, prete, mot in [("voix locale coupée", False, True, "coupée"),
                                ("voix locale pas chargée", True, False, "pas prête")]:
    voix_locale.active, voix_locale.attendre = (lambda a=active: a), (lambda s=0, p=prete: p)
    try:
        FABRIQUER("Bonjour Mamad.")
        verifier(f"{nom} : pas de vocal", "fabriqué", "Echec")
    except vocal.Echec as e:
        verifier(f"{nom} : Echec qui le dit", mot in str(e), True)
try:
    FABRIQUER("   ")
    verifier("rien à dire : pas de vocal", "fabriqué", "Echec")
except vocal.Echec as e:
    verifier("rien à dire : Echec", "rien à dire" in str(e), True)

voix_locale.active, voix_locale.attendre = (lambda: True), (lambda s=0: True)
voix_locale.dire = lambda phrase: np.ones(24000, dtype=np.float32) * 0.1
son = FABRIQUER("Bonjour Mamad. C'est Steve.")
verifier("deux phrases : sa voix, avec des blancs entre elles", (son.dtype, len(son) > 2 * 24000 * 0.5), (np.float32, True))
voix_locale.dire = lambda phrase: None
try:
    FABRIQUER("Bonjour.")
    verifier("une phrase que sa voix ne sait pas dire : Echec", "fabriqué", "Echec")
except vocal.Echec:
    verifier("une phrase que sa voix ne sait pas dire : Echec", True, True)

FICHES = ("Mamad Diallo | tél +33 6 12 34 56 78 (Mobile) | mail m@x.fr\n"
          "Mamadou Sy | tél +33 1 40 00 00 00 (Travail) | tél +33 7 00 00 00 01 (iPhone)")
for nom, fiches, qui, attendu in [
    ("le prénom exact départage Mamad et Mamadou", FICHES, "Mamad", "+33 6 12 34 56 78"),
    ("le portable d'abord", FICHES, "Mamadou", "+33 7 00 00 00 01"),
    ("une seule fiche, un seul numéro", "Léa | tél 06 11 22 33 44 (Domicile)", "Léa", "06 11 22 33 44"),
    ("sans accent ni casse", "Zoé Martin | tél +33 6 00 00 00 02 (portable)", "zoe", "+33 6 00 00 00 02"),
]:
    verifier(f"numéro : {nom}", vocal.choisir_numero(fiches, qui), attendu)
for nom, fiches, qui in [("personne", "", "Mamad"), ("une fiche sans téléphone", "Mamad | mail m@x.fr", "Mamad"),
                         ("deux Mamad : il ne choisit pas au hasard",
                          "Mamad A | tél +33 6 1 (Mobile)\nMamad B | tél +33 6 2 (Mobile)", "Mamad")]:
    try:
        vocal.choisir_numero(fiches, qui)
        verifier(f"numéro : {nom} → Echec", "choisi", "Echec")
    except vocal.Echec:
        verifier(f"numéro : {nom} → Echec", True, True)
try:
    asyncio.run(NUMERO_DE("mamad@x.fr"))
    verifier("une adresse mail : WhatsApp a besoin d'un numéro", "accepté", "Echec")
except vocal.Echec as e:
    verifier("une adresse mail : WhatsApp a besoin d'un numéro", "numéro" in str(e), True)
verifier("un numéro tel quel : au format international", asyncio.run(NUMERO_DE("+33 6 12 34 56 78")), "+33612345678")

# --- Le gardien, les consignes ----------------------------------------------------------------------------------
print("\n🛡  Le gardien et les consignes")
OUTIL = "mcp__jervis__envoyer_vocal_whatsapp"
ENTREE = {"contact": "Mamad", "texte": "Bonjour Mamad", "nom": "Mamad"}
verifier("gardien : un vocal se confirme", "envoyer_vocal_whatsapp" in permissions.JERVIS_A_CONFIRMER, True)
verifier("gardien : la question dit à qui et quoi", permissions.decrire(OUTIL, ENTREE),
         "J'envoie un vocal WhatsApp à Mamad : « Bonjour Mamad ». Je peux ?")
verifier("gardien : son résultat est écrit par le code (pas une lecture du dehors)", permissions.lit_dehors(OUTIL), False)
verifier("gardien : « autorise ça désormais » vise le contact", permissions.cible_de(OUTIL, ENTREE), "Mamad")
verifier("consignes : « envoie-lui un vocal » → l'outil, jamais de texte à la place",
         ("envoyer_vocal_whatsapp" in brain.CONSIGNES, "fichier audio à la place" in brain.CONSIGNES), (True, True))
verifier("l'outil est déclaré avec les autres", "envoyer_vocal_whatsapp" in [o.name for o in vocal.OUTILS_VOCAL], True)

print(f"\n{'✅' if not echecs else '❌'} vocal : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
