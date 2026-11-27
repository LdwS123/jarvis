"""Steve dans toutes les langues : ce qu'il entend (la langue de chaque phrase), ce qu'il dit (la voix de cette
langue), ses filtres anti-vidéo, et la page Réglages (son nom, ses langues, sa voix, ton compte Claude).

    cd ~/Jervis && JERVIS_REGLAGES=non PYTHONPATH=. .venv/bin/python tests/test_langues.py

Sans micro, sans Whisper, sans Internet : la reconnaissance et les voix sont remplacées par des faux.
"""

import asyncio
import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import types
from pathlib import Path

os.environ.setdefault("JERVIS_REGLAGES", "non")  # tes vrais choix ne comptent pas ici
threading.Timer(90, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

import numpy as np  # noqa: E402

from jervis import config, langues, reglages, stt, tableau, tts, voix_locale  # noqa: E402
from jervis import __main__ as m  # noqa: E402
from jervis.naturel import hors_alphabet  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def parler_langues(liste: str) -> None:
    config.LANGUES = liste


RACINE = Path(__file__).resolve().parent.parent
dossier = Path(tempfile.mkdtemp(prefix="jervis-langues-"))

# --- 1. Reconnaître une langue dans un texte (son écriture, ses petits mots) ----------------------------------------
for texte, attendu in [("你好，打开音乐", "zh"), ("გამარჯობა, როგორ ხარ?", "ka"), ("Привет, как дела?", "ru"),
                       ("Привіт, як справи? Їжак", "uk"), ("こんにちは、元気ですか", "ja"), ("안녕하세요", "ko"),
                       ("مرحبا كيف حالك", "ar"), ("Γεια σου τι κάνεις", "el"), ("OK.", None), ("Spotify", None)]:
    verifier(f"écriture/langue : {texte}", langues.detecter(texte), attendu)
for texte, attendu in [("What is on my calendar tomorrow?", "en"), ("Qu'est-ce que j'ai demain ?", "fr"),
                       ("¿Qué tengo mañana por la tarde?", "es"), ("Was habe ich morgen und wann?", "de"),
                       ("Je regarde le mail de Paul.", "fr")]:
    verifier(f"petits mots : {texte}", langues.detecter(texte), attendu)
verifier("écriture latine", langues.ecriture("Bonjour à tous"), "latin")
verifier("écriture : trop peu de lettres", langues.ecriture("a"), None)

verifier("liste : fr,en,zh", langues.lire_liste("fr, en,zh", "fr"), (frozenset({"fr", "en", "zh"}), False))
verifier("liste : la principale en fait partie", langues.lire_liste("zh", "fr")[0], frozenset({"fr", "zh"}))
verifier("liste : toutes", langues.lire_liste("toutes", "fr")[1], True)

# --- 2. Son nom, dit dans d'autres langues ---------------------------------------------------------------------------
for phrase, attendu in [("Steve, allume la lumière", True), ("史蒂夫，打开音乐", True), ("斯蒂夫，我明天有什么安排", True),
                        ("სტივ, ჩართე მუსიკა", True), ("სტივი, რა დროა?", True), ("Стив, привет", True),
                        ("Steve Jobs a fondé Apple", False), ("Il est steve-like", True)]:
    verifier(f"son nom : {phrase}", bool(m.NOM.search(phrase)), attendu)

# --- 3. Une seule langue (par défaut) : rien ne change ------------------------------------------------------------
parler_langues("fr")
verifier("une langue : pas plusieurs", langues.plusieurs(), False)
verifier("une langue : aucune consigne en plus", langues.consigne(), "")
verifier("une langue : l'anglais d'une vidéo reste une vidéo",
         m.en_anglais("I think that you are really going to like this one"), True)
verifier("une langue : du chinois reste une vidéo", hors_alphabet("你好，我们今天来看看这个视频"), True)
verifier("une langue : la voix, toujours la même", tts._langues_des(["Hello there.", "你好。"]), [None, None])
verifier("une langue : sa langue sans même écouter", stt.langue_de(np.zeros(16000, dtype=np.int16)), "fr")
verifier("patience : en français", langues.phrases_patience(), config.PHRASES_PATIENCE)

# --- 4. Plusieurs langues : français, anglais, chinois, géorgien -----------------------------------------------------
parler_langues("fr,en,zh,ka")
verifier("plusieurs : oui", langues.plusieurs(), True)
c = langues.consigne()
verifier("consigne : répondre dans la langue de sa phrase",
         all(x in c for x in ("chinois", "géorgien", "anglais", "dernière phrase", "pas d'une vidéo")), True)
verifier("filtre : ton anglais n'est pas une vidéo", m.en_anglais("I think that you are really going to like this one"),
         False)
verifier("filtre : ton anglais n'est pas du charabia", m.charabia("Could you check my mail for me please"), False)
verifier("filtre : du vrai bruit reste du charabia", m.charabia("Og det spikes sjovt base præskel"), True)
verifier("filtre : ton chinois passe", hors_alphabet("你好，打开音乐"), False)
verifier("filtre : ton géorgien passe", hors_alphabet("გამარჯობა, ჩართე მუსიკა"), False)
verifier("filtre : du russe (pas parlé) reste une vidéo", hors_alphabet("Привет, как у тебя дела сегодня"), True)

# Ce qu'entend Whisper : il ne choisit qu'entre tes langues.
verifier("Whisper : la plus probable de tes langues",
         stt.choisir_langue({"ja": 0.5, "zh": 0.3, "fr": 0.1, "ka": 0.05}), "zh")
verifier("Whisper : aucune des tiennes", stt.choisir_langue({"ja": 0.9, "ko": 0.1}), None)
verifier("indice : en chinois pour du chinois", "打开" in stt.indice("zh"), True)
verifier("indice : en français pour du français", stt.indice("fr"), stt.INDICE)
verifier("indice : une langue sans exemple", stt.indice("hi"), f"{config.PRENOM}.")

appels: list[tuple] = []
_vrais = stt._langue_entendue, stt._transcrire_parakeet, stt._transcrire_whisper, stt._moteur
stt._transcrire_parakeet = lambda audio, nom, langue="fr": appels.append(("parakeet", langue)) or "p"
stt._transcrire_whisper = lambda audio, nom, langue=None: appels.append(("whisper", langue)) or "w"
stt._moteur = "parakeet"
son = np.zeros(16000, dtype=np.int16)
for entendue, attendu in [("fr", ("parakeet", "fr")), ("en", ("parakeet", "en")), ("zh", ("whisper", "zh")),
                          ("ka", ("whisper", "ka")), (None, ("parakeet", "fr"))]:
    stt._langue_entendue = lambda audio, e=entendue: e
    appels.clear()
    stt._transcrire(son, False)
    verifier(f"aiguillage : {entendue}", (appels, langues.derniere), ([attendu], entendue or "fr"))


def en_panne(audio):
    raise RuntimeError("Whisper absent")


stt._langue_entendue = en_panne
verifier("aiguillage : Whisper en panne → ta langue principale", stt.langue_de(son), "fr")
stt._moteur = "whisper"
stt._langue_entendue = lambda audio: "en"
appels.clear()
stt._transcrire(son, False)
verifier("aiguillage : moteur Whisper, dans la langue entendue", appels, [("whisper", "en")])
stt._langue_entendue, stt._transcrire_parakeet, stt._transcrire_whisper, stt._moteur = _vrais

# Le deuxième avis (Whisper en français imposé) seulement pour une phrase en français.
_cache = stt._whisper_en_cache
stt._whisper_en_cache = lambda: True
_tw = stt._transcrire_whisper
stt._transcrire_whisper = lambda audio, indice, langue=None: appels.append(("avis", langue)) or "Ouvre Cursor."
appels.clear()
verifier("deuxième avis : pas pour ta phrase anglaise", stt._deuxieme_avis(son, "Open the door.", "en"), "Open the door.")
verifier("deuxième avis : jamais appelé", appels, [])
config.DEUXIEME_AVIS = True
verifier("deuxième avis : en français, toujours là", stt._deuxieme_avis(son, "Open the door.", "fr"), "Ouvre Cursor.")
stt._transcrire_whisper, stt._whisper_en_cache = _tw, _cache

# Ses « Voyons… » : dans la langue où tu viens de parler.
langues.derniere = "zh"
verifier("patience : en chinois", langues.phrases_patience(), ["我看看。", "稍等。"])
langues.derniere = "hi"
verifier("patience : aucune dans une langue qu'il ne connaît pas", langues.phrases_patience(), [])
langues.derniere = "fr"
verifier("patience : de retour en français", langues.phrases_patience(), config.PHRASES_PATIENCE)

# --- 5. Sa voix : celle de la langue de chaque phrase ---------------------------------------------------------------
langues.derniere = "fr"
phrases = ["Je regarde tes mails.", "OK.", "I will check that for you right now.", "Sure.", "好的，我马上看。",
           "კარგი, ახლავე ვნახავ."]
verifier("voix : la langue de chaque phrase", tts._langues_des(phrases), ["fr", "fr", "en", "en", "zh", "ka"])
langues.derniere = "zh"
verifier("voix : une phrase muette part de ta dernière langue", tts._langues_des(["OK.", "好的。"]), ["zh", "zh"])
langues.derniere = "fr"
verifier("voix Microsoft : la tienne en français", tts._voix_neuronale("fr"), config.VOIX_NEURONALE)
verifier("voix Microsoft : chinois", tts._voix_neuronale("zh"), "zh-CN-YunxiNeural")
verifier("voix Microsoft : géorgien", tts._voix_neuronale("ka"), "ka-GE-GiorgiNeural")
os.environ["JERVIS_VOIX_ZH"] = "zh-CN-XiaoxiaoNeural"
verifier("voix Microsoft : la tienne pour le chinois (JERVIS_VOIX_ZH)", tts._voix_neuronale("zh"), "zh-CN-XiaoxiaoNeural")
del os.environ["JERVIS_VOIX_ZH"]
verifier("voix macOS : la tienne en français", tts._voix_mac("fr"), config.VOIX)
verifier("voix macOS : sans langue, la tienne", tts._voix_mac(None), config.VOIX)
langues._voix_du_mac.cache_clear()
_say = langues._voix_du_mac
langues._voix_du_mac = lambda: {"Thomas": "fr_FR", "Tingting": "zh_CN", "Samantha": "en_US"}
verifier("voix macOS : Tingting pour le chinois", tts._voix_mac("zh"), "Tingting")
verifier("voix macOS : aucune en géorgien → la tienne", tts._voix_mac("ka"), config.VOIX)
langues._voix_du_mac = _say

dits: list[tuple[str, str]] = []


class FauxLigne:
    @staticmethod
    def disponible():
        return True

    @staticmethod
    async def dire(phrase, voix, vitesse, doubler=False):
        dits.append((phrase, voix))
        return b"mp3"


_ligne, _prete, _dire, _vl = tts.ligne, voix_locale.prete, voix_locale.dire, config.VOIX_LOCALE_LANGUE
tts.ligne = FauxLigne
voix_locale.prete = lambda: True
voix_locale.dire = lambda phrase: dits.append((phrase, "locale")) or np.zeros(4800, dtype=np.float32)
config.VOIX_LOCALE_LANGUE = "french"
tts._DOSSIER_AUDIO = dossier
boucle = asyncio.new_event_loop()
dits.clear()
boucle.run_until_complete(tts._en_local("Bonjour.", "fr"))
boucle.run_until_complete(tts._en_local("你好。", "zh"))
boucle.run_until_complete(tts._synthetiser("你好。", langue="zh"))
boucle.run_until_complete(tts._synthetiser("Hello.", langue="en"))
boucle.run_until_complete(tts._synthetiser("Bonjour."))
verifier("voix : locale en français, Microsoft pour le reste",
         dits, [("Bonjour.", "locale"), ("你好。", "zh-CN-YunxiNeural"), ("Hello.", "en-US-AndrewMultilingualNeural"),
                ("Bonjour.", config.VOIX_NEURONALE)])
_neuronale = config.VOIX_NEURONALE
config.VOIX_NEURONALE = "non"
dits.clear()
verifier("JERVIS_VOIX_NEURONALE=non : rien chez Microsoft, dans aucune langue",
         (boucle.run_until_complete(tts._synthetiser("你好。", langue="zh")), dits), (None, []))
config.VOIX_NEURONALE = _neuronale
tts.ligne, voix_locale.prete, voix_locale.dire = _ligne, _prete, _dire

config.VOIX_LOCALE_LANGUE = ""
verifier("voix locale : aucune quand Pocket TTS n'a pas ta langue", voix_locale.active(), False)
config.VOIX_LOCALE_LANGUE = _vl
verifier("modèle local : chinois → aucun, anglais → english", (langues.modele_local("zh"), langues.modele_local("en")),
         ("", "english"))

# --- 6. Une autre langue principale, choisie dans reglages.json ----------------------------------------------------
fichier = dossier / "reglages.json"
fichier.write_text(json.dumps({"PRENOM": "Max", "LANGUE": "zh", "LANGUES": "zh,en", "MODELE": "opus"}), encoding="utf-8")
lire = ("from jervis import config, langues, voix_locale; import json, re; print(json.dumps([config.PRENOM, config.LANGUE, "
        "config.ANNONCE, config.PHRASES_PATIENCE, config.VOIX_NEURONALE, config.VOIX_LOCALE_LANGUE, "
        "voix_locale.active(), config.MODELE_CLAUDE, bool(re.search(config.NOM, 'Max, 打开音乐', re.I)), langues.plusieurs()], "
        "ensure_ascii=False))")


def config_avec(**env: str):
    propre = {k: v for k, v in os.environ.items() if not k.startswith("JERVIS_")}
    sortie = subprocess.run([sys.executable, "-c", lire], cwd=RACINE, capture_output=True, text=True, timeout=60,
                            env={**propre, "PYTHONPATH": str(RACINE), **env})
    return json.loads(sortie.stdout) if sortie.returncode == 0 else sortie.stderr[-400:]


verifier("reglages.json : un Steve chinois qui s'appelle Max", config_avec(JERVIS_REGLAGES=str(fichier)),
         ["Max", "zh", "Max 已上线。", ["我看看。", "稍等。"], "zh-CN-YunxiNeural", "", False, "opus", True, True])
verifier("une variable JERVIS_ l'emporte sur la page",
         config_avec(JERVIS_REGLAGES=str(fichier), JERVIS_PRENOM="Steve")[0], "Steve")
verifier("JERVIS_REGLAGES=non : le fichier est ignoré", config_avec(JERVIS_REGLAGES="non")[:2], ["Steve", "fr"])
fichier.write_text("{ pas du json", encoding="utf-8")
verifier("reglages.json illisible : les défauts, sans planter", config_avec(JERVIS_REGLAGES=str(fichier))[:2],
         ["Steve", "fr"])

# --- 7. La page Réglages ------------------------------------------------------------------------------------------
propres, erreurs = reglages.valider({"PRENOM": " Léa ", "LANGUE": "ka", "LANGUES": ["en", "zh"], "MODELE": "opus",
                                     "VOIX_NEURONALE": "ka-GE-EkaNeural", "VOIX_LOCALE": "non", "AUTRE": "x"})
verifier("valider : propre", (propres, erreurs),
         ({"PRENOM": "Léa", "LANGUE": "ka", "LANGUES": "ka,en,zh", "MODELE": "opus", "VOIX_NEURONALE": "ka-GE-EkaNeural",
           "VOIX_LOCALE": "non"}, []))
_, erreurs = reglages.valider({"PRENOM": "<script>", "LANGUE": "xx", "LANGUES": "fr,zz", "MODELE": "gpt",
                               "VOIX_NEURONALE": "rm -rf", "VOIX_LOCALE": "robot"})
verifier("valider : six erreurs en clair", len(erreurs), 6)
verifier("valider : « toutes »", reglages.valider({"LANGUES": "toutes"})[0], {"LANGUES": "toutes"})
verifier("valider : vide = revenir au défaut", reglages.valider({"MODELE": ""})[0], {"MODELE": ""})

config.SANS_REGLAGES = True
try:
    reglages.ecrire({"PRENOM": "Max"})
    verifier("JERVIS_REGLAGES=non : la page n'écrit rien", "écrit", "refusé")
except PermissionError:
    verifier("JERVIS_REGLAGES=non : la page n'écrit rien", "refusé", "refusé")
config.SANS_REGLAGES, config.FICHIER_REGLAGES = False, dossier / "page" / "reglages.json"
config.FICHIER_REGLAGES.parent.mkdir()
config.FICHIER_REGLAGES.write_text(json.dumps({"VITESSE": "+0%", "MODELE": "haiku"}), encoding="utf-8")
reglages.ecrire({"PRENOM": "Max", "MODELE": ""})
verifier("écrire : fusionne, garde tes autres clés, efface le vide", reglages.lire(), {"VITESSE": "+0%", "PRENOM": "Max"})
verifier("écrire : lisible par toi seul", stat.S_IMODE(config.FICHIER_REGLAGES.stat().st_mode), 0o600)
verifier("écrire : aucun fichier temporaire oublié", sorted(p.name for p in config.FICHIER_REGLAGES.parent.iterdir()),
         ["reglages.json"])

verifier("statut Claude : connecté, sans ton adresse",
         reglages.lire_statut(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                                          "email": "toi@exemple.fr", "orgName": "Toi"})),
         {"installe": True, "connecte": True, "methode": "abonnement Claude", "abonnement": "max"})
verifier("statut Claude : pas connecté", reglages.lire_statut('{"loggedIn": false}')["connecte"], False)
verifier("statut Claude : sortie illisible", reglages.lire_statut("Error")["connecte"], False)

reglages.statut_claude = lambda: {"installe": True, "connecte": True, "methode": "abonnement Claude", "abonnement": "pro"}
connexions: list[bool] = []
reglages.connecter_claude = lambda: connexions.append(True) or True
code, corps, _ = reglages.traiter("GET", "/reglages/etat")
e = json.loads(corps)
verifier("état : tes choix, ce qui tourne, et ton compte",
         (code, e["choisis"]["PRENOM"], e["en_service"]["LANGUE"], e["claude"]["abonnement"],
          {"zh", "ka", "en"} <= {l["code"] for l in e["langues"]}), (200, "Max", "fr", "pro", True))
verifier("état : jamais ton adresse", "toi@exemple.fr" in corps.decode(), False)

# --- 8. Servie par le tableau de bord (cette machine seulement, et le jeton) -------------------------------------
with socket.socket() as s:
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    except OSError:
        port = None
ecran = tableau.Tableau(types.SimpleNamespace(nom="ecoute", oreilles=None), {}, journal=dossier / "jervis.log")
if port is None or not ecran.demarrer(port):
    print("   (serveur non testé : impossible d'ouvrir un port ici)")
else:
    def demander(methode: str, chemin: str, corps: bytes = b"", **entetes: str):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(methode, chemin, body=corps, headers={"Host": f"127.0.0.1:{port}", **entetes})
        r = c.getresponse()
        donnees = r.read()
        c.close()
        return r, donnees

    r, corps = demander("GET", "/reglages")
    regles = r.getheader("Content-Security-Policy") or ""
    verifier("GET /reglages : la page, règles strictes",
             (r.status, "Réglages" in corps.decode(), ecran.jeton in corps.decode(), "unsafe-inline" in regles,
              "default-src 'none'" in regles), (200, True, True, False, True))
    verifier("la page : aucun style en ligne (refusé par ses règles)", 'style="' in corps.decode(), False)
    verifier("GET /reglages/etat sans jeton", demander("GET", "/reglages/etat")[0].status, 403)
    verifier("GET /reglages/etat avec le jeton", demander("GET", "/reglages/etat", **{"X-Jeton": ecran.jeton})[0].status,
             200)
    envoi = json.dumps({"LANGUES": "fr,en,zh,ka"}).encode()
    verifier("POST /reglages sans jeton", demander("POST", "/reglages", envoi)[0].status, 403)
    verifier("POST /reglages d'un autre site", demander("POST", "/reglages", envoi, Host="mechant.example",
                                                        **{"X-Jeton": ecran.jeton})[0].status, 403)
    verifier("rien d'écrit sans le jeton", "LANGUES" in reglages.lire(), False)
    verifier("POST /reglages avec le jeton", demander("POST", "/reglages", envoi, **{"X-Jeton": ecran.jeton})[0].status,
             200)
    verifier("écrit", reglages.lire().get("LANGUES"), "fr,en,zh,ka")
    r, corps = demander("POST", "/reglages", b'{"MODELE": "gpt"}', **{"X-Jeton": ecran.jeton})
    verifier("POST /reglages refusé en clair", (r.status, "Modèle inconnu" in corps.decode()), (422, True))
    verifier("POST /reglages/claude", demander("POST", "/reglages/claude", **{"X-Jeton": ecran.jeton})[0].status, 204)
    verifier("la connexion à Claude s'ouvre (dans le Terminal)", connexions, [True])
    verifier("le bouton Réglages du tableau", ecran.action("reglages") is not None, True)

print(f"\n{'✅' if not echecs else '❌'} langues : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
