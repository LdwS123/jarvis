"""Quand tu branches un casque ou tes AirPods (jervis/appareils.py, et la surveillance du flux dans audio.py) :
il doit continuer à t'écouter, sur le bon micro, et te répondre dans le casque. Sans micro ni son : sounddevice et
CoreAudio sont remplacés par des faux, sauf une vérification qui interroge vraiment macOS (sans rien ouvrir).

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_casque.py
"""

import io
import os
import threading
import time
from contextlib import redirect_stdout
from types import SimpleNamespace

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import appareils, audio, config, voix  # noqa: E402
from jervis.appareils import Appareil  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


# --- Quel micro (appareils.choisir_micro) -----------------------------------------------------------------

MAC = Appareil(71, "Micro MacBook Air", "bltn", True)
HP_MAC = Appareil(76, "Haut-parleurs MacBook Air", "bltn", False)
CASQUE_FIL = Appareil(90, "Casque externe", "bltn", False)
AIRPODS = Appareil(95, "AirPods de Toi", "blue", True)
AIRPODS_LE = Appareil(96, "Beats Fit Pro", "blea", True)
IPHONE = Appareil(83, "Micro de « iPhone de Toi »", "ccwd", True)
YETI = Appareil(99, "Yeti Stereo Microphone", "usb ", True)
TOUS = [IPHONE, MAC, HP_MAC, AIRPODS, YETI]

verifier("auto, micro du Mac : celui de macOS", appareils.choisir_micro("auto", MAC, TOUS), None)
verifier("auto, AirPods : le micro du Mac (les AirPods gardent leur bon son)",
         appareils.choisir_micro("auto", AIRPODS, TOUS), "Micro MacBook Air")
verifier("auto, Bluetooth basse consommation : pareil", appareils.choisir_micro("auto", AIRPODS_LE, TOUS),
         "Micro MacBook Air")
verifier("auto, micro de l'iPhone (peut-être dans ta poche) : celui du Mac",
         appareils.choisir_micro("auto", IPHONE, TOUS), "Micro MacBook Air")
verifier("auto, un micro USB choisi dans macOS : on le garde", appareils.choisir_micro("auto", YETI, TOUS), None)
verifier("auto, AirPods sur un Mac sans micro : ceux des AirPods",
         appareils.choisir_micro("auto", AIRPODS, [AIRPODS, YETI]), None)
verifier("auto, macOS ne répond pas : celui de macOS", appareils.choisir_micro("auto", None, []), None)
verifier("systeme : toujours celui de macOS, même les AirPods", appareils.choisir_micro("systeme", AIRPODS, TOUS), None)
verifier("Systeme (majuscule) : pareil", appareils.choisir_micro(" Systeme ", AIRPODS, TOUS), None)
verifier("un bout du nom : ce micro-là", appareils.choisir_micro("yeti", MAC, TOUS), "Yeti Stereo Microphone")
verifier("un bout du nom, débranché : celui de macOS", appareils.choisir_micro("Yeti", MAC, [MAC, HP_MAC]), None)
verifier("un bout du nom qui ne désigne qu'une sortie : pas un micro",
         appareils.choisir_micro("Haut-parleurs", MAC, TOUS), None)
verifier("réglage vide : comme auto", appareils.choisir_micro("", AIRPODS, TOUS), "Micro MacBook Air")

# --- CoreAudio (appareils.par_defaut, tous) -----------------------------------------------------------------

verifier("les codes de CoreAudio", (appareils._code("dIn "), appareils._texte(appareils._code("blue"))),
         (0x64496E20, "blue"))
vraie = appareils._bibliotheques


def sans_coreaudio():
    raise OSError("pas de CoreAudio ici")


appareils._bibliotheques = sans_coreaudio
verifier("sans CoreAudio : ni micro ni sortie connus", appareils.par_defaut(), (None, None))
verifier("sans CoreAudio : aucun appareil", appareils.tous(), [])
appareils._bibliotheques = vraie
# La vraie question à macOS (rien n'est ouvert) : ne doit jamais planter, même sans appareil ou dans un bac à sable.
micro_vrai, sortie_vraie = appareils.par_defaut()
verifier("le vrai macOS : un Appareil ou None",
         all(a is None or (isinstance(a, Appareil) and isinstance(a.nom, str)) for a in (micro_vrai, sortie_vraie)), True)
verifier("le vrai macOS : une liste d'Appareils", all(isinstance(a, Appareil) for a in appareils.tous()), True)


# --- Le flux qui suit les appareils (audio.Oreilles) ---------------------------------------------------------

class FauxFlux:
    def __init__(self, sorte: str, reglages: dict, latence) -> None:
        self.sorte, self.reglages, self.latency = sorte, reglages, latence
        self.active = False
        self.ferme = False

    def start(self): self.active = True
    def stop(self, ignore_errors=True): self.active = False
    def abort(self, ignore_errors=True): self.active = False
    def close(self, ignore_errors=True): self.ferme = True


class FauxSd:
    """sounddevice : la liste des appareils de PortAudio (relue seulement à _initialize), des flux qu'on peut casser."""

    def __init__(self) -> None:
        self.monde = [{"name": a.nom, "max_input_channels": int(a.entree), "max_output_channels": int(not a.entree) * 2}
                      for a in [MAC, HP_MAC]]
        self.liste = list(self.monde)
        self.default = SimpleNamespace(device=[0, 1])
        self.flux: list[FauxFlux] = []
        self.relances = 0
        self.panne_duplex = self.panne_micro = False
        self.latence = (0.010, 0.020)

    def brancher(self, *autres: Appareil, defaut=(0, 1)) -> None:
        self.monde = [{"name": a.nom, "max_input_channels": int(a.entree),
                       "max_output_channels": 2 if not a.entree or a.transport == "blue" else 0}
                      for a in [MAC, HP_MAC, *autres]]
        self._defaut_voulu = list(defaut)

    def query_devices(self, i=None, kind=None):
        if kind == "output":
            return self.liste[self.default.device[1]]
        return self.liste if i is None else self.liste[i]

    def Stream(self, **reglages):
        if self.panne_duplex:
            raise RuntimeError("Error opening Stream: Invalid sample rate [PaErrorCode -9997]")
        self.flux.append(FauxFlux("duplex", reglages, self.latence))
        return self.flux[-1]

    def InputStream(self, **reglages):
        if self.panne_micro:
            raise RuntimeError("Error querying device -1")
        self.flux.append(FauxFlux("micro", reglages, self.latence[0]))
        return self.flux[-1]

    def _terminate(self) -> None:
        self.relances += 1

    def _initialize(self) -> None:  # PortAudio relit la liste : c'est seulement là qu'il voit le casque
        self.liste = list(self.monde)
        self.default.device = getattr(self, "_defaut_voulu", self.default.device)


sd = FauxSd()
audio.sd = sd
macos = {"micro": MAC, "sortie": HP_MAC, "tous": [MAC, HP_MAC]}
appareils.par_defaut = lambda: (macos["micro"], macos["sortie"])
appareils.tous = lambda: macos["tous"]
voix.AntiEcho = lambda delai: SimpleNamespace(delai=delai, traiter=lambda mic, ref: mic)
config.ANTI_ECHO, config.MICRO, config.SURVEILLANCE_SON_S = True, "auto", 0.02
audio.Oreilles.STABILISATION_S = 0.0


def nouvelles_oreilles() -> audio.Oreilles:
    """Oreilles sans les modèles de voix : seulement ce qui ouvre, surveille et ferme le flux (comme __init__)."""
    o = object.__new__(audio.Oreilles)
    o._ferme, o.en_pause = threading.Event(), False
    o.enceinte = audio.HautParleur(o._enceinte_active)
    o.haut_parleur, o._anti_echo, o._stream = None, None, None
    o._verrou_flux = threading.RLock()
    o._brut = audio.queue.Queue()
    o._mic, o._ref = audio._Decimateur(), audio._Decimateur()
    o._dernier_rappel = time.monotonic()
    o._branches = (None, None, None)
    o._ajouter = lambda bloc: None
    o._ouvrir()
    return o


def muet(f):
    with redirect_stdout(io.StringIO()) as sortie:
        resultat = f()
    return resultat, sortie.getvalue()


o, _ = muet(nouvelles_oreilles)
premier = o._stream
verifier("au départ : un flux duplex sur les appareils de macOS",
         (premier.sorte, premier.reglages["device"], premier.active), ("duplex", (None, None), True))
verifier("au départ : sa voix passe par le flux (anti-écho)", (o.haut_parleur is o.enceinte, o.enceinte.actif),
         (True, True))
verifier("au départ : l'anti-écho attend les latences du Mac", o._anti_echo.delai, 30)
verifier("rien n'a changé : on ne rouvre pas", o._a_rouvrir(), None)

# Tes AirPods se connectent : macOS les prend comme micro et comme sortie.
fini = []
o.enceinte.ajouter(audio.np.zeros(48_000, audio.np.float32), lambda: fini.append(True))
sd.brancher(AIRPODS, defaut=(0, 2))
sd.latence = (0.060, 0.140)
macos.update(micro=AIRPODS, sortie=AIRPODS, tous=[MAC, HP_MAC, AIRPODS])
verifier("AirPods connectés : il le voit", o._a_rouvrir(), "nouveaux appareils son")
_, dit = muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("PortAudio est relancé (sinon il ne voit pas les AirPods)", sd.relances, 1)
verifier("l'ancien flux est coupé et fermé", (premier.active, premier.ferme), (False, True))
verifier("la phrase qu'il disait est libérée (sinon il attendrait sans fin)", fini, [True])
verifier("AirPods, réglage auto : il t'écoute par le micro du Mac", o._stream.reglages["device"], (0, None))
verifier("… et te répond dans les AirPods (la sortie de macOS)", o.haut_parleur is o.enceinte and o.enceinte.actif, True)
verifier("l'anti-écho reprend la latence des AirPods", o._anti_echo.delai, 200)
verifier("il te dit où il t'écoute", "🎧 Nouveaux appareils son : je t'écoute par « Micro MacBook Air », "
         "je réponds par « AirPods de Toi »." in dit, True)
verifier("une fois rouvert : tout va bien", o._a_rouvrir(), None)

config.MICRO = "systeme"
verifier("MICRO=systeme : le choix change, il rouvre", o._a_rouvrir(), "nouveaux appareils son")
muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("MICRO=systeme : le micro des AirPods (celui de macOS)", o._stream.reglages["device"], (None, None))
config.MICRO = "auto"
muet(lambda: o._rouvrir("nouveaux appareils son"))

# Tu les retires, puis tu branches un casque filaire : seule la sortie change.
sd.brancher(CASQUE_FIL, defaut=(0, 2))
sd.latence = (0.010, 0.020)
macos.update(micro=MAC, sortie=CASQUE_FIL, tous=[MAC, HP_MAC, CASQUE_FIL])
verifier("casque filaire : il le voit aussi", o._a_rouvrir(), "nouveaux appareils son")
_, dit = muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("casque filaire : micro de macOS, réponse dans le casque",
         (o._stream.reglages["device"], "je réponds par « Casque externe »" in dit), ((None, None), True))

# Un micro USB que tu as nommé dans le réglage.
config.MICRO = "Yeti"
sd.brancher(YETI, defaut=(0, 1))
macos.update(micro=MAC, sortie=HP_MAC, tous=[MAC, HP_MAC, YETI])
muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("MICRO=Yeti : ce micro-là, par son numéro PortAudio", o._stream.reglages["device"], (2, None))
config.MICRO = "auto"
muet(lambda: o._rouvrir("nouveaux appareils son"))

# Le flux tombe (appareil arraché), ou ne donne plus rien.
o._stream.active = False
verifier("flux arrêté : il le voit", o._a_rouvrir(), "le micro s'était arrêté")
muet(lambda: o._rouvrir("le micro s'était arrêté"))
o._dernier_rappel = time.monotonic() - 10
verifier("plus aucun bloc depuis 10 s : il le voit", o._a_rouvrir(), "le micro ne donnait plus rien")
muet(lambda: o._rouvrir("le micro ne donnait plus rien"))
verifier("rouvert, ça repart", o._a_rouvrir(), None)

# Le flux duplex refuse (micro Bluetooth en 16 kHz…) : micro seul, il parle par afplay.
sd.panne_duplex = True
_, dit = muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("duplex impossible : micro seul", (o._stream.sorte, o._stream.reglages["device"]), ("micro", None))
verifier("micro seul : pas d'anti-écho, sa voix par afplay", (o._anti_echo, o.haut_parleur, o.enceinte.actif),
         (None, None, False))
verifier("micro seul : il le dit", "micro seul, sans anti-écho" in dit and "je réponds par « afplay »" in dit, True)
verifier("micro seul : il écoute toujours", o._a_rouvrir(), None)
sd.panne_duplex = False
muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("le duplex revient avec l'appareil suivant", (o._stream.sorte, o.enceinte.actif), ("duplex", True))

# En pause : il ne surveille rien ; au réveil, il rouvre si un casque est arrivé entre-temps.
o.pause(True)
verifier("pause : le micro est vraiment coupé", (o._stream.active, o.enceinte.actif), (False, False))
avant = o._stream
muet(lambda: o._rouvrir("nouveaux appareils son"))
verifier("pause : la surveillance ne rouvre rien", o._stream is avant, True)
o.pause(False)
verifier("fin de pause, mêmes appareils : le même flux repart", (o._stream is avant, o._stream.active), (True, True))
o.pause(True)
sd.brancher(AIRPODS, defaut=(0, 2))
macos.update(micro=AIRPODS, sortie=AIRPODS, tous=[MAC, HP_MAC, AIRPODS])
muet(lambda: o.pause(False))
verifier("fin de pause, AirPods arrivés : un flux neuf sur le bon micro",
         (o._stream is avant, o._stream.active, o._stream.reglages["device"]), (False, True, (0, None)))

# Plus aucun micro : la surveillance réessaie (un seul message), puis repart quand il revient.
o2, _ = muet(nouvelles_oreilles)
sd.panne_duplex = sd.panne_micro = True
o2._stream.active = False
journal = io.StringIO()
with redirect_stdout(journal):
    fil = threading.Thread(target=o2._surveiller)
    fil.start()
    time.sleep(0.2)
    verifier("sans micro : pas de flux", o2._stream, None)
    sd.panne_duplex = sd.panne_micro = False
    time.sleep(0.2)
    o2.fermer()
    fil.join(2)
dit = journal.getvalue()
verifier("sans micro : un seul message, pas un par essai", dit.count("Plus de micro"), 1)
verifier("le micro revient : il le dit et réécoute", ("🎧 Le micro s'était arrêté" in dit, o2._stream), (True, None))
verifier("fermer arrête la surveillance", fil.is_alive(), False)
dernier = o._stream
o.fermer()
verifier("fermer : le flux est fermé", (dernier.ferme, dernier.active, o._stream), (True, False, None))

print(f"\n{'✅' if not echecs else '❌'} casque : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
