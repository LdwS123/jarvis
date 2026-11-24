"""Ses outils « infos » : actualité, météo, taux de change, Wikipédia, jours fériés, cryptos (jervis/infos.py).

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_infos.py

Sans réseau : un faux transport httpx (httpx.MockTransport) joue les réponses des services. À la fin, un mini essai
en vrai (Open-Meteo, Wikipédia), sauté s'il n'y a pas de réseau. JERVIS_TEST_RESEAU=non pour le sauter d'office.
"""

import asyncio
import os
import threading
from datetime import date, datetime

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import httpx  # noqa: E402

from jervis import config, fond, infos, outils, permissions  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


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


infos.TRANSPORT = httpx.MockTransport(repondre)
MAINTENANT = datetime(2026, 11, 24, 20, 0, tzinfo=infos.PARIS)
infos._maintenant = lambda: MAINTENANT
infos._aujourdhui = lambda: MAINTENANT.date()

# --- Réglages, User-Agent ---------------------------------------------------------------------------------
verifier("réglage : délai court par défaut", config.INFOS_DELAI_S, 8.0)
REPONSES[("news.google.com", "/rss")] = (200, "<rss><channel></channel></rss>")
appeler(infos.chercher_actualites)
verifier("User-Agent générique, rien de perso", VUES[-1].headers["user-agent"], "Jervis/1.0")

# --- L'actualité ------------------------------------------------------------------------------------------
RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Google Actualités</title>
<item><title>Vieille nouvelle sur Nvidia - Les Echos</title><link>https://news.google.com/rss/articles/A1</link>
<pubDate>Sat, 22 Nov 2026 07:05:00 GMT</pubDate><source url="https://www.lesechos.fr">Les Echos</source></item>
<item><title>Nvidia : Trump aurait gagné 15 millions - Le Monde.fr</title><link>https://news.google.com/rss/articles/B2</link>
<pubDate>Tue, 24 Nov 2026 11:56:00 GMT</pubDate><source url="https://www.lemonde.fr">Le Monde.fr</source></item>
<item><title>Nvidia recule en Bourse - Libération</title><link>https://news.google.com/rss/articles/C3</link>
<pubDate>Mon, 23 Nov 2026 08:00:00 GMT</pubDate><source url="https://www.liberation.fr">Libération</source></item>
</channel></rss>"""
articles = infos.lire_rss(RSS)
verifier("RSS : trois articles", len(articles), 3)
verifier("RSS : le « - source » est ôté du titre", articles[1]["titre"], "Nvidia : Trump aurait gagné 15 millions")
verifier("RSS : la source", articles[1]["source"], "Le Monde.fr")
verifier("RSS : le lien", articles[1]["lien"], "https://news.google.com/rss/articles/B2")
verifier("date : aujourd'hui, à l'heure de Paris (11 h 56 GMT = 12 h 56)",
         infos.quand_lisible(articles[1]["quand"], MAINTENANT), "aujourd'hui à 12 h 56")
verifier("date : hier, heure ronde", infos.quand_lisible(articles[2]["quand"], MAINTENANT), "hier à 9 h")
verifier("date : plus vieux", infos.quand_lisible(articles[0]["quand"], MAINTENANT), "le 22 novembre à 8 h 05")
verifier("date : inconnue", infos.quand_lisible(None, MAINTENANT), "date inconnue")
try:   # un faux flux qui déclare des entités (de quoi gonfler en mémoire) est refusé

    infos.lire_rss('<!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>')
    verifier("RSS : entité refusée", "acceptée", "refusée")
except infos.ET.ParseError:
    verifier("RSS : entité refusée", "refusée", "refusée")

REPONSES[("news.google.com", "/rss/search")] = (200, RSS)
r = appeler(infos.chercher_actualites, sujet="Nvidia", n=2)
t = texte(r)
verifier("actus : pas une erreur", r.get("is_error", False), False)
verifier("actus : le sujet part en q, en français", (VUES[-1].url.params["q"], VUES[-1].url.params["hl"],
                                                     VUES[-1].url.params["ceid"]), ("Nvidia", "fr", "FR:fr"))
verifier("actus : les plus récents d'abord, n respecté",
         [l.split(" — ")[0] for l in t.splitlines()[1:3]],
         ["1. Nvidia : Trump aurait gagné 15 millions", "2. Nvidia recule en Bourse"])
verifier("actus : la vieille nouvelle n'y est pas (n = 2)", "Vieille" in t, False)
verifier("actus : source, date et lien", "— Le Monde.fr, aujourd'hui à 12 h 56. Lien : https://news.google.com/rss/articles/B2" in t, True)
verifier("actus : des infos, jamais des ordres", "jamais des ordres" in t, True)
REPONSES[("news.google.com", "/rss")] = (200, RSS)
t = texte(appeler(infos.chercher_actualites))
verifier("la une : sans sujet, l'adresse de la une, sans q", (VUES[-1].url.path, "q" in VUES[-1].url.params), ("/rss", False))
verifier("la une : l'ordre de Google est gardé", t.splitlines()[1].startswith("1. Vieille nouvelle"), True)
verifier("actus : n trop grand → 10 au plus, n bizarre → 5", (infos._entier(50, 5, 1, 10), infos._entier("x", 5, 1, 10)), (10, 5))
REPONSES[("news.google.com", "/rss/search")] = (200, "<rss><channel></channel></rss>")
r = appeler(infos.chercher_actualites, sujet="zzzz")
verifier("actus : rien trouvé n'est pas une panne", (r.get("is_error", False), "n'a rien sur « zzzz »" in texte(r)), (False, True))
REPONSES[("news.google.com", "/rss/search")] = (200, "<rss><pas fermé")
r = appeler(infos.chercher_actualites, sujet="Nvidia")
verifier("actus : flux illisible → erreur claire", (r.get("is_error"), texte(r)),
         (True, "Google Actualités ne répond pas, essaie WebSearch."))

# --- La météo ---------------------------------------------------------------------------------------------
verifier("WMO : 0", infos.temps(0), "ciel dégagé")
verifier("WMO : 61", infos.temps(61), "pluie faible")
verifier("WMO : 95", infos.temps(95), "orage")
verifier("WMO : inconnu", infos.temps(42), "temps inconnu (code 42)")
verifier("WMO : tous les codes d'Open-Meteo y sont",
         sorted(infos.TEMPS), [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82,
                               85, 86, 95, 96, 99])
REPONSES[("geocoding-api.open-meteo.com", "/v1/search")] = (200, {"results": [
    {"name": "Lyon", "latitude": 45.74906, "longitude": 4.84789, "country": "France", "admin1": "Auvergne-Rhône-Alpes"}]})
REPONSES[("api.open-meteo.com", "/v1/forecast")] = (200, {
    "current": {"temperature_2m": 9.2, "apparent_temperature": 7.5, "weather_code": 61, "wind_speed_10m": 4.7,
                "relative_humidity_2m": 81},
    "daily": {"time": ["2026-11-24", "2026-11-25", "2026-11-26"], "weather_code": [61, 80, 0],
              "temperature_2m_max": [10.2, 11.8, 9.6], "temperature_2m_min": [6.6, 8.4, 3.1],
              "precipitation_probability_max": [100, 100, None]}})
r = appeler(infos.chercher_meteo, lieu="Lyon", jours=3)
t = texte(r)
verifier("météo : pas une erreur", r.get("is_error", False), False)
verifier("météo : géocodage en français, un seul lieu",
         (VUES[-2].url.params["name"], VUES[-2].url.params["language"], VUES[-2].url.params["count"]), ("Lyon", "fr", "1"))
verifier("météo : prévisions au bon endroit, fuseau auto",
         (VUES[-1].url.params["latitude"], VUES[-1].url.params["timezone"], VUES[-1].url.params["forecast_days"]),
         ("45.74906", "auto", "3"))
verifier("météo : en ce moment", t.splitlines()[0],
         "Lyon (Auvergne-Rhône-Alpes, France) : en ce moment 9 °C (ressenti 8 °C), pluie faible, vent 5 km/h, humidité 81 %.")
verifier("météo : aujourd'hui", t.splitlines()[1], "Aujourd'hui : pluie faible, de 7 °C à 10 °C, risque de pluie 100 %.")
verifier("météo : demain", t.splitlines()[2], "Demain : averses faibles, de 8 °C à 12 °C, risque de pluie 100 %.")
verifier("météo : après-demain, par son nom, sans risque inconnu", t.splitlines()[3], "Jeudi 26 novembre : ciel dégagé, de 3 °C à 10 °C.")
appeler(infos.chercher_meteo, lieu="Lyon", jours=30)
verifier("météo : 7 jours au plus", VUES[-1].url.params["forecast_days"], "7")
REPONSES[("geocoding-api.open-meteo.com", "/v1/search")] = (200, {"generationtime_ms": 0.07})
r = appeler(infos.chercher_meteo, lieu="Zzzqqxx")
verifier("météo : lieu inconnu, dit simplement", (r.get("is_error", False), texte(r)), (False, "Je ne trouve pas de lieu nommé « Zzzqqxx »."))
REPONSES[("geocoding-api.open-meteo.com", "/v1/search")] = (500, "panne")
r = appeler(infos.chercher_meteo, lieu="Lyon")
verifier("météo : service en panne → erreur claire", (r.get("is_error"), texte(r)), (True, "Open-Meteo ne répond pas, essaie WebSearch."))

# --- Les taux de change -----------------------------------------------------------------------------------
verifier("monnaie : codes et noms", [infos.code_monnaie(m) for m in ("usd", "dollars", "Dollars américains",
                                                                      "livre sterling", "€", "Yen", "truc")],
         ["USD", "USD", "USD", "GBP", "EUR", "JPY", None])
REPONSES[("api.frankfurter.dev", "/v1/latest")] = (200, {"amount": 100.0, "base": "EUR", "date": "2026-11-24",
                                                          "rates": {"USD": 109.63}})
r = appeler(infos.chercher_taux_de_change, montant=100, de="euros", vers="dollars")
verifier("change : 100 € en dollars", (r.get("is_error", False), texte(r)),
         (False, "100,00 EUR = 109,63 USD (taux de la BCE du 24/11/2026)."))
verifier("change : les bons paramètres", (VUES[-1].url.params["from"], VUES[-1].url.params["to"], VUES[-1].url.params["amount"]),
         ("EUR", "USD", "100.0"))
verifier("change : grand nombre à la française", infos._nombre(1234567.891), "1 234 567,89")
r = appeler(infos.chercher_taux_de_change, montant="12,5", de="EUR", vers="EUR")
verifier("change : même monnaie, sans réseau", texte(r), "12,50 EUR = 12,50 EUR.")
r = appeler(infos.chercher_taux_de_change, de="EUR", vers="truc")
verifier("change : monnaie inconnue", (r.get("is_error", False), "« truc »" in texte(r)), (False, True))
REPONSES[("api.frankfurter.dev", "/v1/latest")] = (404, {"message": "not found"})
r = appeler(infos.chercher_taux_de_change, de="EUR", vers="XYZ")
verifier("change : code que la BCE ne suit pas", (r.get("is_error", False), "XYZ" in texte(r)), (False, True))


def dev_injoignable(requete):
    raise httpx.ConnectError("pas de réseau", request=requete)


REPONSES[("api.frankfurter.dev", "/v1/latest")] = dev_injoignable
REPONSES[("api.frankfurter.app", "/latest")] = (200, {"amount": 1.0, "base": "EUR", "date": "2026-11-24",
                                                       "rates": {"GBP": 0.8712}})
r = appeler(infos.chercher_taux_de_change, de="EUR", vers="GBP")
verifier("change : .dev injoignable → repli sur .app", (texte(r), VUES[-1].url.host),
         ("1,00 EUR = 0,87 GBP (taux de la BCE du 24/11/2026).", "api.frankfurter.app"))
REPONSES[("api.frankfurter.app", "/latest")] = dev_injoignable
r = appeler(infos.chercher_taux_de_change, de="EUR", vers="GBP")
verifier("change : les deux injoignables → erreur claire", (r.get("is_error"), texte(r)),
         (True, "Frankfurter ne répond pas, essaie WebSearch."))

# --- Wikipédia --------------------------------------------------------------------------------------------
RESUME = {"type": "standard", "title": "Tour Eiffel", "description": "tour de fer puddlé de 330 mètres à Paris",
          "extract": "La tour Eiffel est une tour autoportante de fer puddlé de 330 m de hauteur située à Paris.",
          "content_urls": {"desktop": {"page": "https://fr.wikipedia.org/wiki/Tour_Eiffel"}}}


def resume(requete):
    titre = requete.url.path.rsplit("/", 1)[-1]
    if titre == "Tour_Eiffel":
        return 200, RESUME
    if titre == "Mercure":
        return 200, {"type": "disambiguation", "title": "Mercure", "extract": "Mercure peut désigner :",
                     "content_urls": {"desktop": {"page": "https://fr.wikipedia.org/wiki/Mercure"}}}
    return 404, {"status": 404}


def recherche(requete):
    trouve = {"la tour eifel": [{"title": "Tour Eiffel"}]}.get(requete.url.params["srsearch"], [])
    return 200, {"query": {"search": trouve}}


for chemin in ("Tour_Eiffel", "Mercure", "la_tour_eifel", "Blorptuk"):
    REPONSES[("fr.wikipedia.org", f"/api/rest_v1/page/summary/{chemin}")] = resume
REPONSES[("fr.wikipedia.org", "/w/api.php")] = recherche
t = texte(appeler(infos.chercher_wikipedia, sujet="Tour Eiffel"))
verifier("wiki : titre exact trouvé du premier coup", (t.startswith("Wikipédia, « Tour Eiffel » (tour de fer puddlé"),
                                                     VUES[-1].url.path), (True, "/api/rest_v1/page/summary/Tour_Eiffel"))
verifier("wiki : le lien", "Lien : https://fr.wikipedia.org/wiki/Tour_Eiffel" in t, True)
t = texte(appeler(infos.chercher_wikipedia, sujet="la tour eifel"))
verifier("wiki : titre pas trouvé → recherche, puis le bon article",
         ("« Tour Eiffel »" in t, [v.url.path for v in VUES[-3:]]),
         (True, ["/api/rest_v1/page/summary/la_tour_eifel", "/w/api.php", "/api/rest_v1/page/summary/Tour_Eiffel"]))
r = appeler(infos.chercher_wikipedia, sujet="Blorptuk")
verifier("wiki : rien du tout", (r.get("is_error", False), texte(r)), (False, "Wikipédia n'a pas d'article sur « Blorptuk »."))
verifier("wiki : homonymie → il demande lequel", "plusieurs sens" in texte(appeler(infos.chercher_wikipedia, sujet="Mercure")), True)
REPONSES[("fr.wikipedia.org", "/api/rest_v1/page/summary/Tour_Eiffel")] = (503, "occupé")
r = appeler(infos.chercher_wikipedia, sujet="Tour Eiffel")
verifier("wiki : panne → erreur claire", (r.get("is_error"), texte(r)), (True, "Wikipédia ne répond pas, essaie WebSearch."))

# --- Les jours fériés -------------------------------------------------------------------------------------
FERIES_2026 = [{"date": "2026-01-01", "localName": "Jour de l'an", "global": True},
               {"date": "2026-11-11", "localName": "Armistice 1918", "global": True},
               {"date": "2026-12-25", "localName": "Noël", "global": True},
               {"date": "2026-12-26", "localName": "Saint-Étienne", "global": False}]
FERIES_2027 = [{"date": "2027-01-01", "localName": "Jour de l'an", "global": True}]
REPONSES[("date.nager.at", "/api/v3/PublicHolidays/2026/FR")] = (200, FERIES_2026)
REPONSES[("date.nager.at", "/api/v3/PublicHolidays/2027/FR")] = (200, FERIES_2027)
REPONSES[("date.nager.at", "/api/v3/PublicHolidays/2026/BE")] = (200, FERIES_2026[:1])
r = appeler(infos.chercher_jours_feries)
t = texte(r)
verifier("fériés : France et cette année par défaut", VUES[-1].url.path, "/api/v3/PublicHolidays/2026/FR")
verifier("fériés : la liste, 1er écrit comme on le dit", "Jour de l'an, jeudi 1er janvier" in t, True)
verifier("fériés : un férié régional est signalé", "Saint-Étienne, samedi 26 décembre (seulement dans certaines régions)" in t, True)
verifier("fériés : le prochain (on est le 24 novembre)", t.splitlines()[-1], "Le prochain : Noël, vendredi 25 décembre, dans 31 jours.")
infos._aujourdhui = lambda: date(2026, 12, 25)
verifier("fériés : c'est aujourd'hui", texte(appeler(infos.chercher_jours_feries)).splitlines()[-1],
         "Le prochain : Noël, vendredi 25 décembre, aujourd'hui.")
infos._aujourdhui = lambda: date(2026, 12, 28)
verifier("fériés : plus rien cette année → celui de l'an prochain",
         texte(appeler(infos.chercher_jours_feries, annee=2026, pays="France")).splitlines()[-1],
         "Le prochain : Jour de l'an, vendredi 1er janvier 2027, dans 4 jours.")
infos._aujourdhui = lambda: MAINTENANT.date()
avant = len(VUES)
t = texte(appeler(infos.chercher_jours_feries, pays="Belgique"))
verifier("fériés : un pays par son nom", VUES[avant].url.path, "/api/v3/PublicHolidays/2026/BE")
verifier("fériés : l'an prochain introuvable → la liste de cette année quand même",
         (t.startswith("Jours fériés 2026 (BE)"), "prochain" in t), (True, False))
REPONSES[("date.nager.at", "/api/v3/PublicHolidays/2026/XX")] = (404, {"title": "Unknown country code"})
r = appeler(infos.chercher_jours_feries, pays="XX")
verifier("fériés : pays inconnu du service", (r.get("is_error", False), "« XX »" in texte(r)), (False, True))
r = appeler(infos.chercher_jours_feries, pays="Atlantide")
verifier("fériés : pays inconnu, sans réseau", (r.get("is_error", False), "« Atlantide »" in texte(r)), (False, True))
REPONSES[("date.nager.at", "/api/v3/PublicHolidays/2026/FR")] = dev_injoignable
r = appeler(infos.chercher_jours_feries)
verifier("fériés : réseau coupé → erreur claire", (r.get("is_error"), texte(r)), (True, "Nager.Date ne répond pas, essaie WebSearch."))

# --- Les cryptos ------------------------------------------------------------------------------------------


def prix(requete):
    cours = {"bitcoin": {"eur": 54609, "eur_24h_change": -3.3186}, "solana": {"eur": 112.345, "eur_24h_change": 2.04},
             "pepe": {"eur": 0.0000081234}}
    ident = requete.url.params["ids"]
    return 200, ({ident: cours[ident]} if ident in cours else {})


REPONSES[("api.coingecko.com", "/api/v3/simple/price")] = prix
REPONSES[("api.coingecko.com", "/api/v3/search")] = lambda q: (200, {"coins": [{"id": "pepe", "name": "Pepe"}]}
                                                                if q.url.params["query"] == "pepe" else {"coins": []})
r = appeler(infos.chercher_crypto, monnaie="BTC")
verifier("crypto : BTC → bitcoin, en euros", (VUES[-1].url.params["ids"], VUES[-1].url.params["vs_currencies"]), ("bitcoin", "eur"))
verifier("crypto : la phrase", (r.get("is_error", False), texte(r)),
         (False, "Bitcoin : 54 609 €, en baisse de 3,3 % sur 24 h (CoinGecko)."))
verifier("crypto : en hausse", texte(appeler(infos.chercher_crypto, monnaie="solana")),
         "Solana : 112,34 €, en hausse de 2,0 % sur 24 h (CoinGecko).")
verifier("crypto : une autre, cherchée par son nom, petit prix lisible",
         texte(appeler(infos.chercher_crypto, monnaie="pepe")), "Pepe : 0,000008123 € (CoinGecko).")
r = appeler(infos.chercher_crypto, monnaie="zorglubcoin")
verifier("crypto : inconnue", (r.get("is_error", False), "zorglubcoin" in texte(r)), (False, True))
REPONSES[("api.coingecko.com", "/api/v3/simple/price")] = (429, {"status": {"error_code": 429}})
r = appeler(infos.chercher_crypto, monnaie="bitcoin")
verifier("crypto : trop de demandes → erreur claire", (r.get("is_error"), texte(r)), (True, "CoinGecko ne répond pas, essaie WebSearch."))


# --- Délai dépassé : jamais de plantage -------------------------------------------------------------------
def trop_long(requete):
    raise httpx.ReadTimeout("trop long", request=requete)


REPONSES[("news.google.com", "/rss")] = trop_long
r = appeler(infos.chercher_actualites)
verifier("délai dépassé → erreur claire", (r.get("is_error"), texte(r)), (True, "Google Actualités ne répond pas, essaie WebSearch."))

# --- Permissions et enregistrement ------------------------------------------------------------------------
noms = [o.name for o in infos.OUTILS_INFOS]
verifier("les six outils", noms, ["chercher_actualites", "chercher_meteo", "chercher_taux_de_change",
                                  "chercher_wikipedia", "chercher_jours_feries", "chercher_crypto"])
verifier("tous donnés à Steve (outils.OUTILS)", all(o in outils.OUTILS for o in infos.OUTILS_INFOS), True)
verifier("pas de doublon de nom dans outils.OUTILS", len({o.name for o in outils.OUTILS}), len(outils.OUTILS))
for nom in noms:
    complet = f"mcp__{outils.NOM_SERVEUR}__{nom}"
    if hasattr(permissions, "est_lecture"):
        verifier(f"{nom} : une lecture (permissions.est_lecture)", permissions.est_lecture(complet), True)
    verifier(f"{nom} : hors de sa ronde, comme le web (un mail piégé ne fait rien sortir)",
             fond.outil_de_lecture(complet), False)
    if hasattr(permissions, "lit_dehors"):
        verifier(f"{nom} : un texte du dehors (permissions.lit_dehors)", permissions.lit_dehors(complet), True)
    verifier(f"{nom} : il le fait sans demander", permissions.politique(complet, {}), permissions.Decision.OUI)


# --- Un mini essai en vrai (facultatif) -------------------------------------------------------------------
def reseau() -> bool:
    if os.environ.get("JERVIS_TEST_RESEAU", "oui").lower() in {"non", "off", "0"}:
        return False
    try:
        httpx.get("https://api.open-meteo.com", timeout=3)
        return True
    except Exception:
        return False


infos.TRANSPORT = None
infos._maintenant = lambda: datetime.now(infos.PARIS)
infos._aujourdhui = lambda: datetime.now(infos.PARIS).date()
if reseau():
    r = appeler(infos.chercher_meteo, lieu="Paris", jours=1)
    verifier("en vrai : la météo de Paris", (r.get("is_error", False), texte(r).startswith("Paris")), (False, True))
    r = appeler(infos.chercher_wikipedia, sujet="tour eiffel")
    verifier("en vrai : Wikipédia retrouve la tour Eiffel", "Tour Eiffel" in texte(r), True)
else:
    print("(pas de réseau : essai en vrai sauté)")

print(f"\n{'✅' if not echecs else '❌'} infos : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
