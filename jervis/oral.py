"""Écrit un texte comme on le dit : « 14 h 30 » → « quatorze heures trente », « 85 % » → « quatre-vingt-cinq pour cent ».

La voix locale (voix_locale.py) ne sait lire que des mots : un chiffre, un symbole ou un émoji lui fait dire
n'importe quoi (« Il est 14 h 30 » sortait « Idea a penby age »). La voix Microsoft, elle, fait ce travail
toute seule : ce module ne sert donc qu'à la voix locale.

L'ordre des règles compte : le téléphone avant les milliers, l'heure avant les unités, les décimales avant
les nombres entiers.
"""

from __future__ import annotations

import re
import unicodedata

_PETITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze",
           "treize", "quatorze", "quinze", "seize"]
_DIZAINES = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
         "novembre", "décembre"]


def _moins_de_cent(n: int) -> str:
    if n < 17:
        return _PETITS[n]
    if n < 20:
        return "dix-" + _PETITS[n - 10]
    if n < 70:
        dizaine, unite = n - n % 10, n % 10
        return _DIZAINES[dizaine] + ("" if not unite else " et un" if unite == 1 else "-" + _PETITS[unite])
    if n < 80:
        return "soixante et onze" if n == 71 else "soixante-" + _moins_de_cent(n - 60)
    return "quatre-vingts" if n == 80 else "quatre-vingt-" + _moins_de_cent(n - 80)


def _moins_de_mille(n: int) -> str:
    centaines, reste = divmod(n, 100)
    if not centaines:
        return _moins_de_cent(reste)
    debut = "cent" if centaines == 1 else _PETITS[centaines] + " cent"
    if not reste:
        return debut + ("s" if centaines > 1 else "")
    return f"{debut} {_moins_de_cent(reste)}"


def nombre(n: int, feminin: bool = False) -> str:
    """Un nombre entier en toutes lettres. `feminin` : « une », « vingt et une » (heures, minutes…)."""
    if n < 0:
        return "moins " + nombre(-n, feminin)
    if n >= 10 ** 12:
        return " ".join(_PETITS[int(c)] for c in str(n))
    morceaux = []
    for valeur, nom in ((10 ** 9, "milliard"), (10 ** 6, "million")):
        combien, n = divmod(n, valeur)
        if combien:
            morceaux.append(f"{_moins_de_mille(combien)} {nom}{'s' if combien > 1 else ''}")
    milliers, n = divmod(n, 1000)
    if milliers == 1:
        morceaux.append("mille")
    elif milliers:  # « quatre-vingt mille », « deux cent mille » : pas de s devant mille
        morceaux.append(re.sub(r"(cent|vingt)s$", r"\1", _moins_de_mille(milliers)) + " mille")
    if n or not morceaux:
        morceaux.append(_moins_de_mille(n))
    mots = " ".join(morceaux)
    return re.sub(r"\bun$", "une", mots) if feminin else mots


def _chiffres(texte: str) -> str:
    """Chiffre par chiffre : « 007 » → « zéro zéro sept »."""
    return " ".join(_PETITS[int(c)] for c in texte)


def _entier(texte: str, feminin: bool = False) -> str:
    """Une suite de chiffres : un nombre, ou chiffre par chiffre si elle commence par zéro (un code)."""
    texte = re.sub(r"\D", "", texte)
    if len(texte) > 1 and texte[0] == "0":
        return _chiffres(texte)
    return nombre(int(texte), feminin)


def ordinal(n: int, feminin: bool = False) -> str:
    """1 → premier (première), 2 → deuxième, 21 → vingt et unième, 80 → quatre-vingtième."""
    if n == 1:
        return "première" if feminin else "premier"
    mots = nombre(n)
    if mots.endswith("cinq"):
        return mots + "uième"
    if mots.endswith("neuf"):
        return mots[:-1] + "vième"
    if mots.endswith(("cents", "vingts")):
        mots = mots[:-1]
    return (mots[:-1] if mots.endswith("e") else mots) + "ième"


# Un nombre comme on l'écrit : « 1 250 », « 1 250,5 », « 49,99 », « 18.2 », « 12 ».
_ESPACES = "   "
_NB = rf"\d{{1,3}}(?:[{_ESPACES}]\d{{3}})+(?:,\d+)?|\d+(?:[.,]\d+)?"


def _dire_nombre(texte: str, feminin: bool = False) -> str:
    """« 1 250 » → mille deux cent cinquante ; « 2,5 » → deux virgule cinq ; « 18.2 » → dix-huit point deux."""
    texte = re.sub(rf"[{_ESPACES}]", "", texte)
    for signe, mot in ((",", "virgule"), (".", "point")):
        if signe in texte:
            entier, _, decimales = texte.partition(signe)
            zeros = len(decimales) - len(decimales.lstrip("0"))
            suite = decimales[zeros:]
            apres = ["zéro"] * zeros + ([nombre(int(suite))] if suite and len(suite) <= 3 else [_chiffres(suite)] if suite else [])
            return f"{_entier(entier)} {mot} {' '.join(apres)}"
    return _entier(texte, feminin)


def _valeur(texte: str) -> float:
    return float(re.sub(rf"[{_ESPACES}]", "", texte).replace(",", "."))


def _pluriel(texte: str, singulier: str, pluriel: str | None = None) -> str:
    return singulier if _valeur(texte) < 2 else (pluriel or singulier + "s")


# Unité → (singulier, pluriel, féminin). Du plus long au plus court, pour que « km/h » passe avant « km ».
_UNITES = {
    "km/h": ("kilomètre heure", "kilomètres heure", False), "km²": ("kilomètre carré", "kilomètres carrés", False),
    "m²": ("mètre carré", "mètres carrés", False), "m2": ("mètre carré", "mètres carrés", False),
    "km": ("kilomètre", None, False), "cm": ("centimètre", None, False), "mm": ("millimètre", None, False),
    "m": ("mètre", None, False), "kg": ("kilo", None, False), "mg": ("milligramme", None, False),
    "g": ("gramme", None, False), "cl": ("centilitre", None, False), "ml": ("millilitre", None, False),
    "L": ("litre", None, False), "l": ("litre", None, False),
    "Go": ("giga", None, False), "Mo": ("méga", None, False), "Ko": ("kilo-octet", None, False),
    "ko": ("kilo-octet", None, False), "To": ("téra", None, False), "GB": ("giga", None, False),
    "MB": ("méga", None, False), "ms": ("milliseconde", None, True), "min": ("minute", None, True),
    "sec": ("seconde", None, True), "s": ("seconde", None, True), "kWh": ("kilowattheure", None, False),
    "kW": ("kilowatt", None, False), "W": ("watt", None, False), "GHz": ("gigahertz", "gigahertz", False),
    "MHz": ("mégahertz", "mégahertz", False), "kHz": ("kilohertz", "kilohertz", False), "Hz": ("hertz", "hertz", False),
    "px": ("pixel", None, False),
}
_RE_UNITES = re.compile(rf"({_NB})\s?({'|'.join(re.escape(u) for u in sorted(_UNITES, key=len, reverse=True))})(?![\w/])")

_MONNAIES = {"€": ("euro", "centime"), "euro": ("euro", "centime"), "euros": ("euro", "centime"),
             "$": ("dollar", "cent"), "£": ("livre", "penny")}
# Les mots féminins les plus courants après un nombre : « 1 fois » → « une fois », « 21 pages » → « vingt et une pages ».
_FEMININS = (r"fois|heures?|minutes?|secondes?|semaines?|années?|journées?|personnes?|pages?|lignes?|tâches?|photos?|"
             r"vidéos?|chansons?|notes?|réunions?|alarmes?|réponses?|questions?|fenêtres?|applis?|applications?|"
             r"notifications?|pièces? jointes?|livres?|tonnes?")

_ABREVIATIONS = [
    (r"\bM\.\s+(?=[A-ZÀ-Ý])", "monsieur "), (r"\bMM\.\s+(?=[A-ZÀ-Ý])", "messieurs "),
    (r"\bMme\b\.?", "madame"), (r"\bMlle\b\.?", "mademoiselle"), (r"\bDr\b\.?(?=\s+[A-ZÀ-Ý])", "docteur"),
    (r"\bPr\b\.?(?=\s+[A-ZÀ-Ý])", "professeur"), (r"\betc\b\.(?=\s+[a-zà-ÿ])", "et cetera"),
    (r"\betc\b\.?(?=\s*[,;)])", "et cetera"), (r"\betc\.{3}", "et cetera."), (r"\betc…", "et cetera."),
    (r"\betc\.", "et cetera."), (r"\b[Cc]f\.\s", "voir "), (r"\bp\.\s?ex\.", "par exemple"),
    (r"\bc\.-à-d\.", "c'est-à-dire"),
]


def abreviations(texte: str) -> str:
    """« M. Dupont » → « monsieur Dupont », « etc. » → « et cetera ». Fait avant de couper le texte en phrases :
    sinon le point de « M. » passerait pour une fin de phrase. Utile à toutes les voix."""
    for motif, mot in _ABREVIATIONS:
        texte = re.sub(motif, mot, texte)
    return texte


def _heure(m: re.Match) -> str:
    heures, minutes = int(m.group(1)), int(m.group(2) or 0)
    dit = f"{nombre(heures, feminin=True)} heure{'s' if heures > 1 else ''}"
    return f"{dit} {nombre(minutes)}" if minutes else dit


def _monnaie(montant: str, signe: str) -> str:
    nom, centime = _MONNAIES[signe.lower()]
    feminin = nom == "livre"
    brut = re.sub(rf"[{_ESPACES}]", "", montant)
    entier, _, decimales = brut.replace(".", ",").partition(",")
    if not decimales:
        return f"{_entier(entier, feminin)} {nom}{'s' if int(entier) > 1 else ''}"
    centimes = int((decimales + "0")[:2])
    if not int(entier):  # « 0,50 € » → cinquante centimes
        return f"{nombre(centimes)} {centime}{'s' if centimes > 1 else ''}"
    dit = f"{_entier(entier, feminin)} {nom}{'s' if int(entier) > 1 else ''}"
    return f"{dit} {nombre(centimes)}" if centimes else dit


def _unite(m: re.Match) -> str:
    singulier, pluriel, feminin = _UNITES[m.group(2)]
    return f"{_dire_nombre(m.group(1), feminin)} {_pluriel(m.group(1), singulier, pluriel)}"


def _date(jour: str, mois: str, annee: str | None = None) -> str:
    if not 1 <= int(mois) <= 12 or not 1 <= int(jour) <= 31:
        return f"{jour} sur {mois}" + (f" sur {annee}" if annee else "")
    return f"{'premier' if int(jour) == 1 else int(jour)} {_MOIS[int(mois) - 1]}" + (f" {annee}" if annee else "")


def _telephone(m: re.Match) -> str:
    paires = re.findall(r"\d\d", m.group(0))
    return ", ".join(_chiffres(p) if p[0] == "0" else nombre(int(p)) for p in paires)


def _sans_symboles(texte: str) -> str:
    """Retire les émojis et les signes qui ne se disent pas (la voix locale les lirait de travers)."""
    garde = []
    for c in texte:
        categorie = unicodedata.category(c)
        if c in "‍︎️" or categorie in {"So", "Sk", "Cs", "Co", "Cn", "Cf", "Sc"}:
            garde.append(" ")
        elif categorie == "Sm":
            garde.append(", " if c in "→⇒←↔" else " ")
        else:
            garde.append(c)
    return "".join(garde)


def en_mots(texte: str) -> str:
    """Le texte tel qu'on le prononce : plus un seul chiffre, symbole ou émoji."""
    t = unicodedata.normalize("NFC", texte)
    t = abreviations(t)
    t = re.sub(r"\bn°\s*", "numéro ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:RDV|rdv)\b", "rendez-vous", t)
    t = re.sub(r"\b(?:SVP|svp)\b", "s'il vous plaît", t)
    t = re.sub(r"\bstp\b", "s'il te plaît", t, flags=re.IGNORECASE)

    # Téléphone : « 06 12 34 56 78 » → paire par paire.
    t = re.sub(r"(?<![\d.,])0\d(?:[ . -]?\d{2}){4}(?![\d])", _telephone, t)
    # Dates : 25/11/2026, 2026-11-25, « le 25/11 », « le 1 mars ».
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", lambda m: _date(m.group(1), m.group(2), m.group(3)), t)
    t = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: _date(m.group(3), m.group(2), m.group(1)), t)
    t = re.sub(r"\b(le|du|au|dès le|jusqu'au)\s+(\d{1,2})/(\d{1,2})\b",
               lambda m: f"{m.group(1)} {_date(m.group(2), m.group(3))}", t)
    t = re.sub(rf"\b1(?:er)?\s+(?=(?:{'|'.join(_MOIS)})\b)", "premier ", t)
    # « 24h/24 », « 7j/7 ».
    t = re.sub(r"\b(\d+)\s?h\s?/\s?(\d+)\b", lambda m: f"{nombre(int(m.group(1)))} heures sur {nombre(int(m.group(2)))}", t)
    t = re.sub(r"\b(\d+)\s?j\s?/\s?(\d+)\b", lambda m: f"{nombre(int(m.group(1)))} jours sur {nombre(int(m.group(2)))}", t)
    # Heures : « 14 h 30 », « 14h30 », « 9 h », « 18:45 ».
    t = re.sub(r"\b(\d{1,2})\s?[hH](?:\s?(\d{2})\b|\b)", _heure, t)
    t = re.sub(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])", _heure, t)
    # « -3° », « +8 % » : le signe se dit avant le nombre.
    t = re.sub(r"(?<![\w,.])[-−](?=\d)", "moins ", t)
    t = re.sub(r"(?<![\w,.])\+\s?(?=\d)", "plus ", t)

    # Argent : « 49,99 € », « 5 M€ », « $20 ».
    t = re.sub(rf"({_NB})\s?k€", lambda m: f"{nombre(round(_valeur(m.group(1)) * 1000))} euros", t)
    t = re.sub(rf"({_NB})\s?M€", lambda m: f"{_dire_nombre(m.group(1))} {_pluriel(m.group(1), 'million')} d'euros", t)
    t = re.sub(rf"({_NB})\s?(€|£|\$|euros?\b)", lambda m: _monnaie(m.group(1), m.group(2)), t)
    t = re.sub(rf"([$£€])\s?({_NB})", lambda m: _monnaie(m.group(2), m.group(1)), t)
    # Pour cent, degrés, unités.
    t = re.sub(rf"({_NB})\s?%", lambda m: f"{_dire_nombre(m.group(1))} pour cent", t)
    t = t.replace("%", " pour cent ")
    t = re.sub(rf"({_NB})\s?°\s?F\b", lambda m: f"{_dire_nombre(m.group(1))} {_pluriel(m.group(1), 'degré')} Fahrenheit", t)
    t = re.sub(rf"({_NB})\s?°(?:\s?C\b)?", lambda m: f"{_dire_nombre(m.group(1))} {_pluriel(m.group(1), 'degré')}", t)
    t = _RE_UNITES.sub(_unite, t)

    # Premier, deuxième…
    t = re.sub(r"\b1(?:re|ère)s?\b", "première", t)
    t = re.sub(r"\b1ers?\b", "premier", t)
    t = re.sub(r"\b(\d+)(?:èmes?|emes?|es?|nde?s?)\b", lambda m: ordinal(int(m.group(1))), t)

    # Calcul et signes.
    calcul = "=" in t
    t = re.sub(r"(?<=\d)\s?[x×*]\s?(?=\d)", " fois ", t)
    t = re.sub(r"(?<=\d)\s?÷\s?(?=\d)", " divisé par ", t)
    t = re.sub(r"\s?=\s?", " égale ", t)
    t = re.sub(r"\+\s?(?=\d)", " plus ", t)
    t = re.sub(r"(?<=\d)\s?/\s?(?=\d)", " sur ", t)
    t = re.sub(r"(?<=\d)\s[-–—]\s(?=\d)", " moins " if calcul else " à ", t)
    t = re.sub(r"(?<=\d)[-–](?=\d)", " à ", t)
    t = re.sub(r"[~≈]\s?(?=\d)", "environ ", t)
    t = re.sub(r"\s&\s", " et ", t)
    t = t.replace("@", " arobase ")

    # Les nombres qui restent : « 1 fois », « 1 250 », « 3.12.1 », « 2,5 », « 2026 ».
    t = re.sub(rf"({_NB})(\s+(?:{_FEMININS})\b)", lambda m: _dire_nombre(m.group(1), feminin=True) + m.group(2), t)
    t = re.sub(r"\d+(?:\.\d+){2,}", lambda m: " point ".join(_entier(p) for p in m.group(0).split(".")), t)
    t = re.sub(_NB, lambda m: f" {_dire_nombre(m.group(0))} ", t)

    t = _sans_symboles(t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([,.!?…;:])", r"\1", t)
    return re.sub(r",(?:\s*,)+", ",", t).strip()
