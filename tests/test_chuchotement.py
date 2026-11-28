"""Quand tu lui chuchotes (jervis/voix.py : Gain, part_voisee, chuchotee ; leur branchement dans audio.py et stt.py),
sans micro, sans modèle : les détecteurs sont remplacés par des faux. Ni Internet, ni carte graphique, ni son.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_chuchotement.py
"""

import io
import os
import threading
import time
from contextlib import redirect_stdout
from types import SimpleNamespace

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402

from jervis import audio, config, stt, voix  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


SR = config.SAMPLE_RATE
BLOC = 1280
hasard = np.random.default_rng(7)


def db(x: np.ndarray) -> float:
    f = x.astype(np.float32) / 32768.0
    return 20 * np.log10(np.sqrt(f.dot(f) / len(f)) + 1e-12)


def au_niveau(x: np.ndarray, niveau_db: float) -> np.ndarray:
    """Un son float, ramené à `niveau_db` dBFS, en int16."""
    x = x / (np.sqrt(np.mean(x ** 2)) + 1e-12) * 10 ** (niveau_db / 20)
    return np.clip(x * 32768, -32768, 32767).astype(np.int16)


def syllabes(n: int) -> np.ndarray:
    """L'enveloppe d'une phrase : des syllabes de ~200 ms séparées de petits blancs."""
    t = np.arange(n) / SR
    return np.maximum(np.sin(2 * np.pi * 2.5 * t), 0) ** 0.5


def voix_qui_vibre(duree: float = 1.6, hauteur: float = 140.0) -> np.ndarray:
    """Une voix « normale » de synthèse : une hauteur qui se répète (les cordes vocales) et ses harmoniques."""
    n = int(duree * SR)
    t = np.arange(n) / SR
    f = hauteur * (1 + 0.05 * np.sin(2 * np.pi * 1.3 * t))
    phase = 2 * np.pi * np.cumsum(f) / SR
    son = sum(np.sin(k * phase) / k for k in range(1, 12))
    return son * syllabes(n) + 0.01 * hasard.standard_normal(n)


def souffle(duree: float = 1.6) -> np.ndarray:
    """Un chuchotement de synthèse : du bruit coloré par deux « formants », sans aucune hauteur."""
    n = int(duree * SR)
    spectre = np.fft.rfft(hasard.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    forme = np.exp(-((f - 700) / 250) ** 2) + 0.7 * np.exp(-((f - 1800) / 400) ** 2) + 0.2 * (f > 3000)
    return np.fft.irfft(spectre * forme, n) * syllabes(n)


def par_blocs(gain: voix.Gain, son: np.ndarray, figer: bool = False) -> np.ndarray:
    return np.concatenate([gain.traiter(son[i:i + BLOC], figer) for i in range(0, len(son) // BLOC * BLOC, BLOC)])


# --- Le gain ------------------------------------------------------------------------------------------

config.GAIN_MAX, config.BRUIT_MAX_DB, config.NIVEAU_VOIX_DB = 10.0, -45.0, -26.0

g = voix.Gain()
# un chuchotement lointain (-58 dB), dans une pièce calme (bruit de fond à -80 dB)
bas = au_niveau(souffle(3.0), -58) + au_niveau(hasard.standard_normal(SR * 3), -80)
sortie = par_blocs(g, bas)
verifier("un son très bas est monté jusqu'à GAIN_MAX", round(g.gain, 2), 10.0)
verifier("… ×10, c'est +20 dB à la fin", round(db(sortie[-SR:]) - db(bas[len(sortie) - SR:len(sortie)])), 20)
verifier("… en douceur : le premier bloc n'est pas monté d'un coup", bool(db(sortie[:BLOC]) - db(bas[:BLOC]) < 3), True)

g = voix.Gain()
fort = au_niveau(voix_qui_vibre(3.0), -22)                   # tu parles normalement
verifier("une voix normale passe telle quelle", np.array_equal(par_blocs(g, fort), fort[:len(fort) // BLOC * BLOC]),
         True)
verifier("… gain ×1", g.gain, 1.0)

g = voix.Gain()
ventilo = au_niveau(hasard.standard_normal(SR * 4), -52)     # un bruit de fond seul, déjà un peu fort
par_blocs(g, ventilo)
verifier("un bruit de fond n'est jamais monté au-dessus de BRUIT_MAX_DB", round(g.gain, 1), round(10 ** (7 / 20), 1))

g = voix.Gain()
verifier("figé (il parle, ou vient de parler) : rien n'est monté", np.array_equal(par_blocs(g, bas, figer=True),
                                                                                   bas[:len(bas) // BLOC * BLOC]), True)
verifier("… gain ×1", g.gain, 1.0)

g = voix.Gain()
par_blocs(g, bas)
g.traiter(au_niveau(hasard.standard_normal(BLOC), -15))
verifier("une voix forte arrive : il redescend tout de suite", g.gain, 1.0)
for _ in range(voix.Gain.TENUE):
    g.traiter(bas[:BLOC])
verifier("… et attend TENUE blocs avant de remonter (les blancs entre tes mots)", g.gain, 1.0)
g.traiter(bas[:BLOC])
verifier("… puis remonte, doucement", round(g.gain, 2), round(voix.Gain.MONTEE, 2))

g = voix.Gain()
claque = np.zeros(BLOC, np.int16)
claque[600:640] = 30000
par_blocs(g, bas)
verifier("pas de saturation sur un bruit sec après un chuchotement", int(np.abs(g.traiter(claque)).max()), 30000)

# --- As-tu chuchoté ? --------------------------------------------------------------------------------

normale, chuchotement = au_niveau(voix_qui_vibre(), -30), au_niveau(souffle(), -50)
p_normale, p_chuchotement = voix.part_voisee(normale), voix.part_voisee(chuchotement)
verifier("une voix qui vibre est surtout « voisée »", bool(p_normale > 0.6), True)
verifier("un chuchotement presque pas", bool(p_chuchotement < config.SEUIL_CHUCHOTE), True)
verifier("chuchotee() : voix normale", voix.chuchotee(normale), False)
verifier("chuchotee() : chuchotement", voix.chuchotee(chuchotement), True)
verifier("… et même chuchoté de très loin (-62 dB)", voix.chuchotee(au_niveau(souffle(), -62)), True)
verifier("… et une voix normale lointaine reste normale (-45 dB)", voix.chuchotee(au_niveau(voix_qui_vibre(), -45)),
         False)
verifier("trop court pour le dire : None", voix.part_voisee(normale[:300]), None)
verifier("du silence : None (et pas « chuchoté »)", (voix.part_voisee(np.zeros(SR, np.int16)),
                                                       voix.chuchotee(np.zeros(SR, np.int16))), (None, False))

# --- Le second détecteur de voix (audio.Oreilles._est_voix) -----------------------------------------


class FauxVAD:
    def __init__(self, score: float):
        self.score, self.appels = score, 0

    def predict(self, bloc, frame_size=640):
        self.appels += 1
        return self.score


def est_voix(debruite: float, brut: float | None, calme: bool) -> tuple[bool, int]:
    vad_brut = FauxVAD(brut) if brut is not None else None
    faux = SimpleNamespace(_vad=FauxVAD(debruite), _vad_brut=vad_brut)
    bloc = np.zeros(BLOC, np.int16)
    return audio.Oreilles._est_voix(faux, bloc, bloc, calme), vad_brut.appels if vad_brut else 0


verifier("le débruité entend : voix", est_voix(0.9, 0.0, False)[0], True)
verifier("chuchotement effacé par le débruitage, entendu tel quel, au calme : voix", est_voix(0.0, 0.8, True)[0], True)
verifier("… mais pas pendant qu'il parle (son écho)", est_voix(0.0, 0.8, False)[0], False)
verifier("… et le second détecteur écoute quand même chaque bloc (sa mémoire)", est_voix(0.0, 0.8, False)[1], 1)
verifier("un souffle tiède (0,5) ne suffit pas au second détecteur", est_voix(0.0, 0.5, True)[0], False)
verifier("sans second détecteur (JERVIS_CHUCHOTEMENT=non) : comme avant", est_voix(0.0, None, True)[0], False)

# --- Le calme (pas d'écho de sa voix à monter) --------------------------------------------------------

hp = SimpleNamespace(dernier_son=time.monotonic())
verifier("il vient de parler : pas calme", audio.Oreilles._calme(SimpleNamespace(haut_parleur=hp,
                                                                                 APRES_SA_VOIX_S=0.5)), False)
hp.dernier_son -= 2
verifier("il s'est tu depuis 2 s : calme", audio.Oreilles._calme(SimpleNamespace(haut_parleur=hp,
                                                                                 APRES_SA_VOIX_S=0.5)), True)
verifier("micro seul (pas de flux duplex) : jamais calme",
         audio.Oreilles._calme(SimpleNamespace(haut_parleur=None, APRES_SA_VOIX_S=0.5)), False)

# --- Sa voix à toi : seuil d'un chuchotement, et rien appris ------------------------------------------


class FausseEmpreinte:
    reference = np.ones(3)

    def vecteur(self, son): return np.ones(3)
    def score(self, v): return 0.4
    def score_strict(self, v): return 0.4
    def seuil(self, duree, reveil): return 0.55
    def apprendre(self, v, sure): return "appris"


config.MIN_VERIF_VOIX_S, config.SEUIL_VOIX_CHUCHOTE, config.DIAGNOSTIC_VOIX, config.APPRENDRE_VOIX = 0.5, 0.3, False, True
oreilles = SimpleNamespace(_empreinte=FausseEmpreinte(), _diagnostic=lambda *a: None, au_telephone=False)


def jugee(chuchotee: bool):
    phrase = audio.Phrase(np.zeros(SR, np.int16), duree_parole=2.0, chuchotee=chuchotee)
    audio.Oreilles._signer(oreilles, phrase)
    with redirect_stdout(io.StringIO()):
        return phrase, audio.Oreilles._voix_inconnue(oreilles, phrase)


phrase, inconnue = jugee(False)
verifier("voix normale à 0,40 pour un seuil de 0,55 : pas toi", (phrase.seuil_voix, inconnue), (0.55, True))
phrase, inconnue = jugee(True)
verifier("la même, chuchotée : seuil abaissé, c'est toi", (phrase.seuil_voix, inconnue), (0.3, False))
verifier("… mais le score strict (les « oui » sensibles) ne change pas", phrase.voix_stricte, 0.4)
verifier("Phrase(audio, reveil, parole, silence) se construit toujours dans l'ordre",
         audio.Phrase(np.zeros(1, np.int16), True, 1.0, 0.3).chuchotee, False)

faux = SimpleNamespace(_empreinte=FausseEmpreinte(), au_telephone=False)
normale_p = audio.Phrase(np.zeros(1, np.int16), empreinte=np.ones(3))
chuchotee_p = audio.Phrase(np.zeros(1, np.int16), empreinte=np.ones(3), chuchotee=True)
verifier("une phrase normale apprend ta voix", audio.Oreilles.apprendre_voix(faux, normale_p, True), "appris")
verifier("un chuchotement n'apprend rien", audio.Oreilles.apprendre_voix(faux, chuchotee_p, True), None)

# --- Il te répond plus bas (audio.HautParleur.douceur) -------------------------------------------------

hp = audio.HautParleur(lambda: True)
hp.ajouter(np.full(4800, 0.5, np.float32))
avant = time.monotonic()
hp.remplir(480)
verifier("jouer un son note l'heure (pour le calme)", bool(hp.dernier_son >= avant), True)
hp.douceur = 0.35
hp.remplir(480)                                   # la rampe vers le nouveau volume
verifier("douceur 0,35 : il parle à 35 %", round(float(hp.remplir(480)[0]), 3), 0.175)
hp.douceur = 1.0
hp.remplir(480)
verifier("douceur 1 : sa voix normale revient", round(float(hp.remplir(480)[0]), 3), 0.5)
hp.arreter()
hp.dernier_son = 0.0
hp.remplir(480)
verifier("du silence ne compte pas comme un son joué", hp.dernier_son, 0.0)

# --- Avant la transcription (stt._flottant) ----------------------------------------------------------

config.CHUCHOTEMENT = True
tres_bas = au_niveau(souffle(), -55)
pic = float(np.abs(tres_bas).max()) / 32768
verifier("une phrase très basse est remontée (pics à 0,5, ×8 au plus)",
         round(float(np.abs(stt._flottant(tres_bas)).max()), 3), round(min(0.5, pic * 8), 3))
normal = au_niveau(voix_qui_vibre(), -20)
verifier("une voix normale n'est pas touchée", np.array_equal(stt._flottant(normal), normal.astype(np.float32) / 32768),
         True)
verifier("du silence reste du silence", float(np.abs(stt._flottant(np.zeros(SR, np.int16))).max()), 0.0)
config.CHUCHOTEMENT = False
verifier("JERVIS_CHUCHOTEMENT=non : rien n'est remonté",
         np.array_equal(stt._flottant(tres_bas), tres_bas.astype(np.float32) / 32768), True)

print(f"\n{'✅' if not echecs else '❌'} chuchotement : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
