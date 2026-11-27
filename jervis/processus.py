"""Ses processus de fond (des « runners ») : un programme qui tourne en continu, sans lui.

Un agent de fond travaille puis s'arrête ; un serveur de dev appartient à un projet. Il manquait le programme qui
tourne tout seul, longtemps : un bot, un script qui surveille un dossier, un scraper qui relève une page toutes les
heures, une file de travail, un long calcul. « Lance ce script et laisse-le tourner », « relance-le s'il plante »,
« il en est où ? », « arrête-le ».

Chacun a un nom, une commande, un dossier, et un journal (sa sortie, dans espace/processus/). Il tourne dans sa
propre session : il survit au redémarrage de Steve, qui le retrouve au démarrage. Avec « relancer », Steve le
relance s'il s'arrête (au plus PROCESSUS_RELANCES_MAX fois par heure : au-delà, il plante en boucle, il te
prévient et le laisse arrêté). Un programme sans « relancer » qui finit : il te le dit (une phrase écrite par le
code, jamais sa sortie à voix haute : elle peut venir d'une page). Le programmateur y regarde toutes les 20 s.

La commande passe par le même gardien qu'une commande Bash (interdits, cage après un texte venu d'ailleurs,
questions selon ton mode). Un programme qui se relance tout seul, juste après une page ou un mail lus : il te
demande d'abord, la commande en entier sous les yeux.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from . import config
from .dev import _arreter_groupe, _env, _fin, _groupe_vivant, duree, il_y_a, sans_couleurs

FICHIER: Path = config.ESPACE / "processus.json"
DOSSIER: Path = config.ESPACE / "processus"
JOURNAL_MAX = 2_000_000        # octets : au-delà, il garde la dernière moitié à la relance suivante
GARDER_FINIS_S = 86400         # un programme fini reste dans la liste un jour (pour relire son journal)
_NOM = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def nom_valide(nom: Any) -> str | None:
    """« Bot Discord » → « bot-discord ». None si rien d'utilisable."""
    n = "-".join(re.findall(r"[a-z0-9]+", str(nom or "").casefold()))[:40].strip("-")
    return n if _NOM.fullmatch(n) else None


def _lire() -> dict[str, dict[str, Any]]:
    try:
        tous = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return tous if isinstance(tous, dict) else {}


def _ecrire(tous: dict[str, dict[str, Any]]) -> None:
    FICHIER.parent.mkdir(parents=True, exist_ok=True)
    FICHIER.write_text(json.dumps(tous, ensure_ascii=False, indent=1), encoding="utf-8")


def journal_de(nom: str) -> Path:
    return DOSSIER / f"{nom}.log"


def tourne(p: dict[str, Any]) -> bool:
    return _groupe_vivant(int(p.get("pid") or 0))


def _demarrer(nom: str, p: dict[str, Any]) -> int:
    journal = journal_de(nom)
    journal.parent.mkdir(parents=True, exist_ok=True)
    if journal.exists() and journal.stat().st_size > JOURNAL_MAX:  # garde la fin, à la relance
        journal.write_bytes(journal.read_bytes()[-JOURNAL_MAX // 2:])
    with open(journal, "a", encoding="utf-8") as f:
        f.write(f"\n--- {time.strftime('%d/%m %H:%M:%S')} : $ {p['commande']}\n")
        f.flush()
        proc = subprocess.Popen(["/bin/zsh", "-c", p["commande"]], cwd=p["dossier"], env=_env(),
                                stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    return proc.pid


def fin_du_journal(nom: str, lignes: int = 40) -> str:
    try:
        texte = sans_couleurs(journal_de(nom).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return _fin("\n".join(texte.rstrip().splitlines()[-max(1, min(lignes, 200)):]), 6000)


def lancer(nom: str, commande: str, dossier: str = "", relancer: bool = False) -> tuple[bool, str]:
    """(c'est parti, ce qu'il faut lui dire)."""
    nom_ok = nom_valide(nom)
    if not nom_ok:
        return False, "Donne-lui un nom court (« bot-discord », « scraper-prix »)."
    commande = str(commande or "").strip()
    if not commande:
        return False, "Il faut la commande à lancer."
    tous = _lire()
    if (p := tous.get(nom_ok)) and tourne(p):
        return False, f"« {nom_ok} » tourne déjà (depuis {il_y_a(p['depuis'])}). Arrête-le d'abord pour le relancer."
    vivants = sum(1 for k, q in tous.items() if k != nom_ok and tourne(q))
    if vivants >= config.PROCESSUS_MAX:
        return False, f"Déjà {vivants} processus en cours (le maximum) : arrêtes-en un d'abord."
    lieu = Path(os.path.expanduser(str(dossier or ""))) if dossier else config.ESPACE
    if not lieu.is_dir():
        return False, f"Le dossier {lieu} n'existe pas."
    p = {"commande": commande, "dossier": str(lieu), "relancer": bool(relancer), "depuis": time.time(),
         "relances": [], "fin": None, "prevenu": False}
    try:
        p["pid"] = _demarrer(nom_ok, p)
    except OSError as e:
        return False, f"Impossible de le lancer : {e}"
    tous[nom_ok] = p
    _ecrire(tous)
    return True, nom_ok


def arreter(nom: str) -> str:
    tous = _lire()
    nom_ok = nom_valide(nom) or ""
    if nom_ok not in tous:
        return f"Pas de processus « {nom} »." + (f" Il y a : {', '.join(tous)}." if tous else "")
    p = tous.pop(nom_ok)
    if tourne(p):
        pid = int(p["pid"])
        _arreter_groupe(pid)
        for _ in range(30):
            if not _groupe_vivant(pid):
                break
            time.sleep(0.1)
        if _groupe_vivant(pid):
            _arreter_groupe(pid, signal.SIGKILL)
    _ecrire(tous)
    return f"« {nom_ok} » est arrêté (son journal reste dans {journal_de(nom_ok)})."


def etat() -> str:
    tous = _lire()
    if not tous:
        return "Aucun processus de fond."
    lignes = []
    for nom, p in tous.items():
        if tourne(p):
            quoi = f"tourne depuis {il_y_a(p['depuis'])}"
        elif p.get("abandon"):
            quoi = "arrêté : il plantait en boucle"
        else:
            quoi = f"fini {il_y_a(p['fin'])}" if p.get("fin") else "arrêté"
        relances = len(p.get("relances") or [])
        lignes.append(f"- {nom} : {quoi}" + (", se relance s'il s'arrête" if p.get("relancer") else "")
                      + (f", relancé {relances} fois" if relances else "") + f" ({_sans_cd(p)[:80]})")
    return "\n".join(lignes)


def _sans_cd(p: dict[str, Any]) -> str:
    """La commande sans le « cd » vers son dossier que le gardien met en tête (permissions.Gardien._processus)."""
    tete = f"cd {shlex.quote(str(p.get('dossier') or ''))} && "
    return p["commande"][len(tete):] if p["commande"].startswith(tete) else p["commande"]


def veiller(maintenant: float | None = None) -> list[str]:
    """Toutes les 20 secondes (fond.Programmateur) : relance ceux qui doivent l'être, note ceux qui ont fini, oublie
    ceux finis depuis un jour. Renvoie ce qu'il faut te dire (des phrases écrites par le code)."""
    maintenant = maintenant or time.time()
    tous, change, dire = _lire(), False, []
    for nom, p in list(tous.items()):
        if tourne(p):
            continue
        if p.get("fin") and maintenant - p["fin"] > GARDER_FINIS_S:
            tous.pop(nom)
            change = True
            continue
        if p.get("fin") or p.get("abandon"):
            continue
        change = True
        if p.get("relancer"):
            p["relances"] = [t for t in p.get("relances") or [] if maintenant - t < 3600]
            if len(p["relances"]) < config.PROCESSUS_RELANCES_MAX:
                try:
                    p["pid"], p["depuis"] = _demarrer(nom, p), maintenant
                    p["relances"].append(maintenant)
                    continue
                except OSError:
                    pass
            p["abandon"], p["fin"] = True, maintenant
            dire.append(f"Le processus « {nom} » s'arrête en boucle ({config.PROCESSUS_RELANCES_MAX} fois en une "
                        "heure) : je le laisse arrêté. Demande-moi son journal pour voir pourquoi.")
        else:
            p["fin"] = maintenant
            dire.append(f"Le processus « {nom} » a fini, après {duree(maintenant - p['depuis'])}.")
    if change:
        _ecrire(tous)
    return dire


def _texte(t: str, erreur: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], **({"is_error": True} if erreur else {})}


@tool("processus", "Un programme qui tourne en continu en arrière-plan, sans toi (un « runner ») : un bot, un "
      "script qui surveille un dossier ou une page, un scraper, une file de travail, un long calcul. `action` = "
      "lancer (`nom` court, `commande` = la commande shell, `dossier` où la lancer, `relancer` = true pour qu'il "
      "soit relancé s'il s'arrête), arreter (`nom`), etat (tous), journal (`nom`, `lignes` : la fin de sa sortie). "
      "Il survit à ton redémarrage. Pour un serveur de dev, serveur_de_dev ; pour un travail qui demande de "
      "réfléchir, lancer_tache_de_fond ; pour une chose à faire à heure fixe, programmer_annonce.",
      {"type": "object",
       "properties": {"action": {"type": "string", "enum": ["lancer", "arreter", "etat", "journal"]},
                      "nom": {"type": "string"}, "commande": {"type": "string"}, "dossier": {"type": "string"},
                      "relancer": {"type": "boolean"}, "lignes": {"type": "integer"}},
       "required": ["action"]})
async def outil_processus(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "etat")
    if action == "etat":
        return _texte(etat())
    if action == "arreter":
        return _texte(await asyncio.to_thread(arreter, str(args.get("nom") or "")))
    if action == "journal":
        nom = nom_valide(args.get("nom")) or ""
        if nom not in _lire() and not journal_de(nom).exists():
            return _texte(f"Pas de processus « {args.get('nom')} ».", erreur=True)
        fin = fin_du_journal(nom, int(args.get("lignes") or 40))
        return _texte(f"Fin du journal de « {nom} » :\n{fin}" if fin else f"« {nom} » n'a encore rien écrit.")
    if action != "lancer":
        return _texte("`action` : lancer, arreter, etat ou journal.", erreur=True)
    ok, quoi = lancer(str(args.get("nom") or ""), str(args.get("commande") or ""), str(args.get("dossier") or ""),
                      args.get("relancer") is True)
    if not ok:
        return _texte(f"Refusé : {quoi}", erreur=True)
    await asyncio.sleep(2)  # s'il meurt tout de suite (commande introuvable, erreur de syntaxe), on le dit
    p = _lire().get(quoi, {})
    if not tourne(p):
        return _texte(f"« {quoi} » s'est arrêté tout de suite. Fin de sa sortie :\n{fin_du_journal(quoi, 30)}",
                      erreur=not p.get("relancer"))
    return _texte(f"« {quoi} » tourne (pid {p['pid']})" + (", et sera relancé s'il s'arrête" if p.get("relancer")
                                                          else "") + f". Sa sortie va dans {journal_de(quoi)}.")


OUTILS_PROCESSUS = [outil_processus]
