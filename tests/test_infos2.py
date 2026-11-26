"""Ses autres outils « infos » : entreprises, adresses, produits, air, carburants, livres, musique, tech, séismes,
traduction, séries, code open source, paquets, articles scientifiques, heure dans le monde (jervis/infos2.py).

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_infos2.py

Sans réseau : un faux transport httpx (httpx.MockTransport) joue les réponses des services. À la fin, un mini essai
en vrai (l'heure à Tokyo, PyPI), sauté s'il n'y a pas de réseau. JERVIS_TEST_RESEAU=non pour le sauter d'office.
"""

import asyncio
import os
import threading
from datetime import datetime

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import httpx  # noqa: E402

from jervis import fond, infos, infos2, outils, permissions  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def contient(nom: str, texte_: str, *morceaux: str) -> None:
    manquants = [m for m in morceaux if m not in texte_]
    verifier(nom, manquants, [])
    if manquants:
        print(f"     dans : {texte_!r}")


def texte(resultat: dict) -> str:
    return resultat["content"][0]["text"]


def appeler(outil, **args) -> dict:
    return asyncio.run(outil.handler(args))


# Le faux Internet : {(hôte, chemin): réponse}. Une réponse = (code, texte ou objet JSON) ou une fonction(requête).
REPONSES: dict[tuple[str, str], object] = {}
VUES: list[httpx.Request] = []


def repondre(requete: httpx.Request) -> httpx.Response:
    VUES.append(requete)
    reponse = REPONSES.get((requete.url.host, requete.url.path))
    if callable(reponse):
        reponse = reponse(requete)
    if reponse is None:
        return httpx.Response(404, json={"message": "not found"})
    code, corps = reponse
    if isinstance(corps, str):
        return httpx.Response(code, text=corps)
    return httpx.Response(code, json=corps)


def derniere(hote: str) -> httpx.Request:
    return [v for v in VUES if v.url.host == hote][-1]


infos.TRANSPORT = httpx.MockTransport(repondre)
MAINTENANT = datetime(2026, 11, 24, 20, 0, tzinfo=infos.PARIS)   # un mardi soir
infos._maintenant = lambda: MAINTENANT
infos._aujourdhui = lambda: MAINTENANT.date()
DEHORS = infos.DU_DEHORS

PARIS_GEO = {"results": [{"name": "Paris", "latitude": 48.85, "longitude": 2.35, "admin1": "Île-de-France",
                          "country": "France", "timezone": "Europe/Paris"}]}
TOKYO_GEO = {"results": [{"name": "Tokyo", "latitude": 35.69, "longitude": 139.69, "admin1": "Tokyo",
                          "country": "Japon", "timezone": "Asia/Tokyo"}]}


def geocodage(requete):
    nom = requete.url.params.get("name", "").lower()
    return (200, {"paris": PARIS_GEO, "tokyo": TOKYO_GEO}.get(nom, {}))


REPONSES[("geocoding-api.open-meteo.com", "/v1/search")] = geocodage

# --- Entreprises ------------------------------------------------------------------------------------------
REPONSES[("recherche-entreprises.api.gouv.fr", "/search")] = (200, {"results": [
    {"nom_complet": "MISTRAL AI", "siren": "952418325", "etat_administratif": "A", "date_creation": "2023-04-28",
     "siege": {"adresse": "15 RUE DES HALLES 75001 PARIS"}, "section_activite_principale": "N",
     "activite_principale": "82.11Z", "categorie_entreprise": "PME", "tranche_effectif_salarie": "22",
     "dirigeants": [{"type_dirigeant": "personne physique", "prenoms": "ARTHUR JEAN", "nom": "MENSCH",
                     "qualite": "Président de SAS"}]},
    {"nom_complet": "MISTRAL ASCENSEURS", "siren": "510178627", "siege": {"libelle_commune": "MARCOUSSIS"}}]})
r = appeler(infos2.chercher_entreprise, nom="Mistral AI")
contient("entreprise : la fiche lisible", texte(r),
         "MISTRAL AI, SIREN 952 418 325, en activité, créée le 28 avril 2023.",
         "Siège : 15 Rue des Halles 75001 Paris.", "Activité : services administratifs et de soutien (code NAF 82.11Z).",
         "PME, 100 à 199 salariés.", "Dirigeants : Arthur Mensch (Président de SAS).",
         "Autres entreprises trouvées : MISTRAL ASCENSEURS (SIREN 510 178 627, Marcoussis).", DEHORS)
appeler(infos2.chercher_entreprise, nom="952 418 325")
verifier("entreprise : un SIREN dicté avec des espaces est recollé",
         derniere("recherche-entreprises.api.gouv.fr").url.params["q"], "952418325")
r = appeler(infos2.chercher_entreprise, nom="ab")
verifier("entreprise : trop court → il le dit, sans appel", (r.get("is_error", False), "trois lettres" in texte(r)),
         (False, True))

# --- Adresses ---------------------------------------------------------------------------------------------
REPONSES[("data.geopf.fr", "/geocodage/search")] = (200, {"features": [
    {"properties": {"label": "8 Boulevard du Port 80000 Amiens", "context": "80, Somme, Hauts-de-France",
                    "score": 0.95, "type": "housenumber"}, "geometry": {"coordinates": [2.29, 49.89]}}]})
r = appeler(infos2.chercher_adresse, adresse="8 bd du Port Amiens")
verifier("adresse : complète, avec ses coordonnées", texte(r),
         "8 Boulevard du Port 80000 Amiens (Somme, Hauts-de-France). Coordonnées : 49.89, 2.29.")
REPONSES[("data.geopf.fr", "/geocodage/search")] = (503, "en panne")
REPONSES[("api-adresse.data.gouv.fr", "/search/")] = (200, {"features": [
    {"properties": {"name": "Brest", "city": "Brest", "citycode": "29019", "type": "municipality",
                    "context": "29, Finistère, Bretagne"}, "geometry": {"coordinates": [-4.49, 48.39]}}]})
REPONSES[("geo.api.gouv.fr", "/communes/29019")] = (200, {
    "nom": "Brest", "population": 139926, "surface": 4951, "codesPostaux": ["29200"],
    "departement": {"nom": "Finistère"}, "region": {"nom": "Bretagne"}})
r = appeler(infos2.chercher_adresse, adresse="Brest")
verifier("adresse : la Géoplateforme en panne → l'ancienne adresse, et la commune en détail", texte(r),
         "Brest (Finistère, Bretagne) : 139 926 habitants, code postal 29200, 49,5 km², code INSEE 29019.")

# --- Produits ---------------------------------------------------------------------------------------------
REPONSES[("search.openfoodfacts.org", "/search")] = (200, {"hits": [
    {"code": "3017620422003", "product_name": "Nutella", "brands": ["xx:ferrero"]},
    {"code": "1", "product_name": "Nutella Biscuits", "brands": ["xx:ferrero"]}]})
REPONSES[("world.openfoodfacts.org", "/api/v2/product/3017620422003.json")] = (200, {"status": 1, "product": {
    "product_name_fr": "Nutella", "brands": "Ferrero", "quantity": "400 g", "nutriscore_grade": "e", "nova_group": 4,
    "nutriments": {"energy-kcal_100g": 539, "sugars_100g": 56.3, "fat_100g": 30.9, "salt_100g": 0.107},
    "ingredients_text_fr": "Sucre, huile de palme, noisettes 13 %", "allergens_tags": ["en:milk", "en:nuts"]}})
r = appeler(infos2.chercher_produit, produit="Nutella")
contient("produit : notes, nutriments, allergènes", texte(r),
         "Open Food Facts : Nutella (Ferrero, 400 g).", "Nutri-Score E, NOVA 4 (ultra-transformé).",
         "Pour 100 g : 539 kcal, 56,3 g de sucres, 30,9 g de matières grasses, 0,11 g de sel.",
         "Allergènes : lait, fruits à coque.", "Autres produits trouvés : Nutella Biscuits (ferrero).", DEHORS)
REPONSES[("world.openfoodfacts.org", "/api/v2/product/12345678.json")] = (404, {"status": 0})
r = appeler(infos2.chercher_produit, produit="1234 5678")
verifier("produit : un code-barres inconnu → il le dit", texte(r), "Open Food Facts ne connaît pas le code-barres 12345678.")

# --- Qualité de l'air -------------------------------------------------------------------------------------
REPONSES[("air-quality-api.open-meteo.com", "/v1/air-quality")] = (200, {"current": {
    "european_aqi": 32, "pm2_5": 8.4, "pm10": 14.2, "ozone": 51, "nitrogen_dioxide": 20.1,
    "grass_pollen": 3.2, "birch_pollen": 0.2}})
REPONSES[("api.open-meteo.com", "/v1/forecast")] = (200, {"daily": {
    "sunrise": ["2026-11-24T08:13"], "sunset": ["2026-11-24T16:57"], "uv_index_max": [1.2]}})
r = appeler(infos2.chercher_qualite_air, lieu="Paris")
verifier("air : indice, polluants, pollens, soleil", texte(r),
         "Paris (Île-de-France, France) : qualité de l'air correcte (indice européen 32 ; sous 20 c'est bon, au-dessus "
         "de 80 très mauvais). Particules fines PM2,5 8 µg/m³, PM10 14 µg/m³, ozone 51 µg/m³, dioxyde d'azote "
         "20 µg/m³.\nPollens dans l'air : graminées 3 grains/m³.\n"
         "Soleil : lever à 8 h 13, coucher à 16 h 57 ; indice UV au plus fort 1,2 (faible).")
r = appeler(infos2.chercher_qualite_air, lieu="Atlantide")
verifier("air : lieu inconnu → il le dit", texte(r), "Je ne trouve pas de lieu nommé « Atlantide ».")

# --- Carburants -------------------------------------------------------------------------------------------
REPONSES[("data.economie.gouv.fr",
          "/api/explore/v2.1/catalog/datasets/prix-des-carburants-en-france-flux-instantane-v2/records")] = (200, {
    "results": [{"adresse": "12 RUE DE SIAM", "ville": "BREST", "cp": "29200", "gazole_prix": 1.659,
                 "gazole_maj": "2026-11-24T07:00:00+01:00"}]})
r = appeler(infos2.chercher_carburant, lieu="29200", carburant="diesel")
verifier("carburant : le gazole le moins cher", texte(r),
         "Gazole à 29200, les moins chers :\n1. 1,659 € le litre, 12 Rue de Siam, Brest (relevé aujourd'hui).\n"
         "(Prix déclarés par les stations au site de l'État.)")
verifier("carburant : le code postal seul entre dans la requête",
         derniere("data.economie.gouv.fr").url.params["where"], 'cp in ("29200") and gazole_prix is not null')
r = appeler(infos2.chercher_carburant, lieu="Brest", carburant="kérosène")
verifier("carburant inconnu → il le dit", "Je ne connais pas le carburant « kérosène »" in texte(r), True)

# --- Livres -----------------------------------------------------------------------------------------------
REPONSES[("openlibrary.org", "/search.json")] = (200, {"docs": [
    {"key": "/works/OL1W", "title": "L'Étranger", "author_name": ["Albert Camus"], "first_publish_year": 1942,
     "number_of_pages_median": 159, "edition_count": 1}]})
r = appeler(infos2.chercher_livre, titre="978-2-07-036002-4")
verifier("livre : un ISBN cherché comme ISBN", derniere("openlibrary.org").url.params.get("isbn"), "9782070360024")
verifier("livre : la ligne", texte(r).splitlines()[1],
         "1. « L'Étranger », Albert Camus, paru en 1942, environ 159 pages, 1 édition. Lien : https://openlibrary.org/works/OL1W")

# --- Musique ----------------------------------------------------------------------------------------------
REPONSES[("itunes.apple.com", "/search")] = (200, {"results": [
    {"trackName": "Get Lucky", "artistName": "Daft Punk", "collectionName": "Random Access Memories",
     "releaseDate": "2013-04-19T07:00:00Z", "trackTimeMillis": 369000, "trackViewUrl": "https://music.apple.com/x"}]})
r = appeler(infos2.chercher_musique, recherche="Get Lucky", sorte="chansons")
verifier("musique : « chansons » = morceau", texte(r).splitlines()[1],
         "1. « Get Lucky », Daft Punk, album « Random Access Memories » (2013), 6 min 09. Lien : https://music.apple.com/x")
r = appeler(infos2.chercher_musique, recherche="Dune", sorte="film")
verifier("musique : pas de films", "Je ne sais pas chercher « film »" in texte(r), True)

# --- Tech -------------------------------------------------------------------------------------------------
REPONSES[("hn.algolia.com", "/api/v1/search")] = (200, {"hits": [
    {"title": "Petit", "points": 3, "num_comments": 0, "created_at": "2026-11-24T10:00:00Z", "objectID": "1"},
    {"title": "Rust 2.0 is out", "points": 812, "num_comments": 300, "created_at": "2026-11-23T08:00:00Z",
     "url": "https://blog.rust-lang.org/x"}]})
r = appeler(infos2.chercher_tech, sujet="Rust", n=2)
verifier("tech : les plus populaires d'abord", texte(r).splitlines()[1].startswith("1. Rust 2.0 is out — 812 points"), True)
verifier("tech : la fenêtre de temps envoyée", derniere("hn.algolia.com").url.params["numericFilters"],
         f"created_at_i>{int(MAINTENANT.timestamp()) - 30 * 86400}")

# --- Séismes ----------------------------------------------------------------------------------------------
REPONSES[("earthquake.usgs.gov", "/fdsnws/event/1/query")] = (200, {"features": [
    {"properties": {"mag": 6.1, "place": "18 km ESE of Güvercinlik, Turkey", "time": 1764000000000, "tsunami": 1},
     "geometry": {"coordinates": [27.6, 37.0, 10.2]}}]})
r = appeler(infos2.chercher_seismes)
contient("séismes : la direction en français, la profondeur, le tsunami", texte(r),
         "Magnitude 6,1, à 18 km à l’est-sud-est de Güvercinlik, Turkey", "à 10 km de profondeur",
         "alerte au tsunami émise")
verifier("séismes : 4,5 au moins dans le monde, les plus forts", (
    derniere("earthquake.usgs.gov").url.params["minmagnitude"], derniere("earthquake.usgs.gov").url.params["orderby"]),
         ("4.5", "magnitude"))
r = appeler(infos2.chercher_seismes, magnitude="fort")
verifier("séismes : une magnitude qui n'est pas un nombre", r.get("is_error"), True)

# --- Traduction -------------------------------------------------------------------------------------------
REPONSES[("api.mymemory.translated.net", "/get")] = (200, {
    "responseStatus": 200, "responseData": {"translatedText": "Hello &amp; welcome", "match": 0.99}})
r = appeler(infos2.chercher_traduction, texte="Bonjour et bienvenue", vers="anglais", de="français")
verifier("traduction : du français vers l'anglais", texte(r),
         f"Traduction du français vers l'anglais (MyMemory) : Hello & welcome\n{DEHORS}")
REPONSES[("api.mymemory.translated.net", "/get")] = (200, {"responseStatus": 429, "quotaFinished": True})
r = appeler(infos2.chercher_traduction, texte="Bonjour", vers="es")
verifier("traduction : quota épuisé → traduis toi-même", "traduis-le toi-même" in texte(r), True)
r = appeler(infos2.chercher_traduction, texte="Bonjour", vers="français", de="fr")
verifier("traduction : déjà dans la langue, sans appel", texte(r), "C'est déjà en français : Bonjour")

# --- Séries -----------------------------------------------------------------------------------------------
REPONSES[("api.tvmaze.com", "/singlesearch/shows")] = (200, {
    "name": "Severance", "network": None, "webChannel": {"name": "Apple TV+"}, "premiered": "2022-02-18",
    "ended": None, "status": "Running", "genres": ["Drama", "Mystery"], "rating": {"average": 8.4},
    "summary": "<p>Mark leads a team.</p>", "url": "https://www.tvmaze.com/shows/44933/severance",
    "_embedded": {"nextepisode": {"season": 3, "number": 1, "name": "Hello", "airdate": "2026-11-25"}}})
r = appeler(infos2.chercher_serie, nom="Severance")
contient("série : chaîne, statut, genres, prochain épisode", texte(r),
         "TVmaze : « Severance » (Apple TV+), série en cours (depuis 2022), drame, mystère ; note 8,4 sur 10.",
         "Prochain épisode : saison 3, épisode 1, « Hello », demain.", "Résumé (en anglais) : Mark leads a team.")

# --- Code open source (GitHub) ----------------------------------------------------------------------------
REPONSES[("api.github.com", "/search/repositories")] = (200, {"items": [
    {"full_name": "ml-explore/mlx", "stargazers_count": 21500, "language": "C++", "license": {"spdx_id": "MIT"},
     "pushed_at": "2026-11-24T09:00:00Z", "description": "MLX: An array framework for Apple silicon",
     "html_url": "https://github.com/ml-explore/mlx"},
    {"full_name": "a/b", "stargazers_count": 1, "license": {"spdx_id": "NOASSERTION"}, "archived": True,
     "html_url": "https://github.com/a/b"}]})
r = appeler(infos2.chercher_code, recherche="mlx", n=9)
verifier("code : la ligne d'un projet", texte(r).splitlines()[1],
         "1. ml-explore/mlx, 21 500 étoiles, C++, licence MIT, dernière modification aujourd'hui : MLX: An array "
         "framework for Apple silicon. Lien : https://github.com/ml-explore/mlx")
verifier("code : une étoile, pas de licence inconnue, archivé", texte(r).splitlines()[2],
         "2. a/b, 1 étoile, archivé (plus maintenu) Lien : https://github.com/a/b")
verifier("code : 5 au plus demandés", derniere("api.github.com").url.params["per_page"], "5")
REPONSES[("api.github.com", "/search/repositories")] = (403, {"message": "API rate limit exceeded"})
r = appeler(infos2.chercher_code, recherche="mlx")
verifier("code : limite de GitHub → réessaie dans une minute", "réessaie dans une minute" in texte(r), True)

# --- Paquets (PyPI, npm) ----------------------------------------------------------------------------------
REPONSES[("pypi.org", "/pypi/httpx/json")] = (200, {
    "info": {"name": "httpx", "version": "0.28.1", "summary": "The next generation HTTP client.",
             "license_expression": "BSD-3-Clause", "requires_python": ">=3.8",
             "project_urls": {"Source": "https://github.com/encode/httpx"}},
    "urls": [{"upload_time": "2024-12-06T15:37:21"}]})
REPONSES[("registry.npmjs.org", "/httpx")] = (200, {
    "name": "httpx", "dist-tags": {"latest": "3.0.1"}, "time": {"3.0.1": "2024-04-01T10:00:00Z"},
    "versions": {"3.0.1": {"license": "MIT"}}, "description": "http(s) module with power",
    "homepage": "https://github.com/JacksonTian/httpx"})
r = appeler(infos2.chercher_paquet, nom="httpx")
verifier("paquet : PyPI et npm, chaque description finit par un point", texte(r),
         "PyPI (Python) : httpx, version 0.28.1, sortie le 6 décembre 2024, Python >=3.8, licence BSD-3-Clause. The "
         "next generation HTTP client. Lien : https://github.com/encode/httpx\n"
         "npm (JavaScript) : httpx, version 3.0.1, sortie le 1er avril 2024, licence MIT. http(s) module with power. "
         f"Lien : https://github.com/JacksonTian/httpx\n{DEHORS}")
VUES.clear()
r = appeler(infos2.chercher_paquet, nom="httpx", ou="python")
verifier("paquet : « python » → PyPI seulement", [v.url.host for v in VUES], ["pypi.org"])
VUES.clear()
appeler(infos2.chercher_paquet, nom="@anthropic-ai/sdk")
verifier("paquet : un nom @… → npm seulement", [v.url.host for v in VUES], ["registry.npmjs.org"])
VUES.clear()
r = appeler(infos2.chercher_paquet, nom="../../etc/passwd")
verifier("paquet : un nom qui n'en est pas → refusé sans appel", (len(VUES), "nom exact du paquet" in texte(r)),
         (0, True))
r = appeler(infos2.chercher_paquet, nom="nexistepas")
verifier("paquet : introuvable des deux côtés", texte(r), "Aucun paquet nommé « nexistepas » sur PyPI ni sur npm.")

# --- Articles (OpenAlex) ----------------------------------------------------------------------------------
REPONSES[("api.openalex.org", "/works")] = (200, {"results": [
    {"display_name": "Sleep and caffeine", "publication_year": 2023, "cited_by_count": 1234,
     "authorships": [{"author": {"display_name": n}} for n in ("A. Un", "B. Deux", "C. Trois", "D. Quatre")],
     "primary_location": {"source": {"display_name": "Sleep Medicine"}},
     "open_access": {"oa_url": "https://libre.example/pdf"}, "doi": "https://doi.org/10.1/x"}]})
r = appeler(infos2.chercher_articles, sujet="sleep caffeine", depuis=2020)
verifier("articles : la ligne, le lien libre d'abord", texte(r).splitlines()[1],
         "1. « Sleep and caffeine », A. Un, B. Deux, C. Trois, et d'autres, 2023, Sleep Medicine, cité 1 234 fois "
         "(lisible gratuitement). Lien : https://libre.example/pdf")
verifier("articles : depuis 2020", derniere("api.openalex.org").url.params["filter"], "from_publication_date:2020-01-01")
appeler(infos2.chercher_articles, sujet="sleep caffeine", depuis=42)
verifier("articles : une année absurde est ignorée", "filter" in derniere("api.openalex.org").url.params, False)

# --- L'heure dans le monde --------------------------------------------------------------------------------
r = appeler(infos2.chercher_heure, lieu="Tokyo")
verifier("heure : Tokyo, déjà demain", texte(r),
         "À Tokyo (Japon), il est 4 h, mercredi 25 novembre, on y est déjà demain "
         "(8 heures de plus qu'à Paris, fuseau Asia/Tokyo).")
r = appeler(infos2.chercher_heure, lieu="paris")
verifier("heure : Paris, la même heure", texte(r),
         "À Paris (France), il est 20 h, mardi 24 novembre (la même heure qu'à Paris, fuseau Europe/Paris).")
verifier("heure : pas de texte venu d'ailleurs à signaler (rien qu'un nom de ville)", DEHORS in texte(r), False)

# --- Pannes : jamais de plantage --------------------------------------------------------------------------
REPONSES[("api.openalex.org", "/works")] = (500, "panne")
r = appeler(infos2.chercher_articles, sujet="sommeil")
verifier("panne → erreur claire", (r.get("is_error"), texte(r)), (True, "OpenAlex ne répond pas, essaie WebSearch."))
REPONSES[("api.tvmaze.com", "/singlesearch/shows")] = (200, "<html>pas du JSON</html>")
r = appeler(infos2.chercher_serie, nom="Severance")
verifier("réponse illisible → erreur claire", (r.get("is_error"), texte(r)), (True, "TVmaze ne répond pas, essaie WebSearch."))

# --- Permissions et enregistrement ------------------------------------------------------------------------
noms = [o.name for o in infos2.OUTILS_INFOS2]
verifier("les quinze outils", len(noms), 15)
verifier("tous commencent par chercher_ (une lecture)", all(n.startswith("chercher_") for n in noms), True)
verifier("tous donnés à Steve (outils.OUTILS)", all(o in outils.OUTILS for o in infos2.OUTILS_INFOS2), True)
verifier("pas de doublon de nom dans outils.OUTILS", len({o.name for o in outils.OUTILS}), len(outils.OUTILS))
for nom in noms:
    complet = f"mcp__{outils.NOM_SERVEUR}__{nom}"
    verifier(f"{nom} : une lecture", permissions.est_lecture(complet), True)
    verifier(f"{nom} : hors de sa ronde (un mail piégé ne fait rien sortir)", fond.outil_de_lecture(complet), False)
    verifier(f"{nom} : un texte du dehors", permissions.lit_dehors(complet), True)
    verifier(f"{nom} : il le fait sans demander", permissions.politique(complet, {}), permissions.Decision.OUI)


# --- Un mini essai en vrai (facultatif) -------------------------------------------------------------------
def reseau() -> bool:
    if os.environ.get("JERVIS_TEST_RESEAU", "oui").lower() in {"non", "off", "0"}:
        return False
    try:
        httpx.get("https://geocoding-api.open-meteo.com", timeout=3)
        return True
    except Exception:
        return False


infos.TRANSPORT = None
infos._maintenant = lambda: datetime.now(infos.PARIS)
infos._aujourdhui = lambda: datetime.now(infos.PARIS).date()
if reseau():
    r = appeler(infos2.chercher_heure, lieu="Tokyo")
    verifier("en vrai : l'heure à Tokyo", (r.get("is_error", False), texte(r).startswith("À Tokyo")), (False, True))
    r = appeler(infos2.chercher_paquet, nom="httpx", ou="python")
    verifier("en vrai : PyPI connaît httpx", texte(r).startswith("PyPI (Python) : httpx, version"), True)
else:
    print("(pas de réseau : essai en vrai sauté)")

print(f"\n{'✅' if not echecs else '❌'} infos2 : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
