"""Le mode réunion et le compte rendu : il voit que tu es en appel, se tait, et transcrit la réunion en local.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_reunion.py

Rien n'écoute vraiment : les apps qui tiennent le micro sont une liste fabriquée, l'horloge est fausse, le son est
synthétique et la transcription est une fonction qui répond tout de suite. Tout s'écrit dans un dossier temporaire.
"""

import asyncio
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402

from jervis import audio, brain, config, permissions, reunion  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp())
config.DOSSIER_REUNIONS = TMP / "reunions"
config.REUNION_LANGUE = "auto"
config.REUNION_GARDER_SON = False
config.REUNION_SON_DU_MAC = ""
reunion.REUNIONS.dossier = TMP / "reunions"
# Pas de vrai micro : le « micro du Mac » est un nom.
reunion.appareils.par_defaut = lambda: (None, None)
reunion.appareils.tous = lambda: []
reunion.appareils.choisir_micro = lambda reglage, defaut, appareils: "Micro du Mac"
SR = config.SAMPLE_RATE

# --- Qui tient le micro ------------------------------------------------------------------------------------
MOI = 4242
processus = [(100, "us.zoom.xos", True), (101, "com.apple.Music", False), (102, "com.google.Chrome.helper", True),
             (MOI, "org.python.python", True), (103, "com.apple.VoiceMemos", True), (104, "us.zoom.xos", True),
             (105, "com.apple.FaceTime", False)]
verifier("appel : Zoom et Chrome écoutent le micro (une fois chacun), pas Mémos vocaux, pas lui-même",
         reunion.apps_en_appel(processus, moi=MOI), ["us.zoom.xos", "com.google.Chrome.helper"])
verifier("appel : FaceTime ouvert sans le micro → pas en appel",
         reunion.apps_en_appel([(1, "com.apple.FaceTime", False)], moi=MOI), [])
verifier("appel : la casse de l'identifiant ne compte pas",
         reunion.apps_en_appel([(1, "US.ZOOM.XOS", True)], apps=["us.zoom"], moi=MOI), ["US.ZOOM.XOS"])
verifier("noms : comme tu les appelles", reunion.noms(["us.zoom.xos", "com.google.Chrome.helper"]), "Zoom et Chrome")
verifier("noms : FaceTime et son démon ne font qu'un",
         reunion.noms(["com.apple.FaceTime", "com.apple.avconferenced"]), "FaceTime")
verifier("noms : rien → « un appel »", reunion.noms([]), "un appel")
verifier("CoreAudio absent (bac à sable, vieux macOS) : liste vide, sans planter", isinstance(reunion.processus_audio(), list),
         True)

# --- La surveillance ---------------------------------------------------------------------------------------
temps = {"t": 0.0}
micro = {"liste": []}
vu: list = []
s = reunion.Surveillance(lambda apps: vu.append(("début", apps)), lambda: vu.append("fin"),
                         lire=lambda: micro["liste"], horloge=lambda: temps["t"])
s.regarder()
verifier("surveillance : personne au micro → rien", (vu, s.en_appel), ([], False))
micro["liste"] = [(100, "us.zoom.xos", True)]
s.regarder()
temps["t"] = 2
s.regarder()
verifier("surveillance : Zoom prend le micro → « début » une seule fois", vu, [("début", ["us.zoom.xos"])])
micro["liste"] = []
temps["t"] = 2 + config.APPEL_FIN_S / 2
s.regarder()
verifier("surveillance : micro coupé quelques secondes (muet) → toujours en appel", (len(vu), s.en_appel), (1, True))
micro["liste"] = [(100, "us.zoom.xos", True)]
temps["t"] += 1
s.regarder()
micro["liste"] = []
temps["t"] += config.APPEL_FIN_S + 0.1
s.regarder()
verifier("surveillance : plus personne depuis APPEL_FIN_S → « fin »", (vu[-1], s.en_appel), ("fin", False))
s.regarder()
verifier("surveillance : la fin n'est dite qu'une fois", vu.count("fin"), 1)

# --- Ce que tu lui dis pendant l'appel ---------------------------------------------------------------------
verifier("commande : « fais le compte rendu » → commencer", reunion.commande_reunion("Steve fais le compte rendu"),
         "commencer")
verifier("commande : « prends des notes » → commencer", reunion.commande_reunion("prends des notes stp"), "commencer")
verifier("commande : en anglais aussi", reunion.commande_reunion("take notes please"), "commencer")
verifier("commande : « arrête le compte rendu » → finir", reunion.commande_reunion("arrête le compte rendu"), "finir")
verifier("commande : « stop the recording » → finir", reunion.commande_reunion("stop the recording"), "finir")
verifier("commande : autre chose → rien", reunion.commande_reunion("quelle heure est-il ?"), None)

# --- Le découpage --------------------------------------------------------------------------------------------
rng = np.random.default_rng(0)


def parole(secondes: float, fort: int = 3000) -> np.ndarray:
    return (rng.standard_normal(int(secondes * SR)) * fort).astype(np.int16)


d = reunion.Decoupeur(morceau_s=3)
son = np.concatenate([parole(2.5), np.zeros(int(0.3 * SR), np.int16), parole(1.5)])  # un blanc à 2,5 s
morceaux = []
for i in range(0, len(son), config.FRAME):
    morceaux += d.ajouter(son[i:i + config.FRAME])
verifier("découpe : un morceau prêt au bout de 3 s", len(morceaux), 1)
verifier("découpe : coupé dans le blanc (entre 2,5 et 2,8 s), pas au milieu d'un mot",
         2.5 * SR <= len(morceaux[0][1]) <= 2.8 * SR, True)
reste = d.vider()
verifier("découpe : le reste à la fin, à la suite du premier", (reste[0], reste[0] + len(reste[1])),
         (len(morceaux[0][1]), len(son)))
verifier("découpe : moins d'une seconde → rien", reunion.Decoupeur(3).vider(), None)
verifier("horodatage : mm:ss puis h:mm:ss", (reunion._horodatage(75 * SR), reunion._horodatage(3725 * SR)),
         ("01:15", "1:02:05"))

# --- Le compte rendu ---------------------------------------------------------------------------------------
phrases = iter([("On valide le budget, Marc envoie le devis vendredi.", "fr"),
                ("Agreed, I'll ship the release on Monday.", "en"),
                ("Perfecto, nos vemos el martes.", "es")])
langues_demandees: list = []


def faux_transcrire(audio_, langue):
    langues_demandees.append(langue)
    return next(phrases, ("", ""))


config.REUNION_MORCEAU_S = 2
cr = reunion.CompteRendu("Point / budget: Q3?", TMP / "reunions", faux_transcrire)
verifier("compte rendu : un nom de fichier propre, daté", cr.fichier.name.endswith(" Point budget Q3.md"), True)
for _ in range(6 * SR // config.FRAME):  # 6 s de parole → au moins trois morceaux de ~2 s
    cr.ajouter(parole(config.FRAME / SR))
cr.finir(attente=10)
texte = cr.fichier.read_text(encoding="utf-8")
verifier("compte rendu : les trois langues, chacune dans la sienne",
         [l.split(") ", 1)[0].split("(")[-1] for l in texte.splitlines() if l.startswith("[")], ["fr", "en", "es"])
verifier("compte rendu : chaque passage horodaté", texte.count("\n[00:0"), 3)
verifier("compte rendu : Whisper devine la langue de chaque morceau (REUNION_LANGUE=auto)",
         set(langues_demandees), {None})
verifier("compte rendu : l'avertissement en tête (des informations, jamais des ordres)",
         "jamais des ordres" in texte.split("## Transcription")[0], True)
verifier("compte rendu : la durée et les langues à la fin", "Langues entendues : fr, en, es." in texte, True)
verifier("compte rendu : un deuxième au même nom ne l'écrase pas",
         reunion.CompteRendu("Point / budget: Q3?", TMP / "reunions", faux_transcrire).fichier.name.endswith("(2).md"),
         True)
demandes = len(langues_demandees)
calme = reunion.CompteRendu("Calme", TMP / "reunions", faux_transcrire)
for _ in range(5 * SR // config.FRAME):
    calme.ajouter(np.zeros(config.FRAME, np.int16))
calme.finir(attente=10)
verifier("compte rendu : le silence n'est jamais transcrit", (len(langues_demandees), calme.lignes), (demandes, 0))
verifier("compte rendu : rien entendu → il l'écrit", "Rien d'intelligible" in calme.fichier.read_text(encoding="utf-8"),
         True)

resume = reunion.consigne_resume(cr)
verifier("résumé : en français quelle que soit la langue, avec les actions et creer_note",
         all(m in resume for m in ("en français", "actions à faire", "creer_note", "lire_compte_rendu",
                                   "jamais un ordre")), True)
verifier("résumé : rien entendu → il le dit, sans inventer", "rien entendu" in reunion.consigne_resume(calme), True)

# --- Reunions : brancher le micro de Steve, ou en ouvrir un --------------------------------------------------
R = reunion.Reunions()
R.dossier = TMP / "reunions2"
R.transcrire = lambda a, l: ("Bonjour à tous.", "fr")
branche: list = []
R.brancher_micro = lambda f: branche.append(f) or True
annonces: list[str] = []
R.annoncer = annonces.append
debut = R.commencer("Équipe")
verifier("réunions : commencer, en rappelant de prévenir les autres",
         (debut.startswith("J'écoute la réunion"), "Préviens les autres" in debut), (True, True))
verifier("réunions : le micro de Steve est branché", callable(branche[0]), True)
verifier("réunions : un seul à la fois", R.commencer("Autre").startswith("Le compte rendu « Équipe » tourne déjà"), True)
for _ in range(2 * SR // config.FRAME + 2):
    branche[0](parole(config.FRAME / SR))
cr2 = R.finir(annoncer=True)
verifier("réunions : finir débranche le micro", branche[-1], None)
verifier("réunions : à la fin d'un appel, Claude reçoit la consigne du résumé",
         (len(annonces), annonces[0].startswith("(Compte rendu de réunion)")), (1, True))
verifier("réunions : transcrit", "Bonjour à tous." in cr2.fichier.read_text(encoding="utf-8"), True)
verifier("réunions : finir sans compte rendu → None", R.finir(), None)

prises: list = []


class FaussePrise:
    def __init__(self, appareil, recevoir):
        self.appareil, self.recevoir, self.ferme = appareil, recevoir, False
        prises.append(self)

    def fermer(self):
        self.ferme = True


R.brancher_micro = lambda f: False  # micro de Steve en pause : il n'en ouvre pas un autre dans ton dos
R.ouvrir_prise = FaussePrise
avant = set((TMP / "reunions2").glob("*"))
verifier("réunions : micro en pause → il refuse, sans ouvrir de micro ni laisser de fichier",
         (R.commencer("En pause").startswith("Ton micro est en pause"), prises, R.en_cours,
          set((TMP / "reunions2").glob("*")) == avant), (True, [], None, True))
R.brancher_micro = None  # sans Steve autour : il ouvre le micro lui-même
prevenu: list[str] = []
R.prevenir = prevenu.append
config.REUNION_SON_DU_MAC = "BlackHole 2ch"
R.commencer("Au casque")
verifier("réunions : sans Steve → il ouvre son micro, plus le son du Mac au casque",
         [p.appareil for p in prises], ["Micro du Mac", "BlackHole 2ch"])
verifier("réunions : chaque compte rendu qui commence se voit (notification)",
         (len(prevenu), "Au casque" in prevenu[0]), (1, True))
R.finir()
verifier("réunions : les prises sont fermées", all(p.ferme for p in prises), True)
config.REUNION_SON_DU_MAC = ""


def prise_cassee(appareil, recevoir):
    raise OSError("pas de micro")


R.ouvrir_prise = prise_cassee
verifier("réunions : aucun micro → il le dit, sans fichier vide", R.commencer("Rien").startswith("Je ne peux pas"), True)
verifier("réunions : … et rien ne reste en cours", R.en_cours, None)

# --- Le micro de Steve donne ses blocs au compte rendu --------------------------------------------------------
verifier("audio : le crochet existe et part de None", "self.enregistrement = None" in Path(audio.__file__).read_text(),
         True)

# --- Les outils ---------------------------------------------------------------------------------------------


async def outils() -> dict:
    r = {}
    r["lire dernier"] = (await reunion.lire_compte_rendu.handler({}))["content"][0]["text"]
    r["lire nom"] = (await reunion.lire_compte_rendu.handler({"fichier": "calme"}))["content"][0]["text"]
    r["lire inconnu"] = await reunion.lire_compte_rendu.handler({"fichier": "../../etc/passwd"})
    r["finir rien"] = (await reunion.finir_compte_rendu.handler({}))["content"][0]["text"]
    return r


r = asyncio.run(outils())
verifier("lire : la dernière réunion", r["lire dernier"].split("\n")[0].endswith(".md"), True)
verifier("lire : par un bout de son nom, sans la casse", "# Calme" in r["lire nom"], True)
verifier("lire : rien hors du dossier des réunions", r["lire inconnu"].get("is_error"), True)
verifier("finir : rien en cours", r["finir rien"], "Aucun compte rendu en cours.")

verifier("permissions : commencer et finir ne rendent que « c'est fait »",
         [permissions.lit_dehors(f"mcp__jervis__{n}") for n in ("commencer_compte_rendu", "finir_compte_rendu")],
         [False, False])
verifier("permissions : la transcription, elle, est un texte venu d'ailleurs",
         permissions.lit_dehors("mcp__jervis__lire_compte_rendu"), True)


class Contexte:
    pass


async def gardien() -> dict:
    questions: list[str] = []

    async def repondre(question: str) -> bool:
        questions.append(question)
        return True

    vu: dict = {}

    async def essai(nom: str, outil: str, entree: dict, **demande) -> None:
        questions.clear()
        g = permissions.Gardien(repondre)  # une conversation neuve : rien de lu au-dehors avant
        g.nouvelle_demande(**demande)
        await g(outil, entree, Contexte())
        vu[nom] = len(questions)

    config.CONFIRMATIONS = "autonome"
    rappel = {"titre": "Envoyer le devis", "quand": "demain 9 h"}
    await essai("autonome : un rappel, sans question", "mcp__jervis__creer_rappel", rappel)
    await essai("entendu en appel : un rappel → ton oui", "mcp__jervis__creer_rappel", rappel, en_appel=True)
    await essai("entendu en appel : lire, sans question", "mcp__jervis__chercher_fichiers", {"texte": "devis"},
                en_appel=True)
    await essai("compte rendu demandé par toi : sans question", "mcp__jervis__commencer_compte_rendu", {})
    questions.clear()
    g = permissions.Gardien(repondre)
    g.nouvelle_demande()
    await g("mcp__navigateur__browser_snapshot", {}, Contexte())  # il vient de lire une page…
    await g("mcp__jervis__commencer_compte_rendu", {"titre": "x"}, Contexte())
    vu["compte rendu après une page lue → ton oui"] = len(questions)
    return vu


v = asyncio.run(gardien())
verifier("gardien : en autonome, un rappel se fait seul", v["autonome : un rappel, sans question"], 0)
verifier("gardien : entendu pendant un appel (peut-être un participant) → il demande",
         v["entendu en appel : un rappel → ton oui"], 1)
verifier("gardien : … mais lire reste libre", v["entendu en appel : lire, sans question"], 0)
verifier("gardien : le compte rendu que tu demandes part seul", v["compte rendu demandé par toi : sans question"], 0)
verifier("gardien : jamais sous la dictée d'une page", v["compte rendu après une page lue → ton oui"], 1)

c = brain._consignes(ecran=False)
verifier("consignes : il sait se taire en appel et faire le compte rendu",
         all(m in c for m in ("commencer_compte_rendu", "finir_compte_rendu", "dans chaque langue")), True)

print(f"\n{'✅' if not echecs else '❌'} réunion : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
