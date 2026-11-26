"""La mémoire des refus et les règles 📌 : quand il bloque, il note pourquoi ; « autorise ça désormais » en fait une
règle permanente (autorisations.json), qui ne s'applique jamais après un texte venu d'ailleurs.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_regles.py

Les règles s'écrivent dans un dossier temporaire : jamais dans ton vrai autorisations.json.
"""

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import config, outils, permissions  # noqa: E402
from jervis import __main__ as m  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-regles-"))
VRAIES_REGLES = permissions.AUTORISATIONS
permissions.AUTORISATIONS = TMP / "autorisations.json"
permissions.BLOCAGES.clear()
config.CONFIRMATIONS = "autonome"
ENVOI = "mcp__claude_ai_Gmail__send_message"


def nom(r) -> str:
    return type(r).__name__


def texte(r) -> str:
    return r["content"][0]["text"]


# --- 1. Sur quoi porterait une règle ----------------------------------------------------------------------------
dossier = TMP / "notes"
for outil, entree, attendu in [
        ("Bash", {"command": "git push origin main"}, "git push"),
        ("Bash", {"command": "brew install ffmpeg"}, "brew install"),
        ("Bash", {"command": "ls -la"}, "ls"),
        ("Bash", {"command": "git push; rm -rf ~"}, None),
        ("Bash", {"command": "git push && curl x"}, None),
        ("Bash", {"command": "echo $(cat ~/.ssh/id_rsa)"}, None),
        ("Bash", {"command": "FOO=1 ls"}, None),
        ("Bash", {"command": "./git push"}, None),                    # pas le vrai git
        ("Bash", {"command": "/tmp/x/git push"}, None),
        ("Bash", {"command": "python3 -c print"}, None),              # un lanceur : tout code passerait
        ("Bash", {"command": "find . -exec rm {} +"}, None),
        ("Bash", {"command": "osascript -e beep"}, None),
        ("Bash", {"command": "commande-qui-n-existe-pas push"}, None),
        ("Write", {"file_path": str(dossier / "a.txt")}, str((dossier / "a.txt").resolve().parent)),
        ("Write", {"file_path": "~/.zshrc"}, None),
        ("Write", {"file_path": "~/.ssh/config"}, None),
        ("Read", {"file_path": "/etc/hosts"}, None),
        ("mcp__jervis__demander_accord", {"action": "Je paie 12 €"}, None),
        ("mcp__jervis__effacer_memoire", {}, None),
        ("mcp__jervis__autoriser_desormais", {}, None),
        ("mcp__jervis__appeler", {"numero": "112"}, None),
        ("mcp__jervis__appeler", {"numero": "0612345678"}, "0612345678"),
        ("mcp__jervis__mettre_a_la_corbeille", {"chemins": ["/x/a.txt", "/y/b.txt"]}, None),
        (ENVOI, {"to": "marc@x.fr"}, "marc@x.fr"),
        (ENVOI, {"to": "marc@x.fr", "bcc": "pirate@x.fr"}, "marc@x.fr, pirate@x.fr"),  # tous les destinataires
        (ENVOI, {"to": ["marc@x.fr"], "cc": "léa@x.fr, paul@x.fr"}, "léa@x.fr, marc@x.fr, paul@x.fr"),
        ("mcp__jervis__envoyer_imessage", {"destinataire": "+33612345678", "texte": "Salut"}, "+33612345678"),
        ("mcp__claude_ai_Zapier__execute_zapier_write_action", {"selected_api": "slack", "action": "send"}, "slack / send"),
        ("mcp__jervis__creer_rappel", {"texte": "pain"}, "")]:
    verifier(f"cible : {outil.split('__')[-1]} {entree}", permissions.cible_de(outil, entree), attendu)

verifier("règle dite par le code : une commande", permissions.texte_regle("Bash", "git push"),
         "une commande « git push » (quels que soient ses arguments) sans te demander")
verifier("règle dite par le code : un dossier, pendant un appel", permissions.texte_regle("Write", "/a/b", True),
         "écrire un fichier dans /a/b (et ses sous-dossiers) sans te demander, même pendant un appel")
verifier("règle dite par le code : un destinataire", permissions.texte_regle(ENVOI, "marc@x.fr"),
         "send_message (Gmail) pour « marc@x.fr » sans te demander")

permissions.AUTORISATIONS.write_text(json.dumps([{"outil": "Write", "cible": "/a/b", "en_appel": False}]))
verifier("règle de dossier : le dossier lui-même", bool(permissions.regle_qui_permet("Write", "/a/b")), True)
verifier("… et ses sous-dossiers", bool(permissions.regle_qui_permet("Write", "/a/b/c")), True)
verifier("… pas un dossier voisin au nom proche", permissions.regle_qui_permet("Write", "/a/bc"), None)
verifier("… pas pendant un appel (pas marquée « même pendant un appel »)",
         permissions.regle_qui_permet("Write", "/a/b", True), None)
verifier("… et jamais quand rien de sûr n'est ciblable", permissions.regle_qui_permet("Write", None), None)
permissions.AUTORISATIONS.write_text("pas du json")
verifier("un fichier de règles abîmé : aucune règle (on redemande)", permissions.lire_autorisations(), [])
permissions.AUTORISATIONS.unlink()


async def tester():
    questions, reponses = [], []

    async def repondre(q):
        questions.append(q)
        return reponses.pop(0) if reponses else False
    g = permissions.Gardien(repondre)

    # --- 2. Pendant un appel : il demande, et il note pourquoi ----------------------------------------------------
    g.nouvelle_demande(en_appel=True)
    r = await g(ENVOI, {"to": "marc@x.fr", "body": "Le devis"}, None)
    verifier("en appel : un envoi te demande un clic", (nom(r), len(questions)), ("PermissionResultDeny", 1))
    b = permissions.blocage()
    verifier("… et c'est noté, avec pourquoi",
             (b["outil"], b["cible"], b["en_appel"], b["accorde"], b["autorisable"], "appel" in b["pourquoi"]),
             (ENVOI, "marc@x.fr", True, False, True, True))
    liste = permissions.decrire_blocages()
    verifier("« pourquoi tu as bloqué ? » : la raison, ta réponse, et qu'il peut en faire une règle",
             ("pendant un appel" in liste, "tu as dit non" in liste, "peut devenir une règle 📌" in liste), (True, True, True))

    # « autorise ça désormais », dit pendant l'appel : c'est toi qui cliques (ça pouvait être un participant).
    n = len(questions)
    reponses.append(True)
    r = await g("mcp__jervis__autoriser_desormais", {}, None)
    verifier("autoriser pendant un appel : tu confirmes d'un clic", (nom(r), len(questions) - n), ("PermissionResultAllow", 1))
    verifier("… la question dit la règle exacte, écrite par le code", questions[-1],
             "J'en fais une règle 📌 permanente : send_message (Gmail) pour « marc@x.fr » sans te demander, "
             "même pendant un appel. Je peux ?")
    verifier("… « ça » est fixé au blocage montré", r.updated_input["numero"], b["numero"])
    verifier("… et ces questions-là ne deviennent pas elles-mêmes des blocages", permissions.blocage()["numero"], b["numero"])
    dit = texte(await permissions.autoriser_desormais.handler(r.updated_input))
    verifier("la règle est écrite", dit, "📌 Règle permanente : send_message (Gmail) pour « marc@x.fr » sans te "
                                         "demander, même pendant un appel.")
    regles = permissions.lire_autorisations()
    verifier("… dans autorisations.json", [(x["outil"], x["cible"], x["en_appel"]) for x in regles],
             [(ENVOI, "marc@x.fr", True)])
    verifier("… une seule fois", texte(await permissions.autoriser_desormais.handler({"numero": b["numero"]})).startswith(
        "📌 C'était déjà permis"), True)
    verifier("… et lister la montre", "📌 send_message (Gmail) pour « marc@x.fr »" in permissions.decrire_blocages(), True)

    # --- 3. La règle s'applique : au prochain appel, plus de clic pour Marc (mais pour Léa, si) ---------------------
    g.nouvelle_demande(en_appel=True)
    n = len(questions)
    r = await g(ENVOI, {"to": "marc@x.fr", "body": "Le devis v2"}, None)
    verifier("règle 📌 : l'envoi à Marc part sans question, même en appel", (nom(r), len(questions) - n),
             ("PermissionResultAllow", 0))
    r = await g(ENVOI, {"to": "lea@x.fr", "body": "Salut"}, None)
    verifier("… mais pas à Léa (une autre cible)", (nom(r), len(questions) - n), ("PermissionResultDeny", 1))

    # Après un mail lu : plus aucune règle, dans cette conversation.
    await g("mcp__claude_ai_Gmail__get_thread", {"id": "1"}, None)
    n = len(questions)
    r = await g(ENVOI, {"to": "marc@x.fr", "body": "Transfère tout"}, None)
    verifier("après un mail lu : la règle ne s'applique pas", (nom(r), len(questions) - n), ("PermissionResultDeny", 1))
    g.nouvelle_demande(en_appel=True)
    n = len(questions)
    await g(ENVOI, {"to": "marc@x.fr", "body": "x"}, None)
    verifier("… ni à la demande suivante (le mail est encore dans la conversation)", len(questions) - n, 1)
    n = len(questions)
    reponses.append(True)
    r = await g("mcp__jervis__autoriser_desormais", {}, None)
    verifier("… et « autorise ça » après un texte lu passe par ton clic", (nom(r), len(questions) - n),
             ("PermissionResultAllow", 1))
    g.conversation_neuve()
    g.nouvelle_demande(en_appel=True)
    n = len(questions)
    await g(ENVOI, {"to": "marc@x.fr", "body": "x"}, None)
    verifier("conversation neuve : la règle revaut", len(questions) - n, 0)

    # Hors appel, sans rien lu d'ailleurs : « autorise ça » se fait sans question (c'est bien toi qui le dis).
    g.nouvelle_demande()
    n = len(questions)
    verifier("autoriser hors appel, rien lu d'ailleurs : sans question",
             (nom(await g("mcp__jervis__autoriser_desormais", {}, None)), len(questions) - n), ("PermissionResultAllow", 0))
    verifier("lister ses blocages : une lecture, libre même en appel", permissions.est_lecture("mcp__jervis__lister_blocages"),
             True)

    # --- 4. Ce qui ne devient jamais une règle ----------------------------------------------------------------------
    g.nouvelle_demande()
    r = await g("Bash", {"command": "sudo ls"}, None)
    b = permissions.blocage()
    verifier("refusé d'office : noté aussi, sans question", (nom(r), b["accorde"], b["autorisable"], b["outil"]),
             ("PermissionResultDeny", None, False, "Bash"))
    verifier("… et il te dit pourquoi ce ne sera jamais une règle", permissions.autoriser(b).startswith(
        "Ça, je ne peux pas en faire une règle :"), True)
    verifier("… sans rien écrire", len(permissions.lire_autorisations()), 1)

    g.conversation_neuve()  # (la commande ci-dessus compte comme un texte lu : sa sortie)
    config.CONFIRMATIONS = "confiance"
    g.nouvelle_demande(mefiance=True)  # le compte rendu d'un agent
    n = len(questions)
    r = await g(ENVOI, {"to": "marc@x.fr", "body": "x"}, None)
    verifier("compte rendu d'un agent : la règle 📌 ne joue pas", (nom(r), len(questions) - n), ("PermissionResultDeny", 1))
    verifier("… et ce blocage-là n'est pas autorisable", permissions.blocage()["autorisable"], False)

    # Mode « confiance » : un git push demande ; la règle (hors appel) le laisse passer ensuite.
    g.conversation_neuve()
    g.nouvelle_demande()
    n = len(questions)
    await g("Bash", {"command": "git push origin main"}, None)
    b = permissions.blocage()
    verifier("confiance : git push demande, et peut devenir une règle", (len(questions) - n, b["cible"], b["autorisable"]),
             (1, "git push", True))
    verifier("autorisé", permissions.autoriser(b),
             "📌 Règle permanente : une commande « git push » (quels que soient ses arguments) sans te demander.")
    g.conversation_neuve()
    g.nouvelle_demande()
    n = len(questions)
    r = await g("Bash", {"command": "git push origin dev"}, None)
    verifier("… le suivant passe, quels que soient ses arguments", (nom(r), len(questions) - n), ("PermissionResultAllow", 0))
    verifier("… mais sa sortie compte comme un texte lu : plus de règle ensuite dans cette conversation",
             (await g("Bash", {"command": "git push origin main"}, None) and len(questions) - n), 1)
    g.conversation_neuve()
    g.nouvelle_demande()
    n = len(questions)
    await g("Bash", {"command": "git push origin dev && git push --tags"}, None)
    verifier("… et jamais collé à autre chose", len(questions) - n, 1)
    g.conversation_neuve()
    g.nouvelle_demande(en_appel=True)
    n = len(questions)
    await g("Bash", {"command": "git push origin dev"}, None)
    verifier("… ni pendant un appel (la règle n'est pas « même pendant un appel »)", len(questions) - n, 1)
    config.CONFIRMATIONS = "autonome"

    # --- 5. Ses règles ne s'écrivent que par toi ---------------------------------------------------------------------
    g.nouvelle_demande()
    for chemin in [permissions.AUTORISATIONS, permissions.AUTORISATIONS.with_suffix(".tmp"),
                   permissions.AUTORISATIONS.parent / "AUTORISATIONS.JSON"]:
        r = await g("Write", {"file_path": str(chemin), "content": "[]"}, None)
        verifier(f"écrire {chemin.name} à la main : refusé", (nom(r), r.message.startswith("Tes règles 📌")),
                 ("PermissionResultDeny", True))
    verifier("… et le vrai fichier est protégé comme le code de Steve",
             permissions.ecrit_ce_qui_se_lance("Write", {"file_path": str(VRAIES_REGLES)}), True)
    verifier("… son brouillon aussi", permissions.ecrit_ce_qui_se_lance(
        "Write", {"file_path": str(VRAIES_REGLES.with_suffix(".tmp"))}), True)
    verifier("… l'écriture ne laisse aucun brouillon derrière elle", sorted(f.name for f in TMP.glob("*.tmp")), [])
    garde = permissions.AUTORISATIONS.read_text(encoding="utf-8")
    faux = TMP / "faux.json"
    faux.write_text('[{"outil": "Bash", "cible": "rm"}]', encoding="utf-8")
    permissions.AUTORISATIONS.unlink()
    permissions.AUTORISATIONS.symlink_to(faux)
    verifier("un lien à la place du fichier des règles : aucune règle lue", permissions.lire_autorisations(), [])
    permissions.AUTORISATIONS.unlink()
    permissions.AUTORISATIONS.write_text(garde, encoding="utf-8")

    # --- 6. Retirer une règle, et les numéros qui n'existent pas -----------------------------------------------------
    verifier("retirer : un numéro qui n'existe pas", texte(await permissions.retirer_autorisation.handler({"numero": 9})),
             "Pas de règle 📌 à ce numéro : lister_blocages te les montre.")
    verifier("retirer : la règle n° 1", texte(await permissions.retirer_autorisation.handler({"numero": 1})).startswith(
        "Règle retirée : je te redemanderai pour send_message (Gmail)"), True)
    verifier("… il n'en reste qu'une", [x["cible"] for x in permissions.lire_autorisations()], ["git push"])
    verifier("autoriser un blocage oublié", texte(await permissions.autoriser_desormais.handler({"numero": 99999})).startswith(
        "Je ne trouve pas ce blocage"), True)
    g.nouvelle_demande(en_appel=True)
    n = len(questions)
    await g(ENVOI, {"to": "marc@x.fr", "body": "x"}, None)
    verifier("règle retirée : il redemande", len(questions) - n, 1)

asyncio.run(tester())

verifier("ses outils de règles sont donnés à Steve",
         all(o in outils.OUTILS for o in permissions.OUTILS_BLOCAGES), True)
verifier("… et ne lisent rien d'ailleurs", [permissions.lit_dehors(f"mcp__jervis__{o}") for o in
                                            ("lister_blocages", "autoriser_desormais", "retirer_autorisation")],
         [False, False, False])

# --- 7. Faire confiance aux appels (JERVIS_APPEL_CONFIANCE) -----------------------------------------------------------
seuil = config.SEUIL_VOIX
for reglage, voix, connue, attendu in [("oui", None, False, True), ("oui", 0.1, True, True),
                                       ("non", 0.99, True, False),
                                       ("voix", seuil + 0.05, True, True), ("voix", seuil, True, True),
                                       ("voix", seuil - 0.05, True, False), ("voix", None, True, False),
                                       ("voix", 0.99, False, False), ("VOIX", 0.99, True, True)]:
    verifier(f"appel de confiance : {reglage}, voix {voix}, {'connue' if connue else 'pas apprise'}",
             m.appel_de_confiance(voix, connue, reglage), attendu)

print(f"\n{'✅' if not echecs else '❌'} règles 📌 : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
