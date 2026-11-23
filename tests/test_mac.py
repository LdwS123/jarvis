"""Steve sur tout le Mac : prompts dans Cursor (Claude Code ou l'agent), corbeille, effacer sa mémoire, autorisations.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_mac.py

Rien n'est vraiment ouvert, tapé ni jeté : `open`, les touches et la corbeille sont remplacés par des faux.
"""

import asyncio
import os
import tempfile
import threading
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, config, outils, tableau  # noqa: E402
from jervis.permissions import Decision, decrire, politique  # noqa: E402

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


# --- 1. Le lien qui dépose un prompt dans Cursor ----------------------------------------------------------------
PROMPT = "Corrige le bug & relance : ça marche ? #urgent\nDeuxième ligne à 100 %."
for cible, debut, parametre in [("claude_code", "cursor://anthropic.claude-code/open?", "prompt"),
                                ("agent", "cursor://anysphere.cursor-deeplink/prompt?", "text")]:
    lien = outils.lien_cursor(PROMPT, cible)
    verifier(f"lien {cible} : la bonne adresse", lien.startswith(debut), True)
    verifier(f"lien {cible} : le prompt revient intact (& ? # retours à la ligne)",
             parse_qs(urlsplit(lien).query, keep_blank_values=True), {parametre: [PROMPT]})
    verifier(f"lien {cible} : aucun espace ni + (Cursor les garderait tels quels)",
             " " in lien or "+" in lien, False)
verifier("lien : trop long pour un lien", outils.lien_cursor("mot " * 4000), None)

config.CIBLE_CURSOR = "claude_code"
for demandee, attendue in [(None, "claude_code"), ("", "claude_code"), ("claude_code", "claude_code"),
                           ("Claude Code", "claude_code"), ("l'extension Claude", "claude_code"),
                           ("agent", "agent"), ("Composer", "agent"), ("chat", "agent"),
                           ("n'importe quoi", "claude_code")]:
    verifier(f"cible « {demandee} »", outils.cible_cursor(demandee), attendue)
config.CIBLE_CURSOR = "agent"
verifier("cible : son réglage décide quand il dit juste « Cursor »", outils.cible_cursor(None), "agent")
verifier("cible : « Claude Code » gagne sur le réglage", outils.cible_cursor("claude-code"), "claude_code")
config.CIBLE_CURSOR = "nimporte"
verifier("cible : réglage invalide → Claude Code", outils.cible_cursor(None), "claude_code")
config.CIBLE_CURSOR = "claude_code"

# --- 2. L'outil ecrire_dans_cursor, avec de faux `open`, clavier et premier plan -------------------------------------
lances: list[tuple] = []
touches: list[str] = []
devant = "Cursor"
refus_touche: str | None = None


async def faux_lancer(*cmd, entree=None, delai=30):
    lances.append((*cmd, entree) if entree is not None else cmd)
    return 0, ""


async def faux_devant():
    return devant


async def fausse_touche(script):
    touches.append(script)
    return refus_touche


outils._lancer, outils._app_devant, outils._touche, outils.ATTENTE_CURSOR_S = faux_lancer, faux_devant, fausse_touche, 0


def ecrire(**args) -> str:
    lances.clear()
    touches.clear()
    return texte(appeler(outils.ecrire_dans_cursor, **args))


dit = ecrire(prompt="Ajoute un test.")
verifier("Cursor : sans `envoyer`, le lien part et aucune touche", (len(lances), lances[0][0], touches), (1, "open", []))
verifier("Cursor : par défaut c'est Claude Code", lances[0][1].startswith("cursor://anthropic.claude-code/open?prompt="), True)
verifier("Cursor : il sait que ce n'est pas envoyé", "pas envoyé" in dit, True)

dit = ecrire(prompt="Ajoute un test.", envoyer=True)
verifier("Claude Code + envoyer : Entrée", touches, ["key code 36"])
verifier("Claude Code + envoyer : il doit vérifier d'un coup d'œil", "see Cursor" in dit, True)

dit = ecrire(prompt="Ajoute un test.", envoyer="true")   # le modèle envoie parfois le booléen en texte
verifier("envoyer en texte : compris", touches, ["key code 36"])

devant = "Google Chrome"
dit = ecrire(prompt="Ajoute un test.", envoyer=True)
verifier("une autre app est devant : pas d'Entrée à l'aveugle", touches, [])
verifier("une autre app est devant : il le dit", "Google Chrome" in dit and "pas appuyé" in dit, True)
devant = "Cursor"

refus_touche = "macOS ne te laisse pas appuyer sur les touches"
dit = ecrire(prompt="Ajoute un test.", envoyer=True)
verifier("Accessibilité refusée : il le dit, sans prétendre avoir envoyé", "pas envoyé" in dit and "macOS" in dit, True)
refus_touche = None

dit = ecrire(prompt="Ajoute un test.", cible="agent", envoyer=True)
verifier("agent de Cursor : l'autre lien", lances[0][1].startswith("cursor://anysphere.cursor-deeplink/prompt?text="), True)
verifier("agent de Cursor : pas d'Entrée (fenêtre de confirmation d'abord)", touches, [])

long_prompt = "ligne de consigne\n" * 900
dit = ecrire(prompt=long_prompt, envoyer=True)
verifier("prompt trop long : presse-papiers, onglet Claude Code, coller, Entrée",
         ([c[0] for c in lances], lances[0][-1] == long_prompt.strip(), lances[1][1], touches),
         (["pbcopy", "open"], True, "cursor://anthropic.claude-code/open",
          ['keystroke "v" using command down', "key code 36"]))
verifier("prompt vide : refusé", appeler(outils.ecrire_dans_cursor, prompt="  ").get("is_error"), True)
verifier("dossier introuvable : refusé", appeler(outils.ecrire_dans_cursor, prompt="x", dossier="/nexiste/pas").get("is_error"), True)

# --- 3. Ce qui ne part jamais à la corbeille ------------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp()).resolve()
maison, jervis = tmp / "koka", tmp / "koka" / "Jervis"
(jervis / "espace").mkdir(parents=True)
for chemin, refuse in [
        ("/", True), ("/System", True), ("/System/Library/CoreServices/Finder.app", True),
        ("/usr/local/bin/python3", True), ("/etc/hosts", True), ("/Library/Preferences/x.plist", True),
        ("/Applications", True), ("/Applications/Vieux Jeu.app", False),
        ("/Users/quelquun", True), ("/Volumes/Disque", True), ("/Volumes/Disque/film.mp4", False),
        ("/tmp", True), ("/private/tmp", True), ("/tmp/essai.txt", False),
        ("notes.txt", True), ("./notes.txt", True), ("", True),
        (maison, True), (tmp, True), (maison / "Documents", True), (maison / "documents", True),
        (maison / "DESKTOP", True), (maison / "Documents" / "vieux.pdf", False),
        (maison / "Desktop" / "Capture d'écran.png", False), (maison / "Downloads" / "truc.zip", False),
        (maison / "Library", True), (maison / "Library" / "Caches", True),
        (maison / "Library" / "Caches" / "com.appli" / "x", False), (maison / ".Trash" / "deja.txt", True),
        (maison / "projets" / "vieux-site", False), (maison / "Documents" / ".." / "Documents", True),
        (jervis, True), (jervis / "jervis" / "brain.py", True), (jervis / "memoire.md", True),
        (jervis / "JERVIS" / "brain.py", True), (jervis / "espace", True),
        (jervis / "espace" / "conversations.jsonl", True), (jervis / "espace" / "brouillon.md", False)]:
    verifier(f"corbeille refusée ? {str(chemin).replace(str(tmp), '…')}",
             outils.refus_corbeille(chemin, maison=maison, jervis=jervis) is not None, refuse)
verifier("corbeille : « ~ » tout seul refusé", outils.refus_corbeille("~") is not None, True)
verifier("corbeille : « ~/Desktop » refusé", outils.refus_corbeille("~/Desktop") is not None, True)
verifier("corbeille : un fichier du Bureau part", outils.refus_corbeille("~/Desktop/vieille capture.png"), None)
verifier("corbeille : son propre code refusé (le vrai dossier)", outils.refus_corbeille(config.DOSSIER / "jervis") is not None, True)

# --- 4. L'outil mettre_a_la_corbeille, avec une fausse corbeille ---------------------------------------------------
jetes: list[Path] = []
panne: str | None = None


def fausse_corbeille(chemin: Path):
    if panne:
        return panne
    jetes.append(Path(chemin))
    Path(chemin).unlink()
    return None


outils._jeter = fausse_corbeille
a, b = tmp / "a.txt", tmp / "b.txt"
a.write_text("a")
b.write_text("b")
resultat = appeler(outils.mettre_a_la_corbeille, chemins=[str(a), "/System/Library/Fonts", str(tmp / "absent.txt")])
verifier("corbeille : le bon fichier part, les autres sont expliqués",
         (jetes, "À la corbeille" in texte(resultat), "système" in texte(resultat), "introuvable" in texte(resultat),
          resultat.get("is_error")), ([a], True, True, True, None))
resultat = appeler(outils.mettre_a_la_corbeille, chemins=str(b))   # un seul chemin, donné en texte
verifier("corbeille : un chemin seul, en texte", jetes, [a, b])
verifier("corbeille : rien de jeté → erreur", appeler(outils.mettre_a_la_corbeille, chemins=["/System"]).get("is_error"), True)
verifier("corbeille : sans chemin → erreur", appeler(outils.mettre_a_la_corbeille, chemins=[]).get("is_error"), True)
c = tmp / "c.txt"
c.write_text("c")
panne = "Operation not permitted"
resultat = appeler(outils.mettre_a_la_corbeille, chemins=[str(c)])
verifier("corbeille : macOS bloque → il pense aux autorisations",
         (resultat.get("is_error"), "verifier_autorisations" in texte(resultat), c.exists()), (True, True, True))
panne = None

# --- 5. Effacer sa mémoire : les fichiers, puis le cerveau repart de zéro ------------------------------------------
outils.MEMOIRE, outils.JOURNAL = tmp / "memoire.md", tmp / "conversations.jsonl"


def remplir():
    outils.MEMOIRE.write_text("- Il s'appelle Koka.\n- Il aime l'escalade.\n", encoding="utf-8")
    outils.JOURNAL.write_text('{"toi": "salut"}\n', encoding="utf-8")
    jetes.clear()
    outils.a_oublier = None


remplir()
resultat = appeler(outils.effacer_memoire, quoi="memoire")
verifier("effacer « memoire » : seul le fichier mémoire part", (jetes, outils.JOURNAL.exists(), outils.a_oublier),
         ([outils.MEMOIRE], True, "memoire"))
remplir()
appeler(outils.effacer_memoire, quoi="conversations")
verifier("effacer « conversations » : seul le journal part", (jetes, outils.MEMOIRE.exists(), outils.a_oublier),
         ([outils.JOURNAL], True, "conversations"))
remplir()
appeler(outils.effacer_memoire, quoi="tout")
verifier("effacer « tout » : les deux", (jetes, outils.a_oublier), ([outils.MEMOIRE, outils.JOURNAL], "tout"))
remplir()
verifier("effacer « nimporte » : refusé, rien ne bouge",
         (appeler(outils.effacer_memoire, quoi="nimporte").get("is_error"), jetes, outils.a_oublier), (True, [], None))
panne = "disque plein"
resultat = appeler(outils.effacer_memoire, quoi="tout")
verifier("effacer : la corbeille refuse → erreur, et le cerveau n'oublie rien",
         (resultat.get("is_error"), outils.a_oublier, outils.MEMOIRE.exists()), (True, None, True))
panne = None

# Le cerveau : ses consignes contenaient la mémoire ; après l'effacement elles sont réécrites et Claude Code relancé.
remplir()
journal: list[str] = []
relances: list[int] = []
annulees: list[int] = []
cerveau = brain.Cerveau.__new__(brain.Cerveau)
cerveau._ecran = False
cerveau._options = types.SimpleNamespace(system_prompt=brain._consignes(ecran=False))
cerveau._journal = journal.append
cerveau._memorisations = [types.SimpleNamespace(cancel=lambda: annulees.append(1))]   # une relecture « en cours »
cerveau._echanges, cerveau._recents = [("a", "b")], [("a", "b")]
cerveau._appris, cerveau._nouveautes, cerveau._a_rappeler = ["il aime l'escalade"], ["il aime l'escalade"], False


async def faux_relancer():
    relances.append(1)
    cerveau._a_rappeler = True   # ce que fait le vrai : « redonne-lui le fil »


cerveau._relancer = faux_relancer
verifier("avant : sa mémoire est dans ses consignes", "Il aime l'escalade" in cerveau._options.system_prompt, True)
appeler(outils.effacer_memoire, quoi="tout")
asyncio.run(cerveau._tout_oublier())
verifier("après : ses consignes ne contiennent plus rien de toi",
         ("escalade" in cerveau._options.system_prompt, "(rien pour l'instant)" in cerveau._options.system_prompt), (False, True))
verifier("après : plus aucun échange gardé, rien à rappeler, Claude Code relancé une fois",
         (cerveau._echanges, cerveau._recents, cerveau._appris, cerveau._nouveautes, cerveau._a_rappeler, relances),
         ([], [], [], [], False, [1]))
verifier("après : la relecture en cours est annulée (elle aurait réécrit la mémoire)", annulees, [1])
verifier("après : le drapeau est baissé, et c'est dans le journal", (outils.a_oublier, "🧽" in journal[0]), (None, True))

# --- 6. Le Gardien : ce qui passe seul, ce qui est demandé, ce qui reste interdit ------------------------------------
CORBEILLE, OUBLI = "mcp__jervis__mettre_a_la_corbeille", "mcp__jervis__effacer_memoire"
for mode, attendu in [("confiance", Decision.OUI), ("essentielles", Decision.DEMANDER), ("toutes", Decision.DEMANDER)]:
    verifier(f"gardien {mode} : mettre à la corbeille",
             politique(CORBEILLE, {"chemins": ["/Users/x/Desktop/vieux.png"]}, mode=mode), attendu)
    verifier(f"gardien {mode} : effacer sa mémoire", politique(OUBLI, {"quoi": "tout"}, mode=mode), attendu)
    verifier(f"gardien {mode} : jeter une clé SSH est toujours demandé",
             politique(CORBEILLE, {"chemins": ["/Users/x/a.txt", "/Users/x/.ssh/id_ed25519"]}, mode=mode), Decision.DEMANDER)
    verifier(f"gardien {mode} : jeter un .env (chemin seul, en texte) est demandé",
             politique(CORBEILLE, {"chemin": "/Users/x/projet/.env"}, mode=mode), Decision.DEMANDER)
    verifier(f"gardien {mode} : rm -rf reste interdit", politique("Bash", {"command": "rm -rf ~/Documents"}, mode=mode), Decision.NON)
    verifier(f"gardien {mode} : sudo reste interdit", politique("Bash", {"command": "sudo purge"}, mode=mode), Decision.NON)
    for outil in ("memoire_du_mac", "verifier_autorisations", "ecrire_dans_cursor"):
        verifier(f"gardien {mode} : {outil} passe seul", politique(f"mcp__jervis__{outil}", {}, mode=mode), Decision.OUI)
verifier("gardien : vider la corbeille est demandé",
         politique("Bash", {"command": """osascript -e 'tell application "Finder" to empty trash'"""}, mode="confiance"),
         Decision.DEMANDER)
verifier("gardien : rm (sans -rf) est demandé", politique("Bash", {"command": "rm ~/Desktop/a.txt"}, mode="confiance"),
         Decision.DEMANDER)
verifier("gardien : quitter une app pour libérer de la mémoire passe seul",
         politique("Bash", {"command": """osascript -e 'tell application "Slack" to quit'"""}, mode="confiance"), Decision.OUI)
verifier("question à voix haute : un fichier", decrire(CORBEILLE, {"chemins": ["/Users/x/.ssh/id_ed25519"]}),
         "Je vais mettre à la corbeille id_ed25519. Je peux ?")
verifier("question à voix haute : plusieurs fichiers", decrire(CORBEILLE, {"chemins": ["/a/un.txt", "/a/deux.txt", "/a/trois.txt"]}),
         "Je vais mettre à la corbeille 3 éléments, dont un.txt. Je peux ?")
verifier("question à voix haute : sa mémoire", decrire(OUBLI, {"quoi": "memoire"}), "Je vais effacer ce que je sais de toi. Je peux ?")

# --- 7. Il connaît ses nouveaux outils, et les autorisations se vérifient sans rien ouvrir ---------------------------
consignes = brain._consignes(ecran=False)
noms = {o.name for o in outils.OUTILS}
for outil in ("ecrire_dans_cursor", "mettre_a_la_corbeille", "effacer_memoire", "memoire_du_mac", "verifier_autorisations"):
    verifier(f"{outil} : branché et expliqué dans ses consignes", (outil in noms, outil in consignes), (True, True))
verifier("consignes : plus de « Oui, vas-y. » sur un début de phrase", "Oui, vas-y" in consignes, False)
verifier("consignes : aucun champ oublié", [c for c in ("{prenom}", "{briefing}", "{web}") if c in consignes], [])
verifier("autorisations : seulement des volets connus", set(outils.autorisations_manquantes()) <= set(outils.VOLETS), True)
verifier("tableau de bord : le nom de l'outil en clair", tableau.nommer_outil(CORBEILLE), (config.PRENOM, "à la corbeille"))
script = config.DOSSIER / "outils" / "autorisations.command"
verifier("le guide des autorisations existe et se lance", (script.exists(), os.access(script, os.X_OK)), (True, True))

print(f"\n{'✅' if not echecs else '❌'} mac : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
