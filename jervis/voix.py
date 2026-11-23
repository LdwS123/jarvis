"""Le traitement de la voix, en local : chaque brique est un petit modèle open source.

- MotCle       : un mot-clé (« Hey Jarvis »…) détecté en continu (openWakeWord), si un modèle existe pour lui
- FinDeTour    : as-tu fini ta phrase, ou fais-tu juste une pause ? (pipecat-ai/smart-turn v3)
- Empreinte    : est-ce bien ta voix ? (wespeaker CAM++, via sherpa-onnx)
- Debruiteur   : retire le bruit de fond (DPDFNet, dérivé de DeepFilterNet, via sherpa-onnx)
- AntiEcho     : retire sa propre voix du micro (WebRTC AEC3, via LiveKit)

Les modèles sont rangés dans Jervis/modeles/voix (téléchargés une fois, quelques Mo chacun).
Chaque brique est optionnelle : si un modèle manque, l'assistant fonctionne sans elle.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np

from . import config

MODELES = config.DOSSIER / "modeles" / "voix"
_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SOURCES = {
    "smart-turn-v3.2-cpu.onnx": "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx",
    "dpdfnet_baseline.onnx": f"{_SHERPA}/speech-enhancement-models/dpdfnet_baseline.onnx",
    "wespeaker_en_voxceleb_CAM++.onnx": f"{_SHERPA}/speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx",
}


def modele(nom: str) -> Path:
    """Le chemin du modèle, téléchargé au premier besoin."""
    chemin = MODELES / nom
    if not chemin.exists():
        MODELES.mkdir(parents=True, exist_ok=True)
        provisoire = chemin.with_suffix(".part")
        urllib.request.urlretrieve(SOURCES[nom], provisoire)
        provisoire.rename(chemin)
    return chemin


def _flottant(audio_int16: np.ndarray) -> np.ndarray:
    return audio_int16.astype(np.float32) / 32768.0


# --- Mot-clé ---------------------------------------------------------------------------------------

class MotCle:
    """Note un score entre 0 et 1 à chaque bloc de 80 ms : ça monte quand tu dis le mot-clé."""

    def __init__(self) -> None:
        import openwakeword
        from openwakeword.model import Model

        nom = config.MOTCLE_MODELE
        if nom.endswith(".onnx"):  # un modèle à toi (livekit-wakeword ou notebook d'openWakeWord)
            chemin = Path(nom).expanduser()
            chemin = chemin if chemin.is_absolute() else MODELES / chemin
            if self._livekit(chemin):
                return
        else:  # un modèle fourni avec openWakeWord : hey_jarvis, alexa, hey_mycroft…
            chemin = Path(openwakeword.__file__).parent / "resources" / "models" / f"{nom}_v0.1.onnx"
        self._modele = Model(wakeword_models=[str(chemin)], inference_framework="onnx")
        self._nom = next(iter(self._modele.models))

    def _livekit(self, chemin: Path) -> bool:
        """Les modèles de livekit-wakeword ont leur propre encodeur, que openWakeWord ne sait pas lire.
        Même interface (predict → {nom: score}, reset) : on essaie lui d'abord."""
        try:
            from livekit.wakeword import WakeWordModel
            self._modele = WakeWordModel(models=[str(chemin)])
            self._nom = chemin.stem
            self._modele.predict(np.zeros(config.FRAME, dtype=np.int16))  # vérifie qu'il sait bien le lire
            self._modele.reset()
            return True
        except Exception:  # pas installé, ou modèle openWakeWord
            return False

    def score(self, bloc_int16: np.ndarray) -> float:
        return float(self._modele.predict(bloc_int16)[self._nom])

    def oublier(self) -> None:
        """Repart de zéro (sinon la fin du mot-clé d'avant peut redéclencher)."""
        self._modele.reset()


# --- Fin de phrase ---------------------------------------------------------------------------------

class FinDeTour:
    """Écoute l'intonation et le rythme des 8 dernières secondes pour deviner si tu as fini.
    « Je voudrais réserver un train pour… » (pause) → pas fini, il attend la suite."""

    DUREE = 8 * config.SAMPLE_RATE

    def __init__(self) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2
        options.log_severity_level = 3
        self._session = ort.InferenceSession(str(modele("smart-turn-v3.2-cpu.onnx")), sess_options=options)
        import importlib.util

        whisper = Path(importlib.util.find_spec("mlx_whisper").submodule_search_locations[0])  # sans charger MLX
        filtres = whisper / "assets" / "mel_filters.npz"
        self._mel = np.load(filtres)["mel_80"]  # mêmes filtres que Whisper (le modèle est un encodeur Whisper)
        self._fenetre = np.hanning(401)[:-1].astype(np.float32)  # Hann périodique, comme torch.hann_window

    def _log_mel(self, audio: np.ndarray) -> np.ndarray:
        """Le spectrogramme log-Mel de Whisper (400 points, pas de 160), calculé en numpy."""
        x = np.pad(audio, 200, mode="reflect")
        cadres = np.lib.stride_tricks.sliding_window_view(x, 400)[::160]
        puissance = np.abs(np.fft.rfft(cadres * self._fenetre, axis=-1)) ** 2
        puissance = puissance[:-1]  # Whisper jette la dernière trame
        log = np.log10(np.maximum(self._mel @ puissance.T, 1e-10))
        log = np.maximum(log, log.max() - 8.0)
        return ((log + 4.0) / 4.0).astype(np.float32)

    def probabilite(self, audio_int16: np.ndarray) -> float:
        """Probabilité que ta phrase soit finie (0 à 1)."""
        audio = _flottant(audio_int16[-self.DUREE:])
        audio = np.pad(audio, (self.DUREE - len(audio), 0))  # silence ajouté au début, comme à l'entraînement
        audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        entree = self._log_mel(audio)[None]
        return float(self._session.run(None, {"input_features": entree})[0].ravel()[0])


# --- Ta voix à toi ---------------------------------------------------------------------------------

class Empreinte:
    """Compare chaque phrase à l'empreinte de ta voix (fichier voix.npy), et l'affine à chaque phrase de toi.

    Sans voix.npy, il l'apprend tout seul : les phrases où tu dis son nom sont forcément de toi (la télé ne
    l'appelle pas). Après quelques-unes qui se ressemblent, l'empreinte est créée. Ces phrases-là sont gardées
    dans voix_debut.npy : un redémarrage ne remet pas le compteur à zéro. Ensuite, chaque phrase
    nettement reconnue comme la tienne la rapproche un peu de ta voix du moment (micro, fatigue, rhume…).
    voix_depart.npy garde la toute première empreinte : il ne peut pas s'en éloigner, donc pas de dérive
    vers une autre voix."""

    FICHIER = config.DOSSIER / "voix.npy"
    DEPART = config.DOSSIER / "voix_depart.npy"
    DEBUT = config.DOSSIER / "voix_debut.npy"   # les phrases sûres déjà entendues, tant que l'empreinte n'existe pas
    PHRASES_POUR_COMMENCER = 4   # phrases avec son nom, et qui se ressemblent, avant de créer l'empreinte
    MEME_VOIX = 0.5              # deux phrases de la même personne se ressemblent au moins autant
    GARDEES = 8                  # phrases sûres gardées en attendant (les plus récentes)
    PAS = 0.05                   # chaque phrase compte pour 5 % : il faut ~20 phrases pour changer vraiment
    ECART_MAX = 0.8              # ressemblance minimale avec l'empreinte de départ

    def __init__(self) -> None:
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(modele("wespeaker_en_voxceleb_CAM++.onnx")), num_threads=2)
        self._extracteur = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.reference = np.load(self.FICHIER) if self.FICHIER.exists() else None
        self.depart = np.load(self.DEPART) if self.DEPART.exists() else self.reference
        self._debut = self._relire_debut()  # les phrases sûres, avant que l'empreinte existe
        self.affinages = 0

    def _relire_debut(self) -> list[np.ndarray]:
        """Les phrases sûres des fois d'avant (rien si l'empreinte existe déjà, ou si le fichier est abîmé)."""
        if self.reference is not None or not self.DEBUT.exists():
            return []
        try:
            tous = np.load(self.DEBUT)
            return [v for v in tous.astype(np.float32)] if tous.ndim == 2 else []
        except (OSError, ValueError, EOFError):
            return []

    def _ranger_debut(self) -> None:
        try:
            if self._debut:
                _ecrire(self.DEBUT, np.array(self._debut, dtype=np.float32))
            else:
                self.DEBUT.unlink(missing_ok=True)
        except OSError:
            pass

    @property
    def en_attente(self) -> int:
        """Combien de phrases sûres il a déjà, avant de créer l'empreinte."""
        return len(self._debut)

    def vecteur(self, audio_int16: np.ndarray) -> np.ndarray:
        flux = self._extracteur.create_stream()
        flux.accept_waveform(config.SAMPLE_RATE, _flottant(audio_int16))
        flux.input_finished()
        v = np.array(self._extracteur.compute(flux), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def ressemblance(self, audio_int16: np.ndarray) -> float:
        """Cosinus entre cette phrase et ta voix : ~0,7-0,9 pour toi, ~0,2-0,5 pour quelqu'un d'autre."""
        assert self.reference is not None
        return float(self.vecteur(audio_int16) @ self.reference)

    def _sauver(self, reference: np.ndarray, depart: bool = False) -> None:
        self.reference = reference.astype(np.float32)
        for chemin in (self.FICHIER, *([self.DEPART] if depart else [])):
            try:
                _ecrire(chemin, self.reference)
            except OSError:
                pass
        if depart:
            self.depart = self.reference

    def apprendre(self, v: np.ndarray, sure: bool) -> str | None:
        """Une phrase de toi vient d'être comprise. `sure` : tu as dit son nom dedans.
        Renvoie un message quand l'empreinte vient d'être créée."""
        if self.reference is None:
            if not sure:
                return None
            self._debut = (self._debut + [v])[-self.GARDEES:]
            tous = np.array(self._debut)
            # Le plus grand groupe de phrases qui se ressemblent : une autre voix qui a dit son nom (ou une
            # phrase pleine de bruit) reste en dehors, au lieu de tout faire recommencer.
            voisins = (tous @ tous.T) >= self.MEME_VOIX
            proches = tous[voisins[int(voisins.sum(axis=1).argmax())]]
            if len(proches) < self.PHRASES_POUR_COMMENCER:
                self._ranger_debut()
                return f"{len(proches)} phrase{'s' if len(proches) > 1 else ''} sur {self.PHRASES_POUR_COMMENCER}"
            moyenne = proches.mean(axis=0)
            self._sauver(moyenne / np.linalg.norm(moyenne), depart=True)
            self._debut = []
            self._ranger_debut()
            return f"j'ai appris ta voix ({len(proches)} phrases) : maintenant j'ignore les autres voix"
        if float(v @ self.reference) < config.SEUIL_APPRENDRE:
            return None  # pas assez sûr que ce soit toi : on n'y touche pas
        nouvelle = (1 - self.PAS) * self.reference + self.PAS * v
        nouvelle /= np.linalg.norm(nouvelle)
        if self.depart is not None and float(nouvelle @ self.depart) < self.ECART_MAX:
            return None
        self._sauver(nouvelle)
        self.affinages += 1
        return None

    def enregistrer(self, phrases: list[np.ndarray]) -> list[float]:
        """Enregistre l'empreinte (la moyenne de tes phrases) et renvoie, pour chaque phrase, sa ressemblance
        avec l'empreinte des AUTRES phrases : une estimation honnête de ton score au quotidien."""
        vecteurs = np.array([self.vecteur(p) for p in phrases])
        scores = []
        for i in range(len(vecteurs)):
            autres = np.delete(vecteurs, i, axis=0).mean(axis=0)
            scores.append(float(vecteurs[i] @ (autres / np.linalg.norm(autres))))
        moyenne = vecteurs.mean(axis=0)
        self._sauver(moyenne / np.linalg.norm(moyenne), depart=True)
        self._debut = []
        self._ranger_debut()
        return scores


def _ecrire(chemin, valeur: np.ndarray) -> None:
    """Écrit à côté puis remplace : jamais de fichier à moitié écrit."""
    temporaire = chemin.with_suffix(".tmp")
    with temporaire.open("wb") as f:
        np.save(f, valeur)
    temporaire.replace(chemin)


# --- Bruit de fond ---------------------------------------------------------------------------------

class Debruiteur:
    """Débruitage en continu, bloc par bloc (~4 ms de calcul par bloc de 80 ms)."""

    def __init__(self) -> None:
        import sherpa_onnx as so

        modele_cfg = so.OfflineSpeechDenoiserModelConfig(
            dpdfnet=so.OfflineSpeechDenoiserDpdfNetModelConfig(model=str(modele("dpdfnet_baseline.onnx"))),
            num_threads=1)
        self._d = so.OnlineSpeechDenoiser(so.OnlineSpeechDenoiserConfig(model=modele_cfg))
        self._reste = np.zeros(0, dtype=np.float32)

    def traiter(self, bloc_int16: np.ndarray) -> np.ndarray:
        """Renvoie un bloc de même taille (avec ~10 ms de retard, sans importance ici)."""
        sortie = np.concatenate([self._reste, np.asarray(self._d.run(_flottant(bloc_int16), config.SAMPLE_RATE).samples,
                                                          dtype=np.float32)])
        n = len(bloc_int16)
        if len(sortie) < n:  # tout début : le modèle n'a pas encore assez d'audio
            sortie = np.concatenate([np.zeros(n - len(sortie), dtype=np.float32), sortie])
        self._reste = sortie[n:]
        return np.clip(sortie[:n] * 32768.0, -32768, 32767).astype(np.int16)


# --- Sa propre voix ----------------------------------------------------------------------------------

class AntiEcho:
    """Annulation d'écho WebRTC (celle de Chrome et de Google Meet).
    On lui donne ce que le haut-parleur joue (`reference`) et ce que le micro capte : elle soustrait l'un de l'autre."""

    TRAME = config.SAMPLE_RATE // 100  # le module travaille par trames de 10 ms

    def __init__(self, delai_ms: int) -> None:
        from livekit import rtc

        self._rtc = rtc
        # Le débruitage WebRTC reste coupé si DPDFNet tourne (deux débruiteurs à la suite abîment la voix).
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True, high_pass_filter=True,
            noise_suppression=config.DEBRUITAGE == "non", auto_gain_control=False)
        # WebRTC n'accepte que 0 à 500 ms. Un casque Bluetooth dépasse souvent : on plafonne
        # (l'anti-écho WebRTC rattrape seul une partie du décalage restant).
        self._delai = min(500, max(0, int(delai_ms)))

    def traiter(self, micro_int16: np.ndarray, reference_int16: np.ndarray) -> np.ndarray:
        """Les deux blocs doivent avoir la même taille (un multiple de 10 ms)."""
        sortie = np.empty_like(micro_int16)
        for i in range(0, len(micro_int16), self.TRAME):
            ref = self._rtc.AudioFrame(bytearray(reference_int16[i:i + self.TRAME].tobytes()),
                                       config.SAMPLE_RATE, 1, self.TRAME)
            self._apm.process_reverse_stream(ref)
            mic = self._rtc.AudioFrame(bytearray(micro_int16[i:i + self.TRAME].tobytes()),
                                       config.SAMPLE_RATE, 1, self.TRAME)
            try:
                self._apm.set_stream_delay_ms(self._delai)
            except RuntimeError:  # délai refusé : on traite quand même, sans indice de délai
                pass
            self._apm.process_stream(mic)
            sortie[i:i + self.TRAME] = np.frombuffer(mic.data, dtype=np.int16)
        return sortie
