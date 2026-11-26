"""Le bureau : fenêtres, modes, rangement d'un dossier, santé du Mac, recherche dans tes fichiers.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_bureau.py

Rien ne bouge sur le vrai Mac : osascript, open et mdfind sont remplacés par de faux, et ta « maison » est un
dossier temporaire (Path.home et HOME y pointent). Tout ce qui s'écrit (modes, règles, journaux) y reste.
"""

import asyncio
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, bureau, config, outils, permissions  # noqa: E402
from jervis.permissions import Decision  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp()).resolve()
MAISON = TMP / "maison"
MAISON.mkdir()
Path.home = classmethod(lambda cls: MAISON)  # type: ignore[method-assign]
os.environ["HOME"] = str(MAISON)
config.FICHIER_MODES = TMP / "modes.json"
config.FICHIER_RANGEMENT = TMP / "rangement.json"
config.DOSSIER_ANNULER_RANGEMENT = TMP / "rangements"

# --- Les faux osascript / open / mdfind ---------------------------------------------------------------------
lances: list[tuple] = []
scripts: list[tuple] = []
reponses_mdfind = {"sortie": ""}


async def faux_lancer(*cmd, entree=None, delai=30):
    lances.append(cmd)
    if cmd[:3] == ("osascript", "-l", "JavaScript"):
        return 0, "[0, 25, 1440, 875]"
    if cmd[0] == "mdfind":
        return 0, reponses_mdfind["sortie"]
    return 0, ""


async def faux_applescript(script, *args):
    scripts.append((script, args))
    if script is bureau._EN_MARCHE:
        return 0, "Music"
    if script is bureau._LECTEUR:
        return 0, "paused"
    return 0, "ok"


bureau._lancer = faux_lancer
bureau._applescript = faux_applescript


def texte(r: dict) -> str:
    return r["content"][0]["text"]


# --- Les fenêtres ---------------------------------------------------------------------------------------------
E = (0, 25, 1440, 875)
verifier("fenêtres : à gauche, la moitié", bureau.rectangles("gauche", E, 1), [(0, 25, 720, 875)])
verifier("fenêtres : à droite, l'autre moitié", bureau.rectangles("droite", E, 1), [(720, 25, 720, 875)])
verifier("fenêtres : côte à côte, la dernière prend le reste",
         bureau.rectangles("cote_a_cote", (0, 0, 1000, 800), 3), [(0, 0, 333, 800), (333, 0, 333, 800), (666, 0, 334, 800)])
verifier("fenêtres : grille de 3 → 2 colonnes, 2 rangées",
         bureau.rectangles("grille", (0, 0, 1000, 800), 3), [(0, 0, 500, 400), (500, 0, 500, 400), (0, 400, 500, 400)])
verifier("fenêtres : principal = une grande à gauche, les autres empilées",
         bureau.rectangles("principal", (0, 0, 1000, 800), 3), [(0, 0, 600, 800), (600, 0, 400, 400),
                                                                (600, 400, 400, 400)])
verifier("fenêtres : plein écran (sans le mode plein écran de macOS)", bureau.rectangles("plein", E, 2), [E, E])
verifier("fenêtres : centré", bureau.rectangles("centre", (0, 0, 900, 1000), 1), [(150, 100, 600, 800)])


async def tour_des_outils() -> dict:
    r = {}
    r["ranger"] = texte(await bureau.ranger_fenetres.handler({"disposition": "cote_a_cote", "apps": "Cursor, Chrome"}))
    r["placer"] = [a for s, a in scripts if s is bureau._PLACER]

    # --- Les modes ---
    r["vide"] = texte(await bureau.mode.handler({}))
    r["definir"] = texte(await bureau.definir_mode.handler(
        {"nom": "Mode Travail", "ouvrir": "Cursor, Slack", "fermer": ["Messages"],
         "liens": "https://linear.app, javascript:alert(1)", "volume": 20, "raccourci": "Concentration",
         "musique": "lo-fi", "fenetres": "cote_a_cote"}))
    r["definir bizarre"] = texte(await bureau.definir_mode.handler({"nom": "détente", "fenetres": "n'importe",
                                                                    "volume": "fort"}))
    r["détente"] = bureau.modes().get("détente")
    r["liste"] = texte(await bureau.mode.handler({}))
    lances.clear()
    scripts.clear()
    r["mode"] = texte(await bureau.mode.handler({"nom": "travail"}))
    r["lances"] = list(lances)
    r["scripts"] = [(s, a) for s, a in scripts]
    r["inconnu"] = await bureau.mode.handler({"nom": "vacances"})
    r["effacer"] = texte(await bureau.definir_mode.handler({"nom": "détente", "supprimer": True}))

    # --- La musique ---
    r["volume musique"] = await bureau.musique.handler({"action": "volume", "valeur": "fort"})
    r["chercher vide"] = await bureau.musique.handler({"action": "chercher"})
    r["pause"] = texte(await bureau.musique.handler({"action": "pause"}))
    return r


r = asyncio.run(tour_des_outils())
verifier("ranger_fenetres : deux apps côte à côte, sur l'écran utile",
         r["placer"], [("Cursor", "0", "25", "720", "875"), ("Chrome", "720", "25", "720", "875")])
verifier("ranger_fenetres : il dit ce qu'il a rangé", r["ranger"], "Rangé (cote_a_cote) : Cursor, Chrome.")
verifier("modes : aucun au début", r["vide"].startswith("Aucun mode"), True)
verifier("modes : enregistré sous « travail » (sans « mode »)", r["definir"].startswith("Mode « travail » enregistré"),
         True)
modes = bureau.modes()
verifier("modes : seulement les liens http(s), pas un javascript:", modes["travail"]["liens"], ["https://linear.app"])
verifier("modes : une disposition inconnue ou un volume en lettres sont ignorés", r["détente"], {})
verifier("modes : la liste", "travail (ouvre Cursor, Slack, ferme Messages" in r["liste"], True)
verifier("mode : il ferme Messages", (bureau._QUITTER, ("Messages",)) in r["scripts"], True)
verifier("mode : il ouvre Cursor et Slack, et le lien",
         [c for c in r["lances"] if c[0] == "open"], [("open", "-a", "Cursor"), ("open", "-a", "Slack"),
                                                     ("open", "https://linear.app")])
verifier("mode : le volume à 20 %", ("set volume output volume 20", ()) in r["scripts"], True)
verifier("mode : le raccourci de concentration", ("shortcuts", "run", "Concentration") in r["lances"], True)
verifier("mode : la musique cherchée", any(s is bureau._LECTEUR and a[0] == "chercher" and a[2] == "lo-fi"
                                          for s, a in r["scripts"]), True)
verifier("mode : les fenêtres rangées à la fin", any(s is bureau._PLACER for s, _ in r["scripts"]), True)
verifier("mode : il raconte", r["mode"].startswith("Mode « travail » : fermé Messages, ouvert Cursor"), True)
verifier("mode : inconnu → la liste des siens", (r["inconnu"].get("is_error"), "travail" in texte(r["inconnu"])),
         (True, True))
verifier("modes : effacé", ("détente" in bureau.modes(), r["effacer"]), (False, "Mode « détente » effacé."))
verifier("musique : un volume en lettres est refusé", r["volume musique"].get("is_error"), True)
verifier("musique : chercher sans rien → la question", r["chercher vide"].get("is_error"), True)
verifier("musique : Musique quand Spotify n'est pas ouvert", r["pause"], "Music : en pause")

# --- Le rangement -----------------------------------------------------------------------------------------------
TEL = MAISON / "Downloads"
TEL.mkdir()
for nom in ("Facture_Mars.pdf", "photo.jpg", "notes.txt", "Setup.dmg", "film.mp4.part", ".DS_Store", "truc.xyz",
            "rapport.pdf"):
    (TEL / nom).write_text(nom)
(TEL / "Un dossier").mkdir()
(TEL / "Images").mkdir()
(TEL / "Images" / "photo.jpg").write_text("l'ancienne")
regles = [{"motif": "facture", "dossier": "Documents/Factures"}]
plan = {f.name: str(d.relative_to(TEL)) for f, d in bureau.plan_rangement(TEL, regles)}
verifier("rangement : tes règles d'abord, puis le genre ; ni caché, ni en cours, ni inconnu, ni dossier",
         plan, {"Facture_Mars.pdf": "Documents/Factures", "photo.jpg": "Images", "notes.txt": "Documents",
                "Setup.dmg": "Installeurs", "rapport.pdf": "PDF"})
verifier("rangement : déjà rangé → rien (pas d'« Images/Images »)", bureau.plan_rangement(TEL / "Images", []), [])
verifier("rangement : une règle vers ~/… va chez toi, pas sous le dossier rangé",
         [d for _, d in bureau.plan_rangement(TEL, [{"motif": "facture", "dossier": "~/Compta/"}])][0],
         MAISON / "Compta")

verifier("permis : Téléchargements", bureau.dossier_permis(TEL), None)
verifier("permis : pas ta maison elle-même", bureau.dossier_permis(MAISON), "c'est ton dossier personnel lui-même")
(MAISON / "Library" / "Caches").mkdir(parents=True)
(MAISON / ".config").mkdir()
verifier("permis : ni la Bibliothèque, ni un dossier caché",
         [bureau.dossier_permis(MAISON / "Library" / "Caches"), bureau.dossier_permis(MAISON / ".config")],
         ["c'est un dossier système ou caché"] * 2)
verifier("permis : rien hors de chez toi", bureau.dossier_permis(Path("/etc")),
         "il n'est pas dans ton dossier personnel")
(MAISON / "projet" / ".git").mkdir(parents=True)
verifier("permis : pas un dépôt de code", bureau.dossier_permis(MAISON / "projet"),
         "c'est un dossier de code, d'app ou de secrets")
verifier("permis : un fichier n'est pas un dossier", bureau.dossier_permis(TEL / "notes.txt"), "ce n'est pas un dossier")


async def rangement() -> dict:
    r = {}
    r["noter .."] = await bureau.noter_regle_rangement.handler({"motif": "x", "dossier": "../../etc"})
    r["noter"] = texte(await bureau.noter_regle_rangement.handler({"motif": "facture",
                                                                   "dossier": "Documents/Factures"}))
    await bureau.noter_regle_rangement.handler({"motif": "rapport", "dossier": "/etc/rapports"})
    r["regles"] = bureau.regles_rangement()
    r["plan"] = texte(await bureau.ranger_dossier.handler({"dossier": "Downloads"}))
    r["apres plan"] = sorted(p.name for p in TEL.iterdir())
    r["refus"] = await bureau.ranger_dossier.handler({"dossier": "~/Library/Caches", "appliquer": True})
    r["appliquer"] = texte(await bureau.ranger_dossier.handler({"dossier": str(TEL), "appliquer": True}))
    r["apres"] = sorted(str(p.relative_to(TEL)) for p in TEL.rglob("*") if p.is_file())
    r["annuler"] = texte(await bureau.annuler_rangement.handler({}))
    r["remis"] = sorted(str(p.relative_to(TEL)) for p in TEL.rglob("*") if p.is_file())
    r["dossiers"] = sorted(p.name for p in TEL.iterdir() if p.is_dir())
    r["encore"] = texte(await bureau.annuler_rangement.handler({}))
    r["oublier"] = texte(await bureau.noter_regle_rangement.handler({"motif": "RAPPORT", "supprimer": True}))
    return r


avant = sorted(str(p.relative_to(TEL)) for p in TEL.rglob("*") if p.is_file())
r = asyncio.run(rangement())
verifier("règle : pas de « .. »", r["noter .."].get("is_error"), True)
verifier("règle : retenue", r["noter"], "Retenu : les fichiers avec « facture » vont dans Documents/Factures.")
verifier("règle : la dernière notée passe en premier", [x["motif"] for x in r["regles"]], ["rapport", "facture"])
verifier("ranger : d'abord le plan, rien ne bouge", (r["plan"].startswith("Plan pour"), r["apres plan"]),
         (True, sorted(p.name for p in TEL.iterdir())))
verifier("ranger : jamais la Bibliothèque", r["refus"].get("is_error"), True)
verifier("ranger : appliqué (une règle vers /etc est ignorée, rien n'est écrasé)",
         r["apres"], sorted([".DS_Store", "Documents/Factures/Facture_Mars.pdf", "Documents/notes.txt",
                             "Images/photo (2).jpg", "Images/photo.jpg", "Installeurs/Setup.dmg", "film.mp4.part",
                             "rapport.pdf", "truc.xyz"]))
verifier("ranger : le journal du rangement, marqué « annulé » une fois défait",
         [p.suffix for p in config.DOSSIER_ANNULER_RANGEMENT.iterdir()], [".annule"])
verifier("annuler : tout revient", (r["annuler"], r["remis"]), ("Remis en place : 4 fichier(s).", avant))
verifier("annuler : les dossiers créés pour l'occasion disparaissent (pas ceux qui étaient là)",
         r["dossiers"], ["Images", "Un dossier"])
verifier("annuler : une seule fois", r["encore"], "Aucun rangement à défaire.")
verifier("règle : oubliée, sans la casse", (r["oublier"], [x["motif"] for x in bureau.regles_rangement()]),
         ("Règle oubliée : « RAPPORT ».", ["facture"]))

# --- La santé du Mac ------------------------------------------------------------------------------------------
config.SANTE_DISQUE_MIN_GO, config.SANTE_BATTERIE_MIN, config.SANTE_CPU_MAX = 15, 10, 90
m = {"disque_libre_go": 6.0, "disque_total_go": 500.0, "batterie": 8, "en_charge": False, "memoire_libre_pct": 4,
     "vitesse_processeur_pct": 50, "apps": [("Chrome", 150.0, 900), ("kernel_task", 300.0, 10), ("Mail", 95.0, 80)]}
precedente = {"apps": [("Chrome", 120.0, 900), ("kernel_task", 300.0, 10)]}
deja: dict = {}
premieres = bureau.alertes(m, deja, "2026-11-26", precedente)
verifier("santé : disque, batterie, mémoire, chaleur, et Chrome (deux fois de suite) — pas kernel_task, pas Mail",
         sorted(deja), ["batterie", "chaleur", "cpu:Chrome", "disque", "memoire"])
verifier("santé : il dit le chiffre", "6 Go" in premieres[0], True)
verifier("santé : une fois par jour", bureau.alertes(m, deja, "2026-11-26", precedente), [])
verifier("santé : le lendemain, il redit", len(bureau.alertes(m, deja, "2026-11-27", precedente)), 5)
verifier("santé : branché, la batterie basse n'est pas un souci",
         bureau.alertes({"batterie": 5, "en_charge": True}, {}, "2026-11-26"), [])
verifier("santé : une app gourmande une seule fois → rien", bureau.alertes({"apps": [("Chrome", 150.0, 900)]}, {}), [])
verifier("santé : le bilan", bureau.bilan(m).startswith("disque : 6 Go libres sur 500 ; batterie : 8 %"), True)
verifier("santé : rien mesuré", bureau.bilan({}), "Je n'arrive pas à mesurer le Mac.")
config.SANTE_INTERVALLE_MIN = 0
arret = bureau.surveiller_sante(lambda t: None)
verifier("santé : SANTE_INTERVALLE_MIN=0 → aucun fil",
         (isinstance(arret, threading.Event), [f.name for f in threading.enumerate() if f.name == "sante-du-mac"]),
         (True, []))

# --- La recherche dans tes fichiers -----------------------------------------------------------------------------
q = bureau.requete_spotlight("devis de mars", "pdf", "2026-03-01", "2026-03-31")
verifier("recherche : chaque mot dans le nom ou le contenu, plus le genre et les dates", q,
         '(kMDItemDisplayName == "*devis*"cd || kMDItemTextContent == "devis*"cdw) && '
         '(kMDItemDisplayName == "*de*"cd || kMDItemTextContent == "de*"cdw) && '
         '(kMDItemDisplayName == "*mars*"cd || kMDItemTextContent == "mars*"cdw) && '
         'kMDItemContentType == "com.adobe.pdf" && '
         'kMDItemContentModificationDate >= $time.iso(2026-03-01) && '
         'kMDItemContentModificationDate <= $time.iso(2026-03-31)')
piege = bureau.requete_spotlight('loyer" || kMDItemFSName == "*')
verifier("recherche : un guillemet ne casse pas la requête (deux mots, quatre guillemets chacun)",
         piege.count('"'), 8)
verifier("recherche : une date mal écrite est ignorée", "$time" in bureau.requete_spotlight("x1", apres="mars"), False)
verifier("recherche : rien à chercher → vide", bureau.requete_spotlight("a"), "")
verifier("caché : Bibliothèque, dossier caché, secrets, node_modules",
         [bureau.chemin_cache(str(MAISON / p)) for p in ("Library/Mail/x.pdf", ".ssh/id_rsa", "Documents/credentials.pdf",
                                                         "code/node_modules/a/b.pdf", "Documents/Devis mars.pdf")],
         [True, True, True, True, False])

DOCS = MAISON / "Documents"
DOCS.mkdir()
(DOCS / "Devis mars.pdf").write_bytes(b"%PDF" + b"0" * 3000)
(DOCS / "installer.sh").write_text("echo")


async def recherche() -> dict:
    r = {}
    reponses_mdfind["sortie"] = "\n".join([str(DOCS / "Devis mars.pdf"), str(MAISON / "Library" / "Mail" / "y.pdf"),
                                           str(DOCS / "disparu.pdf"), ""])
    r["chercher"] = texte(await bureau.chercher_fichiers.handler({"mots": "devis mars", "genre": "PDF"}))
    r["requete"] = [c for c in lances if c[0] == "mdfind"][-1]
    reponses_mdfind["sortie"] = ""
    r["rien"] = texte(await bureau.chercher_fichiers.handler({"mots": "zzz"}))
    r["vide"] = await bureau.chercher_fichiers.handler({"mots": ""})
    lances.clear()
    r["ouvrir"] = texte(await bureau.ouvrir_fichier.handler({"chemin": "~/Documents/Devis mars.pdf"}))
    r["open"] = list(lances)
    r["script"] = await bureau.ouvrir_fichier.handler({"chemin": str(DOCS / "installer.sh")})
    r["dehors"] = await bureau.ouvrir_fichier.handler({"chemin": "/etc/hosts"})
    r["cache"] = await bureau.ouvrir_fichier.handler({"chemin": "~/.ssh/config.txt"})
    r["absent"] = await bureau.ouvrir_fichier.handler({"chemin": "~/Documents/absent.pdf"})
    return r


r = asyncio.run(recherche())
verifier("chercher : seulement chez toi, et en minuscule pour le genre",
         (r["requete"][:3], "com.adobe.pdf" in r["requete"][3]), (("mdfind", "-onlyin", str(MAISON)), True))
verifier("chercher : sans la Bibliothèque ni les fichiers disparus, avec la date et la taille",
         r["chercher"].splitlines()[0], "1 trouvé(s), le plus récent d'abord :")
verifier("chercher : le chemin court (~)", "· ~/Documents/Devis mars.pdf · 2.9 Ko" in r["chercher"], True)
verifier("chercher : rien → il propose autre chose", r["rien"].startswith("Rien trouvé"), True)
verifier("chercher : sans mot → erreur", r["vide"].get("is_error"), True)
verifier("ouvrir : le PDF, dans son app", (r["ouvrir"], r["open"]),
         ("Ouvert : Devis mars.pdf", [("open", str(DOCS / "Devis mars.pdf"))]))
verifier("ouvrir : jamais un script, ni hors de chez toi, ni un dossier caché",
         [r[k].get("is_error") for k in ("script", "dehors", "cache")], [True] * 3)
verifier("ouvrir : un fichier absent", "n'existe pas" in texte(r["absent"]), True)

# --- Permissions, consignes et branchement ------------------------------------------------------------------
verifier("permissions : appliquer un rangement → il te demande",
         permissions._maison("ranger_dossier", {"dossier": "~/Downloads", "appliquer": True}, set()), Decision.DEMANDER)
verifier("permissions : le plan seul se lit sans demander",
         permissions._maison("ranger_dossier", {"dossier": "~/Downloads"}, set()), Decision.OUI)
verifier("permissions : ce qui rend un texte d'ailleurs (fichiers, sélection) n'est pas « interne »",
         [permissions.lit_dehors(f"mcp__jervis__{n}") for n in ("chercher_fichiers", "texte_selectionne",
                                                                 "ranger_dossier", "mode", "ranger_fenetres")],
         [True, True, True, True, False])  # (un mode relaie le titre d'un morceau, la sortie d'un raccourci)
verifier("permissions : un mode et une règle de rangement s'écrivent pour plus tard",
         {"definir_mode", "noter_regle_rangement"} <= permissions.ECRIT_POUR_PLUS_TARD, True)
noms = {t.name for t in outils.OUTILS}
verifier("outils : tous branchés", {t.name for t in bureau.OUTILS_BUREAU} <= noms, True)
c = brain._consignes(ecran=False)
verifier("consignes : il sait ranger d'abord en plan, et chercher puis ouvrir",
         all(m in c for m in ("ranger_dossier", "annuler_rangement", "chercher_fichiers", "ouvrir_fichier",
                              "definir_mode", "sante_du_mac")), True)

print(f"\n{'✅' if not echecs else '❌'} bureau : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
