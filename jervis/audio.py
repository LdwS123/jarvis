"""Les oreilles (et la bouche) de Jervis : le micro, le haut-parleur, et tout ce qui nettoie le son.

Le chemin du son, toutes les 80 ms :

    micro 48 kHz ─► 16 kHz ─► anti-écho (retire sa propre voix) ─► « propre »  → transcription, « Hey Jarvis »,
                                   ▲                                   │            fin de phrase, ta voix
    haut-parleur ◄── sa voix ──────┘ (référence)                       └► débruitage → détecteur de voix

Le micro et le haut-parleur passent par un seul flux audio (« duplex ») : l'anti-écho sait ainsi exactement
ce qui sort des enceintes, au bon moment. Si ce flux ne s'ouvre pas, on revient au micro seul et à `afplay`.

Chaque phrase est découpée par le détecteur de voix (Silero VAD). Sa fin est décidée par smart-turn :
après un court silence, il écoute l'intonation pour savoir si tu as fini ou si tu cherches tes mots.
"""

from __future__ import annotations

import asyncio
import collections
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from . import config

TAUX_HP = 48_000                                         # le flux duplex tourne en 48 kHz (natif sur Mac)
FACTEUR = TAUX_HP // config.SAMPLE_RATE                  # 3
BLOC_HP = config.FRAME * FACTEUR                         # 3840 échantillons = 80 ms


@dataclass
class Phrase:
    audio: np.ndarray       # int16, 16 kHz, sans l'écho de sa voix
    reveil: bool = False    # mot-clé (« Hey Jarvis »…) entendu par son modèle
    duree_parole: float = 0.0
    silence: float = 0.0    # le silence attendu après ta dernière syllabe, avant de dire « fini »
    empreinte: np.ndarray | None = None  # la « signature » de la voix de cette phrase (si assez longue)
    voix: float | None = None            # sa ressemblance avec ta voix (None : pas encore apprise)
    seuil_voix: float | None = None      # le score qu'il fallait pour être toi (selon sa longueur, le mot-clé)
    voix_stricte: float | None = None    # sa ressemblance avec ta voix principale seule (les « oui » sensibles)
    coupee: bool = False    # arrêtée par la durée maximale : tu parlais encore
    inconnue: bool = False  # pas ta voix (gardée seulement si on l'a demandé : un « stop » compte quand même)


class _Decimateur:
    """48 kHz → 16 kHz : filtre passe-bas (sinon les aigus se replient en sifflements), puis 1 échantillon sur 3.
    Le filtre garde la fin du bloc précédent, pour qu'il n'y ait pas de « clic » entre deux blocs."""

    def __init__(self, taps: int = 63) -> None:
        n = np.arange(taps) - (taps - 1) / 2
        coupure = 7_000 / TAUX_HP
        b = 2 * coupure * np.sinc(2 * coupure * n) * np.hamming(taps)
        self._b = (b / b.sum()).astype(np.float32)
        self._reste = np.zeros(taps - 1, dtype=np.float32)

    def __call__(self, x48: np.ndarray) -> np.ndarray:
        x = np.concatenate([self._reste, x48])
        self._reste = x[-(len(self._b) - 1):]
        y = np.convolve(x, self._b, mode="valid")[::FACTEUR]
        return np.clip(y * 32768.0, -32768, 32767).astype(np.int16)


class HautParleur:
    """La voix de Jervis passe par le flux duplex. On empile des sons ; le flux les joue à la suite."""

    def __init__(self, flux_actif) -> None:
        self._actif = flux_actif
        self._file: collections.deque[list] = collections.deque()  # [échantillons, position, fini()]
        self._verrou = threading.Lock()
        self.volume = 1.0    # 0 à 1 : il baisse la voix quand tu commences à lui parler
        self._gain = 1.0     # le volume réellement appliqué (il glisse vers `volume`, sans clic)
        self.niveau = 0.0    # le volume de ce qui sort en ce moment (pour le tableau de bord)

    @property
    def actif(self) -> bool:
        return bool(self._actif())

    def ajouter(self, echantillons: np.ndarray, fini=lambda: None) -> None:
        with self._verrou:
            self._file.append([np.asarray(echantillons, dtype=np.float32), 0, fini])

    async def jouer(self, echantillons: np.ndarray) -> None:
        """Joue un son (float32, 48 kHz, mono) et attend qu'il soit fini (ou coupé)."""
        boucle = asyncio.get_running_loop()
        termine = boucle.create_future()

        def fini() -> None:
            boucle.call_soon_threadsafe(lambda: termine.done() or termine.set_result(None))

        self.ajouter(echantillons, fini)
        await termine

    def arreter(self) -> None:
        """Coupe tout ce qui est en cours et en attente."""
        with self._verrou:
            items, self._file = list(self._file), collections.deque()
        for _, _, fini in items:
            fini()

    def remplir(self, n: int) -> np.ndarray:
        """Appelé par le flux audio : les `n` prochains échantillons à jouer (du silence s'il n'y a rien)."""
        sortie = np.zeros(n, dtype=np.float32)
        rempli = 0
        termines = []
        with self._verrou:
            while rempli < n and self._file:
                item = self._file[0]
                son, pos = item[0], item[1]
                k = min(n - rempli, len(son) - pos)
                sortie[rempli:rempli + k] = son[pos:pos + k]
                rempli += k
                item[1] = pos + k
                if item[1] >= len(son):
                    termines.append(self._file.popleft()[2])
        for fini in termines:
            fini()
        cible = self.volume
        if self._gain != cible or cible != 1.0:
            sortie *= np.linspace(self._gain, cible, n, dtype=np.float32)
            self._gain = cible
        self.niveau = float(np.sqrt(sortie.dot(sortie) / n)) if rempli else 0.0
        return sortie


def _charger(nom: str, fabrique):
    """Charge une brique de voix.py ; si elle échoue (modèle absent, pas d'Internet…), Jervis s'en passe."""
    try:
        return fabrique()
    except Exception as e:
        print(f"   ⚠️  {nom} indisponible ({type(e).__name__}: {e}) : je continue sans.", flush=True)
        return None


class Oreilles:
    # Les réglages d'écoute (avant-parole, durée minimale, vérification de la voix) sont dans config.py.

    def __init__(self) -> None:
        import openwakeword
        from openwakeword.vad import VAD

        from . import voix

        openwakeword.utils.download_models(model_names=[])  # récupère le modèle Silero VAD (2 Mo)
        self._vad = VAD()
        self._q: "queue.Queue[tuple[np.ndarray, np.ndarray]]" = queue.Queue()  # (propre, débruité)
        self._ferme = threading.Event()
        self.en_pause = False
        self.niveau = 0.0  # le volume de ce que le micro capte (pour le tableau de bord)

        self._motcle = (_charger(f"« {config.PHRASE_MOTCLE} »", voix.MotCle)
                        if config.DETECTION != "whisper" and config.MOTCLE_MODELE else None)
        self._fin = _charger("Fin de phrase intelligente", voix.FinDeTour) if config.FIN_INTELLIGENTE else None
        self._debruiteur = _charger("Débruitage", voix.Debruiteur) if config.DEBRUITAGE != "non" else None
        self._empreinte = None
        if config.APPRENDRE_VOIX or voix.Empreinte.FICHIER.exists():
            self._empreinte = _charger("Reconnaissance de ta voix", voix.Empreinte)

        self.haut_parleur: HautParleur | None = None
        self._anti_echo = None
        try:
            self._ouvrir_duplex(voix)
            self._stream.start()
        except Exception as e:
            print(f"   ⚠️  Flux micro + haut-parleur impossible ({e}) : micro seul, sans anti-écho.", flush=True)
            self._ouvrir_micro_seul()
            self._stream.start()
        self._resume()

    # --- Ouverture du son ---------------------------------------------------------------------------

    def _ouvrir_duplex(self, voix) -> None:
        sortie = sd.query_devices(kind="output")
        canaux = max(1, min(2, int(sortie["max_output_channels"])))
        self._brut: "queue.Queue[tuple[np.ndarray, np.ndarray]]" = queue.Queue()
        self.haut_parleur = HautParleur(lambda: self._stream.active)

        def rappel(indata, outdata, frames, _temps, _statut) -> None:
            ref = self.haut_parleur.remplir(frames)
            outdata[:] = ref[:, None]
            self._brut.put((indata[:, 0].copy(), ref))

        self._stream = sd.Stream(samplerate=TAUX_HP, blocksize=BLOC_HP, channels=(1, canaux),
                                 dtype="float32", callback=rappel)
        if config.ANTI_ECHO:
            # Le temps entre « envoyé au haut-parleur » et « revenu dans le micro » : latences de sortie + d'entrée.
            delai = int(1000 * sum(self._stream.latency))
            self._anti_echo = _charger("Anti-écho", lambda: voix.AntiEcho(delai))
        self._mic, self._ref = _Decimateur(), _Decimateur()
        threading.Thread(target=self._traiter_duplex, daemon=True, name="jervis-son").start()

    def _ouvrir_micro_seul(self) -> None:
        self.haut_parleur = None
        self._anti_echo = None

        def rappel(data, *_):
            self._ajouter(data[:, 0].copy())

        self._stream = sd.InputStream(samplerate=config.SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=config.FRAME, callback=rappel)

    def _traiter_duplex(self) -> None:
        """Fil à part : le rappel audio ne doit jamais attendre, donc le calcul se fait ici."""
        while not self._ferme.is_set():
            try:
                mic48, ref48 = self._brut.get(timeout=0.2)
            except queue.Empty:
                continue
            mic, ref = self._mic(mic48), self._ref(ref48)
            if self._anti_echo:
                try:
                    mic = self._anti_echo.traiter(mic, ref)
                except Exception as e:  # une panne d'anti-écho ne doit jamais rendre Steve sourd
                    print(f"   ⚠️  Anti-écho coupé ({e}) : il t'entend toujours, mais coupe-lui la parole "
                          "seulement quand il a fini de parler.", flush=True)
                    self._anti_echo = None
            self._ajouter(mic)

    def _ajouter(self, propre: np.ndarray) -> None:
        x = propre.astype(np.float32) / 32768.0
        self.niveau = float(np.sqrt(x.dot(x) / max(1, len(x))))
        debruite = self._debruiteur.traiter(propre) if self._debruiteur else propre
        if config.DEBRUITAGE == "tout":
            propre = debruite
        self._q.put((propre, debruite))

    def _resume(self) -> None:
        def etat(nom, ok, raison=""):
            return f"{'✓' if ok else '·'} {nom}{raison if not ok else ''}"

        print("   " + "  ".join([
            etat("anti-écho", self._anti_echo is not None),
            etat("débruitage", self._debruiteur is not None),
            *([etat(f"« {config.PHRASE_MOTCLE} »", self._motcle is not None)] if config.MOTCLE_MODELE else []),
            etat("fin de phrase intelligente", self._fin is not None),
            etat("ta voix", self.voix_connue, self._apprentissage()),
        ]), flush=True)

    def _apprentissage(self) -> str:
        """Où il en est pour apprendre ta voix (« 2 phrases sur 4 »), pour la ligne de démarrage."""
        if self._empreinte is None:
            return ""
        faites, total = self._empreinte.en_attente, self._empreinte.PHRASES_POUR_COMMENCER
        return (f" (en apprentissage : {faites} phrase{'s' if faites > 1 else ''} sur {total}, "
                "appelle-le par son nom)")

    @property
    def voix_connue(self) -> bool:
        return self._empreinte is not None and self._empreinte.reference is not None

    def apprendre_voix(self, phrase: Phrase, sure: bool) -> str | None:
        """Une phrase comprise, dite par toi : il affine ta voix (ou l'apprend, si `sure` = tu as dit son nom)."""
        if self._empreinte is None or phrase.empreinte is None or not config.APPRENDRE_VOIX:
            return None
        try:
            return self._empreinte.apprendre(phrase.empreinte, sure)
        except Exception:  # apprendre est un bonus : ça ne doit jamais l'empêcher de répondre
            return None

    @property
    def anti_echo(self) -> bool:
        """Vrai si sa propre voix est retirée du micro (alors on peut chercher son nom pendant qu'il parle)."""
        return self._anti_echo is not None and not self.en_pause

    # --- Commandes --------------------------------------------------------------------------------

    def vider(self) -> None:
        """Jette l'audio accumulé (par ex. ce qui restait de sa propre voix)."""
        while not self._q.empty():
            self._q.get_nowait()
        self._vad.reset_states()
        if self._motcle:
            self._motcle.oublier()

    def pause(self, oui: bool) -> None:
        """Coupe vraiment le micro (plus rien n'est capté), ou le rallume. En pause, il parle via afplay."""
        self.en_pause = oui
        if oui:
            self._stream.stop()
            if self.haut_parleur:
                self.haut_parleur.arreter()
        else:
            self._stream.start()

    def _lire(self, arret: threading.Event | None, fin: float | None = None):
        """Le bloc suivant (propre, débruité), ou None si on doit arrêter d'écouter (ou si `fin` est passée)."""
        while not self._ferme.is_set() and not (arret and arret.is_set()):
            if fin is not None and time.monotonic() > fin:
                return None
            try:
                return self._q.get(timeout=0.2)
            except queue.Empty:
                continue
        return None

    def _est_voix(self, bloc: np.ndarray) -> bool:
        return float(self._vad.predict(bloc, frame_size=640)) >= config.SEUIL_VAD

    @staticmethod
    def seuil_fin(silence: float) -> float:
        """La certitude qu'il lui faut pour dire « tu as fini », selon la durée de ta pause : très sûr juste
        après un blanc (SEUIL_FIN), de moins en moins exigeant jusqu'à SILENCE_MAX_S (SEUIL_FIN_TARD)."""
        etendue = max(config.SILENCE_MAX_S - config.SILENCE_COURT_S, 1e-6)
        avancee = min(max((silence - config.SILENCE_COURT_S) / etendue, 0.0), 1.0)
        return config.SEUIL_FIN + (config.SEUIL_FIN_TARD - config.SEUIL_FIN) * avancee

    def _phrase_finie(self, morceaux: list[np.ndarray], silence: float, demande_a: float) -> tuple[bool, float]:
        """Renvoie (finie, silence au moment de la dernière question à smart-turn, -1 = pas encore).
        Sans smart-turn : un silence fixe de SILENCE_END_S."""
        if self._fin is None:
            return silence >= config.SILENCE_END_S, demande_a
        if silence >= config.SILENCE_MAX_S:
            return True, demande_a
        if silence >= config.SILENCE_COURT_S and (demande_a < 0 or silence - demande_a >= config.ENTRE_QUESTIONS_S):
            return self._fin.probabilite(np.concatenate(morceaux)) >= self.seuil_fin(silence), silence
        return False, demande_a

    def _voix_inconnue(self, phrase: Phrase) -> bool:
        if phrase.voix is not None and phrase.seuil_voix is None:
            phrase.seuil_voix = config.SEUIL_VOIX
        inconnue = phrase.voix is not None and phrase.voix < phrase.seuil_voix
        if config.DIAGNOSTIC_VOIX:
            self._diagnostic(phrase, inconnue)
        if inconnue:
            print(f"   \033[2m(une voix qui n'est pas la tienne, ignorée : {phrase.voix:.2f})\033[0m", flush=True)
        return inconnue

    def _diagnostic(self, phrase: Phrase, inconnue: bool) -> None:
        """JERVIS_DIAGNOSTIC_VOIX=oui : une ligne par phrase dans jervis.log, pour régler les seuils de voix."""
        parole = f"{phrase.duree_parole:.1f} s de parole{' + mot-clé' if phrase.reveil else ''}"
        if phrase.voix is None:
            raison = "trop courte pour juger" if phrase.duree_parole < config.MIN_VERIF_VOIX_S else "pas encore d'empreinte"
            print(f"   🔎 voix : {raison} ({parole}) → acceptée", flush=True)
            return
        empreinte = self._empreinte
        detail = " / ".join(f"{x:.2f}" for x in empreinte.scores(phrase.empreinte))
        depart = f", départ {float(phrase.empreinte @ empreinte.depart):.2f}" if empreinte.depart is not None else ""
        print(f"   🔎 voix : {phrase.voix:.2f} pour un seuil de {phrase.seuil_voix:.2f} ({parole} ; "
              f"empreintes {detail}{depart}) → {'ignorée' if inconnue else 'acceptée'}", flush=True)

    def _signer(self, phrase: Phrase) -> None:
        """Calcule une seule fois la signature de la voix : elle sert à vérifier ET à apprendre."""
        if self._empreinte is None or phrase.duree_parole < config.MIN_VERIF_VOIX_S:
            return
        try:
            phrase.empreinte = self._empreinte.vecteur(phrase.audio)
        except Exception:
            return
        if self._empreinte.reference is not None:
            phrase.voix = self._empreinte.score(phrase.empreinte)
            phrase.voix_stricte = self._empreinte.score_strict(phrase.empreinte)
            phrase.seuil_voix = self._empreinte.seuil(phrase.duree_parole, phrase.reveil)

    def ecouter_phrase(
        self,
        attente_max: float | None = None,
        arret: threading.Event | None = None,
        duree_max: float | None = None,
        repartir_a_zero: bool = True,
        veille: bool = False,
        verifier_voix: bool = True,
        laisser_finir: bool = False,
        quand_tu_parles=None,
        garder_inconnue: bool = False,
    ) -> Phrase | None:
        """Attend que tu parles, puis enregistre jusqu'à la fin de ta phrase.
        Renvoie None si personne n'a parlé pendant `attente_max` secondes (None = attendre sans fin)
        ou si `arret` est déclenché. `duree_max` coupe les longues phrases (pour guetter « stop »).
        `veille=True` : écoute aussi le mot-clé (et, en mode motcle, ignore les phrases sans lui).
        `laisser_finir=True` : `arret` n'agit qu'entre deux phrases, jamais au milieu de la tienne.
        `quand_tu_parles(True)` est appelé dès qu'on est sûr que quelqu'un parle (pas un simple bruit),
        `quand_tu_parles(False)` si ça n'était finalement qu'un bruit.
        `garder_inconnue=True` : une voix qui n'est pas la tienne est quand même rendue (marquée `inconnue`) :
        pendant qu'il parle, sa voix mêlée à la tienne fait baisser ton score, et un « stop » doit passer."""
        if repartir_a_zero:
            self.vider()
        duree_bloc = config.FRAME / config.SAMPLE_RATE
        duree_max = duree_max or config.MAX_UTTERANCE_S
        ecoute_motcle = veille and self._motcle is not None
        fin = None if attente_max is None else time.monotonic() + attente_max
        avant_parole = max(1, round(config.AVANT_PAROLE_S / duree_bloc))  # en blocs de 80 ms

        while True:
            avant: collections.deque[np.ndarray] = collections.deque(maxlen=avant_parole)
            reveil = False

            # 1) on attend le début de la voix
            while True:
                lu = self._lire(arret, fin)
                if lu is None:
                    return None
                propre, debruite = lu
                if ecoute_motcle and self._motcle.score(propre) >= config.SEUIL_MOTCLE:
                    reveil = True
                if self._est_voix(debruite):
                    break
                avant.append(propre)

            # 2) on enregistre jusqu'à la fin de la phrase
            morceaux = [*avant, propre]
            parole, silence, demande = duree_bloc, 0.0, -1.0
            signale = False
            coupee = True  # sauf si on sort de la boucle parce que ta phrase est finie
            while len(morceaux) * duree_bloc < duree_max:
                lu = self._lire(None if laisser_finir else arret)
                if lu is None:
                    return None
                propre, debruite = lu
                morceaux.append(propre)
                if ecoute_motcle and not reveil and self._motcle.score(propre) >= config.SEUIL_MOTCLE:
                    reveil = True
                if self._est_voix(debruite):
                    parole += duree_bloc
                    silence, demande = 0.0, -1.0  # tu as repris : on redemandera à smart-turn
                    if quand_tu_parles and not signale and parole >= config.MIN_PAROLE_S:
                        signale = True
                        quand_tu_parles(True)
                else:
                    silence += duree_bloc
                    finie, demande = self._phrase_finie(morceaux, silence, demande)
                    if finie:
                        coupee = False
                        break

            if parole < config.MIN_PAROLE_S:
                continue  # simple bruit : on recommence à attendre
            if veille and config.DETECTION == "motcle" and self._motcle is not None and not reveil:
                if signale and quand_tu_parles:
                    quand_tu_parles(False)  # pas pour lui : on remet le volume
                continue  # sans le mot-clé, on ne transcrit même pas
            phrase = Phrase(np.concatenate(morceaux), reveil, parole, silence, coupee=coupee)
            self._signer(phrase)
            if verifier_voix and self._voix_inconnue(phrase):
                if garder_inconnue:
                    phrase.inconnue = True
                    return phrase
                if quand_tu_parles:
                    quand_tu_parles(False)  # quelqu'un d'autre : on remet le volume
                continue
            if ecoute_motcle:
                self._motcle.oublier()  # sinon la fin du mot-clé pourrait redéclencher la phrase suivante
            return phrase

    def fermer(self) -> None:
        self._ferme.set()  # libère les fils d'écoute en attente
        if self.haut_parleur:
            self.haut_parleur.arreter()
        self._stream.stop()
        self._stream.close()
