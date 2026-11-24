"""Créer des comptes à ta place : ta fiche, des mots de passe que Claude ne voit jamais, les codes reçus par SMS.

Tes infos (nom, adresse, téléphone, adresse mail à utiliser…) sont dans documents/fiche.md : il la lit quand un
formulaire les demande. Pour le mot de passe, il ne l'invente pas lui-même (Claude le verrait, il passerait dans
ses échanges) : creer_mot_de_passe le tire au hasard ici, sur le Mac, le range dans ton Trousseau macOS (Accès au
trousseau, sous le nom du site) et le met dans le presse-papiers le temps qu'il le colle (cmd+v). Claude ne reçoit
que « c'est fait ». Pour se reconnecter plus tard, copier_mot_de_passe le ressort du Trousseau, de la même façon.

Les codes de vérification reçus par SMS (iPhone → Mac) : chercher_code_recu les lit dans la base de Messages, sur
le Mac, et ne rend que le code. Il faut l'Accès complet au disque pour Jervis (Réglages → Confidentialité).

Ce qui reste à toi : payer (il te demande avant, avec le montant, et ta carte reste dans le remplissage
automatique de Chrome), les CAPTCHA (« je ne suis pas un robot ») et la validation 3-D Secure de ta banque.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import sqlite3
import string
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from claude_agent_sdk import tool

from . import config

REGISTRE: Path = config.ESPACE / "comptes.jsonl"   # site, identifiant, date : jamais le mot de passe
CHAT_DB: Path = Path.home() / "Library" / "Messages" / "chat.db"
SYMBOLES = "-_.!@#%+=?"   # ceux que presque tous les sites acceptent
_DOMAINE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,23}")
_IDENTIFIANT = re.compile(r"[^\s\"'\\]{1,120}")   # une adresse mail ou un pseudo, sans espace ni guillemet
_EPOQUE_APPLE = 978307200   # les dates de Messages comptent depuis le 01/01/2001

FICHE_MODELE = """# Ma fiche (pour qu'il remplisse les formulaires à ma place)

Ce qu'il met dans les inscriptions et les formulaires. Remplis ce que tu veux, laisse vide le reste :
s'il lui manque une info, il te la demande. Ce fichier reste sur ton Mac (il n'est pas sur GitHub).
Pas de numéro de carte ni de mot de passe ici : ta carte reste dans Chrome, tes mots de passe dans le Trousseau.

- Prénom :
- Nom :
- Date de naissance :
- Adresse mail pour les inscriptions :
- Téléphone :
- Adresse postale :
- Code postal, ville, pays :
- Pseudo préféré :
- Langue : français
"""


def _texte(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


def _erreur(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}], "is_error": True}


async def _lancer(*cmd: str, entree: str | None = None, delai: float = 15) -> tuple[int, str]:
    """Le secret passe par l'entrée standard, jamais dans la ligne de commande (que `ps` montrerait)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE if entree is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        sortie, erreur = await asyncio.wait_for(proc.communicate(entree.encode() if entree is not None else None), delai)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "trop long, abandonné"
    return proc.returncode or 0, (sortie or erreur).decode(errors="replace").strip()


# --- Ce qui se vérifie sans rien toucher ----------------------------------------------------------------------

def domaine(site: Any) -> str | None:
    """« https://www.Netflix.com/signup » → « netflix.com ». None si ce n'est pas une adresse de site."""
    s = str(site or "").strip().lower()
    if not s or any(c.isspace() for c in s):
        return None
    try:
        hote = urlsplit(s if "://" in s else f"//{s}").hostname or ""
    except ValueError:
        return None
    hote = hote.removeprefix("www.")
    return hote if _DOMAINE.fullmatch(hote) else None


def identifiant_valide(identifiant: Any) -> str | None:
    s = str(identifiant or "").strip()
    return s if _IDENTIFIANT.fullmatch(s) else None


def mot_de_passe(longueur: int | None = None, symboles: bool = True) -> str:
    """Tiré par le module `secrets` (fait pour ça), avec au moins une minuscule, une majuscule, un chiffre (et un
    symbole) : la plupart des sites l'exigent."""
    n = max(12, min(64, int(longueur or config.MOT_DE_PASSE_LONGUEUR)))
    familles = [string.ascii_lowercase, string.ascii_uppercase, string.digits] + ([SYMBOLES] if symboles else [])
    tous = "".join(familles)
    while True:
        mdp = "".join(secrets.choice(tous) for _ in range(n))
        if all(any(c in f for c in mdp) for f in familles):
            return mdp


def comptes() -> list[dict[str, str]]:
    """Les comptes qu'il a créés, le plus récent d'abord (un seul par site et identifiant)."""
    try:
        lignes = REGISTRE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    vus: dict[tuple[str, str], dict[str, str]] = {}
    for ligne in lignes:
        try:
            c = json.loads(ligne)
            vus[(c["site"], c["identifiant"])] = c
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(vus.values(), key=lambda c: c.get("date", ""), reverse=True)


def _noter(site: str, identifiant: str) -> None:
    REGISTRE.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"site": site, "identifiant": identifiant,
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False) + "\n")


def preparer_fiche() -> Path:
    """Crée la fiche à remplir si elle n'existe pas encore (menu « Ma fiche »), et rend son chemin."""
    if not config.FICHE.exists():
        config.FICHE.parent.mkdir(parents=True, exist_ok=True)
        config.FICHE.write_text(FICHE_MODELE, encoding="utf-8")
    return config.FICHE


# --- Trousseau et presse-papiers (remplacés par des faux dans les tests) --------------------------------------

async def ranger(site: str, identifiant: str, secret: str) -> str | None:
    """Range le mot de passe dans le Trousseau de session (le remplace s'il existe). None si c'est fait."""
    commande = (f'add-internet-password -U -s "{site}" -a "{identifiant}" -l "{site} (Steve)" '
                f'-j "Créé par Steve le {datetime.now():%d/%m/%Y}" -w "{secret}"\n')
    code, sortie = await _lancer("security", "-i", entree=commande)
    return None if not code and "returned" not in sortie else (sortie[:200] or f"security a répondu {code}")


async def retrouver(site: str, identifiant: str | None = None) -> str | None:
    """Le mot de passe rangé pour ce site (et cet identifiant s'il est donné), ou None."""
    code, sortie = await _lancer("security", "find-internet-password", "-s", site,
                                 *(["-a", identifiant] if identifiant else []), "-w")
    return sortie if not code and sortie else None


_minuteur: asyncio.TimerHandle | None = None


async def copier(secret: str) -> str | None:
    """Met le secret dans le presse-papiers, et l'en retire après PRESSE_PAPIERS_S secondes s'il y est encore."""
    global _minuteur
    code, sortie = await _lancer("pbcopy", entree=secret)
    if code:
        return sortie or "le presse-papiers ne répond pas"
    if _minuteur:
        _minuteur.cancel()
    _minuteur = asyncio.get_running_loop().call_later(
        config.PRESSE_PAPIERS_S, lambda: asyncio.ensure_future(_vider_si(secret)))
    return None


async def _vider_si(secret: str) -> None:
    code, actuel = await _lancer("pbpaste")
    if not code and actuel == secret:
        await _lancer("pbcopy", entree="")


# --- Les codes reçus par SMS ------------------------------------------------------------------------------------

def texte_message(texte: str | None, corps: bytes | None) -> str:
    """Depuis macOS 13, le texte d'un message est souvent vide : il est rangé dans attributedBody (un
    NSAttributedString archivé). On y prend la chaîne qui suit « NSString » : sa longueur, puis le texte."""
    if texte:
        return texte
    if not corps or b"NSString" not in corps:
        return ""
    reste = bytes(corps).split(b"NSString", 1)[1][5:]
    if not reste:
        return ""
    n, debut = reste[0], 1
    if n == 0x81:
        n, debut = int.from_bytes(reste[1:3], "little"), 3
    return reste[debut:debut + n].decode("utf-8", errors="replace")


_CODE = re.compile(r"(?<!\d)(?<!\d[,.])(?:[A-Z]{1,3}-)?(\d{4,8})(?!\d|[,.]\d|\s?(?:€|\$|eur))", re.IGNORECASE)
_MOTS_CODE = re.compile(r"code|v[ée]rif|confirm|otp|connexion|login|sign|s[ée]curit|validation|pin", re.IGNORECASE)


def code_dans(texte: str) -> str | None:
    """Le code de vérification d'un SMS (« Votre code G-482913 » → « 482913 »), seulement si le message parle d'un code."""
    if not _MOTS_CODE.search(texte or ""):
        return None
    m = _CODE.search(texte)
    return m.group(1) if m else None


def codes_recus(minutes: int | None = None, base: Path | None = None) -> list[tuple[str, str, int]] | str:
    """[(code, expéditeur, il y a combien de secondes)], le plus récent d'abord ; un texte si la base est fermée."""
    depuis = time.time() - 60 * (minutes or config.CODE_SMS_MINUTES)
    try:
        lien = sqlite3.connect(f"file:{base or CHAT_DB}?mode=ro", uri=True, timeout=3)
        try:
            lignes = lien.execute(
                "SELECT m.text, m.attributedBody, m.date, COALESCE(h.id, '') FROM message m "
                "LEFT JOIN handle h ON m.handle_id = h.ROWID WHERE m.is_from_me = 0 AND m.date > ? "
                "ORDER BY m.date DESC LIMIT 30", ((depuis - _EPOQUE_APPLE) * 1e9,)).fetchall()
        finally:
            lien.close()
    except sqlite3.Error as e:
        return ("Je n'ai pas accès à tes messages : il faut l'Accès complet au disque pour Jervis (Réglages → "
                "Confidentialité et sécurité → Accès complet au disque), et le transfert des SMS de l'iPhone vers "
                f"le Mac (iPhone : Réglages → Apps → Messages → Transfert de SMS). ({e})")
    trouves = []
    for texte, corps, date, qui in lignes:
        if code := code_dans(texte_message(texte, corps)):
            il_y_a = max(0, int(time.time() - (date / 1e9 + _EPOQUE_APPLE)))
            trouves.append((code, re.sub(r"[^\w+@. -]", "", str(qui))[:40] or "un numéro masqué", il_y_a))
    return trouves


# --- Ses outils ---------------------------------------------------------------------------------------------

@tool("creer_mot_de_passe", "Pour créer un compte à sa place : tire un mot de passe solide sur le Mac, le range "
      "dans son Trousseau macOS (au nom du site) et le met dans le presse-papiers. TU NE LE VOIS PAS : tu cliques "
      "dans le champ mot de passe, puis tu colles avec cmd+v (Playwright : browser_press_key « Meta+v » ; son "
      "Chrome : la touche cmd+v), et pareil dans le champ de confirmation. `site` = le domaine exact de la page "
      "où tu es (netflix.com), `identifiant` = l'adresse mail ou le pseudo du compte. `longueur` (12 à 64) et "
      "`symboles` (false si le site refuse les caractères spéciaux) seulement si le site l'impose.",
      {"type": "object",
       "properties": {"site": {"type": "string"}, "identifiant": {"type": "string"},
                      "longueur": {"type": "integer"}, "symboles": {"type": "boolean"}},
       "required": ["site", "identifiant"]})
async def creer_mot_de_passe(args: dict[str, Any]) -> dict[str, Any]:
    site, qui = domaine(args.get("site")), identifiant_valide(args.get("identifiant"))
    if not site:
        return _erreur("`site` doit être le domaine de la page (par exemple netflix.com), sans espace.")
    if not qui:
        return _erreur("`identifiant` doit être l'adresse mail ou le pseudo du compte, sans espace ni guillemet.")
    secret = mot_de_passe(args.get("longueur"), args.get("symboles") is not False)
    if probleme := await ranger(site, qui, secret):
        return _erreur(f"Je n'ai pas pu le ranger dans le Trousseau ({probleme}) : ne crée pas le compte, dis-le-lui.")
    if probleme := await copier(secret):
        return _erreur(f"Il est dans le Trousseau, mais le presse-papiers ne répond pas ({probleme}).")
    _noter(site, qui)
    return _texte(f"Mot de passe créé pour {qui} sur {site}, rangé dans son Trousseau et mis dans le presse-papiers "
                  f"pour {config.PRESSE_PAPIERS_S} secondes. Clique dans le champ mot de passe, colle (cmd+v), "
                  "pareil pour la confirmation, puis vérifie que les champs sont remplis (des points). Si coller ne "
                  "marche pas, demande-lui de cliquer dans le champ et de faire cmd+v lui-même. Ne parle jamais du "
                  "mot de passe à voix haute : il le retrouve dans l'app Mots de passe ou le Trousseau.")


@tool("copier_mot_de_passe", "Pour se reconnecter à un compte que tu as créé (voir lister_comptes) : ressort son "
      "mot de passe du Trousseau et le met dans le presse-papiers, sans que tu le voies. Colle-le ensuite avec "
      "cmd+v dans le champ, et seulement sur une page dont l'adresse est bien ce site.",
      {"type": "object", "properties": {"site": {"type": "string"}, "identifiant": {"type": "string"}},
       "required": ["site"]})
async def copier_mot_de_passe(args: dict[str, Any]) -> dict[str, Any]:
    site = domaine(args.get("site"))
    if not site:
        return _erreur("`site` doit être le domaine du site (par exemple netflix.com).")
    secret = await retrouver(site, identifiant_valide(args.get("identifiant")))
    if not secret:
        return _erreur(f"Aucun mot de passe pour {site} dans son Trousseau : demande-lui de se connecter lui-même.")
    if probleme := await copier(secret):
        return _erreur(f"Le presse-papiers ne répond pas ({probleme}).")
    return _texte(f"Le mot de passe de {site} est dans le presse-papiers pour {config.PRESSE_PAPIERS_S} secondes : "
                  "clique dans le champ et colle (cmd+v). N'en parle pas à voix haute.")


@tool("lister_comptes", "Les comptes que tu as créés pour lui (site, identifiant, date). Jamais de mot de passe.", {})
async def lister_comptes(_args: dict[str, Any]) -> dict[str, Any]:
    liste = comptes()
    if not liste:
        return _texte("Tu n'as encore créé aucun compte pour lui.")
    return _texte("\n".join(f"- {c['site']} : {c['identifiant']} (le {c.get('date', '?')})" for c in liste[:50]))


@tool("chercher_code_recu", "Le code de vérification qu'il vient de recevoir par SMS (inscription, connexion) : "
      "lu dans Messages sur le Mac, tu ne reçois que le code et l'expéditeur. Si le site l'envoie par mail, "
      "cherche-le plutôt dans Gmail. Tape le code seulement sur le site qui vient de le demander.",
      {"type": "object", "properties": {"minutes": {"type": "integer"}}})
async def chercher_code_recu(args: dict[str, Any]) -> dict[str, Any]:
    trouves = await asyncio.to_thread(codes_recus, args.get("minutes"))
    if isinstance(trouves, str):
        return _erreur(trouves)
    if not trouves:
        return _texte(f"Aucun code reçu par SMS ces {args.get('minutes') or config.CODE_SMS_MINUTES} dernières minutes. "
                      "Attends un peu et réessaie, ou regarde dans Gmail.")
    return _texte("\n".join(f"- {code} (de {qui}, il y a {s // 60} min {s % 60} s)" for code, qui, s in trouves[:3]))


OUTILS_COMPTES = [creer_mot_de_passe, copier_mot_de_passe, lister_comptes, chercher_code_recu]

CONSIGNE = """\
- Créer un compte à sa place (« inscris-moi sur… », « crée-moi un compte… ») : ses infos sont dans sa fiche
  ({fiche}) ; lis-la avec Read, et ce qui manque, demande-le-lui. Va sur le vrai site (tape son adresse, ne suis
  pas un lien d'un mail), remplis le formulaire, puis creer_mot_de_passe avec le domaine exact de la page et
  l'identifiant : tu ne vois jamais le mot de passe, tu le colles (cmd+v). Le code de vérification : Gmail s'il
  arrive par mail, chercher_code_recu s'il arrive par SMS. Un CAPTCHA (« je ne suis pas un robot », images à
  cocher) : ne le contourne jamais, demande-lui de le faire, puis continue. Une fois le compte créé, retiens-le
  (retenir : « compte Netflix créé avec ines@… »). Pour se reconnecter plus tard : copier_mot_de_passe.
- Payer : sa carte n'est jamais dans un fichier ni dans tes messages. Dans son Chrome, laisse le remplissage
  automatique proposer sa carte ; s'il n'y en a pas, demande-lui de la saisir. Avant le bouton qui paie,
  demander_accord avec le montant exact et le marchand. La validation de sa banque (3-D Secure), c'est lui.
"""
