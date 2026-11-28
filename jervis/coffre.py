"""Son coffre : tes données sensibles restent sur ton Mac, et Claude ne les voit jamais.

Ton IBAN, ton numéro de sécu, tes identifiants de l'État (FranceConnect, impots.gouv, ameli…), tes mots de passe :
- ils sont rangés dans ton Trousseau macOS (Accès au trousseau, service « jervis-coffre »), jamais dans un fichier ;
  espace/coffre.json ne garde que leur nom, leur genre, un indice (« FR76 … 0189 ») et les sites où les coller ;
- avant que quoi que ce soit parte chez Claude (ce que tu lui dis ou écris, ce que ses outils lui rendent : une
  page, un fichier, une commande, un mail), ils sont remplacés ici, sur le Mac, par « [🔒 iban] ». Un IBAN, un
  numéro de sécu ou un numéro de carte qu'il ne connaît pas encore est masqué de la même façon ; un IBAN, un numéro
  de sécu ou un « mon mot de passe impots c'est … » que tu lui donnes est rangé tout seul dans le coffre ;
- pour remplir un champ, il appelle copier_secret : la valeur passe du Trousseau au presse-papiers (90 secondes au
  plus, 15 après le collage), il clique dans le champ et colle (cmd+v). Tant qu'il y est, il ne peut que cliquer,
  taper et coller (permissions.py) : rien ne peut le lire.

Où il peut coller : sur les sites liés à ce secret (ceux que tu as donnés en le rangeant, ou ceux à qui tu as déjà
dit oui), seulement si la page ouverte est bien ce site. Ailleurs, le Gardien te pose la question, écrite par le
code (« Je vais coller ton iban sur urssaf.fr. Je peux ? ») ; ton oui lie le site. Jamais dans une messagerie, un
mail ou un réseau social (sauf le mot de passe de ce compte-là) : tes secrets ne partent chez personne. Payer reste
exclu (comptes.py) : un numéro de carte n'est jamais rangé, seulement masqué.

Ce que le coffre ne peut pas cacher : une capture d'écran (une image, pas du texte) d'une page qui affiche la donnée,
et ce que tu dis en épelant (« F R 7 6… ») que la transcription n'écrit pas comme un IBAN.

En ligne de commande, sans Claude : python -m jervis.coffre ranger "mot de passe impots" impots.gouv.fr
(il te le demande sans l'afficher), python -m jervis.coffre liste, python -m jervis.coffre oublier "iban".
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from claude_agent_sdk import tool

from . import comptes, config

REGISTRE: Path = config.ESPACE / "coffre.json"   # nom → genre, indice, sites, date : jamais la valeur
SERVICE = "jervis-coffre"
GENRES = {"iban": "IBAN", "nir": "numéro de sécu", "mot de passe": "mot de passe", "identifiant": "identifiant",
          "secret": "donnée secrète"}
INCONNUS = {"iban": "[🔒 un IBAN]", "nir": "[🔒 un numéro de sécu]", "carte": "[🔒 une carte bancaire]"}
VALEUR_MAX = 500
MASQUE_MIN = 6   # une valeur rangée plus courte (un code PIN) n'est pas cherchée mot pour mot : trop de faux pas
# Ses secrets ne partent chez personne : pas de collage dans une messagerie, un mail ou un réseau social, sauf le mot
# de passe du compte lui-même, sur un site que tu as lié.
MESSAGERIES = {"whatsapp.com", "messenger.com", "telegram.org", "discord.com", "slack.com", "signal.org",
               "mail.google.com", "outlook.live.com", "outlook.office.com", "mail.yahoo.com", "icloud.com",
               "proton.me", "gmail.com", "x.com", "twitter.com", "instagram.com", "facebook.com", "linkedin.com",
               "reddit.com", "snapchat.com", "tiktok.com", "leboncoin.fr"}

_valeurs: dict[str, str] = {}          # nom → valeur, en mémoire seulement (pour la masquer partout)
_page: tuple[str, float] | None = None  # la dernière page vue dans son navigateur (domaine, quand)


# --- Le registre (sans les valeurs) --------------------------------------------------------------------------

def nom_valide(nom: Any) -> str | None:
    """« Mot de passe d'impôts » → « mot de passe d impôts ». Lettres, chiffres, espaces, . + - (40 au plus)."""
    s = " ".join(re.sub(r"['’]", " ", str(nom or "")).split()).lower()
    return s if re.fullmatch(r"[\w .+-]{1,40}", s) else None


def fiches() -> dict[str, dict[str, Any]]:
    try:
        donnees = json.loads(REGISTRE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return donnees if isinstance(donnees, dict) else {}


def _ecrire(donnees: dict[str, dict[str, Any]]) -> None:
    REGISTRE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRE.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")


def fiche(nom: str | None) -> dict[str, Any] | None:
    return fiches().get(nom or "")


def lie(f: dict[str, Any] | None, site: str) -> bool:
    return bool(f) and any(couvre(s, site) for s in f.get("sites") or [])


def lier(nom: str, site: str) -> None:
    donnees = fiches()
    if nom in donnees and site not in donnees[nom].setdefault("sites", []):
        donnees[nom]["sites"].append(site)
        _ecrire(donnees)


def couvre(site: str, page: str) -> bool:
    """La page « connexion.impots.gouv.fr » est bien le site « impots.gouv.fr » (et l'inverse)."""
    return page == site or page.endswith("." + site) or site.endswith("." + page)


def messagerie(site: str) -> bool:
    return any(couvre(m, site) for m in MESSAGERIES)


def indice(genre: str, valeur: str) -> str:
    """Ce que Claude peut voir pour s'y retrouver, sans rien de la valeur (sauf le début et la fin d'un IBAN)."""
    if genre == "iban":
        return f"{valeur[:4]} … {valeur[-4:]}"
    if genre == "nir":
        return f"{valeur[0]} … (clé {valeur[-2:]})"
    return f"{len(valeur)} caractères"


# --- Ce qui se reconnaît tout seul ---------------------------------------------------------------------------

def _compact(s: str) -> str:
    return re.sub(r"[\s.\- ]", "", s).upper()


def iban_valide(s: str) -> bool:
    v = _compact(s)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", v):
        return False
    chiffres = "".join(str(int(c, 36)) for c in v[4:] + v[:4])
    return int(chiffres) % 97 == 1


def nir_valide(s: str) -> bool:
    v = _compact(s)
    if not re.fullmatch(r"[12]\d{4}(?:\d{2}|2[AB])\d{8}", v):
        return False
    corps = v[:13].replace("2A", "19").replace("2B", "18")
    return 97 - int(corps) % 97 == int(v[13:])


def carte_valide(s: str) -> bool:
    chiffres = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(chiffres) <= 19 or chiffres[0] not in (3, 4, 5, 6):
        return False
    total = 0
    for i, c in enumerate(reversed(chiffres)):
        c = c * 2 if i % 2 else c
        total += c - 9 if c > 9 else c
    return total % 10 == 0


class Trouve(NamedTuple):
    debut: int
    fin: int
    genre: str
    valeur: str      # telle qu'on la range (IBAN et numéro de sécu : sans espaces)
    pour: str = ""   # « mot de passe impots » : pour quoi (le nom à lui donner)


_DEBUT_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?=[  -]?[A-Za-z0-9])")
_NIR = re.compile(r"(?<![\dA-Za-z])[12](?:[ .]?\d{2}){2}[ .]?(?:\d{2}|2[ABab])(?:[ .]?\d{3}){2}[ .]?\d{2}(?!\d)")
_CARTE = re.compile(r"(?<!\d)(?<!\d[.,])\d(?:[ -]?\d){12,18}(?!\d|[.,]\d)")
# « mon mot de passe impots c'est Truc!2024 », « mdp pour ameli : … », « code secret = … »
_PHRASE = re.compile(
    r"\b(?P<quoi>mot de passe|mdp|password|code secret|code confidentiel|code pin)"
    r"(?:\s+(?:de |du |des |pour |sur |d['’]\s*)?(?:mon |ma |mes |l['’]|la |le )?"
    r"(?P<pour>(?!est\b|c['’]est\b)[\w.-]{2,30}))?"
    r"\s*(?:est|c['’]est|:|=)\s*[«\"]?\s*(?P<valeur>[^\s«»\"]{4,64})", re.IGNORECASE)


def _ibans(texte: str) -> list[tuple[int, int, str]]:
    """Chaque IBAN valide, écrit d'un bloc ou par groupes (« FR76 3000 6000 … »), même suivi d'autres mots."""
    trouves = []
    for m in _DEBUT_IBAN.finditer(texte):
        compact, fins, j = "", [], m.start()
        while j < len(texte) and len(compact) < 34:
            c = texte[j]
            if c.isascii() and c.isalnum():
                compact += c.upper()
                j += 1
                fins.append((len(compact), j))
            elif (c in "  -" and compact and j + 1 < len(texte) and texte[j + 1].isascii()
                  and texte[j + 1].isalnum() and texte[j - 1] not in "  -"):
                j += 1
            else:
                break
        for n, fin in reversed(fins):
            if n >= 15 and (fin == len(texte) or not texte[fin].isalnum()) and iban_valide(compact[:n]):
                trouves.append((m.start(), fin, compact[:n]))
                break
    return trouves


def trouver(texte: str, phrases: bool = False) -> list[Trouve]:
    """Ce qu'il faut masquer dans ce texte, sans chevauchement, dans l'ordre : les valeurs déjà rangées (mot pour
    mot), les IBAN, les numéros de sécu, les cartes, et (`phrases`) « mon mot de passe … c'est … »."""
    pris: list[Trouve] = []

    def ajouter(t: Trouve) -> None:
        if not any(t.debut < p.fin and p.debut < t.fin for p in pris):
            pris.append(t)

    for nom, valeur in sorted(_valeurs.items(), key=lambda nv: -len(nv[1])):
        if len(valeur) >= MASQUE_MIN:
            for m in re.finditer(re.escape(valeur), texte):
                ajouter(Trouve(m.start(), m.end(), "connu", valeur, nom))
    for debut, fin, valeur in _ibans(texte):
        ajouter(Trouve(debut, fin, "iban", valeur))
    for m in _NIR.finditer(texte):
        if nir_valide(m.group()):
            ajouter(Trouve(m.start(), m.end(), "nir", _compact(m.group())))
    for m in _CARTE.finditer(texte):
        if carte_valide(m.group()):
            ajouter(Trouve(m.start(), m.end(), "carte", ""))
    if phrases:
        for m in _PHRASE.finditer(texte):
            valeur = m.group("valeur").rstrip(".,;")
            # un chiffre ou un signe : « le mot de passe est trop court » n'est pas un mot de passe
            if len(valeur) >= 4 and "🔒" not in valeur and re.search(r"[\d\W_]", valeur):
                pour = (m.group("pour") or "").lower()
                ajouter(Trouve(m.start("valeur"), m.start("valeur") + len(valeur), "mot de passe", valeur, pour))
    return sorted(pris)


def _nom_de(genre: str, valeur: str) -> str | None:
    """Le nom sous lequel cette valeur est déjà rangée (un IBAN se compare sans ses espaces)."""
    for nom, v in _valeurs.items():
        if v == valeur or (genre in ("iban", "nir") and _compact(v) == valeur):
            return nom
    return None


def _remplacer(texte: str, trouves: list[Trouve], noms: dict[int, str]) -> str:
    morceaux, fin = [], 0
    for i, t in enumerate(trouves):
        nom = noms.get(i) or (t.pour if t.genre == "connu" else _nom_de(t.genre, t.valeur))
        morceaux += [texte[fin:t.debut], f"[🔒 {nom}]" if nom else INCONNUS.get(t.genre, "[🔒 un secret]")]
        fin = t.fin
    return "".join(morceaux) + texte[fin:]


def masquer(texte: str) -> str:
    """Le texte tel que Claude peut le lire : chaque secret remplacé par « [🔒 nom] » (rien n'est rangé)."""
    if not config.COFFRE or not texte:
        return texte
    trouves = trouver(texte)
    return _remplacer(texte, trouves, {}) if trouves else texte


def _nom_libre(base: str) -> str:
    donnees = fiches()
    if base not in donnees and base not in _valeurs:
        return base
    n = 2
    while f"{base} {n}" in donnees or f"{base} {n}" in _valeurs:
        n += 1
    return f"{base} {n}"


async def proteger(texte: str) -> str:
    """Ce que tu lui dis ou écris, avant que Claude le lise : masqué, et chaque nouvel IBAN, numéro de sécu ou mot
    de passe donné en clair (« mon mot de passe impots c'est … ») rangé dans le coffre (Trousseau)."""
    if not config.COFFRE or not texte:
        return texte
    trouves = trouver(texte, phrases=True)
    noms: dict[int, str] = {}
    for i, t in enumerate(trouves):
        if t.genre not in ("iban", "nir", "mot de passe") or _nom_de(t.genre, t.valeur):
            continue
        base = ("iban" if t.genre == "iban" else "numéro de sécu" if t.genre == "nir"
                else nom_valide(f"mot de passe {t.pour}") or "mot de passe")
        nom = _nom_libre(base)
        site = comptes.domaine(t.pour) if t.genre == "mot de passe" and "." in t.pour else None
        if await enregistrer(nom, t.genre, t.valeur, [site] if site else []) is None:
            noms[i] = nom
    return _remplacer(texte, trouves, noms) if trouves else texte


# --- Ce que ses outils lui rendent ---------------------------------------------------------------------------

_PAGE_URL = re.compile(r"Page URL:\s*(https?://[^\s)\"'<>]+)")
_URL_CHROME = re.compile(r"(?:\burl\b\W{0,4}|Navigated to\s+)(https?://[^\s)\"'<>]+)", re.IGNORECASE)


def noter_page(outil: str, sortie: Any) -> None:
    """Retient la page ouverte dans son navigateur (Playwright, ou son Chrome) : copier_secret ne colle que là."""
    global _page
    texte = sortie if isinstance(sortie, str) else json.dumps(sortie, ensure_ascii=False, default=str)
    urls = _PAGE_URL.findall(texte)
    if not urls and "chrome" in outil:
        urls = _URL_CHROME.findall(texte)
    if urls and (d := comptes.domaine(urls[-1])):
        _page = (d, time.monotonic())


def page_ouverte() -> str | None:
    if _page and time.monotonic() - _page[1] < config.COFFRE_PAGE_S:
        return _page[0]
    return None


def _masquer_objet(o: Any) -> Any:
    """Masque chaque texte d'une sortie d'outil, sans toucher à sa forme (ni aux images)."""
    if isinstance(o, str):
        return o if (len(o) > 1000 and not re.search(r"\s", o[:1000])) else masquer(o)  # (une image en base64)
    if isinstance(o, list):
        return [_masquer_objet(x) for x in o]
    if isinstance(o, dict):
        if o.get("type") == "image":
            return o
        return {k: _masquer_objet(v) for k, v in o.items()}
    return o


def masquer_sortie(outil: str, sortie: Any) -> Any | None:
    """La sortie d'un outil, masquée ; None si elle n'avait rien à masquer (on la laisse telle quelle)."""
    if outil.startswith("mcp__"):
        noter_page(outil, sortie)
    if not config.COFFRE:
        return None
    neuve = _masquer_objet(sortie)
    return None if neuve == sortie else neuve


async def crochet(entree: dict, _id: str | None, _contexte) -> dict:
    """(Crochet PostToolUse) Avant que Claude lise la sortie d'un outil, quel qu'il soit : ses secrets masqués."""
    neuve = masquer_sortie(str(entree.get("tool_name") or ""), entree.get("tool_response"))
    if neuve is None:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": neuve}}


# --- Trousseau et presse-papiers (remplacés par des faux dans les tests) --------------------------------------

async def _trousseau_ranger(nom: str, valeur: str) -> str | None:
    """Range la valeur (la remplace si elle y est). Écrite en hexadécimal : aucun guillemet ne la casse, et elle
    passe par l'entrée standard, jamais par la ligne de commande."""
    commande = (f'add-generic-password -U -s "{SERVICE}" -a "{nom}" -l "{nom} (coffre de Steve)" '
                f'-j "Rangé par Steve le {datetime.now():%d/%m/%Y}" -X "{valeur.encode().hex()}"\n')
    code, sortie = await comptes._lancer("security", "-i", entree=commande)
    return None if not code and "returned" not in sortie else (sortie[:200] or f"security a répondu {code}")


def _lire_sortie(sortie: str) -> str:
    """security -w écrit en hexadécimal une valeur qui n'est pas que de l'ASCII imprimable (« été » → c3a974c3a9)."""
    if re.fullmatch(r"(?:[0-9a-f]{2})+", sortie):
        try:
            brut = bytes.fromhex(sortie).decode()
        except UnicodeDecodeError:
            return sortie
        if not (brut.isascii() and brut.isprintable()):
            return brut
    return sortie


async def _trousseau_retrouver(nom: str) -> str | None:
    code, sortie = await comptes._lancer("security", "find-generic-password", "-s", SERVICE, "-a", nom, "-w")
    return _lire_sortie(sortie) if not code and sortie else None


async def _trousseau_oublier(nom: str) -> None:
    await comptes._lancer("security", "delete-generic-password", "-s", SERVICE, "-a", nom)


async def _presse_papiers() -> str | None:
    code, sortie = await comptes._lancer("pbpaste")
    return None if code else sortie


async def _vider_presse_papiers(valeur: str) -> None:
    if await _presse_papiers() == valeur:   # seulement si c'est toujours lui : rien d'autre n'est effacé
        await comptes._lancer("pbcopy", entree="")


async def enregistrer(nom: str, genre: str, valeur: str, sites: list[str]) -> str | None:
    """Range dans le Trousseau, puis note la fiche (sans la valeur). None si c'est fait, sinon le problème."""
    if probleme := await _trousseau_ranger(nom, valeur):
        return probleme
    _valeurs[nom] = valeur
    donnees = fiches()
    donnees[nom] = {"genre": genre, "indice": indice(genre, valeur), "sites": sorted(set(sites)),
                    "date": datetime.now().isoformat(timespec="minutes")}
    _ecrire(donnees)
    return None


async def charger() -> int:
    """Au démarrage : ressort du Trousseau les valeurs du coffre, en mémoire seulement, pour les masquer partout."""
    for nom in fiches():
        if nom not in _valeurs and (valeur := await _trousseau_retrouver(nom)):
            _valeurs[nom] = valeur
    return len(_valeurs)


def genre_de(valeur: str, demande: Any = None) -> str:
    if iban_valide(valeur):
        return "iban"
    if nir_valide(valeur):
        return "nir"
    return str(demande) if demande in GENRES else "secret"


# --- Ses outils ---------------------------------------------------------------------------------------------

def _texte(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def _erreur(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "is_error": True}


@tool("ranger_secret", "Range dans son coffre (son Trousseau, sur le Mac) une donnée sensible : IBAN, numéro de "
      "sécu, numéro fiscal, identifiant FranceConnect, mot de passe… Tu ne la vois JAMAIS : il la copie lui-même "
      "(cmd+c, ou le bouton « copier » de son appli), ou tu cliques sur un bouton « copier » de la page ; l'outil la "
      "prend dans le presse-papiers, la range, puis l'en retire. `nom` = comment l'appeler (« iban », « mot de passe "
      "impots », « numéro fiscal ») ; `genre` = iban, nir, mot de passe, identifiant ou secret ; `sites` = les "
      "domaines où la coller sans lui redemander (impots.gouv.fr) ; `remplacer` = true pour changer une valeur déjà "
      "rangée sous ce nom.",
      {"type": "object",
       "properties": {"nom": {"type": "string"}, "genre": {"type": "string"},
                      "sites": {"type": "array", "items": {"type": "string"}}, "remplacer": {"type": "boolean"}},
       "required": ["nom"]})
async def ranger_secret(args: dict[str, Any]) -> dict[str, Any]:
    nom = nom_valide(args.get("nom"))
    if not nom:
        return _erreur("`nom` : quelques mots simples (« iban », « mot de passe impots »), 40 caractères au plus.")
    deja = fiche(nom)
    if deja and args.get("remplacer") is not True:
        return _erreur(f"« {nom} » est déjà dans son coffre ({GENRES.get(deja.get('genre'), '')} "
                       f"{deja.get('indice', '')}) : remplacer=true pour le changer, ou un autre nom.")
    valeur = await _presse_papiers()
    if not valeur or len(valeur) > VALEUR_MAX or "\n" in valeur.strip():
        return _erreur("Le presse-papiers ne contient pas une donnée à ranger (vide, sur plusieurs lignes, ou plus "
                       f"de {VALEUR_MAX} caractères) : demande-lui de la copier (cmd+c), puis recommence.")
    valeur = valeur.strip()
    genre = genre_de(valeur, args.get("genre"))
    sites = [d for s in (args.get("sites") or []) if (d := comptes.domaine(s))]
    if probleme := await enregistrer(nom, genre, valeur, sites + (deja.get("sites", []) if deja else [])):
        return _erreur(f"Le Trousseau a refusé ({probleme}) : rien n'est rangé.")
    await _vider_presse_papiers(valeur)
    f = fiche(nom) or {}
    ou = ", ".join(f.get("sites") or []) or "aucun site pour l'instant (je lui demanderai avant de la coller)"
    return _texte(f"Rangé dans son coffre sous « {nom} » ({GENRES[genre]} {f.get('indice', '')}), et retiré du "
                  f"presse-papiers. Sites : {ou}. Partout tu verras [🔒 {nom}] ; pour remplir un champ : "
                  "copier_secret, puis cmd+v.")


@tool("lister_secrets", "Ce qu'il y a dans son coffre : le nom de chaque donnée sensible, son genre, un indice, et "
      "les sites où la coller. Jamais les valeurs.", {})
async def lister_secrets(_args: dict[str, Any]) -> dict[str, Any]:
    donnees = fiches()
    if not donnees:
        return _texte("Son coffre est vide. Pour y ranger quelque chose : il le copie, puis ranger_secret.")
    return _texte("\n".join(f"« {nom} » — {GENRES.get(f.get('genre'), 'secret')} {f.get('indice', '')} — "
                            f"sites : {', '.join(f.get('sites') or []) or 'aucun (question avant de coller)'}"
                            for nom, f in sorted(donnees.items())))


@tool("copier_secret", "Pour remplir un champ avec une donnée de son coffre (voir lister_secrets, ou un "
      "[🔒 nom] dans ce que tu lis) : la met dans le presse-papiers sans que tu la voies. Clique d'abord dans le "
      "champ, puis copier_secret, puis colle aussitôt (cmd+v : Playwright browser_press_key « Meta+v », son Chrome "
      "la touche cmd+v). `site` = le domaine exact de la page où tu colles (impots.gouv.fr) : sur un autre site que "
      "les siens, il te pose la question. Jamais dans un message, un mail, un fichier ou une commande.",
      {"type": "object", "properties": {"nom": {"type": "string"}, "site": {"type": "string"}},
       "required": ["nom", "site"]})
async def copier_secret(args: dict[str, Any]) -> dict[str, Any]:
    nom, site = nom_valide(args.get("nom")), comptes.domaine(args.get("site"))
    if not nom or not fiche(nom) or not site:
        return _erreur("Donne le `nom` d'une donnée de son coffre (lister_secrets) et le `site` de la page.")
    valeur = _valeurs.get(nom) or await _trousseau_retrouver(nom)
    if not valeur:
        return _erreur(f"« {nom} » n'est plus dans son Trousseau : demande-lui de la copier, puis ranger_secret.")
    _valeurs[nom] = valeur
    if probleme := await comptes.copier(valeur):
        return _erreur(f"Le presse-papiers ne répond pas ({probleme}).")
    return _texte(f"Son « {nom} » est dans le presse-papiers pour {config.PRESSE_PAPIERS_S} secondes : colle-le "
                  "maintenant (cmd+v) dans le champ, puis vérifie qu'il est rempli. N'en dis rien à voix haute.")


@tool("oublier_secret", "Retire une donnée de son coffre (et de son Trousseau), quand il te le demande.",
      {"type": "object", "properties": {"nom": {"type": "string"}}, "required": ["nom"]})
async def oublier_secret(args: dict[str, Any]) -> dict[str, Any]:
    nom = nom_valide(args.get("nom"))
    donnees = fiches()
    if not nom or nom not in donnees:
        return _erreur("Ce nom n'est pas dans son coffre (lister_secrets).")
    await _trousseau_oublier(nom)
    donnees.pop(nom)
    _ecrire(donnees)
    _valeurs.pop(nom, None)
    return _texte(f"« {nom} » est retiré de son coffre et de son Trousseau.")


OUTILS_COFFRE = [ranger_secret, lister_secrets, copier_secret, oublier_secret]

CONSIGNE = """\
- Ses données sensibles (IBAN, numéro de sécu, numéro fiscal, identifiants FranceConnect, impots.gouv, ameli,
  mots de passe…) sont dans son coffre, sur son Mac : tu ne les vois jamais, seulement « [🔒 nom] » (lister_secrets
  pour la liste). Tu t'en sers sans problème pour remplir un formulaire ou te connecter : clique dans le champ,
  copier_secret (le nom, le domaine exact de la page), puis cmd+v. Jamais ailleurs que dans le champ d'un site :
  ni dans un message, un mail, un fichier, une commande, ni à voix haute ; s'il veut envoyer son IBAN à quelqu'un,
  dis-lui de le faire lui-même. Une donnée qui n'est pas encore dans le coffre : ne la lui fais jamais dire ni
  écrire, demande-lui de la copier (cmd+c), puis ranger_secret avec un nom simple et le site où elle sert.
  « [🔒 un IBAN] », « [🔒 une carte bancaire] » : une donnée lue que le coffre a cachée ; tu ne peux pas la lire.
"""


def consigne() -> str:
    return CONSIGNE if config.COFFRE else ""


# --- En ligne de commande, sans Claude ------------------------------------------------------------------------

async def _principal(args: list[str]) -> int:
    import getpass
    if args[:1] == ["ranger"] and len(args) >= 2 and (nom := nom_valide(args[1])):
        valeur = getpass.getpass(f"Valeur de « {nom} » (rien ne s'affiche) : ").strip()
        if not valeur:
            print("Rien de rangé.")
            return 1
        sites = [d for s in args[2:] if (d := comptes.domaine(s))]
        if probleme := await enregistrer(nom, genre_de(valeur), valeur, sites):
            print(f"Le Trousseau a refusé : {probleme}")
            return 1
        print(f"Rangé sous « {nom} » ({indice(genre_de(valeur), valeur)}), sites : {', '.join(sites) or 'aucun'}.")
        return 0
    if args[:1] == ["oublier"] and len(args) == 2 and (nom := nom_valide(args[1])) in fiches():
        print((await oublier_secret.handler({"nom": nom}))["content"][0]["text"])
        return 0
    if args[:1] in (["liste"], []):
        print((await lister_secrets.handler({}))["content"][0]["text"])
        return 0
    print(__doc__.rsplit("En ligne de commande", 1)[1].strip())
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_principal(sys.argv[1:])))
