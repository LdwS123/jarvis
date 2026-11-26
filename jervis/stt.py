"""Transcription de ta voix en texte, en local sur la puce Apple (MLX).

Deux moteurs :
- Parakeet v3 (NVIDIA, par défaut) : ~90 ms par phrase, 25 langues dont le français, détecte la langue seul
  (+ ~150 ms quand on cherche son nom et qu'il ne l'a pas reconnu du premier coup) ;
- Whisper large-v3-turbo : ~350 ms par phrase, en secours ou avec JERVIS_STT=whisper. Il sert aussi de
  deuxième avis, en français imposé, quand Parakeet écrit en anglais une phrase de quelques mots.
"""

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import config

# Les modèles sont rangés dans Jervis/modeles plutôt que dans ~/.cache.
os.environ.setdefault("HF_HOME", str(config.DOSSIER / "modeles"))

# Donner un exemple à Whisper lui apprend à écrire son nom (« Jervis », et pas « Gervais » ou « service »).
INDICE = f"Salut {config.PRENOM}, ouvre Cursor. {config.PRENOM}, j'ai rendez-vous avec qui demain ?"

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


def _deuxieme_avis(audio_int16: np.ndarray, texte: str) -> str:
    """Parakeet a écrit de l'anglais pour une phrase courte : Whisper, à qui on impose le français, la réécoute."""
    if not config.DEUXIEME_AVIS or not semble_anglais(texte) or not _whisper_en_cache():
        return texte
    try:
        return _transcrire_whisper(audio_int16, False) or texte
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


def _transcrire_parakeet(audio_int16: np.ndarray, nom: bool) -> str:
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
        return _deuxieme_avis(audio_int16, sans_inconnus(simple.text))
    # Parakeet a raté son nom (pour « Jervis », il entend « J'ai vu », « Jeremy »…). Deuxième écoute en lui soufflant
    # le mot ; mais cette recherche en faisceau rogne parfois la fin des phrases (« Non, annu »), alors on
    # garde son début jusqu'au nom, et la suite vient de la première écoute, recollée grâce aux horodatages.
    souffle = _parakeet.generate(mel, decoding_config=_avec_nom)[0]
    m = NOM.search(souffle.text)
    if not m:
        return _deuxieme_avis(audio_int16, sans_inconnus(simple.text))
    t = _fin_du_caractere(souffle, m.end())
    suite = [j.text for j in simple.tokens if (j.start + j.end) / 2 > t]
    while suite and suite[0][:1].isalnum():  # la fin du mot mal compris à la place du nom (« J'ai v|u »)
        suite.pop(0)
    return sans_inconnus(souffle.text[:m.end()] + "".join(suite))


def _transcrire_whisper(audio_int16: np.ndarray, indice: bool) -> str:
    import mlx_whisper  # import tardif : le chargement prend ~1 s

    resultat = mlx_whisper.transcribe(
        _flottant(audio_int16),
        path_or_hf_repo=config.WHISPER_MODEL,
        language=config.LANGUE,
        initial_prompt=INDICE if indice else None,
        condition_on_previous_text=False,
        temperature=0.0,  # plus rapide, et évite les inventions
    )
    texte = resultat["text"].strip()
    return "" if texte.strip(" .") == INDICE.strip(" .") else texte  # il recopie parfois l'exemple


def transcrire(audio_int16: np.ndarray, nom: bool = False) -> str:
    """`nom=True` quand son nom compte (veille, coupure de parole) : on aide le moteur à reconnaître son nom.
    Pas pendant qu'il parle sans anti-écho : il finirait par l'« entendre » dans sa propre voix."""
    return _sur_le_fil_mlx(_transcrire, audio_int16, nom)


def _transcrire(audio_int16: np.ndarray, nom: bool) -> str:
    if _moteur == "parakeet":
        return _transcrire_parakeet(audio_int16, nom)
    return _transcrire_whisper(audio_int16, nom)


def nom_moteur() -> str:
    return "Parakeet" if _moteur == "parakeet" else "Whisper"


def prechauffer() -> None:
    """Charge le modèle en mémoire (et le télécharge au premier lancement : ~1,2 Go pour Parakeet).
    Si Parakeet ne se charge pas, on passe à Whisper."""
    _sur_le_fil_mlx(_prechauffer)


def _prechauffer() -> None:
    global _moteur
    silence = np.zeros(config.SAMPLE_RATE, dtype=np.int16)
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
