"""Le tableau de bord : ce qu'il lit dans jervis.log, son usage de Claude, et les verrous de son petit serveur.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_tableau.py

Sans micro ni Claude : un faux état, un journal et des dépenses écrits dans un dossier temporaire.
"""

import datetime as dt
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import types
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import compteur, config, tableau  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


dossier = Path(tempfile.mkdtemp(prefix="jervis-tableau-"))
P = config.PRENOM

# --- 1. Le nom des outils, dit en français ------------------------------------------------------------------------
OUTILS = [
    ("mcp__claude_ai_Gmail__trash_thread", ("Gmail", "mail à la corbeille")),
    ("mcp__claude_ai_Google_Calendar__list_events", ("Agenda", "lecture de l'agenda")),
    ("mcp__jervis__tableau_de_bord", (P, "tableau de bord")),               # action inconnue : son nom, sans « _ »
    ("mcp__claude-in-chrome__navigate", ("Chrome", "ouvre une page")),
    ("mcp__mon_serveur__faire-un_truc", ("Mon serveur", "faire un truc")),  # serveur inconnu : son nom, lisible
    ("Bash", ("Bash", "")),                                                 # pas un outil MCP
]
for brut, attendu in OUTILS:
    verifier(f"outil {brut}", tableau.nommer_outil(brut), attendu)

# --- 2. Les temps de réponse ---------------------------------------------------------------------------------------
verifier("chrono simple", tableau.lire_chrono("5,3", "fin de phrase 0,4 · texte 0,2 · Claude 1,8 · voix 2,9"),
         {"total": 5.3, "etapes": [["fin de phrase", 0.4], ["texte", 0.2], ["Claude", 1.8], ["voix", 2.9]], "note": ""})
verifier("chrono avec « Voyons… » : la vraie réponse est une note, pas une étape",
         tableau.lire_chrono("1,6", "fin de phrase 0,4 · texte 0,2 · « Voyons… » à 1,0 · vraie réponse à 4,2"),
         {"total": 1.6, "etapes": [["fin de phrase", 0.4], ["texte", 0.2], ["« Voyons… »", 1.0]],
          "note": "vraie réponse à 4,2"})
verifier("chrono : une étape sans durée est laissée de côté", tableau.lire_chrono("2,0", "texte 0,2 · Claude ?"),
         {"total": 2.0, "etapes": [["texte", 0.2]], "note": ""})
verifier("chrono illisible", tableau.lire_chrono("abc", "texte 0,2"), None)

# --- 3. Ce qu'il lit dans le journal -------------------------------------------------------------------------------
JOURNAL = f"""🤖 {P} : {P} en ligne.
🤖 {P} : {P} est en ligne.
   \x1b[2m(entendu sans « {P} » : C'est quoi ton numéro ?)\x1b[0m
🧑 Toi : {P}, vide ma boîte mail.
   ⚙️  mcp__claude_ai_Gmail__search_threads
   ⚙️  mcp__claude_ai_Gmail__trash_thread
   ⚙️  mcp__claude_ai_Gmail__trash_thread
   ⚙️  mcp__claude_ai_Gmail__trash_thread
🤖 {P} : C'est fait.
🤖 {P} : Douze mails à la corbeille.
   ⏱ 5,3 s  (fin de phrase 0,4 · texte 0,2 · Claude 1,8 · voix 2,9)
🧑 Toi (suite) : Et mon agenda ?
   ⚙️  Bash
🤖 {P} : Rien aujourd'hui.
   ⏱ 2,1 s  (fin de phrase 0,3 · texte 0,2 · Claude 0,9 · voix 0,7)
"""
lu = tableau.lire_journal(JOURNAL)
verifier("journal : les tours (annonce de démarrage et phrases sans son nom écartées, phrases d'une réponse réunies)",
         lu["echanges"],
         [{"qui": "toi", "texte": f"{P}, vide ma boîte mail."},
          {"qui": "steve", "texte": "C'est fait. Douze mails à la corbeille."},
          {"qui": "toi", "texte": "Et mon agenda ?"},
          {"qui": "steve", "texte": "Rien aujourd'hui."}])
verifier("journal : ses actions, les répétitions comptées", lu["outils"],
         [{"nom": "Gmail", "action": "recherche de mails", "fois": 1},
          {"nom": "Gmail", "action": "mail à la corbeille", "fois": 3},
          {"nom": "Bash", "action": "", "fois": 1}])
verifier("journal : ses temps de réponse", [c["total"] for c in lu["chronos"]], [5.3, 2.1])
long = tableau.lire_journal("".join(f"🧑 Toi : question {i}\n🤖 {P} : réponse {i}\n   ⏱ 1,0 s  (texte 1,0)\n"
                                    for i in range(40)))
verifier("journal : seulement la fin (20 tours, 24 chronos)", (len(long["echanges"]), len(long["chronos"]),
                                                                long["echanges"][-1]["texte"]), (20, 24, "réponse 39"))
ecrit = tableau.lire_journal(f"🧑 Toi (écrit) : salut\n🤖 {P} (écrit) : **Bonjour** ⏎ - un\n"
                             f"🧑 Toi (iPhone) : et là ?\n🤖 {P} (iPhone) : Ici.\n")
verifier("journal : le chat et l'iPhone, ses réponses avec leurs retours à la ligne", ecrit["echanges"],
         [{"qui": "toi", "texte": "salut", "ecrit": True, "canal": "chat"},
          {"qui": "steve", "texte": "**Bonjour**\n- un", "ecrit": True, "canal": "chat"},
          {"qui": "toi", "texte": "et là ?", "ecrit": True, "canal": "iPhone"},
          {"qui": "steve", "texte": "Ici.", "ecrit": True, "canal": "iPhone"}])
verifier("journal vide", tableau.lire_journal(""), {"echanges": [], "outils": [], "commandes": [], "chronos": []})

# --- 3 bis. Ses dernières commandes du terminal, sans jamais un secret ----------------------------------------------
verifier("commande : le texte et ce qu'elle voulait faire",
         tableau.lire_commande("{'command': 'df  -h /', 'description': 'Lire le disque'}"),
         {"commande": "df -h /", "but": "Lire le disque"})
verifier("commande : sans description", tableau.lire_commande("{'command': 'ls'}"), {"commande": "ls", "but": ""})
verifier("commande : une ligne coupée ou étrange est ignorée",
         [tableau.lire_commande(x) for x in ("{'command': 'ls", "", "{'file_path': 'x'}", "[1]", "{'command': ''}")], [None] * 5)
longue = tableau.lire_commande("{'command': '" + "a" * 400 + "'}")["commande"]
verifier("commande : très longue, elle est coupée", (len(longue) == tableau.COMMANDE_MAX, longue.endswith("…")), (True, True))
for brut, attendu in [
    ("curl -H 'Authorization: Bearer abc123def' https://x.fr", "abc123def"),
    ("export API_KEY=sk-abcdefghijk1234 && run", "abcdefghijk1234"),
    ("git push https://ghp_abcdefghijklmnop1234@github.com/x", "abcdefghijklmnop1234"),
    ("mysql --password=hunter2 base", "hunter2"),
    ("curl -u moi:motdepasse https://x.fr", "motdepasse"),
    ("TOKEN=\"deux mots\" lancer", "deux mots"),
]:
    verifier(f"commande : le secret est masqué ({brut[:28]}…)", attendu in tableau.masquer_secrets(brut), False)
verifier("commande : une commande ordinaire reste intacte", tableau.masquer_secrets("git log --oneline | head -5 && ls -u"),
         "git log --oneline | head -5 && ls -u")
cmd = tableau.lire_journal(f"""   ⚙️  Bash {{'command': 'pmset -g batt', 'description': 'Batterie'}}
   ⚙️  Read {{'file_path': '/x'}}
   ⚙️  Bash {{'command': 'git status', 'description': 'Voir les changements'}}
   ⚙️  mcp__mac__app {{'command': 'pas du terminal'}}
""")
verifier("journal : seulement les commandes du terminal, dans l'ordre", [c["commande"] for c in cmd["commandes"]],
         ["pmset -g batt", "git status"])
verifier("journal : les six dernières commandes",
         len(tableau.lire_journal("".join(f"   ⚙️  Bash {{'command': 'ls {i}'}}\n" for i in range(20)))["commandes"]), 6)

gros = dossier / "gros.log"
gros.write_text("x" * tableau.FIN_DU_JOURNAL + "\ncoupée\n🧑 Toi : la fin\n", encoding="utf-8")
verifier("gros journal : on ne relit que la fin, sans sa première ligne (coupée)",
         tableau.fin_du_journal(gros).endswith("🧑 Toi : la fin\n") and len(tableau.fin_du_journal(gros)) < tableau.FIN_DU_JOURNAL,
         True)
verifier("journal absent", tableau.fin_du_journal(dossier / "absent.log"), "")

# --- 4. Son usage de Claude : des jetons et une part d'abonnement, jamais des dollars ------------------------------
compteur.DEPENSES = dossier / "depenses.jsonl"
compteur.LIMITES = dossier / "limites.json"
maintenant = time.time()
hier = maintenant - 86400
compteur.DEPENSES.write_text("\n".join(json.dumps(l) for l in [
    {"t": maintenant, "source": "steve", "cout": 0.5, "entree": 1000, "cache": 200000, "sortie": 500},
    {"t": maintenant, "source": "memoire", "cout": 0.1, "entree": 100, "cache": 0, "sortie": 50},
    {"t": hier, "source": "tache", "cout": 2.0, "entree": 5000, "cache": 1000000, "sortie": 3000},
    {"t": maintenant - 30 * 86400, "source": "steve", "cout": 9.0, "entree": 9, "cache": 9, "sortie": 9},  # trop vieux
]) + "\nligne abîmée\n", encoding="utf-8")
jours = compteur.par_jour(7)
verifier("7 jours, du plus ancien à aujourd'hui", [j["jour"] for j in jours],
         [(dt.date.today() - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)])
verifier("aujourd'hui : jetons lus et écrits, appels", jours[-1],
         {"jour": dt.date.today().isoformat(), "cout": 0.6, "jetons": 201650, "appels": 2})
verifier("hier", (jours[-2]["jetons"], jours[-2]["appels"]), (1008000, 1))
verifier("un jour sans rien", (jours[0]["jetons"], jours[0]["appels"]), (0, 0))

compteur.LIMITES.write_text(json.dumps({"fenetres": {
    "five_hour": {"utilization": 0.9, "resets_at": maintenant - 60},           # remise à zéro depuis la mesure
    "seven_day": {"utilization": 0.42, "resets_at": maintenant + 3 * 86400}}}), encoding="utf-8")
u = tableau.usage()
verifier("usage : les jours sans dollars", u["jours"][-1],
         {"jour": dt.date.today().isoformat(), "jetons": 201650, "appels": 2})
verifier("usage : une fenêtre déjà remise à zéro repart de 0", u["limites"][0],
         {"nom": "la fenêtre de 5 heures", "part": 0.0, "retour": ""})
verifier("usage : la semaine", (u["limites"][1]["nom"], u["limites"][1]["part"], u["limites"][1]["retour"].startswith("le ")),
         ("la semaine", 0.42, True))
verifier("usage : aucun prix nulle part", "cout" in json.dumps(u) or "$" in json.dumps(u), False)
compteur.LIMITES.unlink()
verifier("usage : limites pas encore connues", tableau.usage()["limites"], [])

# --- 4 bis. Le Mac, lu dans les sorties de macOS (des sorties écrites à la main : aucune commande lancée) ---------
SORTIES = {
    "pmset": "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t46%; discharging; 3:25 remaining present: true\n",
    "kern.memorystatus_vm_pressure_level": "2\n",
    "vm.swapusage": "total = 7168.00M  used = 6123.25M  free = 1044.75M  (encrypted)\n",
    "kern.boottime": f"{{ sec = {int(time.time()) - 90000}, usec = 0 }} Thu Nov 20 10:00:00 2026\n",
    "/bin/ps": (f"  {os.getpid()}  12,0  20480 /Users/x/Jervis/.venv/bin/python\n"
                "  501  30.0 2097152 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
                "  502  50.0 1048576 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)\n"
                "  503   5.0  524288 /Applications/Cursor.app/Contents/MacOS/Cursor\n"
                "  504   1.0   10240 /usr/sbin/cfprefsd\n"
                "  pas une ligne\n"),
    "/usr/sbin/netstat": ("Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll\n"
                          "lo0        16384 <Link#1>                        900     0     500000      900     0     500000     0\n"
                          "en0        1500  <Link#11>   aa:bb:cc:dd:ee:ff   1000     0    4000000      800     0    1000000     0\n"
                          "en0        1500  192.168.1     192.168.1.20      1000     -    4000000      800     -    1000000     -\n"
                          "en1        1500  <Link#12>                          10     0       1000       10     0       2000     0\n"),
}
vraie_sortie = tableau._sortie
tableau._sortie = lambda *c: next((v for k, v in SORTIES.items() if k in c), "")
verifier("batterie : part, secteur, minutes restantes, en charge", tableau.batterie(),
         {"part": 0.46, "secteur": False, "reste": 205, "charge": False})
SORTIES["pmset"] = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charging; 1:10 remaining present: true\n"
verifier("batterie qui charge", (tableau.batterie()["charge"], tableau.batterie()["secteur"]), (True, True))
SORTIES["pmset"] = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t46%; discharging; 20:00 remaining present: true\n"
verifier("batterie : « 20:00 » juste après le débranchement n'est pas une estimation", tableau.batterie()["reste"], None)
SORTIES["pmset"] = "Now drawing from 'AC Power'\n"
verifier("Mac sans batterie", tableau.batterie(), None)
verifier("pression mémoire « tendue »", tableau.pression_memoire(), "tendue")
SORTIES["kern.memorystatus_vm_pressure_level"] = "rien\n"
verifier("pression illisible", tableau.pression_memoire(), None)
verifier("swap en Go", tableau.swap_go(), 6.0)
verifier("allumé depuis (secondes)", abs(tableau.allume_depuis() - 90000) <= 2, True)
apps = tableau.apps_actives()
coeurs = os.cpu_count() or 1
verifier("apps : les aides comptent pour leur app, lui sous son prénom",
         [a["nom"] for a in apps["processeur"]], ["Google Chrome", P, "Cursor"])
verifier("apps : part du Mac entier", apps["processeur"][0]["part"], round(0.8 / coeurs, 3))
verifier("apps : mémoire en Go", apps["memoire"][:2], [{"nom": "Google Chrome", "part": 3.0}, {"nom": "Cursor", "part": 0.5}])
verifier("réseau : le Wi-Fi et le câble (pas lo0, pas les lignes d'adresse)", tableau._Reseau._compteurs(), (4001000, 1002000))
reseau = tableau._Reseau()
verifier("réseau : première mesure, pas encore de débit", reseau.debit(), None)
reseau._avant = (time.monotonic() - 2, 4001000 - 2000, 1002000 - 1000)
d = reseau.debit()
verifier("réseau : octets par seconde", (900 <= d["recu"] <= 1000, 450 <= d["envoye"] <= 500), (True, True))
tableau._sortie = lambda *c: ""
verifier("rien à lire : rien d'inventé", (tableau.batterie(), tableau.pression_memoire(), tableau.swap_go(),
                                          tableau.allume_depuis(), tableau.apps_actives(), tableau._Reseau._compteurs()),
         (None, None, None, None, {"processeur": [], "memoire": []}, None))
tableau._sortie = vraie_sortie

# --- 5. Ce que la page reçoit --------------------------------------------------------------------------------------
tableau.memoire_occupee = lambda: 0.5                      # aucune commande système pendant le test
tableau.batterie = lambda: {"part": 0.8, "secteur": True, "reste": None, "charge": False}
tableau.pression_memoire = lambda: "normale"
tableau.swap_go = lambda: 1.5
tableau.allume_depuis = lambda: 3600
tableau.apps_actives = lambda: {"processeur": [{"nom": "Cursor", "part": 0.1}], "memoire": [{"nom": "Cursor", "part": 2.0}]}
tableau._Reseau._compteurs = staticmethod(lambda: (1000, 2000))
journal = dossier / "jervis.log"
journal.write_text(JOURNAL, encoding="utf-8")
enceinte = types.SimpleNamespace(niveau=0.25)
oreilles = types.SimpleNamespace(en_pause=False, niveau=0.125, haut_parleur=enceinte)
etat = types.SimpleNamespace(nom="parle", oreilles=oreilles)
appuis: list[str] = []
ecran = tableau.Tableau(etat, {"pause": lambda: appuis.append("pause")}, journal=journal)

verifier("pouls", ecran.pouls(), {"etat": "parle", "voix": 0.25, "micro": 0.125})
oreilles.en_pause = True
verifier("pouls, micro coupé : plus aucun niveau", ecran.pouls(), {"etat": "parle", "voix": 0.0, "micro": 0.0})
etat.oreilles = None
verifier("pouls avant que le micro soit prêt", ecran.pouls(), {"etat": "parle", "voix": 0.0, "micro": 0.0})
etat.oreilles, oreilles.en_pause = oreilles, False

e = ecran.instantane()
verifier("instantané : ses clés", sorted(e), sorted(["prenom", "etat", "micro_en_pause", "echanges", "outils", "chronos",
                                                    "mediane", "usage", "systeme", "voix", "souvenirs", "agents", "commandes",
                                                    "bilan_agents", "connecteurs"]))
verifier("instantané : sans agents, pas de bilan", e["bilan_agents"], {})
verifier("instantané : ses outils connectés, pas encore vérifiés",
         [(c["nom"], c["statut"]) for c in e["connecteurs"]],
         [("Chrome", "inconnu"), ("Gmail", "inconnu"), ("Agenda", "inconnu"), ("Drive", "inconnu"), ("Zapier", "inconnu")])
from jervis import brain  # noqa: E402
brain.STATUT_SERVEURS.update({"claude-in-chrome": ("connected", 1.0), "claude.ai Gmail": ("needs-auth", 1.0),
                              "claude.ai Zapier": ("failed", 1.0), "jervis": ("connected", 1.0)})
ecran._garde.pop("connecteurs", None)   # (la page ne les relit que toutes les 5 s)
verifier("instantané : leur état, tel que Claude Code l'a dit (et pas ses propres serveurs)",
         [(c["nom"], c["statut"]) for c in ecran.instantane()["connecteurs"]],
         [("Chrome", "connected"), ("Gmail", "needs-auth"), ("Agenda", "inconnu"), ("Drive", "inconnu"), ("Zapier", "failed")])
brain.STATUT_SERVEURS.clear()
verifier("instantané : sans agents branchés", e["agents"], [])
verifier("instantané : la médiane de ses temps de réponse", e["mediane"], 3.7)
verifier("instantané : le Mac", (e["systeme"]["memoire"], e["systeme"]["batterie"], 0 <= e["systeme"]["processeur"] <= 1,
                                0 < e["systeme"]["disque"] <= 1), (0.5, tableau.batterie(), True, True))
s = e["systeme"]
verifier("instantané : de quoi expliquer les chiffres",
         (s["pression"], s["swap_go"], s["allume"], s["apps"]["memoire"][0]["nom"], s["reseau"],
          s["memoire_go"][0] == round(0.5 * s["memoire_go"][1], 0) or abs(s["memoire_go"][0] - 0.5 * s["memoire_go"][1]) < 1,
          s["disque_go"][0] <= s["disque_go"][1]),
         ("normale", 1.5, 3600, "Cursor", None, True, True))  # pas de débit à la première mesure
verifier("instantané : sa voix", sorted(e["voix"]), ["directe", "en_repos", "lignes", "locale", "neuronale"])
verifier("instantané : il passe en JSON", json.loads(json.dumps(e, ensure_ascii=False))["echanges"], e["echanges"])
with journal.open("a", encoding="utf-8") as f:
    f.write("🧑 Toi : Merci.\n")
verifier("instantané : le journal est relu dès qu'il change", ecran.instantane()["echanges"][-1],
         {"qui": "toi", "texte": "Merci."})

page, nonce = ecran.page()
verifier("page : rien à remplir ne reste", [m for m in ("__NONCE__", "__JETON__", "__PRENOM__") if m in page], [])
verifier("page : le style et le script portent le nonce", (f'<style nonce="{nonce}">' in page, f'<script nonce="{nonce}">' in page),
         (True, True))
verifier("page : elle reçoit le jeton", f'data-jeton="{ecran.jeton}"' in page, True)
verifier("page : un nonce neuf à chaque fois", ecran.page()[1] != nonce, True)
verifier("page : aucun style ni geste en ligne (la règle CSP les bloquerait)",
         [m for m in (' style="', " onclick=", " onload=") if m in page], [])
verifier("page : les nouveaux panneaux y sont", [m for m in ('id="connecteurs"', 'id="commandes"', 'id="bilan-agents"')
                                                  if m not in page], [])
verifier("page : plus de prix", [m for m in (" $", " $\"", "argent", ".cout", "peindreCout", "coût") if m in page], [])

# --- 5 bis. Toutes vos conversations (conversations.jsonl), pour la fenêtre « Vos conversations » ----------------
verifier("échange : le canal et les étiquettes séparés de ta demande",
         tableau.lire_echange(3, {"quand": "2026-11-27T09:14", "toi": "(WhatsApp) (message vocal) Il fait beau ?",
                                  "steve": "Oui."}),
         {"i": 3, "quand": "2026-11-27T09:14", "canal": "WhatsApp", "notes": ["message vocal"],
          "toi": "Il fait beau ?", "steve": "Oui.", "rapport": False})
verifier("échange : à voix haute, sans étiquette", tableau.lire_echange(0, {"toi": "Bonjour", "steve": "Salut"})["canal"],
         "voix")
verifier("échange : un compte rendu de tâche de fond n'est pas une demande de toi",
         [(e["canal"], e["rapport"]) for e in (tableau.lire_echange(0, {"toi": "(tâche de fond terminée) Le PDF est prêt."}),
                                                tableau.lire_echange(1, {"toi": "(écrit) (interrompu) stop"}))],
         [("voix", True), ("chat", False)])
verifier("échange : une parenthèse au milieu reste dans le texte",
         tableau.lire_echange(0, {"toi": "(écrit) J'ai vu (enfin presque) le film"})["toi"], "J'ai vu (enfin presque) le film")

histoire = dossier / "conversations.jsonl"
histoire.write_text("\n".join([json.dumps({"quand": f"2026-11-{10 + i // 10:02d}T10:{i % 60:02d}",
                                            "toi": ("(écrit) " if i % 2 else "") + f"question {i}" + (" café" if i == 7 else ""),
                                            "steve": f"réponse {i}"}, ensure_ascii=False) for i in range(100)]
                               + ["pas du json", "[1, 2]"]) + "\n", encoding="utf-8")
h = tableau.lire_conversations(histoire, n=10)
verifier("historique : les 10 derniers, du plus ancien au plus récent, les lignes cassées sautées",
         ([e["i"] for e in h["echanges"]], h["plus_anciens"], h["fin"]), (list(range(90, 100)), True, 102))
h = tableau.lire_conversations(histoire, avant=5, n=10)
verifier("historique : plus anciens, jusqu'au tout début", ([e["i"] for e in h["echanges"]], h["plus_anciens"]),
         ([0, 1, 2, 3, 4], False))
h = tableau.lire_conversations(histoire, apres=97)
verifier("historique : seulement les nouveaux", [e["i"] for e in h["echanges"]], [98, 99])
verifier("historique : la recherche, sans accents ni majuscules, tous les mots",
         [e["i"] for e in tableau.lire_conversations(histoire, cherche="CAFE question")["echanges"]], [7])
verifier("historique : rien trouvé", tableau.lire_conversations(histoire, cherche="licorne")["echanges"], [])
verifier("historique : pas encore de fichier", tableau.lire_conversations(dossier / "absent.jsonl"),
         {"echanges": [], "plus_anciens": False, "fin": 0})

# --- 5 ter. Le Markdown de ses réponses, lu par la page (node, s'il est là) ----------------------------------------
html = (Path(tableau.__file__).parent / "tableau.html").read_text(encoding="utf-8")
module = html.split("// <markdown>", 1)[1].split("\n", 1)[1].split("// </markdown>", 1)[0]
node = shutil.which("node")
if not node:
    print("   (Markdown non testé : pas de node ici)")
else:
    essais = {
        "gras": "Il fait **beau** et *doux*, ~~pluie~~ `code`",
        "lien": "Voir [la page](https://exemple.fr/a) ou https://exemple.fr/b. Pas [ça](javascript:alert(1))",
        "liste": "Courses :\n- pain\n- lait\n  - entier\n\n3. trois\n4. quatre",
        "titre": "## Résumé\nTexte\n---\n> cité **fort**",
        "code": "```python\nprint('**pas gras**')\n```",
        "table": "| Ville | °C |\n|---|---|\n| Paris | 12 |",
        "calcul": "2 * 3 * 4 et snake_case_ici",
    }
    script = module + "\nconsole.log(JSON.stringify(Object.fromEntries(Object.entries(" + json.dumps(essais) + \
             ").map(([k, v]) => [k, lireMarkdown(v)]))));"
    sortie = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    md = json.loads(sortie.stdout or "{}")
    verifier("markdown : gras, italique, barré, code", md.get("gras"),
             [{"t": "p", "lignes": [["Il fait ", {"t": "b", "c": ["beau"]}, " et ", {"t": "i", "c": ["doux"]}, ", ",
                                     {"t": "s", "c": ["pluie"]}, " ", {"t": "code", "c": ["code"]}]]}])
    verifier("markdown : liens http(s) seulement (un javascript: reste du texte)", md.get("lien"),
             [{"t": "p", "lignes": [["Voir ", {"t": "lien", "href": "https://exemple.fr/a", "c": ["la page"]}, " ou ",
                                     {"t": "lien", "href": "https://exemple.fr/b", "c": ["https://exemple.fr/b"]},
                                     ". Pas [ça](javascript:alert(1))"]]}])
    verifier("markdown : listes à puces (un sous-niveau) et numérotées", md.get("liste"),
             [{"t": "p", "lignes": [["Courses :"]]},
              {"t": "liste", "ordonnee": False, "debut": 1, "items": [{"c": ["pain"], "sous": []},
                                                                      {"c": ["lait"], "sous": [["entier"]]}]},
              {"t": "liste", "ordonnee": True, "debut": 3, "items": [{"c": ["trois"], "sous": []},
                                                                     {"c": ["quatre"], "sous": []}]}])
    verifier("markdown : titre, filet, citation", md.get("titre"),
             [{"t": "titre", "niveau": 2, "c": ["Résumé"]}, {"t": "p", "lignes": [["Texte"]]}, {"t": "filet"},
              {"t": "citation", "c": [{"t": "p", "lignes": [["cité ", {"t": "b", "c": ["fort"]}]]}]}])
    verifier("markdown : dans un bloc de code, rien n'est interprété", md.get("code"),
             [{"t": "code", "langue": "python", "texte": "print('**pas gras**')"}])
    verifier("markdown : un tableau", md.get("table"),
             [{"t": "table", "tete": [["Ville"], ["°C"]], "rangees": [[["Paris"], ["12"]]]}])
    verifier("markdown : un calcul et un nom_avec_tirets restent tels quels", md.get("calcul"),
             [{"t": "p", "lignes": [["2 * 3 * 4 et snake_case_ici"]]}])
    verifier("markdown : jamais innerHTML dans la page", ("innerHTML" in html.replace("jamais innerHTML", ""),
                                                          "insertAdjacentHTML" in html), (False, False))

# --- 6. Le serveur : cette machine seulement, et un jeton pour appuyer sur un bouton -------------------------------
with socket.socket() as s:
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    except OSError:
        port = None

if port is None or not ecran.demarrer(port):
    print("   (serveur non testé : impossible d'ouvrir un port ici)")
else:
    def demander(methode: str, chemin: str, **entetes: str):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(methode, chemin, headers={"Host": f"127.0.0.1:{port}", **entetes})
        r = c.getresponse()
        corps = r.read()
        c.close()
        return r, corps

    r, corps = demander("GET", "/")
    regles = r.getheader("Content-Security-Policy") or ""
    verifier("GET / : la page", (r.status, "<html" in corps.decode()), (200, True))
    verifier("GET / : règles strictes", ("default-src 'none'" in regles, "frame-ancestors 'none'" in regles,
                                        "unsafe-inline" in regles), (True, True, False))
    verifier("GET / : jamais gardée en cache", (r.getheader("Cache-Control"), r.getheader("X-Content-Type-Options")),
             ("no-store", "nosniff"))
    r, corps = demander("GET", "/pouls")
    verifier("GET /pouls", (r.status, json.loads(corps)["etat"], r.getheader("Access-Control-Allow-Origin")),
             (200, "parle", None))
    r, corps = demander("GET", "/etat?x=1")
    verifier("GET /etat", (r.status, "usage" in json.loads(corps)), (200, True))
    verifier("GET ailleurs", demander("GET", "/jervis.log")[0].status, 404)
    verifier("un autre site (Host étranger) est refusé", demander("GET", "/etat", Host="mechant.example")[0].status, 403)
    verifier("POST sans jeton", demander("POST", "/action/pause")[0].status, 403)
    verifier("POST avec un mauvais jeton", demander("POST", "/action/pause", **{"X-Jeton": "faux"})[0].status, 403)
    verifier("POST d'un autre site, même avec le jeton",
             demander("POST", "/action/pause", Host="mechant.example", **{"X-Jeton": ecran.jeton})[0].status, 403)
    verifier("aucun bouton n'a été pressé jusqu'ici", appuis, [])
    verifier("POST avec le jeton", demander("POST", "/action/pause", **{"X-Jeton": ecran.jeton})[0].status, 204)
    verifier("le bouton a été pressé une fois", appuis, ["pause"])
    from jervis import outils
    vrai_journal, outils.JOURNAL = outils.JOURNAL, histoire   # jamais tes vraies conversations dans un test
    verifier("GET /conversations sans jeton", demander("GET", "/conversations")[0].status, 403)
    r, corps = demander("GET", "/conversations?avant=50&n=3&q=question", **{"X-Jeton": ecran.jeton})
    d = json.loads(corps)
    verifier("GET /conversations avec le jeton", (r.status, [e["i"] for e in d["echanges"]], d["plus_anciens"]),
             (200, [47, 48, 49], True))
    r, corps = demander("GET", "/conversations?avant=-1&n=abc&apres=1e9", **{"X-Jeton": ecran.jeton})
    verifier("GET /conversations : des nombres étranges sont ignorés", (r.status, len(json.loads(corps)["echanges"])),
             (200, tableau.HISTOIRE_PAGE))
    outils.JOURNAL = vrai_journal
    verifier("POST d'un bouton inconnu", demander("POST", "/action/eteindre", **{"X-Jeton": ecran.jeton})[0].status, 404)

print(f"\n{'✅' if not echecs else '❌'} tableau : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
