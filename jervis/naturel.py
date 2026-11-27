"""Pour qu'il parle comme quelqu'un, pas comme un service client.

Deux filets de sécurité, en plus de ses consignes (le modèle les oublie parfois) :
- nettoyer() : chaque phrase de sa réponse, juste avant d'aller à la voix. Enlève les « Bien sûr ! »,
  les « N'hésite pas si… », les « Ta phrase s'est coupée » ;
- corriger_transcription() : ta phrase, juste après la reconnaissance vocale. Corrige les mots qu'elle
  écorche à coup sûr (« load code » = Claude Code). Les cas ambigus (« Watt » = WhatsApp ?) sont dans
  ses consignes : lui sait deviner avec le contexte, une regex non. Les lettres épelées (« T A double L »)
  sont recollées en un mot (decoder_epellation) ;
- hors_alphabet() : le bruit d'une vidéo en russe, en arabe… n'est jamais pour lui.
"""

import re
import unicodedata

_I = re.IGNORECASE

# Ouvertures creuses, toujours enlevées : « Bien sûr ! Je lance Spotify. » → « Je lance Spotify. »
_CLICHE = (r"bien sûr|bien entendu|avec plaisir|(?:très |excellente |bonne )?(?:excellente |bonne )question"
           r"|absolument|certainement|aucun (?:souci|problème)|volontiers")
# Ouvertures qui passent seules (« Parfait. » confirme une commande) mais pas en tête d'une phrase plus longue.
_TIC = r"parfait|pas de (?:souci|soucis|problème)|je comprends|je vois|entendu|super|génial|très bien"
_OUI = r"oui|ouais|ok|okay|d'accord"

_OUVERTURE_CLICHE = re.compile(rf"^(?:(?P<oui>{_OUI})\s*[,!.]?\s*)?(?:{_CLICHE})\s*(?:[,!.…]+\s*|$)", _I)
_OUVERTURE_TIC = re.compile(rf"^(?:{_TIC})\s*[,!]\s*(?=\w)", _I)
# Après un « (interrompu) » : pas d'excuses, il répond à la nouvelle phrase.
_EXCUSES = re.compile(r"^(?:oh\s*,?\s*)?(?:pardon|désolée?|excuse-moi|oups)(?:\s+(?:pour ça|de t'avoir coupé))?"
                      r"\s*[,!.…]+\s*", _I)

# Phrases de remplissage en fin de réponse, enlevées (sauf si c'est sa seule réponse).
_REMPLISSAGE = re.compile(
    r"^(?:et\s+)?(?:"
    r"n'hésite (?:pas|surtout pas)\b"
    r"|(?:dis|fais)-moi (?:si (?:tu as|t'as) besoin|si je peux|ce qu'il te faut|ce que tu en penses si)"
    r"|(?:est-ce que )?(?:tu veux|tu voudrais|veux-tu) (?:que je (?:fasse|regarde|t'aide|creuse|vérifie) )?"
    r"(?:quelque chose|autre chose|d'autres?|plus de détails|que je (?:t'en dise|développe))\b"
    r"|(?:est-ce que )?(?:tu veux|tu voudrais) que je (?:fasse|regarde) (?:quelque chose|autre chose)"
    r"|(?:y a-t-il |il y a |y a )?autre chose(?: (?:pour toi|que je peux faire))?\s*\?"
    r"|je reste (?:à ta disposition|dispo)"
    r"|j'espère que (?:ça|cela) (?:t'aide|te sera utile|répond)"
    r"|je suis là si (?:tu as|t'as) besoin"
    r"|je (?:te |t')(?:l'|les |en )?(?:ouvre|lis|montre|résume|détaille|dis plus)\b[^.!]*\?$"  # « Je t'en lis un ? »
    r")", _I)

# Il raconte que la reconnaissance vocale a coupé ta phrase : jamais à voix haute.
_RECIT_COUPURE = re.compile(
    r"\b(?:ta|la|cette|ton|le) (?:phrase|message|demande|question)\s+(?:s'est|a été|est)\s+(?:encore\s+)?"
    r"(?:coupée?|tronquée?|interrompue?|incomplète?)"
    r"|\b(?:j'ai|je n'ai) (?:mal |pas (?:bien |tout )?)?(?:compris|entendu|saisi) (?:la fin|la suite|ce que tu)"
    r"|\bt(?:u n')?as pas fini ta phrase", _I)


def nettoyer(phrase: str, premiere: bool, apres_coupure: bool = False) -> str:
    """Une phrase de sa réponse → ce qu'il dit vraiment (« » : il ne la dit pas).
    `premiere` : c'est la première phrase qu'il prononce ; `apres_coupure` : tu venais de lui couper la parole."""
    phrase = phrase.strip()
    if not phrase:
        return ""
    if _RECIT_COUPURE.search(phrase):
        return "Oui ?" if premiere else ""
    if not premiere:
        return "" if _REMPLISSAGE.match(phrase) else phrase
    avant = phrase
    if apres_coupure:
        phrase = _EXCUSES.sub("", phrase)
    if m := _OUVERTURE_CLICHE.match(phrase):
        reste = phrase[m.end():]
        if m.group("oui"):  # « Oui, bien sûr. » → « Oui. » ; « Oui, bien sûr, je lance ça. » → « Oui, je lance ça. »
            oui = m.group("oui")
            phrase = f"{oui}, {reste[0].lower()}{reste[1:]}" if reste else f"{oui}."
        else:
            phrase = reste
    phrase = _OUVERTURE_TIC.sub("", phrase)
    if not phrase or phrase == avant:
        return phrase
    return phrase[0].upper() + phrase[1:]


# L'avis que Claude Code écrit à la place du modèle quand l'abonnement est épuisé :
# « You've hit your 5-hour limit · resets 9:50pm (Europe/Paris) · out of usage credits ».
_AVIS_LIMITE = re.compile(
    r"^\W*(?:you['’]ve (?:hit|reached) (?:your|the)\b.{0,40}\blimit\b"
    r"|claude(?: ai)? usage limit reached"
    r"|you['’]re (?:now )?out of (?:extra )?usage)", _I)
_REPRISE = re.compile(
    r"\bresets?\s+(?:at\s+)?(?:(?P<mois>[a-z]{3,9})\.?\s+(?P<jour>\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?\b", _I)


def avis_limite(phrase: str) -> str:
    """L'avis de limite d'abonnement (en anglais) → une phrase à dire en français ; « » si ce n'en est pas un.
    « You've hit your 5-hour limit · resets 9:50pm » → « Tu as atteint la limite de ton abonnement,
    ça se libère à 21 h 50. »"""
    if not _AVIS_LIMITE.search(phrase.strip()):
        return ""
    reprise = ""
    if (m := _REPRISE.search(phrase)) and (m.group("ap") or m.group("m")):
        heure, minutes = int(m.group("h")), m.group("m")
        if m.group("ap"):
            heure = heure % 12 + (12 if m.group("ap").lower() == "pm" else 0)
        if heure < 24:
            jour = f"le {int(m.group('jour'))} " if m.group("jour") else ""
            reprise = f", ça se libère {jour}à {heure} h{f' {minutes}' if minutes and minutes != '00' else ''}"
    return f"Tu as atteint la limite de ton abonnement{reprise}."


# Mots que la reconnaissance vocale écorche, sans ambiguïté possible.
_CORRECTIONS = [
    (re.compile(r"\b(?:load|lod|lode|clode|clod|clot|cloud|clau?de|cl[ao]w?d)[\s-]+code\b", _I), "Claude Code"),
    (re.compile(r"\bwhat'?s[\s-]?app\b|\bwhats?app?\b|\bwats?app?\b|\bwatsap+\b|\bouatsap+\b", _I), "WhatsApp"),
    (re.compile(r"\bg[\s-]mail\b|\bgee[\s-]?mail\b|\bji[\s-]?mail\b", _I), "Gmail"),
    (re.compile(r"\bchat\s?gpt\b|\bchat j'ai pété\b|\bchat g p t\b", _I), "ChatGPT"),
    # « ouvre curseur » = l'app Cursor ; pas « déplace le curseur » (celui de la souris).
    (re.compile(r"\b(ouvre|ouvrir|lance|lancer|ferme|fermer|dans|sur|avec|prompt)\s+curseur\b", _I), r"\1 Cursor"),
    # Les noms que la transcription déforme le plus souvent dans jervis.log.
    (re.compile(r"\blink(?:ed)?[\s-]?(?:in|inn|zin|sin|zinc|ink)\b|\blinkdin\b|\blinquedin\b", _I), "LinkedIn"),
    (re.compile(r"(?<!san\s)\bfrisco\b", _I), "San Francisco"),
    (re.compile(r"(?<!vidi )(?<!vidi, )\bv(?:ici|icy|issi)\b", _I), "VC"),   # « veni, vidi, vici » reste intact
    (re.compile(r"\bl[ée]\s?g[oa]ra\b|\blegorra\b", _I), "Legora"),
    (re.compile(r"\bzap+i[eè]re?s?\b", _I), "Zapier"),
]


def corriger_transcription(texte: str) -> str:
    """« ouvre load code » → « ouvre Claude Code ». Ne touche qu'aux erreurs sûres. Les lettres épelées
    (« T A double L ») sont recollées en un seul mot (« TALL »)."""
    for motif, remplacement in _CORRECTIONS:
        texte = motif.sub(remplacement, texte)
    return decoder_epellation(texte)


# --- Les épellations : « T, A, double L » → « TALL » ----------------------------------------------------------
# La transcription écrit chaque lettre épelée en majuscule, séparée par un espace ou une virgule, et « double L »
# pour deux lettres pareilles. En minuscule, un « a » ou un « y » seul est un vrai mot : on ne touche qu'aux
# majuscules (et à « double / triple » suivi d'une lettre).
_LETTRE = r"(?:(?i:double|triple)\s+[A-Za-z]|[A-Z])"
_SEPARATEUR = r"[\s,.\-]+"
_EPELLE = re.compile(rf"(?<![\w'’]){_LETTRE}(?:{_SEPARATEUR}{_LETTRE})+(?![\w'’])")
_UNE_LETTRE = re.compile(r"(?i:(double|triple)\s+)?([A-Za-z])")


def _lettres(suite: str) -> str:
    return "".join(
        l.upper() * ({"double": 2, "triple": 3}.get((m.group(1) or "").lower(), 1))
        for m in _UNE_LETTRE.finditer(suite) for l in m.group(2))


def decoder_epellation(texte: str) -> str:
    """« Jessica T A double L » → « Jessica TALL ». Au moins trois lettres, sinon « plan B. C » resterait collé."""
    def recoller(m: re.Match) -> str:
        mot = _lettres(m.group(0))
        return mot if len(mot) >= 3 else m.group(0)
    return _EPELLE.sub(recoller, texte)


_FIN_EPELLE = re.compile(rf"(?<![\w'’])(?:[A-Z]{_SEPARATEUR}){{1,}}[A-Z]\W*$|(?<![\w'’])(?i:double|triple)\W*$")


def epellation_en_cours(texte: str) -> bool:
    """« T A » ou « C V » : il épelle encore, la suite arrive. Une lettre seule (« plan B ») est une phrase finie."""
    return bool(_FIN_EPELLE.search(texte.strip()))


# --- Le bruit d'une vidéo dans une autre écriture (russe, chinois, arabe…) : pas toi -------------------------------
def hors_alphabet(texte: str) -> bool:
    """Plus de la moitié des lettres sont hors de l'alphabet latin : une vidéo, jamais une demande pour lui.
    Sauf si c'est l'écriture d'une langue que tu lui parles (chinois, géorgien… avec JERVIS_LANGUES)."""
    from . import langues  # import tardif : naturel reste un module sans dépendance

    if langues.parle(langues.ecriture(texte)):
        return False
    lettres = [c for c in texte if c.isalpha()]
    if len(lettres) < 3:
        return False
    etrangeres = sum(not ("LATIN" in unicodedata.name(c, "")) for c in lettres)
    return etrangeres / len(lettres) > 0.5


# --- Ta phrase dite en plusieurs morceaux -------------------------------------------------------------------------
# « Envoie un mail à Paul, euh… » : tu réfléchis à voix haute, la suite arrive (il attend plus longtemps).
# Les « euh » ne trompent pas ; « du coup », « en fait », « genre »… seulement sans point final
# (« Je suis crevé, en fait. » est une phrase finie).
_EUH = r"euh+|heu+|hum+|hmm+|bah|ben"
_FIN_EUH = re.compile(rf"(?:(?<![\w'’-])(?:{_EUH})\W*)+$", _I)
_HESITATION = re.compile(
    rf"(?:(?<![\w'’-])(?:{_EUH}|alors|enfin|genre|du coup|en fait|tu vois|comment dire|voyons|disons"
    r"|attends voir|c'est-à-dire)\W*)+$", _I)


def hesite(texte: str) -> bool:
    """Ta phrase finit sur une hésitation (« … du coup », « … euh ») : tu cherches tes mots, pas fini."""
    texte = texte.strip()
    if re.search(r"[?!][\s\"'»)]*$", texte):
        return False  # une question, une exclamation : c'est fini
    if re.search(r"(?<![.…])\.[\s\"'»)]*$", texte):  # un vrai point (pas des points de suspension)
        return bool(_FIN_EUH.search(texte))
    return bool(_HESITATION.search(texte))


_MOT = re.compile(r"[\w'’-]+")


def _simple(mot: str) -> str:
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", mot) if unicodedata.category(c) != "Mn")
    return re.sub(r"^(?:[cdjlmnst]|qu)['’]", "", sans_accents.casefold()).strip("'’-")  # « d'appeler » = « appeler »


def recoller(debut: str, suite: str) -> str:
    """Recolle deux morceaux de ta phrase. La reconnaissance réentend souvent la fin du premier au début du
    second (« ouvre le fichier » + « fichier budget ») : le doublon saute. Et si tu as repris ta phrase en la
    corrigeant (« envoie un mail à Paul » + « envoie un mail à Pierre pour lui dire… »), seule la reprise reste."""
    debut, suite = debut.strip(), suite.strip()
    if not suite:
        return debut
    debut = _FIN_EUH.sub("", debut).rstrip(" ,")  # « … à Paul, euh » : le « euh » n'apporte rien
    if not debut:
        return suite
    mots_debut = list(_MOT.finditer(debut))
    a = [_simple(m.group()) for m in mots_debut]
    mots_suite = list(_MOT.finditer(suite))
    b = [_simple(m.group()) for m in mots_suite]
    for k in range(min(len(a), len(b)), 0, -1):  # la plus longue fin du début que la suite redit
        queue, tete = a[-k:], b[:k]
        ecarts = sum(x != y for x, y in zip(queue, tete))
        # Le premier mot doit être le même ; un mot corrigé (« Paul » → « Pierre ») si au moins quatre sont redits.
        if queue[0] != tete[0] or ecarts > (1 if k >= 4 else 0):
            continue
        if k == 1 and len(queue[0]) < 3:  # « tu » + « tu… » : trop court pour être sûr que c'est un doublon
            continue
        if not ecarts:  # un simple doublon : la suite sans ce qu'elle redit
            apres = suite[mots_suite[k - 1].end():].lstrip(" ,") if k < len(b) else ""
            return f"{debut.rstrip(' ?.!,…')} {apres}" if apres else debut
        reste = debut[:mots_debut[-k].start()].rstrip(" ,")  # une reprise corrigée : la nouvelle version
        return f"{reste} {suite}" if reste else suite
    return f"{debut.rstrip(' ?.!,…')} {suite}"
