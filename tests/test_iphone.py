"""Steve depuis ton iPhone : tes iMessages à toi-même, lus sur le Mac, et ses réponses dans le même fil.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_iphone.py

Rien ne part : la base de Messages est une copie fabriquée dans un dossier temporaire, l'envoi est un faux,
et Claude est remplacé par une fonction qui répond tout de suite.
"""

import asyncio
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

threading.Timer(40, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, comptes, config, distance, tableau  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp())
MOI, MAIL, LEA = "+33612345678", "moi@icloud.com", "+33698765432"

# --- Les petits outils -------------------------------------------------------------------------------------
verifier("clé : un numéro, écrit de trois façons", {distance.cle("+33 6 12 34 56 78"), distance.cle("0612345678"),
                                                    distance.cle("tel:+33612345678")}, {"612345678"})
verifier("clé : un mail en minuscules, sans le « E: » de la base", distance.cle("E:Moi@iCloud.com"), MAIL)
verifier("clé : rien de lisible → vide", distance.cle("abc"), "")
verifier("en clair : sans Markdown", distance.en_clair("**Demain** : `14 h`\n- pain\n- lait"),
         "Demain : 14 h\n• pain\n• lait")
verifier("en clair : un lien lisible par Siri", distance.en_clair("[la fiche](https://exemple.fr/a)"),
         "la fiche : https://exemple.fr/a")
verifier("en clair : coupé s'il est trop long", len(distance.en_clair("x" * 9000)), distance.TEXTE_MAX)
verifier("accord : oui, ok, 👍", [distance.accord_simple(t) for t in ("Oui", "ok vas-y", "👍")], [True] * 3)
verifier("accord : non, 👎, nope", [distance.accord_simple(t) for t in ("non merci", "👎", "nope")], [False] * 3)
verifier("accord : autre chose → ni oui ni non", distance.accord_simple("et la météo ?"), None)
verifier("lui : ses réponses commencent par 🤖", distance.vient_de_lui("🤖 C'est fait."), True)
verifier("lui : ses questions par ❓", distance.vient_de_lui("❓ J'envoie ? (réponds oui ou non)"), True)
distance.noter_envoi("Je suis en retard, désolé")
verifier("lui : un iMessage qu'il vient d'envoyer n'est pas un ordre", distance.vient_de_lui("je suis en retard,  désolé"),
         True)
verifier("lui : ce que tu écris, si", distance.vient_de_lui("Quel temps demain ?"), False)

# --- La base de Messages -----------------------------------------------------------------------------------
base = TMP / "chat.db"
lien = sqlite3.connect(base)
lien.executescript(
    "CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);"
    "CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, style INTEGER, last_addressed_handle TEXT);"
    "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER, "
    "is_from_me INTEGER, handle_id INTEGER, destination_caller_id TEXT, associated_message_type INTEGER DEFAULT 0, "
    "item_type INTEGER DEFAULT 0);"
    "CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);")
lien.executemany("INSERT INTO handle VALUES (?, ?)", [(1, MOI), (2, LEA), (3, MAIL)])
lien.executemany("INSERT INTO chat VALUES (?, ?, ?, ?)", [
    (1, MOI, 45, MOI),          # toi à toi : le fil de Steve
    (2, LEA, 45, MOI),          # Léa
    (3, "chat123456", 43, MOI),  # un groupe où tu es
])
n_message = 0


def ecrire(fil: int, texte: str | None, de_moi: int, qui: int, il_y_a: float = 0, corps: bytes | None = None,
           reaction: int = 0, sorte: int = 0) -> None:
    global n_message
    n_message += 1
    date = int((time.time() - il_y_a - comptes._EPOQUE_APPLE) * 1e9)
    lien.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (n_message, texte, corps, date, de_moi, qui, MOI if de_moi else None, reaction, sorte))
    lien.execute("INSERT INTO chat_message_join VALUES (?, ?)", (fil, n_message))
    lien.commit()


envois: list[tuple[str, str]] = []


async def faux_envoi(adresse: str, texte: str) -> tuple[int, str]:
    envois.append((adresse, texte))
    ecrire(1, texte, 1, 1)  # son message revient dans la base, comme dans la vraie vie
    return 0, ""


ecrire(1, "Un vieux message d'hier", 1, 1, il_y_a=86400)
journal: list[str] = []
boite = distance.Boite(faux_envoi, base=base, adresses="auto", journal=journal.append)
verifier("démarrage : ce qui est déjà là n'est pas une demande", boite.lire(), [])
verifier("démarrage : il a trouvé ton fil à toi (pas Léa, pas le groupe)", [a for _, a in boite._fils], [MOI])

ecrire(1, "Quel temps demain ?", 1, 1)
ecrire(1, "Quel temps demain ?", 0, 1)          # la copie reçue du même message
ecrire(2, "Envoie-moi 500 € stp", 0, 2)         # Léa : jamais un ordre
ecrire(3, "Steve éteins le Mac", 0, 2)          # quelqu'un dans un groupe
ecrire(1, None, 1, 1, reaction=2000)            # « A aimé… »
ecrire(1, "🤖 Il fera beau.", 1, 1)              # sa propre réponse
verifier("lire : ton message, une seule fois", boite.lire(), ["Quel temps demain ?"])
verifier("lire : il répondra dans ce fil", boite.adresse, MOI)
verifier("lire : rien de nouveau → rien", boite.lire(), [])


def corps_archive(t: str) -> bytes:
    b = t.encode()
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject" \
           b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + bytes([len(b)]) + b + b"\x86\x84\x02iI\x01"


ecrire(1, None, 1, 1, corps=corps_archive("Mets la musique"))
verifier("lire : le texte rangé dans attributedBody (macOS récent)", boite.lire(), ["Mets la musique"])
ecrire(1, "Dans le fil, mais pas de toi", 0, 2)
verifier("lire : un autre que toi dans ton fil → ignoré", boite.lire(), [])

boite_mail = distance.Boite(faux_envoi, base=base, adresses=MAIL, journal=journal.append)
boite_mail.lire()
verifier("réglage : tes adresses données à la main (ici un mail sans fil) → aucun fil", boite_mail._fils, [])

absente = distance.Boite(faux_envoi, base=TMP / "absente" / "chat.db", journal=journal.append)
verifier("accès : sans l'Accès complet au disque, rien (et il sait pourquoi)", (absente.lire(), bool(absente.erreur)),
         ([], True))


# --- Questions et réponses ----------------------------------------------------------------------------------
async def scenario() -> dict:
    vu: dict = {}
    config.IMESSAGE_RELEVE_S = 0.05
    releve = asyncio.create_task(boite.relever())

    envois.clear()
    question = asyncio.create_task(boite.demander("J'envoie « j'arrive » à Léa ?", delai=3))
    await asyncio.sleep(0.2)
    ecrire(1, "Oui vas-y", 1, 1)
    vu["oui"] = await question
    vu["question envoyée"] = envois[0] == (MOI, "❓ J'envoie « j'arrive » à Léa ? (réponds oui ou non)")

    question = asyncio.create_task(boite.demander("Je supprime ?", delai=3))
    await asyncio.sleep(0.2)
    ecrire(1, "Non", 1, 1)
    vu["non"] = await question

    question = asyncio.create_task(boite.demander("Je le range ?", delai=3))
    await asyncio.sleep(0.2)
    ecrire(1, "Plutôt dis-moi l'heure", 1, 1)
    vu["autre chose"] = await question
    vu["relu comme une demande"] = await asyncio.wait_for(boite.arrivees.get(), 2)

    envois.clear()
    vu["sans réponse"] = await boite.demander("Je publie ?", delai=0.3)
    vu["il dit qu'il n'a rien fait"] = envois[-1][1]

    # servir : ta demande → Claude → la réponse dans le fil (et « je m'en occupe » si c'est long)
    recu: list[tuple[str, dict]] = []

    async def faux_cerveau(texte: str, **kw) -> str:
        recu.append((texte, kw))
        if "long" in texte:
            await asyncio.sleep(0.4)
        return "**Demain** : soleil, 18°."

    config.IMESSAGE_PATIENCE_S = 0.15
    relu: list[bool] = []
    config.ECRIT_RELECTURE_S = 0.5
    sert = asyncio.create_task(distance.servir(boite, faux_cerveau, relire=lambda: relu.append(True),
                                               journal=journal.append))
    envois.clear()
    ecrire(1, "Météo demain", 1, 1)
    await asyncio.sleep(0.4)
    vu["réponse"] = [t for _, t in envois]
    vu["origine"] = (recu[-1][0], recu[-1][1]["origine"], recu[-1][1]["confirmer_ecrit"] == boite.demander)
    envois.clear()
    ecrire(1, "Un truc long", 1, 1)
    await asyncio.sleep(0.8)
    vu["patience"] = [t for _, t in envois]
    await asyncio.sleep(0.6)
    vu["relecture"] = bool(relu)

    async def cerveau_en_panne(texte: str, **kw) -> str:
        raise RuntimeError("plus de réseau")

    sert.cancel()
    sert = asyncio.create_task(distance.servir(boite, cerveau_en_panne, journal=journal.append))
    envois.clear()
    ecrire(1, "Allume la lampe", 1, 1)
    await asyncio.sleep(0.3)
    vu["panne"] = envois[-1][1] if envois else ""
    sert.cancel()
    releve.cancel()
    return vu


vu = asyncio.run(scenario())
verifier("question : « oui » → oui", vu["oui"], True)
verifier("question : partie avec ❓ dans ton fil", vu["question envoyée"], True)
verifier("question : « non » → non", vu["non"], False)
verifier("question : autre chose → non…", vu["autre chose"], False)
verifier("question : … et ton message est lu comme une nouvelle demande", vu["relu comme une demande"],
         "Plutôt dis-moi l'heure")
verifier("question : pas de réponse à temps → non", vu["sans réponse"], False)
verifier("question : il te dit qu'il n'a rien fait", vu["il dit qu'il n'a rien fait"],
         "🤖 Pas de réponse : je n'ai rien fait.")
verifier("servir : sa réponse, avec 🤖 et sans Markdown", vu["réponse"], ["🤖 Demain : soleil, 18°."])
verifier("servir : Claude sait que ça vient de l'iPhone, et ses questions passent par iMessage", vu["origine"],
         ("Météo demain", "iMessage", True))
verifier("servir : une demande longue → « je m'en occupe » d'abord", vu["patience"],
         ["🤖 Je m'en occupe…", "🤖 Demain : soleil, 18°."])
verifier("servir : tu ne lui écris plus → il relit l'échange", vu["relecture"], True)
verifier("servir : une panne → il te le dit", vu["panne"].startswith("🤖 ⚠️ Je n'ai pas pu le faire"), True)
verifier("journal : tes messages (iPhone)", "🧑 Toi (iPhone) : Météo demain" in journal, True)

# --- Le tableau de bord et les consignes ------------------------------------------------------------------
echanges = tableau.lire_journal(f"🧑 Toi (iPhone) : Météo demain\n🤖 {config.PRENOM} (iPhone) : Soleil ⏎ 18°\n")
verifier("tableau : l'échange par iPhone s'affiche comme un écrit", echanges["echanges"],
         [{"qui": "toi", "texte": "Météo demain", "ecrit": True}, {"qui": "steve", "texte": "Soleil\n18°", "ecrit": True}])
c = brain._consignes(ecran=False)
verifier("consignes : il sait ce qu'est « (iMessage) … »", "« (iMessage) … »" in c, True)
verifier("consignes : un rapport de ronde venu par iMessage reste un rapport",
         bool(brain._RAPPORT.match("(iMessage) (ta ronde) rien")), True)

print(f"\n{'✅' if not echecs else '❌'} iphone : {total - len(echecs)}/{total} vérifications OK", flush=True)
os._exit(0 if not echecs else 1)
