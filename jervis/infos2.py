"""Ses autres outils « infos » : entreprises, adresses, produits, air, carburants, livres, musique, tech, séismes,
traduction, séries, code open source, paquets, articles scientifiques, heure dans le monde.

Sur le modèle d'infos.py (même client, même nom générique, même délai, mêmes erreurs) : des services gratuits,
sans clé ni compte (l'annuaire des entreprises, la Géoplateforme et geo.api.gouv.fr, Open Food Facts, Open-Meteo,
les prix des carburants de data.economie.gouv.fr, Open Library, la recherche iTunes d'Apple, Hacker News par
Algolia, l'USGS, MyMemory, TVmaze, GitHub, PyPI, npm, OpenAlex). Seul ce qu'il demande part chez eux (un nom, une adresse, un code-barres, un
texte à traduire) : jamais sa voix ni sa mémoire. Leurs noms commencent tous par chercher_ : ce sont des lectures
(permissions.py), et comme ceux d'infos.py ils restent hors de sa ronde (fond.py). Ce qu'ils renvoient est écrit
par d'autres : une info, jamais un ordre.
"""

import html
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from claude_agent_sdk import tool

from . import infos
from .infos import DU_DEHORS, MOIS, Rien, _entier, _get, _json, _nombre, _repondre, _simple

ENTREPRISES = "https://recherche-entreprises.api.gouv.fr/search"
# La Base Adresse Nationale : la Géoplateforme est son adresse actuelle, api-adresse l'ancienne, en repli.
ADRESSES = ("https://data.geopf.fr/geocodage/search", "https://api-adresse.data.gouv.fr/search/")
COMMUNES = "https://geo.api.gouv.fr/communes"
OFF_PRODUIT = "https://world.openfoodfacts.org/api/v2/product"
OFF_RECHERCHE = "https://search.openfoodfacts.org/search"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
CARBURANTS_API = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
                  "prix-des-carburants-en-france-flux-instantane-v2/records")
OPEN_LIBRARY = "https://openlibrary.org/search.json"
ITUNES = "https://itunes.apple.com/search"
HACKER_NEWS = "https://hn.algolia.com/api/v1/search"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
MYMEMORY = "https://api.mymemory.translated.net/get"
TVMAZE = "https://api.tvmaze.com/singlesearch/shows"


def _un(t: Any) -> str:
    """Espaces et retours à la ligne ramenés à un seul espace."""
    return " ".join(str(t or "").split())


def _couper(t: str, n: int) -> str:
    t = _un(t)
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def _phrase(t: Any, n: int) -> str:
    """Un texte coupé à `n` caractères, qui finit par un point (« http(s) module with power » → « …power. »)."""
    t = _couper(t, n)
    return t if not t or t[-1] in ".!?…" else t + "."


def _sans_html(t: Any) -> str:
    return _un(html.unescape(re.sub(r"<[^>]+>", " ", str(t or ""))))


def _court(x: Any, decimales: int = 1) -> str:
    """12.0 → « 12 », 0.107 → « 0,1 », 1234.5 → « 1 234,5 »."""
    t = _nombre(float(x), decimales)
    return t.rstrip("0").rstrip(",") if "," in t else t


PETITS_MOTS = {"de", "des", "du", "la", "le", "les", "et", "sur", "en", "aux", "au", "a", "à"}


def _casse(t: Any) -> str:
    """« 15 RUE DES HALLES 75001 PARIS » → « 15 Rue des Halles 75001 Paris » (moins dur à lire, et à dire)."""
    mots = _un(t).lower().split(" ")
    return " ".join(m if (m in PETITS_MOTS and i) else "-".join(p[:1].upper() + p[1:] for p in m.split("-"))
                    for i, m in enumerate(mots))


def _majuscule(t: str) -> str:
    """La première lettre en majuscule, sans toucher aux autres (« PME », « NOVA », « PM2,5 » restent tels quels)."""
    return t[:1].upper() + t[1:]


def _de(langue: str) -> str:
    """« du français », « de l'anglais »."""
    return f"de l'{langue}" if langue[:1] in "aeiouyéèh" else f"du {langue}"


def _vers(langue: str) -> str:
    """« vers le français », « vers l'anglais »."""
    return f"vers l'{langue}" if langue[:1] in "aeiouyéèh" else f"vers le {langue}"


def _quel_jour(d: date) -> str:
    """« aujourd'hui », « hier », « demain », sinon « vendredi 4 décembre » (avec l'année si ce n'est pas celle-ci)."""
    aujourdhui = infos._aujourdhui()
    ecart = (d - aujourdhui).days
    if ecart in (-1, 0, 1):
        return {-1: "hier", 0: "aujourd'hui", 1: "demain"}[ecart]
    return infos._jour(d) + (f" {d.year}" if d.year != aujourdhui.year else "")


def _date_longue(iso: Any) -> str:
    """« 2023-04-28 » → « 28 avril 2023 »."""
    d = date.fromisoformat(str(iso)[:10])
    return f"{'1er' if d.day == 1 else d.day} {MOIS[d.month - 1]} {d.year}"


def _instant(iso: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- Les entreprises (l'annuaire des entreprises de l'État) -------------------------------------------------

# Les sections de la nomenclature d'activités (NAF), telles que l'annuaire les donne.
SECTIONS = {
    "A": "agriculture, sylviculture et pêche", "B": "industries extractives", "C": "industrie manufacturière",
    "D": "production d'électricité et de gaz", "E": "eau, déchets et dépollution", "F": "construction",
    "G": "commerce et réparation d'automobiles", "H": "transports et entreposage", "I": "hébergement et restauration",
    "J": "information et communication", "K": "finance et assurance", "L": "immobilier",
    "M": "activités spécialisées, scientifiques et techniques", "N": "services administratifs et de soutien",
    "O": "administration publique", "P": "enseignement", "Q": "santé humaine et action sociale",
    "R": "arts, spectacles et loisirs", "S": "autres services", "T": "ménages employeurs",
    "U": "organisations extraterritoriales",
}
# Les tranches d'effectif salarié de l'INSEE.
EFFECTIFS = {
    "00": "aucun salarié", "01": "1 ou 2 salariés", "02": "3 à 5 salariés", "03": "6 à 9 salariés",
    "11": "10 à 19 salariés", "12": "20 à 49 salariés", "21": "50 à 99 salariés", "22": "100 à 199 salariés",
    "31": "200 à 249 salariés", "32": "250 à 499 salariés", "41": "500 à 999 salariés",
    "42": "1 000 à 1 999 salariés", "51": "2 000 à 4 999 salariés", "52": "5 000 à 9 999 salariés",
    "53": "10 000 salariés ou plus",
}
CATEGORIES = {"PME": "PME", "ETI": "entreprise de taille intermédiaire", "GE": "grande entreprise"}


def _siren(s: Any) -> str:
    s = str(s or "")
    return f"{s[:3]} {s[3:6]} {s[6:]}" if re.fullmatch(r"\d{9}", s) else s


def _dirigeant(d: dict[str, Any]) -> str:
    if d.get("type_dirigeant") == "personne morale":
        nom = d.get("denomination") or "?"
    else:
        prenom = (d.get("prenoms") or "").split(" ")[0]
        nom = _casse(f"{prenom} {d.get('nom') or ''}")
    return f"{nom} ({d['qualite']})" if d.get("qualite") else nom


async def entreprise(recherche: str) -> str:
    recherche = _un(recherche)
    if re.fullmatch(r"[\d ]{9,17}", recherche):   # un SIREN ou un SIRET dicté avec des espaces
        recherche = recherche.replace(" ", "")
    if len(recherche) < 3:
        raise Rien("Donne-moi le nom de l'entreprise (trois lettres au moins), ou son SIREN.")
    r = await _get(ENTREPRISES, {"q": recherche, "per_page": 3, "page": 1})
    if r.status_code == 400:
        raise Rien(f"L'annuaire des entreprises ne comprend pas « {recherche} » : donne un nom ou un SIREN.")
    r.raise_for_status()
    trouvees = r.json().get("results") or []
    if not trouvees:
        raise Rien(f"L'annuaire des entreprises ne trouve rien sous « {recherche} ».")
    e = trouvees[0]
    siege = e.get("siege") or {}
    etat = "en activité" if e.get("etat_administratif") == "A" else (
        f"fermée depuis le {_date_longue(e['date_fermeture'])}" if e.get("date_fermeture") else "fermée")
    phrase = f"Annuaire des entreprises : {e.get('nom_complet') or '?'}, SIREN {_siren(e.get('siren'))}, {etat}"
    if e.get("date_creation"):
        phrase += f", créée le {_date_longue(e['date_creation'])}"
    lignes = [phrase + "."]
    if siege.get("adresse"):
        lignes.append(f"Siège : {_casse(siege['adresse'])}.")
    activite = SECTIONS.get(e.get("section_activite_principale") or "")
    if activite:
        lignes.append(f"Activité : {activite} (code NAF {e.get('activite_principale')}).")
    taille = [t for t in (CATEGORIES.get(e.get("categorie_entreprise") or ""),
                          EFFECTIFS.get(e.get("tranche_effectif_salarie") or "")) if t]
    if taille:
        lignes.append(_majuscule(", ".join(taille)) + ".")
    dirigeants = [_dirigeant(d) for d in (e.get("dirigeants") or [])[:4]]
    if dirigeants:
        lignes.append("Dirigeants : " + ", ".join(dirigeants) + ".")
    autres = [f"{a.get('nom_complet') or '?'} (SIREN {_siren(a.get('siren'))}"
              + (f", {_casse((a.get('siege') or {}).get('libelle_commune'))})"
                 if (a.get("siege") or {}).get("libelle_commune") else ")") for a in trouvees[1:]]
    if autres:
        lignes.append("Autres entreprises trouvées : " + " ; ".join(autres) + ".")
    lignes.append(f"Lien : https://annuaire-entreprises.data.gouv.fr/entreprise/{e.get('siren')}")
    return "\n".join([*lignes, DU_DEHORS])


@tool("chercher_entreprise", "Une entreprise française dans l'annuaire officiel des entreprises : SIREN, état "
      "(en activité ou fermée), date de création, siège, activité, taille, dirigeants. `nom` = son nom (« Mistral "
      "AI », « boulangerie Dupont Lyon ») ou son SIREN/SIRET. S'il y en a plusieurs, la première est détaillée et "
      "les autres citées : demande-lui laquelle si ce n'est pas clair.",
      {"nom": str})
async def chercher_entreprise(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("L'annuaire des entreprises", entreprise(args.get("nom") or ""))


# --- Les adresses et les communes (Base Adresse Nationale, geo.api.gouv.fr) -------------------------------

async def _geocoder(texte: str) -> list[dict[str, Any]]:
    params = {"q": texte, "limit": 3}
    for i, adresse in enumerate(ADRESSES):
        try:
            r = await _get(adresse, params)
        except httpx.HTTPError:
            if i == len(ADRESSES) - 1:
                raise
            continue   # l'autre adresse de la Base Adresse Nationale
        if r.status_code >= 500 and i < len(ADRESSES) - 1:
            continue
        if r.status_code == 400:
            raise Rien(f"La Base Adresse Nationale ne comprend pas « {texte} » : donne une adresse ou une commune.")
        r.raise_for_status()
        return r.json().get("features") or []
    raise httpx.HTTPError("Base Adresse Nationale injoignable")


def _codes_postaux(codes: list[str]) -> str:
    if len(codes) == 1:
        return f"code postal {codes[0]}"
    if len(codes) <= 4:
        return "codes postaux " + ", ".join(codes)
    return f"{len(codes)} codes postaux, de {min(codes)} à {max(codes)}"


async def _commune(p: dict[str, Any]) -> str:
    try:
        c = await _json(f"{COMMUNES}/{p['citycode']}",
                        {"fields": "nom,codesPostaux,population,surface,departement,region"})
    except (httpx.HTTPError, ValueError):
        c = {}   # geo.api ne répond pas : ce que la Base Adresse sait suffit
    nom = c.get("nom") or p.get("city") or p.get("name")
    precision = ", ".join(x for x in ((c.get("departement") or {}).get("nom"), (c.get("region") or {}).get("nom")) if x)
    if not precision:
        precision = ", ".join((p.get("context") or "").split(", ")[1:])
    morceaux = []
    population = c.get("population") or p.get("population")
    if population:
        morceaux.append(f"{_nombre(float(population), 0)} habitants")
    codes = c.get("codesPostaux") or ([p["postcode"]] if p.get("postcode") else [])
    if codes:
        morceaux.append(_codes_postaux(sorted(codes)))
    if c.get("surface"):
        morceaux.append(f"{_court(float(c['surface']) / 100)} km²")   # geo.api la donne en hectares
    morceaux.append(f"code INSEE {p['citycode']}")
    return f"{nom}{f' ({precision})' if precision else ''} : " + ", ".join(morceaux) + "."


async def adresse(texte: str) -> str:
    texte = _un(texte)
    if len(texte) < 3:
        raise Rien("Donne-moi une adresse ou une commune (trois lettres au moins).")
    trouves = await _geocoder(texte)
    if not trouves:
        raise Rien(f"Je ne trouve pas d'adresse « {texte} » en France.")
    p = trouves[0]["properties"]
    if p.get("type") == "municipality":
        return await _commune(p)
    lon, lat = trouves[0]["geometry"]["coordinates"][:2]
    contexte = ", ".join((p.get("context") or "").split(", ")[1:])
    lignes = [f"{p['label']}{f' ({contexte})' if contexte else ''}. "
              f"Coordonnées : {round(float(lat), 5)}, {round(float(lon), 5)}."]
    if float(p.get("score") or 0) < 0.6:
        autres = [f["properties"]["label"] for f in trouves[1:]]
        lignes.append("Pas sûr que ce soit la bonne : vérifie avec lui."
                      + (" Autres possibilités : " + " ; ".join(autres) + "." if autres else ""))
    return "\n".join(lignes)


@tool("chercher_adresse", "Une adresse ou une commune en France (Base Adresse Nationale, geo.api.gouv.fr). "
      "`adresse` = une adresse (« 8 bd du Port Amiens ») : il la donne complète, avec son code postal et ses "
      "coordonnées ; ou une commune ou un code postal (« Brest », « 29200 ») : population, codes postaux, superficie, "
      "département, région. Pas pour l'étranger (c'est WebSearch).",
      {"adresse": str})
async def chercher_adresse(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("La Base Adresse Nationale", adresse(args.get("adresse") or ""))


# --- Les produits alimentaires (Open Food Facts) ----------------------------------------------------------

NOVA = {1: "brut ou peu transformé", 2: "ingrédient culinaire", 3: "transformé", 4: "ultra-transformé"}
ALLERGENES = {"milk": "lait", "gluten": "gluten", "nuts": "fruits à coque", "peanuts": "arachides", "soybeans": "soja",
              "eggs": "œufs", "fish": "poisson", "crustaceans": "crustacés", "molluscs": "mollusques",
              "celery": "céleri", "mustard": "moutarde", "sesame-seeds": "sésame", "lupin": "lupin",
              "sulphur-dioxide-and-sulphites": "sulfites"}
CHAMPS_PRODUIT = ("code,product_name,product_name_fr,brands,quantity,nutriscore_grade,nova_group,"
                  "ingredients_text_fr,ingredients_text,nutriments,allergens_tags")


def _marques(m: Any) -> str:
    """« Ferrero, Nutella » ; la recherche les donne en liste préfixée (« xx:ferrero »)."""
    if isinstance(m, list):
        m = ", ".join(str(x).split(":", 1)[-1] for x in m)
    return _un(m)


async def produit(recherche: str) -> str:
    recherche = _un(recherche)
    if not recherche:
        raise Rien("Dis-moi quel produit (son nom, ou les chiffres de son code-barres).")
    autres: list[dict[str, Any]] = []
    code = recherche.replace(" ", "")
    if not re.fullmatch(r"\d{8,14}", code):   # un nom : la recherche d'Open Food Facts trouve son code-barres
        trouves = (await _json(OFF_RECHERCHE, {"q": recherche, "page_size": 3, "langs": "fr",
                                               "fields": "code,product_name,brands"})).get("hits") or []
        if not trouves:
            raise Rien(f"Open Food Facts ne trouve pas de produit « {recherche} ».")
        code, autres = str(trouves[0]["code"]), trouves[1:]
    r = await _get(f"{OFF_PRODUIT}/{code}.json", {"fields": CHAMPS_PRODUIT})
    donnees = r.json() if r.status_code == 404 else None
    if donnees is None:
        r.raise_for_status()
        donnees = r.json()
    p = donnees.get("product")
    if donnees.get("status") != 1 or not p:
        raise Rien(f"Open Food Facts ne connaît pas le code-barres {code}.")
    nom = _un(p.get("product_name_fr") or p.get("product_name")) or "Produit sans nom"
    details = ", ".join(x for x in (_marques(p.get("brands")), _un(p.get("quantity"))) if x)
    lignes = [f"Open Food Facts : {nom}{f' ({details})' if details else ''}."]
    notes = []
    grade = str(p.get("nutriscore_grade") or "").lower()
    notes.append(f"Nutri-Score {grade.upper()}" if grade in {"a", "b", "c", "d", "e"} else "pas de Nutri-Score")
    if p.get("nova_group") in NOVA:
        notes.append(f"NOVA {p['nova_group']} ({NOVA[p['nova_group']]})")
    lignes.append(_majuscule(", ".join(notes)) + ".")
    n = p.get("nutriments") or {}
    pour_100 = [f"{_court(n[cle], d)} {unite}" for cle, d, unite in (
        ("energy-kcal_100g", 0, "kcal"), ("sugars_100g", 1, "g de sucres"),
        ("fat_100g", 1, "g de matières grasses"), ("salt_100g", 2, "g de sel")) if n.get(cle) is not None]
    if pour_100:
        lignes.append("Pour 100 g : " + ", ".join(pour_100) + ".")
    ingredients = p.get("ingredients_text_fr") or p.get("ingredients_text")
    if ingredients:
        lignes.append(f"Ingrédients : {_couper(ingredients, 250)}")
    allergenes = [ALLERGENES.get(a.split(":", 1)[-1], a.split(":", 1)[-1]) for a in p.get("allergens_tags") or []]
    if allergenes:
        lignes.append("Allergènes : " + ", ".join(allergenes) + ".")
    if autres:
        lignes.append("Autres produits trouvés : " + " ; ".join(
            _un(a.get("product_name")) + (f" ({_marques(a.get('brands'))})" if a.get("brands") else "")
            for a in autres if a.get("product_name")) + ".")
    lignes.append(f"Lien : https://fr.openfoodfacts.org/produit/{code}")
    return "\n".join([*lignes, DU_DEHORS])


@tool("chercher_produit", "Un produit alimentaire dans Open Food Facts (base collaborative) : Nutri-Score, NOVA "
      "(degré de transformation), calories, sucres, gras et sel pour 100 g, ingrédients, allergènes. `produit` = "
      "son nom et sa marque (« Nutella », « Prince de Lu ») ou les chiffres de son code-barres.",
      {"produit": str})
async def chercher_produit(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("Open Food Facts", produit(args.get("produit") or ""))


# --- La qualité de l'air, les pollens et le soleil (Open-Meteo) -------------------------------------------

POLLENS = {"alder_pollen": "aulne", "birch_pollen": "bouleau", "grass_pollen": "graminées",
           "mugwort_pollen": "armoise", "olive_pollen": "olivier", "ragweed_pollen": "ambroisie"}


def qualite_air(indice: float) -> str:
    """L'indice européen de qualité de l'air (EAQI) en mots : plus il est bas, mieux c'est."""
    for plafond, mot in ((20, "bonne"), (40, "correcte"), (60, "moyenne"), (80, "médiocre"), (100, "très mauvaise")):
        if indice <= plafond:
            return mot
    return "extrêmement mauvaise"


def niveau_uv(uv: float) -> str:
    for plafond, mot in ((3, "faible"), (6, "modéré"), (8, "élevé"), (11, "très élevé")):
        if uv < plafond:
            return mot
    return "extrême"


async def _lieu(lieu: str) -> tuple[float, float, str]:
    """Le géocodage d'Open-Meteo, comme pour la météo (infos.py)."""
    trouves = (await _json(infos.GEOCODAGE, {"name": lieu, "language": "fr", "count": 1, "format": "json"})).get("results")
    if not trouves:
        raise Rien(f"Je ne trouve pas de lieu nommé « {lieu} ».")
    e = trouves[0]
    precision = ", ".join(p for p in (e.get("admin1"), e.get("country")) if p and p != e["name"])
    return e["latitude"], e["longitude"], f"{e['name']} ({precision})" if precision else e["name"]


async def air(lieu: str) -> str:
    lieu = _un(lieu)
    if not lieu:
        raise Rien("Dis-moi pour quel lieu (une ville).")
    lat, lon, nom = await _lieu(lieu)
    c = (await _json(AIR, {"latitude": lat, "longitude": lon, "timezone": "auto",
                           "current": "european_aqi,pm2_5,pm10,ozone,nitrogen_dioxide," + ",".join(POLLENS)}))["current"]
    if c.get("european_aqi") is None:
        raise Rien(f"Open-Meteo n'a pas de mesure de la qualité de l'air pour {nom}.")
    indice = float(c["european_aqi"])
    polluants = [f"{mot} {round(float(c[cle]))} µg/m³" for cle, mot in (
        ("pm2_5", "particules fines PM2,5"), ("pm10", "PM10"), ("ozone", "ozone"),
        ("nitrogen_dioxide", "dioxyde d'azote")) if c.get(cle) is not None]
    lignes = [f"{nom} : qualité de l'air {qualite_air(indice)} (indice européen {round(indice)} ; sous 20 c'est bon, "
              f"au-dessus de 80 très mauvais)." + (f" {_majuscule(', '.join(polluants))}." if polluants else "")]
    pollens = {mot: float(c[cle]) for cle, mot in POLLENS.items() if c.get(cle) is not None}
    if pollens:   # Open-Meteo n'en mesure qu'en Europe
        presents = [f"{mot} {round(v)} grains/m³" for mot, v in sorted(pollens.items(), key=lambda x: -x[1]) if v >= 1]
        lignes.append("Pollens dans l'air : " + ", ".join(presents) + "." if presents else "Pollens : presque rien dans l'air.")
    try:
        d = (await _json(infos.PREVISIONS, {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
                                            "daily": "sunrise,sunset,uv_index_max"}))["daily"]
        soleil = (f"Soleil : lever à {infos._heure(datetime.fromisoformat(d['sunrise'][0]))}, "
                  f"coucher à {infos._heure(datetime.fromisoformat(d['sunset'][0]))}")
        if d.get("uv_index_max") and d["uv_index_max"][0] is not None:
            uv = float(d["uv_index_max"][0])
            soleil += f" ; indice UV au plus fort {_court(uv)} ({niveau_uv(uv)})"
        lignes.append(soleil + ".")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        pass   # le soleil est un plus : la qualité de l'air suffit
    return "\n".join(lignes)


@tool("chercher_qualite_air", "La qualité de l'air d'un lieu en ce moment (Open-Meteo) : l'indice européen et les "
      "polluants, les pollens dans l'air (en Europe, en saison), l'heure du lever et du coucher du soleil et l'indice "
      "UV du jour. `lieu` = une ville : s'il ne dit pas où, prends sa ville dans ta mémoire (rubrique Lieux).",
      {"lieu": str})
async def chercher_qualite_air(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("Open-Meteo", air(args.get("lieu") or ""))


# --- Les prix des carburants (data.economie.gouv.fr, le flux instantané) ----------------------------------

CARBURANTS = {"gazole": "Gazole", "e10": "SP95-E10", "sp95": "SP95", "sp98": "SP98", "e85": "E85", "gplc": "GPL"}
NOMS_CARBURANTS = {
    "gazole": "gazole", "diesel": "gazole", "gasoil": "gazole", "gazoil": "gazole", "b7": "gazole",
    "sp95": "sp95", "sansplomb95": "sp95", "sansplomb": "sp95", "95": "sp95",
    "sp98": "sp98", "sansplomb98": "sp98", "98": "sp98",
    "e10": "e10", "sp95e10": "e10", "sansplombe10": "e10", "sansplomb95e10": "e10",
    "e85": "e85", "ethanol": "e85", "superethanol": "e85", "bioethanol": "e85",
    "gpl": "gplc", "gplc": "gplc",
}


def code_carburant(nom: str) -> str | None:
    """« diesel » → gazole, « sans plomb 98 » → sp98, « SP95-E10 » → e10 ; "" si tous ; None si on ne sait pas."""
    simple = re.sub(r"[\s\-]+", "", _simple(nom))
    if simple in {"", "essence", "carburant", "carburants", "tous"}:
        return ""
    return NOMS_CARBURANTS.get(simple)


def _litre(x: Any) -> str:
    return f"{float(x):.3f}".replace(".", ",") + " €"


def _station(s: dict[str, Any]) -> str:
    return f"{_casse(s.get('adresse'))}, {_casse(s.get('ville'))}"


async def _codes_de(lieu: str) -> tuple[list[str], str]:
    """Les codes postaux d'un lieu (« 29200 », ou une commune par son nom, via geo.api.gouv.fr)."""
    if re.fullmatch(r"\d{5}", lieu.replace(" ", "")):
        return [lieu.replace(" ", "")], lieu.replace(" ", "")
    trouvees = await _json(COMMUNES, {"nom": lieu, "fields": "nom,codesPostaux,departement", "boost": "population",
                                      "limit": 1})
    if not trouvees:
        raise Rien(f"Je ne trouve pas de commune nommée « {lieu} » en France.")
    c = trouvees[0]
    departement = (c.get("departement") or {}).get("nom")
    return ([x for x in c.get("codesPostaux") or [] if re.fullmatch(r"\d{5}", str(x))],
            f"{c['nom']} ({departement})" if departement else c["nom"])


async def carburant(lieu: str, sorte: str = "", n: int = 3) -> str:
    lieu = _un(lieu)
    if not lieu:
        raise Rien("Dis-moi où (une commune ou un code postal).")
    code = code_carburant(sorte)
    if code is None:
        raise Rien(f"Je ne connais pas le carburant « {sorte} » : gazole, SP95, SP98, E10, E85 ou GPL.")
    codes, nom = await _codes_de(lieu)
    if not codes:
        raise Rien(f"Je ne trouve pas de code postal pour {nom}.")
    ou = "cp in (" + ", ".join(f'"{c}"' for c in codes) + ")"   # des chiffres seulement : rien d'autre n'y entre
    if code:
        stations = (await _json(CARBURANTS_API, {
            "where": f"{ou} and {code}_prix is not null", "order_by": f"{code}_prix", "limit": n,
            "select": f"adresse,ville,cp,{code}_prix,{code}_maj"})).get("results") or []
        if not stations:
            raise Rien(f"Pas de prix du {CARBURANTS[code]} relevé à {nom}.")
        lignes = [f"{CARBURANTS[code]} à {nom}, les moins chers :"]
        for i, s in enumerate(stations, 1):
            releve = f" (relevé {_quel_jour(date.fromisoformat(s[f'{code}_maj'][:10]))})" if s.get(f"{code}_maj") else ""
            lignes.append(f"{i}. {_litre(s[f'{code}_prix'])} le litre, {_station(s)}{releve}.")
        return "\n".join(lignes + ["(Prix déclarés par les stations au site de l'État.)"])
    reponse = await _json(CARBURANTS_API, {"where": ou, "limit": 100, "select": "adresse,ville,cp,"
                                           + ",".join(f"{c}_prix" for c in CARBURANTS)})
    stations = reponse.get("results") or []
    if not stations:
        raise Rien(f"Pas de station-service relevée à {nom}.")
    moins_chers = []
    for c, mot in CARBURANTS.items():
        avec = [s for s in stations if s.get(f"{c}_prix") is not None]
        if avec:
            s = min(avec, key=lambda s: float(s[f"{c}_prix"]))
            moins_chers.append(f"{mot} {_litre(s[f'{c}_prix'])} ({_station(s)})")
    total = int(reponse.get("total_count") or len(stations))
    compte = f"parmi {total} stations" if total <= 100 else f"parmi 100 des {total} stations"
    return (f"À {nom}, le litre le moins cher pour chaque carburant ({compte}) : " + " ; ".join(moins_chers) + ".\n"
            "(Prix déclarés par les stations au site de l'État.)")


@tool("chercher_carburant", "Les prix des carburants dans les stations d'une commune française (site officiel des "
      "prix, mis à jour en continu). `lieu` = une commune ou un code postal (s'il ne dit pas où, prends sa ville dans "
      "ta mémoire) ; `carburant` = gazole, SP95, SP98, E10, E85 ou GPL : il donne les `n` stations les moins chères "
      "(3 par défaut, 5 au plus) ; sans carburant, le moins cher de chacun.",
      {"type": "object",
       "properties": {"lieu": {"type": "string"}, "carburant": {"type": "string"}, "n": {"type": "integer"}},
       "required": ["lieu"]})
async def chercher_carburant(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("Le site des prix des carburants",
                           carburant(args.get("lieu") or "", args.get("carburant") or "", _entier(args.get("n"), 3, 1, 5)))


# --- Les livres (Open Library) ----------------------------------------------------------------------------

async def livre(recherche: str, auteur: str = "") -> str:
    recherche, auteur = _un(recherche), _un(auteur)
    if not recherche and not auteur:
        raise Rien("Dis-moi quel livre (un titre, un auteur ou un ISBN).")
    params: dict[str, Any] = {"limit": 3, "lang": "fr",
                              "fields": "key,title,author_name,first_publish_year,number_of_pages_median,edition_count"}
    isbn = re.sub(r"[\s\-]", "", recherche)
    if re.fullmatch(r"\d{9}[\dXx]|\d{13}", isbn):
        params["isbn"] = isbn
    elif recherche:
        params["q"] = recherche
    if auteur:
        params["author"] = auteur
    livres = (await _json(OPEN_LIBRARY, params)).get("docs") or []
    if not livres:
        raise Rien(f"Open Library ne trouve pas de livre « {' '.join(x for x in (recherche, auteur) if x)} ».")
    lignes = ["Open Library :"]
    for i, l in enumerate(livres, 1):
        morceaux = [f"« {_un(l.get('title'))} »"]
        if l.get("author_name"):
            morceaux.append(", ".join(l["author_name"][:2]))
        if l.get("first_publish_year"):
            morceaux.append(f"paru en {l['first_publish_year']}")
        if l.get("number_of_pages_median"):
            morceaux.append(f"environ {l['number_of_pages_median']} pages")
        if l.get("edition_count"):
            morceaux.append(f"{l['edition_count']} édition{'s' if l['edition_count'] > 1 else ''}")
        lignes.append(f"{i}. " + ", ".join(morceaux) + f". Lien : https://openlibrary.org{l.get('key', '')}")
    return "\n".join([*lignes, DU_DEHORS])


@tool("chercher_livre", "Un livre dans Open Library (bibliothèque ouverte) : titre, auteur, année de première "
      "parution, nombre de pages, d'éditions. `titre` = un titre, des mots-clés ou un ISBN ; `auteur` = facultatif "
      "(« Camus »). Pour le résumé ou l'avis sur le livre, prends chercher_wikipedia ou WebSearch.",
      {"type": "object",
       "properties": {"titre": {"type": "string"}, "auteur": {"type": "string"}}})
async def chercher_livre(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("Open Library", livre(args.get("titre") or "", args.get("auteur") or ""))


# --- La musique, les podcasts, les apps (la recherche iTunes d'Apple) -------------------------------------

SORTES_ITUNES = {"morceau": ("music", "song"), "album": ("music", "album"), "artiste": ("music", "musicArtist"),
                 "podcast": ("podcast", "podcast"), "app": ("software", "software"),
                 "livre audio": ("audiobook", "audiobook")}
NOMS_SORTES = {"morceau": "morceau", "chanson": "morceau", "titre": "morceau", "musique": "morceau",
               "son": "morceau", "album": "album", "artiste": "artiste", "groupe": "artiste", "chanteur": "artiste",
               "chanteuse": "artiste", "podcast": "podcast", "emission": "podcast", "app": "app",
               "application": "app", "appli": "app", "jeu": "app", "livre audio": "livre audio"}


def sorte_itunes(nom: str) -> str | None:
    simple = _simple(nom or "morceau")
    return NOMS_SORTES.get(simple) or NOMS_SORTES.get(re.sub(r"s$", "", simple))


def _duree(ms: Any) -> str:
    s = round(float(ms) / 1000)
    return f"{s // 60} min {s % 60:02d}"


def _resultat_itunes(sorte: str, x: dict[str, Any]) -> str:
    annee = str(x.get("releaseDate") or "")[:4]
    if sorte == "morceau":
        t = f"« {x.get('trackName')} », {x.get('artistName')}"
        if x.get("collectionName"):
            t += f", album « {x['collectionName']} »"
        t += f" ({annee})" if annee else ""
        t += f", {_duree(x['trackTimeMillis'])}" if x.get("trackTimeMillis") else ""
        lien = x.get("trackViewUrl")
    elif sorte in ("album", "livre audio"):
        t = f"« {x.get('collectionName')} », {x.get('artistName')}" + (f" ({annee})" if annee else "")
        if x.get("trackCount") and sorte == "album":
            t += f", {x['trackCount']} titres"
        t += f", {x['primaryGenreName']}" if x.get("primaryGenreName") else ""
        lien = x.get("collectionViewUrl")
    elif sorte == "artiste":
        t = f"{x.get('artistName')}" + (f" ({x['primaryGenreName']})" if x.get("primaryGenreName") else "")
        lien = x.get("artistLinkUrl")
    elif sorte == "podcast":
        t = f"« {x.get('collectionName') or x.get('trackName')} », {x.get('artistName')}"
        t += f" ({x['primaryGenreName']})" if x.get("primaryGenreName") else ""
        quand = _instant(x.get("releaseDate"))
        t += f", dernier épisode {_quel_jour(quand.astimezone(infos.PARIS).date())}" if quand else ""
        lien = x.get("collectionViewUrl")
    else:   # une app
        prix = "gratuite" if not x.get("price") else (x.get("formattedPrice") or f"{_nombre(float(x['price']))} €")
        t = f"{x.get('trackName')}, de {x.get('sellerName') or x.get('artistName')}, {prix}"
        if x.get("averageUserRating"):
            t += (f", notée {_court(x['averageUserRating'])} sur 5"
                  + (f" ({_nombre(float(x['userRatingCount']), 0)} avis)" if x.get("userRatingCount") else ""))
        lien = x.get("trackViewUrl")
    return t + (f". Lien : {lien}" if lien else ".")


async def musique(recherche: str, sorte: str = "morceau", n: int = 3) -> str:
    recherche = _un(recherche)
    if not recherche:
        raise Rien("Dis-moi quoi chercher (un titre, un artiste, un podcast ou une app).")
    nom_sorte = sorte_itunes(sorte)
    if not nom_sorte:
        raise Rien(f"Je ne sais pas chercher « {sorte} » : morceau, album, artiste, podcast, app ou livre audio.")
    media, entite = SORTES_ITUNES[nom_sorte]
    trouves = (await _json(ITUNES, {"term": recherche, "country": "FR", "lang": "fr_fr", "media": media,
                                    "entity": entite, "limit": n})).get("results") or []
    if not trouves:
        raise Rien(f"Apple ne trouve pas de {nom_sorte} « {recherche} ».")
    lignes = [f"{i}. {_resultat_itunes(nom_sorte, x)}" for i, x in enumerate(trouves[:n], 1)]
    return "\n".join([f"Recherche Apple ({nom_sorte}, « {recherche} ») :", *lignes, DU_DEHORS])


@tool("chercher_musique", "Cherche dans le catalogue d'Apple (iTunes, magasin français) : un morceau, un album, un "
      "artiste, un podcast, une app ou un livre audio. `recherche` = des mots (« Get Lucky Daft Punk », « Doctolib ») ; "
      "`sorte` = morceau (par défaut), album, artiste, podcast, app ou livre audio ; `n` = 3 par défaut, 5 au plus. "
      "Pour « c'est quoi cette chanson », « l'album de… sort quand », « elle coûte combien, cette app ». "
      "Pas de films (Apple ne les donne plus) : pour un film, chercher_wikipedia.",
      {"type": "object",
       "properties": {"recherche": {"type": "string"}, "sorte": {"type": "string"}, "n": {"type": "integer"}},
       "required": ["recherche"]})
async def chercher_musique(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("La recherche d'Apple", musique(args.get("recherche") or "", args.get("sorte") or "morceau",
                                                           _entier(args.get("n"), 3, 1, 5)))


# --- La tech (Hacker News, par Algolia) -------------------------------------------------------------------

async def tech(sujet: str = "", n: int = 5, jours: int = 30) -> str:
    sujet = _un(sujet)
    if sujet:
        depuis = int((infos._maintenant() - timedelta(days=jours)).timestamp())
        params: dict[str, Any] = {"query": sujet, "tags": "story", "hitsPerPage": n,
                                  "numericFilters": f"created_at_i>{depuis}"}
    else:
        params = {"tags": "front_page", "hitsPerPage": n}
    articles = [h for h in (await _json(HACKER_NEWS, params)).get("hits") or [] if h.get("title")]
    if not articles:
        raise Rien(f"Hacker News n'a rien sur « {sujet} » ces {jours} derniers jours." if sujet
                   else "Hacker News n'a rien à la une.")
    articles.sort(key=lambda h: -int(h.get("points") or 0))
    maintenant = infos._maintenant()
    lignes = [f"{i}. {_un(h['title'])} — {h.get('points') or 0} points, {h.get('num_comments') or 0} commentaires, "
              f"{infos.quand_lisible(_instant(h.get('created_at')), maintenant)}. "
              f"Lien : {h.get('url') or 'https://news.ycombinator.com/item?id=' + str(h.get('objectID'))}"
              for i, h in enumerate(articles[:n], 1)]
    entete = (f"Hacker News, « {sujet} », ces {jours} derniers jours, les plus populaires (titres en anglais) :"
              if sujet else "La une de Hacker News (titres en anglais) :")
    return "\n".join([entete, *lignes, DU_DEHORS])


@tool("chercher_tech", "L'actualité tech et startups de Hacker News (en anglais) : titre, points, commentaires, date, "
      "lien. `sujet` = des mots-clés (« Rust », « OpenAI ») : les plus populaires des `jours` derniers jours "
      "(30 par défaut) ; sans sujet, la une du moment. `n` = 5 par défaut, 10 au plus. Traduis les titres en "
      "français en les lui disant, sans lire les liens.",
      {"type": "object",
       "properties": {"sujet": {"type": "string"}, "n": {"type": "integer"}, "jours": {"type": "integer"}}})
async def chercher_tech(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("Hacker News", tech(args.get("sujet") or "", _entier(args.get("n"), 5, 1, 10),
                                               _entier(args.get("jours"), 30, 1, 365)))


# --- Les séismes (USGS, l'institut d'études géologiques américain) ----------------------------------------

DIRECTIONS = {"N": "nord", "NNE": "nord-nord-est", "NE": "nord-est", "ENE": "est-nord-est", "E": "est",
              "ESE": "est-sud-est", "SE": "sud-est", "SSE": "sud-sud-est", "S": "sud", "SSW": "sud-sud-ouest",
              "SW": "sud-ouest", "WSW": "ouest-sud-ouest", "W": "ouest", "WNW": "ouest-nord-ouest", "NW": "nord-ouest",
              "NNW": "nord-nord-ouest"}


def lieu_seisme(place: Any) -> str:
    """« 18 km ESE of Güvercinlik, Turkey » → « à 18 km à l'est-sud-est de Güvercinlik, Turkey »."""
    place = _un(place)
    m = re.fullmatch(r"(\d+) km ([NSEW]{1,3}) of (.+)", place)
    if not m or m.group(2) not in DIRECTIONS:
        return place or "lieu inconnu"
    direction = DIRECTIONS[m.group(2)]
    return f"à {m.group(1)} km {'au ' if direction[0] in 'ns' else 'à l’'}{direction} de {m.group(3)}"


async def seismes(lieu: str = "", jours: int = 7, magnitude: float | None = None, n: int = 5) -> str:
    lieu = _un(lieu)
    mini = magnitude if magnitude is not None else (2.5 if lieu else 4.5)
    params: dict[str, Any] = {
        "format": "geojson", "limit": n, "minmagnitude": mini,
        "starttime": (infos._maintenant() - timedelta(days=jours)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
    nom = ""
    if lieu:   # près d'un lieu : les derniers, à 300 km au plus
        lat, lon, nom = await _lieu(lieu)
        params.update({"latitude": lat, "longitude": lon, "maxradiuskm": 300, "orderby": "time"})
    else:   # dans le monde : les plus forts
        params["orderby"] = "magnitude"
    secousses = (await _json(USGS, params)).get("features") or []
    ou = f" à moins de 300 km de {nom}" if lieu else " dans le monde"
    if not secousses:
        raise Rien(f"Aucun séisme de magnitude {_court(mini)} ou plus{ou} ces {jours} derniers jours (USGS).")
    maintenant = infos._maintenant()
    lignes = [f"Séismes de magnitude {_court(mini)} ou plus{ou}, ces {jours} derniers jours "
              f"({'les plus récents' if lieu else 'les plus forts'} d'abord, USGS) :"]
    for i, s in enumerate(secousses[:n], 1):
        p = s["properties"]
        quand = datetime.fromtimestamp(p["time"] / 1000, timezone.utc)
        ligne = f"{i}. Magnitude {_court(p['mag'])}, {lieu_seisme(p.get('place'))}, {infos.quand_lisible(quand, maintenant)}"
        coordonnees = (s.get("geometry") or {}).get("coordinates") or []
        if len(coordonnees) > 2 and coordonnees[2] is not None:
            ligne += f", à {round(float(coordonnees[2]))} km de profondeur"
        if p.get("tsunami"):
            ligne += ", alerte au tsunami émise"
        lignes.append(ligne + ".")
    return "\n".join(lignes)


@tool("chercher_seismes", "Les tremblements de terre récents (USGS) : magnitude, lieu, heure, profondeur. Sans "
      "`lieu`, les plus forts du monde ; avec un `lieu` (« Nice », « Tokyo »), les derniers à moins de 300 km. "
      "`jours` = 7 par défaut (30 au plus) ; `magnitude` = la plus petite à donner (4,5 dans le monde, 2,5 près "
      "d'un lieu) ; `n` = 5 par défaut, 10 au plus. Les noms de lieux viennent en anglais.",
      {"type": "object",
       "properties": {"lieu": {"type": "string"}, "jours": {"type": "integer"}, "magnitude": {"type": "number"},
                      "n": {"type": "integer"}}})
async def chercher_seismes(args: dict[str, Any]) -> dict[str, Any]:
    try:
        magnitude = None if args.get("magnitude") in (None, "") else float(str(args["magnitude"]).replace(",", "."))
    except ValueError:
        return infos._erreur(f"« {args.get('magnitude')} » n'est pas une magnitude : donne un nombre (4,5).")
    if magnitude is not None:
        magnitude = max(0.0, min(9.0, magnitude))
    return await _repondre("L'USGS", seismes(args.get("lieu") or "", _entier(args.get("jours"), 7, 1, 30), magnitude,
                                             _entier(args.get("n"), 5, 1, 10)))


# --- La traduction (MyMemory) -----------------------------------------------------------------------------

LANGUES = {"francais": "fr", "anglais": "en", "espagnol": "es", "allemand": "de", "italien": "it", "portugais": "pt",
           "neerlandais": "nl", "hollandais": "nl", "japonais": "ja", "chinois": "zh-CN", "mandarin": "zh-CN",
           "russe": "ru", "arabe": "ar", "coreen": "ko", "polonais": "pl", "turc": "tr", "grec": "el",
           "suedois": "sv", "danois": "da", "norvegien": "no", "finnois": "fi", "tcheque": "cs", "roumain": "ro",
           "hongrois": "hu", "ukrainien": "uk", "hebreu": "he", "hindi": "hi", "vietnamien": "vi", "thai": "th",
           "indonesien": "id", "catalan": "ca", "breton": "br", "basque": "eu", "corse": "co", "latin": "la"}
NOMS_LANGUES = {code: nom for nom, code in LANGUES.items() if nom not in {"hollandais", "mandarin"}}
NOMS_LANGUES.update({"fr": "français", "neerlandais": "néerlandais", "coreen": "coréen", "suedois": "suédois",
                     "norvegien": "norvégien", "tcheque": "tchèque", "hebreu": "hébreu", "thai": "thaï",
                     "indonesien": "indonésien"})
OCTETS_MAX = 500   # ce que MyMemory accepte d'un coup


def code_langue(nom: str) -> str | None:
    """« anglais » → en, « Chinois » → zh-CN, « es » → es ; « auto » → autodetect ; None si on ne sait pas."""
    simple = _simple(nom)
    if simple in {"", "auto", "autodetect", "detection", "inconnue"}:
        return "autodetect"
    if simple in LANGUES:
        return LANGUES[simple]
    m = re.fullmatch(r"([a-z]{2})(?:-([a-z]{2}))?", simple)
    return (m.group(1) + (f"-{m.group(2).upper()}" if m.group(2) else "")) if m else None


def nom_langue(code: str) -> str:
    nom = NOMS_LANGUES.get(code, code)
    return NOMS_LANGUES.get(nom, nom)


async def traduction(texte: str, vers: str, de: str = "") -> str:
    texte = _un(texte)
    if not texte:
        raise Rien("Dis-moi quoi traduire.")
    if len(texte.encode()) > OCTETS_MAX:
        raise Rien("C'est trop long pour MyMemory (500 octets au plus d'un coup, environ 450 lettres) : "
                   "traduis-le toi-même, ou par morceaux.")
    source, cible = code_langue(de), code_langue(vers or "")
    for nom, code in ((de, source), (vers, cible)):
        if not code:
            raise Rien(f"Je ne connais pas la langue « {nom} » : donne son nom (anglais, espagnol…) ou son code (en, es…).")
    if cible == "autodetect":
        raise Rien("Dis-moi vers quelle langue traduire.")
    if source == cible:
        return f"C'est déjà en {nom_langue(cible)} : {texte}"
    d = await _json(MYMEMORY, {"q": texte, "langpair": f"{source}|{cible}"})
    statut = str(d.get("responseStatus"))
    if d.get("quotaFinished") or statut == "429":
        raise Rien("Le quota gratuit du jour de MyMemory est épuisé (quelques milliers de lettres par jour) : "
                   "traduis-le toi-même, ou réessaie demain.")
    if statut == "403":
        raise Rien(f"MyMemory ne sait pas traduire {_de(nom_langue(source))} {_vers(nom_langue(cible))}.")
    if statut != "200":
        raise ValueError(f"MyMemory : statut {statut}")
    reponse = d["responseData"]
    traduit = _un(html.unescape(reponse["translatedText"]))
    if not traduit:
        raise Rien("MyMemory n'a pas de traduction pour ce texte.")
    depuis = nom_langue(reponse.get("detectedLanguage") or source) if source == "autodetect" else nom_langue(source)
    sur = "" if float(reponse.get("match") or 0) >= 0.5 else " (traduction peu sûre : vérifie-la)"
    return f"Traduction {_de(depuis)} {_vers(nom_langue(cible))} (MyMemory){sur} : {traduit}\n{DU_DEHORS}"


@tool("chercher_traduction", "Traduit une phrase courte (MyMemory, gratuit, quelques milliers de lettres par jour "
      "au plus) : une traduction de référence, tirée de traductions humaines quand il y en a. `texte` = la phrase "
      "(500 octets au plus) ; `vers` = la langue voulue (« anglais », « es ») ; `de` = sa langue (détectée si tu ne "
      "la donnes pas, mais mets-la pour un mot seul : la détection s'y trompe). Si le quota est épuisé, traduis "
      "toi-même.",
      {"type": "object",
       "properties": {"texte": {"type": "string"}, "vers": {"type": "string"}, "de": {"type": "string"}},
       "required": ["texte", "vers"]})
async def chercher_traduction(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("MyMemory", traduction(args.get("texte") or "", args.get("vers") or "", args.get("de") or ""))


# --- Les séries (TVmaze) ----------------------------------------------------------------------------------

STATUTS = {"Running": "en cours", "Ended": "terminée", "To Be Determined": "en suspens (suite incertaine)",
           "In Development": "en préparation"}
GENRES = {"Drama": "drame", "Comedy": "comédie", "Science-Fiction": "science-fiction", "Thriller": "thriller",
          "Action": "action", "Crime": "policier", "Horror": "horreur", "Romance": "romance", "Fantasy": "fantastique",
          "Mystery": "mystère", "Family": "famille", "Adventure": "aventure", "History": "histoire", "War": "guerre",
          "Espionage": "espionnage", "Medical": "médical", "Legal": "judiciaire", "Supernatural": "surnaturel",
          "Music": "musique", "Sports": "sport", "Western": "western", "Anime": "animé", "Food": "cuisine",
          "Children": "jeunesse", "Nature": "nature", "Travel": "voyage"}


def _episode(e: dict[str, Any]) -> str:
    quand = _instant(e.get("airstamp")) if e.get("airstamp") else None
    jour = quand.astimezone(infos.PARIS).date() if quand else (
        date.fromisoformat(e["airdate"]) if e.get("airdate") else None)
    t = f"saison {e.get('season')}, épisode {e.get('number')}"
    t += f", « {e['name']} »" if e.get("name") else ""
    return t + (f", {_quel_jour(jour)}" if jour else "")


async def serie(nom: str) -> str:
    nom = _un(nom)
    if not nom:
        raise Rien("Dis-moi quelle série.")
    r = await _get(TVMAZE, {"q": nom, "embed[]": ["nextepisode", "previousepisode"]})
    if r.status_code == 404:
        raise Rien(f"TVmaze ne connaît pas de série nommée « {nom} ».")
    r.raise_for_status()
    s = r.json()
    if not s or not s.get("name"):
        raise Rien(f"TVmaze ne connaît pas de série nommée « {nom} ».")
    chaine = (s.get("network") or s.get("webChannel") or {}).get("name")
    debut, fin = str(s.get("premiered") or "")[:4], str(s.get("ended") or "")[:4]
    statut = STATUTS.get(s.get("status"), s.get("status") or "statut inconnu")
    duree = (f"de {debut} à {fin}" if fin and fin != debut else f"en {debut}") if fin else (f"depuis {debut}" if debut else "")
    phrase = f"« {s['name']} »{f' ({chaine})' if chaine else ''}, série {statut}{f' ({duree})' if duree else ''}"
    genres = [GENRES.get(g, g) for g in s.get("genres") or []]
    if genres:
        phrase += ", " + ", ".join(genres)
    note = (s.get("rating") or {}).get("average")
    if note:
        phrase += f" ; note {_court(note)} sur 10"
    lignes = [phrase + "."]
    episodes = s.get("_embedded") or {}
    if episodes.get("nextepisode"):
        lignes.append(f"Prochain épisode : {_episode(episodes['nextepisode'])}.")
    if episodes.get("previousepisode"):
        lignes.append(f"Dernier épisode sorti : {_episode(episodes['previousepisode'])}.")
    if s.get("summary"):
        lignes.append(f"Résumé (en anglais) : {_couper(_sans_html(s['summary']), 300)}")
    if s.get("url"):
        lignes.append(f"Lien : {s['url']}")
    return "\n".join([f"TVmaze : {lignes[0]}", *lignes[1:], DU_DEHORS])


@tool("chercher_serie", "Une série télé (TVmaze) : chaîne ou plateforme, en cours ou terminée, genres, note, date "
      "du prochain épisode et du dernier sorti, résumé (en anglais : traduis-le). `nom` = son titre "
      "(« Severance », « Le Bureau des légendes »). Pour « la suite de … sort quand ? ».",
      {"nom": str})
async def chercher_serie(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("TVmaze", serie(args.get("nom") or ""))


# --- Le code open source (la recherche de GitHub, sans compte) -------------------------------------------

GITHUB = "https://api.github.com/search/repositories"


async def code(recherche: str, n: int = 3) -> str:
    recherche = _un(recherche)
    if len(recherche) < 2:
        raise Rien("Dis-moi quoi chercher (« assistant vocal mlx », « facturation open source »).")
    r = await _get(GITHUB, {"q": recherche, "sort": "stars", "order": "desc", "per_page": n})
    if r.status_code in (403, 429):
        raise Rien("GitHub limite les recherches sans compte (une dizaine par minute) : réessaie dans une minute.")
    if r.status_code == 422:
        raise Rien(f"GitHub ne comprend pas la recherche « {recherche} » : mets juste des mots-clés.")
    r.raise_for_status()
    depots = r.json().get("items") or []
    if not depots:
        raise Rien(f"GitHub ne trouve pas de projet « {recherche} ».")
    lignes = [f"GitHub, « {recherche} », les plus suivis :"]
    for i, d in enumerate(depots[:n], 1):
        etoiles = int(d.get("stargazers_count") or 0)
        morceaux = [d.get("full_name") or "?", f"{_nombre(float(etoiles), 0)} étoile{'s' if etoiles > 1 else ''}"]
        if d.get("language"):
            morceaux.append(d["language"])
        if (d.get("license") or {}).get("spdx_id") not in (None, "NOASSERTION"):
            morceaux.append(f"licence {d['license']['spdx_id']}")
        if (quand := _instant(d.get("pushed_at"))):
            morceaux.append(f"dernière modification {_quel_jour(quand.astimezone(infos.PARIS).date())}")
        if d.get("archived"):
            morceaux.append("archivé (plus maintenu)")
        ligne = f"{i}. " + ", ".join(morceaux)
        if d.get("description"):
            ligne += f" : {_phrase(d['description'], 160)}"
        lignes.append(ligne + f" Lien : {d.get('html_url')}")
    return "\n".join([*lignes, DU_DEHORS])


@tool("chercher_code", "Des projets open source sur GitHub (sans compte) : nom, étoiles, langage, licence, dernière "
      "modification, description. `recherche` = des mots-clés (« assistant vocal mlx », « invoice self-hosted ») ; "
      "`n` = 3 par défaut, 5 au plus. Pour « est-ce que ça existe déjà en open source ? », « un outil libre pour… ». "
      "Une dizaine de recherches par minute au plus.",
      {"type": "object",
       "properties": {"recherche": {"type": "string"}, "n": {"type": "integer"}},
       "required": ["recherche"]})
async def chercher_code(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("GitHub", code(args.get("recherche") or "", _entier(args.get("n"), 3, 1, 5)))


# --- Les paquets de code (PyPI pour Python, npm pour JavaScript) -----------------------------------------

PYPI = "https://pypi.org/pypi"
NPM = "https://registry.npmjs.org"
_NOM_PAQUET = re.compile(r"(@[a-z0-9][\w.\-]*/)?[A-Za-z0-9][\w.\-]{0,100}")


async def _pypi(nom: str) -> str | None:
    r = await _get(f"{PYPI}/{nom}/json")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    i = d.get("info") or {}
    morceaux = [f"version {i.get('version')}"]
    fichiers = d.get("urls") or []
    if fichiers and fichiers[0].get("upload_time"):
        morceaux.append(f"sortie le {_date_longue(fichiers[0]['upload_time'])}")
    if i.get("requires_python"):
        morceaux.append(f"Python {i['requires_python']}")
    licence = _un(i.get("license_expression") or i.get("license"))
    if licence and len(licence) <= 40:
        morceaux.append(f"licence {licence}")
    liens = i.get("project_urls") or {}
    lien = liens.get("Source") or liens.get("Homepage") or i.get("home_page") or f"https://pypi.org/project/{nom}/"
    return (f"PyPI (Python) : {i.get('name') or nom}, " + ", ".join(morceaux) + "."
            + (f" {_phrase(i['summary'], 200)}" if i.get("summary") else "") + f" Lien : {lien}")


async def _npm(nom: str) -> str | None:
    r = await _get(f"{NPM}/{nom.replace('/', '%2F')}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    version = (d.get("dist-tags") or {}).get("latest")
    if not version:
        return None
    morceaux = [f"version {version}"]
    if (quand := (d.get("time") or {}).get(version)):
        morceaux.append(f"sortie le {_date_longue(quand)}")
    licence = (d.get("versions") or {}).get(version, {}).get("license") or d.get("license")
    if isinstance(licence, str) and len(licence) <= 40:
        morceaux.append(f"licence {licence}")
    return (f"npm (JavaScript) : {d.get('name') or nom}, " + ", ".join(morceaux) + "."
            + (f" {_phrase(d['description'], 200)}" if d.get("description") else "")
            + f" Lien : {d.get('homepage') or 'https://www.npmjs.com/package/' + nom}")


async def paquet(nom: str, ou: str = "") -> str:
    nom, ou = _un(nom), _simple(ou)
    if not _NOM_PAQUET.fullmatch(nom):
        raise Rien("Donne-moi le nom exact du paquet (« httpx », « react », « @anthropic-ai/sdk »).")
    if ou not in {"", "pypi", "python", "pip", "npm", "javascript", "js", "node", "typescript"}:
        raise Rien(f"Je ne connais pas « {ou} » : python (PyPI) ou javascript (npm).")
    sources = ([_npm] if ou in {"npm", "javascript", "js", "node", "typescript"} or nom.startswith("@")
               else [_pypi] if ou else [_pypi, _npm])
    trouves = [x for x in [await s(nom) for s in sources] if x]
    if not trouves:
        raise Rien(f"Aucun paquet nommé « {nom} »" + (" sur PyPI ni sur npm." if len(sources) > 1 else "."))
    return "\n".join([*trouves, DU_DEHORS])


@tool("chercher_paquet", "La dernière version d'un paquet de code : PyPI (Python) et npm (JavaScript), sa date de "
      "sortie, sa licence, sa description, son lien. `nom` = le nom exact (« httpx », « react », "
      "« @anthropic-ai/sdk ») ; `ou` = python ou javascript (les deux si tu ne sais pas). Pour « quelle est la "
      "dernière version de… », avant d'écrire un requirements.txt ou un package.json.",
      {"type": "object",
       "properties": {"nom": {"type": "string"}, "ou": {"type": "string"}},
       "required": ["nom"]})
async def chercher_paquet(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("PyPI et npm", paquet(args.get("nom") or "", args.get("ou") or ""))


# --- Les articles scientifiques (OpenAlex, le catalogue ouvert de la recherche) ---------------------------

OPENALEX = "https://api.openalex.org/works"


async def articles(sujet: str, depuis: int = 0, n: int = 3) -> str:
    sujet = _un(sujet)
    if len(sujet) < 3:
        raise Rien("Dis-moi sur quel sujet (« reconnaissance vocale », « sommeil et caféine »).")
    params: dict[str, Any] = {"search": sujet, "per_page": n,
                              "select": "display_name,publication_year,cited_by_count,doi,authorships,"
                                        "primary_location,open_access,id"}
    if depuis:
        params["filter"] = f"from_publication_date:{depuis}-01-01"
    trouves = (await _json(OPENALEX, params)).get("results") or []
    if not trouves:
        raise Rien(f"OpenAlex ne trouve pas d'article sur « {sujet} »" + (f" depuis {depuis}." if depuis else "."))
    lignes = [f"OpenAlex, « {sujet} »" + (f" depuis {depuis}" if depuis else "") + " (titres souvent en anglais) :"]
    for i, w in enumerate(trouves[:n], 1):
        auteurs = [((a.get("author") or {}).get("display_name") or "?") for a in (w.get("authorships") or [])[:3]]
        if len(w.get("authorships") or []) > 3:
            auteurs.append("et d'autres")
        revue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        morceaux = [f"« {_couper(w.get('display_name'), 200)} »"]
        if auteurs:
            morceaux.append(", ".join(auteurs))
        if w.get("publication_year"):
            morceaux.append(str(w["publication_year"]))
        if revue:
            morceaux.append(revue)
        morceaux.append(f"cité {_nombre(float(w.get('cited_by_count') or 0), 0)} fois")
        libre = (w.get("open_access") or {}).get("oa_url")
        lien = libre or w.get("doi") or w.get("id")
        lignes.append(f"{i}. " + ", ".join(morceaux) + (" (lisible gratuitement)" if libre else "")
                      + (f". Lien : {lien}" if lien else "."))
    return "\n".join([*lignes, DU_DEHORS])


@tool("chercher_articles", "Des articles scientifiques (OpenAlex, plus de 200 millions de publications) : titre, "
      "auteurs, année, revue, nombre de citations, lien (vers le texte gratuit quand il existe). `sujet` = des "
      "mots-clés, en anglais de préférence (« sleep caffeine », « speech recognition ») ; `depuis` = une année "
      "(facultatif, pour le récent) ; `n` = 3 par défaut, 5 au plus. Pour « qu'est-ce que dit la recherche sur… ».",
      {"type": "object",
       "properties": {"sujet": {"type": "string"}, "depuis": {"type": "integer"}, "n": {"type": "integer"}},
       "required": ["sujet"]})
async def chercher_articles(args: dict[str, Any]) -> dict[str, Any]:
    annee = _entier(args.get("depuis"), 0, 0, infos._aujourdhui().year)
    return await _repondre("OpenAlex", articles(args.get("sujet") or "", annee if annee >= 1900 else 0,
                                                _entier(args.get("n"), 3, 1, 5)))


# --- L'heure dans le monde (le fuseau d'Open-Meteo, l'heure calculée sur le Mac) ---------------------------

async def heure(lieu: str) -> str:
    lieu = _un(lieu)
    if not lieu:
        raise Rien("Dis-moi pour quelle ville.")
    trouves = (await _json(infos.GEOCODAGE, {"name": lieu, "language": "fr", "count": 1, "format": "json"})).get("results")
    if not trouves or not trouves[0].get("timezone"):
        raise Rien(f"Je ne trouve pas de lieu nommé « {lieu} ».")
    e = trouves[0]
    try:
        fuseau = ZoneInfo(e["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise Rien(f"Je ne connais pas le fuseau horaire de {e['name']}.") from None
    ici = infos._maintenant()
    la_bas = ici.astimezone(fuseau)
    ecart = (la_bas.utcoffset() - ici.utcoffset()).total_seconds() / 3600
    pays = f" ({e['country']})" if e.get("country") and e["country"] != e["name"] else ""
    if ecart == 0:
        decalage = "la même heure qu'à Paris"
    else:
        heures = _court(abs(ecart))
        decalage = f"{heures} heure{'s' if abs(ecart) >= 2 else ''} de {'plus' if ecart > 0 else 'moins'} qu'à Paris"
    jour = la_bas.date() - ici.date()
    quel = {1: ", on y est déjà demain", -1: ", on y est encore hier"}.get(jour.days, "")
    return (f"À {e['name']}{pays}, il est {infos._heure(la_bas)}, {infos._jour(la_bas.date())}{quel} "
            f"({decalage}, fuseau {e['timezone']}).")


@tool("chercher_heure", "L'heure qu'il est dans une ville du monde, et le décalage avec Paris (heure d'été comprise). "
      "`lieu` = une ville (« Tokyo », « San Francisco »). Pour « il est quelle heure à… », avant d'appeler ou "
      "d'écrire à quelqu'un à l'étranger.",
      {"lieu": str})
async def chercher_heure(args: dict[str, Any]) -> dict[str, Any]:
    return await _repondre("Open-Meteo", heure(args.get("lieu") or ""))


OUTILS_INFOS2 = [chercher_entreprise, chercher_adresse, chercher_produit, chercher_qualite_air, chercher_carburant,
                 chercher_livre, chercher_musique, chercher_tech, chercher_seismes, chercher_traduction,
                 chercher_serie, chercher_code, chercher_paquet, chercher_articles, chercher_heure]
