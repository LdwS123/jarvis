"""Toutes les API publiques gratuites : le catalogue (chercher_api) et l'appel (appeler_api) (jervis/apis.py).

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_apis.py

Sans réseau ni DNS : un faux transport httpx joue les réponses, un faux résolveur donne les adresses IP. On vérifie
surtout qu'il ne va jamais sur le Mac ni sur ton réseau, même par une redirection. À la fin, un mini essai en vrai
(Pokéapi), sauté s'il n'y a pas de réseau. JERVIS_TEST_RESEAU=non pour le sauter d'office.
"""

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import httpx  # noqa: E402

from jervis import apis, brain, fond, infos, outils, permissions  # noqa: E402

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


DEHORS = infos.DU_DEHORS

# --- Le catalogue : lire le README de public-apis ----------------------------------------------------------
README = """\
# Public APIs

### APILayer APIs
| API | Description | Auth | HTTPS | CORS |
|---|---|---|---|---|
| [IPstack](https://ipstack.com/) | Locate and identify website visitors by IP address | `apiKey` | Yes | Unknown |

### Animals
API | Description | Auth | HTTPS | CORS |
|:---|:---|:---|:---|:---|
| [Dog CEO](https://dog.ceo/dog-api/) | Dog breeds &amp; pictures | No | Yes | Yes |
| [Cat Facts](https://alexwohlbruck.github.io/cat-facts/) | Daily cat facts | `No` | Yes | No |
| [The Dog](https://thedogapi.com/) | A public service all about Dogs | `apiKey` | Yes | No |

### Science & Math
| [Open Notify](http://open-notify.org/Open-Notify-API/) | ISS astronauts,   current location, etc | No | No | No |
| [NASA](https://api.nasa.gov) | NASA data, including imagery | `OAuth` | Yes | No |
| [Reddit](https://www.reddit.com/dev/api) | Homepage of the internet | `User-Agent` | Yes | Unknown |
| Une ligne sans lien | rien | No | Yes | No |
"""
lues = apis.construire(README)
verifier("toutes les lignes avec un lien, sans la partie sponsors", [a["nom"] for a in lues],
         ["Dog CEO", "Cat Facts", "The Dog", "Open Notify", "NASA", "Reddit"])
verifier("une ligne lue en entier", lues[0], {"nom": "Dog CEO", "description": "Dog breeds & pictures",
                                              "categorie": "Animals", "cle": "", "https": True,
                                              "lien": "https://dog.ceo/dog-api/"})
verifier("les clés : No, `No` et User-Agent = sans clé", [a["cle"] for a in lues], ["", "", "apiKey", "", "OAuth", ""])
verifier("http seulement repéré", lues[3]["https"], False)
verifier("espaces ramenés à un", lues[3]["description"], "ISS astronauts, current location, etc")
verifier("la catégorie avec son &", lues[3]["categorie"], "Science & Math")

# --- Le catalogue livré avec Steve -------------------------------------------------------------------------
vrai = json.loads(apis.CATALOGUE.read_text(encoding="utf-8"))["apis"]
verifier("le catalogue livré : plus de 2 000 API", len(vrai) > 2000, True)
verifier("… dont plus de 1 000 sans clé", sum(1 for a in vrai if not a["cle"]) > 1000, True)
verifier("… chacune complète", all(set(a) == {"nom", "description", "categorie", "cle", "https", "lien"} for a in vrai),
         True)
verifier("… avec un vrai lien", all(a["lien"].startswith(("https://", "http://")) for a in vrai), True)
verifier("… plus de 40 catégories", len({a["categorie"] for a in vrai}) > 40, True)

# --- Chercher dans un petit catalogue de test ---------------------------------------------------------------
dossier = Path(tempfile.mkdtemp())
apis.CATALOGUE = dossier / "apis_publiques.json"
apis.CATALOGUE.write_text(json.dumps({"apis": lues}), encoding="utf-8")
apis._cache = apis._index = None

verifier("les mots qui couvrent le plus d'abord", [a["nom"] for a in apis.chercher("iss astronaut space")],
         ["Open Notify"])
verifier("le nom pèse plus que la description", [a["nom"] for a in apis.chercher("dog")], ["Dog CEO"])
verifier("avec clé : seulement si demandé", [a["nom"] for a in apis.chercher("dog", avec_cle=True)],
         ["Dog CEO", "The Dog"])  # à égalité, sans clé d'abord
verifier("le pluriel retrouve le singulier", [a["nom"] for a in apis.chercher("facts")], ["Cat Facts"])
verifier("majuscules et accents ignorés", [a["nom"] for a in apis.chercher("CÂT")], ["Cat Facts"])
verifier("rien", apis.chercher("blockchain"), [])

r = appeler(apis.chercher_api, recherche="dog pictures")
verifier("chercher_api : une lecture réussie", r.get("is_error", False), False)
contient("chercher_api : nom, catégorie, accès, doc", texte(r), "API gratuites sans clé pour « dog pictures »",
         "- Dog CEO (Animals, sans clé) : Dog breeds & pictures Doc : https://dog.ceo/dog-api/", DEHORS)
r = appeler(apis.chercher_api, recherche="nasa imagery", avec_cle=True)
contient("chercher_api : la clé est dite", texte(r), "NASA (Science & Math, clé gratuite à demander (OAuth))")
r = appeler(apis.chercher_api, recherche="iss")
contient("chercher_api : http seulement est dit", texte(r), "Open Notify (Science & Math, sans clé, http seulement)")
r = appeler(apis.chercher_api, recherche="dog", n=500)
verifier("chercher_api : n borné", texte(r).count("\n- ") + texte(r).startswith("- "), 1)
r = appeler(apis.chercher_api, recherche="nasa")
contient("chercher_api : rien sans clé → essaie avec_cle", texte(r), "Rien dans le catalogue pour « nasa » sans clé",
         "avec_cle=true", "WebSearch")
r = appeler(apis.chercher_api)
verifier("chercher_api vide : les catégories (sans clé)", texte(r),
         "4 API sans clé, par catégorie : Animals (2), Science & Math (2). Cherche avec des mots-clés en anglais.")

apis.CATALOGUE = dossier / "absent.json"
apis._cache = apis._index = None
r = appeler(apis.chercher_api, recherche="dog")
verifier("catalogue absent : il dit comment le télécharger", (r.get("is_error"), "python -m jervis.apis" in texte(r)),
         (True, True))
apis.CATALOGUE = dossier / "apis_publiques.json"
apis._cache = apis._index = None

# --- Appeler une API : le faux Internet ---------------------------------------------------------------------
REPONSES: dict[tuple[str, str], object] = {}
VUES: list[httpx.Request] = []
# Le faux DNS : chaque nom a ses adresses. Un nom absent n'existe pas.
DNS = {"api.exemple.org": ["93.184.216.34"], "cdn.exemple.org": ["2606:4700::6810:84e5"],
       "piege.exemple.org": ["127.0.0.1"], "box.exemple.org": ["192.168.1.254"],
       "moitie.exemple.org": ["93.184.216.34", "10.0.0.7"], "nuage.exemple.org": ["169.254.169.254"]}


async def faux_dns(hote: str, _port: int) -> list[str]:
    if hote not in DNS:
        raise OSError("introuvable")
    return DNS[hote]


VRAI_DNS = apis._adresses
apis._adresses = faux_dns


def repondre(requete: httpx.Request) -> httpx.Response:
    VUES.append(requete)
    reponse = REPONSES.get((requete.headers["host"].split(":")[0], requete.url.path))
    if callable(reponse):
        reponse = reponse(requete)
    if reponse is None:
        return httpx.Response(404, json={"message": "not found"})
    if isinstance(reponse, httpx.Response):
        return reponse
    code, corps = reponse
    if isinstance(corps, str):
        return httpx.Response(code, text=corps)
    return httpx.Response(code, json=corps)


infos.TRANSPORT = httpx.MockTransport(repondre)

# Ce qu'il refuse avant même de partir : rien ne doit sortir.
for url, motif in [
    ("http://127.0.0.1:8765/etat", "jamais sur le Mac"), ("http://localhost/", "jamais sur le Mac"),
    ("http://LOCALHOST./", "jamais sur le Mac"), ("http://tableau.localhost/", "jamais sur le Mac"),
    ("http://[::1]/", "jamais sur le Mac"), ("http://[::ffff:127.0.0.1]/", "jamais sur le Mac"),
    ("http://0.0.0.0/", "jamais sur le Mac"), ("http://192.168.1.1/", "jamais sur le Mac"),
    ("http://10.0.0.1/", "jamais sur le Mac"), ("http://172.16.0.1/", "jamais sur le Mac"),
    ("http://100.64.0.1/", "jamais sur le Mac"), ("http://169.254.169.254/latest/meta-data", "jamais sur le Mac"),
    ("http://[fe80::1]/", "jamais sur le Mac"), ("http://224.0.0.1/", "jamais sur le Mac"),
    ("http://imprimante.local/", "jamais sur le Mac"), ("http://nas.lan/", "jamais sur le Mac"),
    ("http://serveur.internal/", "jamais sur le Mac"), ("http://routeur/", "jamais sur le Mac"),
    ("http://2130706433/", "jamais sur le Mac"),
    ("http://piege.exemple.org/", "jamais sur le Mac"), ("http://box.exemple.org/", "jamais sur le Mac"),
    ("http://moitie.exemple.org/", "jamais sur le Mac"), ("http://nuage.exemple.org/", "jamais sur le Mac"),
    ("file:///etc/passwd", "http:// ou https://"), ("ftp://api.exemple.org/", "http:// ou https://"),
    ("api.exemple.org/x", "http:// ou https://"), ("", "http:// ou https://"),
    ("https://moi:secret@api.exemple.org/", "mot de passe"), ("http://api.exemple.org:abc/", "mal formée"),
]:
    VUES.clear()
    r = appeler(apis.appeler_api, url=url)
    verifier(f"refusé : {url or '(vide)'}", (r.get("is_error"), texte(r).startswith("Refusé"), motif in texte(r), VUES),
             (True, True, True, []))

VUES.clear()
r = appeler(apis.appeler_api, url="https://inconnu.exemple.org/")
verifier("un nom qui n'existe pas : injoignable, rien n'est parti", (r.get("is_error"), "injoignable" in texte(r), VUES),
         (True, True, []))

# Une vraie réponse JSON, avec ses paramètres.
REPONSES[("api.exemple.org", "/v1/chiens")] = (200, {"races": [f"race {i}" for i in range(40)], "page": 1,
                                                       "texte": "a" * 900,
                                                       "profond": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}})
VUES.clear()
r = appeler(apis.appeler_api, url="https://api.exemple.org/v1/chiens", params={"q": "berger", "n": 3,
                                                                                "filtre": {"taille": "grand"}})
verifier("JSON : réussi", r.get("is_error", False), False)
requete = VUES[-1]
verifier("les paramètres envoyés", dict(requete.url.params), {"q": "berger", "n": "3", "filtre": '{"taille": "grand"}'})
verifier("une seule requête, en GET", (len(VUES), requete.method), (1, "GET"))
verifier("pas d'en-tête à lui : son nom générique, ni clé ni cookie",
         (requete.headers["user-agent"], "authorization" in requete.headers, "cookie" in requete.headers),
         (infos.AGENT, False, False))
corps, fin = texte(r).rsplit("\n", 1)
verifier("le texte du dehors signalé", fin, DEHORS)
lu = json.loads(corps)
verifier("JSON : 15 éléments par liste, le reste compté", (len(lu["races"]), lu["races"][-1]), (16, "… et 25 de plus"))
verifier("JSON : un long texte coupé", (len(lu["texte"]), lu["texte"][-1]), (501, "…"))
verifier("JSON : 8 niveaux au plus", lu["profond"]["a"]["b"]["c"]["d"]["e"]["f"], {"g": "…"})
verifier("JSON : compact (pas d'espaces perdus)", corps.startswith('{"races":["race 0","race 1"'), True)

verifier("la requête part vers l'adresse vérifiée, le nom dans Host et dans le certificat TLS",
         (requete.url.host, requete.headers["host"], requete.extensions.get("sni_hostname")),
         ("93.184.216.34", "api.exemple.org", "api.exemple.org"))

# Le « DNS rebinding » : le nom donne une adresse publique à la vérification, puis 127.0.0.1. Résolu une seule fois,
# la requête part vers l'adresse vérifiée.
tours = {"n": 0}


async def dns_changeant(hote: str, port: int) -> list[str]:
    if hote == "change.exemple.org":
        tours["n"] += 1
        return ["93.184.216.34"] if tours["n"] == 1 else ["127.0.0.1"]
    return await faux_dns(hote, port)


apis._adresses = dns_changeant
REPONSES[("change.exemple.org", "/x")] = (200, {"ok": 1})
VUES.clear()
appeler(apis.appeler_api, url="http://change.exemple.org:8080/x")
verifier("DNS rebinding : une seule résolution, vers l'adresse vérifiée",
         (tours["n"], str(VUES[-1].url), VUES[-1].headers["host"], "sni_hostname" in VUES[-1].extensions),
         (1, "http://93.184.216.34:8080/x", "change.exemple.org:8080", False))
apis._adresses = faux_dns

REPONSES[("api.exemple.org", "/champs")] = (200, {f"c{i}": i for i in range(70)})
lu = json.loads(texte(appeler(apis.appeler_api, url="https://api.exemple.org/champs")).rsplit("\n", 1)[0])
verifier("JSON : 60 champs par objet, le reste compté", (len(lu), lu["…"]), (61, "10 autres champs"))

REPONSES[("cdn.exemple.org", "/liste")] = (200, [1, 2, 3])
r = appeler(apis.appeler_api, url="https://cdn.exemple.org/liste")
verifier("une adresse IPv6 publique passe", (texte(r), str(VUES[-1].url)),
         (f"[1,2,3]\n{DEHORS}", "https://[2606:4700::6810:84e5]/liste"))

# Les redirections : suivies, mais chacune vérifiée.
REPONSES[("api.exemple.org", "/vieux")] = httpx.Response(301, headers={"location": "/nouveau?x=1"})
REPONSES[("api.exemple.org", "/nouveau")] = (200, {"ok": True})
VUES.clear()
r = appeler(apis.appeler_api, url="https://api.exemple.org/vieux", params={"q": "a"})
verifier("redirection relative suivie", (texte(r), [(str(v.url), v.headers["host"]) for v in VUES]),
         (f'{{"ok":true}}\n{DEHORS}', [("https://93.184.216.34/vieux?q=a", "api.exemple.org"),
                                       ("https://93.184.216.34/nouveau?x=1", "api.exemple.org")]))
REPONSES[("api.exemple.org", "/rebond")] = httpx.Response(302, headers={"location": "http://127.0.0.1:8765/etat"})
VUES.clear()
r = appeler(apis.appeler_api, url="https://api.exemple.org/rebond")
verifier("une redirection vers le Mac : refusée, jamais suivie",
         (r.get("is_error"), "jamais sur le Mac" in texte(r), [v.headers["host"] for v in VUES]),
         (True, True, ["api.exemple.org"]))
REPONSES[("api.exemple.org", "/vers-box")] = httpx.Response(307, headers={"location": "http://box.exemple.org/admin"})
VUES.clear()
r = appeler(apis.appeler_api, url="https://api.exemple.org/vers-box")
verifier("une redirection vers un nom qui mène à ta box : refusée", ("Refusé" in texte(r), len(VUES)), (True, 1))
REPONSES[("api.exemple.org", "/boucle")] = httpx.Response(302, headers={"location": "/boucle"})
VUES.clear()
r = appeler(apis.appeler_api, url="https://api.exemple.org/boucle")
verifier("une boucle de redirections : arrêtée", (r.get("is_error"), "redirections" in texte(r), len(VUES)),
         (True, True, apis.REDIRECTIONS_MAX + 1))

# Les autres formats.
REPONSES[("api.exemple.org", "/page")] = httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=(
    "<html><head><style>p{color:red}</style><script>alert('x')</script></head>"
    "<body><h1>Bonjour</h1><p>L&#39;API &amp; sa doc</p></body></html>"))
verifier("HTML : sans balises ni script, et WebFetch conseillé",
         texte(appeler(apis.appeler_api, url="https://api.exemple.org/page")),
         f"(Une page web, pas une API : pour la lire, WebFetch fait mieux.) Bonjour L'API & sa doc\n{DEHORS}")
REPONSES[("api.exemple.org", "/csv")] = httpx.Response(200, headers={"content-type": "text/csv"}, text="a,b\n1,2")
verifier("CSV : tel quel", texte(appeler(apis.appeler_api, url="https://api.exemple.org/csv")), f"a,b\n1,2\n{DEHORS}")
REPONSES[("api.exemple.org", "/image")] = httpx.Response(200, headers={"content-type": "image/png"},
                                                         content=b"\x89PNG" + bytes(96))
verifier("une image : décrite, pas lue", texte(appeler(apis.appeler_api, url="https://api.exemple.org/image")),
         f"(Un fichier image/png de 100 octets : pas du texte, je ne peux pas le lire ici.)\n{DEHORS}")
REPONSES[("api.exemple.org", "/sans-type")] = httpx.Response(200, content=b'{"a": 1}')
verifier("JSON sans le dire : reconnu", texte(appeler(apis.appeler_api, url="https://api.exemple.org/sans-type")),
         f'{{"a":1}}\n{DEHORS}')
REPONSES[("api.exemple.org", "/enorme")] = httpx.Response(200, headers={"content-type": "text/plain"},
                                                          content=b"x" * 3_000_000)
r = texte(appeler(apis.appeler_api, url="https://api.exemple.org/enorme"))
verifier("une réponse énorme : coupée", (r.startswith("x" * apis.CARACTERES_MAX + "\n… (coupé à 8000 caractères)"),
                                         len(r) < apis.CARACTERES_MAX + 200), (True, True))

# Les erreurs.
REPONSES[("api.exemple.org", "/absent")] = (404, {"error": "Breed not found"})
r = appeler(apis.appeler_api, url="https://api.exemple.org/absent")
verifier("404 : une erreur, avec ce qu'elle dit", (r.get("is_error"), texte(r)),
         (True, 'L\'API a répondu 404. Ce qu\'elle dit : {"error":"Breed not found"}'))
REPONSES[("api.exemple.org", "/prive")] = (401, {"message": "missing api key"})
r = appeler(apis.appeler_api, url="https://api.exemple.org/prive")
contient("401 : il faut une clé, prends-en une autre", texte(r), "L'API a répondu 401.", "Une clé est sans doute nécessaire")


def trop_lent(requete: httpx.Request):
    raise httpx.ReadTimeout("lent", request=requete)


def coupe(requete: httpx.Request):
    raise httpx.ConnectError("refusée", request=requete)


REPONSES[("api.exemple.org", "/lent")] = trop_lent
REPONSES[("api.exemple.org", "/coupe")] = coupe
r = appeler(apis.appeler_api, url="https://api.exemple.org/lent")
verifier("trop lent : une erreur claire", (r.get("is_error"), "n'a pas répondu en" in texte(r)), (True, True))
r = appeler(apis.appeler_api, url="https://api.exemple.org/coupe")
verifier("injoignable : une erreur claire", (r.get("is_error"), texte(r)), (True, "L'API est injoignable (ConnectError)."))
r = appeler(apis.appeler_api, url="https://api.exemple.org/v1/chiens", params="q=berger")
verifier("params qui n'est pas un objet : refusé", r.get("is_error"), True)

# --- Permissions et enregistrement ------------------------------------------------------------------------
verifier("les deux outils donnés à Steve", all(o in outils.OUTILS for o in apis.OUTILS_APIS), True)
verifier("pas de doublon de nom dans outils.OUTILS", len({o.name for o in outils.OUTILS}), len(outils.OUTILS))
cherche, appelle = (f"mcp__{outils.NOM_SERVEUR}__{n}" for n in ("chercher_api", "appeler_api"))
verifier("chercher_api : une lecture", permissions.est_lecture(cherche), True)
verifier("appeler_api : pas une lecture (comme WebFetch, l'adresse part dehors)", permissions.est_lecture(appelle), False)
for nom in (cherche, appelle):
    verifier(f"{nom} : hors de sa ronde", fond.outil_de_lecture(nom), False)
    verifier(f"{nom} : un texte du dehors", permissions.lit_dehors(nom), True)
    verifier(f"{nom} : il le fait sans demander", permissions.politique(nom, {"url": "https://x.org"}),
             permissions.Decision.OUI)
contient("ses consignes lui disent quand s'en servir", Path(brain.__file__).read_text(encoding="utf-8"),
         "cherche-la dans ton catalogue (chercher_api", "puis appelle-la (appeler_api)")


# --- Un mini essai en vrai (facultatif) -------------------------------------------------------------------
def reseau() -> bool:
    if os.environ.get("JERVIS_TEST_RESEAU", "oui").lower() in {"non", "off", "0"}:
        return False
    try:
        httpx.get("https://pokeapi.co", timeout=3)
        return True
    except Exception:
        return False


infos.TRANSPORT = None
apis._adresses = VRAI_DNS
if reseau():
    r = appeler(apis.appeler_api, url="https://pokeapi.co/api/v2/pokemon/pikachu")
    verifier("en vrai : Pokéapi connaît Pikachu", (r.get("is_error", False), '"name":"pikachu"' in texte(r)), (False, True))
    r = appeler(apis.appeler_api, url="http://127.0.0.1:1/")
    verifier("en vrai : le Mac reste refusé", texte(r).startswith("Refusé"), True)
else:
    print("(pas de réseau : essai en vrai sauté)")

print(f"\n{'✅' if not echecs else '❌'} apis : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
