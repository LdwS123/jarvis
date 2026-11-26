"""Steve plus naturel : phrases d'exemple (simples, complexes, coupées, mal transcrites) et comportement attendu.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_naturel.py

Sans micro ni Claude : les oreilles, la voix et Claude sont remplacés par des faux.
"""

import asyncio
import os
import re
import threading
import time
import types

threading.Timer(80, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock  # noqa: E402

from jervis import audio, brain, chat, config, fond, outils, stt, tts  # noqa: E402
from jervis import __main__ as m  # noqa: E402
from jervis.naturel import (avis_limite, corriger_transcription, decoder_epellation, epellation_en_cours,  # noqa: E402
                            hesite, hors_alphabet, nettoyer, recoller)

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


# --- 1. Ce qu'il dit : chaque phrase de sa réponse, juste avant la voix -------------------------------------------
# (phrase de Claude, première phrase ?, juste après une coupure ?, ce qui part vraiment à la voix)
REPONSES = [
    ("Bien sûr ! Je lance Spotify.", True, False, "Je lance Spotify."),
    ("Bien sûr !", True, False, ""),                                         # creuse : il passe à la suivante
    ("Oui, bien sûr.", True, False, "Oui."),
    ("Oui, bien sûr, je t'ouvre ça.", True, False, "Oui, je t'ouvre ça."),
    ("Excellente question, en fait c'est le 14 juillet.", True, False, "En fait c'est le 14 juillet."),
    ("Pas de souci, je regarde ton agenda.", True, False, "Je regarde ton agenda."),
    ("Parfait.", True, False, "Parfait."),                                   # seul, ça confirme : il le garde
    ("Je comprends, t'as eu une sale journée.", True, False, "T'as eu une sale journée."),
    ("Tu veux que je fasse quelque chose de précis là-dessus ?", False, False, ""),
    ("N'hésite pas si t'as besoin.", False, False, ""),
    ("Dis-moi ce qu'il te faut.", False, False, ""),
    ("Je t'ouvre le détail de l'un d'eux ?", False, False, ""),
    ("Autre chose ?", False, False, ""),
    ("Et toi, t'as fait quoi ce week-end ?", False, False, "Et toi, t'as fait quoi ce week-end ?"),  # vraie relance
    ("Ta phrase s'est coupée après « un coup de f ».", True, False, "Oui ?"),
    ("Et voir si quoi ?", True, False, "Et voir si quoi ?"),
    ("Ta phrase s'est encore coupée.", False, False, ""),
    ("Pardon ! Je mets du jazz.", True, True, "Je mets du jazz."),           # coupé : pas d'excuses
    ("Pardon, je n'ai pas trouvé le fichier.", True, False, "Pardon, je n'ai pas trouvé le fichier."),
    ("Il fait 12 degrés, prends un pull.", True, False, "Il fait 12 degrés, prends un pull."),
]
for phrase, premiere, coupure, attendu in REPONSES:
    verifier(f"nettoyer « {phrase} »", nettoyer(phrase, premiere, coupure), attendu)

# --- 2. Ce qu'il entend : les mots que la reconnaissance vocale écorche ---------------------------------------------
TRANSCRIPTIONS = [
    ("ouvre load code dans le projet Jervis", "ouvre Claude Code dans le projet Jervis"),
    ("mets un prompt dans clode code", "mets un prompt dans Claude Code"),
    ("envoie un message what's app à Léa", "envoie un message WhatsApp à Léa"),
    ("regarde sur watsap", "regarde sur WhatsApp"),
    ("lis mes g mail", "lis mes Gmail"),
    ("ouvre curseur", "ouvre Cursor"),
    ("déplace le curseur en haut", "déplace le curseur en haut"),            # la souris : pas touché
    ("quelle heure il est", "quelle heure il est"),
    # Les noms que la transcription déforme le plus dans jervis.log
    ("poste ça sur Link Zin", "poste ça sur LinkedIn"),
    ("mon profil linked in", "mon profil LinkedIn"),
    ("je pars à Frisco", "je pars à San Francisco"),
    ("je pars à San Francisco", "je pars à San Francisco"),                  # déjà bon : pas doublé
    ("cherche des Vici en France", "cherche des VC en France"),
    ("veni, vidi, vici", "veni, vidi, vici"),                                 # le latin reste intact
    ("regarde Legora", "regarde Legora"),
    ("ouvre lé gora", "ouvre Legora"),
    ("lance une zapière", "lance une Zapier"),
    # Les lettres épelées sont recollées
    ("Jessica T A double L", "Jessica TALL"),
    ("Jessica T, A, double L, et après", "Jessica TALL, et après"),
    ("C V G Dave.", "CVG Dave."),
    ("passe au plan B. C est bon", "passe au plan B. C est bon"),             # une lettre seule : intacte
    ("il y a A et B", "il y a A et B"),                                         # deux lettres : pas un mot
    ("j'ai un A. Un B", "j'ai un A. Un B"),
]
for entendu, attendu in TRANSCRIPTIONS:
    verifier(f"transcription « {entendu} »", corriger_transcription(entendu), attendu)

# Épellation : « T A » arrive en deux morceaux, il attend la suite au lieu de répondre « continue d'épeler ».
for texte, en_cours in [("Jessica T A", True), ("C V", True), ("Jessica T A double", True), ("c'est T, A", True),
                        ("passe au plan B", False), ("Jessica TALL", False), ("quelle heure il est", False),
                        ("mets du jazz.", False)]:
    verifier(f"épellation en cours ? « {texte} »", epellation_en_cours(texte), en_cours)
verifier("épellation recollée entre deux morceaux", decoder_epellation("Jessica T A" + " " + "double L"), "Jessica TALL")
verifier("triple", decoder_epellation("W triple W"), "WWWW")
for texte, etranger in [("Щяшки, весь яркий день", True), ("今天天气很好我们走吧", True), ("مرحبا بكم في القناة", True),
                        ("ouvre Cursor", False), ("tu peux lire ce message de Дмитрий", False), ("ok", False), ("", False)]:
    verifier(f"hors alphabet ? « {texte} »", hors_alphabet(texte), etranger)

# --- 3. Phrase finie ou juste un début ? ----------------------------------------------------------------------------
PHRASES = [
    ("quelle heure il est", False),                                          # simples
    ("mets de la musique", False),
    ("ouvre Cursor", False),
    ("c'est bon merci.", False),
    ("j'ai eu une journée de fou au boulot, franchement", False),
    ("compare les vols pour Lisbonne vendredi et envoie le moins cher à Léa", False),   # complexe
    ("fais-le comme il faut", False),
    ("passe au plan B", False),
    ("est-ce que tu peux", True),                                            # coupées
    ("Bah il me faut que tu regardes", True),
    ("envoie un message à", True),
    ("mets-moi un coup de f", True),
    ("il faut que tu", True),
    ("tu peux supprimer", True),
    ("je voudrais que tu lises", True),
    ("Watt, regarde les messages de", True),                                 # mal transcrite et coupée
]
for phrase, coupee in PHRASES:
    verifier(f"coupée ? « {phrase} »", m.phrase_coupee(phrase), coupee)

# --- 4. La télé et les vidéos captées par le micro ------------------------------------------------------------------
verifier("vidéo en anglais", m.en_anglais("so if you think about it this is what you have to do"), True)
verifier("toi en français", m.en_anglais("bon en fait je pense que c'est mieux"), False)
verifier("écho de sa propre voix", m.ajout_probable("il fait douze degrés", "Il fait douze degrés."), False)
verifier("toi qui ajoutes", m.ajout_probable("et envoie aussi à Léa", "J'envoie à Scott."), True)
for bruit in ["Mm-hmm.", "Hmm hmm", "Euh…", "Ah. Oh.", "Hein ?"]:
    verifier(f"« {bruit} » pendant sa réponse : il continue", m.ajout_probable(bruit, "Je lis tes mails."), False)
    verifier(f"« {bruit} » tout seul : pas une demande", bool(m.BRUITS.match(bruit)), True)
for vrai in ["Ouais.", "Ok.", "Euh, mets du jazz", "Ah bon ?", "Oh, et envoie à Léa"]:
    verifier(f"« {vrai} » est gardé", bool(m.BRUITS.match(vrai)), False)


# --- 5. Le cerveau : réponse de Claude → ce qui part vraiment à la voix ---------------------------------------------
def delta(txt):
    return StreamEvent(uuid="u", session_id="s", parent_tool_use_id=None,
                       event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": txt}})


class FauxClaude:
    def __init__(self, morceaux):
        self.morceaux = morceaux

    async def query(self, t): pass
    async def interrupt(self): pass
    async def get_mcp_status(self): return {"mcpServers": []}

    async def receive_response(self):
        for txt in self.morceaux:
            yield delta(txt)
        yield AssistantMessage(content=[TextBlock("".join(self.morceaux))], model="m", parent_tool_use_id=None)


async def reponse_dite(demande: str, morceaux: list[str]) -> list[str]:
    dit = []

    async def parler(p): dit.append(p)
    async def confirmer(q): return True
    c = brain.Cerveau(parler, confirmer, journal=lambda *_: None, anticiper=None, patience=False)
    c._client = FauxClaude(morceaux)
    await c.demander(demande)
    return dit


outils.noter_echange = lambda *a, **_k: None
CERVEAU = [
    ("mets Spotify", ["Bien sûr ! ", "Je lance ", "Spotify. ", "Tu veux que je fasse autre chose ?"], ["Je lance Spotify."]),
    ("ouvre Cursor", ["Bien sûr !"], ["OK."]),                               # tout était creux : il confirme quand même
    ("(interrompu) mets plutôt du jazz", ["Pardon ! ", "Je mets du jazz."], ["Je mets du jazz."]),
    ("mets-moi un coup de f", ["Ta phrase s'est coupée après « un coup de f ». ", "Tu disais ?"], ["Oui ?", "Tu disais ?"]),
    ("j'ai trop mal dormi", ["Aïe. ", "T'as veillé tard ?"], ["Aïe.", "T'as veillé tard ?"]),
]


async def tester_cerveau():
    for demande, morceaux, attendu in CERVEAU:
        verifier(f"cerveau « {demande} »", await reponse_dite(demande, morceaux), attendu)

asyncio.run(tester_cerveau())


# --- 5 bis. Limite d'abonnement atteinte : une phrase en français, une seule fois ------------------------------------
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from claude_agent_sdk import RateLimitEvent  # noqa: E402
from claude_agent_sdk.types import RateLimitInfo  # noqa: E402

from jervis import compteur  # noqa: E402

AVIS_ANGLAIS = "You've hit your 5-hour limit · resets 9:50pm (Europe/Paris) · out of usage credits"
AVIS_FRANCAIS = "Tu as atteint la limite de ton abonnement, ça se libère à 21 h 50."


def texte(txt):
    return AssistantMessage(content=[TextBlock(txt)], model="m", parent_tool_use_id=None)


def refus():
    return RateLimitEvent(RateLimitInfo(status="rejected", rate_limit_type="five_hour", utilization=1.0), "u", "s")


class FauxClaudeAuBout(FauxClaude):
    async def receive_response(self):
        for message in self.morceaux:
            yield message


async def tester_limite():
    async def dit_avec(messages):
        compteur.LIMITES = Path(tempfile.mkdtemp()) / "limites.json"  # jamais ton vrai fichier de limites
        dit = []

        async def parler(p): dit.append(p)
        async def confirmer(q): return True
        c = brain.Cerveau(parler, confirmer, journal=lambda *_: None, anticiper=None, patience=False)
        c._client = FauxClaudeAuBout(messages)
        await c.demander("quelle heure il est")
        return dit

    alerte = "Attention, tu as atteint la limite de ton abonnement pour la fenêtre de 5 heures."
    verifier("limite : l'avis anglais seul → dit en français", await dit_avec([texte(AVIS_ANGLAIS)]), [AVIS_FRANCAIS])
    verifier("limite : l'avis arrivé mot à mot → dit en français",
             await dit_avec([delta(AVIS_ANGLAIS[:30]), delta(AVIS_ANGLAIS[30:]), texte(AVIS_ANGLAIS)]), [AVIS_FRANCAIS])
    verifier("limite : alerte puis avis anglais → une seule phrase",
             await dit_avec([refus(), texte(AVIS_ANGLAIS)]), [alerte])
    verifier("limite : avis anglais puis alerte → une seule phrase",
             await dit_avec([texte(AVIS_ANGLAIS), refus()]), [AVIS_FRANCAIS])
    verifier("limite : l'avis après une vraie phrase → les deux, en français",
             await dit_avec([texte("Je regarde ça.\n" + AVIS_ANGLAIS)]), ["Je regarde ça.", AVIS_FRANCAIS])

limites_avant = compteur.LIMITES
asyncio.run(tester_limite())
compteur.LIMITES = limites_avant


class FauxClaudeLent(FauxClaude):
    """Claude qui met `avant` secondes à écrire, puis écrit sa phrase mot à mot."""
    def __init__(self, morceaux, avant):
        super().__init__(morceaux)
        self.avant = avant

    async def receive_response(self):
        await asyncio.sleep(self.avant)
        for txt in self.morceaux:
            await asyncio.sleep(0.1)
            yield delta(txt)
        yield AssistantMessage(content=[TextBlock("".join(self.morceaux))], model="m", parent_tool_use_id=None)


async def avec_patience(morceaux, avant):
    dit = []

    async def parler(p): dit.append(p)
    async def confirmer(q): return True
    vrai_s, config.PATIENCE_S = config.PATIENCE_S, 0.3
    try:
        c = brain.Cerveau(parler, confirmer, journal=lambda *_: None, anticiper=None, patience=True)
        c._client = FauxClaudeLent(morceaux, avant)
        await c.demander("raconte")
    finally:
        config.PATIENCE_S = vrai_s
    return dit

# Sa phrase commence à s'écrire juste avant le délai de patience : pas de « Voyons… » collé devant.
dit = asyncio.run(avec_patience(["Bon, ", "en fait ", "c'est simple."], avant=0.15))
verifier("pas de « Voyons… » quand sa phrase arrive", dit, ["Bon, en fait c'est simple."])
# Rien du tout pendant longtemps (il travaille) : là, le « Voyons… » a du sens.
dit = asyncio.run(avec_patience(["C'est fait."], avant=0.6))
verifier("« Voyons… » quand il travaille en silence", dit[0] in config.PHRASES_PATIENCE and dit[1:], ["C'est fait."])

# --- 5 bis. « Stop » : tout ce qui veut dire stop, et seulement ça -------------------------------------------------
STOPS = [  # (ce que tu dis, un stop et rien d'autre ?, ce qui reste à faire après)
    ("Stop.", True, ""),
    ("Stop, stop, stop !", True, ""),
    ("Steve, stop", True, ""),
    ("Stoppe !", True, ""),
    ("Arrête-toi.", True, ""),
    ("Arrêtes s'il te plaît", True, ""),
    ("non non arrête", True, ""),
    ("Chuuut", True, ""),
    ("Tais-toi deux secondes", True, ""),
    ("Ok, ça suffit maintenant.", True, ""),
    ("Stop, mets du jazz", False, "mets du jazz"),
    ("Arrête tout", False, "tout"),
    ("Attends, envoie plutôt à Paul", False, "envoie plutôt à Paul"),
    ("mets de la musique", False, "mets de la musique"),
    ("je vais m'arrêter là", False, "je vais m'arrêter là"),     # « arrêter » n'est pas « arrête »
]
for dit, net, reste in STOPS:
    verifier(f"stop net ? « {dit} »", m.stop_net(dit), net)
    verifier(f"reste après stop : « {dit} »", m.reste_apres_stop(dit), reste)
for tape, stop in [("stop", True), ("Stop stop", True), ("arrête tout et relance", True), ("Steve, attends", True),
                   ("ok stop", True), ("c'est bon pour moi, envoie", False), ("envoie-le, c'est bon", False),
                   ("lis mes mails", False), ("comment je l'arrête ?", False)]:
    verifier(f"tapé, commence par stop ? « {tape} »", m.commence_par_stop(tape), stop)
INF = float("inf")
verifier("par-dessus un « Je regarde. » qu'il vient de finir : pas une coupure",
         m.sur_un_voyons([[10.0, "Je regarde."]], 10.2), True)
verifier("par-dessus un « Voyons… » qu'il dit encore", m.sur_un_voyons([[INF, "Voyons…"]], 3.0), True)
verifier("par-dessus une vraie phrase : coupure", m.sur_un_voyons([[INF, "Ton vol part à 9 h."]], 3.0), False)
verifier("un « Voyons… » fini depuis longtemps ne compte plus", m.sur_un_voyons([[5.0, "Voyons…"]], 9.0), False)
verifier("écho : « stop » qu'il vient de dire lui-même", m.est_interruption("stop", "Je mets stop au minuteur."), False)
verifier("écho : un « arrête » qu'il n'a pas dit", m.est_interruption("arrête", "Je regarde tes mails."), True)


# --- 6. En vrai : phrase coupée → « Oui ? » sans Claude, et lui couper la parole -----------------------------------
T0 = time.monotonic()
FIN = threading.Event()
SCRIPT: list = []
dit_par_steve: list[str] = []
dit_quand: list[float] = []   # à quel moment il a commencé chaque phrase
recu: list[str] = []
PARTIE = [0]                  # change à chaque scénario : les écoutes du scénario d'avant s'arrêtent


def t():
    return time.monotonic() - T0


class FauxOreilles:
    haut_parleur = None
    enceinte = None
    en_pause = False
    anti_echo = True

    def apprendre_voix(self, phrase, sure): return None
    def fermer(self): pass

    def ecouter_phrase(self, attente=None, arret=None, duree_max=None, repartir_a_zero=False, **kw):
        # Une ligne du script : (quand ta phrase est finie, ce que tu dis[, quand tu as commencé à parler]).
        debut, partie, tu_parles = t(), PARTIE[0], kw.get("quand_tu_parles")
        while not FIN.is_set() and partie == PARTIE[0]:
            if arret is not None and arret.is_set():
                return None
            if SCRIPT and len(SCRIPT[0]) > 2 and SCRIPT[0][2] <= t() and tu_parles:
                tu_parles(True)   # le micro entend ta voix : il doit se retenir de parler
                tu_parles = None
            if SCRIPT and SCRIPT[0][0] <= t():
                audio = SCRIPT.pop(0)[1]  # « (autre voix) … » : une voix que l'empreinte ne reconnaît pas
                inconnue = audio.startswith("(autre voix) ") and kw.get("garder_inconnue", False)
                if audio.startswith("(autre voix) ") and not inconnue:
                    continue  # sans garder_inconnue, Oreilles l'aurait ignorée
                return types.SimpleNamespace(audio=audio.removeprefix("(autre voix) "), reveil=False,
                                             silence=0.3, inconnue=inconnue)
            if attente is not None and t() - debut > attente:
                return None
            time.sleep(0.02)
        return None


async def faux_parler(texte):
    dit_par_steve.append(texte)
    dit_quand.append(round(t(), 2))
    for _ in range(10):
        if tts._tais_toi:
            return
        await asyncio.sleep(0.03)


async def rien(self): await asyncio.sleep(3600)


async def rien_a_precharger(phrases): pass


GLISSES: list[str] = []      # ce que tu lui as dit par-dessus pendant son travail
TRAVAUX_FINIS: list[str] = []


class FauxCerveau:
    def __init__(self, parler, confirmer, **kw):
        self.au_travail = False
        self.parler = parler
        self._interrompu = False
        self.derniere_reponse = ""
        self.premiere_phrase = None

    async def demarrer(self): pass
    async def arreter(self): pass
    def fin_de_conversation(self): pass
    def couper(self, tout=False): self._interrompu = True
    def pas_lus(self): return ""
    def lu_dehors(self): return False

    def glisser(self, texte):
        if not self.au_travail or self._interrompu:
            return False
        GLISSES.append(texte)
        return True

    async def demander(self, texte):
        self._interrompu = False
        tts._tais_toi = False
        recu.append(texte)
        if "site" in texte:  # un vrai travail : des outils, et il raconte entre deux
            self.au_travail = True
            for p in ["J'ouvre le site.", "Je me connecte.", "Je remplis le formulaire.", "C'est publié."]:
                if self._interrompu:
                    break
                if not GLISSES:  # (le vrai cerveau se tait aussi jusqu'à t'avoir lu)
                    await self.parler(p)
                await asyncio.sleep(0.4)   # un outil
            else:
                TRAVAUX_FINIS.append(texte)
            self.au_travail = False
            return
        await asyncio.sleep(1.0 if "logs" in texte else 0.1)   # « logs » : une demande qui le fait travailler
        phrases = (["Il était une fois un dragon.", "Il vivait dans une grotte.", "Un jour il sortit.",
                    "Il vit un chevalier.", "Fin de l'histoire."] if "histoire" in texte else ["C'est fait."])
        for p in phrases:
            if self._interrompu:
                return
            await self.parler(p)


audio.Oreilles = FauxOreilles
stt.transcrire = lambda a, *x: a
stt.prechauffer = lambda: None
tts.parler = faux_parler
tts.precharger = rien_a_precharger
tts.bip = tts.bip_fin = lambda: None
tts.brancher = lambda hp: None
tts.taire = lambda: setattr(tts, "_tais_toi", True)
fond.Programmateur.tourner = rien
m.Cerveau = FauxCerveau
STANDARDS: list = []


class StandardSuivi(fond.Standard):
    def __init__(self):
        super().__init__()
        STANDARDS.append(self)


m.Standard = StandardSuivi
config.SUITE_PHRASE_S = 2.0        # une phrase en l'air : il attend la suite (sans rien dire)
config.SUITE_SANS_POINT_S = 0.2    # une phrase sans point final : il attend un peu
config.FOLLOW_UP_S = config.FOLLOW_UP_QUESTION_S = 1.5


def jouer(script, duree, evenements=(), tape=()):
    """Joue une conversation : ce que tu dis (le script), puis ce qu'il a reçu et ce qu'il a dit.
    `evenements` : (quand, genre, texte) que ses tâches de fond lui postent pendant ce temps.
    `tape` : (quand, texte) que tu tapes dans le chat du tableau de bord."""
    global T0
    PARTIE[0] += 1
    SCRIPT[:] = script
    dit_par_steve.clear(); dit_quand.clear(); recu.clear()
    tts._tais_toi = False
    T0 = time.monotonic()
    avant = len(STANDARDS)

    async def poster():
        while len(STANDARDS) == avant:
            await asyncio.sleep(0.01)
        for quand, genre, texte in evenements:
            await asyncio.sleep(max(0.0, quand - t()))
            STANDARDS[-1].poster(fond.Evenement(genre, texte))

    for quand, texte in tape:
        threading.Timer(quand, chat.MESSAGERIE.recevoir, [texte]).start()  # depuis le fil du serveur

    async def scenario():
        envoi = asyncio.ensure_future(poster())
        try:
            await asyncio.wait_for(m.mode_vocal(), timeout=duree)
        except asyncio.TimeoutError:
            pass
        envoi.cancel()

    asyncio.new_event_loop().run_until_complete(scenario())
    print("   cerveau a reçu :", recu)
    print("   Steve a dit    :", dit_par_steve)


jouer([
    (0.2, "Steve, est-ce que tu peux"),     # un début de phrase, puis tu réfléchis
    (1.5, "ouvrir load code"),              # la suite : il a attendu sans rien dire
    (2.4, "raconte-moi une histoire"),       # il se lance dans une longue réponse…
    (3.0, "ouais"),                          # … un « ouais » ne le coupe pas
    (3.4, "en fait mets plutôt du jazz"),    # … mais là tu lui coupes la parole
    (4.6, "merci"),
], 7)
verifier("phrase coupée : il attend la suite sans rien dire (plus de « Oui ? »)", dit_par_steve[1:2], ["C'est fait."])
verifier("phrase coupée : le début et la suite arrivent ensemble (et corrigés)",
         recu[:1], ["est-ce que tu peux ouvrir Claude Code"])
verifier("lui couper la parole : il s'arrête", "Fin de l'histoire." in dit_par_steve, False)
verifier("lui couper la parole : il répond à la nouvelle phrase (le « ouais » seul est ignoré)",
         recu[2:3], ["(interrompu) en fait mets plutôt du jazz"])
verifier("fin de conversation", dit_par_steve[-1:], ["À plus tard."])

print("\n🧑 Tu continues ta phrase alors qu'il a déjà commencé à travailler")
jouer([
    (0.5, "Steve, regarde les logs."),               # il croit ta phrase finie et se met au travail…
    (1.0, "et dis-moi pourquoi il me coupe"),        # … mais tu continuais
    (3.2, "merci"),
], 5)
verifier("suite : il lâche le début et repart avec la phrase entière",
         recu[:2], ["regarde les logs", f"{m.MARQUE_SUITE} regarde les logs et dis-moi pourquoi il me coupe"])
verifier("suite : une seule réponse, pour la phrase entière", dit_par_steve[1:], ["C'est fait.", "À plus tard."])

print("\n🤫 Tant que tu parles, il ne prend pas la parole")
jouer([
    (0.5, "Steve, regarde les logs."),               # sa réponse serait prête à 1,5 s…
    (2.3, "ouais", 1.3),                             # … mais tu parles de 1,3 s à 2,3 s (rien pour lui)
    (4.2, "merci"),
], 6)
verifier("retenue : il a quand même répondu", dit_par_steve[1:2], ["C'est fait."])
verifier("retenue : il a attendu que tu aies fini de parler", dit_quand[1:2] and dit_quand[1] >= 2.3, True)

config.ANNONCE_CALME_S = 0.4       # un blanc de 0,4 s : le moment de dire ce qu'il a à dire
config.ANNONCE_ATTENTE_MAX_S = 3.0
config.SUITE_HESITATION_S = 2.5

print("\n🧵 Deux tâches de fond finissent pendant que vous parlez : un seul compte rendu, et la conversation continue")
jouer([
    (0.3, "Steve, quelle heure il est ?"),
    (2.4, "et mets du jazz"),                        # sans son nom : la conversation continue après le compte rendu
    (3.8, "merci"),
], 5.5, evenements=[(0.8, "fini", "Tâche « mails » finie : 3 mails importants."),
                    (0.8, "fini", "Tâche « budget » finie : tableau prêt.")])
rapports = [r for r in recu if "tâches de fond viennent de finir" in r]
verifier("tâches finies : un seul compte rendu pour les deux", len(rapports), 1)
verifier("tâches finies : avec les deux résultats", rapports and ("mails" in rapports[0] and "budget" in rapports[0]), True)
verifier("tâches finies : d'abord ta question, puis le compte rendu, puis ta suite",
         [r[:20] for r in recu], ["quelle heure il est", "(2 tâches de fond vi", "et mets du jazz"])
verifier("tâches finies : la conversation continue sans redire son nom", dit_par_steve[-1:], ["À plus tard."])

print("\n📣 Une annonce arrive pendant qu'on parle à côté : elle attend un blanc, ta question passe d'abord")
jouer([
    (0.6, "tu as vu le match hier", 0.4),            # des gens qui parlent à côté (pas à lui)
    (0.9, "Steve, quelle heure il est ?", 0.7),      # … puis toi
    (2.6, "décale de dix minutes"),                  # ta réponse à l'annonce
    (4.0, "merci"),
], 5.5, evenements=[(0.3, "annonce", "Ta réunion commence dans dix minutes.")])
quand = {d: q for d, q in zip(dit_par_steve, dit_quand)}
annonces = [d for d in dit_par_steve if "réunion" in d]
verifier("annonce : dite une fois", len(annonces), 1)
verifier("annonce : pas pendant qu'on parlait, après ta réponse",
         bool(annonces) and dit_par_steve.index(annonces[0]) > dit_par_steve.index("C'est fait.")
         and quand[annonces[0]] >= 0.9, True)
verifier("annonce : ta question est passée d'abord", recu[:1], ["quelle heure il est"])
verifier("annonce : il sait à quoi tu réponds",
         recu[1:2], ["(Tu viens de lui annoncer : « Ta réunion commence dans dix minutes. ».) décale de dix minutes"])

print("\n🤔 Tu hésites (« euh ») puis tu te reprends : il attend, et garde la phrase corrigée")
jouer([
    (0.3, "Steve, envoie un mail à Paul, euh"),      # tu réfléchis : il ne coupe pas
    (2.0, "envoie un mail à Pierre pour lui dire que j'arrive"),
    (3.5, "merci"),
], 5)
verifier("hésitation : il a attendu la suite, et pris ta correction",
         recu[:1], ["envoie un mail à Pierre pour lui dire que j'arrive"])
verifier("hésitation : une seule réponse", dit_par_steve[1:], ["C'est fait.", "À plus tard."])

print("\n✋ « Stop » pendant qu'il parle, même quand ta voix est mal reconnue (sa voix couvre la tienne)")
jouer([
    (0.2, "Steve, raconte-moi une histoire"),
    (0.6, "(autre voix) le match était nul"),          # une autre voix qui parle d'autre chose : ignorée
    (0.8, "(autre voix) Stop, stop !"),                # toi, couvert par sa voix : un stop net passe quand même
    (2.6, "merci"),
], 4)
verifier("stop d'une voix mal reconnue : il se tait", "Fin de l'histoire." in dit_par_steve, False)
verifier("stop : rien n'est renvoyé à Claude, et l'autre voix est ignorée", recu, ["raconte-moi une histoire"])

print("\n⌨️  « stop » tapé dans le chat pendant qu'il parle : il se tait tout de suite")
jouer([
    (0.2, "Steve, raconte-moi une histoire"),
    (2.6, "merci"),
], 4, tape=[(1.0, "stop")])
verifier("stop tapé : il se tait sans finir son histoire", "Fin de l'histoire." in dit_par_steve, False)
verifier("stop tapé : il ne le renvoie pas à Claude", recu, ["raconte-moi une histoire"])

print("\n🛠  Tu lui parles par-dessus pendant qu'il travaille : il se tait mais n'abandonne pas le travail")
jouer([
    (0.2, "Steve, publie l'article sur le site."),
    (1.2, "pas de soucis pour ça je vais attendre"),    # un commentaire pendant « Je me connecte. »
    (3.4, "merci"),
], 5)
verifier("par-dessus pendant le travail : le travail va jusqu'au bout", TRAVAUX_FINIS, ["publie l'article sur le site"])
verifier("par-dessus pendant le travail : ta phrase lui est glissée", GLISSES, ["pas de soucis pour ça je vais attendre"])
verifier("par-dessus pendant le travail : pas de « (interrompu) » qui le ferait tout lâcher",
         [r for r in recu if "interrompu" in r], [])
verifier("par-dessus pendant le travail : il ne raconte plus rien après", "Je remplis le formulaire." in dit_par_steve, False)

print("\n🎙  Pendant un vocal WhatsApp, sa propre voix dans les enceintes ne le coupe pas")
from jervis import vocal  # noqa: E402


def vocal_pendant(debut, fin):
    """Le drapeau du vocal (vocal.suspendre), posé et retiré à heure fixe pendant la conversation."""
    threading.Timer(debut, lambda: setattr(vocal, "_jusqua", time.monotonic() + 60)).start()
    threading.Timer(fin, lambda: setattr(vocal, "_jusqua", 0.0)).start()


vocal_pendant(0.9, 2.6)
jouer([
    (0.2, "Steve, raconte-moi une histoire"),
    (1.0, "Stop, stop !", 0.95),                 # sa voix, enregistrée dans le vocal : le micro l'entend
    (1.3, "en fait mets plutôt du jazz"),
    (3.0, "merci"),
], 4.5)
verifier("vocal : « stop » entendu pendant l'enregistrement ne coupe rien", "Fin de l'histoire." in dit_par_steve, True)
verifier("vocal : rien de ce qu'il a entendu n'est renvoyé à Claude", recu, ["raconte-moi une histoire"])
verifier("vocal : après, il t'écoute de nouveau", dit_par_steve[-1:], ["À plus tard."])
FIN.set()

print("\n🔊 La voix : longues phrases dites par bouts, le premier fabriqué seul")
import numpy as np  # noqa: E402

BOUTS = [
    ("Ok.", ["Ok."]),
    ("Il fait 12,5 degrés à Paris, avec un peu de vent.", ["Il fait 12,5 degrés à Paris, avec un peu de vent."]),
    ("Pas mal de choses : gérer ton agenda et tes mails, ouvrir tes applis, chercher sur le web, et discuter.",
     ["Pas mal de choses :", "gérer ton agenda et tes mails,", "ouvrir tes applis,", "chercher sur le web,",
      "et discuter."]),
    ("Oui, c'est bien parti : je lance la musique dans Spotify et je te dis quand c'est prêt.",
     ["Oui, c'est bien parti :", "je lance la musique dans Spotify et je te dis quand c'est prêt."]),
]
for phrase, attendu in BOUTS:
    verifier(f"bouts de « {phrase[:40]}… »", tts._bouts(phrase), attendu)
verifier("phrases puis bouts", tts._phrases("Salut. Pas mal de choses : gérer ton agenda et tes mails, "
                                            "ouvrir tes applis, chercher sur le web."),
         ["Salut.", "Pas mal de choses :", "gérer ton agenda et tes mails,", "ouvrir tes applis,", "chercher sur le web."])

son = np.concatenate([np.zeros(48000), np.full(4800, 0.5, dtype=np.float32), np.zeros(48000)]).astype(np.float32)
verifier("silences autour du bout rognés (≈0,3 s au lieu de 2,1 s)", round(len(tts._rogner(son)) / 48000, 2), 0.28)


async def ordre_de_fabrication():
    debuts = []
    _vrai = tts._preparer

    async def faux_preparer(p, en_tete=False):
        debuts.append((p, round(time.monotonic() - t0, 1)))
        await asyncio.sleep(0.3)
        return None, None
    tts._preparer, tts._en_tete = faux_preparer, None
    t0 = time.monotonic()
    taches = [tts._fabriquer(p) for p in ["un", "deux", "trois"]]
    await asyncio.gather(*taches)
    tts._preparer = _vrai
    return debuts

debuts = asyncio.new_event_loop().run_until_complete(ordre_de_fabrication())
verifier("le premier bout part seul, les autres ensemble juste après", debuts,
         [("un", 0.0), ("deux", 0.3), ("trois", 0.3)])

# --- Pendant qu'il parle : ce qui est un vrai ajout, et ce qui n'en est pas -------------------------------------
# (ce que le micro a entendu, « je te suis » à ignorer ?, un ajout à prendre ?)
PENDANT = [
    ("D'accord.", True, False),
    ("Ah oui, c'est ça.", True, False),
    ("Ok d'accord", True, False),
    ("Oui oui.", True, False),
    ("ouais et envoie aussi à Léa", False, True),      # il commence par « ouais » mais il demande quelque chose
    ("ok merci", False, True),
    ("okapi", False, False),                           # « ok » collé à autre chose n'est pas « ok » ; un seul mot
]
for entendu, suivi, ajout in PENDANT:
    verifier(f"acquiescement : {entendu}", bool(m.ACQUIESCEMENT.match(entendu)), suivi)
    verifier(f"ajout : {entendu}", m.ajout_probable(entendu, "je regarde tes mails"), ajout)

# Ce que le micro a vraiment entendu pendant qu'il parlait (jervis.log) : sa propre voix, mal transcrite.
ECHOS = [
    ("Je peux aussi te prendre le forfait, puis le numéro géorgien en plus 995 avec 48 gigas de data.",
     "Le numéro Georgien en plus neuf cent quatre-vingt-quinze avec"),
    ("C'est fait : 52 gigas supprimés pour Mnfst.", "C'était cinquante-deux gigas supprimés pour M N."),
    ("Pas mal de choses : gérer ton agenda et tes mails, envoyer des messages, ouvrir tes applis.",
     "Je dirais que mon agenda était mail, envoyer des messages."),
]
for dit, entendu in ECHOS:
    verifier(f"écho de sa voix : {entendu[:45]}", m.ajout_probable(entendu, dit), False)
for entendu in ["Og det spikes sjovt base præskel.", "Gamla fed le plussoeau Ernesto.", "I suppose easy on Google.",
                "Remotes the MNEF's creative agency"]:
    verifier(f"charabia : {entendu}", m.ajout_probable(entendu, "Voyons…"), False)
for dit, entendu in [("Je regarde tes mails, tu peux me dire lequel ?", "tu peux me rappeler demain matin"),
                     ("Je regarde tes mails.", "en fait mets plutôt du jazz"),
                     ("C'est fait : 52 gigas supprimés pour Mnfst.", "et vide aussi les médias de WhatsApp"),
                     ("Voyons…", "regarde aussi mes mails stp")]:
    verifier(f"vrai ajout gardé : {entendu}", m.ajout_probable(entendu, dit), True)

# Combien de temps il attend la suite : (ta phrase, ce qu'on attend). SUITE_PHRASE_S = 2, SUITE_SANS_POINT_S = 0,2.
for texte, attendu in [("Steve, est-ce que tu peux ouvrir", 2.0),     # en l'air : il attend longtemps
                       ("Est-ce que tu peux ouvrir.", 2.0),            # en l'air malgré le point (2,5 s au plus)
                       ("No parce que Scott", 0.2),                    # pas de point : tu allais continuer
                       ("Bah il me faut que tu regardes", 2.0),
                       ("Jessica T A", 2.0),                           # épellation en cours : il attend la fin
                       ("Jessica T A double", 2.0),
                       ("Tu peux regarder mes mails ?", 0.0),          # finie : il répond tout de suite
                       ("Quelle heure il est ?", 0.0),
                       ("Mets du jazz.", 0.0),
                       ("merci", 0.0), ("stop", 0.0), ("au revoir Steve", 0.0)]:
    verifier(f"attente de la suite : {texte}", m.attente_suite(texte), attendu)
verifier("attente : le point lu sur la phrase d'origine", m.attente_suite("regarde mes mails", ponctuee=True), 0.0)
verifier("attente : sans point sur la phrase d'origine", m.attente_suite("regarde mes mails", ponctuee=False), 0.2)
for texte in ["envoie un mail à Paul, euh", "ouvre le fichier euh…", "il faut que, comment dire", "et alors..."]:
    verifier(f"attente : tu hésites ({texte})", m.attente_suite(texte), config.SUITE_HESITATION_S)

# Tu hésites : « euh », « en fait », « tu vois »… en fin de phrase, sauf si la phrase est vraiment finie.
for texte, attendu in [("envoie un mail euh", True), ("envoie un mail à Paul, euh", True), ("bah", True),
                       ("ouvre le fichier, hmm.", True),            # un point après « hmm » : il hésitait quand même
                       ("et alors...", True), ("il faut que, comment dire", True), ("mets la musique, du coup", True),
                       ("Je suis crevé, en fait.", False),         # « en fait. » avec un vrai point : c'est fini
                       ("c'est quoi ton genre ?", False), ("mets du jazz", False), ("euh, mets du jazz", False),
                       ("Steve, merci !", False), ("", False)]:
    verifier(f"hésite : {texte!r}", hesite(texte), attendu)

# Une phrase dite en plusieurs morceaux : les mots réentendus et les reprises ne sont pas doublés.
for debut, suite, attendu in [
    ("envoie un mail à Paul, euh", "envoie un mail à Pierre pour lui dire que j'arrive",
     "envoie un mail à Pierre pour lui dire que j'arrive"),                     # tu te reprends : la correction gagne
    ("ouvre le fichier", "fichier budget", "ouvre le fichier budget"),          # un mot réentendu
    ("rappelle-moi d'appeler Léa", "appeler Léa demain à 9 h", "rappelle-moi d'appeler Léa demain à 9 h"),
    ("Steve, est-ce que tu peux", "tu peux ouvrir Cursor.", "Steve, est-ce que tu peux ouvrir Cursor."),
    ("euh", "ouvre Cursor", "ouvre Cursor"),
    ("regarde les logs.", "et dis-moi pourquoi", "regarde les logs et dis-moi pourquoi"),
    ("il faut que tu", "tu regardes", "il faut que tu tu regardes"),           # un mot de deux lettres : pas sûr
    ("mets la musique dans le salon", "mets la musique aussi dans la cuisine",
     "mets la musique dans le salon mets la musique aussi dans la cuisine"),   # trop différent : rien de perdu
    ("ouvre Cursor", "", "ouvre Cursor"),
]:
    verifier(f"recoller : « {debut} » + « {suite} »", recoller(debut, suite), attendu)

for bout, en_l_air in [("et le", True), ("et aussi à", True), ("ouvre Cursor et", True), ("envoie-le", False),
                       ("merci", False), ("stop", False), ("au revoir", False),
                       ("et envoie aussi le mail à", False)]:        # plus de trois mots : une vraie demande coupée
    verifier(f"bout en l'air : {bout}", m.bout_en_l_air(bout), en_l_air)

for texte, anglais in [("Skip the mail.", True), ("Thank you", True), ("Go to bed", True),
                       ("Okay.", False),                             # un seul mot : il veut dire la même chose
                       ("Hey Steve", False),                         # son nom : c'est bien à lui qu'on parle
                       ("What is the weather today", False),         # quatre mots et plus : une vidéo, ignorée ailleurs
                       ("Passe au mail.", False), ("skip ça", False)]:
    verifier(f"semble anglais : {texte}", stt.semble_anglais(texte), anglais)

for texte, attendu in [("bonjour <unk> toi", "bonjour toi"), ("  salut  ", "salut"), ("<unk>", ""),
                       ("<unk> oui", ""), ("<unk> <unk> <unk> salut tout le monde ici", "")]:
    verifier(f"sons inconnus : {texte}", stt.sans_inconnus(texte), attendu)

# L'avis de limite d'abonnement, écrit en anglais par Claude Code : il le dit en français, avec l'heure.
AVIS = [
    ("You've hit your 5-hour limit · resets 9:50pm (Europe/Paris) · out of usage credits",
     "Tu as atteint la limite de ton abonnement, ça se libère à 21 h 50."),
    ("You've hit your 5-hour limit · resets 10:30am (Europe/Paris) · out of usage credits",
     "Tu as atteint la limite de ton abonnement, ça se libère à 10 h 30."),
    ("You’ve hit your weekly limit · resets Nov 27, 9am (Europe/Paris)",
     "Tu as atteint la limite de ton abonnement, ça se libère le 27 à 9 h."),
    ("You've hit your limit · resets 12am", "Tu as atteint la limite de ton abonnement, ça se libère à 0 h."),
    ("You've hit your limit · resets 12pm", "Tu as atteint la limite de ton abonnement, ça se libère à 12 h."),
    ("You've reached your usage limit.", "Tu as atteint la limite de ton abonnement."),
    ("Claude usage limit reached", "Tu as atteint la limite de ton abonnement."),
    ("You're out of extra usage · resets 3pm", "Tu as atteint la limite de ton abonnement, ça se libère à 15 h."),
    # Ce qui n'en est pas un : il le dit tel quel.
    ("Tu as atteint la limite de vitesse.", ""),
    ("Il te reste 20 % de ta limite, ça se libère à 21 h 50.", ""),
    ("Le morceau s'appelle « You've Got a Friend ».", ""),
    ("C'est parti.", ""),
    ("", ""),
]
for phrase, attendu in AVIS:
    verifier(f"avis de limite « {phrase} »", avis_limite(phrase), attendu)

# Un « oui » sensible doit être de ta voix (si sa voix est apprise).
SEUIL = config.SEUIL_VOIX
for nom, voix, connue, actif, attendu in [
    ("ta voix, reconnue", SEUIL + 0.2, True, True, "ok"),
    ("juste au seuil", SEUIL, True, True, "ok"),
    ("une autre voix", SEUIL - 0.2, True, True, "inconnue"),
    ("un « oui » trop court pour être signé", None, True, True, "courte"),
    ("aucune voix apprise : rien ne change", None, False, True, "ok"),
    ("aucune voix apprise, autre score", SEUIL - 0.3, False, True, "ok"),
    ("contrôle coupé (court)", None, True, False, "ok"),
    ("contrôle coupé (autre voix)", SEUIL - 0.3, True, False, "ok"),
]:
    verifier(f"oui sensible : {nom}", m.controle_voix(voix, connue, actif), attendu)

# La phrase qu'il demande pour reconnaître ta voix doit être assez longue (0,8 s de parole minimum : « oui, vas-y »
# n'y suffisait pas, il le redemandait en boucle) et elle doit elle-même valoir un « oui ».
for i, demande in enumerate(m.DEMANDES_PHRASE):
    dite = re.search(r"« (.+?) »", demande).group(1)
    verifier(f"demande de phrase {i + 1} : au moins 5 mots", len(dite.split()) >= 5, True)
    verifier(f"demande de phrase {i + 1} : vaut un oui", m.lire_accord(dite), True)
verifier("chaque demande est plus longue que la précédente",
         len(m.DEMANDES_PHRASE[1]) > len(m.DEMANDES_PHRASE[0]), True)
verifier("ancienne boucle : « oui, vas-y » ne suffit plus comme phrase demandée",
         all("« oui, vas-y »" not in d for d in m.DEMANDES_PHRASE), True)

print(f"\n{'✅' if not echecs else '❌'} naturel : {total - len(echecs)}/{total} vérifications OK "
      f"({len(REPONSES) + len(TRANSCRIPTIONS) + len(PHRASES) + len(CERVEAU)} phrases d'exemple)", flush=True)
os._exit(0 if not echecs else 1)
