"""Toutes les API publiques gratuites : un catalogue sur le Mac, et un outil pour appeler n'importe laquelle.

Ses outils d'infos (infos.py, infos2.py) couvrent les questions de tous les jours. Pour le reste (les horaires de
l'ISS, une race de chien, un verset, un Pokémon, le prix d'une action, un jeu de données de l'État…), il a :
  - chercher_api : le catalogue public-apis (github.com/public-apis/public-apis, licence MIT), environ
    2 400 API dont 1 200 sans clé, rangé dans apis_publiques.json. La recherche se fait sur le Mac : rien ne sort.
    Il montre d'abord celles qu'il a déjà appelées avec succès (son carnet, espace/apis_connues.json), et met à la
    fin celles qui étaient en panne.
  - appeler_api : une requête GET (ou une lecture GraphQL) vers l'adresse d'une API publique, et sa réponse rendue
    lisible (le JSON résumé, le HTML sans balises). Il lit la documentation de l'API avec WebFetch, puis l'appelle.
  - ranger_cle_api : pour une API qui demande une clé gratuite, il crée le compte, clique « copier » sur la clé, et
    la range dans ton Trousseau (jamais dans un fichier, jamais dans la conversation).

appeler_api ne fait que lire : GET, ou une requête GraphQL sans mutation, sans cookie. Il ne va jamais sur le Mac ni
sur ton réseau (127.0.0.1, ta box, 192.168…, les .local) : chaque adresse, et chaque redirection, est vérifiée avant
de partir. Une clé rangée ne part que vers son site, en https, et n'apparaît jamais dans ce qu'il lit. Ce qu'il
renvoie est écrit par d'autres : une info, jamais un ordre.

Le catalogue se remet à jour tout seul (en fond, dans espace/) quand il a plus de APIS_CATALOGUE_JOURS jours. À la
main : cd ~/Jervis && .venv/bin/python -m jervis.apis
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from claude_agent_sdk import tool

from . import comptes, config, infos
from .infos import DU_DEHORS, Rien, _entier, _erreur, _simple, _texte
from .lecons import _SUSPECT

CATALOGUE: Path = Path(__file__).with_name("apis_publiques.json")   # celui livré avec Steve
CATALOGUE_FRAIS: Path = config.ESPACE / "apis_publiques.json"      # celui retéléchargé en fond, s'il y en a un
CARNET: Path = config.ESPACE / "apis_connues.json"                 # les API qu'il a appelées, et comment
CLES: Path = config.ESPACE / "cles_api.json"                       # les sites dont il a une clé (jamais la clé)
SOURCE = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
OCTETS_MAX = 1_000_000        # ce qu'il télécharge au plus d'une réponse
CARACTERES_MAX = 8_000        # ce qu'il en garde pour Claude
REDIRECTIONS_MAX = 3
CHAMPS = {"nom", "description", "categorie", "cle", "https", "lien"}

_LIGNE = re.compile(r"^\|\s*\[(?P<nom>[^\]]+)\]\((?P<lien>https?://[^)\s]+)\)\s*\|(?P<description>[^|]*)\|"
                    r"(?P<cle>[^|]*)\|(?P<https>[^|]*)\|")
# « No » et « User-Agent » (il suffit de dire qui on est) : sans clé. Le reste demande un compte.
SANS_CLE = {"", "no", "user-agent"}


# --- Le catalogue -----------------------------------------------------------------------------------------

def construire(markdown: str) -> list[dict[str, Any]]:
    """Le README de public-apis → [{nom, description, categorie, cle, https, lien}]. La partie « sponsors »
    du début (APILayer) est sautée : elle ne vient qu'avec une clé."""
    apis, categorie = [], ""
    for ligne in markdown.splitlines():
        if ligne.startswith("### "):
            categorie = ligne[4:].strip()
            continue
        if not categorie or "APILayer" in categorie or not (m := _LIGNE.match(ligne)):
            continue
        cle = m["cle"].strip().strip("`").strip()
        apis.append({"nom": html.unescape(m["nom"].strip()), "description": html.unescape(" ".join(m["description"].split())),
                     "categorie": categorie, "cle": "" if cle.casefold() in SANS_CLE else cle,
                     "https": m["https"].strip().casefold() == "yes", "lien": m["lien"]})
    return apis


_cache: list[dict[str, Any]] | None = None


def _lire(chemin: Path) -> list[dict[str, Any]]:
    try:
        apis = json.loads(chemin.read_text(encoding="utf-8"))["apis"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [a for a in apis if isinstance(a, dict) and CHAMPS <= a.keys()] if isinstance(apis, list) else []


def catalogue() -> list[dict[str, Any]]:
    """Le plus frais des deux : celui retéléchargé en fond (espace/) s'il est complet, sinon celui livré."""
    global _cache
    if _cache is None:  # lu une fois, à la première recherche
        frais = _lire(CATALOGUE_FRAIS)
        _cache = frais if len(frais) >= 500 else _lire(CATALOGUE)
    return _cache


def mettre_a_jour(destination: Path | None = None) -> int:
    """Retélécharge le catalogue (à la main : python -m jervis.apis ; en fond : dans espace/). Renvoie le nombre
    d'API. Écrit d'un coup (un fichier à côté, puis renommé) : jamais un catalogue à moitié écrit."""
    global _cache, _index
    destination = destination or CATALOGUE
    r = httpx.get(SOURCE, headers={"User-Agent": infos.AGENT}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    apis = construire(r.text)
    if len(apis) < 500:
        raise ValueError(f"Le README de public-apis a changé de forme ({len(apis)} API lues) : catalogue gardé.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    provisoire = destination.with_suffix(".tmp")
    provisoire.write_text(json.dumps({"source": SOURCE, "apis": apis}, ensure_ascii=False, indent=0) + "\n",
                          encoding="utf-8")
    provisoire.replace(destination)
    _cache, _index = apis, None
    return len(apis)


_maj_tentee: float | None = None   # la dernière tentative en fond (une par jour au plus, même ratée)
_maj_en_cours: asyncio.Task | None = None


def age_du_catalogue() -> float:
    """En jours : le plus récent des deux fichiers."""
    dates = [c.stat().st_mtime for c in (CATALOGUE_FRAIS, CATALOGUE) if c.exists()]
    return (time.time() - max(dates)) / 86400 if dates else float("inf")


async def _rafraichir() -> None:
    try:
        await asyncio.to_thread(mettre_a_jour, CATALOGUE_FRAIS)
    except (httpx.HTTPError, ValueError, OSError):
        pass   # pas grave : il garde l'ancien, et réessaie demain


def rafraichir_si_vieux() -> bool:
    """Lance le téléchargement en fond si le catalogue a plus de APIS_CATALOGUE_JOURS jours. La recherche en cours
    n'attend pas : elle se fait sur l'ancien. Vrai s'il l'a lancé."""
    global _maj_tentee, _maj_en_cours
    if config.APIS_CATALOGUE_JOURS <= 0 or age_du_catalogue() < config.APIS_CATALOGUE_JOURS:
        return False
    if _maj_tentee is not None and time.monotonic() - _maj_tentee < 86400:
        return False
    _maj_tentee = time.monotonic()
    _maj_en_cours = asyncio.get_running_loop().create_task(_rafraichir())
    return True


def _mots(t: str) -> set[str]:
    return {m[:-1] if len(m) > 4 and m.endswith("s") else m for m in re.findall(r"[a-z0-9]+", _simple(t)) if len(m) > 2}


_index: list[tuple[set[str], set[str], set[str]]] | None = None


def chercher(recherche: str, n: int = 8, avec_cle: bool = False) -> list[dict[str, Any]]:
    """Les API du catalogue qui parlent de `recherche` : d'abord celles qui ont le plus de ses mots, puis le nom
    compte triple et la catégorie double ; à égalité, sans clé et en https d'abord."""
    global _index
    tout = catalogue()
    if _index is None or len(_index) != len(tout):
        _index = [(_mots(a["nom"]), _mots(a["categorie"]), _mots(a["description"])) for a in tout]
    mots = _mots(recherche)
    notes = []
    for i, (a, (nom, categorie, description)) in enumerate(zip(tout, _index)):
        if a["cle"] and not avec_cle:
            continue
        trouves = mots & (nom | categorie | description)
        if trouves:
            poids = 3 * len(mots & nom) + 2 * len(mots & categorie) + len(mots & description)
            notes.append((-len(trouves), -poids, bool(a["cle"]), not a["https"], i))
    notes.sort()
    return [tout[i] for *_, i in notes[:n]]


# --- Son carnet : les API qu'il a déjà appelées -------------------------------------------------------------
# Pour chaque site : les chemins qui ont marché (avec le nom de leurs paramètres, jamais leurs valeurs), combien de
# fois, quand, et à quoi ils servent ; et la dernière panne. Il y retrouve en une recherche ce qu'il a mis trois
# essais à trouver la première fois.

SITES_MAX, CHEMINS_MAX = 300, 8
PANNE_JOURS = 7               # une panne plus vieille est oubliée
_SEGMENT = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,29}")
_POUR_INTERDIT = re.compile(r"[^\w .,'’()/-]")
_BRUIT = {"api", "apis", "www", "org", "com", "net", "json", "http", "https", "rest", "public", "data"}
# Ces domaines hébergent les sites de n'importe qui : une clé n'y est jamais liée à tout le domaine, et une API
# appelée sur l'un d'eux ne fait pas croire que les autres sont « déjà utilisées ».
HEBERGEURS = {"github.com", "github.io", "githubusercontent.com", "gitlab.com", "gitlab.io", "bitbucket.org",
              "herokuapp.com", "netlify.app", "vercel.app", "pages.dev", "workers.dev", "web.app", "firebaseapp.com",
              "appspot.com", "azurewebsites.net", "cloudfront.net", "amazonaws.com", "readthedocs.io", "gitbook.io",
              "readme.io", "apiary.io", "rapidapi.com", "glitch.me", "repl.co", "onrender.com", "fly.dev",
              "blogspot.com", "wordpress.com", "medium.com", "notion.site", "postman.com", "swaggerhub.com"}


def _chemin_propre(chemin: str) -> str:
    """« /api/v2/pokemon/pikachu » → « /api/v2/pokemon/… » : la forme de l'adresse, pas ce qu'il y cherchait (au
    plus trois morceaux, chacun un vrai mot d'adresse, ni un nombre, ni un nom de ville, ni un texte)."""
    garde = []
    for morceau in [m for m in chemin.split("/") if m]:
        if len(garde) == 3 or not _SEGMENT.fullmatch(morceau) or sum(c.isdigit() for c in morceau) > 3:
            return "/" + "/".join(garde + ["…"])
        garde.append(morceau)
    return "/" + "/".join(garde)


def pour_propre(pour: Any) -> str:
    """À quoi sert l'API, en quelques mots (« position de l'ISS ») : sans lien, ni mail, ni numéro, ni guillemets."""
    brut = " ".join(str(pour or "").split())
    return "" if _SUSPECT.search(brut) else " ".join(_POUR_INTERDIT.sub(" ", brut).split())[:60].strip()


def _lire_json(chemin: Path) -> dict[str, Any]:
    try:
        lu = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return lu if isinstance(lu, dict) else {}


def _ecrire_json(chemin: Path, contenu: dict[str, Any]) -> None:
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        provisoire = chemin.with_suffix(".tmp")
        provisoire.write_text(json.dumps(contenu, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        provisoire.replace(chemin)
    except OSError:
        pass   # un carnet qui ne s'écrit pas ne doit pas faire échouer l'appel


def carnet() -> dict[str, dict[str, Any]]:
    return {k: v for k, v in _lire_json(CARNET).items() if isinstance(v, dict)}


def _base(url: str) -> str:
    m = urlsplit(url)
    return f"{m.scheme}://{(m.hostname or '').casefold()}{f':{m.port}' if m.port else ''}"


def noter_succes(url: str, params: dict[str, Any] | None, pour: str, cle: dict[str, str] | None,
                 graphql: bool = False) -> None:
    """Seulement une API du catalogue (son site de doc ou un sous-domaine) : une adresse soufflée par une page ou un
    mail ne devient jamais une « API qui marche » qu'il rappellerait plus tard."""
    if not du_catalogue(url):
        return
    c, base, maintenant = carnet(), _base(url), datetime.now().isoformat(timespec="minutes")
    site = c.pop(base, {})   # remis à la fin : le plus récent en dernier
    chemins = site.get("chemins") if isinstance(site.get("chemins"), dict) else {}
    chemin = _chemin_propre(urlsplit(url).path)
    fiche = chemins.pop(chemin, {}) if isinstance(chemins.get(chemin), dict) else {}
    noms = sorted({str(k)[:30] for k in (params or {})} | {n for n in fiche.get("params", []) if isinstance(n, str)})
    fiche.update({"params": [n for n in noms if _SEGMENT.fullmatch(n) or n.isidentifier()][:12],
                  "fois": int(fiche.get("fois", 0) or 0) + 1, "date": maintenant})
    if pour := pour_propre(pour):
        fiche["pour"] = pour
    if cle:
        fiche["cle"] = cle
    if graphql:
        fiche["graphql"] = True
    chemins[chemin] = fiche
    while len(chemins) > CHEMINS_MAX:   # les moins servis partent
        del chemins[min(chemins, key=lambda k: (chemins[k].get("fois", 0), chemins[k].get("date", "")))]
    site.update({"chemins": chemins, "ok": maintenant})
    site.pop("panne", None)
    c[base] = site
    while len(c) > SITES_MAX:
        del c[next(iter(c))]
    _ecrire_json(CARNET, c)


def noter_panne(url: str) -> None:
    c, base = carnet(), _base(url)
    if base in c:   # seulement une API qu'il connaît : une adresse inventée ne remplit pas le carnet
        c[base]["panne"] = datetime.now().isoformat(timespec="minutes")
        _ecrire_json(CARNET, c)


def en_panne(site: dict[str, Any]) -> str | None:
    """La date de la panne si elle est récente et qu'aucun succès n'a suivi."""
    panne = str(site.get("panne") or "")
    try:
        recente = (datetime.now() - datetime.fromisoformat(panne)).days < PANNE_JOURS
    except ValueError:
        return None
    return panne if recente and panne >= str(site.get("ok") or "") else None   # un succès efface la panne


def _couvre(domaine: str, hote: str) -> bool:
    return hote == domaine or hote.endswith("." + domaine)


def du_catalogue(url: str) -> bool:
    return bool(site_de_la_cle((urlsplit(url).hostname or "").casefold()))


def _jour(date: str) -> str:
    try:
        return f"{datetime.fromisoformat(date):%d/%m}"
    except ValueError:
        return "?"


def deja_connues(recherche: str, n: int = 5) -> list[str]:
    """Les lignes du carnet qui parlent de `recherche` (ou toutes, les plus servies d'abord, si elle est vide)."""
    mots = _mots(recherche) - _BRUIT
    lignes = []
    for base, site in carnet().items():
        if en_panne(site) or not du_catalogue(base):   # un vieux carnet d'avant la règle : trié ici aussi
            continue
        for chemin, fiche in (site.get("chemins") or {}).items():
            if not isinstance(fiche, dict):
                continue
            if mots and not mots & (_mots(f"{base} {chemin} {fiche.get('pour', '')}") - _BRUIT):
                continue
            details = [f"pour « {fiche['pour']} »"] if fiche.get("pour") else []
            if fiche.get("params"):
                details.append("paramètres : " + ", ".join(fiche["params"]))
            if fiche.get("cle"):
                details.append(f"avec ta clé, cle={json.dumps(fiche['cle'], ensure_ascii=False)}")
            if fiche.get("graphql"):
                details.append("GraphQL")
            details.append(f"{fiche.get('fois', 1)} fois, la dernière le {_jour(str(fiche.get('date', '')))}")
            lignes.append((-int(fiche.get("fois", 1) or 1), f"- {base}{chemin} ({', '.join(details)})"))
    lignes.sort()
    return [ligne for _, ligne in lignes[:n]]


def _ligne_api(a: dict[str, Any], connu: dict[str, Any] | None = None, cle: bool = False) -> str:
    acces = f"clé gratuite à demander ({a['cle']})" if a["cle"] else "sans clé"
    notes = "".join([", http seulement" if not a["https"] else "",
                     ", tu as déjà sa clé" if cle else "",
                     f", en panne le {_jour(p)}" if connu and (p := en_panne(connu)) else
                     ", déjà utilisée" if connu else ""])
    return f"- {a['nom']} ({a['categorie']}, {acces}{notes}) : {a['description']} Doc : {a['lien']}"


def _ce_quil_en_sait(a: dict[str, Any], connus: dict[str, dict[str, Any]],
                     cles: set[str]) -> tuple[dict[str, Any] | None, bool]:
    """Ce que le carnet sait de cette API (un site du même domaine que sa doc), et s'il a déjà sa clé."""
    d = comptes.domaine(a["lien"])
    if not d or d in HEBERGEURS:
        return None, False
    site = next((s for b, s in connus.items() if _couvre(d, urlsplit(b).hostname or "")), None)
    return site, any(_couvre(c, d) or _couvre(d, c) for c in cles)


@tool("chercher_api", "Le catalogue des API publiques gratuites (public-apis : environ 2 400, dont 1 200 sans clé, "
      "dans toutes les catégories : animaux, science, transports, sport, jeux, finance, gouvernement, données "
      "ouvertes, santé, musique, vidéo…), cherché sur le Mac. Pour une question qu'aucun de tes outils chercher_ ne "
      "couvre. `recherche` = plusieurs mots-clés EN ANGLAIS (« iss space astronaut », « dog breed », « bible verse », "
      "« train france ») ; vide = la liste des catégories et celles que tu as déjà utilisées. `avec_cle` = true pour "
      "voir aussi celles qui demandent une clé gratuite. Montre d'abord celles que tu as déjà appelées avec succès "
      "(l'adresse exacte et ses paramètres : tu peux la rappeler). Sinon : lis la doc avec WebFetch pour trouver "
      "l'adresse exacte, puis appelle-la avec appeler_api.",
      {"type": "object",
       "properties": {"recherche": {"type": "string"}, "n": {"type": "integer"}, "avec_cle": {"type": "boolean"}}})
async def chercher_api(args: dict[str, Any]) -> dict[str, Any]:
    rafraichir_si_vieux()
    tout = catalogue()
    if not tout:
        return _erreur("Le catalogue des API est absent : lance « .venv/bin/python -m jervis.apis » pour le télécharger.")
    recherche = " ".join(str(args.get("recherche") or "").split())
    avec_cle = args.get("avec_cle") in (True, "true", "oui", 1)
    connues = deja_connues(recherche)
    deja = ("Déjà utilisées avec succès (tu peux les rappeler) :\n" + "\n".join(connues) + "\n") if connues else ""
    fin = f"\n{DU_DEHORS}" if connues else ""
    if not recherche:
        categories: dict[str, int] = {}
        for a in tout:
            if not a["cle"]:
                categories[a["categorie"]] = categories.get(a["categorie"], 0) + 1
        return _texte(deja + f"{sum(categories.values())} API sans clé, par catégorie : "
                      + ", ".join(f"{c} ({k})" for c, k in categories.items())
                      + ". Cherche avec des mots-clés en anglais." + fin)
    trouvees = chercher(recherche, _entier(args.get("n"), 8, 1, 20), avec_cle)
    if not trouvees:
        return _texte(deja + f"Rien dans le catalogue pour « {recherche} »"
                      + ("" if avec_cle else " sans clé (essaie avec_cle=true)")
                      + ". Essaie d'autres mots en anglais, ou WebSearch « free API " + recherche + " »." + fin)
    connus, cles = carnet(), set(cles_rangees())
    lignes = [(a, *_ce_quil_en_sait(a, connus, cles)) for a in trouvees]
    lignes.sort(key=lambda t: bool(t[1] and en_panne(t[1])))   # celles en panne à la fin (tri stable)
    return _texte(deja + f"API {'gratuites' if avec_cle else 'gratuites sans clé'} pour « {recherche} » :\n"
                  + "\n".join(_ligne_api(*t) for t in lignes) + f"\n{DU_DEHORS}")


# --- Les clés gratuites, dans le Trousseau ------------------------------------------------------------------
# Rangées comme des mots de passe génériques (service « jervis-api:<domaine> », compte « Steve ») : ni dans un
# fichier, ni dans sa mémoire, ni dans la conversation. espace/cles_api.json ne garde que le nom des sites.

_CLE = re.compile(r"[A-Za-z0-9_\-.:+/=~]{8,200}")
_NOM_PARAM = re.compile(r"[A-Za-z0-9_.\-\[\]]{1,40}")
_NOM_ENTETE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,39}")
_PREFIXE = re.compile(r"[A-Za-z]{0,12}")
ENTETES_INTERDITS = {"host", "cookie", "content-length", "content-type", "transfer-encoding", "connection",
                     "user-agent", "accept", "proxy-authorization"}
MASQUE = "•••"


async def _trousseau_ranger(site: str, secret: str) -> str | None:
    """Range la clé (la remplace si elle y est). None si c'est fait. Le secret passe par l'entrée standard."""
    commande = (f'add-generic-password -U -s "jervis-api:{site}" -a "Steve" -l "Clé API {site} (Steve)" '
                f'-j "Rangée par Steve le {datetime.now():%d/%m/%Y}" -w "{secret}"\n')
    code, sortie = await comptes._lancer("security", "-i", entree=commande)
    return None if not code and "returned" not in sortie else (sortie[:200] or f"security a répondu {code}")


async def _trousseau_retrouver(site: str) -> str | None:
    code, sortie = await comptes._lancer("security", "find-generic-password", "-s", f"jervis-api:{site}",
                                         "-a", "Steve", "-w")
    return sortie if not code and sortie else None


async def _presse_papiers() -> str | None:
    code, sortie = await comptes._lancer("pbpaste")
    return None if code else sortie


async def _vider_presse_papiers(secret: str) -> None:
    if await _presse_papiers() == secret:   # seulement si c'est toujours la clé : rien d'autre n'est effacé
        await comptes._lancer("pbcopy", entree="")


def cles_rangees() -> dict[str, Any]:
    return _lire_json(CLES)


def site_de_la_cle(d: str) -> str | None:
    """Le domaine auquel lier une clé : celui de la doc d'une API du catalogue, ou un de ses sous-domaines
    (« api.x.org » pour une doc sur « x.org »). Jamais plus large que la doc (un parent pourrait couvrir les sites
    d'autres gens), et jamais une doc posée chez un hébergeur (github.com, rapidapi.com…) : la clé y couvrirait les
    projets de n'importe qui."""
    if d in HEBERGEURS:
        return None
    for a in catalogue():
        doc = comptes.domaine(a["lien"])
        if doc and doc not in HEBERGEURS and _couvre(doc, d):
            return d
    return None


@tool("ranger_cle_api", "Range dans le Trousseau la clé gratuite d'une API (celle qu'un site donne après "
      "l'inscription), pour t'en servir ensuite avec appeler_api. D'abord clique sur le bouton « copier » de la clé "
      "sur le site (ou sélectionne-la et cmd+c) : l'outil la prend dans le presse-papiers, la range, puis l'en "
      "retire. Ne tape jamais la clé ailleurs, ne l'écris nulle part. `site` = le domaine de l'API (« "
      "openweathermap.org ») : la clé ne partira que vers lui et ses sous-domaines.",
      {"type": "object", "properties": {"site": {"type": "string"}}, "required": ["site"]})
async def ranger_cle_api(args: dict[str, Any]) -> dict[str, Any]:
    d = comptes.domaine(args.get("site"))
    if not d:
        return _erreur("Donne le domaine de l'API (« openweathermap.org »).")
    if not (site := site_de_la_cle(d)):
        return _erreur(f"{d} n'est pas le site d'une API du catalogue (chercher_api avec_cle=true) : je n'y lie "
                       "pas de clé.")
    secret = await _presse_papiers()
    if not secret or not _CLE.fullmatch(secret):
        return _erreur("Le presse-papiers ne contient pas une clé d'API (8 à 200 caractères, sans espace) : clique "
                       "d'abord sur le bouton « copier » de la clé sur le site, puis recommence.")
    if probleme := await _trousseau_ranger(site, secret):
        return _erreur(f"Le Trousseau a refusé ({probleme}).")
    await _vider_presse_papiers(secret)
    cles = cles_rangees()
    cles[site] = {"date": datetime.now().isoformat(timespec="minutes")}
    _ecrire_json(CLES, cles)
    return _texte(f"Clé de {site} rangée dans le Trousseau, et retirée du presse-papiers. Pour t'en servir : "
                  "appeler_api avec cle={\"param\": \"<son nom dans la doc>\"} ou cle={\"entete\": \"<nom>\"} "
                  "(et \"prefixe\": \"Bearer \" si la doc le dit).")


# --- Appeler une API ----------------------------------------------------------------------------------------

class Interdit(Exception):
    """Une adresse où il ne va pas (le Mac, ton réseau, un mot de passe dans l'adresse…)."""


async def _adresses(hote: str, port: int) -> list[str]:
    """Les adresses IP de `hote` (les tests la remplacent : aucun DNS)."""
    infos_ip = await asyncio.get_running_loop().getaddrinfo(hote, port, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos_ip]


async def verifier_adresse(url: str) -> str:
    """L'adresse IP publique où aller pour `url` ; lève Interdit si elle ne mène pas à un serveur public d'Internet.
    La requête part ensuite vers cette adresse-là, pas vers le nom : un nom qui changerait d'adresse entre la
    vérification et l'envoi (« DNS rebinding ») ne peut pas le ramener sur le Mac."""
    try:
        morceaux = urlsplit(url)
        port = morceaux.port or (443 if morceaux.scheme == "https" else 80)
    except ValueError:
        raise Interdit("Cette adresse est mal formée.") from None
    if morceaux.scheme not in ("https", "http"):
        raise Interdit("Seulement des adresses http:// ou https://.")
    if morceaux.username or morceaux.password:
        raise Interdit("Pas d'identifiant ni de mot de passe dans l'adresse.")
    hote = (morceaux.hostname or "").rstrip(".").casefold()
    if not hote or hote == "localhost" or hote.endswith((".localhost", ".local", ".internal", ".lan", ".home",
                                                        ".arpa")) or "." not in hote and ":" not in hote:
        raise Interdit("Je ne vais que sur Internet, jamais sur le Mac ni sur ton réseau.")
    try:
        ips = [ipaddress.ip_address(hote)]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(ip.split("%")[0]) for ip in await _adresses(hote, port)]
        except OSError:
            raise httpx.ConnectError(f"{hote} est introuvable") from None
    if not ips or not all(ip.is_global and not ip.is_multicast for ip in ips):
        raise Interdit("Je ne vais que sur Internet, jamais sur le Mac ni sur ton réseau.")
    return str(ips[0])


def _elaguer(x: Any, profondeur: int = 0) -> Any:
    """Le JSON raccourci pour être lu : 15 éléments par liste, 500 caractères par texte, 8 niveaux."""
    if profondeur >= 8:
        return "…"
    if isinstance(x, dict):
        return {k: _elaguer(v, profondeur + 1) for k, v in list(x.items())[:60]} | (
            {"…": f"{len(x) - 60} autres champs"} if len(x) > 60 else {})
    if isinstance(x, list):
        return [_elaguer(v, profondeur + 1) for v in x[:15]] + ([f"… et {len(x) - 15} de plus"] if len(x) > 15 else [])
    if isinstance(x, str) and len(x) > 500:
        return x[:500] + "…"
    return x


def _lisible(corps: bytes, type_: str) -> str:
    """La réponse en texte : JSON résumé, HTML sans balises, texte tel quel ; un fichier binaire n'est que décrit."""
    texte = corps.decode("utf-8", errors="replace")
    if "json" in type_ or texte.lstrip()[:1] in ("{", "["):
        try:
            return json.dumps(_elaguer(json.loads(texte)), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    if "html" in type_:
        texte = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", texte)
        texte = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", texte)).split())
        return "(Une page web, pas une API : pour la lire, WebFetch fait mieux.) " + texte
    if type_ and not type_.startswith("text/") and not any(t in type_ for t in ("xml", "csv", "javascript", "yaml")):
        return f"(Un fichier {type_} de {len(corps)} octets : pas du texte, je ne peux pas le lire ici.)"
    return texte


class Cle:
    """Une clé rangée, et comment la donner : un paramètre de l'adresse ou un en-tête. Elle ne part que vers son
    site (ou un de ses sous-domaines), en https."""

    def __init__(self, site: str, secret: str, param: str = "", entete: str = "", prefixe: str = ""):
        self.site, self.secret, self.param, self.entete, self.prefixe = site, secret, param, entete, prefixe

    def couvre(self, url: httpx.URL) -> bool:
        return _couvre(self.site, (url.host or "").casefold())

    def masquer(self, texte: str) -> str:
        """La clé n'apparaît jamais dans ce qu'il lit, même recopiée par l'API (« your key XYZ is invalid »)."""
        for forme in {self.secret, quote(self.secret, safe=""), quote(self.secret)}:
            texte = texte.replace(forme, MASQUE)
        return texte


async def appeler(url: str, params: dict[str, Any] | None = None, cle: Cle | None = None,
                  graphql: dict[str, Any] | None = None) -> tuple[int, str, str]:
    """GET `url` (ses paramètres ajoutés), ou POST de la requête `graphql`, en suivant au plus 3 redirections,
    chacune vérifiée. Chaque requête part vers l'adresse IP vérifiée (le nom reste dans l'en-tête Host et dans le
    certificat TLS demandé), et jamais par un proxy réglé dans l'environnement. Renvoie (code, texte, type)."""
    async with httpx.AsyncClient(timeout=config.INFOS_DELAI_S, follow_redirects=False, transport=infos.TRANSPORT,
                                 trust_env=False,
                                 headers={"User-Agent": infos.AGENT, "Accept": "application/json, */*;q=0.5"}) as client:
        for _ in range(REDIRECTIONS_MAX + 1):
            ip = await verifier_adresse(url)
            demande = httpx.URL(url).copy_merge_params(params) if params else httpx.URL(url)
            entetes = {"Host": demande.netloc.decode("ascii")}
            if cle and cle.couvre(demande):
                if demande.scheme != "https":
                    raise Interdit("Ta clé ne part qu'en https.")
                if cle.param:
                    demande = demande.copy_merge_params({cle.param: cle.secret})
                else:
                    entetes[cle.entete] = cle.prefixe + cle.secret
            elif cle and cle.masquer(str(demande)) != str(demande):
                raise Interdit("Cette redirection emporterait ta clé vers un autre site : je m'arrête là.")
            requete = client.build_request(
                "POST" if graphql else "GET", demande.copy_with(host=ip), headers=entetes, json=graphql,
                extensions={"sni_hostname": demande.host} if demande.scheme == "https" else {})
            r = await client.send(requete, stream=True)
            try:
                if r.is_redirect and r.headers.get("location"):
                    if graphql:
                        raise Interdit("Cette API GraphQL redirige ailleurs : donne-moi directement son adresse finale.")
                    url, params = urljoin(str(demande), r.headers["location"]), None
                    continue
                corps = b""
                async for morceau in r.aiter_bytes():
                    corps += morceau
                    if len(corps) >= OCTETS_MAX:
                        break
                type_ = r.headers.get("content-type", "").casefold()
                texte = _lisible(corps[:OCTETS_MAX], type_)
                return r.status_code, cle.masquer(texte) if cle else texte, type_
            finally:
                await r.aclose()
    raise Interdit(f"Plus de {REDIRECTIONS_MAX} redirections : je m'arrête là.")


# Les réponses récentes, resservies telles quelles (Claude rappelle souvent la même adresse dans une conversation).
# Seulement les réussites, APIS_CACHE_S secondes, 100 au plus. Jamais une clé dedans : le texte est déjà masqué.
_memoire: dict[str, tuple[float, str]] = {}
MEMOIRE_MAX = 100


def _de_memoire(cle_memoire: str) -> tuple[float, str] | None:
    trouve = _memoire.get(cle_memoire)
    if trouve and time.monotonic() - trouve[0] < config.APIS_CACHE_S:
        return trouve
    _memoire.pop(cle_memoire, None)
    return None


def _garder(cle_memoire: str, texte: str) -> None:
    if config.APIS_CACHE_S <= 0:
        return
    _memoire.pop(cle_memoire, None)
    _memoire[cle_memoire] = (time.monotonic(), texte)
    while len(_memoire) > MEMOIRE_MAX:
        del _memoire[next(iter(_memoire))]


_MODIFIE = re.compile(r"\b(?:mutation|subscription)\b")
# Le POST de GraphQL ne part que vers une adresse qui le dit (« /graphql », « /v1/gql », « graphql.x.org ») : jamais un
# POST vers une API ordinaire, où il pourrait créer ou changer quelque chose.
_GQL = {"graphql", "gql"}


def adresse_graphql(url: str) -> bool:
    """Un segment entier du chemin, ou le premier mot du nom, vaut « graphql » ou « gql ». Le chemin est lu comme
    httpx l'enverra (décodé, « .. » résolus), et un « . » ou « .. » qui y resterait (« %2e%2e ») est refusé : le
    serveur pourrait le résoudre ailleurs."""
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    segments = u.path.split("/")
    if {".", ".."} & set(segments):
        return False
    return bool(_GQL & {s.casefold() for s in segments}) or u.host.split(".")[0].casefold() in _GQL


def _facon_de_donner(cle: Any) -> dict[str, str] | str:
    """`cle` vérifiée : {"param": nom} ou {"entete": nom, "prefixe": …}. Un texte d'erreur sinon."""
    if not isinstance(cle, dict):
        return "`cle` est un objet : {\"param\": \"appid\"} ou {\"entete\": \"X-Api-Key\"}."
    param, entete, prefixe = (str(cle.get(k) or "").strip() for k in ("param", "entete", "prefixe"))
    if bool(param) == bool(entete):
        return "`cle` dit soit \"param\" (son nom dans l'adresse), soit \"entete\" (le nom de l'en-tête)."
    if param and not _NOM_PARAM.fullmatch(param):
        return "Ce nom de paramètre n'est pas valable."
    if entete and (not _NOM_ENTETE.fullmatch(entete) or entete.casefold() in ENTETES_INTERDITS):
        return "Cet en-tête n'est pas valable pour une clé."
    if not _PREFIXE.fullmatch(prefixe):
        return "Le préfixe est un mot court (« Bearer », « Token »)."
    return {"param": param} if param else {"entete": entete} | ({"prefixe": prefixe + " "} if prefixe else {})


@tool("appeler_api", "Appelle une API publique et rend sa réponse lisible : le JSON résumé (15 éléments par liste), "
      "le reste en texte. `url` = l'adresse complète trouvée dans sa doc (https://…) ; `params` = les paramètres de "
      "la requête (facultatif, {\"q\": \"…\"}) ; `pour` = à quoi elle sert, en quelques mots (« position de l'ISS ») : "
      "il le note dans son carnet pour la retrouver. Une API GraphQL : `graphql` = la requête (lecture seulement, "
      "pas de mutation), `variables` = ses variables. Une API à clé gratuite déjà rangée (ranger_cle_api) : `cle` = "
      "{\"param\": \"appid\"} ou {\"entete\": \"X-Api-Key\", \"prefixe\": \"Bearer \"} selon sa doc ; la clé est "
      "ajoutée seule, ne l'écris jamais toi-même. Pour une API trouvée avec chercher_api, ou que tu connais (une API "
      "ouverte de l'État, d'une ville, d'un musée…). Jamais sur le Mac ni sur son réseau, et ne mets dans l'adresse "
      "rien de lui (mail, adresse, mémoire) qu'il ne t'ait demandé de chercher.",
      {"type": "object",
       "properties": {"url": {"type": "string"}, "params": {"type": "object"}, "pour": {"type": "string"},
                      "graphql": {"type": "string"}, "variables": {"type": "object"}, "cle": {"type": "object"}},
       "required": ["url"]})
async def appeler_api(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    params = args.get("params") or None
    if params is not None and not isinstance(params, dict):
        return _erreur("`params` est un objet ({\"nom\": \"valeur\"}).")
    if params:
        params = {str(k): v if isinstance(v, (str, int, float)) else json.dumps(v, ensure_ascii=False)
                  for k, v in params.items()}
    graphql = None
    if requete_gql := str(args.get("graphql") or "").strip():
        variables = args.get("variables") or {}
        if not isinstance(variables, dict):
            return _erreur("`variables` est un objet ({\"nom\": \"valeur\"}).")
        if not adresse_graphql(url):
            return _erreur("Refusé : GraphQL seulement vers une adresse GraphQL (« …/graphql », « …/gql ») ; "
                           "regarde dans sa doc l'adresse exacte.")
        if _MODIFIE.search(requete_gql):
            return _erreur("Refusé : seulement des lectures en GraphQL (query), pas de mutation ni d'abonnement.")
        if len(requete_gql) > 10_000:
            return _erreur("Cette requête GraphQL est trop longue.")
        graphql = {"query": requete_gql} | ({"variables": variables} if variables else {})
    facon, cle = None, None
    if args.get("cle"):
        if isinstance(facon := _facon_de_donner(args["cle"]), str):
            return _erreur(facon)
        hote = (urlsplit(url).hostname or "").casefold() if url.startswith(("http://", "https://")) else ""
        site = max((s for s in cles_rangees() if _couvre(s, hote)), key=len, default=None)
        if not site:
            return _erreur(f"Pas de clé rangée pour {hote or 'cette adresse'} : crée le compte gratuit, copie la "
                           "clé, puis ranger_cle_api.")
        if not (secret := await _trousseau_retrouver(site)):
            return _erreur(f"La clé de {site} n'est plus dans le Trousseau : range-la à nouveau (ranger_cle_api).")
        cle = Cle(site, secret, facon.get("param", ""), facon.get("entete", ""), facon.get("prefixe", ""))

    cle_memoire = json.dumps([url, params, graphql, facon], sort_keys=True, ensure_ascii=False)
    if gardee := _de_memoire(cle_memoire):
        return _texte(f"{gardee[1]}\n(Réponse d'il y a {time.monotonic() - gardee[0]:.0f} s.)\n{DU_DEHORS}")
    try:
        code, texte, type_ = await appeler(url, params, cle, graphql)
    except Interdit as e:
        return _erreur(f"Refusé : {e}")
    except httpx.TimeoutException:
        noter_panne(url)
        return _erreur(f"L'API n'a pas répondu en {config.INFOS_DELAI_S:g} secondes.")
    except httpx.HTTPError as e:
        noter_panne(url)
        return _erreur(f"L'API est injoignable ({type(e).__name__}).")
    texte = texte if len(texte) <= CARACTERES_MAX else texte[:CARACTERES_MAX] + f"\n… (coupé à {CARACTERES_MAX} caractères)"
    if code >= 400:
        if code >= 500:
            noter_panne(url)
        return _erreur(f"L'API a répondu {code}. " + (f"Ce qu'elle dit : {texte[:600]}" if texte.strip() else "")
                       + ("\nUne clé est sans doute nécessaire : prends-en une autre (chercher_api), ou crée le "
                          "compte gratuit et range sa clé (ranger_cle_api)." if code in (401, 403) else ""))
    if 200 <= code < 300:
        _garder(cle_memoire, texte)
        if "html" not in type_:
            noter_succes(url, params, str(args.get("pour") or ""), facon, graphql is not None)
    return _texte(f"{texte}\n{DU_DEHORS}")


OUTILS_APIS = [chercher_api, appeler_api, ranger_cle_api]


if __name__ == "__main__":
    try:
        print(f"Catalogue à jour : {mettre_a_jour()} API ({CATALOGUE}).")
    except (httpx.HTTPError, ValueError) as e:
        sys.exit(f"Catalogue non mis à jour : {e}")
