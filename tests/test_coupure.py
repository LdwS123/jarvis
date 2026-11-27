"""Lui couper la parole, et qu'il réponde plus vite ; la page d'installation qui attend ton compte Claude.

    cd ~/Jervis && JERVIS_REGLAGES=non PYTHONPATH=. .venv/bin/python tests/test_coupure.py

Sans micro, sans haut-parleur, sans Claude : le micro rend des blocs de « voix » et de silence, Claude est un faux
qui note ce qu'on lui demande, et le statut du compte Claude est joué à la main.
"""

import asyncio
import http.client
import http.server
import json
import os
import queue
import re
import socket
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("JERVIS_REGLAGES", "non")  # tes vrais choix ne comptent pas ici
threading.Timer(90, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402
from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock  # noqa: E402

from jervis import audio, brain, compteur, config, langues, outils, reglages  # noqa: E402
from jervis import __main__ as m  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


# --- 1. Sa voix se met en pause (sans rien jeter), puis repart du même mot ------------------------------------------
print("🔇 La pause")
hp = audio.HautParleur(lambda: True)
hp.ajouter(np.ones(1000, dtype=np.float32))
verifier("avant : le son passe tel quel", float(hp.remplir(100).min()), 1.0)
hp.en_pause = True
fondu = hp.remplir(100)
verifier("en pause : le bloc en cours s'éteint en fondu (sans clic)",
         (round(float(fondu[0]), 2), float(fondu[-1]), hp._file[0][1]), (1.0, 0.0, 200))
verifier("en pause, éteint : silence, et le son n'avance plus",
         (float(np.abs(hp.remplir(100)).max()), hp._file[0][1], hp.niveau), (0.0, 200, 0.0))
hp.remplir(100)
verifier("toujours en pause : toujours au même endroit", hp._file[0][1], 200)
hp.en_pause = False
reprise = hp.remplir(100)
verifier("la pause levée : il repart du même mot, en fondu", (float(reprise[0]), float(reprise[-1]), hp._file[0][1]),
         (0.0, 1.0, 300))
hp.en_pause = True
fini: list[bool] = []
hp._file[0][2] = lambda: fini.append(True)
hp.arreter()
verifier("arrêter : la pause est levée et le son prévenu", (hp.en_pause, len(hp._file), fini), (False, 0, [True]))

# --- 2. Le micro : le moment où tu lui coupes la parole -------------------------------------------------------------
print("\n✋ Tu lui coupes la parole")
VOIX, BLANC = np.ones(config.FRAME, dtype=np.float32), np.zeros(config.FRAME, dtype=np.float32)


class FaussesOreilles(audio.Oreilles):
    def __init__(self, voix: int = 12) -> None:  # (pas de vrai micro)
        self._q = queue.Queue()
        for bloc in [BLANC] * 3 + [VOIX] * voix + [BLANC] * 8:
            self._q.put((bloc, bloc, False))
        self._ferme = threading.Event()
        self._motcle = None
        self._chuchotee = None

    def vider(self) -> None:
        pass

    def _est_voix(self, propre, _debruite, calme=False) -> bool:
        return bool(propre[0])

    def _phrase_finie(self, _morceaux, silence, demande):
        return silence >= 0.4, demande

    def _signer(self, _phrase) -> None:
        pass

    def _voix_inconnue(self, _phrase) -> bool:
        return False


appels: list[float] = []


def sa_voix_s_arrete() -> bool:
    appels.append(time.monotonic())
    return True


phrase = FaussesOreilles().ecouter_phrase(1.0, quand_tu_coupes=sa_voix_s_arrete)
verifier("demandé une seule fois, dès COUPURE_S de parole", len(appels), 1)
verifier("la phrase retient où sa voix s'est tue (après le début de ta voix, en blocs entiers)",
         (phrase.coupure is not None, phrase.coupure and phrase.coupure % config.FRAME,
          phrase.coupure and 3 * config.FRAME < phrase.coupure < len(phrase.audio)), (True, 0, True))
appels.clear()
phrase = FaussesOreilles(voix=5).ecouter_phrase(1.0, quand_tu_coupes=sa_voix_s_arrete)
verifier("un « hm » (moins de COUPURE_S) ne l'arrête pas", (appels, phrase.coupure), ([], None))
phrase = FaussesOreilles().ecouter_phrase(1.0, quand_tu_coupes=lambda: False)
verifier("sa voix ne s'est pas arrêtée : pas de coupure retenue", phrase.coupure, None)
phrase = FaussesOreilles().ecouter_phrase(1.0)
verifier("sans quand_tu_coupes : rien ne change", phrase.coupure, None)

# Ta voix est jugée sur ce qui suit la coupure, sans la sienne mêlée
vus: list[int] = []
oreilles = object.__new__(audio.Oreilles)
oreilles._empreinte = types.SimpleNamespace(vecteur=lambda a: vus.append(len(a)) or np.zeros(4), reference=None)
SR = config.SAMPLE_RATE
oreilles._signer(audio.Phrase(np.zeros(3 * SR, dtype=np.float32), duree_parole=3.0, coupure=SR))
oreilles._signer(audio.Phrase(np.zeros(3 * SR, dtype=np.float32), duree_parole=3.0))
oreilles._signer(audio.Phrase(np.zeros(3 * SR, dtype=np.float32), duree_parole=3.0, coupure=3 * SR - 100))
verifier("empreinte : la fin seule après la coupure ; toute la phrase sinon, ou si la fin est trop courte",
         vus, [2 * SR, 3 * SR, 3 * SR])

# --- 3. Les réglages ------------------------------------------------------------------------------------------------
print("\n⚙️  Réglages")
verifier("COUPURE_S par défaut", config.COUPURE_S, float(os.environ.get("JERVIS_COUPURE_S", "0.5")))
verifier("MOTS_POUR_COUPER_ARRETE par défaut", config.MOTS_POUR_COUPER_ARRETE,
         int(os.environ.get("JERVIS_MOTS_POUR_COUPER_ARRETE", "2")))
verifier("EFFORT par défaut : low (il répond plus vite)", config.EFFORT, os.environ.get("JERVIS_EFFORT", "low"))
verifier("DEBUT_TOT_CARACTERES par défaut", config.DEBUT_TOT_CARACTERES,
         int(os.environ.get("JERVIS_DEBUT_TOT_CARACTERES", "35")))

# --- 4. Il commence à parler dès la première virgule d'une longue phrase --------------------------------------------
print("\n⚡ Plus vite")
vrai_debut, config.DEBUT_TOT_CARACTERES = config.DEBUT_TOT_CARACTERES, 35
MORCEAUX = [
    ("Je regarde les vols pour Lisbonne vendredi prochain, et je te", ("Je regarde les vols pour Lisbonne vendredi "
                                                                       "prochain,", "et je te")),
    ("Alors, je regarde ça", None),                                    # trop court : il attend la suite
    ("Il reste 1,5 litre de lait dans le frigo de la cuisine", None),  # « 1,5 » n'est pas une virgule de phrase
    ("Voilà le détail de ta journée de demain avec Léa et Paul, ", ("Voilà le détail de ta journée de demain avec "
                                                                    "Léa et Paul,", "")),
    ("Bon, tu as trois réunions demain matin, puis le déjeuner", ("Bon, tu as trois réunions demain matin,",
                                                                   "puis le déjeuner")),
]
for tampon, attendu in MORCEAUX:
    verifier(f"premier morceau de « {tampon} »", brain._premier_morceau(tampon), attendu)
config.DEBUT_TOT_CARACTERES = 0
verifier("DEBUT_TOT_CARACTERES=0 : il attend toujours le point", brain._premier_morceau(MORCEAUX[0][0]), None)
config.DEBUT_TOT_CARACTERES = 35


def delta(txt):
    return StreamEvent(uuid="u", session_id="s", parent_tool_use_id=None,
                       event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": txt}})


class FauxClaude:
    """Note dans l'ordre ce qu'on lui demande (query, get_mcp_status)."""
    def __init__(self, morceaux):
        self.morceaux = morceaux
        self.ordre: list[str] = []

    async def query(self, t):
        self.ordre.append(f"query:{t}")

    async def interrupt(self): pass

    async def get_mcp_status(self):
        self.ordre.append("mcp")
        return {"mcpServers": []}

    async def receive_response(self):
        for txt in self.morceaux:
            yield delta(txt)
        yield AssistantMessage(content=[TextBlock("".join(self.morceaux))], model="m", parent_tool_use_id=None)


outils.noter_echange = lambda *a, **_k: None
compteur.LIMITES = Path(tempfile.mkdtemp()) / "limites.json"  # jamais ton vrai fichier de limites


def cerveau(morceaux) -> tuple[brain.Cerveau, list[str]]:
    dit: list[str] = []

    async def parler(p): dit.append(p)
    async def confirmer(q): return True
    c = brain.Cerveau(parler, confirmer, journal=lambda *_: None, anticiper=None, patience=False)
    c._client = FauxClaude(morceaux)
    return c, dit


async def tester_cerveau() -> None:
    c, dit = cerveau(["Je regarde les vols pour Lisbonne ", "vendredi prochain, et je te dis ",
                      "lequel est le moins cher."])
    await c.demander("trouve-moi un vol pour Lisbonne")
    verifier("une longue première phrase part en deux : la voix commence à la virgule",
             dit, ["Je regarde les vols pour Lisbonne vendredi prochain,", "et je te dis lequel est le moins cher."])
    verifier("rien ne passe avant ta demande (les connecteurs sont coupés après, en fond)",
             [o.startswith("query:") and o.endswith("trouve-moi un vol pour Lisbonne") for o in c._client.ordre], [True])
    await asyncio.sleep(2.4)
    verifier("… puis le ménage coupe les connecteurs arrivés en retard", c._client.ordre[-1:], ["mcp"])
    c._menage_en_cours.cancel()

    c, dit = cerveau(["Il fait 12 degrés. ", "Prends un pull."])
    await c.demander("quel temps il fait")
    verifier("des phrases courtes : rien ne change", dit, ["Il fait 12 degrés.", "Prends un pull."])

    # Après une longue pause, la conversation est remise à zéro (/clear) en fond, pas pendant ta phrase suivante
    vrai_oubli, config.OUBLI_MIN = config.OUBLI_MIN, 0.01  # 0,6 s
    try:
        c, dit = cerveau(["D'accord."])
        await c.demander("retiens que j'aime le jazz")
        await asyncio.sleep(3.6)  # 2 s, les connecteurs ; puis 1 s, le /clear
        ordre = list(c._client.ordre)
        verifier("le /clear est fait en fond, après la pause", ordre[-2:], ["mcp", "query:/clear"])
        await c.demander("mets de la musique")
        apres = c._client.ordre[len(ordre):]
        verifier("ta phrase suivante part tout de suite : pas de second /clear", "query:/clear" in apres, False)
        verifier("… avec le fil d'avant la pause", bool(apres) and "Vos derniers échanges" in apres[0]
                 and apres[0].endswith("mets de la musique"), True)
    finally:
        config.OUBLI_MIN = vrai_oubli

asyncio.run(tester_cerveau())
config.DEBUT_TOT_CARACTERES = vrai_debut

# --- 5. La page d'installation : « Terminer » attend ton compte Claude ---------------------------------------------
print("\n🔑 Installation")
verifier("EFFORT valide", reglages.valider({"EFFORT": "medium"}), ({"EFFORT": "medium"}, []))
_, erreurs = reglages.valider({"EFFORT": "turbo"})
verifier("EFFORT inconnu : refusé en clair", bool(erreurs) and erreurs[0].startswith("Vitesse inconnue"), True)
verifier("l'état donne les vitesses et celle en service",
         (reglages.etat()["efforts"], reglages.etat()["en_service"]["EFFORT"]), (["low", "medium", "high"], config.EFFORT))
verifier("statut illisible : marqué « je ne sais pas »", reglages.lire_statut("Error"),
         {"installe": True, "connecte": False, "illisible": True})
verifier("statut sans loggedIn : illisible aussi", reglages.lire_statut('{"autre": 1}').get("illisible"), True)
verifier("statut clair : pas illisible", "illisible" in reglages.lire_statut('{"loggedIn": false}'), False)

vrai_statut = reglages.statut_claude
STATUT = {"installe": True, "connecte": False}
reglages.statut_claude = lambda: dict(STATUT)
verifier("/reglages/fini hors installation : n'existe pas", reglages.traiter("POST", "/reglages/fini")[0], 404)
verifier("/reglages/fini sans compte connecté : 409", reglages.traiter("POST", "/reglages/fini", installation=True)[0],
         409)
code, corps, _ = reglages.traiter("GET", "/reglages/claude")
verifier("GET /reglages/claude : juste le statut", (code, json.loads(corps)), (200, STATUT))
verifier("GET /reglages/etat : dit s'il s'agit de l'installation",
         json.loads(reglages.traiter("GET", "/reglages/etat", installation=True)[1])["installation"], True)
STATUT["connecte"] = True
verifier("/reglages/fini, compte connecté : 200", reglages.traiter("POST", "/reglages/fini", installation=True)[0], 200)
STATUT.update(connecte=False, illisible=True)
verifier("/reglages/fini, statut illisible : ne bloque pas",
         reglages.traiter("POST", "/reglages/fini", installation=True)[0], 200)

# Ses serveurs (réglages, tableau de bord) écoutent tout de suite, sans chercher d'abord le nom du Mac
# (socket.getfqdn : 30 s et plus quand le DNS est lent)
vrai_getfqdn = socket.getfqdn
socket.getfqdn = lambda *a: (_ for _ in ()).throw(AssertionError("getfqdn appelé"))
try:
    serveur_essai = reglages.ServeurLocal(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    serveur_essai.server_close()
    sans_nom = True
except AssertionError:
    sans_nom = False
except OSError:
    sans_nom = None
finally:
    socket.getfqdn = vrai_getfqdn
if sans_nom is not None:
    verifier("les serveurs écoutent sans chercher le nom du Mac (DNS lent)", sans_nom, True)

# servir(installation=True) se ferme tout seul après « Terminer et lancer », et le dit (True)
with socket.socket() as s:
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    except OSError:
        port = None
if port is None:
    print("   (serveur non testé : impossible d'ouvrir un port ici)")
else:
    rendu: list[bool] = []
    fil = threading.Thread(target=lambda: rendu.append(reglages.servir(port, ouvrir_page=False, installation=True)),
                           daemon=True)
    fil.start()

    def demander(methode: str, chemin: str, **entetes: str):
        # Le serveur démarre encore (refusé, ou rien ne répond : sur une machine chargée comme celles de GitHub,
        # le port est pris avant d'écouter) : on réessaie, 30 s au plus en tout.
        fin = time.monotonic() + 30
        while time.monotonic() < fin:
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request(methode, chemin, headers={"Host": f"127.0.0.1:{port}", **entetes})
                r = c.getresponse()
                donnees = r.read()
                c.close()
                return r.status, donnees
            except OSError:  # ConnectionRefusedError, TimeoutError…
                time.sleep(0.1)
        return None, b""

    code, corps = demander("GET", "/")
    jeton = (re.search(r'data-jeton="([^"]+)"', corps.decode()) or [None, ""])[1]
    verifier("la page d'installation s'ouvre, avec son jeton", (code, bool(jeton)), (200, True))
    STATUT.update(connecte=False, illisible=False)
    verifier("Terminer sans le jeton : refusé", demander("POST", "/reglages/fini")[0], 403)
    verifier("Terminer sans compte : refusé (409)", demander("POST", "/reglages/fini", **{"X-Jeton": jeton})[0], 409)
    STATUT["connecte"] = True
    verifier("Terminer, compte connecté : 200", demander("POST", "/reglages/fini", **{"X-Jeton": jeton})[0], 200)
    fil.join(5)
    verifier("… et la page se ferme : l'installation continue", (fil.is_alive(), rendu), (False, [True]))

verifier("la phrase « pas de compte » existe dans chaque langue de la page",
         all(langues.sans_claude(c) for c in ("fr", "en", "zh", "ka")), True)
verifier("une langue sans phrase : l'anglais", langues.sans_claude("xx"), langues.sans_claude("en"))

# --- 6. Au démarrage : il attend ton compte Claude au lieu de démarrer à moitié --------------------------------------
print("\n🚀 Démarrage")
notes: list[str] = []
dits: list[str] = []
pages: list[bool] = []
vrai_notifier, vrai_servir = m.notifier, reglages.servir


async def faux_notifier(_titre, texte):
    notes.append(texte)


async def dire(phrase):
    dits.append(phrase)

m.notifier = faux_notifier
reglages.servir = lambda *a, **k: pages.append(True)
try:
    STATUT.update(connecte=True, illisible=False)
    avant = time.monotonic()
    asyncio.run(m.attendre_claude())
    verifier("connecté : il démarre tout de suite, sans rien dire", (time.monotonic() - avant < 1, notes, dits),
             (True, [], []))
    STATUT.update(connecte=False, illisible=True)
    asyncio.run(m.attendre_claude())
    verifier("statut illisible : il démarre quand même", (notes, dits), ([], []))

    reponses = iter([False, False, True])
    reglages.statut_claude = lambda: {"installe": True, "connecte": next(reponses, True)}
    etat = types.SimpleNamespace(nom="demarre")
    asyncio.run(m.attendre_claude(etat, dire))
    verifier("pas connecté : il le dit, ouvre la page, attend, puis démarre",
             (notes, dits, pages, etat.nom), ([langues.sans_claude(config.LANGUE)], [langues.sans_claude(
                 config.LANGUE)], [True], "demarre"))
finally:
    m.notifier, reglages.servir, reglages.statut_claude = vrai_notifier, vrai_servir, vrai_statut

print(f"\n{'✅' if not echecs else '❌'} coupure : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
