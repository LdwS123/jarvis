"""Les langues que tu parles à Steve : laquelle il entend, dans laquelle il répond, avec quelle voix.

Tout se décide sur le Mac. Pour ta voix, c'est Whisper (local) qui reconnaît la langue ; pour un texte (ce qu'il
va dire, ce que la transcription a écrit), c'est son écriture (中文, ქართული, русский…) ou ses petits mots
(« the », « und », « que »…). Rien ne part sur Internet pour ça.

JERVIS_LANGUE : ta langue principale (fr, en, zh, ka…). JERVIS_LANGUES : toutes celles que tu lui parles
(« fr,en,zh,ka », ou « toutes »). Par défaut, seulement la principale : une vidéo dans une autre langue
n'est alors jamais prise pour toi.
"""

from __future__ import annotations

import functools
import re
import subprocess
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Langue:
    code: str              # ISO 639-1, celui de Whisper
    nom: str               # en français, pour ses consignes et les réglages
    natif: str             # comme elle s'écrit chez elle, pour les réglages
    neuronale: str         # la voix Microsoft qui la parle (sa voix quand sa voix locale ne la parle pas)
    mac: tuple[str, ...]   # les voix macOS qui la parlent, de la préférée à la moins bonne
    locale: str = ""       # le modèle Pocket TTS qui la parle sur le Mac (vide : aucun)


LANGUES: dict[str, Langue] = {l.code: l for l in (
    Langue("fr", "français", "Français", "fr-FR-RemyMultilingualNeural", ("Thomas", "Amélie"), "french"),
    Langue("en", "anglais", "English", "en-US-AndrewMultilingualNeural", ("Samantha", "Daniel", "Alex"), "english"),
    Langue("zh", "chinois", "中文", "zh-CN-YunxiNeural", ("Tingting", "Meijia", "Sinji")),
    Langue("ka", "géorgien", "ქართული", "ka-GE-GiorgiNeural", ()),
    Langue("es", "espagnol", "Español", "es-ES-AlvaroNeural", ("Mónica", "Paulina", "Jorge"), "spanish"),
    Langue("de", "allemand", "Deutsch", "de-DE-ChristophNeural", ("Anna",), "german"),
    Langue("it", "italien", "Italiano", "it-IT-DiegoNeural", ("Alice", "Luca"), "italian"),
    Langue("pt", "portugais", "Português", "pt-BR-AntonioNeural", ("Luciana", "Joana"), "portuguese"),
    Langue("nl", "néerlandais", "Nederlands", "nl-NL-MaartenNeural", ("Xander", "Ellen"), "dutch"),
    Langue("ru", "russe", "Русский", "ru-RU-DmitryNeural", ("Milena", "Yuri")),
    Langue("uk", "ukrainien", "Українська", "uk-UA-OstapNeural", ("Lesya",)),
    Langue("ar", "arabe", "العربية", "ar-SA-HamedNeural", ("Majed", "Maged")),
    Langue("he", "hébreu", "עברית", "he-IL-AvriNeural", ("Carmit",)),
    Langue("ja", "japonais", "日本語", "ja-JP-KeitaNeural", ("Kyoko", "Otoya")),
    Langue("ko", "coréen", "한국어", "ko-KR-InJoonNeural", ("Yuna",)),
    Langue("hi", "hindi", "हिन्दी", "hi-IN-MadhurNeural", ("Lekha",)),
    Langue("tr", "turc", "Türkçe", "tr-TR-AhmetNeural", ("Yelda",)),
    Langue("pl", "polonais", "Polski", "pl-PL-MarekNeural", ("Zosia",)),
    Langue("el", "grec", "Ελληνικά", "el-GR-NestorasNeural", ("Melina",)),
    Langue("th", "thaï", "ไทย", "th-TH-NiwatNeural", ("Kanya",)),
    Langue("vi", "vietnamien", "Tiếng Việt", "vi-VN-NamMinhNeural", ("Linh",)),
    Langue("sv", "suédois", "Svenska", "sv-SE-MattiasNeural", ("Alva",)),
    Langue("ro", "roumain", "Română", "ro-RO-EmilNeural", ("Ioana",)),
    Langue("fa", "persan", "فارسی", "fa-IR-FaridNeural", ()),
    Langue("id", "indonésien", "Bahasa Indonesia", "id-ID-ArdiNeural", ("Damayanti",)),
)}

# Les 25 langues que Parakeet v3 (sa transcription rapide) connaît. Pour les autres, c'est Whisper.
PARAKEET = frozenset("bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk".split())

# Ce qu'il dit quand il est prêt, et ses petits « Voyons… », dans ta langue principale.
_ANNONCES = {"fr": "{p} est en ligne.", "en": "{p} is online.", "zh": "{p} 已上线。", "ka": "{p} მზადაა.",
             "es": "{p} está en línea.", "de": "{p} ist bereit.", "it": "{p} è in linea.", "pt": "{p} está online.",
             "ru": "{p} на связи.", "ja": "{p}、準備できました。"}
_PATIENCES = {"fr": "Voyons…|Alors…|Une seconde.|Je regarde.", "en": "Let's see…|One sec.|Checking.",
              "zh": "我看看。|稍等。", "ka": "ერთი წამით.|ვნახოთ…", "es": "A ver…|Un segundo.",
              "de": "Moment…|Ich schaue.", "it": "Vediamo…|Un secondo.", "pt": "Vamos ver…|Um segundo.",
              "ru": "Секунду.|Сейчас посмотрю.", "ja": "ちょっと待ってね。"}


def annonce(code: str, prenom: str) -> str:
    return _ANNONCES.get(code, _ANNONCES["fr"]).format(p=prenom)


def patiences(code: str) -> str:
    return _PATIENCES.get(code, _PATIENCES["fr"])


derniere: str | None = None  # la langue de ta dernière phrase dite à voix haute (stt la note)


def phrases_patience() -> list[str]:
    """Ses « Voyons… » dans la langue où tu viens de lui parler (aucun s'il n'en connaît pas dans cette langue)."""
    from . import config

    if derniere in (None, config.LANGUE):
        return config.PHRASES_PATIENCE
    return [p for p in _PATIENCES.get(derniere, "").split("|") if p]


def nom(code: str) -> str:
    return LANGUES[code].nom if code in LANGUES else code


def lire_liste(texte: str, principale: str) -> tuple[frozenset[str], bool]:
    """« fr, en,zh » → ({fr, en, zh}, False) ; « toutes » → (tout, True). La principale en fait toujours partie."""
    mots = {m.strip().lower() for m in re.split(r"[,;\s]+", texte) if m.strip()}
    if mots & {"toutes", "all", "auto", "*"}:
        return frozenset(LANGUES) | {principale}, True
    return frozenset(m for m in mots if re.fullmatch(r"[a-z]{2,3}", m)) | {principale}, False


# --- Les langues que tu parles (d'après les réglages) -----------------------------------------------------

def _reglees() -> tuple[frozenset[str], bool]:
    from . import config
    return lire_liste(config.LANGUES, config.LANGUE)


def parlees() -> frozenset[str]:
    return _reglees()[0]


def toutes() -> bool:
    return _reglees()[1]


def parle(code: str | None) -> bool:
    """Est-ce une langue que tu lui parles ? (« toutes » : n'importe laquelle.)"""
    return bool(code) and (toutes() or code in parlees())


def plusieurs() -> bool:
    """Tu lui parles plus d'une langue : il doit reconnaître laquelle à chaque phrase."""
    return toutes() or len(parlees()) > 1


def principale() -> str:
    from . import config
    return config.LANGUE


# --- La langue d'un texte ---------------------------------------------------------------------------------

# L'écriture suffit pour la plupart des langues : le nom Unicode de la lettre dit d'où elle vient.
_ECRITURES = (("CJK UNIFIED", "zh"), ("HIRAGANA", "ja"), ("KATAKANA", "ja"), ("HANGUL", "ko"),
              ("GEORGIAN", "ka"), ("CYRILLIC", "ru"), ("ARABIC", "ar"), ("HEBREW", "he"), ("GREEK", "el"),
              ("DEVANAGARI", "hi"), ("THAI", "th"), ("ARMENIAN", "hy"))


def _ecriture_de(c: str) -> str | None:
    nom_unicode = unicodedata.name(c, "")
    if "LATIN" in nom_unicode:
        return "latin"
    return next((code for prefixe, code in _ECRITURES if nom_unicode.startswith(prefixe)), None)


def ecriture(texte: str) -> str | None:
    """La langue que dit son écriture (« 你好 » → zh, « გამარჯობა » → ka), « latin », ou None (trop peu de lettres)."""
    comptes: dict[str, int] = {}
    for c in texte:
        if c.isalpha() and (e := _ecriture_de(c)):
            comptes[e] = comptes.get(e, 0) + 1
    if sum(comptes.values()) < 2:
        return None
    if comptes.get("ja"):  # du japonais mêle des kanji (les mêmes que le chinois) à ses kana
        comptes["ja"] += comptes.pop("zh", 0)
    code = max(comptes, key=comptes.get)
    if code == "ru" and re.search(r"[іїєґІЇЄҐ]", texte):
        return "uk"
    if code == "ar" and re.search(r"[پچژگ]", texte):
        return "fa"
    return code


# En alphabet latin, ce sont les petits mots qui trahissent la langue (un mot peut compter pour plusieurs).
_PETITS_MOTS = {
    "fr": "le la les un une des de du et est je tu il elle on nous vous pas ça que qui au aux mais pour ce cette mon ma "
          "mes ton ta oui non ouais merci bonjour salut c'est j'ai t'as d'accord avec dans sur très aussi alors "
          "quoi comment pourquoi peux veux fais",
    "en": "the this that these those you your my is are was it it's i'm what where when how why who please okay "
          "yes yeah of to and with for from do does did don't can could would will have has not just like know "
          "want need let's get thanks thank sorry hello hi there we they he she our be been",
    "es": "el los las una unos de y es está estoy yo tú usted que qué por para con como pero sí gracias hola muy "
          "también dónde cuándo cómo quiero puedes hay del al lo",
    "de": "der die das ein eine und ist bin ich du sie wir nicht ja nein danke bitte mit für auf wie was wo "
          "warum kannst kann hallo auch noch schon sehr",
    "it": "il lo gli una e è sono io tu che non sì grazie ciao per con come perché anche molto dove quando "
          "voglio puoi della",
    "pt": "o os as um uma de e é está sou eu você que não sim obrigado obrigada olá oi por para com como mas "
          "também muito onde quando quero pode",
    "nl": "de het een en is ben ik jij je wij niet ja nee dank bedankt alsjeblieft met voor op hoe wat waar "
          "waarom kun kan hallo ook nog al",
}
_PETITS_MOTS_ENS = {code: frozenset(mots.split()) for code, mots in _PETITS_MOTS.items()}
_ACCENTS = {"fr": r"[àâçéèêëîïôùûœ]", "es": r"[ñ¿¡]", "de": r"[äöüß]", "pt": r"[ãõ]"}


def detecter(texte: str) -> str | None:
    """La langue la plus probable de ce texte, ou None quand il n'y a pas de quoi trancher (« OK. », « Spotify »).
    Prudent : un doute en alphabet latin ne change pas de langue (sa voix ne passe pas à l'anglais pour rien)."""
    code = ecriture(texte)
    if code != "latin":
        return code
    mots = re.findall(r"[\w'’]+", texte.casefold().replace("’", "'"))
    if not mots:
        return None
    scores = {c: sum(m in ens for m in mots) for c, ens in _PETITS_MOTS_ENS.items()}
    for c, motif in _ACCENTS.items():
        if re.search(motif, texte.casefold()):
            scores[c] += 1
    classement = sorted(scores.items(), key=lambda s: s[1], reverse=True)
    (meilleure, score), (_, second) = classement[0], classement[1]
    if score < (1 if len(mots) <= 3 else 2) or score <= second:
        return None
    return meilleure


def autre_que_bruit(texte: str) -> bool:
    """Une phrase dans une des langues que tu lui parles (autre que le français) : ce n'est pas du bruit.
    (Les filtres de __main__ sont faits pour le français : sans ça, ton anglais passerait pour une vidéo.)"""
    code = detecter(texte)
    return code is not None and code != "fr" and parle(code)


# --- Les voix ---------------------------------------------------------------------------------------------

def voix_neuronale(code: str) -> str:
    """La voix Microsoft de cette langue : la tienne (JERVIS_VOIX_NEURONALE) pour la principale,
    JERVIS_VOIX_ZH, JERVIS_VOIX_KA… pour les autres."""
    import os
    from . import config
    if code == config.LANGUE:
        return config.VOIX_NEURONALE
    reglee = os.environ.get(f"JERVIS_VOIX_{code.upper()}") or config.reglage(f"VOIX_{code.upper()}")
    return reglee or (LANGUES[code].neuronale if code in LANGUES else "")


@functools.cache
def _voix_du_mac() -> dict[str, str]:
    """Les voix installées sur ce Mac : nom → langue (« Tingting » → « zh_CN »)."""
    try:
        sortie = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return {m[1].strip(): m[2] for m in re.finditer(r"^(.+?)\s+([a-z]{2,3}_[A-Z0-9]{2,4})\s+#", sortie, re.M)}


def voix_mac(code: str) -> str | None:
    """La voix macOS de secours pour cette langue, ou None si ce Mac n'en a aucune."""
    from . import config
    if code == config.LANGUE:
        return config.VOIX
    installees = _voix_du_mac()
    preferees = LANGUES[code].mac if code in LANGUES else ()
    return next((v for v in preferees if v in installees),
                next((v for v, loc in installees.items() if loc.split("_")[0] == code and "(" not in v), None))


def modele_local(code: str) -> str:
    """Le modèle Pocket TTS de cette langue (« french »), ou « » s'il n'y en a pas."""
    return LANGUES[code].locale if code in LANGUES else ""


def consigne() -> str:
    """Ce que Claude doit savoir : dans quelle langue répondre. Rien quand tu ne lui parles que français."""
    principale_ = principale()
    if not plusieurs():
        if principale_ == "fr":
            return ""
        n = nom(principale_)
        return (f"\nLangue : il te parle {n}. Réponds toujours en {n}, même si ces consignes sont en français ; "
                f"garde le même ton, court et oral.\n")
    if toutes():
        liste = "n'importe quelle langue"
    else:
        liste = ", ".join(nom(c) for c in sorted(parlees(), key=lambda c: (c != principale_, c)))
    return (f"\nLangues : il te parle {liste}. Réponds toujours dans la langue de sa dernière phrase (chinois → "
            f"chinois en caractères simplifiés, géorgien → géorgien en alphabet géorgien, anglais → anglais…), "
            f"avec le même ton court et oral ; ta voix change de langue avec toi. Une phrase dans une de ces langues "
            f"vient de lui, pas d'une vidéo. S'il te demande de changer de langue, fais-le jusqu'à ce qu'il "
            f"t'en redemande une autre.\n")
