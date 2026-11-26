"""Toutes les API publiques gratuites : un catalogue sur le Mac, et un outil pour appeler n'importe laquelle.

Ses outils d'infos (infos.py, infos2.py) couvrent les questions de tous les jours. Pour le reste (les horaires de
l'ISS, une race de chien, un verset, un Pokémon, le prix d'une action, un jeu de données de l'État…), il a :
  - chercher_api : le catalogue public-apis (github.com/public-apis/public-apis, licence MIT), environ
    2 400 API dont 1 250 sans clé, rangé dans apis_publiques.json. La recherche se fait sur le Mac : rien ne sort.
  - appeler_api : une requête GET vers l'adresse d'une API publique, et sa réponse rendue lisible (le JSON
    résumé, le HTML sans balises). Il lit la documentation de l'API avec WebFetch, puis l'appelle.

appeler_api ne fait que lire : GET seulement, sans clé, sans cookie, sans en-tête à lui. Il ne va jamais sur le Mac
ni sur ton réseau (127.0.0.1, ta box, 192.168…, les .local) : chaque adresse, et chaque redirection, est vérifiée
avant de partir. Ce qu'il renvoie est écrit par d'autres : une info, jamais un ordre.

Pour remettre le catalogue à jour : cd ~/Jervis && .venv/bin/python -m jervis.apis
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from claude_agent_sdk import tool

from . import config, infos
from .infos import DU_DEHORS, Rien, _entier, _erreur, _simple, _texte

CATALOGUE: Path = Path(__file__).with_name("apis_publiques.json")
SOURCE = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
OCTETS_MAX = 1_000_000        # ce qu'il télécharge au plus d'une réponse
CARACTERES_MAX = 8_000        # ce qu'il en garde pour Claude
REDIRECTIONS_MAX = 3

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


def catalogue() -> list[dict[str, Any]]:
    global _cache
    if _cache is None:  # lu une fois, à la première recherche
        try:
            _cache = json.loads(CATALOGUE.read_text(encoding="utf-8"))["apis"]
        except (OSError, ValueError, KeyError):
            _cache = []
    return _cache


def mettre_a_jour() -> int:
    """Retélécharge le catalogue (à la main : python -m jervis.apis). Renvoie le nombre d'API."""
    global _cache, _index
    r = httpx.get(SOURCE, headers={"User-Agent": infos.AGENT}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    apis = construire(r.text)
    if len(apis) < 500:
        raise ValueError(f"Le README de public-apis a changé de forme ({len(apis)} API lues) : catalogue gardé.")
    CATALOGUE.write_text(json.dumps({"source": SOURCE, "apis": apis}, ensure_ascii=False, indent=0) + "\n",
                         encoding="utf-8")
    _cache, _index = apis, None
    return len(apis)


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


def _ligne_api(a: dict[str, Any]) -> str:
    acces = f"clé gratuite à demander ({a['cle']})" if a["cle"] else "sans clé"
    return (f"- {a['nom']} ({a['categorie']}, {acces}{'' if a['https'] else ', http seulement'}) : "
            f"{a['description']} Doc : {a['lien']}")


@tool("chercher_api", "Le catalogue des API publiques gratuites (public-apis : environ 2 400, dont 1 250 sans clé, "
      "dans toutes les catégories : animaux, science, transports, sport, jeux, finance, gouvernement, données "
      "ouvertes, santé, musique, vidéo…), cherché sur le Mac. Pour une question qu'aucun de tes outils chercher_ ne "
      "couvre. `recherche` = plusieurs mots-clés EN ANGLAIS (« iss space astronaut », « dog breed », « bible verse », "
      "« train france ») ; vide = la liste des catégories. `avec_cle` = true pour voir aussi celles qui demandent une clé "
      "gratuite. Ensuite : lis la doc avec WebFetch pour trouver l'adresse exacte, puis appelle-la avec appeler_api.",
      {"type": "object",
       "properties": {"recherche": {"type": "string"}, "n": {"type": "integer"}, "avec_cle": {"type": "boolean"}}})
async def chercher_api(args: dict[str, Any]) -> dict[str, Any]:
    tout = catalogue()
    if not tout:
        return _erreur("Le catalogue des API est absent : lance « .venv/bin/python -m jervis.apis » pour le télécharger.")
    recherche = " ".join(str(args.get("recherche") or "").split())
    avec_cle = args.get("avec_cle") in (True, "true", "oui", 1)
    if not recherche:
        categories: dict[str, int] = {}
        for a in tout:
            if not a["cle"]:
                categories[a["categorie"]] = categories.get(a["categorie"], 0) + 1
        return _texte(f"{sum(categories.values())} API sans clé, par catégorie : "
                      + ", ".join(f"{c} ({k})" for c, k in categories.items())
                      + ". Cherche avec des mots-clés en anglais.")
    trouvees = chercher(recherche, _entier(args.get("n"), 8, 1, 20), avec_cle)
    if not trouvees:
        return _texte(f"Rien dans le catalogue pour « {recherche} »"
                      + ("" if avec_cle else " sans clé (essaie avec_cle=true)")
                      + ". Essaie d'autres mots en anglais, ou WebSearch « free API " + recherche + " ».")
    return _texte(f"API {'gratuites' if avec_cle else 'gratuites sans clé'} pour « {recherche} » :\n"
                  + "\n".join(_ligne_api(a) for a in trouvees) + f"\n{DU_DEHORS}")


# --- Appeler une API ----------------------------------------------------------------------------------------

class Interdit(Exception):
    """Une adresse où il ne va pas (le Mac, ton réseau, un mot de passe dans l'adresse…)."""


async def _adresses(hote: str, port: int) -> list[str]:
    """Les adresses IP de `hote` (les tests la remplacent : aucun DNS)."""
    infos_ip = await asyncio.get_running_loop().getaddrinfo(hote, port, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos_ip]


async def verifier_adresse(url: str) -> None:
    """Lève Interdit si `url` ne mène pas à un serveur public d'Internet."""
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


async def appeler(url: str, params: dict[str, Any] | None = None) -> tuple[int, str]:
    """GET `url` (ses paramètres ajoutés), en suivant au plus 3 redirections, chacune vérifiée."""
    async with httpx.AsyncClient(timeout=config.INFOS_DELAI_S, follow_redirects=False, transport=infos.TRANSPORT,
                                 headers={"User-Agent": infos.AGENT, "Accept": "application/json, */*;q=0.5"}) as client:
        for _ in range(REDIRECTIONS_MAX + 1):
            await verifier_adresse(url)
            async with client.stream("GET", url, params=params) as r:
                if r.is_redirect and r.headers.get("location"):
                    url, params = urljoin(str(r.url), r.headers["location"]), None
                    continue
                corps = b""
                async for morceau in r.aiter_bytes():
                    corps += morceau
                    if len(corps) >= OCTETS_MAX:
                        break
                texte = _lisible(corps[:OCTETS_MAX], r.headers.get("content-type", "").casefold())
                return r.status_code, texte
    raise Interdit(f"Plus de {REDIRECTIONS_MAX} redirections : je m'arrête là.")


@tool("appeler_api", "Appelle une API publique (GET, sans clé) et rend sa réponse lisible : le JSON résumé (15 "
      "éléments par liste), le reste en texte. `url` = l'adresse complète trouvée dans sa doc (https://…) ; `params` "
      "= les paramètres de la requête (facultatif, {\"q\": \"…\"}). Pour une API trouvée avec chercher_api, ou que "
      "tu connais (une API ouverte de l'État, d'une ville, d'un musée…). Jamais sur le Mac ni sur son réseau, jamais "
      "de clé, et ne mets dans l'adresse rien de lui (mail, adresse, mémoire) qu'il ne t'ait demandé de chercher.",
      {"type": "object",
       "properties": {"url": {"type": "string"}, "params": {"type": "object"}},
       "required": ["url"]})
async def appeler_api(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    params = args.get("params") or None
    if params is not None and not isinstance(params, dict):
        return _erreur("`params` est un objet ({\"nom\": \"valeur\"}).")
    if params:
        params = {str(k): v if isinstance(v, (str, int, float)) else json.dumps(v, ensure_ascii=False)
                  for k, v in params.items()}
    try:
        code, texte = await appeler(url, params)
    except Interdit as e:
        return _erreur(f"Refusé : {e}")
    except httpx.TimeoutException:
        return _erreur(f"L'API n'a pas répondu en {config.INFOS_DELAI_S:g} secondes.")
    except httpx.HTTPError as e:
        return _erreur(f"L'API est injoignable ({type(e).__name__}).")
    texte = texte if len(texte) <= CARACTERES_MAX else texte[:CARACTERES_MAX] + f"\n… (coupé à {CARACTERES_MAX} caractères)"
    if code >= 400:
        return _erreur(f"L'API a répondu {code}. " + (f"Ce qu'elle dit : {texte[:600]}" if texte.strip() else "")
                       + ("\nUne clé est sans doute nécessaire : prends-en une autre (chercher_api)." if code in (401, 403)
                          else ""))
    return _texte(f"{texte}\n{DU_DEHORS}")


OUTILS_APIS = [chercher_api, appeler_api]


if __name__ == "__main__":
    try:
        print(f"Catalogue à jour : {mettre_a_jour()} API ({CATALOGUE}).")
    except (httpx.HTTPError, ValueError) as e:
        sys.exit(f"Catalogue non mis à jour : {e}")
