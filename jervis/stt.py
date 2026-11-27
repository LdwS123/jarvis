"""Transcription de ta voix en texte, en local sur la puce Apple (MLX).

Deux moteurs :
- Parakeet v3 (NVIDIA, par défaut) : ~90 ms par phrase, 25 langues dont le français, détecte la langue seul
  (+ ~150 ms quand on cherche son nom et qu'il ne l'a pas reconnu du premier coup) ;
- Whisper large-v3-turbo : ~350 ms par phrase, en secours ou avec JERVIS_STT=whisper. Il sert aussi de
  deuxième avis, en français imposé, quand Parakeet écrit en anglais une phrase de quelques mots.

Quand tu lui parles plusieurs langues (JERVIS_LANGUES, voir langues.py), Whisper reconnaît d'abord la langue de
chaque phrase, parmi les tiennes (~0,2 s). Une langue que Parakeet connaît (français, anglais, russe…) part ensuite
chez lui ; les autres (chinois, géorgien, japonais, arabe…) chez Whisper, dans cette langue-là.
"""

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import config, langues

# Les modèles sont rangés dans Jervis/modeles plutôt que dans ~/.cache.
os.environ.setdefault("HF_HOME", str(config.DOSSIER / "modeles"))

# Donner un exemple à Whisper lui apprend à écrire son nom (« Jervis », et pas « Gervais » ou « service »).
INDICE = f"Salut {config.PRENOM}, ouvre Cursor. {config.PRENOM}, j'ai rendez-vous avec qui demain ?"
_INDICES = {"en": "Hey {p}, open Cursor. {p}, what's on my calendar tomorrow?",
            "zh": "{p}，打开音乐。{p}，我明天有什么安排？", "ka": "{p}, ჩართე მუსიკა. {p}, ხვალ რა მაქვს?",
            "es": "Hola {p}, abre Cursor. {p}, ¿qué tengo mañana?", "de": "Hallo {p}, öffne Cursor. {p}, was habe ich morgen?",
            "ru": "Привет, {p}, открой Cursor. {p}, что у меня завтра?"}


def indice(langue: str) -> str:
    """L'exemple soufflé à Whisper, dans la langue qu'il va écrire (un exemple en français le pousserait à traduire)."""
    if langue == "fr":
        return INDICE
    return _INDICES[langue].format(p=config.PRENOM) if langue in _INDICES else f"{config.PRENOM}."

_moteur = config.STT
_parakeet = None
_avec_nom = None   # réglage de Parakeet qui « pousse » son nom
NOM = re.compile(config.NOM, re.IGNORECASE)

# MLX (depuis la 0.32) range ses calculs dans des « streams » propres à chaque fil d'exécution : un modèle chargé
# dans un fil plante dans un autre (« There is no Stream(cpu, 1) in current thread »). Tout ce qui touche MLX passe
# donc par ce fil unique, quel que soit le fil qui demande la transcription.
_FIL_MLX = "stt-mlx"
_fil = ThreadPoolExecutor(max_workers=1, thread_name_prefix=_FIL_MLX)


def _sur_le_fil_mlx(fonction, *args):
    if threading.current_thread().name.startswith(_FIL_MLX):  # déjà dessus (prechauffer → transcrire)
        return fonction(*args)
    return _fil.submit(fonction, *args).result()


# Parakeet devine la langue tout seul, et sur trois mots il se trompe : un « passe au mail » devient
# « Skip the mail. ». Ces petits mots-là ne laissent pas de doute, et un seul mot français suffit à l'innocenter.
_ANGLAIS = frozenset("the this that these those you your my is are was it it's i'm what where when how why who "
                     "please skip okay yes yeah of to and with for from do does did don't can could would will "
                     "have has not just like know want need let's get go thanks thank sorry".split())
_FRANCAIS = frozenset("le la les un une des de du et est je tu il elle on nous vous pas ça que qui en au aux mais "
                      "pour ce cette mon ma mes ton ta tes oui non ouais merci bonjour salut d'accord".split())


def semble_anglais(texte: str) -> bool:
    """Deux ou trois mots écrits en anglais, sans son nom : sûrement du français mal entendu. (Un seul mot,
    « Okay », veut dire la même chose ; à partir de quatre, c'est plutôt une vidéo, que __main__ ignore.)"""
    mots = re.findall(r"[\w'’]+", texte.casefold())
    if not 2 <= len(mots) <= 3 or NOM.search(texte) or re.search(r"[àâçéèêëîïôùûœ]", texte.casefold()):
        return False
    return any(m in _ANGLAIS for m in mots) and not any(m in _FRANCAIS for m in mots)


def sans_inconnus(texte: str) -> str:
    """Parakeet écrit « <unk> » pour un son qu'il ne sait pas écrire. Un ou deux dans une phrase : on les
    retire. Une rafale (du bruit, de la musique) : il n'y a rien à comprendre, la phrase est vide."""
    inconnus = texte.count("<unk>")
    if not inconnus:
        return texte.strip()
    reste = re.sub(r"\s+", " ", texte.replace("<unk>", " ")).strip()
    return "" if inconnus >= 3 or inconnus >= len(re.findall(r"\w+", reste)) else reste


def _whisper_en_cache() -> bool:
    """Whisper est-il déjà sur le disque ? (On ne télécharge pas 1,6 Go pour un deuxième avis.)"""
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(config.WHISPER_MODEL, "config.json"), str)
    except Exception:
        return False


def _deuxieme_avis(audio_int16: np.ndarray, texte: str, langue: str = "fr") -> str:
    """Parakeet a écrit de l'anglais pour une phrase courte : Whisper, à qui on impose le français, la réécoute.
    Seulement pour une phrase en français : si tu lui parles aussi anglais, c'est la langue reconnue qui décide."""
    if langue != "fr" or not config.DEUXIEME_AVIS or not semble_anglais(texte) or not _whisper_en_cache():
        return texte
    try:
        return _transcrire_whisper(audio_int16, False, "fr") or texte
    except Exception:
        return texte


def _flottant(audio_int16: np.ndarray) -> np.ndarray:
    """En float32, entre -1 et 1. Une phrase restée très basse (chuchotée de loin) est remontée avant d'être
    transcrite : ses pics à 0,5 (×8 au plus). Une voix normale n'est pas touchée."""
    x = audio_int16.astype(np.float32) / 32768.0
    pic = float(np.abs(x).max()) if len(x) else 0.0
    if config.CHUCHOTEMENT and PIC_BAS > pic > 1e-4:
        x *= min(0.5 / pic, 8.0)
    return x


PIC_BAS = 0.25   # en dessous, la phrase est remontée


def _fin_du_caractere(resultat, position: int) -> float:
    """L'instant (en s) où Parakeet a fini de prononcer le caractère `position` de son texte."""
    texte = ""
    for jeton in resultat.tokens:
        texte += jeton.text
        if len(texte.lstrip()) >= position:
            return jeton.end
    return resultat.tokens[-1].end


def _transcrire_parakeet(audio_int16: np.ndarray, nom: bool, langue: str = config.LANGUE) -> str:
    import mlx.core as mx
    from parakeet_mlx import boost, from_pretrained
    from parakeet_mlx.audio import get_logmel
    from parakeet_mlx.parakeet import Beam, DecodingConfig

    global _parakeet, _avec_nom
    if _parakeet is None:
        _parakeet = from_pretrained(config.PARAKEET_MODEL)
        # Pour Jarvis, souffler « Jervis » marche mieux (testé : « Jarvis vu, stop » au lieu de « Jervis, stop »).
        mot = "Jervis" if config.PRENOM.casefold() in {"jervis", "jarvis"} else config.PRENOM
        mots = {mot: config.BOOST_NOM, mot.lower(): config.BOOST_NOM}
        _avec_nom = DecodingConfig(decoding=Beam(
            beam_size=4, booster=boost.lift(boost.words(mots), _parakeet.vocabulary)))
    if len(audio_int16) < config.SAMPLE_RATE // 10:
        return ""
    # En float32 : get_logmel ne sait pas faire sa FFT en bfloat16 (le modèle, lui, reste en bfloat16).
    mel = get_logmel(mx.array(_flottant(audio_int16)), _parakeet.preprocessor_config)
    simple = _parakeet.generate(mel)[0]
    if not nom or NOM.search(simple.text) or config.BOOST_NOM <= 0:
        return _deuxieme_avis(audio_int16, sans_inconnus(simple.text), langue)
    # Parakeet a raté son nom (pour « Jervis », il entend « J'ai vu », « Jeremy »…). Deuxième écoute en lui soufflant
    # le mot ; mais cette recherche en faisceau rogne parfois la fin des phrases (« Non, annu »), alors on
    # garde son début jusqu'au nom, et la suite vient de la première écoute, recollée grâce aux horodatages.
    souffle = _parakeet.generate(mel, decoding_config=_avec_nom)[0]
    m = NOM.search(souffle.text)
    if not m:
        return _deuxieme_avis(audio_int16, sans_inconnus(simple.text), langue)
    t = _fin_du_caractere(souffle, m.end())
    suite = [j.text for j in simple.tokens if (j.start + j.end) / 2 > t]
    while suite and suite[0][:1].isalnum():  # la fin du mot mal compris à la place du nom (« J'ai v|u »)
        suite.pop(0)
    return sans_inconnus(souffle.text[:m.end()] + "".join(suite))


def _transcrire_whisper(audio_int16: np.ndarray, avec_indice: bool, langue: str | None = None) -> str:
    import mlx_whisper  # import tardif : le chargement prend ~1 s

    langue = langue or config.LANGUE
    exemple = indice(langue)
    resultat = mlx_whisper.transcribe(
        _flottant(audio_int16),
        path_or_hf_repo=config.WHISPER_MODEL,
        language=langue,
        initial_prompt=exemple if avec_indice else None,
        condition_on_previous_text=False,
        temperature=0.0,  # plus rapide, et évite les inventions
    )
    texte = resultat["text"].strip()
    return "" if texte.strip(" .。") == exemple.strip(" .。") else texte  # il recopie parfois l'exemple


def choisir_langue(probas: dict[str, float]) -> str | None:
    """Parmi les langues que Whisper croit entendre, la plus probable de celles que tu parles."""
    tiennes = {code: p for code, p in probas.items() if langues.parle(code)}
    return max(tiennes, key=tiennes.get) if tiennes else None


def _langue_entendue(audio_int16: np.ndarray) -> str | None:
    """La langue de cette phrase, reconnue par Whisper sur le Mac (une passe de son encodeur, ~0,2 s).
    None si Whisper n'est pas encore là (il se télécharge au démarrage quand tu parles plusieurs langues)."""
    if len(audio_int16) < config.SAMPLE_RATE // 4 or not _whisper_en_cache():
        return None
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.transcribe import ModelHolder

    modele = ModelHolder.get_model(config.WHISPER_MODEL, mx.float16)
    mel = log_mel_spectrogram(_flottant(audio_int16), n_mels=modele.dims.n_mels, padding=N_SAMPLES)
    _, probas = modele.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))
    return choisir_langue(probas)


def transcrire(audio_int16: np.ndarray, nom: bool = False) -> str:
    """`nom=True` quand son nom compte (veille, coupure de parole) : on aide le moteur à reconnaître son nom.
    Pas pendant qu'il parle sans anti-écho : il finirait par l'« entendre » dans sa propre voix."""
    return _sur_le_fil_mlx(_transcrire, audio_int16, nom)


def langue_de(audio_int16: np.ndarray) -> str:
    """Dans quelle langue tu viens de parler : ta langue principale, sauf si tu lui en parles plusieurs."""
    if not langues.plusieurs():
        return config.LANGUE
    try:
        return _langue_entendue(audio_int16) or config.LANGUE
    except Exception:  # Whisper absent ou en panne : on fait comme avant, dans ta langue principale
        return config.LANGUE


def _transcrire(audio_int16: np.ndarray, nom: bool) -> str:
    langue = langues.derniere = langue_de(audio_int16)  # ses « Voyons… » suivront
    if _moteur == "parakeet" and langue in langues.PARAKEET:
        return _transcrire_parakeet(audio_int16, nom, langue)
    return _transcrire_whisper(audio_int16, nom, langue)


def nom_moteur() -> str:
    return "Parakeet" if _moteur == "parakeet" else "Whisper"


def prechauffer() -> None:
    """Charge le modèle en mémoire (et le télécharge au premier lancement : ~1,2 Go pour Parakeet).
    Si Parakeet ne se charge pas, on passe à Whisper."""
    _sur_le_fil_mlx(_prechauffer)


def _prechauffer() -> None:
    global _moteur
    silence = np.zeros(config.SAMPLE_RATE, dtype=np.int16)
    if langues.plusieurs() or config.LANGUE not in langues.PARAKEET:
        # Il faut Whisper pour reconnaître la langue (ou pour l'écrire) : il se charge ici, et se télécharge
        # la première fois (1,6 Go), plutôt qu'au milieu de ta première phrase.
        try:
            _transcrire_whisper(silence, False, config.LANGUE)
        except Exception as e:
            print(f"   ⚠️  Whisper indisponible ({type(e).__name__}: {e}) : il t'entendra dans ta langue principale.",
                  flush=True)
    if _moteur == "parakeet" and config.LANGUE not in langues.PARAKEET and not langues.plusieurs():
        _moteur = "whisper"  # ta langue, Parakeet ne la connaît pas : Whisper fera tout
    if _moteur == "parakeet":
        try:
            transcrire(silence)
            return
        except Exception as e:
            print(f"   ⚠️  Parakeet indisponible ({type(e).__name__}: {e}) : je passe à Whisper.", flush=True)
            _moteur = "whisper"
    transcrire(silence)


# --- Les longs enregistrements (réunions, messages vocaux), dans toutes les langues ----------------------

# Ce que Whisper « entend » dans un blanc ou une musique : des génériques de vidéos, dans toutes les langues.
HALLUCINATIONS = re.compile(r"amara\.org|sous-titr|merci d'avoir regardé|abonnez-vous|thanks? for watching|"
                            r"subtitles? by|please subscribe|untertitel|продолжение следует|字幕", re.IGNORECASE)


def moteur_long() -> str:
    """Le moteur des longs enregistrements : Whisper s'il est là (99 langues, la langue de chaque morceau),
    sinon Parakeet (25 langues européennes). JERVIS_REUNION_STT impose l'un des deux."""
    if config.REUNION_STT in {"whisper", "parakeet"}:
        return config.REUNION_STT
    return "whisper" if _whisper_en_cache() else "parakeet"


def transcrire_morceau(audio_int16: np.ndarray, langue: str | None = None) -> tuple[str, str]:
    """Un morceau de réunion ou un message vocal (jusqu'à ~30 s) : (texte, langue détectée, « fr », « en »…,
    « » si le moteur ne la dit pas). `langue` = None : il la devine à chaque morceau (une réunion en deux langues
    reste juste). Un blanc, une musique : ("", "")."""
    return _sur_le_fil_mlx(_transcrire_morceau, audio_int16, langue)


def _transcrire_morceau(audio_int16: np.ndarray, langue: str | None) -> tuple[str, str]:
    if len(audio_int16) < config.SAMPLE_RATE // 2:
        return "", ""
    if moteur_long() == "whisper":
        import mlx_whisper

        resultat = mlx_whisper.transcribe(_flottant(audio_int16), path_or_hf_repo=config.WHISPER_MODEL,
                                          language=langue, condition_on_previous_text=False, temperature=0.0)
        texte, langue_vue = resultat.get("text", "").strip(), str(resultat.get("language") or langue or "")
    else:
        texte, langue_vue = _transcrire_parakeet(audio_int16, False), langue or ""
    if not texte or HALLUCINATIONS.search(texte):
        return "", ""
    return texte, langue_vue
