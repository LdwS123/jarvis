"""Répondre à tes messages à ta place, dans ton style : la lecture d'iMessage et de WhatsApp, ton style, l'envoi
dans la bonne conversation, le répondeur et ses garde-fous, le Gardien.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_reponses.py

Rien ne part et rien n'est lu de ton Mac : Messages, WhatsApp et Contacts sont des bases fabriquées dans un dossier
temporaire, l'envoi est un faux, et le Claude qui rédige est remplacé par une fonction.
"""

import asyncio
import inspect
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, comptes, config, distance, outils, permissions, reponses  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp())
MAINTENANT = time.time()
MOI, LEA, PAUL, BANQUE, ROMAIN = "+33612345678", "+33698765432", "+33655555555", "38800", "+33611112222"
config.IMESSAGE = "auto"

# --- Contacts ------------------------------------------------------------------------------------------------
carnet = TMP / "AddressBook-v22.abcddb"
with sqlite3.connect(carnet) as c:
    c.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);
        INSERT INTO ZABCDRECORD VALUES (1, 'Léa', 'Martin', NULL), (2, 'Paul', NULL, NULL), (3, NULL, NULL, 'Garage Dupont');
        INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '06 98 76 54 32'), (2, 2, '+33 6 55 55 55 55'), (3, 3, '+33677777777');
    """)
reponses.CARNETS = [carnet]
verifier("contacts : un numéro écrit à la française", reponses.carnet().get(LEA), "Léa Martin")
verifier("contacts : une entreprise, par son nom", reponses.carnet().get("+33677777777"), "Garage Dupont")

# --- Messages (chat.db) --------------------------------------------------------------------------------------
messages_db = TMP / "chat.db"
lien = sqlite3.connect(messages_db)
lien.executescript("""
    CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
    CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, style INTEGER, last_addressed_handle TEXT,
                       display_name TEXT);
    CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER, is_from_me INTEGER,
                          handle_id INTEGER, destination_caller_id TEXT, associated_message_type INTEGER DEFAULT 0,
                          item_type INTEGER DEFAULT 0);
    CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
""")
lien.executemany("INSERT INTO handle VALUES (?, ?)", [(1, LEA), (2, PAUL), (3, BANQUE), (4, MOI), (5, ROMAIN)])
lien.executemany("INSERT INTO chat VALUES (?, ?, ?, ?, ?)", [
    (1, MOI, 45, MOI, ""), (2, LEA, 45, MOI, ""), (3, PAUL, 45, MOI, ""), (4, BANQUE, 45, MOI, ""),
    (5, "chat123456", 43, MOI, "Foot"), (6, "+33644444444", 45, MOI, "")])
_HANDLES = {2: 1, 3: 2, 4: 3, 5: 5, 6: 0, 1: 4}


def ecrire(chat: int, texte: str, de_moi: bool, il_y_a: float, **extra) -> float:
    """Un message dans la fausse base ; rend son heure (epoch)."""
    quand = MAINTENANT - il_y_a
    curseur = lien.execute(
        "INSERT INTO message (text, date, is_from_me, handle_id, destination_caller_id, associated_message_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (texte, int((quand - comptes._EPOQUE_APPLE) * 1e9), int(de_moi), 0 if de_moi else _HANDLES[chat],
         MOI if de_moi else None, extra.get("reaction", 0)))
    lien.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, curseur.lastrowid))
    lien.commit()
    return quand


ecrire(1, "Steve, quel temps demain ?", True, 7200)          # ta conversation avec toi-même : jamais lue ici
for i, t in enumerate(["wsh ça dit quoi", "mdr grave 😂", "jsp encore", "tkt je gère", "ouais vas y 😂",
                       "on se capte demain", "mdr t'es fou", "vrm ?"]):
    ecrire(2, f"salut, ça va ?" if i == 0 else "bien et toi", False, 6000 - i * 600)
    ecrire(2, t, True, 5900 - i * 600)
ecrire(2, "tu viens ce soir ?", False, 600)
ecrire(2, "😂", False, 590, reaction=2000)                     # une réaction : pas un message
ecrire(3, "Ça marche, à demain", False, 4000)
ecrire(3, "top merci", True, 3000)                              # Paul : c'est toi qui as écrit en dernier
ecrire(4, "Votre code de vérification est 482913", False, 500)  # la banque : un code court, pas une personne
ecrire(5, "qui vient dimanche ?", False, 400)                   # un groupe
ecrire(6, "t'es là ?", False, 86400 * 10)                       # trop vieux
comptes.CHAT_DB = messages_db

# --- WhatsApp ------------------------------------------------------------------------------------------------
wa = TMP / "ChatStorage.sqlite"
lien_wa = sqlite3.connect(wa)
lien_wa.executescript("""
    CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZLASTMESSAGEDATE REAL,
                                 ZMESSAGECOUNTER INTEGER, ZSESSIONTYPE INTEGER);
    CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCONTACTNAME TEXT, ZMEMBERJID TEXT);
    CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER, ZTEXT TEXT,
                             ZMESSAGEDATE REAL, ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER);
    INSERT INTO ZWAGROUPMEMBER VALUES (1, 'Tonton', '33600000000@s.whatsapp.net');
""")
lien_wa.executemany("INSERT INTO ZWACHATSESSION VALUES (?, ?, ?, 0, 0, 0)", [
    (1, "33677777777@s.whatsapp.net", "Karim"), (2, "98765@lid", "Sofia"), (3, "status@broadcast", ""),
    (4, "120363@g.us", "Famille")])


def ecrire_wa(session: int, texte: str | None, de_moi: bool, il_y_a: float, genre: int = 0, membre=None) -> float:
    quand = MAINTENANT - il_y_a
    lien_wa.execute("INSERT INTO ZWAMESSAGE (ZCHATSESSION, ZISFROMME, ZTEXT, ZMESSAGEDATE, ZMESSAGETYPE, ZGROUPMEMBER) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (session, int(de_moi), texte, quand - comptes._EPOQUE_APPLE, genre, membre))
    lien_wa.execute("UPDATE ZWACHATSESSION SET ZLASTMESSAGEDATE = MAX(ZLASTMESSAGEDATE, ?) WHERE Z_PK = ?",
                    (quand - comptes._EPOQUE_APPLE, session))
    lien_wa.commit()
    return quand


ecrire_wa(1, "Yo frérot", False, 3000)
ecrire_wa(1, "Wesh", True, 2900)
ecrire_wa(1, None, False, 800, genre=3)                         # un vocal
ecrire_wa(1, "Tu passes quand ?", False, 700)
ecrire_wa(2, "Hello ! Tu as reçu mon mail ?", False, 900)
ecrire_wa(3, "Mon statut", False, 100)
ecrire_wa(4, "Repas dimanche ?", False, 300, membre=1)
reponses.WHATSAPP_DB = wa

# --- Ton style -----------------------------------------------------------------------------------------------
print("✍️  Ton style")
M = reponses.Message
s = reponses.style([M(True, t, 0) for t in ["wsh ça dit quoi", "mdr grave 😂", "jsp encore", "tkt je gère",
                                            "ouais vas y 😂", "mdr t'es fou"]])
for morceau in ("en français", "tutoie", "très courts", "commence en minuscule", "presque jamais de point final",
                "😂", "mdr", "jsp", "tkt"):
    verifier(f"style : {morceau}", morceau in str(s), True)
verifier("style : des exemples de toi", "« mdr t'es fou »" in str(s), True)
s = reponses.style([M(True, t, 0) for t in ["Bonjour Monsieur, merci pour votre retour.",
                                            "Je vous confirme notre rendez-vous de jeudi.",
                                            "Très bonne journée à vous.", "Pouvez-vous m'envoyer le devis ?"]])
for morceau in ("vouvoie", "commence par une majuscule", "finit ses phrases par un point", "presque pas d'emojis"):
    verifier(f"style soutenu : {morceau}", morceau in str(s), True)
verifier("style en anglais", "en anglais" in str(reponses.style([M(True, t, 0) for t in [
    "yeah I think so", "what are you doing tonight", "lol that is so true", "idk man"]])), True)
verifier("style : trop peu de messages → rien d'inventé", reponses.style([M(True, "ok", 0)]).n, 0)
rafale = [M(False, "?", 0), M(True, "a", 0), M(True, "b", 0), M(True, "c", 0), M(False, "?", 0), M(True, "d", 0),
          M(True, "e", 0)]
verifier("style : tes rafales de petits messages", "messages d'affilée" in str(reponses.style(
    [m for m in rafale if m.de_moi], rafale)), True)

# --- Lire les conversations ----------------------------------------------------------------------------------
print("\n📥 Les conversations")
fils, soucis = reponses.lire_fils(["imessage"], MAINTENANT - 72 * 3600)
par_id = {f.ident: f for f in fils}
verifier("iMessage : ni toi-même, ni la banque, ni le groupe, ni le vieux fil", sorted(par_id),
         ["imessage:2", "imessage:3"])
verifier("iMessage : le nom vient de tes contacts", par_id["imessage:2"].nom, "Léa Martin")
verifier("iMessage : Léa attend ta réponse, pas Paul", (par_id["imessage:2"].attend, par_id["imessage:3"].attend),
         (True, False))
verifier("iMessage : les réactions ne sont pas des messages", par_id["imessage:2"].messages[-1].texte,
         "tu viens ce soir ?")
verifier("iMessage : rien de mal lu", soucis, [])
fils_g, _ = reponses.lire_fils(["imessage"], MAINTENANT - 72 * 3600, groupes=True)
verifier("iMessage : les groupes si on les demande, par leur nom", [f.nom for f in fils_g if f.groupe], ["Foot"])
verifier("iMessage : qui parle dans un groupe", [f for f in fils_g if f.groupe][0].messages[-1].qui, ROMAIN)
wfils, _ = reponses.lire_fils(["whatsapp"], MAINTENANT - 72 * 3600)
wpar = {f.nom: f for f in wfils}
verifier("WhatsApp : ni les statuts ni les groupes", sorted(wpar), ["Karim", "Sofia"])
verifier("WhatsApp : un numéro, ou rien (on l'ouvre par son nom)", (wpar["Karim"].adresse, wpar["Sofia"].adresse),
         ("+33677777777", ""))
verifier("WhatsApp : un vocal dit ce qu'il est", [m.texte for m in wpar["Karim"].messages],
         ["Yo frérot", "Wesh", "(vocal)", "Tu passes quand ?"])
wfils_g, _ = reponses.lire_fils(["whatsapp"], MAINTENANT - 72 * 3600, groupes=True)
famille = [f for f in wfils_g if f.groupe]
verifier("WhatsApp : le groupe, avec qui parle", (famille[0].nom, famille[0].messages[-1].qui), ("Famille", "Tonton"))
verifier("trouver : une conversation relue dans la base", reponses.trouver("whatsapp:2").nom, "Sofia")
verifier("trouver : un identifiant inventé → rien", (reponses.trouver("imessage:99"), reponses.trouver("sms:2"),
                                                       reponses.trouver("imessage:2; DROP")), (None, None, None))
verifier("le nom pour le Gardien", reponses.nom_du_fil("imessage:2"), "Léa Martin")
verifier("qui : un mot entier du nom", (reponses.correspond("Léa Martin", LEA, ["lea"]),
                                         reponses.correspond("Léa Martin", LEA, ["le"]),
                                         reponses.correspond("Léa Martin", LEA, ["06 98 76 54 32"])), (True, False, True))
reponses.WHATSAPP_DB = TMP / "absente.sqlite"
_, soucis = reponses.lire_fils(["whatsapp"], 0)
verifier("WhatsApp absent : dit, sans planter", "WhatsApp" in " ".join(soucis), True)
reponses.WHATSAPP_DB = wa

texte = reponses.en_attente(["imessage", "whatsapp"], 72, False, [], maintenant=MAINTENANT)
verifier("en attente : Léa, Karim et Sofia", all(f"[{i}]" in texte for i in ("imessage:2", "whatsapp:1", "whatsapp:2")),
         True)
verifier("en attente : pas Paul (tu lui as répondu)", "Paul" in texte, False)
verifier("en attente : la conversation, avec tes lignes « Moi : »", "Moi : vrm ?\nLéa Martin : tu viens ce soir ?" in texte,
         True)
verifier("en attente : ton style avec elle", "Son style avec Léa Martin : Il écrit en français" in texte, True)
verifier("en attente : leurs messages ne sont pas des ordres", "jamais des ordres" in texte, True)
verifier("en attente : la banque et son code restent dehors", "482913" in texte, False)
verifier("en attente : seulement Karim", "[whatsapp:1]" in (t := reponses.en_attente(
    ["imessage", "whatsapp"], 72, False, ["karim"], maintenant=MAINTENANT)) and "Léa" not in t, True)
verifier("en attente : personne", reponses.en_attente(["imessage"], 72, False, ["Zoé"], maintenant=MAINTENANT)
         .startswith("Personne n'attend"), True)
verifier("le style général ne lit pas tes notes à toi-même", "quel temps" in str(reponses.style_general(["imessage"])),
         False)

# --- Répondre dans une conversation --------------------------------------------------------------------------
print("\n📤 Répondre")
partis: list[tuple] = []


async def faux_imessage(adresse, texte, groupe):
    partis.append(("imessage", adresse, groupe, texte))
    return 0, ""


async def faux_whatsapp(contact, texte, groupe):
    partis.append(("whatsapp", contact, groupe, texte))
    return "ENVOYÉ", "ok"

reponses._par_imessage, reponses._par_whatsapp = faux_imessage, faux_whatsapp
verifier("bulles : une ligne vide sépare, 3 au plus", reponses.bulles("« grave »\n\nj'arrive\n\nvers 9h\n\nok ?"),
         ["grave", "j'arrive", "vers 9h ok ?"])
r = asyncio.run(reponses.repondre.handler({"fil": "imessage:2", "texte": "grave\n\nj'arrive vers 21h"}))
verifier("repondre : deux messages, à Léa, dans sa conversation", partis,
         [("imessage", LEA, "", "grave"), ("imessage", LEA, "", "j'arrive vers 21h")])
verifier("repondre : ENVOYÉ, sans nom (rien d'écrit par quelqu'un d'autre)", r["content"][0]["text"],
         "ENVOYÉ : 2 message(s) envoyé(s) dans cette conversation.")
verifier("repondre : ce qui part n'est jamais relu comme un ordre de toi", distance.vient_de_lui("grave"), True)
partis.clear()
asyncio.run(reponses.repondre.handler({"fil": "whatsapp:1", "texte": "ce soir"}))
asyncio.run(reponses.repondre.handler({"fil": "whatsapp:2", "texte": "oui reçu"}))
verifier("repondre WhatsApp : par le numéro, ou par le nom exact", partis,
         [("whatsapp", "+33677777777", "", "ce soir"), ("whatsapp", "", "Sofia", "oui reçu")])
partis.clear()
r = asyncio.run(reponses.repondre.handler({"fil": "imessage:42", "texte": "coucou"}))
verifier("repondre : une conversation inconnue → rien ne part", (r.get("is_error"), partis), (True, []))


async def imessage_rate(adresse, texte, groupe):
    return 0, "aucun"
reponses._par_imessage = imessage_rate
r = asyncio.run(reponses.repondre.handler({"fil": "imessage:5", "texte": "moi"}))
verifier("repondre : un groupe introuvable → ÉCHEC", r.get("is_error"), True)
reponses._par_imessage = faux_imessage

# --- Le répondeur --------------------------------------------------------------------------------------------
print("\n📨 Le répondeur")
R = reponses.Repondeur()
dits: list[str] = []
redige: list[tuple[str, str]] = []
reponse_du_modele = ["tkt j'arrive vers 21h"]


async def faux_rediger(fil, style_, consigne):
    redige.append((fil.nom, consigne))
    return reponse_du_modele[0]

R.prevenir, R.rediger = dits.append, faux_rediger
config.REPONDEUR_DELAI_S, config.REPONDEUR_MAX_PAR_H = 90, 2
verifier("éteint au départ", R.decrire(), "éteint.")
R.activer([], ["imessage"], 2, "je suis en vol", maintenant=MAINTENANT - 300)
verifier("allumé : ce qu'il fera", "jusqu'à" in R.decrire() and "« je suis en vol »" in R.decrire(), True)
asyncio.run(R.tour(maintenant=MAINTENANT))
verifier("les messages d'avant l'allumage restent à toi", (partis, redige), ([], []))
arrive = ecrire(2, "t'es où ?", False, 100)
asyncio.run(R.tour(maintenant=arrive + 30))
verifier("il te laisse le temps de répondre toi-même", partis, [])
asyncio.run(R.tour(maintenant=arrive + 95))
verifier("puis il répond, à Léa, dans ton style", partis, [("imessage", LEA, "", "tkt j'arrive vers 21h")])
verifier("le Claude qui rédige a ta consigne", redige, [("Léa Martin", "je suis en vol")])
verifier("il te le raconte", dits[-1].startswith("📨 Répondu à Léa Martin"), True)
asyncio.run(R.tour(maintenant=arrive + 200))
verifier("une seule réponse par message", len(partis), 1)

partis.clear()
reponse_du_modele[0] = "PASSE : elle demande où il est"
arrive = ecrire(2, "tu m'en veux ?", False, 50)
asyncio.run(R.tour(maintenant=arrive + 100))
verifier("PASSE : rien ne part, il te prévient", (partis, "je n'ai pas répondu seul" in dits[-1]), ([], True))

reponse_du_modele[0] = "ok"
arrive = ecrire(2, "allo ?", False, 40)
asyncio.run(R.tour(maintenant=arrive + 100))
verifier("la deuxième réponse de l'heure part", len(partis), 1)
arrive = ecrire(2, "allo ??", False, 30)
asyncio.run(R.tour(maintenant=arrive + 100))
verifier("au-delà du plafond, il te laisse la suite (pas de ping-pong de répondeurs)",
         (len(partis), "je te laisse la suite" in dits[-1]), (1, True))

partis.clear()


async def tu_reponds_pendant_qu_il_ecrit(fil, style_, consigne):
    ecrire(3, "j'arrive", True, 5)
    return "ok"
R.activer(["Paul"], ["imessage"], 0, "", maintenant=MAINTENANT - 300)
R.rediger = tu_reponds_pendant_qu_il_ecrit
arrive_lea = ecrire(2, "réponds !", False, 15)
arrive = ecrire(3, "tu viens ?", False, 10)
asyncio.run(R.tour(maintenant=arrive + 100))
verifier("seulement Paul, et rien s'il a déjà répondu toi-même", partis, [])
verifier("0 h : jusqu'à ce que tu l'arrêtes", "jusqu'à ce que tu l'arrêtes" in R.decrire(), True)
verifier("éteint, il dit ce qu'il a fait", R.desactiver().startswith("Répondeur éteint."), True)
R.activer([], ["imessage"], 1, "", maintenant=MAINTENANT)
asyncio.run(R.tour(maintenant=MAINTENANT + 3700))
verifier("l'heure passée, il s'éteint et te prévient", (R.reglage, "Répondeur arrêté" in dits[-1]), (None, True))
verifier("PASSE : la raison", reponses.passe("PASSE : c'est perso"), "c'est perso")
verifier("PASSE : en gras ou entre guillemets aussi", reponses.passe("**PASSE** — dispute"), "dispute")
verifier("une vraie réponse n'est pas un PASSE", reponses.passe("« ok ça marche »"), None)
verifier("rien d'écrit vaut PASSE", reponses.passe("  "), "rien d'écrit")
verifier("le Claude qui rédige n'a aucun outil", "tools=[]" in inspect.getsource(reponses.rediger), True)
for regle in ("PASSE", "robot", "Ne l'engage à rien", "données"):
    verifier(f"ses règles : {regle}", regle in reponses.REDACTION, True)

r = asyncio.run(reponses.repondeur.handler({"action": "activer", "qui": "Léa et Karim", "heures": 2,
                                            "consigne": "en vol jusqu'à 18h"}))
verifier("l'outil : allumer", ("Léa et Karim" in r["content"][0]["text"], reponses.REPONDEUR.reglage.qui),
         (True, ["Léa", "Karim"]))
r = asyncio.run(reponses.repondeur.handler({"action": "etat"}))
verifier("l'outil : son état", r["content"][0]["text"].startswith("Répondeur : je réponds à Léa et Karim"), True)
asyncio.run(reponses.repondeur.handler({"action": "desactiver"}))
verifier("l'outil : éteindre", reponses.REPONDEUR.reglage, None)

# --- Raconter sur l'iPhone ------------------------------------------------------------------------------------
racontes: list[tuple[str, str]] = []


async def faux_envoi(adresse, texte):
    racontes.append((adresse, texte))
    return 0, ""
boite = distance.Boite(envoyer=faux_envoi, base=messages_db, adresses="auto")
with boite._ouvrir() as l:
    boite._trouver_fils(l)
asyncio.run(boite.raconter("📨 Répondu à Léa"))
verifier("raconté dans ta conversation avec toi-même, marqué 🤖", racontes, [(MOI, "🤖 📨 Répondu à Léa")])

# --- Le Gardien ----------------------------------------------------------------------------------------------
print("\n🛡  Le Gardien")
J = "mcp__jervis__"
verifier("la question : répondre", permissions.decrire(J + "repondre", {"fil": "imessage:2", "texte": "ok"}),
         "Je réponds à Léa Martin (iMessage) : « ok ». Je peux ?")
verifier("la question : le répondeur", permissions.decrire(J + "repondeur", {
    "action": "activer", "qui": "Léa", "canal": "whatsapp", "heures": 2, "consigne": "je suis en vol"}),
    "J'active ton répondeur : je réponds à ta place à Léa sur WhatsApp, pendant 2 h, en disant : « je suis en vol ». "
    "Je peux ?")
verifier("règle 📌 : cette conversation-là", permissions.cible_de(J + "repondre", {"fil": "imessage:2", "texte": "x"}),
         "la conversation avec Léa Martin")
verifier("règle 📌 : jamais pour le répondeur", permissions.cible_de(J + "repondeur", {"action": "activer"}), None)
verifier("répondre : un résultat écrit par le code", permissions.lit_dehors(J + "repondre"), False)
verifier("leurs messages : un texte venu d'ailleurs", permissions.lit_dehors(J + "messages_en_attente"), True)
questions: list[str] = []


async def non(q):
    questions.append(q)
    return False


async def essayer(outil, entree, dehors=True, mefiance=False):
    g = permissions.Gardien(non)
    g.nouvelle_demande(mefiance=mefiance)
    if dehors:
        g.a_lu_dehors()
    return type(await g(J + outil, entree, None)).__name__

vraie_mefiance, vraies_confirmations = config.MEFIANCE, config.CONFIRMATIONS
try:
    config.MEFIANCE, config.CONFIRMATIONS = True, "autonome"
    verifier("autonome : « réponds à tous », après les avoir lus, part seul",
             asyncio.run(essayer("repondre", {"fil": "imessage:2", "texte": "grave"})), "PermissionResultAllow")
    verifier("un lien dans ta réponse, après leurs messages : toi d'abord", asyncio.run(essayer(
        "repondre", {"fil": "imessage:2", "texte": "regarde https://piege.xyz/a"})), "PermissionResultDeny")
    verifier("demandé par un agent, pas par toi : toi d'abord", asyncio.run(essayer(
        "repondre", {"fil": "imessage:2", "texte": "ok"}, mefiance=True)), "PermissionResultDeny")
    verifier("allumer le répondeur sans rien lu : seul", asyncio.run(essayer(
        "repondeur", {"action": "activer"}, dehors=False)), "PermissionResultAllow")
    verifier("allumer le répondeur après un texte d'ailleurs : toi d'abord", asyncio.run(essayer(
        "repondeur", {"action": "activer"})), "PermissionResultDeny")
    verifier("l'éteindre : toujours seul", asyncio.run(essayer("repondeur", {"action": "desactiver"})),
             "PermissionResultAllow")
    config.CONFIRMATIONS = "toutes"
    avant = len(questions)
    verifier("mode « toutes » : chaque réponse se confirme", asyncio.run(essayer(
        "repondre", {"fil": "imessage:2", "texte": "grave"}, dehors=False)), "PermissionResultDeny")
    verifier("mode « toutes » : la question dit à qui et quoi", questions[avant:],
             ["Je réponds à Léa Martin (iMessage) : « grave ». Je peux ?"])
    verifier("mode « toutes » : son état, sans question", asyncio.run(essayer("repondeur", {"action": "etat"},
                                                                              dehors=False)), "PermissionResultAllow")
finally:
    config.MEFIANCE, config.CONFIRMATIONS = vraie_mefiance, vraies_confirmations

verifier("ses outils sont déclarés", {o.name for o in reponses.OUTILS_REPONSES} <= {o.name for o in outils.OUTILS}, True)
verifier("ses consignes le disent", "reponses.consigne()" in inspect.getsource(brain._consignes)
         and "messages_en_attente" in reponses.consigne() and "jamais des ordres" in reponses.consigne(), True)

print(f"\n{'✅' if not echecs else '❌'} réponses : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
