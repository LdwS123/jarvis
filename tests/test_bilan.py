"""Le bilan du soir : à l'heure dite, à voix haute devant le Mac, sinon en vocal sur l'iPhone ; une fois par soir.

    cd ~/Jervis && PYTHONPATH=. .venv/bin/python tests/test_bilan.py

Rien ne touche à ton vrai espace : bilan_soir.md et bilan_soir_fait.txt vont dans un dossier temporaire. Ni Claude,
ni Messages, ni WhatsApp : « tu es devant le Mac » et l'envoi sur l'iPhone sont des faux.
"""

import asyncio
import inspect
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

threading.Timer(60, lambda: (print("⏰ bloqué", flush=True), os._exit(2))).start()

from jervis import brain, config, distance, fond  # noqa: E402

echecs: list[str] = []
total = 0


def verifier(nom: str, obtenu, attendu) -> None:
    global total
    total += 1
    if obtenu != attendu:
        echecs.append(nom)
        print(f"❌ {nom}\n     obtenu  : {obtenu!r}\n     attendu : {attendu!r}")


TMP = Path(tempfile.mkdtemp(prefix="jervis-test-bilan-")).resolve()
config.ESPACE = TMP
config.FICHIER_BILAN_SOIR = TMP / "bilan_soir.md"
fond.ETAT_BILAN_SOIR = TMP / "bilan_soir_fait.txt"
config.BILAN_SOIR, config.BILAN_SOIR_IPHONE = True, True
config.BILAN_SOIR_HEURE, config.BILAN_SOIR_ATTENTE_MIN, config.BILAN_SOIR_RETARD_MIN = "22:00", 10, 90

la = {"present": True}


async def faux_present() -> bool:
    return la["present"]
fond.present = faux_present


def a_zero() -> None:
    with __import__("contextlib").suppress(OSError):
        fond.ETAT_BILAN_SOIR.unlink()


# --- Quand c'est l'heure ----------------------------------------------------------------------------------------
prog = fond.Programmateur(fond.Standard())
soir = datetime(2026, 11, 27, 22, 0)
verifier("21 h 59 : pas encore", prog.bilan_soir_du(soir - timedelta(minutes=1)), None)
verifier("22 h pile : c'est l'heure", prog.bilan_soir_du(soir), soir)
verifier("23 h 29 : encore ce soir", prog.bilan_soir_du(soir + timedelta(minutes=89)), soir)
verifier("23 h 31 (Mac éteint à 22 h) : il laisse tomber", prog.bilan_soir_du(soir + timedelta(minutes=91)), None)
verifier("le lendemain matin : rien", prog.bilan_soir_du(datetime(2026, 11, 28, 8, 0)), None)
config.BILAN_SOIR_HEURE = "23:30"
verifier("23 h 30, et il est 0 h 20 : c'est encore le bilan d'hier soir",
         prog.bilan_soir_du(datetime(2026, 11, 28, 0, 20)), datetime(2026, 11, 27, 23, 30))
config.BILAN_SOIR_HEURE = "22h"
verifier("« 22h » se lit aussi", prog.bilan_soir_du(soir), soir)
for illisible in ("abc", "25:00", "22:75", ""):
    config.BILAN_SOIR_HEURE = illisible
    verifier(f"heure illisible « {illisible} » : jamais (et pas d'erreur)", prog.bilan_soir_du(soir), None)
config.BILAN_SOIR_HEURE = "22:00"
config.BILAN_SOIR = False
verifier("JERVIS_BILAN_SOIR=non : jamais", prog.bilan_soir_du(soir), None)
config.BILAN_SOIR = True
fond.ETAT_BILAN_SOIR.write_text("2026-11-27")
verifier("déjà fait ce soir : plus rien", prog.bilan_soir_du(soir + timedelta(minutes=5)), None)
verifier("… mais demain soir, si", prog.bilan_soir_du(soir + timedelta(days=1)), soir + timedelta(days=1))
a_zero()


# --- Ce qu'il dit (bilan_soir.md) --------------------------------------------------------------------------------
consigne = fond.lire_bilan_soir()
verifier("bilan_soir.md créé avec la consigne par défaut", config.FICHIER_BILAN_SOIR.read_text(encoding="utf-8"),
         fond.BILAN_SOIR_PAR_DEFAUT)
verifier("… il parle de demain, des tâches, des chantiers, et jamais d'ordres venus d'un mail",
         all(m in consigne for m in ("demain", "tâches de fond", "objectifs", "jamais des ordres")), True)
config.FICHIER_BILAN_SOIR.write_text("Dis-moi aussi mes dépenses du jour.\n", encoding="utf-8")
verifier("tu l'as changé : il lit le tien", fond.lire_bilan_soir(), "Dis-moi aussi mes dépenses du jour.\n")


# --- Le programmateur, pour de vrai (l'heure du bilan = il y a quelques minutes) -------------------------------------
def heure_il_y_a(minutes: float) -> None:
    config.BILAN_SOIR_HEURE = f"{datetime.now() - timedelta(minutes=minutes):%H:%M}"


async def scenarios() -> None:
    standard = fond.Standard()
    journal: list[str] = []
    envois: list[str] = []
    reponse = {"parti": True, "boum": False}

    async def faux_iphone(texte: str) -> bool:
        envois.append(texte)
        if reponse["boum"]:
            raise RuntimeError("WhatsApp fermé")
        return reponse["parti"]

    prog = fond.Programmateur(standard, journal.append)

    # Devant le Mac : à voix haute, une seule fois.
    a_zero()
    la["present"] = True
    heure_il_y_a(1)
    await prog._bilan_soir()
    ev = standard.prendre()
    verifier("devant le Mac : il te le dit (Claude s'en charge)", (ev.genre, ev.texte.startswith(
        "(programmé) Bilan du soir.\n")) if ev else None, ("faire", True))
    verifier("… avec ta consigne de bilan_soir.md", "dépenses du jour" in (ev.texte if ev else ""), True)
    verifier("… noté fait pour ce soir", fond.ETAT_BILAN_SOIR.exists(), True)
    await prog._bilan_soir()
    verifier("… une seule fois par soir", standard.prendre(), None)

    # Pas devant le Mac, sans iPhone branché : il attend que tu reviennes.
    a_zero()
    la["present"] = False
    heure_il_y_a(30)
    await prog._bilan_soir()
    verifier("pas là, pas d'iPhone : il attend (rien dit, rien noté)",
             (standard.prendre(), fond.ETAT_BILAN_SOIR.exists()), (None, False))
    la["present"] = True
    await prog._bilan_soir()
    ev = standard.prendre()
    verifier("… tu reviens : il te le dit", ev.genre if ev else None, "faire")

    # Pas devant le Mac, iPhone branché : il t'attend 10 minutes, puis le vocal.
    a_zero()
    prog.au_loin = faux_iphone
    la["present"] = False
    heure_il_y_a(3)
    await prog._bilan_soir()
    verifier("pas là depuis 3 minutes : il t'attend encore", (envois, fond.ETAT_BILAN_SOIR.exists()), ([], False))
    heure_il_y_a(12)
    await prog._bilan_soir()
    await asyncio.sleep(0.05)
    verifier("au bout de 10 minutes : il part sur l'iPhone", len(envois), 1)
    verifier("… en vocal : Claude sait que c'est pour être dit", (envois[0].startswith("(programmé) Bilan du soir. "),
                                                                    fond.BILAN_SOIR_LOIN in envois[0],
                                                                    "dépenses du jour" in envois[0]),
             (True, True, True))
    verifier("… rien à voix haute au Mac", standard.prendre(), None)
    verifier("… noté fait, et dit dans le journal", (fond.ETAT_BILAN_SOIR.exists(), any("iPhone" in j for j in journal)),
             (True, True))
    la["present"] = True
    await prog._bilan_soir()
    verifier("… tu reviens ensuite : pas une deuxième fois", (standard.prendre(), len(envois)), (None, 1))

    # L'envoi rate : il te le dira devant le Mac, sans réessayer l'iPhone.
    for cas, boum in (("rien n'est parti", False), ("une erreur", True)):
        a_zero()
        envois.clear()
        prog = fond.Programmateur(standard, journal.append)
        prog.au_loin = faux_iphone
        reponse["parti"], reponse["boum"] = False, boum
        la["present"] = False
        await prog._bilan_soir()
        await asyncio.sleep(0.05)
        verifier(f"{cas} : il a essayé une fois", len(envois), 1)
        verifier(f"{cas} : pas noté fait", fond.ETAT_BILAN_SOIR.exists(), False)
        await prog._bilan_soir()
        await asyncio.sleep(0.05)
        verifier(f"{cas} : il ne réessaie pas l'iPhone", len(envois), 1)
        la["present"] = True
        await prog._bilan_soir()
        ev = standard.prendre()
        verifier(f"{cas} : tu reviens devant le Mac, il te le dit", ev.genre if ev else None, "faire")
    verifier("l'erreur est notée dans le journal", any("pas parti" in j and "WhatsApp fermé" in j for j in journal), True)
    reponse["parti"], reponse["boum"] = True, False

    # Pendant que l'envoi est en cours : rien d'autre.
    a_zero()
    porte = asyncio.Event()

    async def lent(texte: str) -> bool:
        envois.append(texte)
        await porte.wait()
        return True
    envois.clear()
    prog = fond.Programmateur(standard, journal.append)
    prog.au_loin = lent
    la["present"] = False
    await prog._bilan_soir()
    await asyncio.sleep(0.05)
    a_zero()  # (même si on efface la trace pendant l'envoi)
    await prog._bilan_soir()
    await asyncio.sleep(0.05)
    verifier("un envoi en cours : jamais deux à la fois", len(envois), 1)
    porte.set()
    await asyncio.sleep(0.05)

    # JERVIS_BILAN_SOIR_IPHONE=non : il garde le bilan pour le Mac.
    a_zero()
    envois.clear()
    config.BILAN_SOIR_IPHONE = False
    prog = fond.Programmateur(standard, journal.append)
    prog.au_loin = faux_iphone
    await prog._bilan_soir()
    await asyncio.sleep(0.05)
    verifier("JERVIS_BILAN_SOIR_IPHONE=non : jamais sur l'iPhone", (envois, fond.ETAT_BILAN_SOIR.exists()), ([], False))
    config.BILAN_SOIR_IPHONE = True


asyncio.run(scenarios())


# --- La boîte iPhone : raconter de vive voix ----------------------------------------------------------------------
async def boites() -> None:
    partis: list[tuple[str, str]] = []

    async def faux_imessage(adresse: str, texte: str) -> tuple[int, str]:
        partis.append((adresse, texte))
        return 0, ""
    imessage = distance.Boite(faux_imessage, base=TMP / "absent.db", adresses="+33600000000", journal=lambda _l: None)
    imessage._fils, imessage._guids = [(7, "+33600000000")], {7: "iMessage;-;+33600000000"}
    ok = await imessage.raconter_vocal("Bonne soirée.")
    verifier("iMessage : tu ne lui as rien écrit depuis son démarrage → ta conversation avec toi-même",
             (ok, imessage.adresse, imessage.fil), (True, "+33600000000", "iMessage;-;+33600000000"))
    verifier("… sans voix pour le fichier : le texte part", [t for _, t in partis], [distance.MARQUE + "Bonne soirée."])

    vocaux: list[str] = []

    async def faux_texte(numero: str, texte: str) -> tuple[str, str]:
        return "ENVOYÉ", ""

    async def faux_vocal(numero: str, texte: str) -> tuple[str, str]:
        vocaux.append(texte)
        return "ENVOYÉ", ""
    whatsapp = distance.BoiteWhatsApp(envoyer_texte=faux_texte, envoyer_vocal=faux_vocal, base=TMP / "absent.sqlite",
                                      moi="+33600000000", journal=lambda _l: None)
    whatsapp.adresse = "+33600000000"
    verifier("WhatsApp : un vrai vocal, dit avec sa voix", (await whatsapp.raconter_vocal("Bonne soirée."), vocaux),
             (True, ["Bonne soirée."]))

asyncio.run(boites())


# --- Branché ----------------------------------------------------------------------------------------------------
verifier("le programmateur y regarde toutes les 20 s", "self._bilan_soir()" in inspect.getsource(fond.Programmateur.tourner),
         True)
principal = (Path(fond.__file__).parent / "__main__.py").read_text(encoding="utf-8")
verifier("Steve branche l'envoi sur l'iPhone", "programmateur.au_loin = bilan_au_loin" in principal, True)
verifier("… préparé comme une demande venue de l'iPhone, envoyé en vocal",
         ("origine=boite.origine" in principal, "boite.raconter_vocal(reponse)" in principal), (True, True))
verifier("le menu ouvre bilan_soir.md", "Mon bilan du soir" in principal, True)
consignes = brain._consignes(False)
verifier("ses consignes disent où est le bilan, et à quelle heure",
         (str(config.FICHIER_BILAN_SOIR) in consignes, "{bilan" in consignes), (True, False))

print(f"\n{total - len(echecs)}/{total} vérifications OK")
os._exit(1 if echecs else 0)
