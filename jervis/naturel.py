"""Pour qu'il parle comme quelqu'un, pas comme un service client.

Deux filets de sécurité, en plus de ses consignes (le modèle les oublie parfois) :
- nettoyer() : chaque phrase de sa réponse, juste avant d'aller à la voix. Enlève les « Bien sûr ! »,
  les « N'hésite pas si… », les « Ta phrase s'est coupée » ;
- corriger_transcription() : ta phrase, juste après la reconnaissance vocale. Corrige les mots qu'elle
  écorche à coup sûr (« load code » = Claude Code). Les cas ambigus (« Watt » = WhatsApp ?) sont dans
  ses consignes : lui sait deviner avec le contexte, une regex non.
"""

import re

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
]


def corriger_transcription(texte: str) -> str:
    """« ouvre load code » → « ouvre Claude Code ». Ne touche qu'aux erreurs sûres."""
    for motif, remplacement in _CORRECTIONS:
        texte = motif.sub(remplacement, texte)
    return texte
