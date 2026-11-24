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
    vers une autre voix.

    Ta voix change pourtant d'un jour à l'autre (fatigue, rhume, autre micro) : une seule moyenne la reconnaît
    mal ces jours-là. Il garde donc d'autres empreintes dans voix_centres.npy (config.VOIX_CENTRES en tout) :
    quand tu dis son nom d'une voix qu'il reconnaissait de justesse, il en ajoute une, et chacune s'affine sur
    les phrases qui lui ressemblent le plus. Une phrase est à toi si elle ressemble à l'une d'elles. Elles restent
    toutes proches de voix_depart.npy (config.ECART_CENTRE), et voix.npy garde le même rôle qu'avant."""

    FICHIER = config.DOSSIER / "voix.npy"
    DEPART = config.DOSSIER / "voix_depart.npy"
    DEBUT = config.DOSSIER / "voix_debut.npy"   # les phrases sûres déjà entendues, tant que l'empreinte n'existe pas
    CENTRES = config.DOSSIER / "voix_centres.npy"  # tes autres empreintes (voix fatiguée, autre micro…)
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
        self._charger()

    def _charger(self) -> None:
        """Relit tes empreintes sur le disque (au démarrage)."""
        self.reference = np.load(self.FICHIER) if self.FICHIER.exists() else None
        self.depart = np.load(self.DEPART) if self.DEPART.exists() else self.reference
        self._debut = self._relire_debut()  # les phrases sûres, avant que l'empreinte existe
        self.centres = self._relire_centres()
        self.affinages = 0

    def _relire_centres(self) -> list[np.ndarray]:
        """Tes empreintes en plus de voix.npy (aucune si le fichier manque, est abîmé ou d'une autre taille)."""
        if self.reference is None or not self.CENTRES.exists():
            return []
        try:
            tous = np.load(self.CENTRES).astype(np.float32)
        except (OSError, ValueError, EOFError):
            return []
        if tous.ndim != 2 or tous.shape[1] != self.reference.shape[0]:
            return []
        return [c / (np.linalg.norm(c) + 1e-9) for c in tous][:max(0, config.VOIX_CENTRES - 1)]

    def _ranger_centres(self) -> None:
        try:
            if self.centres:
                _ecrire(self.CENTRES, np.array(self.centres, dtype=np.float32))
            else:
                self.CENTRES.unlink(missing_ok=True)
        except OSError:
            pass

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
        return self.score(self.vecteur(audio_int16))

    def scores(self, v: np.ndarray) -> list[float]:
        """La ressemblance de cette phrase avec chacune de tes empreintes (voix.npy d'abord)."""
        return [float(v @ c) for c in (self.reference, *self.centres)]

    def score(self, v: np.ndarray) -> float:
        """La ressemblance avec ta voix : celle de l'empreinte la plus proche. Une phrase que seules les autres
        empreintes reconnaissent doit aussi ressembler un minimum à ta voix de départ (VOIX_PLANCHER)."""
        assert self.reference is not None
        principale, *autres = self.scores(v)
        if autres and (self.depart is None or float(v @ self.depart) >= config.VOIX_PLANCHER):
            return max(principale, *autres)
        return principale

    def score_strict(self, v: np.ndarray) -> float:
        """Pour un « oui » sensible : la ressemblance avec voix.npy ou ta voix de départ seulement. Les autres
        empreintes (apprises toutes seules, dès qu'on dit son nom) ne suffisent jamais à autoriser une action."""
        assert self.reference is not None
        return max(float(v @ self.reference), *([float(v @ self.depart)] if self.depart is not None else []))

    @staticmethod
    def seuil(duree_parole: float, reveil: bool = False) -> float:
        """Le score qu'il faut pour que ce soit toi. Une phrase courte a une empreinte moins nette : le seuil
        descend en douceur de SEUIL_VOIX (VOIX_LONGUE_S de parole et plus) à SEUIL_VOIX_COURTE (MIN_VERIF_VOIX_S).
        Le mot-clé entendu par son modèle (la télé ne le dit pas) le descend jusqu'à SEUIL_VOIX_REVEIL."""
        court = config.MIN_VERIF_VOIX_S
        etendue = max(config.VOIX_LONGUE_S - court, 1e-6)
        avancee = min(max((duree_parole - court) / etendue, 0.0), 1.0)
        seuil = min(config.SEUIL_VOIX, config.SEUIL_VOIX_COURTE + (config.SEUIL_VOIX - config.SEUIL_VOIX_COURTE) * avancee)
        return min(seuil, config.SEUIL_VOIX_REVEIL) if reveil else seuil

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
            self._debut, self.centres = [], []
            self._ranger_debut()
            self._ranger_centres()
            return f"j'ai appris ta voix ({len(proches)} phrases) : maintenant j'ignore les autres voix"
        scores = self.scores(v)
        if self.score(v) < config.SEUIL_APPRENDRE:
            # Pas assez sûr que ce soit toi : on n'affine rien. Mais avec son nom dedans, c'est bien toi, avec
            # une voix qu'il reconnaît mal (fatigue, autre micro) : il en garde une empreinte de plus.
            return self._ajouter_centre(v) if sure else None
        if scores[0] >= config.SEUIL_APPRENDRE:
            nouvelle = (1 - self.PAS) * self.reference + self.PAS * v
            nouvelle /= np.linalg.norm(nouvelle)
            if self.depart is None or float(nouvelle @ self.depart) >= self.ECART_MAX:
                self._sauver(nouvelle)
                self.affinages += 1
        plus_proche = int(np.argmax(scores))
        if plus_proche > 0 and scores[plus_proche] >= config.SEUIL_APPRENDRE:
            # Une de tes autres voix : c'est elle qui s'affine (sans jamais s'éloigner de ta voix de départ).
            centre = (1 - self.PAS) * self.centres[plus_proche - 1] + self.PAS * v
            centre /= np.linalg.norm(centre)
            if self.depart is None or float(centre @ self.depart) >= config.ECART_CENTRE:
                self.centres[plus_proche - 1] = centre.astype(np.float32)
                self._ranger_centres()
                self.affinages += 1
        return None

    def _ajouter_centre(self, v: np.ndarray) -> str | None:
        """Une empreinte de plus pour ta voix, si elle reste assez proche de ta voix de départ. Toutes les
        places prises : les deux empreintes les plus semblables (la nouvelle comprise) n'en font plus qu'une."""
        places = config.VOIX_CENTRES - 1
        if places <= 0 or (self.depart is not None and float(v @ self.depart) < config.ECART_CENTRE):
            return None
        tous = [*self.centres, v.astype(np.float32)]
        if len(tous) > places:
            matrice = np.array(tous) @ np.array(tous).T
            np.fill_diagonal(matrice, -2.0)
            i, j = sorted(np.unravel_index(int(np.argmax(matrice)), matrice.shape))
            fusion = tous[i] + tous[j]
            tous = [c for k, c in enumerate(tous) if k not in (i, j)] + [(fusion / np.linalg.norm(fusion)).astype(np.float32)]
        self.centres = tous
        self._ranger_centres()
        return f"une empreinte de plus pour ta voix ({1 + len(self.centres)} en tout)"

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
        self._debut, self.centres = [], []   # une voix toute neuve : les anciennes empreintes ne valent plus
        self._ranger_debut()
        self._ranger_centres()
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
