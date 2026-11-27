"""Ses processus de fond (« runners ») : lancer, état, journal, arrêter, relance, abandon, et le gardien.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_runners.py

Rien ne touche à ton vrai espace : processus.json et les journaux vont dans un dossier temporaire. Les seuls
programmes lancés pour de vrai sont de petites commandes du shell (sleep, echo), toutes arrêtées à la fin.
"""

import asyncio
import os
import signal
import tempfile
import threading
import time
from pathlib import Path

threading.Timer(90, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, config, fond, outils, permissions, processus  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


def texte(r) -> str:
    return r["content"][0]["text"]


def nom(r) -> str:
    return type(r).__name__


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-runners-")).resolve()
processus.FICHIER = TMP / "processus.json"
processus.DOSSIER = TMP / "processus"
config.ESPACE = TMP
config.CONFIRMATIONS = "autonome"
permissions.AUTORISATIONS = TMP / "autorisations.json"
permissions.BLOCAGES.clear()


def attendre(condition, s: float = 5.0) -> bool:
    fin = time.monotonic() + s
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def tout_arreter() -> None:
    for n, p in processus._lire().items():
        processus._arreter_groupe(int(p.get("pid") or 0), signal.SIGKILL)


# --- Les noms --------------------------------------------------------------------------------------------------
verifier("un nom court, lisible", [processus.nom_valide(n) for n in ("Bot Discord", "scraper_prix!", "", "  ", None,
                                                                    "é" * 3)],
         ["bot-discord", "scraper-prix", None, None, None, None])
verifier("… coupé à 40 caractères", len(processus.nom_valide("a" * 80) or ""), 40)


# --- Lancer, état, journal, arrêter ----------------------------------------------------------------------------
def tester_cycle():
    ok, n = processus.lancer("Mon Bot", "echo bonjour; sleep 30")
    verifier("lancer : il rend le nom propre", (ok, n), (True, "mon-bot"))
    p = processus._lire()["mon-bot"]
    verifier("… il tourne, dans ton espace par défaut", (processus.tourne(p), p["dossier"]), (True, str(TMP)))
    verifier("… sa sortie va dans son journal", attendre(lambda: "bonjour" in processus.fin_du_journal("mon-bot")), True)
    verifier("… le journal montre la commande lancée", "$ echo bonjour; sleep 30" in processus.fin_du_journal("mon-bot"),
             True)
    ok, quoi = processus.lancer("mon-bot", "sleep 5")
    verifier("deux fois le même : refusé tant qu'il tourne", (ok, "tourne déjà" in quoi), (False, True))
    verifier("état : il tourne", "- mon-bot : tourne depuis" in processus.etat(), True)
    verifier("un dossier qui n'existe pas : refusé", processus.lancer("x", "true", str(TMP / "nulle-part"))[0], False)
    verifier("sans commande : refusé", processus.lancer("x", "  ")[0], False)
    verifier("sans nom : refusé", processus.lancer("!!", "true")[0], False)

    vrai_max = config.PROCESSUS_MAX
    config.PROCESSUS_MAX = 1
    ok, quoi = processus.lancer("deuxieme", "sleep 5")
    verifier("au-delà de PROCESSUS_MAX : refusé", (ok, "maximum" in quoi), (False, True))
    config.PROCESSUS_MAX = vrai_max

    pid = int(p["pid"])
    dit = processus.arreter("Mon Bot")
    verifier("arrêter : il le dit, et le groupe entier s'arrête (le sleep aussi)",
             ("est arrêté" in dit, attendre(lambda: not processus._groupe_vivant(pid))), (True, True))
    verifier("… il sort de la liste", "mon-bot" in processus._lire(), False)
    verifier("arrêter un inconnu : il dit ce qui existe", processus.arreter("fantome").startswith("Pas de processus"),
             True)


# --- La veille : fini, relancé, abandonné, oublié --------------------------------------------------------------
def tester_veiller():
    processus.lancer("court", "echo fini")
    p = processus._lire()["court"]
    attendre(lambda: not processus.tourne(p))
    dire = processus.veiller()
    verifier("un programme sans « relancer » qui finit : une phrase écrite par le code, sans sa sortie",
             (len(dire), dire[0].startswith("Le processus « court » a fini, après"), "fini\n" in dire[0]),
             (1, True, False))
    verifier("… une seule fois", processus.veiller(), [])
    verifier("… l'état dit qu'il a fini", "- court : fini" in processus.etat(), True)
    verifier("… oublié au bout d'un jour", (processus.veiller(time.time() + processus.GARDER_FINIS_S + 60),
                                             "court" in processus._lire()), ([], False))

    processus.lancer("plante", "echo boum; exit 1", relancer=True)
    for i in range(config.PROCESSUS_RELANCES_MAX):
        p = processus._lire()["plante"]
        attendre(lambda: not processus.tourne(p))
        verifier(f"relancer : il repart après sa chute n° {i + 1}, sans rien te dire", processus.veiller(), [])
    p = processus._lire()["plante"]
    verifier("… relancé PROCESSUS_RELANCES_MAX fois en une heure", len(p["relances"]), config.PROCESSUS_RELANCES_MAX)
    attendre(lambda: not processus.tourne(p))
    dire = processus.veiller()
    verifier("… au-delà, il le laisse arrêté et te prévient", (len(dire), "s'arrête en boucle" in dire[0]), (1, True))
    verifier("… et ne le relance plus", (processus.veiller(), processus.tourne(processus._lire()["plante"])), ([], False))
    verifier("… l'état le dit", "plante : arrêté : il plantait en boucle" in processus.etat(), True)
    verifier("… son journal garde chaque essai", processus.fin_du_journal("plante").splitlines().count("boum"),
             config.PROCESSUS_RELANCES_MAX + 1)

    # les relances d'il y a plus d'une heure ne comptent plus
    processus.lancer("tenace", "exit 0", relancer=True)
    tous = processus._lire()
    tous["tenace"]["relances"] = [time.time() - 7200] * 10
    processus._ecrire(tous)
    attendre(lambda: not processus.tourne(processus._lire()["tenace"]))
    verifier("des chutes d'il y a plus d'une heure ne comptent plus", (processus.veiller(),
                                                                        len(processus._lire()["tenace"]["relances"])),
             ([], 1))
    processus.arreter("tenace")
    processus.arreter("plante")

    # le journal trop gros : il garde la fin à la relance
    vrai = processus.JOURNAL_MAX
    processus.JOURNAL_MAX = 1000
    processus.journal_de("gros").write_text("a" * 5000, encoding="utf-8")
    processus.lancer("gros", "true")
    verifier("un journal trop gros : il ne garde que la fin", processus.journal_de("gros").stat().st_size < 1000, True)
    processus.JOURNAL_MAX = vrai
    processus.arreter("gros")


# --- L'outil -----------------------------------------------------------------------------------------------------
async def tester_outil():
    o = processus.outil_processus.handler
    r = await o({"action": "lancer", "nom": "faute", "commande": "commande-qui-n-existe-pas-xyz"})
    verifier("une commande qui meurt tout de suite : il le sait, avec la fin de la sortie",
             (r.get("is_error"), "s'est arrêté tout de suite" in texte(r), "not found" in texte(r)), (True, True, True))
    r = await o({"action": "lancer", "nom": "veilleur", "commande": "while true; do echo tic; sleep 1; done",
                 "relancer": True})
    verifier("lancer : il tourne, et il sait où va sa sortie", ("« veilleur » tourne" in texte(r),
                                                                 "sera relancé" in texte(r), r.get("is_error")),
             (True, True, None))
    r = await o({"action": "journal", "nom": "veilleur", "lignes": 5})
    verifier("journal : la fin de sa sortie", "tic" in texte(r), True)
    r = await o({"action": "journal", "nom": "inconnu"})
    verifier("journal d'un inconnu : une erreur", r.get("is_error"), True)
    r = await o({"action": "etat"})
    verifier("état : tous", ("veilleur : tourne" in texte(r), "faute" in texte(r)), (True, True))
    r = await o({"action": "arreter", "nom": "veilleur"})
    verifier("arrêter", "est arrêté" in texte(r), True)
    r = await o({"action": "danser"})
    verifier("une action inconnue : une erreur", r.get("is_error"), True)
    processus.arreter("faute")


# --- Le gardien ----------------------------------------------------------------------------------------------------
async def tester_gardien():
    questions = []
    reponse = {"oui": False}

    async def repondre(q):
        questions.append(q)
        return reponse["oui"]
    cage_dispo = Path(permissions.CAGE_EXEC).exists()

    def neuf(dehors: bool = False):
        g = permissions.Gardien(repondre)
        g.nouvelle_demande()
        if dehors:
            g.a_lu_dehors()
        return g
    outil = "mcp__jervis__processus"

    r = await neuf()(outil, {"action": "etat"}, None)
    verifier("état : libre", nom(r), "PermissionResultAllow")
    g = neuf()
    r = await g(outil, {"action": "journal", "nom": "bot"}, None)
    verifier("journal : libre, mais sa sortie est un texte d'ailleurs", (nom(r), g.contenu_externe), (
        "PermissionResultAllow", True))
    r = await neuf()(outil, {"action": "lancer", "nom": "bot", "commande": "python3 bot.py", "relancer": True}, None)
    verifier("lancer à ta demande (autonome) : sans question, hors cage",
             (nom(r), r.updated_input["commande"], questions), ("PermissionResultAllow", "python3 bot.py", []))
    r = await neuf()(outil, {"action": "lancer", "nom": "bot", "commande": "sudo python3 bot.py"}, None)
    verifier("… une commande interdite reste interdite", nom(r), "PermissionResultDeny")
    r = await neuf()(outil, {"action": "arreter", "nom": "bot"}, None)
    verifier("arrêter à ta demande : sans question", (nom(r), questions), ("PermissionResultAllow", []))

    r = await neuf(dehors=True)(outil, {"action": "lancer", "nom": "Bot", "commande": "python3   bot.py",
                                        "relancer": True}, None)
    verifier("après un texte venu d'ailleurs, un programme qui se relance : il te demande, la commande sous les yeux",
             (nom(r), questions), ("PermissionResultDeny",
                                   ["Je vais lancer « bot » en continu, relancé s'il s'arrête : « python3 bot.py ». "
                                    "Je peux ?"]))
    verifier("… un refus : il n'insiste pas", "refusé" in r.message, True)
    verifier("… la question ne devient pas une règle 📌", permissions.BLOCAGES[-1]["autorisable"], False)
    r = await neuf(dehors=True)(outil, {"action": "lancer", "nom": "bot", "commande": "echo « x »", "relancer": True},
                                None)
    verifier("… les guillemets de la commande ne cassent pas la question", questions[-1],
             "Je vais lancer « bot » en continu, relancé s'il s'arrête : « echo \" x \" ». Je peux ?")
    r = await neuf(dehors=True)(outil, {"action": "lancer", "nom": "bot", "commande": "x" * 400, "relancer": True},
                                None)
    verifier("… une commande trop longue pour être montrée : refusée sans question", (nom(r), len(questions)),
             ("PermissionResultDeny", 2))

    reponse["oui"] = True
    r = await neuf(dehors=True)(outil, {"action": "lancer", "nom": "bot", "commande": "python3 bot.py",
                                        "relancer": True}, None)
    verifier("… tu dis oui : il le lance, dans la cage", (nom(r), r.updated_input["commande"] != "python3 bot.py")
             if cage_dispo else nom(r), ("PermissionResultAllow", True) if cage_dispo else "PermissionResultDeny")
    if cage_dispo:
        verifier("… la commande qui tournera est celle encagée", permissions.CAGE_EXEC in
            r.updated_input["commande"], True)
    avant = len(questions)
    r = await neuf(dehors=True)(outil, {"action": "lancer", "nom": "une-fois", "commande": "python3 calcul.py"}, None)
    verifier("après un texte venu d'ailleurs, un programme qui ne se relance pas : pas de question en plus, la cage",
             (len(questions) - avant, permissions.CAGE_EXEC in r.updated_input.get("commande", ""))
             if cage_dispo else nom(r), (0, True) if cage_dispo else "PermissionResultDeny")
    r = await neuf(dehors=True)(outil, {"action": "arreter", "nom": "bot"}, None)
    verifier("… en arrêter un : il te demande", questions[-1], "Je vais arrêter le processus de fond « bot ». Je peux ?")
    reponse["oui"] = False

    vrai = config.MEFIANCE
    config.MEFIANCE = False
    avant = len(questions)
    r = await neuf(dehors=True)(outil, {"action": "arreter", "nom": "bot"}, None)
    verifier("sans MEFIANCE : pas de question", (nom(r), len(questions) - avant), ("PermissionResultAllow", 0))
    config.MEFIANCE = vrai


# --- Le programmateur l'annonce --------------------------------------------------------------------------------
async def tester_annonce():
    postes, notes = [], []

    class Faux:
        class _standard:
            @staticmethod
            def poster(e):
                postes.append((e.genre, e.texte))

    async def noter(titre, t):
        notes.append(t)
    vraie = fond.notifier
    fond.notifier = noter
    processus.lancer("annonce", "true")
    attendre(lambda: not processus.tourne(processus._lire()["annonce"]))
    await fond.Programmateur._processus(Faux())
    fond.notifier = vraie
    verifier("le programmateur te le dit (annonce et notification)",
             ([g for g, _ in postes], len(notes), "« annonce » a fini" in (notes or [""])[0]), (["annonce"], 1, True))


tester_cycle()
tester_veiller()
asyncio.run(tester_outil())
asyncio.run(tester_gardien())
asyncio.run(tester_annonce())
tout_arreter()

# --- Branchement et consignes ----------------------------------------------------------------------------------
verifier("outil branché", "processus" in {t.name for t in outils.OUTILS}, True)
c = brain._consignes(ecran=False)
verifier("consignes : il connaît ses processus, et n'utilise pas nohup", ("processus" in c, "nohup" in c), (True, True))

print(f"\n{'✅' if not echecs else '❌'} runners : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
