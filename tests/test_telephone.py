"""Steve t'appelle pour le bilan du soir (telephone.py) : qui il appelle, le câble audio, le micro de macOS changé
le temps de l'appel puis remis, FaceTime qui n'est pas pris pour une réunion, et ta voix ni vérifiée ni apprise.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_telephone.py

Aucun vrai appel, aucun micro changé : les appareils de macOS, FaceTime et les clics sont des faux, et le fichier
du micro d'avant l'appel va dans un dossier temporaire (rien dans ton vrai espace).
"""

import asyncio
import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import appareils, appels, audio, config, reunion, telephone  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-telephone-")).resolve()
telephone.ETAT_MICRO = TMP / "micro_avant_appel.txt"

# --- Qui appeler --------------------------------------------------------------------------------------------------
verifier("une adresse Apple", telephone.adresse("moi@icloud.com"), "moi@icloud.com")
verifier("un numéro, écrit comme on veut", telephone.adresse("06 12 34 56 78"), "0612345678")
verifier("jamais un numéro surtaxé", telephone.adresse("08 99 12 34 56"), None)
verifier("jamais les urgences", telephone.adresse("112"), None)
verifier("ni un nom", telephone.adresse("maman"), None)

# --- Les appareils de macOS (faux) --------------------------------------------------------------------------------
MAC = appareils.Appareil(10, "Micro du MacBook Pro", appareils.INTEGRE, True)
AIRPODS = appareils.Appareil(11, "AirPods de toi", "blue", True)
CABLE = appareils.Appareil(20, "BlackHole 2ch", "virt", True)
HP = appareils.Appareil(12, "Haut-parleurs du MacBook Pro", appareils.INTEGRE, False)
mac = {"tous": [MAC, AIRPODS, CABLE, HP], "micro": MAC, "reglages": []}


def faux_regler(objet: int) -> bool:
    mac["reglages"].append(objet)
    trouve = next((a for a in mac["tous"] if a.id == objet), None)
    if trouve is None:
        return False
    mac["micro"] = trouve
    return True


appareils.tous = lambda: list(mac["tous"])
appareils.par_defaut = lambda: (mac["micro"], HP)
appareils.regler_micro = faux_regler

# --- Prêt ou pas ---------------------------------------------------------------------------------------------------
config.APPELS, config.APPEL_CABLE, config.MICRO = True, "BlackHole", "auto"
config.BILAN_SOIR_APPEL = ""
verifier("sans adresse : pas d'appel (le vocal, comme avant)", "JERVIS_BILAN_SOIR_APPEL" in telephone.pas_pret(),
         True)
config.BILAN_SOIR_APPEL = "maman"
verifier("une adresse qui n'en est pas une", "ni un numéro" in telephone.pas_pret(), True)
config.BILAN_SOIR_APPEL = "moi@icloud.com"
config.APPELS = False
verifier("les appels coupés", "JERVIS_APPELS=non" in telephone.pas_pret(), True)
config.APPELS = True
mac["tous"] = [MAC, AIRPODS, HP]
verifier("BlackHole pas installé (ou Mac pas redémarré)", "BlackHole" in (telephone.pas_pret() or ""), True)
mac["tous"] = [MAC, AIRPODS, CABLE, HP]
verifier("tout est là : il peut t'appeler", telephone.pas_pret(), None)
verifier("le câble se trouve par un bout de son nom", telephone.cable().nom, "BlackHole 2ch")

# --- Le micro de macOS, changé le temps de l'appel puis remis -------------------------------------------------------
prepare = telephone.preparer()
verifier("il prépare : le câble, et le micro par lequel il t'entendra",
         (prepare["cable"], prepare["cable_id"], prepare["avant"], prepare["micro"]),
         ("BlackHole 2ch", 20, "Micro du MacBook Pro", "Micro du MacBook Pro"))
verifier("préparer ne change rien", (mac["micro"].nom, mac["reglages"]), ("Micro du MacBook Pro", []))

mac["micro"] = AIRPODS
verifier("tes AirPods comme micro : il t'écoute par celui du Mac (MICRO=auto), et les remet après",
         (telephone.preparer()["micro"], telephone.preparer()["avant"]), ("Micro du MacBook Pro", "AirPods de toi"))
mac["micro"] = CABLE
verifier("le câble est déjà le micro de macOS : il t'écoute par celui du Mac",
         telephone.preparer()["micro"], "Micro du MacBook Pro")
mac["micro"] = MAC

verifier("pas en ligne avant", (telephone.en_ligne(), telephone.ignorer_facetime()), (False, False))
verifier("il branche le câble", telephone.brancher(prepare), True)
verifier("… qui devient le micro de macOS (FaceTime l'écoute)", mac["micro"].nom, "BlackHole 2ch")
verifier("… l'ancien est noté (pour un redémarrage en plein appel)",
         telephone.ETAT_MICRO.read_text(encoding="utf-8"), "Micro du MacBook Pro")
verifier("en ligne", (telephone.en_ligne(), telephone.ignorer_facetime()), (True, True))

telephone.debrancher()
verifier("fin de l'appel : ton micro revient", mac["micro"].nom, "Micro du MacBook Pro")
verifier("… la note est effacée", telephone.ETAT_MICRO.exists(), False)
verifier("… FaceTime reste ignoré quelques secondes (il lâche le micro)",
         (telephone.en_ligne(), telephone.ignorer_facetime()), (False, True))
telephone._jusqua = 0.0
verifier("… puis plus du tout", telephone.ignorer_facetime(), False)

# Steve s'arrête en plein appel : au redémarrage, le micro d'avant revient.
telephone.brancher(prepare)
telephone._en_ligne = False
verifier("au redémarrage : il remet ton micro", (telephone.remettre_le_micro(), mac["micro"].nom),
         (True, "Micro du MacBook Pro"))
verifier("… une seule fois", (telephone.remettre_le_micro(), telephone.ETAT_MICRO.exists()), (False, False))
telephone.ETAT_MICRO.write_text("Micro qui n'est plus branché", encoding="utf-8")
verifier("un micro débranché depuis : on n'insiste pas", (telephone.remettre_le_micro(),
                                                          telephone.ETAT_MICRO.exists()), (False, False))

# macOS refuse : rien ne reste branché.
appareils.regler_micro = lambda objet: False
verifier("macOS refuse de changer de micro", telephone.brancher(prepare), False)
verifier("… pas en ligne, pas de note", (telephone.en_ligne(), telephone.ETAT_MICRO.exists()), (False, False))
appareils.regler_micro = faux_regler
telephone._jusqua = 0.0

# --- FaceTime : composer, raccrocher (faux) ----------------------------------------------------------------------
lance: list = []


async def faux_lancer(*cmd, **_kw):
    lance.append(cmd)
    return 0, ""


async def faux_cliquer(noms, _secondes=6):
    lance.append(("clic", noms[0]))
    return noms == appels.BOUTONS_APPELER


appels._lancer, appels.cliquer = faux_lancer, faux_cliquer
config.APPEL_AUTO = True
verifier("il compose en FaceTime audio", asyncio.run(telephone.composer("moi@icloud.com")), True)
verifier("… facetime-audio:// puis « Appeler »", lance,
         [("open", "facetime-audio://moi@icloud.com"), ("clic", "Appeler")])
lance.clear()
asyncio.run(telephone.raccrocher())
verifier("raccrocher : le bouton, sinon FaceTime fermé", [c[0] for c in lance], ["clic", "osascript"])

# --- La surveillance des appels ne prend pas son appel pour une réunion --------------------------------------------
debuts: list = []
lu = [(1, "com.apple.FaceTime", True)]
s = reunion.Surveillance(lambda apps: debuts.append(apps), lambda: None, lire=lambda: lu)
telephone._en_ligne = True
s.regarder()
verifier("pendant son appel : FaceTime au micro n'est pas une réunion", (debuts, s.en_appel), ([], False))
lu.append((2, "us.zoom.xos", True))
s.regarder()
verifier("… Zoom, si", debuts, [["us.zoom.xos"]])
telephone._en_ligne, telephone._jusqua = False, 0.0
debuts.clear()
s2 = reunion.Surveillance(lambda apps: debuts.append(apps), lambda: None, lire=lambda: [(1, "com.apple.FaceTime", True)])
s2.regarder()
verifier("hors de son appel, un FaceTime est bien un appel", debuts, [["com.apple.FaceTime"]])

# --- Ses oreilles : sa voix dans le câble, ta voix ni vérifiée ni apprise ------------------------------------------
o = object.__new__(audio.Oreilles)
o._route = None
o.haut_parleur = object()
o._stream = SimpleNamespace(device=(3, 7))
rouvert: list = []
o._rouvrir = lambda raison: rouvert.append(raison)
noms = {7: "BlackHole 2ch"}
audio.sd = SimpleNamespace(query_devices=lambda i=None, kind=None: {"name": noms.get(i, "?")}, default=audio.sd.default)

verifier("au départ : pas au téléphone", o.au_telephone, False)
verifier("router : sa voix part dans le câble", o.router("BlackHole 2ch", "Micro du MacBook Pro"), True)
verifier("… ses appareils sont épinglés (macOS change de micro : il ne bouge pas)",
         (o._signature(), o.au_telephone, rouvert), (("appel", "BlackHole 2ch", "Micro du MacBook Pro"), True,
                                                     ["il t'appelle"]))
o._empreinte = SimpleNamespace(apprendre=lambda *_a: echecs.append("il a appris ta voix au téléphone"))
config.APPRENDRE_VOIX = True
phrase = SimpleNamespace(empreinte=[1.0], chuchotee=False)
verifier("au téléphone, ta voix n'est pas apprise (voix.npy intacte)", o.apprendre_voix(phrase, True), None)
noms[7] = "Haut-parleurs du MacBook Pro"
verifier("sa voix ne part pas dans le câble : router le dit", o.router("BlackHole 2ch", "Micro du MacBook Pro"),
         False)
verifier("fin de l'appel : retour aux appareils de macOS", (o.router(None), o.au_telephone, rouvert[-1]),
         (True, False, "fin de l'appel"))

# --- Branché dans Steve ------------------------------------------------------------------------------------------
principal = (Path(telephone.__file__).parent / "__main__.py").read_text(encoding="utf-8")
verifier("le bilan au loin essaie l'appel d'abord, le vocal sinon",
         ("telephone.pas_pret()" in principal, "appeler_pour_le_bilan(" in principal,
          "boite.raconter_vocal(reponse)" in principal), (True, True, True))
verifier("c'est la boucle du micro qui passe l'appel",
         ('if direct["consigne"] is not None:' in principal and "await au_telephone()" in principal), True)
verifier("dans l'ordre : sa voix dans le câble, puis le câble comme micro ; à la fin, l'inverse",
         (principal.index("oreilles.router, prepare") < principal.index("telephone.brancher, prepare"),
          principal.index("telephone.debrancher") < principal.index("oreilles.router, None")), (True, True))
verifier("au démarrage, il remet ton micro d'avant un appel interrompu", "telephone.remettre_le_micro" in principal,
         True)

verifier("au téléphone, il ne fait que lire sans ton oui (sa voix n'y est pas vérifiée)",
         "cerveau.demander(texte, en_appel=oreilles.au_telephone)" in principal, True)
verifier("… et ce oui se donne par écrit sur ton iPhone, jamais à voix haute dans l'appel",
         "if oreilles.au_telephone:" in principal and "distance.la_derniere(boites).demander(question)" in principal,
         True)

# Il attend un vrai « allô » pour savoir que tu as décroché.
from jervis import __main__ as m  # noqa: E402

for entendu, attendu in [("Allô ?", True), ("allo oui", True), ("Oui, je t'écoute", True), ("Salut", True),
                         (f"Hey {config.PRENOM}", True), ("ouais vas-y", True),
                         ("et maintenant la météo de demain", False), ("hmm", False), ("", False)]:
    verifier(f"décroché ? « {entendu} »", bool(m.DECROCHE.search(entendu)), attendu)

print(f"\n{total - len(echecs)}/{total} vérifications OK")
os._exit(1 if echecs else 0)
