"""La touche fn : tapée seule, elle le réveille ; enfoncée pendant ta phrase, elle vaut le mot-clé ; avec une autre
touche (fn+F1, fn+clic, fn+cmd), c'est un raccourci et elle ne compte pas.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_touche.py

Rien n'écoute le vrai clavier : les événements sont joués à la main, avec une fausse horloge, et le micro est un
faux qui rend des blocs de « voix » et de silence.
"""

import os
import queue
import threading
import time

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402

from jervis import audio, config, outils, touche  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


class Horloge:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


FN_BAS, FN_HAUT = (touche.MODIFICATEURS, 63, touche.DRAPEAU_FN), (touche.MODIFICATEURS, 63, 0)
CMD = 0x100000


def jouer(*evenements) -> tuple[touche.Fn, Horloge]:
    h = Horloge()
    fn = touche.Fn(horloge=h)
    for ev in evenements:
        if isinstance(ev, (int, float)):
            h.t += ev
        else:
            fn.evenement(*ev)
    return fn, h


# --- La touche, seule ou avec une autre ---------------------------------------------------------------------------
print("⌨️  La touche fn")
fn, _ = jouer(FN_BAS, 0.2, FN_HAUT)
verifier("fn tapée seule : il se réveille", fn.tapee.is_set(), True)
verifier("prendre : vu une fois", (fn.prendre(), fn.prendre()), (True, False))
fn, _ = jouer(FN_BAS, 0.1)
verifier("fn qui descend : pas encore (ce pourrait être fn+F1)", fn.tapee.is_set(), False)
for nom, evenements in [
    ("fn+F1 (une touche pendant fn)", (FN_BAS, (touche.TOUCHE,), FN_HAUT)),
    ("fn+clic", (FN_BAS, (1,), FN_HAUT)),
    ("fn+clic droit", (FN_BAS, (3,), FN_HAUT)),
    ("fn+cmd (cmd enfoncé après)", (FN_BAS, (touche.MODIFICATEURS, 55, touche.DRAPEAU_FN | CMD), FN_HAUT)),
    ("cmd+fn (cmd déjà enfoncé)", ((touche.MODIFICATEURS, 63, touche.DRAPEAU_FN | CMD), FN_HAUT)),
    ("fn restée enfoncée 40 s (coincée)", (FN_BAS, 40.0, FN_HAUT)),
    ("fn qui remonte sans être descendue", (FN_HAUT,)),
]:
    fn, _ = jouer(*evenements)
    verifier(f"un raccourci, pas un appel : {nom}", fn.tapee.is_set(), False)
fn, _ = jouer((touche.TOUCHE,), FN_BAS, FN_HAUT)
verifier("une touche avant fn ne gêne pas", fn.tapee.is_set(), True)
fn, _ = jouer((touche.MODIFICATEURS, 179, touche.DRAPEAU_FN), (touche.MODIFICATEURS, 179, 0))
verifier("la touche 🌐 (code 179) compte aussi", fn.tapee.is_set(), True)
fn, _ = jouer(FN_BAS, FN_BAS, 0.1, FN_HAUT)
verifier("deux « fn descend » de suite : un seul appui", fn.tapee.is_set(), True)
fn, _ = jouer(FN_BAS, (touche.TOUCHE,), FN_HAUT, FN_BAS, FN_HAUT)
verifier("après un raccourci, le fn suivant compte", fn.tapee.is_set(), True)
fn, _ = jouer(FN_BAS, 0.1, FN_HAUT)
verifier("un genre inconnu ne change rien", (fn.evenement(0xFFFFFFFE), fn.tapee.is_set())[1], True)

# --- Enfoncée pendant ta phrase ----------------------------------------------------------------------------------
print("\n🎙  fn enfoncée pendant ta phrase")
fn, h = jouer(FN_BAS, 2.0)
verifier("encore enfoncée : ta phrase compte", fn.tenue_depuis(h.t - 1), True)
fn.phrase_prise()
fn.evenement(*FN_HAUT)
verifier("sa remontée après coup ne relance pas d'écoute", fn.tapee.is_set(), False)
fn.evenement(*FN_BAS)
fn.evenement(*FN_HAUT)
verifier("le fn suivant, lui, compte", fn.tapee.is_set(), True)
fn, h = jouer(FN_BAS, 0.3, FN_HAUT, 1.0)
verifier("remontée pendant ta phrase (commencée avant)", fn.tenue_depuis(h.t - 2), True)
verifier("remontée avant ta phrase : ce n'était pas pour elle", fn.tenue_depuis(h.t - 0.5), False)
fn, h = jouer(FN_BAS, (touche.TOUCHE,), 1.0)
verifier("fn+une touche, encore enfoncée : pas pour ta phrase", fn.tenue_depuis(h.t - 2), False)
# Pendant un appel, seul fn tenu de bout en bout vaut ta voix (t = 100 au départ ; ta parole de 101 à 104)
for nom, evenements, attendu in [
    ("tenue avant ta parole et lâchée après", (0.5, FN_BAS, 4.0, FN_HAUT, 0.5), True),
    ("encore enfoncée à la fin, depuis avant", (0.5, FN_BAS, 4.5), True),
    ("enfoncée en cours de route", (2.0, FN_BAS, 3.0, FN_HAUT), False),
    ("tapée au vol pendant qu'un autre parle", (2.0, FN_BAS, 0.2, FN_HAUT, 2.0), False),
    ("lâchée avant la fin", (0.5, FN_BAS, 1.5, FN_HAUT, 3.0), False),
    ("fn+une touche, tenue tout du long", (0.5, FN_BAS, (touche.TOUCHE,), 4.0), False),
]:
    fn, h = jouer(*evenements)
    verifier(f"appel : fn {nom}", fn.tenue_tout_du_long(101.0, 104.0), attendu)
fn, h = jouer(FN_BAS, 50.0)
verifier("coincée depuis 50 s : pas pour ta phrase", fn.tenue_depuis(h.t - 2), False)
fn, h = jouer()
verifier("sans fn : rien", fn.tenue_depuis(0), False)

a, b = threading.Event(), threading.Event()
deux = touche.L_un_ou_l_autre(a, b)
verifier("la veille : rien ne sonne", deux.is_set(), False)
b.set()
verifier("la veille : fn sonne", deux.is_set(), True)
b.clear()
a.set()
verifier("la veille : une annonce sonne", deux.is_set(), True)

# --- Le micro : fn vaut le mot-clé -------------------------------------------------------------------------------
print("\n👂 Le micro")
VOIX, BLANC = np.ones(config.FRAME, dtype=np.float32), np.zeros(config.FRAME, dtype=np.float32)


class FauxMotcle:
    def __init__(self, score: float = 0.0) -> None:
        self._score = score

    def score(self, _bloc) -> float:
        return self._score

    def oublier(self) -> None:
        pass


class FaussesOreilles(audio.Oreilles):
    def __init__(self, motcle: float = 0.0) -> None:  # (pas de vrai micro)
        self._q = queue.Queue()
        for bloc in [BLANC] * 3 + [VOIX] * 10 + [BLANC] * 8:
            self._q.put((bloc, bloc, False))
        self._ferme = threading.Event()
        self._motcle = FauxMotcle(motcle)
        self._chuchotee = None

    def vider(self) -> None:
        pass

    def _est_voix(self, propre, _debruite, calme=False) -> bool:
        return bool(propre[0])

    def _phrase_finie(self, _morceaux, silence, demande):
        return silence >= 0.4, demande

    def _signer(self, _phrase) -> None:
        pass

    def _voix_inconnue(self, _phrase) -> bool:
        return False


ancienne_detection = config.DETECTION
config.DETECTION = "motcle"
try:
    appels: list[float] = []

    def touche_tenue(debut: float) -> bool:
        appels.append(debut)
        return True

    avant = time.monotonic()
    phrase = FaussesOreilles().ecouter_phrase(1.0, veille=True, touche=touche_tenue)
    verifier("fn enfoncée : ta phrase est pour lui, sans mot-clé",
             (phrase is not None, phrase and phrase.reveil, phrase and phrase.par_touche), (True, True, True))
    verifier("le début de ta phrase est demandé (un peu avant qu'elle commence)",
             len(appels) == 1 and appels[0] <= time.monotonic() and appels[0] >= avant - 2, True)
    verifier("sans fn ni mot-clé : ignorée", FaussesOreilles().ecouter_phrase(0.5, veille=True,
                                                                             touche=lambda d: False), None)
    verifier("sans touche branchée : ignorée", FaussesOreilles().ecouter_phrase(0.5, veille=True), None)
    appels.clear()
    phrase = FaussesOreilles(motcle=0.9).ecouter_phrase(1.0, veille=True, touche=touche_tenue)
    verifier("le mot-clé entendu : fn n'est même pas regardée",
             (phrase.reveil, phrase.par_touche, appels), (True, False, []))
finally:
    config.DETECTION = ancienne_detection

# --- Réglages, autorisation --------------------------------------------------------------------------------------
print("\n🔐 Réglages")
verifier("allumée par défaut", config.TOUCHE_FN, os.environ.get("JERVIS_TOUCHE_FN", "oui").lower()
         not in {"non", "off", "0"})
verifier("son volet de Réglages est connu", outils.VOLETS.get("clavier", ("",))[0], "ListenEvent")


class FauxCG:
    demande = 0

    def CGRequestListenEventAccess(self):
        FauxCG.demande += 1
        return False


vrai_autorisee, vrai_cg = touche.autorisee, touche._graphiques
touche.autorisee, touche._graphiques = (lambda: False), (lambda: FauxCG())
try:
    souci = touche.demarrer(touche.Fn())
finally:
    touche.autorisee, touche._graphiques = vrai_autorisee, vrai_cg
verifier("sans l'autorisation : il dit laquelle allumer", "Surveillance de l'entrée" in (souci or ""), True)
verifier("sans l'autorisation : macOS est prié de l'ajouter à la liste", FauxCG.demande, 1)

import subprocess  # noqa: E402

vrai_run = subprocess.run
for nom, sortie, conseil in [("« Ne rien faire »", "0\n", False), ("les émojis", "2\n", True),
                             ("jamais réglé", "", True)]:
    subprocess.run = lambda *a, _s=sortie, **k: subprocess.CompletedProcess(a, 0, stdout=_s)
    try:
        verifier(f"le conseil pour fn dans macOS : {nom}", touche.reglage_systeme() is not None, conseil)
    finally:
        subprocess.run = vrai_run

# Les fonctions de macOS qu'il appelle existent bien (sans rien écouter : aucune écoute n'est créée ici)
import ctypes  # noqa: E402

cg = touche._graphiques()
cf = ctypes.cdll.LoadLibrary(touche._CADRES + "CoreFoundation.framework/CoreFoundation")
verifier("macOS : l'écoute du clavier et ses champs", all(hasattr(cg, f) for f in (
    "CGEventTapCreate", "CGEventTapEnable", "CGEventGetFlags", "CGEventGetIntegerValueField")), True)
verifier("macOS : la boucle d'événements", ctypes.c_void_p.in_dll(cf, "kCFRunLoopCommonModes").value is not None, True)

print(f"\n{'✅' if not echecs else '❌'} touche : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
