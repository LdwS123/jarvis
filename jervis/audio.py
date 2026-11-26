"""Les oreilles (et la bouche) de Jervis : le micro, le haut-parleur, et tout ce qui nettoie le son.

Le chemin du son, toutes les 80 ms :

    micro 48 kHz ─► 16 kHz ─► anti-écho ─► gain (si tu parles bas) ─► « propre »  → transcription, « Hey Jarvis »,
                                   ▲                                       │           fin de phrase, ta voix
    haut-parleur ◄── sa voix ──────┘ (référence)                           ├► débruitage → détecteur de voix
                                                                           └► détecteur de voix (chuchotements)

Le micro et le haut-parleur passent par un seul flux audio (« duplex ») : l'anti-écho sait ainsi exactement
ce qui sort des enceintes, au bon moment. Si ce flux ne s'ouvre pas, on revient au micro seul et à `afplay`.

Tu branches un casque, tes AirPods se connectent, le micro tombe : un fil de surveillance le voit (appareils.py
demande à macOS) et rouvre le flux sur les bons appareils, sans redémarrer Steve.

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

from . import appareils, config

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
    chuchotee: bool = False  # dite en chuchotant (sans que la voix vibre)


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
        self.douceur = 1.0   # VOLUME_CHUCHOTE quand tu viens de lui chuchoter : il répond plus bas
        self.dernier_son = 0.0  # quand il a joué un son pour la dernière fois (time.monotonic)

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
        if rempli:
            self.dernier_son = time.monotonic()
        cible = self.volume * self.douceur
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
        self._q: "queue.Queue[tuple[np.ndarray, np.ndarray, bool]]" = queue.Queue()  # (propre, débruité, calme)
        self._ferme = threading.Event()
        self.en_pause = False
        self.niveau = 0.0  # le volume de ce que le micro capte (pour le tableau de bord)
        # Le compte rendu de réunion (reunion.py) reçoit ici chaque bloc, après l'anti-écho et avant tout gain.
        self.enregistrement = None

        self._motcle = (_charger(f"« {config.PHRASE_MOTCLE} »", voix.MotCle)
                        if config.DETECTION != "whisper" and config.MOTCLE_MODELE else None)
        self._fin = _charger("Fin de phrase intelligente", voix.FinDeTour) if config.FIN_INTELLIGENTE else None
        self._debruiteur = _charger("Débruitage", voix.Debruiteur) if config.DEBRUITAGE != "non" else None
        # Chuchotement : le gain, et un second détecteur sur le son non débruité (le débruitage efface un chuchotement).
        self._chuchotee = voix.chuchotee if config.CHUCHOTEMENT else None
        self._gain = voix.Gain() if config.CHUCHOTEMENT else None
        self._vad_brut = VAD() if config.CHUCHOTEMENT and self._debruiteur and config.DEBRUITAGE == "detection" else None
        self._empreinte = None
        if config.APPRENDRE_VOIX or voix.Empreinte.FICHIER.exists():
            self._empreinte = _charger("Reconnaissance de ta voix", voix.Empreinte)

        # Sa bouche : le même objet du début à la fin (tts le garde), même quand le flux est rouvert sur un casque.
        # `haut_parleur` vaut `enceinte` quand le flux duplex tourne, None en micro seul (alors il parle par afplay).
        self.enceinte = HautParleur(self._enceinte_active)
        self.haut_parleur: HautParleur | None = None
        self._anti_echo = None
        self._stream = None
        self._verrou_flux = threading.RLock()   # la surveillance, la pause et la fermeture ne se marchent pas dessus
        self._brut: "queue.Queue[tuple[np.ndarray, np.ndarray]]" = queue.Queue()
        self._mic, self._ref = _Decimateur(), _Decimateur()
        self._dernier_rappel = time.monotonic()  # le dernier bloc reçu du micro (s'il n'en vient plus, il est tombé)
        self._branches: tuple = (None, None, None)  # les appareils du moment où le flux a été ouvert
        micro, sortie = self._ouvrir()
        print(f"   🎙  J'écoute par « {micro} », je réponds par « {sortie} ».", flush=True)
        threading.Thread(target=self._traiter_duplex, daemon=True, name="jervis-son").start()
        threading.Thread(target=self._surveiller, daemon=True, name="jervis-casque").start()
        self._resume()

    # --- Ouverture du son ---------------------------------------------------------------------------

    def _enceinte_active(self) -> bool:
        flux = self._stream
        return self.haut_parleur is not None and flux is not None and bool(flux.active)

    def _signature(self) -> tuple:
        """(micro de macOS, sortie de macOS, micro choisi) : si ça change, un casque vient d'arriver ou de partir."""
        defaut, sortie = appareils.par_defaut()
        if defaut is None and sortie is None:
            return (None, None, None)  # macOS ne répond pas : on ne sait pas
        tous = appareils.tous() if (config.MICRO or "auto").strip().casefold() != "systeme" else []
        return (defaut and defaut.id, sortie and sortie.id, appareils.choisir_micro(config.MICRO, defaut, tous))

    @staticmethod
    def _index_micro(nom: str | None) -> int | None:
        """Le numéro PortAudio du micro qui porte ce nom (None : celui de macOS)."""
        if not nom:
            return None
        for i, appareil in enumerate(sd.query_devices()):
            if appareil["name"] == nom and appareil["max_input_channels"] > 0:
                return i
        return None

    def _ouvrir(self) -> tuple[str, str]:
        """Ouvre le flux sur les appareils du moment (duplex, sinon micro seul). Renvoie (micro, sortie)."""
        from . import voix

        self._branches = self._signature()
        entree = self._index_micro(self._branches[2])
        try:
            self._ouvrir_duplex(voix, entree)
            self._stream.start()
        except Exception as e:
            self._fermer_flux()
            self._ouvrir_micro_seul(entree)
            self._stream.start()  # (s'il rate aussi, la surveillance réessaiera, sans répéter ce message)
            print(f"   ⚠️  Flux micro + haut-parleur impossible ({e}) : micro seul, sans anti-écho.", flush=True)
        self._dernier_rappel = time.monotonic()
        try:
            micro = sd.query_devices(entree if entree is not None else sd.default.device[0])["name"]
            sortie = sd.query_devices(kind="output")["name"] if self.haut_parleur else "afplay"
        except Exception:
            micro, sortie = "le micro", "le haut-parleur"
        return micro, sortie

    def _ouvrir_duplex(self, voix, entree: int | None = None) -> None:
        sortie = sd.query_devices(kind="output")
        canaux = max(1, min(2, int(sortie["max_output_channels"])))
        enceinte = self.enceinte
        self._anti_echo = None

        def rappel(indata, outdata, frames, _temps, _statut) -> None:
            self._dernier_rappel = time.monotonic()
            ref = enceinte.remplir(frames)
            outdata[:] = ref[:, None]
            self._brut.put((indata[:, 0].copy(), ref))

        self._stream = sd.Stream(samplerate=TAUX_HP, blocksize=BLOC_HP, channels=(1, canaux), device=(entree, None),
                                 dtype="float32", callback=rappel)
        if config.ANTI_ECHO:
            # Le temps entre « envoyé au haut-parleur » et « revenu dans le micro » : latences de sortie + d'entrée.
            # (Recalculé à chaque ouverture : des AirPods n'ont pas du tout la latence des haut-parleurs du Mac.)
            delai = int(1000 * sum(self._stream.latency))
            self._anti_echo = _charger("Anti-écho", lambda: voix.AntiEcho(delai))
        self._mic, self._ref = _Decimateur(), _Decimateur()
        self.haut_parleur = enceinte

    def _ouvrir_micro_seul(self, entree: int | None = None) -> None:
        self.haut_parleur = None
        self._anti_echo = None

        def rappel(data, *_):
            self._dernier_rappel = time.monotonic()
            self._ajouter(data[:, 0].copy())

        self._stream = sd.InputStream(samplerate=config.SAMPLE_RATE, channels=1, dtype="int16", device=entree,
                                      blocksize=config.FRAME, callback=rappel)

    def _fermer_flux(self) -> None:
        """Ferme le flux sans attendre la fin du son (un appareil débranché ne la jouerait jamais)."""
        flux, self._stream = self._stream, None
        self.haut_parleur = None
        if flux is not None:
            flux.abort(ignore_errors=True)
            flux.close(ignore_errors=True)

    # --- Casque branché, micro tombé ------------------------------------------------------------------

    MUET_S = 3.0          # plus aucun bloc du micro depuis ce temps : le flux est mort (appareil parti)
    STABILISATION_S = 0.8  # macOS change la sortie puis le micro : on attend qu'il ait fini avant de rouvrir

    def _a_rouvrir(self) -> str | None:
        """Pourquoi il faut rouvrir le flux (None : tout va bien)."""
        flux = self._stream
        if flux is None or not flux.active:
            return "le micro s'était arrêté"
        if time.monotonic() - self._dernier_rappel > self.MUET_S:
            return "le micro ne donnait plus rien"
        signature = self._signature()
        if signature != (None, None, None) and signature != self._branches:
            return "nouveaux appareils son"
        return None

    def _rouvrir(self, raison: str) -> None:
        with self._verrou_flux:
            if self._ferme.is_set() or self.en_pause:
                return
            self._fermer_flux()
            self.enceinte.arreter()  # la phrase en cours ne finira jamais sur l'ancien appareil : on la libère
            try:
                sd._terminate()
            except Exception:
                pass
            sd._initialize()  # PortAudio relit la liste des appareils : il voit enfin le casque
            micro, sortie = self._ouvrir()
        print(f"   🎧 {raison[0].upper()}{raison[1:]} : je t'écoute par « {micro} », je réponds par « {sortie} ».",
              flush=True)

    def _surveiller(self) -> None:
        """Fil à part : toutes les SURVEILLANCE_SON_S secondes, il regarde si le micro vit encore et si macOS
        a changé d'appareils (casque, AirPods). Si oui, il rouvre le flux ; si ça rate, il réessaie au tour suivant."""
        en_panne = False
        while not self._ferme.wait(config.SURVEILLANCE_SON_S):
            if self.en_pause:
                continue
            try:
                raison = self._a_rouvrir()
                if raison is None:
                    continue
                if raison == "nouveaux appareils son":
                    self._ferme.wait(self.STABILISATION_S)
                self._rouvrir(raison)
                en_panne = False
            except Exception as e:  # pas de micro du tout pour l'instant : on réessaie, sans remplir le journal
                if not en_panne:
                    print(f"   ⚠️  Plus de micro ({type(e).__name__}: {e}) : je réessaie toutes les "
                          f"{config.SURVEILLANCE_SON_S:g} s.", flush=True)
                en_panne = True

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

    # Après sa voix, l'écho qui traîne dans la pièce : le gain et le détecteur de chuchotements attendent ce temps-là.
    APRES_SA_VOIX_S = 0.5

    def _calme(self) -> bool:
        """Vrai s'il ne parle pas (et n'a pas parlé juste avant) : on peut monter le son sans monter son écho.
        Micro seul (sans flux duplex), on ne sait pas quand il parle : jamais."""
        hp = self.haut_parleur
        return hp is not None and time.monotonic() - hp.dernier_son > self.APRES_SA_VOIX_S

    def _ajouter(self, propre: np.ndarray) -> None:
        x = propre.astype(np.float32) / 32768.0
        self.niveau = float(np.sqrt(x.dot(x) / max(1, len(x))))
        if (enregistrer := self.enregistrement) is not None:
            try:
                enregistrer(propre)
            except Exception:  # un compte rendu en panne ne doit jamais rendre Steve sourd
                self.enregistrement = None
        calme = self._calme()
        if self._gain:
            propre = self._gain.traiter(propre, figer=not calme)
        debruite = self._debruiteur.traiter(propre) if self._debruiteur else propre
        if config.DEBRUITAGE == "tout":
            propre = debruite
        self._q.put((propre, debruite, calme))

    def _resume(self) -> None:
        def etat(nom, ok, raison=""):
            return f"{'✓' if ok else '·'} {nom}{raison if not ok else ''}"

        print("   " + "  ".join([
            etat("anti-écho", self._anti_echo is not None),
            etat("débruitage", self._debruiteur is not None),
            *([etat("chuchotements", self._chuchotee is not None)] if config.CHUCHOTEMENT else []),
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
        if self._empreinte is None or phrase.empreinte is None or not config.APPRENDRE_VOIX or phrase.chuchotee:
            return None  # (un chuchotement n'apprend rien : son empreinte ne ressemble pas à ta voix)
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
        if self._vad_brut is not None:
            self._vad_brut.reset_states()
        if self._motcle:
            self._motcle.oublier()

    def pause(self, oui: bool) -> None:
        """Coupe vraiment le micro (plus rien n'est capté), ou le rallume. En pause, il parle via afplay."""
        with self._verrou_flux:
            self.en_pause = oui
            if oui:
                if self._stream is not None:
                    self._stream.stop(ignore_errors=True)
                self.enceinte.arreter()
                return
            self._dernier_rappel = time.monotonic()
            try:
                if self._stream is None or self._signature() not in {(None, None, None), self._branches}:
                    raise RuntimeError("appareils changés pendant la pause")
                self._stream.start()
            except Exception:
                try:
                    self._rouvrir("micro rallumé")
                except Exception:
                    pass  # la surveillance réessaiera

    def _lire(self, arret: threading.Event | None, fin: float | None = None):
        """Le bloc suivant (propre, débruité, calme), ou None si on doit arrêter d'écouter (ou si `fin` est passée)."""
        while not self._ferme.is_set() and not (arret and arret.is_set()):
            if fin is not None and time.monotonic() > fin:
                return None
            try:
                return self._q.get(timeout=0.2)
            except queue.Empty:
                continue
        return None

    def _est_voix(self, propre: np.ndarray, debruite: np.ndarray, calme: bool = False) -> bool:
        """Quelqu'un parle dans ce bloc ? Le détecteur écoute le son débruité ; pour un chuchotement, que le
        débruitage prend pour du souffle, un second écoute le son tel quel (seulement quand il ne parle pas)."""
        voix = float(self._vad.predict(debruite, frame_size=640)) >= config.SEUIL_VAD
        if self._vad_brut is not None:  # appelé à chaque bloc : il se souvient des blocs d'avant
            brut = float(self._vad_brut.predict(propre, frame_size=640)) >= config.SEUIL_VAD_BRUT
            voix = voix or (brut and calme)
        return voix

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
        parole = (f"{phrase.duree_parole:.1f} s de parole{' chuchotée' if phrase.chuchotee else ''}"
                  f"{' + mot-clé' if phrase.reveil else ''}")
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
            phrase.voix_stricte = self._empreinte.score_strict(phrase.empreinte)  # inchangé : un « oui » sensible
            phrase.seuil_voix = self._empreinte.seuil(phrase.duree_parole, phrase.reveil)
            if phrase.chuchotee:  # l'empreinte d'un chuchotement ne ressemble plus à ta voix : seuil bien plus bas
                phrase.seuil_voix = min(phrase.seuil_voix, config.SEUIL_VOIX_CHUCHOTE)

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
                propre, debruite, calme = lu
                if ecoute_motcle and self._motcle.score(propre) >= config.SEUIL_MOTCLE:
                    reveil = True
                if self._est_voix(propre, debruite, calme):
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
                propre, debruite, calme = lu
                morceaux.append(propre)
                if ecoute_motcle and not reveil and self._motcle.score(propre) >= config.SEUIL_MOTCLE:
                    reveil = True
                if self._est_voix(propre, debruite, calme):
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
            phrase.chuchotee = self._chuchotee is not None and self._chuchotee(phrase.audio)
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
        self._ferme.set()  # libère les fils d'écoute (et la surveillance) en attente
        with self._verrou_flux:
            self.enceinte.arreter()
            self._fermer_flux()
