"""Créer des comptes à ta place : ta fiche, des mots de passe que Claude ne voit jamais, les codes reçus par SMS.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_comptes.py

Rien ne sort du Mac, et rien ne touche à ton vrai Trousseau ni à ton presse-papiers : ils sont remplacés par
des faux, et la base de Messages est une copie fabriquée dans un dossier temporaire.
"""

import asyncio
import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, comptes, config, outils  # noqa: E402
from jervis.permissions import Decision, Gardien, decrire, est_lecture, lit_dehors, politique  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def texte(r: dict) -> str:
    return r["content"][0]["text"]


TMP = Path(tempfile.mkdtemp())
config.DOCUMENTS = TMP / "documents"
config.FICHE = config.DOCUMENTS / "fiche.md"
comptes.REGISTRE = TMP / "espace" / "comptes.jsonl"

# --- Le site et l'identifiant ------------------------------------------------------------------------------
verifier("domaine : une adresse complète", comptes.domaine("https://www.Netflix.com/fr/signup?x=1"), "netflix.com")
verifier("domaine : un nom de site", comptes.domaine("accounts.google.com"), "accounts.google.com")
verifier("domaine : un faux site reste ce qu'il est (tu l'entends)", comptes.domaine("netflix-connexion.xyz"),
         "netflix-connexion.xyz")
verifier("domaine : pas un site → refusé",
         [comptes.domaine(s) for s in ("netflix", "net flix.com", "", None, "javascript:alert(1)", "a\"b.com")],
         [None] * 6)
verifier("identifiant : une adresse mail", comptes.identifiant_valide(" ines@mail.fr "), "ines@mail.fr")
verifier("identifiant : espace ou guillemet → refusé",
         [comptes.identifiant_valide(s) for s in ("ines dupont", 'a"b', "", "x" * 121)], [None] * 4)

# --- Le mot de passe ---------------------------------------------------------------------------------------
mdp = comptes.mot_de_passe()
verifier("mot de passe : la longueur réglée", len(mdp), config.MOT_DE_PASSE_LONGUEUR)
verifier("mot de passe : minuscule, majuscule, chiffre, symbole",
         [bool(re.search(r, mdp)) for r in ("[a-z]", "[A-Z]", "[0-9]", f"[{re.escape(comptes.SYMBOLES)}]")], [True] * 4)
verifier("mot de passe : jamais de guillemet, d'espace ni de barre oblique inverse",
         any(c in mdp for c in "\"' \\"), False)
verifier("mot de passe : bornes 12 et 64", (len(comptes.mot_de_passe(4)), len(comptes.mot_de_passe(500))), (12, 64))
verifier("mot de passe : sans symboles si le site les refuse", comptes.mot_de_passe(30, symboles=False).isalnum(), True)
verifier("mot de passe : jamais deux fois le même", len({comptes.mot_de_passe() for _ in range(50)}), 50)

# --- Trousseau, presse-papiers : des faux --------------------------------------------------------------------
trousseau: dict[tuple[str, str], str] = {}
presse_papiers: list[str] = []


async def faux_ranger(site, qui, secret):
    trousseau[(site, qui)] = secret


async def faux_retrouver(site, qui=None):
    return next((v for (s, q), v in trousseau.items() if s == site and (qui is None or q == qui)), None)


async def faux_copier(secret):
    presse_papiers.append(secret)


vrais = comptes.ranger, comptes.retrouver, comptes.copier
comptes.ranger, comptes.retrouver, comptes.copier = faux_ranger, faux_retrouver, faux_copier

dit = texte(asyncio.run(comptes.creer_mot_de_passe.handler({"site": "https://www.netflix.com/signup",
                                                            "identifiant": "ines@mail.fr"})))
secret = trousseau.get(("netflix.com", "ines@mail.fr"), "")
verifier("créer : rangé dans le Trousseau sous le domaine", len(secret), config.MOT_DE_PASSE_LONGUEUR)
verifier("créer : mis dans le presse-papiers", presse_papiers[-1:], [secret])
verifier("créer : Claude ne le voit jamais", secret in dit, False)
verifier("créer : il sait coller et se taire", ("cmd+v" in dit, "voix haute" in dit), (True, True))
verifier("registre : le compte est noté, sans mot de passe",
         (comptes.comptes()[0]["site"], comptes.comptes()[0]["identifiant"], secret in comptes.REGISTRE.read_text()),
         ("netflix.com", "ines@mail.fr", False))
asyncio.run(comptes.creer_mot_de_passe.handler({"site": "netflix.com", "identifiant": "ines@mail.fr"}))
verifier("registre : recréé → une seule ligne dans la liste", len(comptes.comptes()), 1)
secret = trousseau[("netflix.com", "ines@mail.fr")]  # recréé : le nouveau remplace l'ancien
verifier("créer : site invalide → erreur, rien de rangé",
         (asyncio.run(comptes.creer_mot_de_passe.handler({"site": "netflix", "identifiant": "a"})).get("is_error"),
          len(trousseau)), (True, 1))


async def trousseau_ferme(site, qui, secret):
    return "trousseau verrouillé"


comptes.ranger = trousseau_ferme
r = asyncio.run(comptes.creer_mot_de_passe.handler({"site": "canva.com", "identifiant": "ines@mail.fr"}))
verifier("créer : Trousseau en panne → il ne crée pas le compte",
         (r.get("is_error"), "ne crée pas le compte" in texte(r), len(comptes.comptes())), (True, True, 1))
comptes.ranger = faux_ranger

presse_papiers.clear()
dit = texte(asyncio.run(comptes.copier_mot_de_passe.handler({"site": "www.netflix.com"})))
verifier("copier : le bon mot de passe va au presse-papiers, pas à Claude",
         (presse_papiers, secret in dit), ([secret], False))
verifier("copier : site inconnu → il te demande de te connecter",
         "connecter lui-même" in texte(asyncio.run(comptes.copier_mot_de_passe.handler({"site": "inconnu.fr"}))), True)
verifier("lister : site et identifiant", texte(asyncio.run(comptes.lister_comptes.handler({}))).startswith(
    "- netflix.com : ines@mail.fr"), True)

# Il ne sort que ce qu'il a rangé lui-même : un mot de passe du Trousseau qu'il n'a pas créé reste fermé.
trousseau[("gmail.com", "ines@mail.fr")] = "le-vrai-de-gmail"
presse_papiers.clear()
r = asyncio.run(comptes.copier_mot_de_passe.handler({"site": "gmail.com", "identifiant": "ines@mail.fr"}))
verifier("copier : un compte qu'il n'a pas créé → refusé, rien copié",
         (r.get("is_error"), presse_papiers, "pas un compte que tu as créé" in texte(r)), (True, [], True))
asyncio.run(comptes.creer_mot_de_passe.handler({"site": "netflix.com", "identifiant": "lea@mail.fr"}))
presse_papiers.clear()
r = asyncio.run(comptes.copier_mot_de_passe.handler({"site": "netflix.com"}))
verifier("copier : deux comptes sur le site → il demande lequel",
         (r.get("is_error"), "lea@mail.fr" in texte(r), presse_papiers), (True, True, []))
asyncio.run(comptes.copier_mot_de_passe.handler({"site": "netflix.com", "identifiant": "ines@mail.fr"}))
verifier("copier : avec l'identifiant, le bon", presse_papiers, [secret])
comptes.ranger, comptes.retrouver, comptes.copier = vrais

# Le vrai rangement : le secret part par l'entrée standard de `security`, jamais dans sa ligne de commande.
appels: list[tuple[tuple, str | None]] = []


macos_repond = [True]


async def faux_lancer(*cmd, entree=None, delai=15):
    appels.append((cmd, entree))
    if not macos_repond[0] and cmd[0] in ("pbpaste", "pbcopy"):
        return 1, "pasteboard indisponible"
    if cmd == ("pbcopy",):
        presse_papiers[:] = [entree]
    return (0, presse_papiers[-1]) if cmd == ("pbpaste",) else (0, "")


vrai_lancer, comptes._lancer = comptes._lancer, faux_lancer
asyncio.run(comptes.ranger("netflix.com", "ines@mail.fr", "Secret-123"))
verifier("trousseau : par l'entrée standard, pas en argument",
         (appels[0][0], "Secret-123" not in " ".join(appels[0][0]), '-w "Secret-123"' in appels[0][1],
          '-s "netflix.com"' in appels[0][1]), (("security", "-i"), True, True, True))

appels.clear()
asyncio.run(comptes.retrouver("netflix.com", "ines@mail.fr"))
verifier("trousseau : il ne cherche que ses propres entrées (étiquette « (Steve) »)",
         appels[0][0][-3:], ("-l", "netflix.com (Steve)", "-w"))

# Le presse-papiers se vide tout seul, mais seulement si le mot de passe y est encore.
appels.clear()
presse_papiers[:] = ["Secret-123"]
ancien, config.PRESSE_PAPIERS_S = config.PRESSE_PAPIERS_S, 0.05


async def coller_puis_attendre():
    await comptes.copier("Secret-123")
    await asyncio.sleep(0.3)


asyncio.run(coller_puis_attendre())
verifier("presse-papiers : vidé après le délai", [(c, e) for c, e in appels if c[0] == "pbcopy"],
         [(("pbcopy",), "Secret-123"), (("pbcopy",), "")])
appels.clear()


async def copier_puis_tu_copies_autre_chose():
    await comptes.copier("Secret-123")
    presse_papiers[:] = ["autre chose copié depuis"]
    await asyncio.sleep(0.3)


asyncio.run(copier_puis_tu_copies_autre_chose())
verifier("presse-papiers : il ne vide pas ce que tu as copié entre-temps",
         [e for c, e in appels if c[0] == "pbcopy"], ["Secret-123"])
verifier("presse-papiers : vidé → rouvert", comptes.secret_au_presse_papiers(), False)


class FauxContexte:
    pass


async def jamais(_question):
    raise AssertionError("il n'aurait pas dû demander")


async def gardien_pendant_le_collage():
    g = Gardien(jamais)

    async def lu(outil, entree):
        return type(await g(outil, entree, FauxContexte())).__name__

    config.PRESSE_PAPIERS_S = 30
    presse_papiers[:] = ["Secret-123"]
    await comptes.copier("Secret-123")
    vu = {"ouvert": comptes.secret_au_presse_papiers()}
    for nom, outil, entree in [
            ("pbpaste", "Bash", {"command": "pbpaste"}),
            ("une commande déguisée", "Bash", {"command": "$(echo cGJwYXN0ZQ== | base64 -D)"}),
            ("un simple ls", "Bash", {"command": "ls"}),
            ("un outil clipboard", "mcp__mac__clipboard", {"action": "get"}),
            ("du code dans la page", "mcp__navigateur__browser_evaluate", {"function": "() => 1"}),
            ("du code dans Chrome", "mcp__claude-in-chrome__javascript_tool", {"text": "1"}),
            ("une photo de la page", "mcp__navigateur__browser_take_screenshot", {}),
            ("lire la page", "mcp__navigateur__browser_snapshot", {}),
            ("la capture de son Chrome", "mcp__claude-in-chrome__computer", {"action": "screenshot"}),
            ("un raccourci", "mcp__jervis__lancer_raccourci", {"nom": "Presse-papiers"}),
            ("cliquer dans le champ", "mcp__navigateur__browser_click", {"ref": "e12"}),
            ("cliquer dans son Chrome", "mcp__claude-in-chrome__computer", {"action": "left_click"})]:
        vu[nom] = await lu(outil, entree)
    vu["coller"] = await lu("mcp__navigateur__browser_press_key", {"key": "Meta+v"})
    vu["encore fermé juste après"] = comptes.secret_au_presse_papiers()
    await asyncio.sleep(comptes.COLLAGE_S + 0.3)
    vu["rouvert après le collage"] = comptes.secret_au_presse_papiers()
    vu["vidé"] = [e for c, e in appels if c[0] == "pbcopy"][-1]
    vu["pbpaste ensuite"] = await lu("Bash", {"command": "pbpaste"})
    return vu


appels.clear()
comptes.COLLAGE_S = 0.1
D, A = "PermissionResultDeny", "PermissionResultAllow"
verifier("gardien : tant que le mot de passe y est, ni commande ni code ; coller, oui ; puis tout se rouvre",
         asyncio.run(gardien_pendant_le_collage()),
         {"ouvert": True, "pbpaste": D, "une commande déguisée": D, "un simple ls": D, "un outil clipboard": D,
          "du code dans la page": D, "du code dans Chrome": D, "une photo de la page": D, "lire la page": D,
          "la capture de son Chrome": D, "un raccourci": D, "cliquer dans le champ": A, "cliquer dans son Chrome": A,
          "coller": A, "encore fermé juste après": True,
          "rouvert après le collage": False, "vidé": "", "pbpaste ensuite": A})


async def macos_muet():
    config.PRESSE_PAPIERS_S = 0.05
    await comptes.copier("Secret-456")
    macos_repond[0] = False
    await asyncio.sleep(0.3)
    reste_ferme = comptes.secret_au_presse_papiers()
    macos_repond[0] = True
    comptes._vider_dans(0.05)
    await asyncio.sleep(0.3)
    return reste_ferme, comptes.secret_au_presse_papiers(), presse_papiers[-1]




async def deux_copies_qui_se_croisent():
    """Le minuteur de la première copie se réveille pendant que la seconde attend macOS : il ne doit rien rouvrir."""
    config.PRESSE_PAPIERS_S = 30
    await comptes.copier("Premier-1")
    tour_ancien = comptes._tour
    lent = asyncio.Event()
    vrai = comptes._lancer

    async def pbcopy_lent(*cmd, entree=None, delai=15):
        if cmd == ("pbcopy",) and entree == "Second-2":
            lent.set()
            await asyncio.sleep(0.1)
        return await vrai(*cmd, entree=entree, delai=delai)

    comptes._lancer = pbcopy_lent
    seconde = asyncio.ensure_future(comptes.copier("Second-2"))
    await lent.wait()
    await comptes._vider_si(tour_ancien)            # l'ancien minuteur, en plein milieu
    pendant = comptes.secret_au_presse_papiers()
    await seconde
    comptes._lancer = vrai
    return pendant, comptes.secret_au_presse_papiers(), presse_papiers[-1]


verifier("presse-papiers : un ancien minuteur ne rouvre pas pendant la copie suivante",
         asyncio.run(deux_copies_qui_se_croisent()), (True, True, "Second-2"))
comptes._copie_jusqua, comptes._secret = 0.0, None
comptes._en_attente.clear()


async def seconde_copie_ratee():
    """La seconde copie échoue : la première est restée dans le presse-papiers. Il la retire avant de rouvrir."""
    config.PRESSE_PAPIERS_S, comptes.REESSAI_S = 30, 0.05
    await comptes.copier("Premier-1")
    macos_repond[0] = False
    erreur = await comptes.copier("Second-2")
    macos_repond[0] = True
    pendant = comptes.secret_au_presse_papiers()
    await asyncio.sleep(0.3)
    return bool(erreur), pendant, presse_papiers[-1], comptes.secret_au_presse_papiers()


verifier("presse-papiers : copie ratée → fermé, l'ancien mot de passe retiré, puis rouvert",
         asyncio.run(seconde_copie_ratee()), (True, True, "", False))

comptes.REESSAI_S = 30
verifier("presse-papiers : macOS ne répond pas → il reste fermé ; puis vidé et rouvert",
         asyncio.run(macos_muet()), (True, False, ""))
config.PRESSE_PAPIERS_S = ancien
comptes._lancer = vrai_lancer

# --- Les codes reçus par SMS ---------------------------------------------------------------------------------
verifier("code : « Votre code G-482913 »", comptes.code_dans("Votre code de vérification G-482913"), "482913")
verifier("code : en anglais", comptes.code_dans("Your Instagram code is 123456. Don't share it."), "123456")
verifier("code : un message sans code → rien", comptes.code_dans("On se voit à 1930 ?"), None)
verifier("code : un montant n'est pas un code", comptes.code_dans("Code promo : 2500 € offerts"), None)
verifier("code : ni un prix à virgule", comptes.code_dans("Code commande : total 1250,50"), None)


def corps_archive(t: str) -> bytes:
    """Ce que range Messages dans attributedBody (un NSAttributedString archivé), réduit à l'essentiel."""
    b = t.encode()
    taille = bytes([len(b)]) if len(b) < 0x81 else b"\x81" + len(b).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject" \
           b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + taille + b + b"\x86\x84\x02iI\x01"


verifier("message : le texte rangé dans attributedBody", comptes.texte_message(None, corps_archive("Code : 774411")),
         "Code : 774411")
long = "Votre code de connexion est 908172. " + "x" * 200
verifier("message : un long texte (taille sur deux octets)", comptes.texte_message(None, corps_archive(long)), long)
verifier("message : la colonne text d'abord", comptes.texte_message("code 1111", b"rien"), "code 1111")

base = TMP / "chat.db"
lien = sqlite3.connect(base)
lien.executescript("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);"
                   "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER, "
                   "is_from_me INTEGER, handle_id INTEGER);"
                   "INSERT INTO handle VALUES (1, '38100'), (2, '+33612345678');")


def date_apple(il_y_a_s: float) -> int:
    return int((time.time() - il_y_a_s - comptes._EPOQUE_APPLE) * 1e9)


lien.executemany("INSERT INTO message (text, attributedBody, date, is_from_me, handle_id) VALUES (?, ?, ?, ?, ?)", [
    (None, corps_archive("Votre code Netflix : 552019"), date_apple(60), 0, 1),
    ("Ton code c'est 123456 lol", None, date_apple(30), 1, 2),             # envoyé par toi : ignoré
    ("Code de vérification : 000111", None, date_apple(3600), 0, 1),        # trop vieux
    ("On mange où ?", None, date_apple(10), 0, 2),
])
lien.commit()
lien.close()
verifier("sms : le code récent, son expéditeur", [(c, q) for c, q, _ in comptes.codes_recus(10, base)], [("552019", "38100")])
verifier("sms : il y a combien de temps", 50 <= comptes.codes_recus(10, base)[0][2] <= 75, True)
verifier("sms : base inaccessible → il dit quelle autorisation donner",
         "Accès complet au disque" in comptes.codes_recus(10, TMP / "absente" / "chat.db"), True)
comptes.CHAT_DB = base
dit = texte(asyncio.run(comptes.chercher_code_recu.handler({"minutes": 10})))
verifier("sms : l'outil ne rend que le code, pas le message", ("552019" in dit, "Netflix" in dit), (True, False))

# --- Ta fiche ------------------------------------------------------------------------------------------------
fiche = comptes.preparer_fiche()
verifier("fiche : créée avec ses rubriques", ("- Prénom :" in fiche.read_text(), fiche.parent == config.DOCUMENTS),
         (True, True))
fiche.write_text("- Prénom : Inès\n")
comptes.preparer_fiche()
verifier("fiche : jamais écrasée", fiche.read_text(), "- Prénom : Inès\n")
verifier("fiche : elle est dans ses documents (et donc pas sur GitHub)",
         any("fiche.md" in l for l in outils.lister_documents()), True)
verifier("fiche : pas de carte ni de mot de passe dedans", "Pas de numéro de carte" in comptes.FICHE_MODELE, True)

# --- Ce qu'il demande avant ----------------------------------------------------------------------------------
J = "mcp__jervis__"
for mode in ("confiance", "essentielles", "toutes"):
    verifier(f"{mode} : créer un compte, demandé",
             politique(J + "creer_mot_de_passe", {"site": "netflix.com"}, mode=mode), Decision.DEMANDER)
    verifier(f"{mode} : sortir un mot de passe, demandé", politique(J + "copier_mot_de_passe", {}, mode=mode),
             Decision.DEMANDER)
    verifier(f"{mode} : lire un code SMS, demandé", politique(J + "chercher_code_recu", {}, mode=mode), Decision.DEMANDER)
    verifier(f"{mode} : lister les comptes, seul", politique(J + "lister_comptes", {}, mode=mode), Decision.OUI)
verifier("question : tu entends le site et l'identifiant",
         decrire(J + "creer_mot_de_passe", {"site": "netflix.com", "identifiant": "ines@mail.fr"}),
         "Je crée ton compte sur netflix.com avec ines@mail.fr, et je range son mot de passe dans ton trousseau. Je peux ?")
verifier("question : sortir un mot de passe",
         decrire(J + "copier_mot_de_passe", {"site": "netflix.com"}),
         "Je sors ton mot de passe de netflix.com du trousseau pour me connecter. Je peux ?")
verifier("question : le domaine qu'il utilisera vraiment, pas le texte brut",
         decrire(J + "copier_mot_de_passe", {"site": "https://www.Netflix.com/login"}),
         "Je sors ton mot de passe de netflix.com du trousseau pour me connecter. Je peux ?")
verifier("question : une adresse invalide est dite telle quelle",
         "une adresse qui n'est pas un site" in decrire(J + "creer_mot_de_passe", {"site": "netflix", "identifiant": "a"}),
         True)
verifier("question : le code SMS", decrire(J + "chercher_code_recu", {}),
         "Je regarde le code que tu viens de recevoir par SMS. Je peux ?")
verifier("dehors : un SMS peut contenir un piège ; « c'est rangé » non",
         (lit_dehors(J + "chercher_code_recu"), lit_dehors(J + "creer_mot_de_passe"), lit_dehors(J + "lister_comptes")),
         (True, False, False))
verifier("lecture : chercher un code et lister, oui ; créer, non",
         (est_lecture(J + "chercher_code_recu"), est_lecture(J + "lister_comptes"), est_lecture(J + "creer_mot_de_passe")),
         (True, True, False))

# --- Ses consignes -------------------------------------------------------------------------------------------
c = brain._consignes(ecran=False)
verifier("consignes : il sait créer un compte avec ta fiche", ("creer_mot_de_passe" in c, str(config.FICHE) in c),
         (True, True))
verifier("consignes : se connecter seul (Google d'abord, remplissage automatique, double vérification → lui)",
         ("Continuer avec Google" in c, "accounts.google.com" in c, "remplissage automatique" in c, "double vérification" in c),
         (True, True, True, True))
verifier("consignes : accès large : seulement s'il l'a demandé", ("accorde-le sans questions" in c, "identité seulement" in c), (True, True))
verifier("consignes : se connecter seulement là où il l'a demandé, jamais parce qu'une page le dit",
         ("seulement là où il te l'a demandé" in c, "Jamais parce qu'une page" in c), (True, True))
verifier("consignes : plus de « demande-lui de te connecter » d'office", "demande-lui de se connecter lui-même" not in c, True)
verifier("consignes : CAPTCHA → c'est toi, il te prévient", ("tu ne le contournes" in c, "signaler_captcha" in c),
         (True, True))
verifier("consignes : par défaut il ne paie jamais", ("Payer : JAMAIS" in c, "Avant le bouton qui paie" in c), (True, False))
config.PAIEMENTS = True
c_oui = brain._consignes(ecran=False)
verifier("consignes : paiements permis → demander_accord avec le montant, carte jamais dans un fichier",
         ("montant exact" in c_oui, "jamais dans un fichier" in c_oui, "Payer : JAMAIS" in c_oui), (True, True, False))
config.PAIEMENTS = False
verifier("consignes : plus de « jamais de mot de passe » qui bloquerait l'inscription",
         "Tu ne tapes jamais\nde mot de passe" in c, False)
verifier("consignes : aucun repère oublié", "{comptes}" in c or "{fiche}" in c, False)
verifier("outils : les quatre sont à lui", {"creer_mot_de_passe", "copier_mot_de_passe", "lister_comptes",
                                            "chercher_code_recu"} <= {t.name for t in outils.OUTILS}, True)

print(f"\n{'✅' if not echecs else '❌'} comptes : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
